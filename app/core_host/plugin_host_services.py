"""Core-owned implementations of Plugin API v4 Host Services."""

from __future__ import annotations

import base64
import json
import math
import re
import secrets
import threading
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from app.plugin_sdk.sakura_tools import Tool
from app.core.runtime_log import log_event, log_message
from app.plugins.host_services import HOST_CALLER, HOST_CALLER_LOG_METADATA, HOST_CALLER_SCOPE, HOST_LOGGING_SERVICE
from app.plugin_sdk.sakura_context import ContextFragment, ContextRequest
from app.plugins.models import ContextProviderContribution


HOST_CONTEXT_SERVICE = "sakura.host.context"
HOST_DIAGNOSTICS_SERVICE = "sakura.host.diagnostics"
HOST_ARTIFACTS_SERVICE = "sakura.host.artifacts"
HOST_CHARACTER_SERVICE = "sakura.host.character"
HOST_MODEL_SLOTS_SERVICE = "sakura.host.model_slots"
HOST_SETTINGS_SERVICE = "sakura.host.settings"
HOST_SETTINGS_COLLECTION_V0_SERVICE = "sakura.host.settings.collection-v0"
HOST_SETTINGS_SURFACE_V0_SERVICE = "sakura.host.settings.surface-v0"
HOST_STORAGE_SERVICE = "sakura.host.storage"
HOST_TOOLS_SERVICE = "sakura.host.tools"
HOST_COMPOSER_TOOLS_V0_SERVICE = "sakura.host.ui.composer-tools-v0"
HOST_TIMELINE_SERVICE = "sakura.host.timeline"
_TIMELINE_RESPONSE_ENTRY_BYTES = 700 * 1024
_TOOL_CALLBACK_TIMEOUT_SECONDS = 15.0
_TOOL_NAME = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,199}$")
_COMPOSER_TOOL_PUBLIC_ID = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}:[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$"
)
_COMPOSER_TOOL_ICONS = frozenset(
    {"camera", "folder", "globe", "link", "note", "settings", "sparkles", "terminal"}
)
_SETTINGS_STATUS_STATES = frozenset(
    {"neutral", "ready", "working", "warning", "error"}
)
_SETTINGS_RESOURCE_TASK_STATES = frozenset(
    {"idle", "queued", "running", "succeeded", "failed", "cancelled"}
)
_SETTINGS_RESOURCE_APPLICABILITY = frozenset(
    {"required", "not_required", "unsupported"}
)


class HostServiceError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class _DiagnosticsHostService:
    """Compatibility adapter for plugins using the original diagnostic API."""

    def call(self, method: str, args: Sequence[Any]) -> object:
        if method != "emit" or len(args) != 2:
            raise HostServiceError("HOST_METHOD_INVALID")
        descriptor = _mapping(args[1], "DIAGNOSTIC_DESCRIPTOR_INVALID")
        event = descriptor.get("event")
        attributes = descriptor.get("attributes")
        if not isinstance(event, str) or not event.strip() or not isinstance(attributes, Mapping):
            raise HostServiceError("DIAGNOSTIC_DESCRIPTOR_INVALID")
        # Identity, redaction, size limits and persistence all use the logger.
        # args[0] is retained for wire compatibility; the caller owns identity.
        return _LoggingHostService().call("emit", [[{
            "severity": descriptor.get("severity"),
            "message": event,
            "fields": {"event": event, **attributes},
        }], 0])

    def clear(self) -> None:
        return None


class _LoggingHostService:
    def call(self, method: str, args: Sequence[Any]) -> object:
        plugin_id = HOST_CALLER.get()
        if plugin_id is None:
            raise HostServiceError("LOG_CALLER_REQUIRED")
        if method != "emit" or len(args) != 2:
            raise HostServiceError("HOST_METHOD_INVALID")
        plugin_name, provides = HOST_CALLER_LOG_METADATA.get()
        channel = "tts" if any(key == "sakura.tts" or key.startswith("sakura.tts.provider.") for key in provides) else "plugin"
        batch, dropped = args
        if (not isinstance(batch, list) or not 1 <= len(batch) <= 8
                or type(dropped) is not int or not 0 <= dropped <= 2**63 - 1):
            raise HostServiceError("LOG_PAYLOAD_INVALID")
        for item in batch:
            if (not isinstance(item, Mapping) or set(item) != {"severity", "message", "fields"}
                    or item["severity"] not in {"debug", "info", "warning", "error"}
                    or not isinstance(item["message"], str) or not isinstance(item["fields"], Mapping)):
                raise HostServiceError("LOG_PAYLOAD_INVALID")
        if dropped:
            log_message("warning", "插件日志发送拥塞或中断，部分记录已丢弃",
                fields={"dropped_count": dropped}, component=channel, plugin_id=plugin_id, plugin_name=plugin_name or None)
        for item in batch:
            if item["fields"].get("event") == "model.call.metric":
                from app.core_host.runtime_logging import submit_telemetry_model_call
                submit_telemetry_model_call(item["fields"].get("modelCall", {}))
            log_message(item["severity"], item["message"], fields=item["fields"],
                component=channel, plugin_id=plugin_id, plugin_name=plugin_name or None)
        # Core owns downstream loss accounting; the SDK counts transport loss only.
        return {"accepted": True}


