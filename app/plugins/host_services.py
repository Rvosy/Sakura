"""Worker-side scoped proxies for the first real Plugin API v3 Host Services."""

from __future__ import annotations

from typing import Any, Callable, Mapping, Sequence

from app.plugins.kernel import EffectScope, PluginKernelError


HOST_CONTEXT_SERVICE = "sakura.host.context"
HOST_ARTIFACTS_SERVICE = "sakura.host.artifacts"
HOST_CHARACTER_SERVICE = "sakura.host.character"
HOST_MODEL_SLOTS_SERVICE = "sakura.host.model_slots"
HOST_SETTINGS_SERVICE = "sakura.host.settings"
HOST_SETTINGS_COLLECTION_V0_SERVICE = "sakura.host.settings.collection-v0"
HOST_SETTINGS_SURFACE_V0_SERVICE = "sakura.host.settings.surface-v0"
HOST_TOOLS_SERVICE = "sakura.host.tools"
HOST_COMPOSER_TOOLS_V0_SERVICE = "sakura.host.ui.composer-tools-v0"


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
        def activate() -> Callable[[], None]:
            result = self._host_call(
                self._service_key,
                "register",
                [dict(descriptor), handle],
            )
            registration_id = (
                result.get("registrationId")
                if isinstance(result, Mapping)
                else None
            )
            if not isinstance(registration_id, str) or not registration_id:
                raise PluginKernelError(
                    "HOST_REGISTRATION_INVALID",
                    plugin_id=self._plugin_id,
                )

            def cleanup() -> None:
                self._host_call(
                    self._service_key,
                    "unregister",
                    [registration_id],
                )

            return cleanup

        try:
            return self._scope.stage(activate)
        except Exception:
            dispose_callback()
            raise


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


class _ComposerToolsV0Proxy:
    def __init__(
        self,
        plugin_id: str,
        scope: EffectScope,
        host_call: Callable[[str, str, Sequence[Any]], Any],
        callbacks: Any,
    ) -> None:
        self._plugin_id = plugin_id
        self._scope = scope
        self._host_call = host_call
        self._callbacks = callbacks

    def register(
        self,
        descriptor: Mapping[str, Any],
        callback: Callable[[Mapping[str, Any]], Any],
    ) -> Callable[[], None]:
        if not isinstance(descriptor, Mapping) or not callable(callback):
            raise PluginKernelError(
                "COMPOSER_TOOL_REGISTRATION_INVALID",
                plugin_id=self._plugin_id,
            )
        handle, dispose_callback = self._callbacks.register(
            self._plugin_id,
            "ui.composer_tool.invoke",
            callback,
            self._scope,
        )

        def activate() -> Callable[[], None]:
            result = self._host_call(
                HOST_COMPOSER_TOOLS_V0_SERVICE,
                "register",
                [self._plugin_id, dict(descriptor), handle],
            )
            registration_id = (
                result.get("registrationId")
                if isinstance(result, Mapping)
                else None
            )
            if not isinstance(registration_id, str) or not registration_id:
                raise PluginKernelError(
                    "HOST_REGISTRATION_INVALID",
                    plugin_id=self._plugin_id,
                )

            def cleanup() -> None:
                self._host_call(
                    HOST_COMPOSER_TOOLS_V0_SERVICE,
                    "unregister",
                    [registration_id],
                )

            return cleanup

        try:
            return self._scope.stage(activate)
        except Exception:
            dispose_callback()
            raise


class _ComposerToolsV0Factory:
    _sakura_host_service_factory = True

    def __init__(
        self,
        host_call: Callable[[str, str, Sequence[Any]], Any],
        callbacks: Any,
    ) -> None:
        self._host_call = host_call
        self._callbacks = callbacks

    def for_plugin(self, plugin_id: str, scope: EffectScope) -> _ComposerToolsV0Proxy:
        return _ComposerToolsV0Proxy(
            plugin_id,
            scope,
            self._host_call,
            self._callbacks,
        )


