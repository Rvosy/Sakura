"""Core-owned implementations of the first Plugin API v3 Host Services."""

from __future__ import annotations

import base64
import json
import re
import secrets
import threading
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Sequence

from app.agent.tools import Tool
from app.llm.prompts.types import ContextFragment, ContextRequest
from app.plugins.models import ContextProviderContribution


HOST_CONTEXT_SERVICE = "sakura.host.context"
HOST_ARTIFACTS_SERVICE = "sakura.host.artifacts"
HOST_CHARACTER_SERVICE = "sakura.host.character"
HOST_MODEL_SLOTS_SERVICE = "sakura.host.model_slots"
HOST_SETTINGS_SERVICE = "sakura.host.settings"
HOST_SETTINGS_COLLECTION_V0_SERVICE = "sakura.host.settings.collection-v0"
HOST_SETTINGS_SURFACE_V0_SERVICE = "sakura.host.settings.surface-v0"
HOST_TOOLS_SERVICE = "sakura.host.tools"
HOST_COMPOSER_TOOLS_V0_SERVICE = "sakura.host.ui.composer-tools-v0"
HOST_TIMELINE_SERVICE = "sakura.host.timeline"
_TIMELINE_RESPONSE_ENTRY_BYTES = 700 * 1024
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


class _TimelineHostService:
    def __init__(
        self,
        store: object,
        current_character_id: Callable[[], str | None],
    ) -> None:
        self._store = store
        self._current_character_id = current_character_id

    def call(self, method: str, args: Sequence[Any]) -> object:
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
    def __init__(self, store: object) -> None:
        self._store = store

    def call(self, method: str, args: Sequence[Any]) -> object:
        try:
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
                    "released": getattr(self._store, "release")(
                        _bounded_identifier(args[0], "PLUGIN_ID_INVALID", 64),
                        _bounded_identifier(args[1], "ARTIFACT_NOT_FOUND", 200),
                    )
                }
        except Exception as error:
            code = getattr(error, "code", "ARTIFACT_OPERATION_FAILED")
            raise HostServiceError(code if isinstance(code, str) else "ARTIFACT_OPERATION_FAILED") from error
        raise HostServiceError("HOST_METHOD_INVALID")

    def clear(self) -> None:
        getattr(self._store, "clear")()

    def resolve_committed(self, artifact_id: str) -> object:
        return getattr(self._store, "resolve_committed_by_id")(artifact_id)

    def release_committed(self, artifact_id: str) -> bool:
        artifact = self.resolve_committed(artifact_id)
        return bool(
            getattr(self._store, "release")(
                getattr(artifact, "plugin_id"),
                artifact_id,
            )
        )

    def consume_tool_result(self, value: object) -> object:
        """Resolve one explicit tool artifact envelope without crossing it back over RPC."""

        if not isinstance(value, Mapping) or set(value) != {"content", "artifact"}:
            return value
        descriptor = _mapping(value.get("artifact"), "TOOL_ARTIFACT_INVALID")
        if set(descriptor) != {"artifactId", "mediaType", "byteLength"}:
            raise HostServiceError("TOOL_ARTIFACT_INVALID")
        artifact_id = descriptor.get("artifactId")
        if not isinstance(artifact_id, str):
            raise HostServiceError("TOOL_ARTIFACT_INVALID")
        try:
            artifact = self.resolve_committed(artifact_id)
            media_type = getattr(artifact, "media_type", "")
            byte_length = getattr(artifact, "byte_length", -1)
            if (
                descriptor.get("mediaType") != media_type
                or descriptor.get("byteLength") != byte_length
                or not isinstance(media_type, str)
                or not media_type.startswith("image/")
            ):
                raise HostServiceError("TOOL_ARTIFACT_INVALID")
            payload = getattr(artifact, "path").read_bytes()
            if len(payload) != byte_length:
                raise HostServiceError("TOOL_ARTIFACT_INVALID")
            return {
                "content": value.get("content"),
                "artifact": {
                    "type": "image",
                    "data": base64.b64encode(payload).decode("ascii"),
                    "mimeType": media_type,
                },
            }
        except HostServiceError:
            raise
        except Exception as error:
            raise HostServiceError("TOOL_ARTIFACT_INVALID") from error
        finally:
            try:
                self.release_committed(artifact_id)
            except Exception:
                pass

    @property
    def count(self) -> int:
        return int(getattr(self._store, "count", 0))


