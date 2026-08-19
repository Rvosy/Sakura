"""Core-owned implementations of the first Plugin API v3 Host Services."""

from __future__ import annotations

import re
import secrets
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Sequence

from app.agent.tools import Tool
from app.llm.prompts.types import ContextFragment, ContextRequest
from app.plugins.models import ContextProviderContribution


HOST_CONTEXT_SERVICE = "sakura.host.context"
HOST_TOOLS_SERVICE = "sakura.host.tools"
_TOOL_NAME = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,199}$")


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


class PluginHostServices:
    """Generation-bound generic dispatcher; it does not import plugin code."""

    def __init__(
        self,
        tool_registry: object,
        *,
        invoke_callback: Callable[..., Any],
        encode_context_request: Callable[[ContextRequest], dict[str, Any]],
        on_context_change: Callable[[list[ContextProviderContribution]], None],
    ) -> None:
        self._tools = _ToolsHostService(tool_registry, invoke_callback)
        self._context = _ContextHostService(
            invoke_callback,
            encode_context_request,
            on_context_change,
        )
        self._services = {
            HOST_TOOLS_SERVICE: self._tools,
            HOST_CONTEXT_SERVICE: self._context,
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

    @property
    def tool_count(self) -> int:
        return self._tools.count

    def clear(self) -> None:
        self._tools.clear()
        self._context.clear()


def _mapping(value: object, code: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
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


__all__ = ["HostServiceError", "PluginHostServices"]