class _ArtifactsProxy:
    def __init__(
        self,
        plugin_id: str,
        scope: EffectScope,
        host_call: Callable[[str, str, Sequence[Any]], Any],
    ) -> None:
        self._plugin_id = plugin_id
        self._scope = scope
        self._host_call = host_call
        self._disposers: dict[
            str,
            tuple[Callable[[], None], dict[str, bool]],
        ] = {}

    def allocate(self, descriptor: Mapping[str, Any]) -> dict[str, Any]:
        if not isinstance(descriptor, Mapping):
            raise PluginKernelError("ARTIFACT_DESCRIPTOR_INVALID", plugin_id=self._plugin_id)
        result = self._host_call(
            HOST_ARTIFACTS_SERVICE,
            "allocate",
            [self._plugin_id, dict(descriptor)],
        )
        artifact_id = result.get("artifactId") if isinstance(result, Mapping) else None
        path = result.get("path") if isinstance(result, Mapping) else None
        if not isinstance(artifact_id, str) or not isinstance(path, str) or not path:
            raise PluginKernelError("ARTIFACT_DESCRIPTOR_INVALID", plugin_id=self._plugin_id)

        ownership = {"transferred": False}

        def cleanup() -> None:
            self._disposers.pop(artifact_id, None)
            if not ownership["transferred"]:
                self._host_call(
                    HOST_ARTIFACTS_SERVICE,
                    "release",
                    [self._plugin_id, artifact_id],
                )

        disposer = self._scope.effect(cleanup)
        self._disposers[artifact_id] = (disposer, ownership)
        return dict(result)

    def commit(self, artifact_id: str) -> dict[str, Any]:
        binding = self._disposers.get(artifact_id)
        if binding is None:
            raise PluginKernelError("ARTIFACT_NOT_FOUND", plugin_id=self._plugin_id)
        result = self._host_call(
            HOST_ARTIFACTS_SERVICE,
            "commit",
            [self._plugin_id, artifact_id],
        )
        if not isinstance(result, Mapping):
            raise PluginKernelError("ARTIFACT_DESCRIPTOR_INVALID", plugin_id=self._plugin_id)
        disposer, ownership = binding
        ownership["transferred"] = True
        disposer()
        return dict(result)

    def release(self, artifact_id: str) -> bool:
        binding = self._disposers.pop(artifact_id, None)
        if binding is None:
            return False
        disposer, _ownership = binding
        disposer()
        return True


class _ArtifactsFactory:
    _sakura_host_service_factory = True

    def __init__(self, host_call: Callable[[str, str, Sequence[Any]], Any]) -> None:
        self._host_call = host_call

    def for_plugin(self, plugin_id: str, scope: EffectScope) -> _ArtifactsProxy:
        return _ArtifactsProxy(plugin_id, scope, self._host_call)


class _CharacterProxy:
    def __init__(
        self,
        plugin_id: str,
        host_call: Callable[[str, str, Sequence[Any]], Any],
    ) -> None:
        self._plugin_id = plugin_id
        self._host_call = host_call

    def get(self, character_id: str) -> dict[str, Any]:
        result = self._host_call(
            HOST_CHARACTER_SERVICE,
            "get",
            [self._plugin_id, character_id],
        )
        if not isinstance(result, Mapping):
            raise PluginKernelError("CHARACTER_EXTENSION_INVALID", plugin_id=self._plugin_id)
        return dict(result)

    def update(self, character_id: str, values: Mapping[str, Any]) -> dict[str, Any]:
        if not isinstance(values, Mapping):
            raise PluginKernelError("CHARACTER_EXTENSION_INVALID", plugin_id=self._plugin_id)
        result = self._host_call(
            HOST_CHARACTER_SERVICE,
            "update",
            [self._plugin_id, character_id, dict(values)],
        )
        if not isinstance(result, Mapping):
            raise PluginKernelError("CHARACTER_EXTENSION_INVALID", plugin_id=self._plugin_id)
        return dict(result)

    def resolve_resource(self, character_id: str, relative_path: str) -> str:
        result = self._host_call(
            HOST_CHARACTER_SERVICE,
            "resolve_resource",
            [self._plugin_id, character_id, relative_path],
        )
        if not isinstance(result, str) or not result:
            raise PluginKernelError("CHARACTER_RESOURCE_INVALID", plugin_id=self._plugin_id)
        return result


class _CharacterFactory:
    _sakura_host_service_factory = True

    def __init__(self, host_call: Callable[[str, str, Sequence[Any]], Any]) -> None:
        self._host_call = host_call

    def for_plugin(self, plugin_id: str, _scope: EffectScope) -> _CharacterProxy:
        return _CharacterProxy(plugin_id, self._host_call)