class _CharacterHostService:
    def __init__(self, store: object) -> None:
        self._store = store

    def call(self, method: str, args: Sequence[Any]) -> object:
        try:
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


@dataclass
class _ToolRegistration:
    name: str
    tool: Tool


class _ToolsHostService:
    def __init__(
        self,
        tool_registry: object,
        invoke_callback: Callable[..., Any],
        consume_result: Callable[[object], object] | None = None,
    ) -> None:
        self._tool_registry = tool_registry
        self._invoke_callback = invoke_callback
        self._consume_result = consume_result or (lambda value: value)
        self._registrations: dict[str, _ToolRegistration] = {}

    def call(self, method: str, args: Sequence[Any]) -> object:
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
        if (
            not isinstance(name, str)
            or not _TOOL_NAME.fullmatch(name)
            or not isinstance(description, str)
            or not description
            or len(description) > 500
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

        def handler(arguments: dict[str, Any]) -> object:
            return self._consume_result(
                self._invoke_callback(
                    handle,
                    "tools.handler",
                    arguments,
                )
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
        )
        registration_id = _new_registration_id(self._registrations)
        getattr(self._tool_registry, "register")(tool)
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
        if (
            not isinstance(provider_id, str)
            or not _IDENTIFIER.fullmatch(provider_id)
            or not isinstance(description, str)
            or len(description) > 240
            or not isinstance(order, (int, float))
            or isinstance(order, bool)
            or not isinstance(enabled, bool)
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
                _context_fragment(item, index)
                for index, item in enumerate(payload[:16])
            )

        contribution = ContextProviderContribution(
            provider_id=provider_id,
            description=description,
            build_context=build_context,
            order=float(order),
            enabled=enabled,
        )
        registration_id = _new_registration_id(self._registrations)
        self._registrations[registration_id] = _ContextRegistration(contribution)
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


@dataclass(frozen=True)
class _SettingsCollection:
    collection_id: str
    title: str
    description: str
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
    def __init__(self, invoke_callback: Callable[..., Any]) -> None:
        self._invoke_callback = invoke_callback
        self._registrations: dict[str, _ModelSlotRegistration] = {}
        self._lock = threading.RLock()

    def call(self, method: str, args: Sequence[Any]) -> object:
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
            or not isinstance(description, str) or len(description) > 240
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
        allowed_descriptor = {
            "sectionId",
            "title",
            "fields",
            "actions",
            "order",
        }
        if any(key not in allowed_descriptor for key in descriptor):
            raise HostServiceError("SETTINGS_DESCRIPTOR_INVALID")
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
            or len(title) > 120
            or not isinstance(order, (int, float))
            or isinstance(order, bool)
            or not isinstance(raw_fields, list)
            or len(raw_fields) > 32
            or not isinstance(raw_actions, list)
            or len(raw_actions) > 15
        ):
            raise HostServiceError("SETTINGS_DESCRIPTOR_INVALID")
        fields = tuple(_settings_field(item) for item in raw_fields)
        field_keys = {field["key"] for field in fields}
        if len(field_keys) != len(fields) or any(
            field["enabledWhen"] is not None
            and (
                field["enabledWhen"]["field"] not in field_keys
                or field["enabledWhen"]["field"] == field["key"]
            )
            for field in fields
        ):
            raise HostServiceError("SETTINGS_DESCRIPTOR_INVALID")
        actions = tuple(_settings_action(item) for item in raw_actions)
        if len({action["actionId"] for action in actions}) != len(actions):
            raise HostServiceError("SETTINGS_DESCRIPTOR_INVALID")
        declared_action_ids = {action["actionId"] for action in actions}
        if any(
            not set(field["actionIds"]).issubset(declared_action_ids)
            for field in fields
        ):
            raise HostServiceError("SETTINGS_DESCRIPTOR_INVALID")
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
        if set(raw_action_handles) != action_ids:
            raise HostServiceError("SETTINGS_CALLBACK_INVALID")
        action_handles = {
            action_id: _callback_handle(handle)
            for action_id, handle in raw_action_handles.items()
        }
        if any(not field["readonly"] for field in fields) and save_handle is None:
            raise HostServiceError("SETTINGS_CALLBACK_INVALID")

        registration = _SettingsRegistration(
            plugin_id=plugin_id,
            section_id=section_id,
            title=title,
            fields=fields,
            actions=actions,
            collections=(),
            load_handle=load_handle,
            save_handle=save_handle,
            action_handles=action_handles,
            order=float(order),
            surface=None,
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
        fields = []
        projected_values: dict[str, Any] = {}
        for spec in registration.fields:
            value = values.get(spec["key"], spec["default"])
            if not _settings_value_valid(spec, value):
                value = spec["default"]
            public = dict(spec)
            public["value"] = value
            fields.append(public)
            projected_values[spec["key"]] = value
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
        if "values" in public:
            if not isinstance(public["values"], Mapping):
                raise HostServiceError("SETTINGS_ACTION_RESULT_INVALID")
            fields = {field["key"]: field for field in registration.fields}
            if any(key not in fields for key in public["values"]):
                raise HostServiceError("SETTINGS_ACTION_RESULT_INVALID")
            projected_values = {}
            for key, value in public["values"].items():
                if not _settings_value_valid(fields[key], value):
                    raise HostServiceError("SETTINGS_ACTION_RESULT_INVALID")
                projected_values[key] = value
            public["values"] = projected_values
        if "message" in public and (
            not isinstance(public["message"], str)
            or len(public["message"]) > 240
        ):
            raise HostServiceError("SETTINGS_ACTION_RESULT_INVALID")
        if not _json_compatible(public, 64 * 1024):
            raise HostServiceError("SETTINGS_ACTION_RESULT_INVALID")
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
            or len(label) > 40
            or not isinstance(description, str)
            or len(description) > 120
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
    ) -> None:
        self._artifacts = _ArtifactsHostService(artifact_store)
        self._character = _CharacterHostService(character_store)
        self._timeline = _TimelineHostService(timeline_store, current_character_id)
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
        self._model_slots = _ModelSlotsHostService(invoke_callback)
        self._composer_tools_v0 = _ComposerToolsV0HostService(invoke_callback)
        self._services = {
            HOST_ARTIFACTS_SERVICE: self._artifacts,
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

    def release_committed_artifact(self, artifact_id: str) -> bool:
        return self._artifacts.release_committed(artifact_id)

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
        or len(model) > 256
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


def _context_fragment(value: object, index: int) -> ContextFragment:
    raw = _mapping(value, "CONTEXT_RESULT_INVALID")
    content = raw.get("content")
    if not isinstance(content, str) or not content.strip():
        raise HostServiceError("CONTEXT_RESULT_INVALID")
    sensitivity = raw.get("sensitivity", "private")
    return ContextFragment(
        fragment_id=str(raw.get("id") or index)[:64],
        source="plugin",
        content=content[:8192],
        trust="untrusted",
        priority=_bounded_int(raw.get("priority"), 0, 100, 50),
        token_budget=_bounded_int(raw.get("budgetHint"), 1, 4096, 512),
        sensitivity=(
            sensitivity
            if sensitivity in {"public", "private", "sensitive"}
            else "private"
        ),
        cache_scope="step",
        required=False,
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
    allowed = {
        "key",
        "label",
        "type",
        "default",
        "description",
        "options",
        "minimum",
        "maximum",
        "step",
        "required",
        "readonly",
        "copyable",
        "restartRequired",
        "maxLength",
        "placement",
        "actionIds",
        "enabledWhen",
    }
    if any(key not in allowed for key in raw):
        raise HostServiceError("SETTINGS_DESCRIPTOR_INVALID")
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
        or len(label) > 120
        or kind not in kind_map
        or not isinstance(description, str)
        or len(description) > 240
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
    if placement not in {"row", "advanced", "section_header"} or (
        placement == "section_header" and public_kind != "status"
    ):
        raise HostServiceError("SETTINGS_DESCRIPTOR_INVALID")
    raw_action_ids = raw.get("actionIds", [])
    if (
        not isinstance(raw_action_ids, list)
        or len(raw_action_ids) > 8
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
        if set(condition) != {"field", "equals"}:
            raise HostServiceError("SETTINGS_DESCRIPTOR_INVALID")
        condition_field = _bounded_identifier(
            condition.get("field"),
            "SETTINGS_DESCRIPTOR_INVALID",
            64,
        )
        condition_value = condition.get("equals")
        if not isinstance(condition_value, str) or len(condition_value) > 200:
            raise HostServiceError("SETTINGS_DESCRIPTOR_INVALID")
        enabled_when = {"field": condition_field, "equals": condition_value}
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
    if not isinstance(value, list) or len(value) > 64:
        raise HostServiceError("SETTINGS_DESCRIPTOR_INVALID")
    options: list[dict[str, Any]] = []
    for item in value:
        raw = _mapping(item, "SETTINGS_DESCRIPTOR_INVALID")
        if set(raw) != {"label", "value"}:
            raise HostServiceError("SETTINGS_DESCRIPTOR_INVALID")
        label = raw.get("label")
        option_value = raw.get("value")
        if (
            not isinstance(label, str)
            or not label
            or len(label) > 120
            or isinstance(option_value, (dict, list))
            or not isinstance(option_value, (str, bool, int, float))
        ):
            raise HostServiceError("SETTINGS_DESCRIPTOR_INVALID")
        options.append({"label": label, "value": option_value})
    return options


def _settings_action(value: object) -> dict[str, Any]:
    raw = _mapping(value, "SETTINGS_DESCRIPTOR_INVALID")
    allowed = {"actionId", "label", "description", "danger"}
    if any(key not in allowed for key in raw):
        raise HostServiceError("SETTINGS_DESCRIPTOR_INVALID")
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
        or len(label) > 120
        or not isinstance(description, str)
        or len(description) > 240
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
    searchable = raw.get("searchable", False)
    page_size = raw.get("pageSize", 25)
    delete_confirmation = raw.get("deleteConfirmation", "")
    raw_columns = raw.get("columns", [])
    raw_fields = raw.get("fields", [])
    raw_filters = raw.get("filters", [])
    if (
        not isinstance(title, str)
        or not title
        or len(title) > 120
        or not isinstance(description, str)
        or len(description) > 240
        or not isinstance(searchable, bool)
        or not isinstance(page_size, int)
        or isinstance(page_size, bool)
        or not 1 <= page_size <= 100
        or not isinstance(delete_confirmation, str)
        or len(delete_confirmation) > 240
        or not isinstance(raw_columns, list)
        or not 1 <= len(raw_columns) <= 12
        or not isinstance(raw_fields, list)
        or len(raw_fields) > 16
        or not isinstance(raw_filters, list)
        or len(raw_filters) > 8
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
        or len(label) > 120
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
    if not isinstance(label, str) or not label or len(label) > 120:
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
        (cursor is not None and (not isinstance(cursor, str) or len(cursor) > 256))
        or not isinstance(limit, int)
        or isinstance(limit, bool)
        or not 1 <= limit <= 100
        or not isinstance(search, str)
        or len(search) > 200
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
    if not _json_compatible(result, 256 * 1024):
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
    if not _json_compatible(result, 128 * 1024):
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
        return value in {item["value"] for item in field.get("options", [])}
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
        state in _SETTINGS_STATUS_STATES
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
        value.get("applicability") in _SETTINGS_RESOURCE_APPLICABILITY
        and isinstance(value.get("subtitle"), str)
        and len(value["subtitle"]) <= 512
        and isinstance(value.get("ready"), bool)
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


def _json_compatible(value: object, maximum: int) -> bool:
    try:
        encoded = json.dumps(value, ensure_ascii=False).encode("utf-8")
    except (TypeError, ValueError):
        return False
    return len(encoded) <= maximum


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
