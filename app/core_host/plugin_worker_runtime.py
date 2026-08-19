"""Private subprocess runtime that is the only Runtime v2 plugin importer."""

from __future__ import annotations

import argparse
import json
import secrets
import sys
from collections import deque
from dataclasses import asdict
from pathlib import Path
from typing import Any, BinaryIO, Callable, Mapping, Sequence

from app.llm.prompts.types import ContextMessage, ContextRequest
from app.plugins.kernel import PluginKernelError, PluginKernelManager
from app.plugins.manager import PluginManager
from app.plugins.discovery import PluginDiscovery
from app.plugins.models import PERMISSION_MOBILE_CHAT, PLUGIN_API_V3_VERSION

from .plugin_worker import _read_private_frame, _write_private_frame


_ALLOWED_EVENTS = frozenset({
    "app.start", "message.user", "message.ai", "tool.started", "tool.finished", "tool.failed",
    "tts.start", "tts.end",
})
_FIELD_TYPES = frozenset({"string", "password", "boolean", "integer", "number", "select", "readonly"})


class WorkerRuntimeError(ValueError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class _WorkerBridge:
    """Worker side of the framed channel, including nested Host calls."""

    def __init__(
        self,
        input_stream: BinaryIO,
        output_stream: BinaryIO,
        generation_id: str,
        token: str,
    ) -> None:
        self._input = input_stream
        self._output = output_stream
        self._generation_id = generation_id
        self._token = token
        self._queued_requests: deque[dict[str, Any]] = deque()

    def read_request(self) -> dict[str, Any] | None:
        if self._queued_requests:
            return self._queued_requests.popleft()
        return _read_private_frame(self._input)

    def write_response(self, response: Mapping[str, Any]) -> None:
        _write_private_frame(self._output, response)

    def host_call(self, service_key: str, method: str, args: Sequence[Any]) -> object:
        if not _json_value(list(args)):
            raise PluginKernelError("HOST_ARGUMENTS_INVALID")
        request_id = secrets.token_hex(12)
        self.write_response(
            {
                "kind": "host.request",
                "generationId": self._generation_id,
                "token": self._token,
                "id": request_id,
                "name": "host.call",
                "payload": {
                    "serviceKey": service_key,
                    "method": method,
                    "args": list(args),
                },
            }
        )
        while True:
            frame = _read_private_frame(self._input)
            if frame is None:
                raise PluginKernelError("HOST_BRIDGE_UNAVAILABLE")
            if frame.get("kind") != "host.response":
                if frame.get("kind") is None:
                    self._queued_requests.append(frame)
                    continue
                raise PluginKernelError("HOST_PROTOCOL_INVALID")
            valid = (
                frame.get("generationId") == self._generation_id
                and frame.get("token") == self._token
                and frame.get("id") == request_id
                and isinstance(frame.get("ok"), bool)
            )
            if not valid:
                raise PluginKernelError("HOST_PROTOCOL_INVALID")
            if frame["ok"]:
                return frame.get("payload")
            error = frame.get("error") if isinstance(frame.get("error"), Mapping) else {}
            code = error.get("code")
            raise PluginKernelError(
                code if isinstance(code, str) and code else "HOST_CALL_FAILED"
            )


class PluginWorkerRuntime:
    def __init__(
        self,
        app_root: Path,
        generation_id: str,
        *,
        host_call: Callable[[str, str, Sequence[Any]], Any] | None = None,
    ) -> None:
        self._app_root = app_root
        self._generation_id = generation_id
        self._host_call = host_call
        self._manager = PluginManager(app_root, available_service_permissions=frozenset())
        self._kernel: PluginKernelManager | None = None
        self._initialized = False
        self._closed = False
        self._tools: dict[str, Any] = {}
        self._contexts: dict[str, Any] = {}
        self._settings: dict[tuple[str, str], Any] = {}
        self._actions: dict[tuple[str, str, str], Any] = {}
        self._snapshot: dict[str, Any] | None = None

    def handle(self, name: str, payload: Mapping[str, Any]) -> object:
        if self._closed and name != "worker.close":
            raise WorkerRuntimeError("GENERATION_INVALIDATED")
        if name == "worker.initialize":
            raw_host_services = payload.get("hostServices", [])
            if (
                not isinstance(raw_host_services, list)
                or len(raw_host_services) > 16
                or any(
                    not isinstance(item, str) or not item.startswith("sakura.host.")
                    for item in raw_host_services
                )
            ):
                raise WorkerRuntimeError("HOST_SERVICES_INVALID")
            return self.initialize(tuple(dict.fromkeys(raw_host_services)))
        if not self._initialized:
            raise WorkerRuntimeError("PLUGIN_NOT_READY")
        if name == "tool.call":
            contribution = self._contribution(self._tools, payload.get("contributionId"))
            arguments = _object(payload.get("arguments"), "TOOL_ARGUMENTS_INVALID")
            result = contribution.handler(dict(arguments))
            if not _json_value(result):
                raise WorkerRuntimeError("TOOL_RESULT_INVALID")
            return result
        if name == "context.call":
            contribution = self._contribution(self._contexts, payload.get("contributionId"))
            request = _context_request(_object(payload.get("request"), "CONTEXT_REQUEST_INVALID"))
            provided = contribution.build_context(request)
            if not isinstance(provided, Sequence) or isinstance(provided, (str, bytes)):
                raise WorkerRuntimeError("CONTEXT_RESULT_INVALID")
            return [_context_fragment(item, index) for index, item in enumerate(provided[:16])]
        if name == "status.get":
            return self._status_snapshot()
        if name == "service.call":
            kernel = self._require_kernel()
            service_key = _identifier(payload.get("serviceKey"), "SERVICE_KEY_INVALID")
            method = _identifier(payload.get("method"), "SERVICE_METHOD_INVALID")
            args = payload.get("args", [])
            if not isinstance(args, list) or len(args) > 32 or not _json_value(args):
                raise WorkerRuntimeError("SERVICE_ARGUMENTS_INVALID")
            try:
                result = kernel.call_service(service_key, method, args)
            except PluginKernelError as error:
                raise WorkerRuntimeError(error.code) from error
            if not _json_value(result):
                raise WorkerRuntimeError("SERVICE_RESULT_INVALID")
            return result
        if name == "hook.transform":
            kernel = self._require_kernel()
            hook_name = _identifier(payload.get("hook"), "TRANSFORM_NAME_INVALID")
            value = payload.get("value")
            if not _json_value(value):
                raise WorkerRuntimeError("TRANSFORM_VALUE_INVALID")
            try:
                result = kernel.transform(hook_name, value)
            except PluginKernelError as error:
                raise WorkerRuntimeError(error.code) from error
            if not _json_value(result):
                raise WorkerRuntimeError("TRANSFORM_RESULT_INVALID")
            return result
        if name == "callback.invoke":
            kernel = self._require_kernel()
            handle = _identifier(payload.get("handle"), "CALLBACK_INVALID")
            shape = _identifier(payload.get("shape"), "CALLBACK_SHAPE_INVALID")
            args = payload.get("args", [])
            if not isinstance(args, list) or len(args) > 32 or not _json_value(args):
                raise WorkerRuntimeError("CALLBACK_ARGUMENTS_INVALID")
            try:
                result = kernel.invoke_callback(handle, shape, args)
            except PluginKernelError as error:
                raise WorkerRuntimeError(error.code) from error
            if not _json_value(result):
                raise WorkerRuntimeError("CALLBACK_RESULT_INVALID")
            return result
        if name == "lifecycle.set_enabled":
            kernel = self._require_kernel()
            plugin_id = _identifier(payload.get("pluginId"), "PLUGIN_ID_INVALID")
            enabled = payload.get("enabled")
            if not isinstance(enabled, bool):
                raise WorkerRuntimeError("PLUGIN_ENABLED_INVALID")
            try:
                kernel.set_enabled(plugin_id, enabled)
            except PluginKernelError as error:
                raise WorkerRuntimeError(error.code) from error
            self._refresh_v3_snapshot()
            return self._status_snapshot()
        if name == "event.emit":
            event_type = _identifier(payload.get("eventType"), "EVENT_INVALID")
            is_host_event = event_type.startswith("sakura.host.")
            if event_type not in _ALLOWED_EVENTS and not is_host_event:
                raise WorkerRuntimeError("EVENT_INVALID")
            event_payload = dict(_object(payload.get("payload"), "EVENT_INVALID"))
            if event_type in {"app.start", "message.user", "message.ai", "tts.start", "tts.end"}:
                self._manager.emit_event(event_type, event_payload)
            self._manager.emit_bus_event(_bus_event_name(event_type), event_payload)
            kernel = self._require_kernel()
            try:
                kernel.emit_host_event(
                    event_type if is_host_event else _host_event_name(event_type),
                    event_payload,
                )
            except PluginKernelError as error:
                raise WorkerRuntimeError(error.code) from error
            return {"accepted": True}
        if name == "settings.get":
            return self.settings_snapshot()
        if name == "settings.save":
            plugin_id, section_id, contribution = self._settings_contribution(payload)
            values = dict(_object(payload.get("values"), "SETTINGS_VALUES_INVALID"))
            values = _editable_settings_values(contribution, values)
            if not callable(contribution.save):
                raise WorkerRuntimeError("SETTINGS_SAVE_UNAVAILABLE")
            result = contribution.save(values)
            return result if _json_value(result) else {"saved": True}
        if name == "settings.action":
            plugin_id, section_id, contribution = self._settings_contribution(payload)
            action_id = _identifier(payload.get("actionId"), "SETTINGS_ACTION_INVALID")
            action = self._actions.get((plugin_id, section_id, action_id))
            if action is None or not callable(action.handler):
                raise WorkerRuntimeError("SETTINGS_ACTION_INVALID")
            values = dict(_object(payload.get("values"), "SETTINGS_VALUES_INVALID"))
            values = _editable_settings_values(contribution, values)
            result = action.handler(values)
            if not _json_value(result):
                raise WorkerRuntimeError("SETTINGS_ACTION_RESULT_INVALID")
            return result
        if name == "worker.close":
            self.close()
            return {"closed": True}
        raise WorkerRuntimeError("PLUGIN_COMMAND_UNKNOWN")

    def initialize(self, host_service_keys: Sequence[str] = ()) -> dict[str, Any]:
        if self._snapshot is not None:
            return self._snapshot
        discovered = PluginDiscovery(self._app_root).discover()
        results = self._manager.load_all(
            continue_after_required_failure=True,
            specs=[
                spec
                for spec in discovered
                if spec.enabled and spec.api_version != PLUGIN_API_V3_VERSION
            ],
        )
        self._kernel = PluginKernelManager(
            self._app_root,
            [spec for spec in discovered if spec.api_version == PLUGIN_API_V3_VERSION],
            host_service_keys=host_service_keys,
            host_call=self._host_call,
        )
        result_ids = {result.spec.plugin_id for result in results}
        plugins: list[dict[str, Any]] = []
        prompt_patches: list[dict[str, Any]] = []
        context_providers: list[dict[str, Any]] = []
        for result in results[:64]:
            spec = result.spec
            plugin_id = spec.plugin_id[:64]
            if result.loaded and result.manifest is not None and result.capabilities is not None:
                manifest = result.manifest
                capabilities = result.capabilities
                sections = []
                unavailable = []
                if capabilities.tools_tabs:
                    unavailable.append("tools_tab")
                if capabilities.chat_ui_widgets:
                    unavailable.append("chat_ui")
                if capabilities.renderers:
                    unavailable.append("renderer")
                if PERMISSION_MOBILE_CHAT in manifest.permissions:
                    unavailable.append(PERMISSION_MOBILE_CHAT)
                for tool in capabilities.tools[:64]:
                    contribution_id = f"{plugin_id}:tool:{tool.name}"
                    self._tools[contribution_id] = tool
                for patch in capabilities.prompt_patches[:16]:
                    prompt_patches.append({
                        "pluginId": plugin_id,
                        "patchId": patch.patch_id[:64],
                        "systemPromptAppend": patch.system_prompt_append[:8192],
                        "replyProtocolAppend": patch.reply_protocol_append[:4096],
                    })
                for provider in capabilities.context_providers[:16]:
                    contribution_id = f"{plugin_id}:context:{provider.provider_id}"
                    self._contexts[contribution_id] = provider
                    context_providers.append({
                        "contributionId": contribution_id,
                        "pluginId": plugin_id,
                        "providerId": provider.provider_id[:64],
                        "description": provider.description[:240],
                        "order": provider.order,
                        "enabled": provider.enabled,
                    })
                for section in capabilities.plugin_settings[:16]:
                    self._settings[(plugin_id, section.section_id)] = section
                    for action in section.actions[:16]:
                        if not action.danger:
                            self._actions[(plugin_id, section.section_id, action.action_id)] = action
                    sections.append(_settings_section(section, load_values=False))
                plugins.append({
                    "pluginId": plugin_id,
                    "name": manifest.name[:120],
                    "version": manifest.version[:64],
                    "author": manifest.author[:120],
                    "description": manifest.description[:500],
                    "apiVersion": manifest.api_version,
                    "enabled": True,
                    "required": manifest.required,
                    "supported": True,
                    "state": "degraded" if unavailable else "ready",
                    "reasonCode": "HOST_SERVICE_UNAVAILABLE" if unavailable else "READY",
                    "permissions": list(manifest.permissions[:32]),
                    "unavailable": unavailable,
                    "sections": sections,
                })
            else:
                plugins.append({
                    "pluginId": plugin_id,
                    "name": (spec.name or plugin_id)[:120],
                    "version": spec.version[:64],
                    "author": spec.author[:120],
                    "description": spec.description[:500],
                    "apiVersion": spec.api_version,
                    "enabled": True,
                    "required": spec.required,
                    "supported": False,
                    "state": "degraded",
                    "reasonCode": _load_reason(result.error),
                    "permissions": list(spec.permissions[:32]),
                    "unavailable": [],
                    "sections": [],
                })
        for spec in discovered:
            if (
                spec.api_version == PLUGIN_API_V3_VERSION
                or spec.plugin_id in result_ids
                or spec.enabled
            ):
                continue
            plugins.append({
                "pluginId": spec.plugin_id[:64],
                "name": (spec.name or spec.plugin_id)[:120],
                "version": spec.version[:64],
                "author": spec.author[:120],
                "description": spec.description[:500],
                "apiVersion": spec.api_version,
                "enabled": False,
                "required": spec.required,
                "supported": spec.api_version == 2,
                "state": "disabled",
                "reasonCode": "PLUGIN_DISABLED",
                "permissions": list(spec.permissions[:32]),
                "unavailable": [],
                "sections": [],
            })
        plugins.extend(self._kernel.snapshot()["plugins"])
        self._initialized = True
        degraded = any(item["state"] in {"degraded", "failed", "conflict"} for item in plugins)
        self._snapshot = {
            "schemaVersion": 1,
            "state": "degraded" if degraded else "ready",
            "reasonCode": "PLUGIN_LOAD_PARTIAL" if degraded else "READY",
            "plugins": plugins,
            "tools": [
                {
                    "contributionId": contribution_id,
                    "name": tool.name,
                    "description": tool.description[:500],
                    "parameters": tool.parameters,
                    "group": tool.group[:64],
                    "risk": tool.risk if tool.risk in {"low", "medium", "high"} else "high",
                    "requiresConfirmation": bool(tool.requires_confirmation),
                    "capability": tool.capability,
                    "source": "plugin",
                }
                for contribution_id, tool in self._tools.items()
            ],
            "promptPatches": prompt_patches,
            "contextProviders": context_providers,
        }
        return self._snapshot

    def settings_snapshot(self) -> dict[str, Any]:
        self._refresh_v3_snapshot()
        assert self._snapshot is not None
        plugins = json.loads(json.dumps(self._snapshot["plugins"], ensure_ascii=False))
        for plugin in plugins:
            for index, section in enumerate(plugin["sections"]):
                contribution = self._settings.get((plugin["pluginId"], section["sectionId"]))
                if contribution is None:
                    continue
                plugin["sections"][index] = _settings_section(contribution, load_values=True)
        return {"schemaVersion": 1, "plugins": plugins}

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._tools.clear()
        self._contexts.clear()
        self._settings.clear()
        self._actions.clear()
        if self._kernel is not None:
            self._kernel.close()
            self._kernel = None
        self._manager.shutdown_all()

    def _require_kernel(self) -> PluginKernelManager:
        if self._kernel is None:
            raise WorkerRuntimeError("PLUGIN_NOT_READY")
        return self._kernel

    def _refresh_v3_snapshot(self) -> None:
        if self._snapshot is None or self._kernel is None:
            return
        v2_plugins = [
            item
            for item in self._snapshot["plugins"]
            if item.get("apiVersion") != PLUGIN_API_V3_VERSION
        ]
        plugins = [*v2_plugins, *self._kernel.snapshot()["plugins"]]
        degraded = any(item["state"] in {"degraded", "failed", "conflict"} for item in plugins)
        self._snapshot["plugins"] = plugins
        self._snapshot["state"] = "degraded" if degraded else "ready"
        self._snapshot["reasonCode"] = "PLUGIN_LOAD_PARTIAL" if degraded else "READY"

    def _status_snapshot(self) -> dict[str, Any]:
        self._refresh_v3_snapshot()
        assert self._snapshot is not None
        return json.loads(json.dumps(self._snapshot, ensure_ascii=False))

    @staticmethod
    def _contribution(values: Mapping[str, Any], raw_id: object) -> Any:
        contribution_id = _identifier(raw_id, "CONTRIBUTION_INVALID")
        if contribution_id.count(":") != 2 or contribution_id not in values:
            raise WorkerRuntimeError("CONTRIBUTION_INVALID")
        return values[contribution_id]

    def _settings_contribution(self, payload: Mapping[str, Any]) -> tuple[str, str, Any]:
        plugin_id = _identifier(payload.get("pluginId"), "SETTINGS_ID_INVALID")
        section_id = _identifier(payload.get("sectionId"), "SETTINGS_ID_INVALID")
        contribution = self._settings.get((plugin_id, section_id))
        if contribution is None:
            raise WorkerRuntimeError("SETTINGS_ID_INVALID")
        return plugin_id, section_id, contribution


def _settings_section(section: Any, *, load_values: bool) -> dict[str, Any]:
    values: dict[str, Any] = {}
    reason = "READY"
    if load_values and callable(section.load):
        try:
            loaded = section.load()
            if not isinstance(loaded, Mapping):
                raise TypeError
            values = dict(loaded)
        except Exception:
            reason = "SETTINGS_LOAD_FAILED"
    fields = []
    for field in section.fields[:32]:
        field_type = field.field_type if field.field_type in _FIELD_TYPES else "readonly"
        value = values.get(field.key, field.default)
        if not _json_value(value):
            value = field.default if _json_value(field.default) else None
        fields.append(
            {
                "key": field.key[:64],
                "label": field.label[:120],
                "type": field_type,
                "default": field.default if _json_value(field.default) else None,
                "description": field.description[:240],
                "options": _settings_options(field.options),
                "minimum": field.minimum,
                "maximum": field.maximum,
                "step": field.step,
                "required": bool(field.required),
                "readonly": bool(field.readonly),
                "copyable": bool(field.copyable),
                "restartRequired": bool(field.restart_required),
                "value": value,
            }
        )
    allowed_keys = {item["key"] for item in fields}
    projected_values = {
        item["key"]: values.get(item["key"], item["default"])
        for item in fields
        if _json_value(values.get(item["key"], item["default"]))
    }
    return {
        "sectionId": section.section_id[:64],
        "title": section.title[:120],
        "reasonCode": reason,
        "fields": fields,
        "values": {key: value for key, value in projected_values.items() if key in allowed_keys},
        "actions": [
            {
                "actionId": action.action_id[:64],
                "label": action.label[:120],
                "description": action.description[:240],
                "danger": bool(action.danger),
            }
            for action in section.actions[:16]
            if not action.danger
        ],
    }


def _editable_settings_values(section: Any, values: Mapping[str, Any]) -> dict[str, Any]:
    fields = {field.key: field for field in section.fields}
    if any(key not in fields for key in values):
        raise WorkerRuntimeError("SETTINGS_VALUES_INVALID")
    editable: dict[str, Any] = {}
    for key, value in values.items():
        field = fields[key]
        if field.readonly or field.field_type == "readonly":
            continue
        if not _field_value_valid(field, value):
            raise WorkerRuntimeError("SETTINGS_VALUES_INVALID")
        editable[key] = value
    return editable


def _field_value_valid(field: Any, value: object) -> bool:
    kind = field.field_type
    if kind in {"string", "password", "readonly", "select"}:
        if not isinstance(value, str) or len(value) > 4096:
            return False
        if kind == "select" and field.options:
            return value in {
                str(item.get("value"))
                for item in field.options
                if isinstance(item, Mapping)
            }
        return True
    if kind == "boolean":
        return isinstance(value, bool)
    if kind == "integer":
        valid = isinstance(value, int) and not isinstance(value, bool)
    elif kind == "number":
        valid = isinstance(value, (int, float)) and not isinstance(value, bool)
    else:
        return False
    if not valid:
        return False
    return not (
        isinstance(field.minimum, (int, float)) and value < field.minimum
        or isinstance(field.maximum, (int, float)) and value > field.maximum
    )


def _settings_options(raw: object) -> list[dict[str, object]]:
    if not isinstance(raw, tuple):
        return []
    options: list[dict[str, object]] = []
    for item in raw[:64]:
        if not isinstance(item, Mapping) or set(item) != {"label", "value"}:
            raise WorkerRuntimeError("SETTINGS_SCHEMA_INVALID")
        label = item.get("label")
        value = item.get("value")
        if (
            not isinstance(label, str)
            or not label
            or len(label) > 120
            or not isinstance(value, (str, int, float, bool))
        ):
            raise WorkerRuntimeError("SETTINGS_SCHEMA_INVALID")
        options.append({"label": label, "value": value})
    return options


def _context_request(raw: Mapping[str, Any]) -> ContextRequest:
    messages = []
    recent_messages = (
        raw.get("recent_messages", [])[:8]
        if isinstance(raw.get("recent_messages"), list)
        else []
    )
    for item in recent_messages:
        if (
            isinstance(item, Mapping)
            and item.get("role") in {"user", "assistant"}
            and isinstance(item.get("content"), str)
        ):
            messages.append(ContextMessage(str(item["role"]), str(item["content"])[:2000]))
    return ContextRequest(
        current_input=str(raw.get("current_input", ""))[:4096],
        character_id=str(raw.get("character_id", ""))[:64],
        character_name=str(raw.get("character_name", ""))[:120],
        source=raw.get("source") if raw.get("source") in {"chat", "event", "confirmed_action"} else "chat",
        mode=raw.get("mode") if raw.get("mode") in {"normal", "screen_awareness"} else "normal",
        event_type=str(raw.get("event_type", ""))[:64],
        step_index=_integer(raw.get("step_index"), 0, 32),
        remaining_steps=_integer(raw.get("remaining_steps"), 0, 32),
        recent_messages=tuple(messages),
        available_tools=(
            tuple(str(item)[:64] for item in raw.get("available_tools", [])[:64])
            if isinstance(raw.get("available_tools"), list)
            else ()
        ),
        visual_summaries=(),
        screen_context_available=bool(raw.get("screen_context_available")),
        seconds_since_pet_interaction=None,
        service_status={},
        current_time=str(raw.get("current_time", ""))[:80],
    )


def _context_fragment(item: object, index: int) -> dict[str, Any]:
    content = getattr(item, "content", None)
    if not isinstance(content, str):
        raise WorkerRuntimeError("CONTEXT_RESULT_INVALID")
    return {
        "fragmentId": str(getattr(item, "fragment_id", index))[:64],
        "content": content[:8192],
        "priority": _integer(getattr(item, "priority", 50), 0, 100),
        "freshness": str(getattr(item, "freshness", ""))[:80],
        "tokenBudget": _integer(getattr(item, "token_budget", 512), 1, 512),
        "sensitivity": getattr(item, "sensitivity", "private"),
    }


def _load_reason(error: object) -> str:
    text = str(error or "")
    if "API" in text:
        return "API_VERSION_UNSUPPORTED"
    if "权限" in text:
        return "PERMISSION_UNKNOWN"
    if "重复" in text or "冲突" in text:
        return "CONTRIBUTION_DUPLICATE"
    return "PLUGIN_LOAD_FAILED"


def _bus_event_name(event_type: str) -> str:
    return {
        "app.start": "app.started",
        "message.user": "chat.message.received",
        "message.ai": "chat.message.sent",
        "tts.start": "tts.started",
        "tts.end": "tts.finished",
    }.get(event_type, event_type)


def _host_event_name(event_type: str) -> str:
    return {
        "app.start": "sakura.host.app.started",
        "message.user": "sakura.host.message.received",
        "message.ai": "sakura.host.message.sent",
        "tool.started": "sakura.host.tool.started",
        "tool.finished": "sakura.host.tool.finished",
        "tool.failed": "sakura.host.tool.failed",
        "tts.start": "sakura.host.tts.started",
        "tts.end": "sakura.host.tts.ended",
    }[event_type]


def _object(value: object, code: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise WorkerRuntimeError(code)
    return value


def _identifier(value: object, code: str) -> str:
    if not isinstance(value, str) or not value or len(value) > 200:
        raise WorkerRuntimeError(code)
    return value


def _integer(value: object, minimum: int, maximum: int) -> int:
    if isinstance(value, int) and not isinstance(value, bool):
        return min(maximum, max(minimum, value))
    return minimum


def _json_value(value: object) -> bool:
    try:
        encoded = json.dumps(value, ensure_ascii=False)
    except (TypeError, ValueError):
        return False
    return len(encoded.encode("utf-8")) <= 64 * 1024


def _run(
    bridge: _WorkerBridge,
    runtime: PluginWorkerRuntime,
    generation_id: str,
    token: str,
) -> None:
    while True:
        request = bridge.read_request()
        if request is None:
            break
        request_id = request.get("id")
        valid = (
            request.get("generationId") == generation_id
            and request.get("token") == token
            and isinstance(request_id, str)
            and isinstance(request.get("name"), str)
            and isinstance(request.get("payload"), Mapping)
        )
        should_close = request.get("name") == "worker.close"
        try:
            if not valid:
                raise WorkerRuntimeError("PLUGIN_PROTOCOL_INVALID")
            payload = runtime.handle(str(request["name"]), request["payload"])
            response = {
                "generationId": generation_id,
                "token": token,
                "id": request_id,
                "ok": True,
                "payload": payload,
            }
        except WorkerRuntimeError as error:
            response = {
                "generationId": generation_id,
                "token": token,
                "id": request_id or "invalid",
                "ok": False,
                "error": {
                    "code": error.code,
                    "retryable": error.code.endswith("TIMEOUT"),
                },
            }
        except Exception as error:
            response = {
                "generationId": generation_id,
                "token": token,
                "id": request_id or "invalid",
                "ok": False,
                "error": {
                    "code": _callback_failure_code(request, error),
                    "retryable": isinstance(error, TimeoutError),
                },
            }
        bridge.write_response(response)
        if should_close:
            break
    runtime.close()


def _callback_failure_code(request: Mapping[str, Any], error: Exception) -> str:
    """Classify callback failures without returning exception text, paths or values."""
    payload = request.get("payload") if isinstance(request.get("payload"), Mapping) else {}
    contribution_id = payload.get("contributionId")
    if request.get("name") == "tool.call" and isinstance(contribution_id, str):
        if contribution_id.startswith("playwright_browser:tool:playwright_"):
            if isinstance(error, TimeoutError):
                return "PLAYWRIGHT_OPERATION_TIMEOUT"
            if contribution_id.endswith(("playwright_navigate", "playwright_search_web")):
                return "PLAYWRIGHT_NAVIGATION_FAILED"
            return "PLAYWRIGHT_OPERATION_FAILED"
    if isinstance(error, TimeoutError):
        return "PLUGIN_CALLBACK_TIMEOUT"
    if isinstance(error, ImportError):
        return "PLUGIN_DEPENDENCY_UNAVAILABLE"
    if isinstance(error, OSError):
        return "PLUGIN_CALLBACK_IO_FAILED"
    if isinstance(error, (TypeError, ValueError, KeyError)):
        return "PLUGIN_CALLBACK_DATA_INVALID"
    return "PLUGIN_CALLBACK_FAILED"


def main() -> int:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--app-root", required=True)
    parser.add_argument("--generation-id", required=True)
    parser.add_argument("--token", required=True)
    args = parser.parse_args()
    # Plugin code may print.  Protocol bytes retain the original stdout handle;
    # all plugin text is redirected away from the private framed channel.
    protocol_output = sys.stdout.buffer
    sys.stdout = sys.stderr
    bridge = _WorkerBridge(
        sys.stdin.buffer,
        protocol_output,
        args.generation_id,
        args.token,
    )
    runtime = PluginWorkerRuntime(
        Path(args.app_root).resolve(),
        args.generation_id,
        host_call=bridge.host_call,
    )
    _run(bridge, runtime, args.generation_id, args.token)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