class _SettingsRegistrationProxy:
    def __init__(
        self,
        plugin_id: str,
        scope: EffectScope,
        host_call: Callable[[str, str, Sequence[Any]], Any],
        callbacks: Any,
    ) -> None:
        self._plugin_id = plugin_id
        self._scope = scope
        self._host_call = host_call
        self._callbacks = callbacks

    def register(
        self,
        descriptor: Mapping[str, Any],
        *,
        load: Callable[[], Any] | None = None,
        save: Callable[[Mapping[str, Any]], Any] | None = None,
        actions: Mapping[str, Callable[[Mapping[str, Any]], Any]] | None = None,
    ) -> Callable[[], None]:
        if not isinstance(descriptor, Mapping):
            raise PluginKernelError("HOST_DESCRIPTOR_INVALID", plugin_id=self._plugin_id)
        if load is not None and not callable(load):
            raise PluginKernelError("SETTINGS_CALLBACK_INVALID", plugin_id=self._plugin_id)
        if save is not None and not callable(save):
            raise PluginKernelError("SETTINGS_CALLBACK_INVALID", plugin_id=self._plugin_id)
        action_callbacks = dict(actions or {})
        if any(
            not isinstance(action_id, str) or not callable(callback)
            for action_id, callback in action_callbacks.items()
        ):
            raise PluginKernelError("SETTINGS_CALLBACK_INVALID", plugin_id=self._plugin_id)
        callback_disposers: list[Callable[[], None]] = []

        def bind(shape: str, callback: Callable[..., Any] | None) -> str | None:
            if callback is None:
                return None
            handle, disposer = self._callbacks.register(
                self._plugin_id,
                shape,
                callback,
                self._scope,
            )
            callback_disposers.append(disposer)
            return handle

        handles = {
            "load": bind("settings.load", load),
            "save": bind("settings.save", save),
            "actions": {
                action_id: bind("settings.action", callback)
                for action_id, callback in action_callbacks.items()
            },
        }
        def activate() -> Callable[[], None]:
            result = self._host_call(
                HOST_SETTINGS_SERVICE,
                "register",
                [self._plugin_id, dict(descriptor), handles],
            )
            registration_id = (
                result.get("registrationId")
                if isinstance(result, Mapping)
                else None
            )
            if not isinstance(registration_id, str) or not registration_id:
                raise PluginKernelError(
                    "HOST_REGISTRATION_INVALID",
                    plugin_id=self._plugin_id,
                )

            def cleanup() -> None:
                self._host_call(
                    HOST_SETTINGS_SERVICE,
                    "unregister",
                    [registration_id],
                )

            return cleanup

        try:
            return self._scope.stage(activate)
        except Exception:
            for disposer in reversed(callback_disposers):
                disposer()
            raise


class _SettingsRegistrationFactory:
    _sakura_host_service_factory = True

    def __init__(
        self,
        host_call: Callable[[str, str, Sequence[Any]], Any],
        callbacks: Any,
    ) -> None:
        self._host_call = host_call
        self._callbacks = callbacks

    def for_plugin(self, plugin_id: str, scope: EffectScope) -> _SettingsRegistrationProxy:
        return _SettingsRegistrationProxy(
            plugin_id,
            scope,
            self._host_call,
            self._callbacks,
        )


class _SettingsSurfaceV0Proxy:
    def __init__(
        self,
        plugin_id: str,
        scope: EffectScope,
        host_call: Callable[[str, str, Sequence[Any]], Any],
    ) -> None:
        self._plugin_id = plugin_id
        self._scope = scope
        self._host_call = host_call

    def register(self, section_id: str, surface: str) -> Callable[[], None]:
        def activate() -> Callable[[], None]:
            result = self._host_call(
                HOST_SETTINGS_SURFACE_V0_SERVICE,
                "register",
                [self._plugin_id, section_id, surface],
            )
            registration_id = result.get("registrationId") if isinstance(result, Mapping) else None
            if not isinstance(registration_id, str) or not registration_id:
                raise PluginKernelError("HOST_REGISTRATION_INVALID", plugin_id=self._plugin_id)

            def cleanup() -> None:
                self._host_call(
                    HOST_SETTINGS_SURFACE_V0_SERVICE,
                    "unregister",
                    [registration_id],
                )

            return cleanup

        return self._scope.stage(activate)


