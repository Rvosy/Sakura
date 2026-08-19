"""Core-owned implementations of the first Plugin API v3 Host Services."""

from __future__ import annotations

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
HOST_SETTINGS_SERVICE = "sakura.host.settings"
HOST_TOOLS_SERVICE = "sakura.host.tools"
_TOOL_NAME = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,199}$")
_SETTINGS_RELOAD_ACTION = "sakura.reload"


class HostServiceError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass
class _ToolRegistration:
    name: str
    tool: Tool


class _ToolsHostService:
    def __init__(
        self,
        tool_registry: object,
        invoke_callback: Callable[..., Any],
    ) -> None:
        self._tool_registry = tool_registry
        self._invoke_callback = invoke_callback
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
            return self._invoke_callback(
                handle,
                "tools.handler",
                arguments,
            )

        tool = Tool(
            name=name,
            description=description,
            parameters=dict(parameters),
            handler=handler,
            requires_confirmation=False,
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
    load_handle: str | None
    save_handle: str | None
    action_handles: dict[str, str]
    order: float
    application_state: str = "applied"
    reason_code: str = "READY"


class _SettingsHostService:
    def __init__(
        self,
        invoke_callback: Callable[..., Any],
        reload_plugin: Callable[[str], Any],
    ) -> None:
        self._invoke_callback = invoke_callback
        self._reload_plugin = reload_plugin
        self._registrations: dict[str, _SettingsRegistration] = {}
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
        plugin_id = _bounded_identifier(raw_plugin_id, "PLUGIN_ID_INVALID", 200)
        descriptor = _mapping(raw_descriptor, "SETTINGS_DESCRIPTOR_INVALID")
        allowed_descriptor = {"sectionId", "title", "fields", "actions", "order"}
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
        if len({field["key"] for field in fields}) != len(fields):
            raise HostServiceError("SETTINGS_DESCRIPTOR_INVALID")
        actions = tuple(_settings_action(item) for item in raw_actions)
        if len({action["actionId"] for action in actions}) != len(actions):
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
            load_handle=load_handle,
            save_handle=save_handle,
            action_handles=action_handles,
            order=float(order),
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
            except Exception:
                reason_code = "SETTINGS_LOAD_FAILED"
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
        if registration.application_state in {"restart_required", "error"}:
            actions.append(
                {
                    "actionId": _SETTINGS_RELOAD_ACTION,
                    "label": "重新加载插件",
                    "description": "使用已保存配置重新建立插件及其依赖连接。",
                    "danger": False,
                }
            )
        return {
            "sectionId": registration.section_id,
            "title": registration.title,
            "reasonCode": reason_code,
            "fields": fields,
            "values": projected_values,
            "actions": actions,
        }

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
        if action_id == _SETTINGS_RELOAD_ACTION:
            if registration.application_state not in {"restart_required", "error"}:
                raise HostServiceError("SETTINGS_ACTION_INVALID")
            self._reload_plugin(plugin_id)
            return True, {"message": "插件已重新加载。"}
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

    @property
    def count(self) -> int:
        with self._lock:
            return len(self._registrations)


class PluginHostServices:
    """Generation-bound generic dispatcher; it does not import plugin code."""

    def __init__(
        self,
        tool_registry: object,
        *,
        invoke_callback: Callable[..., Any],
        encode_context_request: Callable[[ContextRequest], dict[str, Any]],
        on_context_change: Callable[[list[ContextProviderContribution]], None],
        reload_plugin: Callable[[str], Any],
    ) -> None:
        self._tools = _ToolsHostService(tool_registry, invoke_callback)
        self._context = _ContextHostService(
            invoke_callback,
            encode_context_request,
            on_context_change,
        )
        self._settings = _SettingsHostService(invoke_callback, reload_plugin)
        self._services = {
            HOST_TOOLS_SERVICE: self._tools,
            HOST_CONTEXT_SERVICE: self._context,
            HOST_SETTINGS_SERVICE: self._settings,
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

    @property
    def tool_count(self) -> int:
        return self._tools.count

    @property
    def settings_count(self) -> int:
        return self._settings.count

    def clear(self) -> None:
        self._tools.clear()
        self._context.clear()
        self._settings.clear()


def _mapping(value: object, code: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise HostServiceError(code)
    return value


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


def _settings_field(value: object) -> dict[str, Any]:
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
    options = _settings_options(raw.get("options", []))
    minimum = _optional_number(raw.get("minimum"))
    maximum = _optional_number(raw.get("maximum"))
    step = _optional_number(raw.get("step"))
    if minimum is not None and maximum is not None and minimum > maximum:
        raise HostServiceError("SETTINGS_DESCRIPTOR_INVALID")
    flags = {
        name: raw.get(name, default)
        for name, default in {
            "required": False,
            "readonly": public_kind == "readonly",
            "copyable": False,
            "restartRequired": False,
        }.items()
    }
    if any(not isinstance(flag, bool) for flag in flags.values()):
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
        **flags,
    }
    if not _settings_value_valid(field, default):
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
        action_id == _SETTINGS_RELOAD_ACTION
        or not isinstance(label, str)
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
    if kind in {"string", "password", "readonly"}:
        return isinstance(value, str) and len(value) <= 4096
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
        if field["readonly"] or field["type"] == "readonly":
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


__all__ = ["HostServiceError", "PluginHostServices"]