class _TimelineHostService:
    def __init__(
        self,
        store: object,
        current_character_id: Callable[[], str | None],
        artifact_store: object | None = None,
    ) -> None:
        self._store = store
        self._current_character_id = current_character_id
        self._artifact_store = artifact_store
        self._history_lock = threading.RLock()
        self._history: dict[str, tuple[str, str, str, set[str]]] = {}

    def grant(self, plugin_id: str, character_id: str, snapshot_cursor: str | None = None) -> dict[str, str]:
        snapshot = snapshot_cursor if snapshot_cursor is not None else self._store.latest_cursor(character_id)
        token = secrets.token_urlsafe(24)
        with self._history_lock:
            self._history[token] = (plugin_id, character_id, snapshot, set())
        return {"historyToken": token, "snapshotCursor": snapshot}

    def revoke(self, token: str) -> None:
        with self._history_lock:
            binding = self._history.pop(token, None)
        if binding is not None and self._artifact_store is not None:
            for artifact_id in binding[3]:
                self._artifact_store.release(binding[0], artifact_id)

    def revoke_scope(self, plugin_id: str) -> None:
        with self._history_lock:
            tokens = [token for token, binding in self._history.items() if binding[0] == plugin_id]
        for token in tokens:
            self.revoke(token)

    def _read_turn_page(self, request: Mapping[str, Any]) -> object:
        from datetime import datetime

        token = request.get("historyToken")
        binding = self._history.get(token) if isinstance(token, str) else None
        if binding is None or HOST_CALLER.get() != binding[0] or (
            request.get("characterId") != binding[1] or request.get("snapshotCursor") != binding[2]
        ):
            raise HostServiceError("TIMELINE_HISTORY_UNAVAILABLE")
        page = self._store.read_turn_page(binding[1],
            category=request.get("category"), limit=request.get("limit", 16),
            before_cursor=request.get("beforeCursor"), snapshot_cursor=binding[2],
            observation_since=datetime.fromisoformat(request["observationSince"]),
            proactive_since=datetime.fromisoformat(request["proactiveSince"]),
            max_bytes=_TIMELINE_RESPONSE_ENTRY_BYTES)
        result = {"turns": [[{**_timeline_entry_mapping(entry), "sequence": entry.seq} for entry in turn]
                            for turn in page.turns],
                  "nextCursor": page.next_cursor, "snapshotCursor": page.snapshot_cursor}
        payload = json.dumps(result, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        if len(payload) <= _TIMELINE_RESPONSE_ENTRY_BYTES:
            return result
        if self._artifact_store is None:
            raise HostServiceError("TIMELINE_ARTIFACT_UNAVAILABLE")
        allocation = self._artifact_store.allocate(binding[0], {"mediaType": "application/json", "suffix": ".json"})
        artifact_id = allocation["artifactId"]
        try:
            Path(allocation["path"]).write_bytes(payload)
            descriptor = self._artifact_store.commit(binding[0], artifact_id)
        except Exception:
            self._artifact_store.release(binding[0], artifact_id)
            raise
        binding[3].add(artifact_id)
        return {"artifact": descriptor}

    def call(self, method: str, args: Sequence[Any]) -> object:
        if method == "read_turn_page" and len(args) == 1:
            try:
                request = _mapping(args[0], "TIMELINE_ARGUMENTS_INVALID")
                with self._history_lock:
                    return self._read_turn_page(request)
            except HostServiceError:
                raise
            except Exception as exc:
                code = str(exc)
                raise HostServiceError(code if code.startswith(("TIMELINE_", "ARTIFACT_")) else "TIMELINE_READ_FAILED") from exc
        character_id = self._current_character_id()
        if not isinstance(character_id, str) or not character_id:
            raise HostServiceError("TIMELINE_CHARACTER_UNAVAILABLE")
        try:
            if method == "latest_cursor" and not args:
                return {"cursor": getattr(self._store, "latest_cursor")(character_id)}
            if method == "read_recent" and len(args) == 1:
                request = _mapping(args[0], "TIMELINE_ARGUMENTS_INVALID")
                if set(request) != {"limit"}:
                    raise HostServiceError("TIMELINE_ARGUMENTS_INVALID")
                entries, cursor = getattr(self._store, "read_recent")(
                    character_id,
                    request["limit"],
                    max_bytes=_TIMELINE_RESPONSE_ENTRY_BYTES,
                )
                return {
                    "entries": [_timeline_entry_mapping(entry) for entry in entries],
                    "cursor": cursor,
                }
            if method == "read_since" and len(args) == 1:
                request = _mapping(args[0], "TIMELINE_ARGUMENTS_INVALID")
                if set(request) != {"cursor", "limit"}:
                    raise HostServiceError("TIMELINE_ARGUMENTS_INVALID")
                entries, cursor, has_more = getattr(self._store, "read_since")(
                    character_id,
                    request["cursor"],
                    request["limit"],
                    max_bytes=_TIMELINE_RESPONSE_ENTRY_BYTES,
                )
                return {
                    "entries": [_timeline_entry_mapping(entry) for entry in entries],
                    "nextCursor": cursor,
                    "hasMore": has_more,
                }
        except HostServiceError:
            raise
        except Exception as exc:
            code = str(exc)
            if code in {
                "TIMELINE_CURSOR_INVALID",
                "TIMELINE_LIMIT_INVALID",
                "TIMELINE_NOT_ACTIVATED",
                "TIMELINE_DATABASE_INVALID",
            }:
                raise HostServiceError(code) from exc
            raise HostServiceError("TIMELINE_READ_FAILED") from exc
        raise HostServiceError("HOST_METHOD_UNAVAILABLE")


class _ArtifactsHostService:
    def __init__(self, store: object, commit_scope: Callable[..., Any] | None = None) -> None:
        self._store = store
        self._commit_scope = commit_scope
        self._received: dict[str, tuple[str, str]] = {}
        self._received_lock = threading.RLock()

    def call(self, method: str, args: Sequence[Any]) -> object:
        try:
            if method == "resolve" and len(args) == 1:
                artifact = self._resolve_received(str(args[0]))
                return {"artifactId": artifact.artifact_id, "path": str(artifact.path),
                        "mediaType": artifact.media_type, "byteLength": artifact.byte_length}
            if method == "release_received" and len(args) == 1:
                self._resolve_received(str(args[0]))
                return {"released": self.release_committed(str(args[0]))}
            if method == "allocate" and len(args) == 2:
                return getattr(self._store, "allocate")(
                    _bounded_identifier(args[0], "PLUGIN_ID_INVALID", 64),
                    _mapping(args[1], "ARTIFACT_DESCRIPTOR_INVALID"),
                )
            if method == "commit" and len(args) == 2:
                return getattr(self._store, "commit")(
                    _bounded_identifier(args[0], "PLUGIN_ID_INVALID", 64),
                    _bounded_identifier(args[1], "ARTIFACT_NOT_FOUND", 200),
                )
            if method == "release" and len(args) == 2:
                return {
                    "released": self._release_owned(
                        _bounded_identifier(args[0], "PLUGIN_ID_INVALID", 64),
                        _bounded_identifier(args[1], "ARTIFACT_NOT_FOUND", 200),
                    )
                }
        except Exception as error:
            code = getattr(error, "code", "ARTIFACT_OPERATION_FAILED")
            raise HostServiceError(code if isinstance(code, str) else "ARTIFACT_OPERATION_FAILED") from error
        raise HostServiceError("HOST_METHOD_INVALID")

    def _release_owned(self, plugin_id: str, artifact_id: str) -> bool:
        with self._received_lock:
            released = self._store.release(plugin_id, artifact_id)
            if released:
                self._received.pop(artifact_id, None)
            return released

    def _resolve_received(self, artifact_id):
        with self._received_lock:
            artifact = self.resolve_committed(artifact_id)
            caller = HOST_CALLER.get()
            received = self._received.get(artifact_id)
            if (artifact.plugin_id != caller or received is not None and
                    received != (caller, HOST_CALLER_SCOPE.get())):
                raise HostServiceError("ARTIFACT_NOT_FOUND")
            return artifact

    def create_json(self, plugin_id, value):
        allocation = self._store.allocate(plugin_id, {"mediaType": "application/json", "suffix": ".json"})
        try:
            Path(allocation["path"]).write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
            return self._store.commit(plugin_id, allocation["artifactId"])
        except BaseException:
            self._store.release(plugin_id, allocation["artifactId"])
            raise

    def clear(self) -> None:
        with self._received_lock:
            self._received.clear()
            getattr(self._store, "clear")()

    def revoke_scope(self, plugin_id: str) -> None:
        with self._received_lock:
            self._received = {artifact_id: identity for artifact_id, identity in self._received.items()
                              if identity[0] != plugin_id}
            getattr(self._store, "release_plugin")(plugin_id)

    def resolve_committed(self, artifact_id: str) -> object:
        return getattr(self._store, "resolve_committed_by_id")(artifact_id)

    def release_committed(self, artifact_id: str) -> bool:
        with self._received_lock:
            self._received.pop(artifact_id, None)
            artifact = self.resolve_committed(artifact_id)
            return bool(
                getattr(self._store, "release")(
                    getattr(artifact, "plugin_id"), artifact_id,
                )
            )

    def consume_tool_result(self, value: object, *, source_plugin_id: str | None = None) -> object:
        """Resolve one explicit tool artifact envelope without crossing it back over RPC."""

        if not isinstance(value, Mapping) or set(value) != {"content", "artifact"}:
            return value
        descriptor = _mapping(value.get("artifact"), "TOOL_ARTIFACT_INVALID")
        artifact_id = descriptor.get("artifactId")
        if not isinstance(artifact_id, str):
            raise HostServiceError("TOOL_ARTIFACT_INVALID")
        cleanup_owner = None
        try:
            artifact = self.resolve_committed(artifact_id)
            if source_plugin_id is not None and artifact.plugin_id != source_plugin_id:
                raise HostServiceError("TOOL_ARTIFACT_INVALID")
            cleanup_owner = artifact.plugin_id
            media_type = getattr(artifact, "media_type", "")
            byte_length = getattr(artifact, "byte_length", -1)
            if (
                set(descriptor) != {"artifactId", "mediaType", "byteLength"}
                or descriptor.get("mediaType") != media_type
                or descriptor.get("byteLength") != byte_length
                or not isinstance(media_type, str)
                or not media_type.startswith("image/")
            ):
                raise HostServiceError("TOOL_ARTIFACT_INVALID")
            receiver_id, scope_id = HOST_CALLER.get(), HOST_CALLER_SCOPE.get()
            if receiver_id not in {None, "sakura.core"}:
                def accept():
                    nonlocal cleanup_owner
                    with self._received_lock:
                        self._store.transfer_committed(artifact.plugin_id, artifact_id, receiver_id)
                        cleanup_owner = receiver_id
                        self._received[artifact_id] = (receiver_id, scope_id)

                if not scope_id or self._commit_scope is None:
                    raise HostServiceError("ARTIFACT_RECEIVER_INVALID")
                self._commit_scope(receiver_id, scope_id, accept)
            return {"content": value.get("content"), "artifact": {
                "artifactId": artifact_id, "mediaType": media_type, "byteLength": byte_length,
            }}
        except Exception as error:
            # Invalid descriptors and late callbacks both relinquish their
            # original file; never delete an artifact another consumer owns.
            if cleanup_owner is not None:
                try:
                    self._release_owned(cleanup_owner, artifact_id)
                except Exception as cleanup_error:
                    if getattr(cleanup_error, "code", "") != "ARTIFACT_NOT_FOUND":
                        raise cleanup_error from error
            if isinstance(error, HostServiceError):
                raise
            raise HostServiceError("TOOL_ARTIFACT_INVALID") from error

    @property
    def count(self) -> int:
        return int(getattr(self._store, "count", 0))


class _CharacterHostService:
    def __init__(self, store: object) -> None:
        self._store = store
        self.workspaces = {}
        self.workspace_lock = threading.Lock()

    def call(self, method: str, args: Sequence[Any]) -> object:
        try:
            if method == "current" and len(args) == 1:
                return getattr(self._store, "current")(
                    _bounded_identifier(args[0], "PLUGIN_ID_INVALID", 64),
                )
            if method == "get" and len(args) == 2:
                return getattr(self._store, "get")(
                    _bounded_identifier(args[0], "PLUGIN_ID_INVALID", 64),
                    _bounded_identifier(args[1], "CHARACTER_NOT_FOUND", 128),
                )
            if method == "update" and len(args) == 3:
                return getattr(self._store, "update")(
                    _bounded_identifier(args[0], "PLUGIN_ID_INVALID", 64),
                    _bounded_identifier(args[1], "CHARACTER_NOT_FOUND", 128),
                    _mapping(args[2], "CHARACTER_EXTENSION_INVALID"),
                )
            if method == "resolve_resource" and len(args) == 3:
                _bounded_identifier(args[0], "PLUGIN_ID_INVALID", 64)
                with self.workspace_lock:
                    workspace = self.workspaces.get(args[1])
                if workspace is not None:
                    from app.plugins.visuals import relative_resource_path, resolve_resource_path
                    if workspace[0] != args[0]:
                        raise HostServiceError("VISUAL_WORKSPACE_DENIED")
                    return str(resolve_resource_path(workspace[1], relative_resource_path(args[2])))
                return getattr(self._store, "resolve_resource")(
                    _bounded_identifier(args[1], "CHARACTER_NOT_FOUND", 128),
                    args[2],
                )
        except Exception as error:
            code = getattr(error, "code", "CHARACTER_OPERATION_FAILED")
            raise HostServiceError(code if isinstance(code, str) else "CHARACTER_OPERATION_FAILED") from error
        raise HostServiceError("HOST_METHOD_INVALID")

    def clear(self) -> None:
        return None


class _StorageHostService:
    """Resolve explicitly shared user-data directories without plugin-ID branches."""

    def __init__(self, user_root: Path) -> None:
        self._data_root = Path(user_root) / "data"

    def call(self, method: str, args: Sequence[Any]) -> object:
        if method != "resolve" or len(args) != 2:
            raise HostServiceError("HOST_METHOD_INVALID")
        _bounded_identifier(args[0], "PLUGIN_ID_INVALID", 64)
        descriptor = _mapping(args[1], "STORAGE_DESCRIPTOR_INVALID")
        if set(descriptor) != {"scope", "name"}:
            raise HostServiceError("STORAGE_DESCRIPTOR_INVALID")
        scope = descriptor.get("scope")
        name = _bounded_identifier(
            descriptor.get("name"),
            "STORAGE_DESCRIPTOR_INVALID",
            64,
        )
        if scope == "data":
            parent = self._data_root
        elif scope == "cache":
            parent = self._data_root / "cache"
        else:
            raise HostServiceError("STORAGE_DESCRIPTOR_INVALID")
        path = (parent / name).resolve(strict=False)
        try:
            path.relative_to(parent.resolve(strict=False))
        except ValueError as error:
            raise HostServiceError("STORAGE_DESCRIPTOR_INVALID") from error
        return {"scope": scope, "name": name, "path": str(path)}

    def clear(self) -> None:
        return None


@dataclass
class _ToolRegistration:
    name: str
    tool: Tool


class _ToolsHostService:
    def __init__(
        self,
        tool_registry: object,
        invoke_callback: Callable[..., Any],
        consume_result: Callable[..., object] | None = None,
    ) -> None:
        self._tool_registry = tool_registry
        self._invoke_callback = invoke_callback
        self._consume_result = consume_result or (lambda value, **_kwargs: value)
        self._registrations: dict[str, _ToolRegistration] = {}

    def call(self, method: str, args: Sequence[Any]) -> object:
        if method == "catalog" and not args:
            return [{"registrationId": tool.registration_id, "name": tool.name,
                     "description": tool.description, "parameters": tool.parameters,
                     "group": tool.group, "risk": tool.risk, "capability": tool.capability,
                     "source": tool.source, "timeoutSeconds": tool.timeout_seconds}
                    for tool in self._tool_registry.all()]
        if method == "execute" and len(args) == 3:
            registration_id, name, arguments = args
            tool = self._tool_registry.get(name)
            if tool is None or tool.registration_id != registration_id:
                raise HostServiceError("TOOL_REGISTRATION_EXPIRED")
            # Execute the captured object; a concurrent same-name replacement cannot be substituted.
            return self._tool_registry.execute(name, dict(arguments), expected=tool).to_dict()
        if method == "register" and len(args) == 2:
            return self._register(args[0], args[1])
        if method == "unregister" and len(args) == 1:
            return {"removed": self._unregister(_registration_id(args[0]))}
        raise HostServiceError("HOST_METHOD_INVALID")

    def _register(self, raw_descriptor: object, raw_handle: object) -> dict[str, str]:
        descriptor = _mapping(raw_descriptor, "TOOL_DESCRIPTOR_INVALID")
        handle = _callback_handle(raw_handle)
        name = descriptor.get("name")
        description = descriptor.get("description")
        parameters = descriptor.get("parameters", {})
        timeout = descriptor.get("timeoutSeconds", _TOOL_CALLBACK_TIMEOUT_SECONDS)
        if (
            isinstance(timeout, bool) or not isinstance(timeout, (int, float))
            or not math.isfinite(timeout) or not 0 < timeout <= 120
        ):
            raise HostServiceError("TOOL_DESCRIPTOR_INVALID")
        if (
            not isinstance(name, str)
            or not _TOOL_NAME.fullmatch(name)
            or not isinstance(description, str)
            or not description

            or not isinstance(parameters, Mapping)
        ):
            raise HostServiceError("TOOL_DESCRIPTOR_INVALID")
        if getattr(self._tool_registry, "get")(name) is not None:
            raise HostServiceError("TOOL_NAME_CONFLICT")
        group = descriptor.get("group", "plugin")
        risk = descriptor.get("risk", "low")
        capability = descriptor.get("capability")
        if not isinstance(group, str) or not group or len(group) > 64:
            raise HostServiceError("TOOL_DESCRIPTOR_INVALID")
        if risk not in {"low", "medium", "high"}:
            raise HostServiceError("TOOL_DESCRIPTOR_INVALID")
        if capability is not None and (
            not isinstance(capability, str) or len(capability) > 64
        ):
            raise HostServiceError("TOOL_DESCRIPTOR_INVALID")

        source_plugin_id = HOST_CALLER.get()

        def handler(arguments: dict[str, Any]) -> object:
            return self._consume_result(
                self._invoke_callback(
                    handle,
                    "tools.handler",
                    arguments,
                    timeout=float(timeout),
                ),
                source_plugin_id=source_plugin_id,
            )

        tool = Tool(
            name=name,
            description=description,
            parameters=dict(parameters),
            handler=handler,
            group=group,
            risk=risk,
            capability=capability,
            source="plugin",
            timeout_seconds=float(timeout),
        )
        registration_id = _new_registration_id(self._registrations)
        try:
            getattr(self._tool_registry, "register")(tool, replace=False)
        except ValueError as error:
            raise HostServiceError("TOOL_NAME_CONFLICT") from error
        self._registrations[registration_id] = _ToolRegistration(name, tool)
        return {"registrationId": registration_id}

    def _unregister(self, registration_id: str) -> bool:
        registration = self._registrations.pop(registration_id, None)
        if registration is None:
            return False
        return bool(
            getattr(self._tool_registry, "unregister")(
                registration.name,
                expected=registration.tool,
            )
        )

    def clear(self) -> None:
        for registration_id in list(self._registrations):
            self._unregister(registration_id)

    @property
    def count(self) -> int:
        return len(self._registrations)


@dataclass
class _ContextRegistration:
    contribution: ContextProviderContribution
    callback_handle: str


class _ContextHostService:
    def __init__(
        self,
        invoke_callback: Callable[..., Any],
        encode_request: Callable[[ContextRequest], dict[str, Any]],
        on_change: Callable[[list[ContextProviderContribution]], None],
    ) -> None:
        self._invoke_callback = invoke_callback
        self._encode_request = encode_request
        self._on_change = on_change
        self._registrations: dict[str, _ContextRegistration] = {}

    def call(self, method: str, args: Sequence[Any]) -> object:
        if method == "catalog" and not args:
            return [{"registrationId": key, "providerId": item.contribution.provider_id,
                     "description": item.contribution.description, "order": item.contribution.order,
                     "enabled": item.contribution.enabled, "scope": item.contribution.scope,
                     "failurePolicy": item.contribution.failure_policy, "pluginId": item.contribution.plugin_id}
                    for key, item in self._registrations.items()]
        if method == "collect" and len(args) == 2:
            registration = self._registrations.get(_registration_id(args[0]))
            if registration is None:
                raise HostServiceError("CONTEXT_REGISTRATION_EXPIRED")
            payload = self._invoke_callback(registration.callback_handle, "context.contributor",
                                            dict(_mapping(args[1], "CONTEXT_REQUEST_INVALID")))
            if not isinstance(payload, list):
                raise HostServiceError("CONTEXT_RESULT_INVALID")
            return [asdict(_context_fragment(item, index, scope=registration.contribution.scope))
                    for index, item in enumerate(payload)]
        if method == "describe" and not args:
            return {
                "schemaVersion": 2,
                "scopes": ["step", "turn"],
                "failurePolicies": ["skip", "abort"],
            }
        if method == "register" and len(args) == 2:
            return self._register(args[0], args[1])
        if method == "unregister" and len(args) == 1:
            return {"removed": self._unregister(_registration_id(args[0]))}
        raise HostServiceError("HOST_METHOD_INVALID")

    def _register(self, raw_descriptor: object, raw_handle: object) -> dict[str, str]:
        descriptor = _mapping(raw_descriptor, "CONTEXT_DESCRIPTOR_INVALID")
        handle = _callback_handle(raw_handle)
        provider_id = descriptor.get("providerId")
        description = descriptor.get("description", "")
        order = descriptor.get("order", 100.0)
        enabled = descriptor.get("enabled", True)
        scope = descriptor.get("scope", "step")
        failure_policy = descriptor.get("failurePolicy", "skip")
        if (
            not isinstance(provider_id, str)
            or not _IDENTIFIER.fullmatch(provider_id)
            or not isinstance(description, str)

            or not isinstance(order, (int, float))
            or isinstance(order, bool)
            or not math.isfinite(order)
            or not isinstance(enabled, bool)
            or scope not in ("step", "turn")
            or failure_policy not in ("skip", "abort")
        ):
            raise HostServiceError("CONTEXT_DESCRIPTOR_INVALID")
        if any(
            item.contribution.provider_id == provider_id
            for item in self._registrations.values()
        ):
            raise HostServiceError("CONTEXT_PROVIDER_CONFLICT")

        def build_context(request: ContextRequest) -> Sequence[ContextFragment]:
            payload = self._invoke_callback(
                handle,
                "context.contributor",
                self._encode_request(request),
            )
            if not isinstance(payload, list):
                raise HostServiceError("CONTEXT_RESULT_INVALID")
            return tuple(
                _context_fragment(item, index, scope=scope)
                for index, item in enumerate(payload)
            )

        contribution = ContextProviderContribution(
            provider_id=provider_id,
            description=description,
            build_context=build_context,
            order=float(order),
            enabled=enabled,
            scope=scope,
            failure_policy=failure_policy,
            plugin_id=HOST_CALLER.get() or "",
        )
        registration_id = _new_registration_id(self._registrations)
        self._registrations[registration_id] = _ContextRegistration(contribution, handle)
        self._publish()
        return {"registrationId": registration_id}

    def _unregister(self, registration_id: str) -> bool:
        removed = self._registrations.pop(registration_id, None) is not None
        if removed:
            self._publish()
        return removed

    def providers(self) -> list[ContextProviderContribution]:
        return [item.contribution for item in self._registrations.values()]

    def clear(self) -> None:
        if not self._registrations:
            return
        self._registrations.clear()
        self._publish()

    def _publish(self) -> None:
        self._on_change(self.providers())


@dataclass
class _SettingsRegistration:
    plugin_id: str
    section_id: str
    title: str
    fields: tuple[dict[str, Any], ...]
    actions: tuple[dict[str, Any], ...]
    collections: tuple["_SettingsCollection", ...]
    load_handle: str | None
    save_handle: str | None
    action_handles: dict[str, str]
    order: float
    surface: str | None
    application_state: str = "applied"
    reason_code: str = "READY"
    descriptor_invalid: bool = False


@dataclass(frozen=True)
class _SettingsCollection:
    collection_id: str
    title: str
    description: str
    scope: str
    columns: tuple[dict[str, Any], ...]
    fields: tuple[dict[str, Any], ...]
    filters: tuple[dict[str, Any], ...]
    searchable: bool
    page_size: int
    delete_confirmation: str
    query_handle: str
    create_handle: str | None
    update_handle: str | None
    delete_handle: str | None


@dataclass(frozen=True)
class _ModelSlotRegistration:
    plugin_id: str
    slot_id: str
    label: str
    description: str
    model_kind: str
    required: bool
    order: float
    load_handle: str
    save_handle: str


class _ModelSlotsHostService:
    def __init__(
        self,
        invoke_callback: Callable[..., Any],
        catalog: Callable[[], list[dict[str, object]]] | None = None,
        resolver: Callable[[Mapping[str, Any]], dict[str, object]] | None = None,
        active_resolver: Callable[[], dict[str, object]] | None = None,
    ) -> None:
        self._invoke_callback = invoke_callback
        self._catalog = catalog
        self._resolver = resolver
        self._active_resolver = active_resolver
        self._registrations: dict[str, _ModelSlotRegistration] = {}
        self._lock = threading.RLock()

    def call(self, method: str, args: Sequence[Any]) -> object:
        if method == "active" and not args:
            if self._active_resolver is None:
                raise HostServiceError("MODEL_CATALOG_UNAVAILABLE")
            return self._active_resolver()
        if method == "catalog" and not args:
            if self._catalog is None:
                raise HostServiceError("MODEL_CATALOG_UNAVAILABLE")
            try:
                return self._catalog()
            except Exception as error:
                raise HostServiceError("MODEL_CATALOG_UNAVAILABLE") from error
        if method == "resolve" and len(args) == 1:
            if self._resolver is None:
                raise HostServiceError("MODEL_CATALOG_UNAVAILABLE")
            selection = _model_slot_selection(args[0])
            try:
                return self._resolver(selection)
            except Exception as error:
                code = str(error)
                raise HostServiceError(
                    code
                    if code in {"MODEL_SLOT_SELECTION_INVALID", "MODEL_REFERENCE_INVALID"}
                    else "MODEL_CATALOG_UNAVAILABLE"
                ) from error
        if method == "register" and len(args) == 3:
            return self._register(args[0], args[1], args[2])
        if method == "unregister" and len(args) == 1:
            registration_id = _bounded_identifier(args[0], "MODEL_SLOT_REGISTRATION_INVALID", 200)
            with self._lock:
                return {"removed": self._registrations.pop(registration_id, None) is not None}
        raise HostServiceError("HOST_METHOD_INVALID")

    def _register(self, raw_plugin_id: object, raw_descriptor: object, raw_handles: object) -> dict[str, str]:
        plugin_id = _bounded_identifier(raw_plugin_id, "PLUGIN_ID_INVALID", 64)
        descriptor = _mapping(raw_descriptor, "MODEL_SLOT_DESCRIPTOR_INVALID")
        if set(descriptor) != {
            "slotId", "label", "description", "modelKind", "required", "order"
        }:
            raise HostServiceError("MODEL_SLOT_DESCRIPTOR_INVALID")
        slot_id = _bounded_identifier(descriptor.get("slotId"), "MODEL_SLOT_DESCRIPTOR_INVALID", 64)
        label = descriptor.get("label")
        description = descriptor.get("description")
        model_kind = descriptor.get("modelKind")
        required = descriptor.get("required")
        order = descriptor.get("order")
        if (
            not isinstance(label, str) or not 1 <= len(label) <= 120
            or not isinstance(description, str)
            or model_kind != "chat_completion"
            or not isinstance(required, bool)
            or isinstance(order, bool) or not isinstance(order, (int, float))
        ):
            raise HostServiceError("MODEL_SLOT_DESCRIPTOR_INVALID")
        handles = _mapping(raw_handles, "MODEL_SLOT_CALLBACK_INVALID")
        if set(handles) != {"load", "save"}:
            raise HostServiceError("MODEL_SLOT_CALLBACK_INVALID")
        load_handle = _callback_handle(handles.get("load"))
        save_handle = _callback_handle(handles.get("save"))
        registration = _ModelSlotRegistration(
            plugin_id=plugin_id,
            slot_id=slot_id,
            label=label,
            description=description,
            model_kind=model_kind,
            required=required,
            order=float(order),
            load_handle=load_handle,
            save_handle=save_handle,
        )
        identity = f"plugin:{plugin_id}:{slot_id}"
        with self._lock:
            if any(
                item.plugin_id == plugin_id and item.slot_id == slot_id
                for item in self._registrations.values()
            ):
                raise HostServiceError("MODEL_SLOT_CONFLICT")
            registration_id = _new_registration_id(self._registrations)
            self._registrations[registration_id] = registration
        return {"registrationId": registration_id, "identity": identity}

    def snapshot(self) -> list[dict[str, Any]]:
        with self._lock:
            registrations = sorted(
                self._registrations.values(),
                key=lambda item: (item.order, item.plugin_id, item.slot_id),
            )[:32]
        result: list[dict[str, Any]] = []
        for item in registrations:
            reason_code = "READY"
            try:
                selection = _model_slot_selection(
                    self._invoke_callback(item.load_handle, "model_slots.load")
                )
            except Exception:
                selection = {"profileId": "", "model": ""}
                reason_code = "MODEL_SLOT_LOAD_FAILED"
            result.append({
                "identity": f"plugin:{item.plugin_id}:{item.slot_id}",
                "ownerType": "plugin",
                "ownerId": item.plugin_id,
                "slotId": item.slot_id,
                "label": item.label,
                "description": item.description,
                "modelKind": item.model_kind,
                "required": item.required,
                "order": item.order,
                "selection": selection,
                "reasonCode": reason_code,
            })
        return result

    def save(self, identity: str, raw_selection: Mapping[str, Any]) -> object:
        selection = _model_slot_selection(raw_selection)
        with self._lock:
            registration = next(
                (
                    item for item in self._registrations.values()
                    if f"plugin:{item.plugin_id}:{item.slot_id}" == identity
                ),
                None,
            )
        if registration is None:
            raise HostServiceError("MODEL_SLOT_UNAVAILABLE")
        result = self._invoke_callback(
            registration.save_handle,
            "model_slots.save",
            selection,
        )
        _application_state(result)
        return result

    @property
    def count(self) -> int:
        with self._lock:
            return len(self._registrations)

    def clear(self) -> None:
        with self._lock:
            self._registrations.clear()


class _SettingsHostService:
    def __init__(
        self,
        invoke_callback: Callable[..., Any],
    ) -> None:
        self._invoke_callback = invoke_callback
        self._registrations: dict[str, _SettingsRegistration] = {}
        self._surface_registrations: dict[str, tuple[str, str, str]] = {}
        self._collection_registrations: dict[
            str,
            tuple[str, str, _SettingsCollection],
        ] = {}
        self._lock = threading.RLock()

    def call(self, method: str, args: Sequence[Any]) -> object:
        if method == "register" and len(args) == 3:
            return self._register(args[0], args[1], args[2])
        if method == "unregister" and len(args) == 1:
            return {"removed": self._unregister(_registration_id(args[0]))}
        raise HostServiceError("HOST_METHOD_INVALID")

    def _register(
        self,
        raw_plugin_id: object,
        raw_descriptor: object,
        raw_handles: object,
    ) -> dict[str, str]:
        plugin_id = _bounded_identifier(raw_plugin_id, "PLUGIN_ID_INVALID", 64)
        descriptor = _mapping(raw_descriptor, "SETTINGS_DESCRIPTOR_INVALID")
        section_id = _bounded_identifier(
            descriptor.get("sectionId"),
            "SETTINGS_DESCRIPTOR_INVALID",
            64,
        )
        title = descriptor.get("title")
        order = descriptor.get("order", 100.0)
        raw_fields = descriptor.get("fields", [])
        raw_actions = descriptor.get("actions", [])
        if (
            not isinstance(title, str)
            or not title

            or not isinstance(order, (int, float))
            or isinstance(order, bool)
            or not isinstance(raw_fields, list)

            or not isinstance(raw_actions, list)

        ):
            raise HostServiceError("SETTINGS_DESCRIPTOR_INVALID")
        fields = []
        field_keys = set()
        descriptor_invalid = False
        for item in raw_fields:
            try:
                field = _settings_field(item)
                if field["key"] in field_keys:
                    raise HostServiceError("SETTINGS_DESCRIPTOR_INVALID")
            except HostServiceError:
                descriptor_invalid = True
                continue
            fields.append(field)
            field_keys.add(field["key"])
        actions = []
        declared_action_ids = set()
        for item in raw_actions:
            try:
                action = _settings_action(item)
                if action["actionId"] in declared_action_ids:
                    raise HostServiceError("SETTINGS_DESCRIPTOR_INVALID")
            except HostServiceError:
                descriptor_invalid = True
                continue
            actions.append(action)
            declared_action_ids.add(action["actionId"])
        valid_fields = [field for field in fields if set(field["actionIds"]).issubset(declared_action_ids)]
        descriptor_invalid |= len(valid_fields) != len(fields)
        fields = valid_fields
        field_keys = {field["key"] for field in fields}
        for field in fields:
            condition = field["enabledWhen"]
            if condition is not None and (
                condition["field"] not in field_keys or condition["field"] == field["key"]
            ):
                field["readonly"] = True
                field["enabledWhen"] = None
                descriptor_invalid = True
        handles = _mapping(raw_handles, "SETTINGS_CALLBACK_INVALID")
        if set(handles) != {"load", "save", "actions"}:
            raise HostServiceError("SETTINGS_CALLBACK_INVALID")
        load_handle = _optional_callback_handle(handles.get("load"))
        save_handle = _optional_callback_handle(handles.get("save"))
        raw_action_handles = _mapping(
            handles.get("actions"),
            "SETTINGS_CALLBACK_INVALID",
        )
        action_ids = {action["actionId"] for action in actions}
        if not action_ids.issubset(raw_action_handles):
            raise HostServiceError("SETTINGS_CALLBACK_INVALID")
        action_handles = {
            action_id: _callback_handle(raw_action_handles[action_id])
            for action_id in action_ids
        }
        if any(not field["readonly"] for field in fields) and save_handle is None:
            raise HostServiceError("SETTINGS_CALLBACK_INVALID")

        registration = _SettingsRegistration(
            plugin_id=plugin_id,
            section_id=section_id,
            title=title,
            fields=tuple(fields),
            actions=tuple(actions),
            collections=(),
            load_handle=load_handle,
            save_handle=save_handle,
            action_handles=action_handles,
            order=float(order),
            surface=None,
            descriptor_invalid=descriptor_invalid,
        )
        with self._lock:
            if any(
                item.plugin_id == plugin_id and item.section_id == section_id
                for item in self._registrations.values()
            ):
                raise HostServiceError("SETTINGS_SECTION_CONFLICT")
            registration_id = _new_registration_id(self._registrations)
            self._registrations[registration_id] = registration
        return {"registrationId": registration_id}

    def _unregister(self, registration_id: str) -> bool:
        with self._lock:
            return self._registrations.pop(registration_id, None) is not None

    def register_surface(
        self,
        raw_plugin_id: object,
        raw_section_id: object,
        raw_surface: object,
    ) -> dict[str, str]:
        plugin_id = _bounded_identifier(raw_plugin_id, "PLUGIN_ID_INVALID", 64)
        section_id = _bounded_identifier(
            raw_section_id,
            "SETTINGS_SURFACE_INVALID",
            64,
        )
        surface = _bounded_identifier(raw_surface, "SETTINGS_SURFACE_INVALID", 64)
        with self._lock:
            registration = self._find_locked(plugin_id, section_id)
            if registration is None:
                raise HostServiceError("SETTINGS_SECTION_INVALID")
            if surface == "about":
                referenced_actions = {
                    action_id
                    for field in registration.fields
                    for action_id in field["actionIds"]
                }
                declared_actions = {
                    action["actionId"] for action in registration.actions
                }
                has_collection = any(
                    owner == plugin_id and section == section_id
                    for owner, section, _item in self._collection_registrations.values()
                )
                if (
                    registration.load_handle is None
                    or registration.save_handle is not None
                    or not registration.fields
                    or any(
                        field["type"] != "resource" or not field["readonly"]
                        for field in registration.fields
                    )
                    or referenced_actions != declared_actions
                    or has_collection
                ):
                    raise HostServiceError("SETTINGS_SURFACE_INVALID")
            if any(
                owner == plugin_id and section == section_id
                for owner, section, _surface in self._surface_registrations.values()
            ):
                raise HostServiceError("SETTINGS_SURFACE_CONFLICT")
            registration_id = _new_registration_id(self._surface_registrations)
            self._surface_registrations[registration_id] = (
                plugin_id,
                section_id,
                surface,
            )
        return {"registrationId": registration_id}

    def unregister_surface(self, registration_id: str) -> bool:
        with self._lock:
            return self._surface_registrations.pop(registration_id, None) is not None

    def register_collection(
        self,
        raw_plugin_id: object,
        raw_section_id: object,
        raw_descriptor: object,
        raw_handles: object,
    ) -> dict[str, str]:
        plugin_id = _bounded_identifier(raw_plugin_id, "PLUGIN_ID_INVALID", 64)
        section_id = _bounded_identifier(
            raw_section_id,
            "SETTINGS_COLLECTION_INVALID",
            64,
        )
        descriptor = _settings_collection(raw_descriptor)
        collection = _collection_with_handles(descriptor, raw_handles)
        with self._lock:
            if self._find_locked(plugin_id, section_id) is None:
                raise HostServiceError("SETTINGS_SECTION_INVALID")
            if any(
                owner == plugin_id and section == section_id and surface == "about"
                for owner, section, surface in self._surface_registrations.values()
            ):
                raise HostServiceError("SETTINGS_COLLECTION_INVALID")
            if any(
                owner == plugin_id
                and section == section_id
                and item.collection_id == collection.collection_id
                for owner, section, item in self._collection_registrations.values()
            ):
                raise HostServiceError("SETTINGS_COLLECTION_CONFLICT")
            registration_id = _new_registration_id(self._collection_registrations)
            self._collection_registrations[registration_id] = (
                plugin_id,
                section_id,
                collection,
            )
        return {"registrationId": registration_id}

    def unregister_collection(self, registration_id: str) -> bool:
        with self._lock:
            return self._collection_registrations.pop(registration_id, None) is not None

    def sections_for_plugin(self, plugin_id: str) -> list[dict[str, Any]]:
        with self._lock:
            registrations = sorted(
                (
                    registration
                    for registration in self._registrations.values()
                    if registration.plugin_id == plugin_id
                ),
                key=lambda item: (item.order, item.section_id),
            )[:16]
        return [self._section_snapshot(registration) for registration in registrations]

    def sections_for_surface(self, surface: str) -> list[dict[str, Any]]:
        if not isinstance(surface, str) or not _IDENTIFIER.fullmatch(surface):
            raise HostServiceError("SETTINGS_SURFACE_INVALID")
        with self._lock:
            surfaced = {
                (plugin_id, section_id)
                for plugin_id, section_id, registered_surface
                in self._surface_registrations.values()
                if registered_surface == surface
            }
            registrations = sorted(
                (
                    registration
                    for registration in self._registrations.values()
                    if (registration.plugin_id, registration.section_id) in surfaced
                ),
                key=lambda item: (item.order, item.plugin_id, item.section_id),
            )[:32]
        return [
            {"pluginId": registration.plugin_id, **self._section_snapshot(registration)}
            for registration in registrations
        ]

    def _section_snapshot(self, registration: _SettingsRegistration) -> dict[str, Any]:
        values: Mapping[str, Any] = {}
        reason_code = registration.reason_code
        if reason_code in {"READY", "SETTINGS_VALUE_INVALID"} and registration.descriptor_invalid:
            reason_code = "SETTINGS_DESCRIPTOR_INVALID"
        if registration.load_handle is not None:
            try:
                loaded = self._invoke_callback(
                    registration.load_handle,
                    "settings.load",
                )
                values = _mapping(loaded, "SETTINGS_LOAD_FAILED")
            except Exception as error:
                reason_code = (
                    "PLUGIN_CONFIG_INVALID"
                    if getattr(error, "code", "") == "PLUGIN_CONFIG_INVALID"
                    else "SETTINGS_LOAD_FAILED"
                )
                values = {}
        projected_values, invalid_value = _settings_display_values(registration.fields, values)
        if invalid_value and reason_code == "READY":
            reason_code = "SETTINGS_VALUE_INVALID"
        fields = [
            {**spec, "value": projected_values[spec["key"]]}
            for spec in registration.fields
        ]
        actions = [dict(action) for action in registration.actions]
        with self._lock:
            surface = next(
                (
                    value
                    for plugin_id, section_id, value
                    in self._surface_registrations.values()
                    if plugin_id == registration.plugin_id
                    and section_id == registration.section_id
                ),
                None,
            )
            collections = [
                item
                for plugin_id, section_id, item
                in self._collection_registrations.values()
                if plugin_id == registration.plugin_id
                and section_id == registration.section_id
            ][:4]
        return {
            "sectionId": registration.section_id,
            "title": registration.title,
            "surface": surface,
            "reasonCode": reason_code,
            "fields": fields,
            "values": projected_values,
            "actions": actions,
            "collections": [
                _public_collection(collection) for collection in collections
            ],
        }

    def collection(
        self,
        operation: str,
        plugin_id: str,
        section_id: str,
        collection_id: str,
        payload: Mapping[str, Any],
    ) -> object:
        registration = self._find(plugin_id, section_id)
        if registration is None:
            raise HostServiceError("SETTINGS_COLLECTION_INVALID")
        with self._lock:
            collection = next(
                (
                    item
                    for owner, section, item
                    in self._collection_registrations.values()
                    if owner == plugin_id
                    and section == section_id
                    and item.collection_id == collection_id
                ),
                None,
            )
        if collection is None:
            raise HostServiceError("SETTINGS_COLLECTION_INVALID")
        if operation == "query":
            request = _collection_query_request(collection, payload)
            result = self._invoke_callback(
                collection.query_handle,
                "settings.collection.query",
                request,
            )
            return _collection_query_result(collection, result, request["limit"])
        if operation in {"create", "update"}:
            values = _editable_settings_values(collection.fields, _mapping(
                payload.get("values"),
                "SETTINGS_COLLECTION_VALUES_INVALID",
            ))
            if operation == "create":
                if set(payload) != {"values"} or collection.create_handle is None:
                    raise HostServiceError("SETTINGS_COLLECTION_OPERATION_UNAVAILABLE")
                if any(
                    field["required"]
                    and not field["readonly"]
                    and (
                        field["key"] not in values
                        or values[field["key"]] is None
                    )
                    for field in collection.fields
                ):
                    raise HostServiceError("SETTINGS_COLLECTION_VALUES_INVALID")
                result = self._invoke_callback(
                    collection.create_handle,
                    "settings.collection.create",
                    values,
                )
            else:
                if set(payload) != {"itemId", "values"} or collection.update_handle is None:
                    raise HostServiceError("SETTINGS_COLLECTION_OPERATION_UNAVAILABLE")
                item_id = _collection_item_id(payload.get("itemId"))
                result = self._invoke_callback(
                    collection.update_handle,
                    "settings.collection.update",
                    item_id,
                    values,
                )
            return _collection_item(collection, result)
        if operation == "delete":
            if set(payload) != {"itemId"} or collection.delete_handle is None:
                raise HostServiceError("SETTINGS_COLLECTION_OPERATION_UNAVAILABLE")
            result = self._invoke_callback(
                collection.delete_handle,
                "settings.collection.delete",
                _collection_item_id(payload.get("itemId")),
            )
            if not isinstance(result, Mapping) or set(result) != {"deleted"} or not isinstance(
                result.get("deleted"), bool
            ):
                raise HostServiceError("SETTINGS_COLLECTION_RESULT_INVALID")
            return {"deleted": result["deleted"]}
        raise HostServiceError("SETTINGS_COLLECTION_OPERATION_INVALID")

    def save(
        self,
        plugin_id: str,
        section_id: str,
        values: Mapping[str, Any],
    ) -> tuple[bool, object]:
        registration = self._find(plugin_id, section_id)
        if registration is None:
            return False, None
        editable = _editable_settings_values(registration.fields, values)
        if registration.save_handle is None:
            raise HostServiceError("SETTINGS_SAVE_UNAVAILABLE")
        result = self._invoke_callback(
            registration.save_handle,
            "settings.save",
            editable,
        )
        state = _application_state(result)
        reason_code = {
            "applied": "READY",
            "restart_required": "CONFIG_RELOAD_REQUIRED",
            "error": "CONFIG_APPLY_FAILED",
        }[state]
        with self._lock:
            if registration in self._registrations.values():
                registration.application_state = state
                registration.reason_code = reason_code
        return True, {
            "saved": True,
            "applicationState": state,
            "reasonCode": reason_code,
        }

    def action(
        self,
        plugin_id: str,
        section_id: str,
        action_id: str,
        values: Mapping[str, Any],
    ) -> tuple[bool, object]:
        registration = self._find(plugin_id, section_id)
        if registration is None:
            return False, None
        editable = _editable_settings_values(registration.fields, values)
        handle = registration.action_handles.get(action_id)
        if handle is None:
            raise HostServiceError("SETTINGS_ACTION_INVALID")
        result = self._invoke_callback(handle, "settings.action", editable)
        if not isinstance(result, Mapping) or any(
            key not in {"values", "message"} for key in result
        ):
            raise HostServiceError("SETTINGS_ACTION_RESULT_INVALID")
        public = dict(result)
        invalid_value = False
        if "values" in public:
            if not isinstance(public["values"], Mapping):
                raise HostServiceError("SETTINGS_ACTION_RESULT_INVALID")
            # Action values are a display patch, not another write request.
            # Omitted controls and plugin-private keys cannot invalidate an
            # already executed action; absent fields must not reset UI drafts.
            fields = [field for field in registration.fields if field["key"] in public["values"]]
            public["values"], invalid_value = _settings_display_values(fields, public["values"])
        if "message" in public and (
            not isinstance(public["message"], str)
            or len(public["message"]) > 240
        ):
            raise HostServiceError("SETTINGS_ACTION_RESULT_INVALID")
        if not _json_compatible(public):
            raise HostServiceError("SETTINGS_ACTION_RESULT_INVALID")
        if "values" in public:
            with self._lock:
                if registration in self._registrations.values() and registration.reason_code in {
                    "READY", "SETTINGS_VALUE_INVALID",
                }:
                    registration.reason_code = "SETTINGS_VALUE_INVALID" if invalid_value else "READY"
        return True, public

    def _find(self, plugin_id: str, section_id: str) -> _SettingsRegistration | None:
        with self._lock:
            return self._find_locked(plugin_id, section_id)

    def _find_locked(
        self,
        plugin_id: str,
        section_id: str,
    ) -> _SettingsRegistration | None:
        return next(
            (
                registration
                for registration in self._registrations.values()
                if registration.plugin_id == plugin_id
                and registration.section_id == section_id
            ),
            None,
        )

    def clear(self) -> None:
        with self._lock:
            self._registrations.clear()
            self._surface_registrations.clear()
            self._collection_registrations.clear()

    @property
    def count(self) -> int:
        with self._lock:
            return len(self._registrations)


class _SettingsSurfaceV0HostService:
    def __init__(self, settings: _SettingsHostService) -> None:
        self._settings = settings

    def call(self, method: str, args: Sequence[Any]) -> object:
        if method == "register" and len(args) == 3:
            return self._settings.register_surface(args[0], args[1], args[2])
        if method == "unregister" and len(args) == 1:
            return {
                "removed": self._settings.unregister_surface(
                    _registration_id(args[0])
                )
            }
        raise HostServiceError("HOST_METHOD_INVALID")


class _SettingsCollectionV0HostService:
    def __init__(self, settings: _SettingsHostService) -> None:
        self._settings = settings

    def call(self, method: str, args: Sequence[Any]) -> object:
        if method == "register" and len(args) == 4:
            return self._settings.register_collection(
                args[0],
                args[1],
                args[2],
                args[3],
            )
        if method == "unregister" and len(args) == 1:
            return {
                "removed": self._settings.unregister_collection(
                    _registration_id(args[0])
                )
            }
        raise HostServiceError("HOST_METHOD_INVALID")


@dataclass
class _ComposerToolRegistration:
    registration_id: str
    plugin_id: str
    tool_id: str
    label: str
    description: str
    icon: str
    order: float
    handle: str

    @property
    def public_id(self) -> str:
        return f"{self.plugin_id}:{self.tool_id}"


class _ComposerToolsV0HostService:
    """Own declarative, host-rendered actions for the composer tool dock."""

    def __init__(self, invoke_callback: Callable[..., Any]) -> None:
        self._invoke_callback = invoke_callback
        self._registrations: dict[str, _ComposerToolRegistration] = {}

    def call(self, method: str, args: Sequence[Any]) -> object:
        if method == "register" and len(args) == 3:
            return self._register(args[0], args[1], args[2])
        if method == "unregister" and len(args) == 1:
            return {"removed": self._unregister(_registration_id(args[0]))}
        raise HostServiceError("HOST_METHOD_INVALID")

    def _register(
        self,
        raw_plugin_id: object,
        raw_descriptor: object,
        raw_handle: object,
    ) -> dict[str, str]:
        plugin_id = _bounded_identifier(raw_plugin_id, "PLUGIN_ID_INVALID", 64)
        descriptor = _mapping(raw_descriptor, "COMPOSER_TOOL_DESCRIPTOR_INVALID")
        handle = _callback_handle(raw_handle)
        if not set(descriptor).issubset(
            {"toolId", "label", "description", "icon", "order"}
        ):
            raise HostServiceError("COMPOSER_TOOL_DESCRIPTOR_INVALID")
        tool_id = descriptor.get("toolId")
        label = descriptor.get("label")
        description = descriptor.get("description", "")
        icon = descriptor.get("icon", "sparkles")
        order = descriptor.get("order", 100.0)
        if (
            not isinstance(tool_id, str)
            or not _IDENTIFIER.fullmatch(tool_id)
            or len(tool_id) > 64
            or not isinstance(label, str)
            or not label.strip()

            or not isinstance(description, str)

            or icon not in _COMPOSER_TOOL_ICONS
            or not isinstance(order, (int, float))
            or isinstance(order, bool)
            or not -10_000 <= float(order) <= 10_000
        ):
            raise HostServiceError("COMPOSER_TOOL_DESCRIPTOR_INVALID")
        public_id = f"{plugin_id}:{tool_id}"
        if any(item.public_id == public_id for item in self._registrations.values()):
            raise HostServiceError("COMPOSER_TOOL_CONFLICT")
        registration_id = _new_registration_id(self._registrations)
        self._registrations[registration_id] = _ComposerToolRegistration(
            registration_id=registration_id,
            plugin_id=plugin_id,
            tool_id=tool_id,
            label=label.strip(),
            description=description.strip(),
            icon=icon,
            order=float(order),
            handle=handle,
        )
        return {"registrationId": registration_id}

    def _unregister(self, registration_id: str) -> bool:
        return self._registrations.pop(registration_id, None) is not None

    def snapshot(self) -> list[dict[str, object]]:
        ordered = sorted(
            self._registrations.values(),
            key=lambda item: (item.order, item.label.casefold(), item.public_id),
        )
        return [
            {
                "id": item.public_id,
                "pluginId": item.plugin_id,
                "toolId": item.tool_id,
                "label": item.label,
                "description": item.description,
                "icon": item.icon,
                "order": item.order,
            }
            for item in ordered[:64]
        ]

    def invoke(self, public_id: str) -> dict[str, str]:
        if not isinstance(public_id, str) or not _COMPOSER_TOOL_PUBLIC_ID.fullmatch(public_id):
            raise HostServiceError("COMPOSER_TOOL_ID_INVALID")
        registration = next(
            (item for item in self._registrations.values() if item.public_id == public_id),
            None,
        )
        if registration is None:
            raise HostServiceError("COMPOSER_TOOL_NOT_FOUND")
        result = self._invoke_callback(
            registration.handle,
            "ui.composer_tool.invoke",
            {"source": "composer"},
        )
        if result is None:
            return {"status": "completed", "message": ""}
        if not isinstance(result, Mapping) or not set(result).issubset({"status", "message"}):
            raise HostServiceError("COMPOSER_TOOL_RESULT_INVALID")
        status = result.get("status", "completed")
        message = result.get("message", "")
        if status != "completed" or not isinstance(message, str) or len(message) > 200:
            raise HostServiceError("COMPOSER_TOOL_RESULT_INVALID")
        return {"status": status, "message": message}

    def clear(self) -> None:
        self._registrations.clear()


class PluginHostServices:
    """Generation-bound generic dispatcher; it does not import plugin code."""

    def grant_visual_workspace(self, plugin_id, package_dir):
        token = "studio-" + secrets.token_hex(16)
        with self._character.workspace_lock:
            self._character.workspaces[token] = (plugin_id, Path(package_dir).resolve(strict=True))
        return token

    def revoke_visual_workspace(self, token):
        with self._character.workspace_lock:
            self._character.workspaces.pop(token, None)

    def __init__(
        self,
        tool_registry: object,
        *,
        artifact_store: object,
        character_store: object,
        timeline_store: object,
        current_character_id: Callable[[], str | None],
        invoke_callback: Callable[..., Any],
        encode_context_request: Callable[[ContextRequest], dict[str, Any]],
        on_context_change: Callable[[list[ContextProviderContribution]], None],
        storage_root: Path | None = None,
        model_catalog: Callable[[], list[dict[str, object]]] | None = None,
        model_resolver: Callable[[Mapping[str, Any]], dict[str, object]] | None = None,
        active_model_resolver: Callable[[], dict[str, object]] | None = None,
        commit_plugin_scope: Callable[..., Any] | None = None,
    ) -> None:
        self._artifacts = _ArtifactsHostService(artifact_store, commit_plugin_scope)
        self._diagnostics = _DiagnosticsHostService()
        self._character = _CharacterHostService(character_store)
        self._timeline = _TimelineHostService(timeline_store, current_character_id, artifact_store)
        self._tools = _ToolsHostService(
            tool_registry,
            invoke_callback,
            self._artifacts.consume_tool_result,
        )
        self._context = _ContextHostService(
            invoke_callback,
            encode_context_request,
            on_context_change,
        )
        self._settings = _SettingsHostService(invoke_callback)
        self._settings_surface_v0 = _SettingsSurfaceV0HostService(self._settings)
        self._settings_collection_v0 = _SettingsCollectionV0HostService(self._settings)
        self._model_slots = _ModelSlotsHostService(
            invoke_callback,
            catalog=model_catalog,
            resolver=model_resolver,
            active_resolver=active_model_resolver,
        )
        self._storage = (
            _StorageHostService(storage_root) if storage_root is not None else None
        )
        self._composer_tools_v0 = _ComposerToolsV0HostService(invoke_callback)
        self._services = {
            HOST_LOGGING_SERVICE: _LoggingHostService(),
            HOST_ARTIFACTS_SERVICE: self._artifacts,
            HOST_DIAGNOSTICS_SERVICE: self._diagnostics,
            HOST_CHARACTER_SERVICE: self._character,
            HOST_TOOLS_SERVICE: self._tools,
            HOST_CONTEXT_SERVICE: self._context,
            HOST_MODEL_SLOTS_SERVICE: self._model_slots,
            HOST_SETTINGS_SERVICE: self._settings,
            HOST_SETTINGS_SURFACE_V0_SERVICE: self._settings_surface_v0,
            HOST_SETTINGS_COLLECTION_V0_SERVICE: self._settings_collection_v0,
            HOST_COMPOSER_TOOLS_V0_SERVICE: self._composer_tools_v0,
            HOST_TIMELINE_SERVICE: self._timeline,
        }
        if self._storage is not None:
            self._services[HOST_STORAGE_SERVICE] = self._storage

    @property
    def available_keys(self) -> tuple[str, ...]:
        return tuple(self._services)

    def call(self, service_key: str, method: str, args: Sequence[Any]) -> object:
        service = self._services.get(service_key)
        if service is None:
            raise HostServiceError("HOST_SERVICE_UNAVAILABLE")
        return service.call(method, args)

    def context_providers(self) -> list[ContextProviderContribution]:
        return self._context.providers()

    def grant_history(self, plugin_id: str, character_id: str, snapshot_cursor: str | None = None) -> dict[str, str]:
        return self._timeline.grant(plugin_id, character_id, snapshot_cursor)

    def revoke_history(self, history_token: str) -> None:
        self._timeline.revoke(history_token)

    def decorate_settings_snapshot(self, snapshot: Mapping[str, Any]) -> dict[str, Any]:
        result = json.loads(json.dumps(dict(snapshot), ensure_ascii=False))
        plugins = result.get("plugins")
        if not isinstance(plugins, list):
            raise HostServiceError("SETTINGS_SNAPSHOT_INVALID")
        for plugin in plugins:
            if not isinstance(plugin, dict) or not isinstance(plugin.get("pluginId"), str):
                continue
            sections = plugin.get("sections")
            if not isinstance(sections, list):
                sections = []
            plugin["sections"] = [
                *sections,
                *self._settings.sections_for_plugin(plugin["pluginId"]),
            ][:16]
        return result

    def settings_save(
        self,
        plugin_id: str,
        section_id: str,
        values: Mapping[str, Any],
    ) -> tuple[bool, object]:
        return self._settings.save(plugin_id, section_id, values)

    def settings_action(
        self,
        plugin_id: str,
        section_id: str,
        action_id: str,
        values: Mapping[str, Any],
    ) -> tuple[bool, object]:
        return self._settings.action(plugin_id, section_id, action_id, values)

    def settings_collection(
        self,
        operation: str,
        plugin_id: str,
        section_id: str,
        collection_id: str,
        payload: Mapping[str, Any],
    ) -> object:
        return self._settings.collection(
            operation,
            plugin_id,
            section_id,
            collection_id,
            payload,
        )

    def settings_sections(self, surface: str) -> list[dict[str, Any]]:
        """Return declarative sections for a capability-owned settings shell."""

        return self._settings.sections_for_surface(surface)

    def model_slots(self) -> list[dict[str, Any]]:
        return self._model_slots.snapshot()

    def model_slot_save(self, identity: str, selection: Mapping[str, Any]) -> object:
        return self._model_slots.save(identity, selection)

    def composer_tools(self) -> list[dict[str, object]]:
        return self._composer_tools_v0.snapshot()

    def invoke_composer_tool(self, public_id: str) -> dict[str, str]:
        return self._composer_tools_v0.invoke(public_id)

    def resolve_committed_artifact(self, artifact_id: str) -> object:
        """Core-only lookup; this method is never routed through host.call."""

        return self._artifacts.resolve_committed(artifact_id)

    def create_json_artifact(self, plugin_id, value):
        return self._artifacts.create_json(plugin_id, value)

    def release_owned_artifact(self, plugin_id, artifact_id):
        return self._artifacts._store.release(plugin_id, artifact_id)

    def release_committed_artifact(self, artifact_id: str) -> bool:
        return self._artifacts.release_committed(artifact_id)

    def revoke_scope(self, service_key: str, plugin_id: str) -> None:
        service = self._services.get(service_key)
        callback = getattr(service, "revoke_scope", None)
        if callable(callback):
            callback(plugin_id)

    @property
    def tool_count(self) -> int:
        return self._tools.count

    @property
    def settings_count(self) -> int:
        return self._settings.count

    @property
    def model_slot_count(self) -> int:
        return self._model_slots.count

    @property
    def artifact_count(self) -> int:
        return self._artifacts.count

    def clear(self) -> None:
        self._artifacts.clear()
        self._character.clear()
        self._tools.clear()
        self._context.clear()
        self._model_slots.clear()
        if self._storage is not None:
            self._storage.clear()
        self._settings.clear()
        self._composer_tools_v0.clear()


def _mapping(value: object, code: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise HostServiceError(code)
    return value


def _model_slot_selection(value: object) -> dict[str, str]:
    raw = _mapping(value, "MODEL_SLOT_SELECTION_INVALID")
    if set(raw) != {"profileId", "model"}:
        raise HostServiceError("MODEL_SLOT_SELECTION_INVALID")
    profile_id = raw.get("profileId")
    model = raw.get("model")
    if (
        not isinstance(profile_id, str)
        or len(profile_id) > 64
        or not isinstance(model, str)

        or bool(profile_id) != bool(model)
    ):
        raise HostServiceError("MODEL_SLOT_SELECTION_INVALID")
    return {"profileId": profile_id, "model": model}


def _bounded_identifier(value: object, code: str, maximum: int) -> str:
    if (
        not isinstance(value, str)
        or len(value) > maximum
        or not _IDENTIFIER.fullmatch(value)
    ):
        raise HostServiceError(code)
    return value


def _callback_handle(value: object) -> str:
    if (
        not isinstance(value, str)
        or not value.startswith("cb_")
        or len(value) != 35
        or any(character not in "0123456789abcdef" for character in value[3:])
    ):
        raise HostServiceError("CALLBACK_HANDLE_INVALID")
    return value


def _optional_callback_handle(value: object) -> str | None:
    return None if value is None else _callback_handle(value)


def _registration_id(value: object) -> str:
    if not isinstance(value, str) or not value.startswith("reg_") or len(value) != 36:
        raise HostServiceError("HOST_REGISTRATION_INVALID")
    return value


def _new_registration_id(existing: Mapping[str, Any]) -> str:
    registration_id = ""
    while not registration_id or registration_id in existing:
        registration_id = f"reg_{secrets.token_hex(16)}"
    return registration_id


def _context_fragment(value: object, index: int, *, scope: str = "step") -> ContextFragment:
    raw = _mapping(value, "CONTEXT_RESULT_INVALID")
    if "kind" in raw:
        raise HostServiceError("CONTEXT_SCHEMA_INCOMPATIBLE")
    content = raw.get("content")
    if not isinstance(content, str) or not content.strip():
        raise HostServiceError("CONTEXT_RESULT_INVALID")
    required = raw.get("required", False)
    if not isinstance(required, bool):
        raise HostServiceError("CONTEXT_RESULT_INVALID")
    sensitivity = raw.get("sensitivity", "private")
    return ContextFragment(
        fragment_id=str(raw.get("id") or index)[:64],
        source="plugin",
        content=content,
        priority=_bounded_int(raw.get("priority"), 0, 100, 50),
        token_budget=_bounded_int(raw.get("budgetHint"), 1, 4096, 512),
        sensitivity=(
            sensitivity
            if sensitivity in {"public", "private", "sensitive"}
            else "private"
        ),
        cache_scope=scope,
        required=required,
    )


def _bounded_int(value: object, minimum: int, maximum: int, default: int) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        return default
    return min(maximum, max(minimum, value))


def _settings_field(
    value: object,
    *,
    allow_required_without_default: bool = False,
    allow_display_types: bool = True,
) -> dict[str, Any]:
    raw = _mapping(value, "SETTINGS_DESCRIPTOR_INVALID")
    key = _bounded_identifier(raw.get("key"), "SETTINGS_DESCRIPTOR_INVALID", 64)
    label = raw.get("label")
    kind = raw.get("type")
    description = raw.get("description", "")
    kind_map = {
        "text": "string",
        "path": "string",
        "secret": "password",
        "toggle": "boolean",
        "slider": "number",
        "string": "string",
        "password": "password",
        "boolean": "boolean",
        "integer": "integer",
        "number": "number",
        "select": "select",
        "readonly": "readonly",
        "status": "status",
        "resource": "resource",
    }
    if (
        not isinstance(label, str)
        or not label

        or not isinstance(kind, str)
        or kind not in kind_map
        or not isinstance(description, str)

    ):
        raise HostServiceError("SETTINGS_DESCRIPTOR_INVALID")
    public_kind = kind_map[kind]
    if not allow_display_types and public_kind in {"status", "resource"}:
        raise HostServiceError("SETTINGS_DESCRIPTOR_INVALID")
    options = _settings_options(raw.get("options", []))
    minimum = _optional_number(raw.get("minimum"))
    maximum = _optional_number(raw.get("maximum"))
    step = _optional_number(raw.get("step"))
    max_length = raw.get("maxLength")
    if max_length is not None and (
        not isinstance(max_length, int)
        or isinstance(max_length, bool)
        or not 1 <= max_length <= 16_384
        or public_kind not in {"string", "password", "readonly"}
    ):
        raise HostServiceError("SETTINGS_DESCRIPTOR_INVALID")
    if minimum is not None and maximum is not None and minimum > maximum:
        raise HostServiceError("SETTINGS_DESCRIPTOR_INVALID")
    if public_kind in {"status", "resource"} and (
        options
        or minimum is not None
        or maximum is not None
        or step is not None
        or max_length is not None
    ):
        raise HostServiceError("SETTINGS_DESCRIPTOR_INVALID")
    placement = raw.get("placement", "row")
    if not isinstance(placement, str) or placement not in {"row", "advanced", "section_header"} or (
        placement == "section_header" and public_kind != "status"
    ):
        raise HostServiceError("SETTINGS_DESCRIPTOR_INVALID")
    raw_action_ids = raw.get("actionIds", [])
    if (
        not isinstance(raw_action_ids, list)

        or any(
            not isinstance(action_id, str)
            or not _IDENTIFIER.fullmatch(action_id)
            or len(action_id) > 64
            for action_id in raw_action_ids
        )
        or len(set(raw_action_ids)) != len(raw_action_ids)
        or (raw_action_ids and public_kind != "resource")
    ):
        raise HostServiceError("SETTINGS_DESCRIPTOR_INVALID")
    raw_enabled_when = raw.get("enabledWhen")
    enabled_when = None
    if raw_enabled_when is not None:
        condition = _mapping(raw_enabled_when, "SETTINGS_DESCRIPTOR_INVALID")
        if "hide" in condition and not isinstance(condition["hide"], bool):
            raise HostServiceError("SETTINGS_DESCRIPTOR_INVALID")
        condition_field = _bounded_identifier(
            condition.get("field"),
            "SETTINGS_DESCRIPTOR_INVALID",
            64,
        )
        condition_value = condition.get("equals")
        if not isinstance(condition_value, str):
            raise HostServiceError("SETTINGS_DESCRIPTOR_INVALID")
        enabled_when = {"field": condition_field, "equals": condition_value}
        if "hide" in condition:
            enabled_when["hide"] = condition["hide"]
    flags = {
        name: raw.get(name, default)
        for name, default in {
            "required": False,
            "readonly": public_kind in {"readonly", "status", "resource"},
            "copyable": False,
            "restartRequired": False,
        }.items()
    }
    if any(not isinstance(flag, bool) for flag in flags.values()):
        raise HostServiceError("SETTINGS_DESCRIPTOR_INVALID")
    if public_kind in {"status", "resource"} and not flags["readonly"]:
        raise HostServiceError("SETTINGS_DESCRIPTOR_INVALID")
    default = raw.get("default")
    field = {
        "key": key,
        "label": label,
        "type": public_kind,
        "default": default,
        "description": description,
        "options": options,
        "minimum": minimum,
        "maximum": maximum,
        "step": step,
        "maxLength": max_length,
        "placement": placement,
        "actionIds": list(raw_action_ids),
        "enabledWhen": enabled_when,
        **flags,
    }
    field["default"] = default
    if not _settings_value_valid(field, default) and not (
        allow_required_without_default and default is None and flags["required"]
    ):
        raise HostServiceError("SETTINGS_DESCRIPTOR_INVALID")
    return field


def _settings_options(value: object) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        raise HostServiceError("SETTINGS_DESCRIPTOR_INVALID")
    options: list[dict[str, Any]] = []
    for item in value:
        raw = _mapping(item, "SETTINGS_DESCRIPTOR_INVALID")
        label = raw.get("label")
        option_value = raw.get("value")
        if (
            not isinstance(label, str)
            or not label

            or isinstance(option_value, (dict, list))
            or not isinstance(option_value, (str, bool, int, float))
        ):
            raise HostServiceError("SETTINGS_DESCRIPTOR_INVALID")
        options.append({"label": label, "value": option_value})
    return options


def _settings_action(value: object) -> dict[str, Any]:
    raw = _mapping(value, "SETTINGS_DESCRIPTOR_INVALID")
    action_id = _bounded_identifier(
        raw.get("actionId"),
        "SETTINGS_DESCRIPTOR_INVALID",
        64,
    )
    label = raw.get("label")
    description = raw.get("description", "")
    danger = raw.get("danger", False)
    if (
        not isinstance(label, str)
        or not label

        or not isinstance(description, str)

        or danger is not False
    ):
        raise HostServiceError("SETTINGS_DESCRIPTOR_INVALID")
    return {
        "actionId": action_id,
        "label": label,
        "description": description,
        "danger": False,
    }


def _settings_collection(value: object) -> dict[str, Any]:
    raw = _mapping(value, "SETTINGS_DESCRIPTOR_INVALID")
    allowed = {
        "collectionId",
        "title",
        "description",
        "scope",
        "columns",
        "fields",
        "filters",
        "searchable",
        "pageSize",
        "deleteConfirmation",
    }
    if any(key not in allowed for key in raw):
        raise HostServiceError("SETTINGS_DESCRIPTOR_INVALID")
    collection_id = _bounded_identifier(
        raw.get("collectionId"),
        "SETTINGS_DESCRIPTOR_INVALID",
        64,
    )
    title = raw.get("title")
    description = raw.get("description", "")
    # Collection v0 originally treated every collection as character-owned.
    # Keep that default for already installed plugins; global data opts in.
    scope = raw.get("scope", "character")
    searchable = raw.get("searchable", False)
    page_size = raw.get("pageSize", 25)
    delete_confirmation = raw.get("deleteConfirmation", "")
    raw_columns = raw.get("columns", [])
    raw_fields = raw.get("fields", [])
    raw_filters = raw.get("filters", [])
    if (
        not isinstance(title, str)
        or not title

        or not isinstance(description, str)
        or not isinstance(scope, str)
        or scope not in {"global", "character"}
        or not isinstance(searchable, bool)
        or not isinstance(page_size, int)
        or isinstance(page_size, bool)
        or not 1 <= page_size <= 100
        or not isinstance(delete_confirmation, str)

        or not isinstance(raw_columns, list)
        or not 1 <= len(raw_columns) <= 12
        or not isinstance(raw_fields, list)

        or not isinstance(raw_filters, list)

    ):
        raise HostServiceError("SETTINGS_DESCRIPTOR_INVALID")
    columns = tuple(_collection_column(item) for item in raw_columns)
    fields = tuple(
        _settings_field(
            item,
            allow_required_without_default=True,
            allow_display_types=False,
        )
        for item in raw_fields
    )
    filters = tuple(_collection_filter(item) for item in raw_filters)
    for items, key in ((columns, "key"), (fields, "key"), (filters, "key")):
        if len({item[key] for item in items}) != len(items):
            raise HostServiceError("SETTINGS_DESCRIPTOR_INVALID")
    visible_keys = {item["key"] for item in columns}
    if any(item["key"] not in visible_keys for item in filters):
        raise HostServiceError("SETTINGS_DESCRIPTOR_INVALID")
    return {
        "collectionId": collection_id,
        "title": title,
        "description": description,
        "scope": scope,
        "columns": columns,
        "fields": fields,
        "filters": filters,
        "searchable": searchable,
        "pageSize": page_size,
        "deleteConfirmation": delete_confirmation,
    }


def _collection_column(value: object) -> dict[str, Any]:
    raw = _mapping(value, "SETTINGS_DESCRIPTOR_INVALID")
    if any(key not in {"key", "label", "type", "maxLength"} for key in raw):
        raise HostServiceError("SETTINGS_DESCRIPTOR_INVALID")
    key = _bounded_identifier(raw.get("key"), "SETTINGS_DESCRIPTOR_INVALID", 64)
    label = raw.get("label")
    kind = raw.get("type")
    max_length = raw.get("maxLength")
    if (
        not isinstance(label, str)
        or not label

        or kind not in {"string", "number", "boolean", "datetime"}
        or (
            max_length is not None
            and (
                kind != "string"
                or not isinstance(max_length, int)
                or isinstance(max_length, bool)
                or not 1 <= max_length <= 16_384
            )
        )
    ):
        raise HostServiceError("SETTINGS_DESCRIPTOR_INVALID")
    return {"key": key, "label": label, "type": kind, "maxLength": max_length}


def _collection_filter(value: object) -> dict[str, Any]:
    raw = _mapping(value, "SETTINGS_DESCRIPTOR_INVALID")
    if set(raw) != {"key", "label", "options"}:
        raise HostServiceError("SETTINGS_DESCRIPTOR_INVALID")
    key = _bounded_identifier(raw.get("key"), "SETTINGS_DESCRIPTOR_INVALID", 64)
    label = raw.get("label")
    if not isinstance(label, str) or not label:
        raise HostServiceError("SETTINGS_DESCRIPTOR_INVALID")
    options = _settings_options(raw.get("options"))
    if not options:
        raise HostServiceError("SETTINGS_DESCRIPTOR_INVALID")
    return {"key": key, "label": label, "options": options}


def _collection_with_handles(
    descriptor: Mapping[str, Any],
    raw_handles: object,
) -> _SettingsCollection:
    handles = _mapping(raw_handles, "SETTINGS_CALLBACK_INVALID")
    if set(handles) != {"query", "create", "update", "delete"}:
        raise HostServiceError("SETTINGS_CALLBACK_INVALID")
    query_handle = _optional_callback_handle(handles.get("query"))
    if query_handle is None:
        raise HostServiceError("SETTINGS_CALLBACK_INVALID")
    create_handle = _optional_callback_handle(handles.get("create"))
    update_handle = _optional_callback_handle(handles.get("update"))
    delete_handle = _optional_callback_handle(handles.get("delete"))
    if delete_handle is not None and not descriptor["deleteConfirmation"]:
        raise HostServiceError("SETTINGS_DESCRIPTOR_INVALID")
    return _SettingsCollection(
        collection_id=str(descriptor["collectionId"]),
        title=str(descriptor["title"]),
        description=str(descriptor["description"]),
        scope=str(descriptor["scope"]),
        columns=tuple(descriptor["columns"]),
        fields=tuple(descriptor["fields"]),
        filters=tuple(descriptor["filters"]),
        searchable=bool(descriptor["searchable"]),
        page_size=int(descriptor["pageSize"]),
        delete_confirmation=str(descriptor["deleteConfirmation"]),
        query_handle=query_handle,
        create_handle=create_handle,
        update_handle=update_handle,
        delete_handle=delete_handle,
    )


def _public_collection(collection: _SettingsCollection) -> dict[str, Any]:
    return {
        "collectionId": collection.collection_id,
        "title": collection.title,
        "description": collection.description,
        "scope": collection.scope,
        "columns": [dict(item) for item in collection.columns],
        "fields": [dict(item) for item in collection.fields],
        "filters": [dict(item) for item in collection.filters],
        "searchable": collection.searchable,
        "pageSize": collection.page_size,
        "canCreate": collection.create_handle is not None,
        "canUpdate": collection.update_handle is not None,
        "canDelete": collection.delete_handle is not None,
        "deleteConfirmation": collection.delete_confirmation,
    }


def _collection_query_request(
    collection: _SettingsCollection,
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    if set(payload) != {"cursor", "limit", "search", "filters"}:
        raise HostServiceError("SETTINGS_COLLECTION_QUERY_INVALID")
    cursor = payload.get("cursor")
    limit = payload.get("limit")
    search = payload.get("search")
    filters = _mapping(payload.get("filters"), "SETTINGS_COLLECTION_QUERY_INVALID")
    if (
        (cursor is not None and (not isinstance(cursor, str)))
        or not isinstance(limit, int)
        or isinstance(limit, bool)
        or not 1 <= limit <= 100
        or not isinstance(search, str)

        or (search and not collection.searchable)
        or len(filters) > len(collection.filters)
    ):
        raise HostServiceError("SETTINGS_COLLECTION_QUERY_INVALID")
    by_key = {item["key"]: item for item in collection.filters}
    if any(
        key not in by_key
        or value not in {option["value"] for option in by_key[key]["options"]}
        for key, value in filters.items()
    ):
        raise HostServiceError("SETTINGS_COLLECTION_QUERY_INVALID")
    return {
        "cursor": cursor,
        "limit": limit,
        "search": search,
        "filters": dict(filters),
    }


def _collection_query_result(
    collection: _SettingsCollection,
    value: object,
    limit: int,
) -> dict[str, Any]:
    raw = _mapping(value, "SETTINGS_COLLECTION_RESULT_INVALID")
    if set(raw) != {"items", "nextCursor", "total"}:
        raise HostServiceError("SETTINGS_COLLECTION_RESULT_INVALID")
    items = raw.get("items")
    next_cursor = raw.get("nextCursor")
    total = raw.get("total")
    if (
        not isinstance(items, list)
        or len(items) > limit
        or (next_cursor is not None and (not isinstance(next_cursor, str) or len(next_cursor) > 256))
        or (total is not None and (not isinstance(total, int) or isinstance(total, bool) or total < 0))
    ):
        raise HostServiceError("SETTINGS_COLLECTION_RESULT_INVALID")
    result = {
        "items": [_collection_item(collection, item) for item in items],
        "nextCursor": next_cursor,
        "total": total,
    }
    if not _json_compatible(result):
        raise HostServiceError("SETTINGS_COLLECTION_RESULT_INVALID")
    return result


def _collection_item(
    collection: _SettingsCollection,
    value: object,
) -> dict[str, Any]:
    raw = _mapping(value, "SETTINGS_COLLECTION_RESULT_INVALID")
    if set(raw) != {"itemId", "values"}:
        raise HostServiceError("SETTINGS_COLLECTION_RESULT_INVALID")
    item_id = _collection_item_id(raw.get("itemId"))
    values = _mapping(raw.get("values"), "SETTINGS_COLLECTION_RESULT_INVALID")
    allowed = {
        **{item["key"]: ("column", item) for item in collection.columns},
        **{item["key"]: ("field", item) for item in collection.fields},
    }
    if any(key not in allowed for key in values):
        raise HostServiceError("SETTINGS_COLLECTION_RESULT_INVALID")
    projected: dict[str, Any] = {}
    for key, item in values.items():
        source, spec = allowed[key]
        if source == "field":
            valid = _settings_value_valid(spec, item)
        else:
            valid = _collection_cell_valid(spec, item)
        if not valid:
            raise HostServiceError("SETTINGS_COLLECTION_RESULT_INVALID")
        projected[key] = item
    result = {"itemId": item_id, "values": projected}
    if not _json_compatible(result):
        raise HostServiceError("SETTINGS_COLLECTION_RESULT_INVALID")
    return result


def _collection_cell_valid(spec: Mapping[str, Any], value: object) -> bool:
    if value is None:
        return True
    kind = spec.get("type")
    if kind in {"string", "datetime"}:
        maximum = spec.get("maxLength") if kind == "string" else None
        if not isinstance(maximum, int):
            maximum = 4096
        return isinstance(value, str) and len(value) <= maximum
    if kind == "boolean":
        return isinstance(value, bool)
    return (
        kind == "number"
        and isinstance(value, (int, float))
        and not isinstance(value, bool)
    )


def _collection_item_id(value: object) -> str:
    if not isinstance(value, str) or not value or len(value) > 200:
        raise HostServiceError("SETTINGS_COLLECTION_ITEM_INVALID")
    return value


def _optional_number(value: object) -> int | float | None:
    if value is None:
        return None
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise HostServiceError("SETTINGS_DESCRIPTOR_INVALID")
    return value


def _settings_value_valid(field: Mapping[str, Any], value: object) -> bool:
    if value is None:
        return not bool(field.get("required"))
    kind = field.get("type")
    if kind == "status":
        return _settings_status_value_valid(value)
    if kind == "resource":
        return _settings_resource_value_valid(field, value)
    if kind in {"string", "password", "readonly"}:
        maximum = field.get("maxLength")
        if not isinstance(maximum, int):
            maximum = 4096
        return isinstance(value, str) and len(value) <= maximum
    if kind == "select":
        return any(value == item["value"] for item in field.get("options", []))
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
    minimum = field.get("minimum")
    maximum = field.get("maximum")
    return not (
        isinstance(minimum, (int, float)) and value < minimum
        or isinstance(maximum, (int, float)) and value > maximum
    )


def _settings_status_value_valid(value: object) -> bool:
    if not isinstance(value, Mapping) or set(value) != {"state", "label", "message"}:
        return False
    state = value.get("state")
    label = value.get("label")
    message = value.get("message")
    return (
        isinstance(state, str)
        and state in _SETTINGS_STATUS_STATES
        and isinstance(label, str)
        and 1 <= len(label) <= 120
        and isinstance(message, str)
        and len(message) <= 240
    )


def _settings_resource_value_valid(
    field: Mapping[str, Any],
    value: object,
) -> bool:
    if not isinstance(value, Mapping) or set(value) != {
        "applicability",
        "subtitle",
        "ready",
        "taskState",
        "message",
        "detail",
        "progress",
        "availableActionIds",
    }:
        return False
    progress = value.get("progress")
    available_action_ids = value.get("availableActionIds")
    allowed_action_ids = set(field.get("actionIds", []))
    return (
        isinstance(value.get("applicability"), str)
        and value.get("applicability") in _SETTINGS_RESOURCE_APPLICABILITY
        and isinstance(value.get("subtitle"), str)
        and len(value["subtitle"]) <= 512
        and isinstance(value.get("ready"), bool)
        and isinstance(value.get("taskState"), str)
        and value.get("taskState") in _SETTINGS_RESOURCE_TASK_STATES
        and isinstance(value.get("message"), str)
        and len(value["message"]) <= 240
        and isinstance(value.get("detail"), str)
        and len(value["detail"]) <= 240
        and (
            progress is None
            or (
                isinstance(progress, int)
                and not isinstance(progress, bool)
                and 0 <= progress <= 100
            )
        )
        and isinstance(available_action_ids, list)
        and len(available_action_ids) <= 8
        and all(isinstance(action_id, str) for action_id in available_action_ids)
        and len(set(available_action_ids)) == len(available_action_ids)
        and all(
            isinstance(action_id, str) and action_id in allowed_action_ids
            for action_id in available_action_ids
        )
    )


def _settings_display_values(
    fields: Sequence[Mapping[str, Any]],
    values: Mapping[str, Any],
) -> tuple[dict[str, Any], bool]:
    projected: dict[str, Any] = {}
    invalid = False
    for field in fields:
        value = values.get(field["key"], field["default"])
        if not _settings_value_valid(field, value):
            value = field["default"]
            invalid = True
        projected[field["key"]] = value
    return projected, invalid


def _editable_settings_values(
    fields: Sequence[Mapping[str, Any]],
    values: Mapping[str, Any],
) -> dict[str, Any]:
    by_key = {field["key"]: field for field in fields}
    if any(key not in by_key for key in values):
        raise HostServiceError("SETTINGS_VALUES_INVALID")
    editable: dict[str, Any] = {}
    for key, value in values.items():
        field = by_key[key]
        if field["readonly"] or field["type"] in {"readonly", "status", "resource"}:
            continue
        if not _settings_value_valid(field, value):
            raise HostServiceError("SETTINGS_VALUES_INVALID")
        editable[key] = value
    return editable


def _application_state(value: object) -> str:
    if value is None:
        return "applied"
    if isinstance(value, str):
        states = [value]
    elif isinstance(value, list):
        states = value
    elif isinstance(value, Mapping):
        state = value.get("applicationState")
        states = [state]
    else:
        raise HostServiceError("SETTINGS_SAVE_RESULT_INVALID")
    if not states or any(
        state not in {"applied", "restart_required", "error"}
        for state in states
    ):
        raise HostServiceError("SETTINGS_SAVE_RESULT_INVALID")
    if "error" in states:
        return "error"
    if "restart_required" in states:
        return "restart_required"
    return "applied"


def _json_compatible(value: object) -> bool:
    try:
        json.dumps(value, ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError):
        return False
    return True


def _timeline_entry_mapping(entry: object) -> dict[str, Any]:
    kind = getattr(entry, "kind")
    kind_value = getattr(kind, "value", kind)
    payload = getattr(entry, "payload")
    if not isinstance(payload, Mapping):
        raise HostServiceError("TIMELINE_READ_FAILED")
    return {
        "entryId": str(getattr(entry, "entry_id")),
        "turnId": str(getattr(entry, "turn_id")),
        "characterId": str(getattr(entry, "character_id")),
        "kind": str(kind_value),
        "origin": str(getattr(entry, "origin")),
        "createdAt": str(getattr(entry, "created_at")),
        "payload": json.loads(json.dumps(dict(payload), ensure_ascii=False)),
    }


__all__ = ["HostServiceError", "PluginHostServices"]