class _SettingsSurfaceV0Factory:
    _sakura_host_service_factory = True

    def __init__(self, host_call: Callable[[str, str, Sequence[Any]], Any]) -> None:
        self._host_call = host_call

    def for_plugin(self, plugin_id: str, scope: EffectScope) -> _SettingsSurfaceV0Proxy:
        return _SettingsSurfaceV0Proxy(plugin_id, scope, self._host_call)


class _SettingsCollectionV0Proxy:
    def __init__(
        self,
        plugin_id: str,
        scope: EffectScope,
        host_call: Callable[[str, str, Sequence[Any]], Any],
        callbacks: Any,
    ) -> None:
        self._plugin_id = plugin_id
        self._scope = scope
        self._host_call = host_call
        self._callbacks = callbacks

    def register(
        self,
        section_id: str,
        descriptor: Mapping[str, Any],
        *,
        query: Callable[..., Any],
        create: Callable[..., Any] | None = None,
        update: Callable[..., Any] | None = None,
        delete: Callable[..., Any] | None = None,
    ) -> Callable[[], None]:
        callbacks = {
            "query": query,
            "create": create,
            "update": update,
            "delete": delete,
        }
        if (
            not isinstance(section_id, str)
            or not isinstance(descriptor, Mapping)
            or not callable(query)
            or any(callback is not None and not callable(callback) for callback in callbacks.values())
        ):
            raise PluginKernelError("SETTINGS_CALLBACK_INVALID", plugin_id=self._plugin_id)
        callback_disposers: list[Callable[[], None]] = []
        handles: dict[str, str | None] = {}
        for operation, callback in callbacks.items():
            if callback is None:
                handles[operation] = None
                continue
            handle, disposer = self._callbacks.register(
                self._plugin_id,
                f"settings.collection.{operation}",
                callback,
                self._scope,
            )
            callback_disposers.append(disposer)
            handles[operation] = handle

        def activate() -> Callable[[], None]:
            result = self._host_call(
                HOST_SETTINGS_COLLECTION_V0_SERVICE,
                "register",
                [self._plugin_id, section_id, dict(descriptor), handles],
            )
            registration_id = result.get("registrationId") if isinstance(result, Mapping) else None
            if not isinstance(registration_id, str) or not registration_id:
                raise PluginKernelError("HOST_REGISTRATION_INVALID", plugin_id=self._plugin_id)

            def cleanup() -> None:
                self._host_call(
                    HOST_SETTINGS_COLLECTION_V0_SERVICE,
                    "unregister",
                    [registration_id],
                )

            return cleanup

        try:
            return self._scope.stage(activate)
        except Exception:
            for disposer in reversed(callback_disposers):
                disposer()
            raise


class _SettingsCollectionV0Factory:
    _sakura_host_service_factory = True

    def __init__(
        self,
        host_call: Callable[[str, str, Sequence[Any]], Any],
        callbacks: Any,
    ) -> None:
        self._host_call = host_call
        self._callbacks = callbacks

    def for_plugin(self, plugin_id: str, scope: EffectScope) -> _SettingsCollectionV0Proxy:
        return _SettingsCollectionV0Proxy(
            plugin_id,
            scope,
            self._host_call,
            self._callbacks,
        )


