"""Resource binding and two-level control parsing over ordinary v4 Services.

This host owns targets and lifetimes; providers own resource and control semantics.
Renderer mounting and chat/playback wiring are separate consumers of this boundary.
"""

from __future__ import annotations

import threading
import uuid
from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol

from app.config.character_resources import CharacterVisualResource
from app.core.runtime_log import diagnostic_attributes, log_event
from app.plugin_sdk.sakura_visual_control import validate_visual_control
from app.plugins.inventory import InstalledPluginRecord, PluginInventorySnapshot
from app.plugins.runtime_v4 import PluginRuntimeError
from app.plugins.visuals import VISUAL_CONTRACT_VERSION, VisualCapability, relative_resource_path, resolve_resource_path


class VisualRuntime(Protocol):
    def service_identity(self, service_key: str) -> dict[str, str]: ...

    def call_service(self, service_key: str, method: str, *args: object) -> object: ...


class VisualHostError(ValueError):
    def __init__(self, code: str, message: str | None = None) -> None:
        super().__init__(message or code)
        self.code = code


# These are availability states, not failed resource/plugin executions.
VISUAL_INACTIVE_REASONS = frozenset({
    "VISUAL_NOT_BOUND", "VISUAL_BINDING_EXPIRED", "VISUAL_RESOURCE_MISSING",
    "VISUAL_PROVIDER_MISSING", "PLUGIN_DISABLED", "VISUAL_PROVIDER_SELECTION_REQUIRED",
    "VISUAL_SERVICE_UNAVAILABLE", "VISUAL_CONTRACT_UNSUPPORTED", "API_VERSION_UNSUPPORTED",
    "VISUAL_MANIFEST_INVALID", "VISUAL_MODULE_INVALID",
})


@dataclass(frozen=True)
class VisualControlResult:
    control: dict[str, Any] | None
    reason_code: str = "READY"
    error: BaseException | None = field(default=None, repr=False, compare=False)


class VisualBinding:
    def __init__(
        self,
        runtime: VisualRuntime,
        capability: VisualCapability,
        identity: dict[str, str],
        request: dict[str, Any],
        description: dict[str, Any],
        install_id: str = "",
    ) -> None:
        self.id = uuid.uuid4().hex
        self.capability = capability
        self._runtime = runtime
        self._identity = dict(identity)
        self._request = deepcopy(request)
        self._description = deepcopy(description)
        self._closed = threading.Event()
        self.install_id = install_id

    def presentation(self) -> dict[str, Any]:
        self._check_active()
        return {
            "bindingId": self.id,
            "resourceId": self._request["resource"]["id"],
            "type": self._request["resource"]["type"],
            "providerId": self._identity["providerId"],
            "installId": self.install_id,
            "renderer": self.capability.renderer,
            "editor": self.capability.editor,
            "data": deepcopy(self._description["rendererData"]),
            "assets": deepcopy(self._description.get("assets", {})),
        }

    @property
    def description(self) -> dict[str, Any]:
        self._check_active()
        return deepcopy(self._description)

    @property
    def reply_visual(self) -> dict[str, Any] | None:
        try:
            self._check_active()
        except VisualHostError as error:
            if error.code != "VISUAL_BINDING_EXPIRED":
                raise
            return None
        return {
            "resourceId": self.resource_id,
            "prompt": self._description["prompt"],
            "outputSchema": deepcopy(self._description["outputSchema"]),
        }

    @property
    def resource_id(self) -> str:
        return self._request["resource"]["id"]

    @property
    def provider_id(self) -> str:
        return self._identity["providerId"]

    def close(self) -> None:
        self._closed.set()

    def _check_active(self) -> None:
        if self._closed.is_set():
            raise VisualHostError("VISUAL_BINDING_EXPIRED")
        try:
            current = self._runtime.service_identity(self.capability.service)
        except PluginRuntimeError as error:
            if error.code != "SERVICE_MISSING":
                raise
            raise VisualHostError("VISUAL_BINDING_EXPIRED") from error
        if self._closed.is_set() or current != self._identity:
            raise VisualHostError("VISUAL_BINDING_EXPIRED")

    def parse_control(
        self, control: object, *, legacy: Mapping[str, Any] | None = None,
        segment: Mapping[str, Any] | None = None,
    ) -> VisualControlResult:
        """Reject only visual data. The caller retains its parsed text and speech.

        An explicit envelope wins over legacy fields, even when it is invalid.
        Legacy field interpretation belongs exclusively to the selected provider.
        """
        try:
            self._check_active()
            if control is None and legacy is None:
                return VisualControlResult(None)
            payload = None
            if control is not None:
                if (
                    not isinstance(control, Mapping)
                    or set(control) != {"version", "resourceId", "payload"}
                    or type(control.get("version")) is not int
                    or control["version"] != VISUAL_CONTRACT_VERSION
                    or control.get("resourceId") != self._request["resource"]["id"]
                ):
                    raise VisualHostError("VISUAL_CONTROL_INVALID")
                payload = control["payload"]
            request = deepcopy(self._request)
            request["segment"] = deepcopy(segment or {})
            parsed = self._runtime.call_service(
                self.capability.service,
                "parseControl",
                request,
                deepcopy(self._description["parserData"]),
                deepcopy(payload),
                deepcopy(legacy) if control is None else None,
            )
            # A stopped/replaced process must not publish an in-flight result.
            self._check_active()
            parsed = deepcopy(parsed)
            if (
                not isinstance(parsed, dict)
                or not ({"state", "actions"} & parsed.keys())
                or not isinstance(parsed.get("actions", []), list)
            ):
                raise VisualHostError("VISUAL_CONTROL_INVALID")
            self._check_active()
            return VisualControlResult({
                "version": VISUAL_CONTRACT_VERSION,
                "bindingId": self.id,
                "resourceId": self._request["resource"]["id"],
                **{key: parsed[key] for key in ("state", "actions") if key in parsed},
            })
        except VisualHostError as error:
            return VisualControlResult(None, error.code, error)
        except PluginRuntimeError as error:
            return VisualControlResult(None, "VISUAL_CONTROL_INVALID" if error.code == "SERVICE_PAYLOAD_INVALID" else "VISUAL_CONTROL_REJECTED", error)


