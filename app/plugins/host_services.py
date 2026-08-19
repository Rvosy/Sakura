"""Worker-side scoped proxies for the first real Plugin API v3 Host Services."""

from __future__ import annotations

from typing import Any, Callable, Mapping, Sequence

from app.plugins.kernel import EffectScope, PluginKernelError


HOST_CONTEXT_SERVICE = "sakura.host.context"
HOST_TOOLS_SERVICE = "sakura.host.tools"


class _RegistrationProxy:
    def __init__(
        self,
        service_key: str,
        callback_shape: str,
        plugin_id: str,
        scope: EffectScope,
        host_call: Callable[[str, str, Sequence[Any]], Any],
        callbacks: Any,
    ) -> None:
        self._service_key = service_key
        self._callback_shape = callback_shape
        self._plugin_id = plugin_id
        self._scope = scope
        self._host_call = host_call
        self._callbacks = callbacks

    def register(
        self,
        descriptor: Mapping[str, Any],
        callback: Callable[..., Any],
    ) -> Callable[[], None]:
        if not isinstance(descriptor, Mapping):
            raise PluginKernelError("HOST_DESCRIPTOR_INVALID", plugin_id=self._plugin_id)
        handle, dispose_callback = self._callbacks.register(
            self._plugin_id,
            self._callback_shape,
            callback,
            self._scope,
        )
        try:
            result = self._host_call(
                self._service_key,
                "register",
                [dict(descriptor), handle],
            )
        except Exception:
            dispose_callback()
            raise
        registration_id = (
            result.get("registrationId")
            if isinstance(result, Mapping)
            else None
        )
        if not isinstance(registration_id, str) or not registration_id:
            dispose_callback()
            raise PluginKernelError("HOST_REGISTRATION_INVALID", plugin_id=self._plugin_id)

        def cleanup() -> None:
            try:
                self._host_call(
                    self._service_key,
                    "unregister",
                    [registration_id],
                )
            finally:
                dispose_callback()

        return self._scope.effect(cleanup)


class _RegistrationFactory:
    _sakura_host_service_factory = True

    def __init__(
        self,
        service_key: str,
        callback_shape: str,
        host_call: Callable[[str, str, Sequence[Any]], Any],
        callbacks: Any,
    ) -> None:
        self._service_key = service_key
        self._callback_shape = callback_shape
        self._host_call = host_call
        self._callbacks = callbacks

    def for_plugin(self, plugin_id: str, scope: EffectScope) -> _RegistrationProxy:
        return _RegistrationProxy(
            self._service_key,
            self._callback_shape,
            plugin_id,
            scope,
            self._host_call,
            self._callbacks,
        )


def build_worker_host_services(
    service_keys: Sequence[str],
    host_call: Callable[[str, str, Sequence[Any]], Any],
    callbacks: Any,
) -> dict[str, _RegistrationFactory]:
    supported = {
        HOST_TOOLS_SERVICE: "tools.handler",
        HOST_CONTEXT_SERVICE: "context.contributor",
    }
    return {
        service_key: _RegistrationFactory(
            service_key,
            supported[service_key],
            host_call,
            callbacks,
        )
        for service_key in dict.fromkeys(service_keys)
        if service_key in supported
    }


__all__ = [
    "HOST_CONTEXT_SERVICE",
    "HOST_TOOLS_SERVICE",
    "build_worker_host_services",
]