class _ModelSlotRegistrationProxy:
    def __init__(
        self,
        plugin_id: str,
        scope: EffectScope,
        host_call: Callable[[str, str, Sequence[Any]], Any],
        callbacks: Any,
    ) -> None:
        self._plugin_id = plugin_id
        self._scope = scope
        self._host_call = host_call
        self._callbacks = callbacks

    def register(
        self,
        descriptor: Mapping[str, Any],
        *,
        load: Callable[[], Any],
        save: Callable[[Mapping[str, Any]], Any],
    ) -> Callable[[], None]:
        if not isinstance(descriptor, Mapping) or not callable(load) or not callable(save):
            raise PluginKernelError("MODEL_SLOT_REGISTRATION_INVALID", plugin_id=self._plugin_id)
        callback_disposers: list[Callable[[], None]] = []

        def bind(shape: str, callback: Callable[..., Any]) -> str:
            handle, disposer = self._callbacks.register(
                self._plugin_id,
                shape,
                callback,
                self._scope,
            )
            callback_disposers.append(disposer)
            return handle

        handles = {
            "load": bind("model_slots.load", load),
            "save": bind("model_slots.save", save),
        }

        def activate() -> Callable[[], None]:
            result = self._host_call(
                HOST_MODEL_SLOTS_SERVICE,
                "register",
                [self._plugin_id, dict(descriptor), handles],
            )
            registration_id = result.get("registrationId") if isinstance(result, Mapping) else None
            if not isinstance(registration_id, str) or not registration_id:
                raise PluginKernelError("HOST_REGISTRATION_INVALID", plugin_id=self._plugin_id)

            def cleanup() -> None:
                self._host_call(
                    HOST_MODEL_SLOTS_SERVICE,
                    "unregister",
                    [registration_id],
                )

            return cleanup

        try:
            return self._scope.stage(activate)
        except Exception:
            for disposer in reversed(callback_disposers):
                disposer()
            raise


class _ModelSlotRegistrationFactory:
    _sakura_host_service_factory = True

    def __init__(
        self,
        host_call: Callable[[str, str, Sequence[Any]], Any],
        callbacks: Any,
    ) -> None:
        self._host_call = host_call
        self._callbacks = callbacks

    def for_plugin(self, plugin_id: str, scope: EffectScope) -> _ModelSlotRegistrationProxy:
        return _ModelSlotRegistrationProxy(
            plugin_id,
            scope,
            self._host_call,
            self._callbacks,
        )


def build_worker_host_services(
    service_keys: Sequence[str],
    host_call: Callable[[str, str, Sequence[Any]], Any],
    callbacks: Any,
) -> dict[str, Any]:
    supported = {
        HOST_TOOLS_SERVICE: "tools.handler",
        HOST_CONTEXT_SERVICE: "context.contributor",
    }
    services: dict[str, Any] = {
        service_key: _RegistrationFactory(
            service_key,
            supported[service_key],
            host_call,
            callbacks,
        )
        for service_key in dict.fromkeys(service_keys)
        if service_key in supported
    }
    if HOST_ARTIFACTS_SERVICE in service_keys:
        services[HOST_ARTIFACTS_SERVICE] = _ArtifactsFactory(host_call)
    if HOST_CHARACTER_SERVICE in service_keys:
        services[HOST_CHARACTER_SERVICE] = _CharacterFactory(host_call)
    if HOST_SETTINGS_SERVICE in service_keys:
        services[HOST_SETTINGS_SERVICE] = _SettingsRegistrationFactory(
            host_call,
            callbacks,
        )
    if HOST_SETTINGS_SURFACE_V0_SERVICE in service_keys:
        services[HOST_SETTINGS_SURFACE_V0_SERVICE] = _SettingsSurfaceV0Factory(host_call)
    if HOST_SETTINGS_COLLECTION_V0_SERVICE in service_keys:
        services[HOST_SETTINGS_COLLECTION_V0_SERVICE] = _SettingsCollectionV0Factory(
            host_call,
            callbacks,
        )
    if HOST_MODEL_SLOTS_SERVICE in service_keys:
        services[HOST_MODEL_SLOTS_SERVICE] = _ModelSlotRegistrationFactory(
            host_call,
            callbacks,
        )
    if HOST_COMPOSER_TOOLS_V0_SERVICE in service_keys:
        services[HOST_COMPOSER_TOOLS_V0_SERVICE] = _ComposerToolsV0Factory(
            host_call,
            callbacks,
        )
    return services


__all__ = [
    "HOST_CONTEXT_SERVICE",
    "HOST_ARTIFACTS_SERVICE",
    "HOST_CHARACTER_SERVICE",
    "HOST_MODEL_SLOTS_SERVICE",
    "HOST_SETTINGS_SERVICE",
    "HOST_SETTINGS_COLLECTION_V0_SERVICE",
    "HOST_SETTINGS_SURFACE_V0_SERVICE",
    "HOST_TOOLS_SERVICE",
    "HOST_COMPOSER_TOOLS_V0_SERVICE",
    "build_worker_host_services",
]