class VisualHost:
    def __init__(
        self, runtime: VisualRuntime,
        *, inventory: Callable[[], PluginInventorySnapshot],
    ) -> None:
        self._inventory = inventory
        self._runtime = runtime
        self._lock = threading.Lock()
        self._revision = 0
        self._binding: VisualBinding | None = None
        self._closed = False

    def close(self) -> None:
        with self._lock:
            self._closed = True
            self._clear_locked()

    def clear(self) -> None:
        with self._lock:
            self._clear_locked()

    def presentation(self):
        with self._lock:
            return self._binding.presentation() if self._binding is not None else None

    def resolve_control(self, envelope: object) -> VisualControlResult:
        """Resolve one playback value without holding the visual publication lock."""
        try:
            value = validate_visual_control(envelope)
            if "deferred" not in value:
                raise VisualHostError("VISUAL_CONTROL_INVALID")
            with self._lock:
                binding = self._binding
            if binding is None or (value["bindingId"], value["resourceId"]) != (binding.id, binding.resource_id):
                return VisualControlResult(None, "VISUAL_BINDING_EXPIRED")
            deferred = value["deferred"]
            parsed = binding.parse_control(deferred["control"],
                legacy={"portrait": deferred["portrait"], "tone": deferred["tone"]},
                segment={"tone": deferred["tone"]})
            if parsed.control is not None:
                parsed = VisualControlResult(validate_visual_control(parsed.control))
        except Exception as error:
            reason = "VISUAL_CONTROL_INVALID" if isinstance(error, ValueError) else "VISUAL_CONTROL_REJECTED"
            parsed = VisualControlResult(None, reason, error)
        if parsed.reason_code not in {"READY", "VISUAL_BINDING_EXPIRED"}:
            log_event("Visual", "表现控制未应用", diagnostic_attributes(parsed.error or RuntimeError(parsed.reason_code),
                reason_code=parsed.reason_code, stage="visual.parse_control"), event="visual.control.failed", severity="warning")
        return parsed

    def _clear_locked(self) -> None:
        self._revision += 1
        if self._binding is not None:
            self._binding.close()
            self._binding = None

    def _candidates(self, resource_type: str) -> list[tuple[InstalledPluginRecord, VisualCapability]]:
        return [
            (record, capability)
            for record in self._inventory().records
            for capability in record.visuals
            if capability.resource_type == resource_type
        ]

    def _unavailable_candidates(self, resource_type, provider_id=None):
        result = []
        for record in self._inventory().records:
            if provider_id is not None and record.plugin_id != provider_id:
                continue
            if any(cap.resource_type == resource_type for cap in record.visuals):
                continue
            issue = next((item for item in record.capability_issues
                if item["kind"] == "visual" and (item["type"] == resource_type
                    or (provider_id is not None and not item["type"]))), None)
            if issue is not None:
                result.append({"installId": record.install_id, "pluginId": record.plugin_id,
                    "name": record.name, "type": resource_type, "contract": None,
                    "hasEditor": False, "reasonCode": issue["reasonCode"]})
        return result

    @staticmethod
    def _editor_issue(record, capability):
        return next((item["reasonCode"] for item in record.capability_issues
            if item["kind"] == "visual" and item["type"] == capability.resource_type
            and item["part"] == "editor"), None)

    def _reason(self, record: InstalledPluginRecord, capability: VisualCapability) -> str:
        if not record.runtime_eligible:
            return record.reason_code
        if capability.contract != VISUAL_CONTRACT_VERSION:
            return "VISUAL_CONTRACT_UNSUPPORTED"
        if not record.desired_enabled:
            return "PLUGIN_DISABLED"
        try:
            identity = self._runtime.service_identity(capability.service)
        except PluginRuntimeError:
            return "VISUAL_SERVICE_UNAVAILABLE"
        return "READY" if identity.get("providerId") == record.plugin_id else "VISUAL_PROVIDER_MISMATCH"

    def candidates(self, resource_type: str) -> list[dict[str, Any]]:
        """Static discovery remains available when no provider is running."""
        return [{
            "installId": record.install_id,
            "pluginId": record.plugin_id,
            "name": record.name,
            "type": capability.resource_type,
            "contract": capability.contract,
            "hasEditor": capability.editor is not None,
            "reasonCode": self._reason(record, capability),
        } for record, capability in self._candidates(resource_type)] + self._unavailable_candidates(resource_type)

    def bind(
        self,
        character_id: str,
        package_dir: Path,
        resource: CharacterVisualResource,
        *,
        provider_id: str | None = None,
    ) -> VisualBinding:
        revision, binding = self.prepare(character_id, package_dir, resource, provider_id=provider_id)
        try:
            self.publish(revision, binding)
        except BaseException:
            binding.close()
            raise
        return binding

    def prepare(self, character_id, package_dir, resource, *, provider_id=None):
        """Describe a candidate without retiring the published visual."""
        with self._lock:
            if self._closed:
                raise VisualHostError("VISUAL_BINDING_EXPIRED")
            self._revision += 1
            revision = self._revision
        if resource is None:
            return revision, None
        try:
            resource.validate_paths(package_dir)
        except ValueError as error:
            raise VisualHostError("VISUAL_RESOURCE_INVALID") from error
        record, capability = self._select(resource.type, provider_id)
        request = {"characterId": character_id, "resource": resource.to_mapping()}
        try:
            binding = self._describe(record, capability, request, package_dir)
        except PluginRuntimeError as error:
            code = "VISUAL_RESOURCE_INVALID" if error.code == "VISUAL_RESOURCE_INVALID" else "VISUAL_PROVIDER_FAILED"
            raise VisualHostError(code) from error
        return revision, binding

    def publish(self, revision, binding) -> None:
        with self._lock:
            if self._closed or revision != self._revision:
                if binding is not None:
                    binding.close()
                raise VisualHostError("VISUAL_BINDING_EXPIRED")
            if binding is not None:
                binding._check_active()
            if self._binding is not None:
                self._binding.close()
            self._binding = binding

    def _matching_candidates(self, resource_type, provider_id=None):
        candidates = self._candidates(resource_type)
        winners = {record.plugin_id.casefold() for record, _ in candidates if record.runtime_eligible and record.plugin_id}
        candidates = [item for item in candidates if item[0].runtime_eligible
            or not item[0].plugin_id or item[0].plugin_id.casefold() not in winners]
        if provider_id is not None:
            candidates = [item for item in candidates if item[0].plugin_id == provider_id]
        return candidates

    def resource_choice(self, resource, provider_id=None):
        candidates = self._matching_candidates(resource.type, provider_id)
        record, capability = candidates[0] if len(candidates) == 1 else (None, None)
        if not candidates:
            unavailable = self._unavailable_candidates(resource.type, provider_id)
            if len(unavailable) > 1:
                return {"id": resource.id, "name": resource.name or "未命名形态",
                    "providerId": provider_id, "installId": None, "reasonCode": "VISUAL_PROVIDER_SELECTION_REQUIRED"}
            if len(unavailable) == 1:
                item = unavailable[0]
                return {"id": resource.id, "name": resource.name or item["name"],
                    "providerId": item["pluginId"], "installId": item["installId"], "reasonCode": item["reasonCode"]}
        reason = self._reason(record, capability) if record else ("VISUAL_PROVIDER_MISSING" if not candidates else "VISUAL_PROVIDER_SELECTION_REQUIRED")
        return {"id": resource.id, "name": resource.name or (record.name if record else "未命名形态"),
            "providerId": record.plugin_id if record else provider_id,
            "installId": record.install_id if record else None, "reasonCode": reason}

    def _select(self, resource_type, provider_id=None):
        candidates = self._matching_candidates(resource_type, provider_id)
        if not candidates:
            unavailable = self._unavailable_candidates(resource_type, provider_id)
            if len(unavailable) == 1:
                raise VisualHostError(unavailable[0]["reasonCode"])
            raise VisualHostError("VISUAL_PROVIDER_MISSING" if not unavailable else "VISUAL_PROVIDER_SELECTION_REQUIRED")
        # Selection is explicit when several installations claim the format.
        # A disabled or failed preferred provider never causes silent fallback.
        if len(candidates) != 1:
            raise VisualHostError("VISUAL_PROVIDER_SELECTION_REQUIRED")
        record, capability = candidates[0]
        reason = self._reason(record, capability)
        if reason != "READY":
            raise VisualHostError(reason)
        return record, capability

    def startup_service(self, resource_type: str, provider_id: str | None = None) -> str | None:
        """Find the selected declaration before its process has started."""
        candidates = self._matching_candidates(resource_type, provider_id)
        if len(candidates) != 1:
            return None
        record, capability = candidates[0]
        if not record.runtime_eligible or not record.desired_enabled or capability.contract != VISUAL_CONTRACT_VERSION:
            return None
        return capability.service

    def catalog(self):
        result = []
        for record in self._inventory().records:
            for capability in record.visuals:
                editor_issue = self._editor_issue(record, capability)
                if capability.editor is not None or editor_issue:
                    reason = editor_issue or self._reason(record, capability)
                    scope = None
                    if reason == "READY":
                        try:
                            scope = self._runtime.service_identity(capability.service)["scopeId"]
                        except PluginRuntimeError:
                            reason = "VISUAL_SERVICE_UNAVAILABLE"
                    result.append({"type": capability.resource_type, "pluginId": record.plugin_id, "name": record.name, "reasonCode": reason, "scopeId": scope})
        return result

    def editor(self, resource, raw, provider_id=None):
        record, capability = self._select(resource.type, provider_id)
        if capability.editor is None:
            raise VisualHostError(self._editor_issue(record, capability) or "VISUAL_EDITOR_MISSING")
        identity = self._runtime.service_identity(capability.service)
        data = deepcopy(self._runtime.call_service(capability.service, "editorData", resource.to_mapping(), deepcopy(raw)))
        binding = VisualBinding(self._runtime, capability, identity, {"resource": resource.to_mapping()}, {"rendererData": {}, "assets": {}}, record.install_id)
        binding._check_active()
        return {"visual": binding.presentation(), "data": data, "providerScopeId": identity["scopeId"]}

    def preview_image(self, resource, raw, provider_id=None):
        """Ask for a static cover without describing or mounting a renderer."""
        _record, capability = self._select(resource.type, provider_id)
        try:
            path = self._runtime.call_service(capability.service, "previewImage", resource.to_mapping(), deepcopy(raw))
        except PluginRuntimeError as error:
            if error.code == "SERVICE_METHOD_NOT_EXPORTED":
                return None
            raise
        return relative_resource_path(path) if path is not None else None

    def _describe(self, record, capability, request, package_dir):
        identity = self._runtime.service_identity(capability.service)
        if identity.get("providerId") != record.plugin_id:
            raise VisualHostError("VISUAL_PROVIDER_MISMATCH")
        description = deepcopy(
            self._runtime.call_service(capability.service, "describe", request),
        )
        if isinstance(description, dict) and description.get("error") == "VISUAL_RESOURCE_INVALID":
            raise VisualHostError("VISUAL_RESOURCE_INVALID", description.get("message"))
        if (
            not isinstance(description, dict)
            or not {"prompt", "outputSchema", "rendererData", "parserData"} <= set(description)
            or not isinstance(description["prompt"], str)
            or not isinstance(description["outputSchema"], dict)
        ):
            raise VisualHostError("VISUAL_DESCRIPTION_INVALID")
        assets = description.get("assets", {})
        if not isinstance(assets, dict):
            raise VisualHostError("VISUAL_DESCRIPTION_INVALID")
        try:
            for key, path in assets.items():
                if not isinstance(key, str) or not key or any(ord(c) < 32 for c in key):
                    raise ValueError("invalid asset key")
                if not resolve_resource_path(package_dir, relative_resource_path(path)).is_file():
                    raise ValueError("invalid asset")
        except ValueError as error:
            raise VisualHostError("VISUAL_RESOURCE_INVALID") from error
        binding = VisualBinding(self._runtime, capability, identity, request, description, record.install_id)
        binding._check_active()
        return binding
