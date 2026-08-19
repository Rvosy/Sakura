"""Thin composable Plugin API v3 kernel hosted inside the private worker.

The kernel deliberately knows only lifecycle, named services, events,
transforms, reversible effects, and plugin-scoped config.  Domain services
such as TTS or Memory are ordinary values registered by plugins.
"""

from __future__ import annotations

import json
import re
import secrets
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

from app.core.runtime_log import log_event
from app.plugins.models import PLUGIN_API_V3_VERSION, PluginSpec
from app.storage.atomic import atomic_write_text
from app.storage.paths import StoragePaths


_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,199}$")
_METHOD = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")
_HOST_EVENT_PREFIX = "sakura.host."


class PluginKernelError(RuntimeError):
    """Stable internal error that can be projected as a sanitized code."""

    def __init__(
        self,
        code: str,
        *,
        plugin_id: str = "",
        service_key: str = "",
    ) -> None:
        super().__init__(code)
        self.code = code
        self.plugin_id = plugin_id
        self.service_key = service_key


class MissingServiceError(PluginKernelError):
    def __init__(self, service_key: str) -> None:
        super().__init__("SERVICE_MISSING", service_key=service_key)


class ServiceConflictError(PluginKernelError):
    def __init__(self, plugin_id: str, service_key: str) -> None:
        super().__init__(
            "SERVICE_CONFLICT",
            plugin_id=plugin_id,
            service_key=service_key,
        )


class _Effect:
    def __init__(self, cleanup: Callable[[], Any]) -> None:
        self._cleanup = cleanup
        self._disposed = False

    @property
    def disposed(self) -> bool:
        return self._disposed

    def __call__(self) -> None:
        if self._disposed:
            return
        self._disposed = True
        self._cleanup()


class _StagedEffect:
    def __init__(self, activate: Callable[[], Callable[[], Any]]) -> None:
        self._activate = activate
        self._cleanup: Callable[[], Any] | None = None
        self._disposed = False

    def commit(self) -> None:
        if self._disposed or self._cleanup is not None:
            return
        cleanup = self._activate()
        if not callable(cleanup):
            raise TypeError("staged effect activation must return cleanup")
        self._cleanup = cleanup

    def dispose(self) -> None:
        if self._disposed:
            return
        self._disposed = True
        cleanup = self._cleanup
        self._cleanup = None
        if cleanup is not None:
            cleanup()


class EffectScope:
    """A LIFO collection of idempotent cleanup effects."""

    def __init__(self, plugin_id: str, label: str = "root") -> None:
        self.plugin_id = plugin_id
        self.label = label
        self._effects: list[_Effect] = []
        self._staged: list[_StagedEffect] = []
        self._committed = False
        self._disposed = False

    @property
    def disposed(self) -> bool:
        return self._disposed

    @property
    def effect_count(self) -> int:
        return sum(not effect.disposed for effect in self._effects)

    def effect(self, cleanup: Callable[[], Any]) -> Callable[[], None]:
        if not callable(cleanup):
            raise TypeError("effect cleanup must be callable")
        if self._disposed:
            raise PluginKernelError("EFFECT_SCOPE_DISPOSED", plugin_id=self.plugin_id)
        effect = _Effect(cleanup)
        self._effects.append(effect)

        def dispose_effect() -> None:
            try:
                effect()
            finally:
                self._effects[:] = [item for item in self._effects if item is not effect]

        return dispose_effect

    def stage(
        self,
        activate: Callable[[], Callable[[], Any]],
    ) -> Callable[[], None]:
        """Delay externally visible registration until this scope activates."""

        if not callable(activate):
            raise TypeError("staged effect activation must be callable")
        if self._disposed:
            raise PluginKernelError("EFFECT_SCOPE_DISPOSED", plugin_id=self.plugin_id)
        staged = _StagedEffect(activate)
        self._staged.append(staged)
        dispose_effect = self.effect(staged.dispose)
        if self._committed:
            try:
                staged.commit()
            except Exception:
                dispose_effect()
                raise
        return dispose_effect

    def commit(self) -> None:
        if self._disposed:
            raise PluginKernelError("EFFECT_SCOPE_DISPOSED", plugin_id=self.plugin_id)
        if self._committed:
            return
        for staged in self._staged:
            staged.commit()
        self._committed = True

    def dispose(self) -> None:
        if self._disposed:
            return
        self._disposed = True
        while self._effects:
            effect = self._effects.pop()
            try:
                effect()
            except Exception as error:  # noqa: BLE001 - cleanup must continue
                log_event(
                    "PluginKernel",
                    "插件 Effect 清理失败",
                    {
                        "plugin_id": self.plugin_id,
                        "scope": self.label,
                        "error_type": type(error).__name__,
                    },
                )


@dataclass
class _CallbackBinding:
    plugin_id: str
    shape: str
    callback: Callable[..., Any]
    active: bool = False


class CallbackRegistry:
    """Generation-private opaque callbacks registered through Host Services."""

    def __init__(self) -> None:
        self._callbacks: dict[str, _CallbackBinding] = {}
        self._active_plugins: set[str] = set()

    def register(
        self,
        plugin_id: str,
        shape: str,
        callback: Callable[..., Any],
        scope: EffectScope,
    ) -> tuple[str, Callable[[], None]]:
        _validate_identifier(shape, "CALLBACK_SHAPE_INVALID")
        if not callable(callback):
            raise PluginKernelError("CALLBACK_INVALID", plugin_id=plugin_id)
        handle = ""
        while not handle or handle in self._callbacks:
            handle = f"cb_{secrets.token_hex(16)}"
        binding = _CallbackBinding(
            plugin_id,
            shape,
            callback,
            active=plugin_id in self._active_plugins,
        )
        self._callbacks[handle] = binding

        def remove() -> None:
            if self._callbacks.get(handle) is binding:
                del self._callbacks[handle]

        return handle, scope.effect(remove)

    def activate_plugin(self, plugin_id: str) -> None:
        self._active_plugins.add(plugin_id)
        for binding in self._callbacks.values():
            if binding.plugin_id == plugin_id:
                binding.active = True

    def deactivate_plugin(self, plugin_id: str) -> None:
        self._active_plugins.discard(plugin_id)
        for binding in self._callbacks.values():
            if binding.plugin_id == plugin_id:
                binding.active = False

    def invoke(self, handle: str, shape: str, args: Sequence[Any]) -> Any:
        binding = self._callbacks.get(handle)
        if binding is None:
            raise PluginKernelError("CALLBACK_INVALID")
        if binding.shape != shape:
            raise PluginKernelError("CALLBACK_SHAPE_INVALID", plugin_id=binding.plugin_id)
        if not binding.active:
            raise PluginKernelError("CALLBACK_INACTIVE", plugin_id=binding.plugin_id)
        return binding.callback(*args)

    def clear(self) -> None:
        self._callbacks.clear()
        self._active_plugins.clear()

    @property
    def count(self) -> int:
        return len(self._callbacks)


@dataclass
class _ServiceBinding:
    plugin_id: str
    value: Any
    exports: frozenset[str]
    published: bool = False


class _ServiceRegistry:
    def __init__(self, on_change: Callable[[str, Any | None, Any | None], None]) -> None:
        self._bindings: dict[str, _ServiceBinding] = {}
        self._on_change = on_change

    def install(self, service_key: str, value: Any) -> None:
        _validate_identifier(service_key, "SERVICE_KEY_INVALID")
        if service_key in self._bindings:
            raise PluginKernelError("SERVICE_CONFLICT", service_key=service_key)
        self._bindings[service_key] = _ServiceBinding(
            "sakura.kernel",
            value,
            frozenset(),
            True,
        )
        self._on_change(service_key, None, value)

    def provide(
        self,
        plugin_id: str,
        service_key: str,
        value: Any,
        exports: Iterable[str],
        scope: EffectScope,
        *,
        published: bool,
    ) -> Callable[[], None]:
        _validate_identifier(service_key, "SERVICE_KEY_INVALID")
        if service_key in self._bindings:
            raise ServiceConflictError(plugin_id, service_key)
        exported = frozenset(exports)
        for method in exported:
            if not isinstance(method, str) or not _METHOD.fullmatch(method):
                raise PluginKernelError(
                    "SERVICE_EXPORT_INVALID",
                    plugin_id=plugin_id,
                    service_key=service_key,
                )
            if not callable(getattr(value, method, None)):
                raise PluginKernelError(
                    "SERVICE_EXPORT_INVALID",
                    plugin_id=plugin_id,
                    service_key=service_key,
                )
        binding = _ServiceBinding(plugin_id, value, exported, published)
        self._bindings[service_key] = binding

        def remove() -> None:
            current = self._bindings.get(service_key)
            if current is not binding:
                return
            del self._bindings[service_key]
            if current.published:
                self._on_change(service_key, current.value, None)

        disposer = scope.effect(remove)
        if published:
            self._on_change(service_key, None, value)
        return disposer

    def publish_plugin(self, plugin_id: str) -> None:
        for service_key, binding in list(self._bindings.items()):
            if binding.plugin_id != plugin_id or binding.published:
                continue
            binding.published = True
            self._on_change(service_key, None, binding.value)

    def get(self, service_key: str) -> Any:
        binding = self._bindings.get(service_key)
        if binding is None or not binding.published:
            raise MissingServiceError(service_key)
        return binding.value

    def provider_id(self, service_key: str) -> str | None:
        binding = self._bindings.get(service_key)
        if binding is None or not binding.published:
            return None
        return binding.plugin_id

    def has_binding(self, service_key: str, plugin_id: str) -> bool:
        binding = self._bindings.get(service_key)
        return binding is not None and binding.plugin_id == plugin_id

    def keys_for_plugin(self, plugin_id: str, *, published_only: bool = True) -> set[str]:
        return {
            key
            for key, binding in self._bindings.items()
            if binding.plugin_id == plugin_id and (binding.published or not published_only)
        }

    def published_keys(self) -> list[str]:
        return sorted(key for key, binding in self._bindings.items() if binding.published)

    def clear(self) -> None:
        for service_key, binding in list(self._bindings.items()):
            del self._bindings[service_key]
            if binding.published:
                self._on_change(service_key, binding.value, None)

    def call(self, service_key: str, method: str, args: Sequence[Any]) -> Any:
        binding = self._bindings.get(service_key)
        if binding is None or not binding.published:
            raise MissingServiceError(service_key)
        if method not in binding.exports:
            raise PluginKernelError(
                "SERVICE_METHOD_NOT_EXPORTED",
                plugin_id=binding.plugin_id,
                service_key=service_key,
            )
        callback = getattr(binding.value, method, None)
        if not callable(callback):
            raise PluginKernelError(
                "SERVICE_METHOD_NOT_EXPORTED",
                plugin_id=binding.plugin_id,
                service_key=service_key,
            )
        return callback(*args)


@dataclass
class _Handler:
    plugin_id: str
    callback: Callable[[Any], Any]


class _HandlerRegistry:
    def __init__(self, label: str, failure_sink: Callable[[str, Exception], None]) -> None:
        self._label = label
        self._failure_sink = failure_sink
        self._handlers: dict[str, list[_Handler]] = {}

    def on(
        self,
        plugin_id: str,
        name: str,
        callback: Callable[[Any], Any],
        scope: EffectScope,
    ) -> Callable[[], None]:
        _validate_identifier(name, f"{self._label.upper()}_NAME_INVALID")
        if not callable(callback):
            raise TypeError(f"{self._label} handler must be callable")
        handler = _Handler(plugin_id, callback)
        self._handlers.setdefault(name, []).append(handler)

        def remove() -> None:
            handlers = self._handlers.get(name)
            if handlers is None:
                return
            self._handlers[name] = [item for item in handlers if item is not handler]
            if not self._handlers[name]:
                del self._handlers[name]

        return scope.effect(remove)

    def emit(self, name: str, value: Any) -> None:
        for handler in list(self._handlers.get(name, ())):
            try:
                handler.callback(value)
            except Exception as error:  # noqa: BLE001 - handlers are isolated
                self._failure_sink(handler.plugin_id, error)
                log_event(
                    "PluginKernel",
                    f"插件 {self._label} handler 失败",
                    {
                        "plugin_id": handler.plugin_id,
                        "name": name,
                        "error_type": type(error).__name__,
                    },
                )

    def transform(self, name: str, value: Any) -> Any:
        current = value
        for handler in list(self._handlers.get(name, ())):
            try:
                current = handler.callback(current)
            except Exception as error:  # noqa: BLE001 - retain last valid value
                self._failure_sink(handler.plugin_id, error)
                log_event(
                    "PluginKernel",
                    "插件 transform handler 失败",
                    {
                        "plugin_id": handler.plugin_id,
                        "name": name,
                        "error_type": type(error).__name__,
                    },
                )
        return current

    def count(self, *, plugin_id: str | None = None) -> int:
        return sum(
            1
            for handlers in self._handlers.values()
            for handler in handlers
            if plugin_id is None or handler.plugin_id == plugin_id
        )


@dataclass
class _Injection:
    plugin_id: str
    service_key: str
    callback: Callable[[Any, "KernelEffectScope"], Any]
    owner_scope: EffectScope
    child_scope: EffectScope | None = None
    child_disposer: Callable[[], None] | None = None
    service: Any = None


class PluginConfig:
    """Plugin-scoped JSON config with atomic user overrides."""

    def __init__(self, plugin_id: str, plugin_root: Path, data_dir: Path, scope: EffectScope) -> None:
        self._plugin_id = plugin_id
        self._plugin_root = plugin_root
        self._data_dir = data_dir
        self._scope = scope
        self._handlers: list[Callable[[Mapping[str, Any]], str]] = []

    def get(self) -> dict[str, Any]:
        merged = _read_json_object(self._plugin_root / "config.json")
        merged.update(_read_json_object(self._data_dir / "config.json"))
        return merged

    def save(self, values: Mapping[str, Any]) -> list[str]:
        """Merge top-level user overrides; retained as the Settings-friendly default."""

        return self.update(values)

    def update(self, values: Mapping[str, Any]) -> list[str]:
        self._validate(values)
        overrides = _read_json_object(self._data_dir / "config.json")
        overrides.update(dict(values))
        return self._write(overrides)

    def replace(self, values: Mapping[str, Any]) -> list[str]:
        """Explicitly replace the complete user override document."""

        self._validate(values)
        return self._write(dict(values))

    def _validate(self, values: Mapping[str, Any]) -> None:
        if not isinstance(values, Mapping) or not _json_compatible(values):
            raise PluginKernelError("CONFIG_VALUE_INVALID", plugin_id=self._plugin_id)

    def _write(self, overrides: Mapping[str, Any]) -> list[str]:
        target = self._data_dir / "config.json"
        atomic_write_text(
            target,
            json.dumps(dict(overrides), ensure_ascii=False, indent=2),
        )
        effective = self.get()
        if not self._handlers:
            return ["restart_required"]
        results: list[str] = []
        for handler in list(self._handlers):
            try:
                result = handler(dict(effective))
            except Exception:  # noqa: BLE001 - stable application status only
                result = "error"
            results.append(result if result in {"applied", "restart_required", "error"} else "error")
        return results

    def on_change(
        self,
        handler: Callable[[Mapping[str, Any]], str],
    ) -> Callable[[], None]:
        if not callable(handler):
            raise TypeError("config handler must be callable")
        self._handlers.append(handler)

        def remove() -> None:
            self._handlers[:] = [item for item in self._handlers if item is not handler]

        return self._scope.effect(remove)


class KernelEffectScope:
    """Restricted helper passed to an inject callback."""

    def __init__(self, kernel: "PluginKernel", plugin_id: str, scope: EffectScope) -> None:
        self._kernel = kernel
        self._plugin_id = plugin_id
        self._scope = scope

    def effect(self, cleanup: Callable[[], Any]) -> Callable[[], None]:
        return self._scope.effect(cleanup)

    def on(self, name: str, handler: Callable[[Any], Any]) -> Callable[[], None]:
        return self._kernel.on(self._plugin_id, name, handler, self._scope)

    def on_transform(self, name: str, handler: Callable[[Any], Any]) -> Callable[[], None]:
        return self._kernel.on_transform(self._plugin_id, name, handler, self._scope)


class PluginContextV3(KernelEffectScope):
    """The complete first-stage API exposed to one v3 plugin."""

    def __init__(
        self,
        kernel: "PluginKernel",
        plugin_id: str,
        plugin_root: Path,
        data_dir: Path,
        scope: EffectScope,
    ) -> None:
        super().__init__(kernel, plugin_id, scope)
        self.plugin_id = plugin_id
        self.config = PluginConfig(plugin_id, plugin_root, data_dir, scope)
        self._data_dir = data_dir

    def data_path(self, relative_path: str) -> Path:
        """Resolve one plugin-private persistent path without crossing its data root."""

        if not isinstance(relative_path, str) or not relative_path.strip():
            raise PluginKernelError("PLUGIN_DATA_PATH_INVALID", plugin_id=self.plugin_id)
        raw = relative_path.strip()
        lexical = Path(raw)
        if (
            lexical.is_absolute()
            or lexical.drive
            or raw.startswith(("\\", "//"))
            or ".." in lexical.parts
        ):
            raise PluginKernelError("PLUGIN_DATA_PATH_INVALID", plugin_id=self.plugin_id)
        try:
            root = self._data_dir.resolve(strict=False)
            resolved = (self._data_dir / lexical).resolve(strict=False)
            resolved.relative_to(root)
        except (OSError, ValueError) as error:
            raise PluginKernelError(
                "PLUGIN_DATA_PATH_INVALID",
                plugin_id=self.plugin_id,
            ) from error
        return resolved

    def provide(
        self,
        service_key: str,
        service: Any,
        *,
        exports: Iterable[str] = (),
    ) -> Callable[[], None]:
        return self._kernel.provide(
            self.plugin_id,
            service_key,
            service,
            exports,
            self._scope,
        )

    def get(self, service_key: str) -> Any:
        return self._kernel.get(service_key, self.plugin_id, self._scope)

    def inject(
        self,
        service_key: str,
        setup: Callable[[Any, KernelEffectScope], Any],
    ) -> Callable[[], None]:
        return self._kernel.inject(
            self.plugin_id,
            service_key,
            setup,
            self._scope,
        )

    def emit(self, name: str, payload: Any) -> None:
        self._kernel.emit(name, payload, source_plugin=self.plugin_id)

    def transform(self, name: str, value: Any) -> Any:
        return self._kernel.transform(name, value)


class PluginKernel:
    """Application-scoped mechanisms shared by all v3 plugins in one worker."""

    def __init__(self) -> None:
        self._active_plugins: set[str] = set()
        self._runtime_failures: list[tuple[str, Exception, str]] = []
        self.events = _HandlerRegistry("event", self._handler_failed)
        self.transforms = _HandlerRegistry("transform", self._handler_failed)
        self.services = _ServiceRegistry(self._service_changed)
        self._injections: list[_Injection] = []

    def install_host_service(self, service_key: str, factory: Any) -> None:
        self.services.install(service_key, factory)

    def provide(
        self,
        plugin_id: str,
        service_key: str,
        service: Any,
        exports: Iterable[str],
        scope: EffectScope,
    ) -> Callable[[], None]:
        return self.services.provide(
            plugin_id,
            service_key,
            service,
            exports,
            scope,
            published=plugin_id in self._active_plugins,
        )

    def get(self, service_key: str, plugin_id: str, scope: EffectScope) -> Any:
        return self._scoped_service(self.services.get(service_key), plugin_id, scope)

    def activate_plugin(self, plugin_id: str) -> None:
        self._active_plugins.add(plugin_id)
        self.services.publish_plugin(plugin_id)

    def deactivate_plugin(self, plugin_id: str) -> None:
        self._active_plugins.discard(plugin_id)

    def on(
        self,
        plugin_id: str,
        name: str,
        handler: Callable[[Any], Any],
        scope: EffectScope,
    ) -> Callable[[], None]:
        return self.events.on(plugin_id, name, handler, scope)

    def emit(self, name: str, payload: Any, *, source_plugin: str | None = None) -> None:
        _validate_identifier(name, "EVENT_NAME_INVALID")
        if source_plugin is not None and name.startswith(_HOST_EVENT_PREFIX):
            raise PluginKernelError("HOST_EVENT_RESERVED", plugin_id=source_plugin)
        self.events.emit(name, payload)

    def on_transform(
        self,
        plugin_id: str,
        name: str,
        handler: Callable[[Any], Any],
        scope: EffectScope,
    ) -> Callable[[], None]:
        return self.transforms.on(plugin_id, name, handler, scope)

    def transform(self, name: str, value: Any) -> Any:
        _validate_identifier(name, "TRANSFORM_NAME_INVALID")
        return self.transforms.transform(name, value)

    def inject(
        self,
        plugin_id: str,
        service_key: str,
        callback: Callable[[Any, KernelEffectScope], Any],
        scope: EffectScope,
    ) -> Callable[[], None]:
        _validate_identifier(service_key, "SERVICE_KEY_INVALID")
        if not callable(callback):
            raise TypeError("inject setup must be callable")
        injection = _Injection(plugin_id, service_key, callback, scope)
        self._injections.append(injection)

        def remove() -> None:
            self._stop_injection(injection)
            self._injections[:] = [item for item in self._injections if item is not injection]

        disposer = scope.effect(remove)
        try:
            service = self.services.get(service_key)
        except MissingServiceError:
            return disposer
        self._start_injection(injection, service, propagate=True)
        return disposer

    def drain_runtime_failures(self) -> list[tuple[str, Exception, str]]:
        failures = list(self._runtime_failures)
        self._runtime_failures.clear()
        return failures

    def close(self) -> None:
        self.services.clear()
        self._injections.clear()
        self._active_plugins.clear()

    def _handler_failed(self, plugin_id: str, error: Exception) -> None:
        if isinstance(error, ServiceConflictError):
            self._runtime_failures.append((plugin_id, error, "conflict"))

    def _service_changed(self, service_key: str, previous: Any | None, current: Any | None) -> None:
        for injection in list(self._injections):
            if injection.service_key != service_key:
                continue
            if previous is not None:
                self._stop_injection(injection)
            if current is not None:
                try:
                    self._start_injection(injection, current, propagate=False)
                except Exception as error:  # pragma: no cover - guarded by propagate=False
                    self._runtime_failures.append((injection.plugin_id, error, "inject"))

    def _start_injection(self, injection: _Injection, service: Any, *, propagate: bool) -> None:
        child = EffectScope(injection.plugin_id, f"inject:{injection.service_key}")
        injection.child_scope = child
        injection.child_disposer = injection.owner_scope.effect(child.dispose)
        injection.service = service
        try:
            injection.callback(
                self._scoped_service(service, injection.plugin_id, child),
                KernelEffectScope(self, injection.plugin_id, child),
            )
        except Exception as error:
            self._stop_injection(injection)
            if propagate:
                raise
            self._runtime_failures.append((injection.plugin_id, error, "inject"))

    @staticmethod
    def _stop_injection(injection: _Injection) -> None:
        child = injection.child_scope
        child_disposer = injection.child_disposer
        injection.child_scope = None
        injection.child_disposer = None
        injection.service = None
        if child_disposer is not None:
            child_disposer()
        elif child is not None:
            child.dispose()

    @staticmethod
    def _scoped_service(value: Any, plugin_id: str, scope: EffectScope) -> Any:
        if not bool(getattr(value, "_sakura_host_service_factory", False)):
            return value
        factory = getattr(value, "for_plugin", None)
        if not callable(factory):
            raise PluginKernelError("HOST_SERVICE_INVALID", plugin_id=plugin_id)
        return factory(plugin_id, scope)


@dataclass
class PluginRecordV3:
    spec: PluginSpec
    state: str
    reason_code: str
    missing_services: tuple[str, ...] = ()
    conflicts: tuple[str, ...] = ()
    instance: Any = None
    context: PluginContextV3 | None = None
    root_scope: EffectScope | None = None
    compatibility_shutdown: Callable[[], Any] | None = None
    sticky_failure: bool = False
    runtime_conflict: str = ""

    @property
    def plugin_id(self) -> str:
        return self.spec.plugin_id


class PluginKernelManager:
    """Discover-independent v3 lifecycle manager used only inside the worker."""

    def __init__(
        self,
        app_root: Path,
        specs: Sequence[PluginSpec],
        *,
        host_service_keys: Sequence[str] = (),
        host_call: Callable[[str, str, Sequence[Any]], Any] | None = None,
    ) -> None:
        self._app_root = Path(app_root)
        self.kernel = PluginKernel()
        self.callbacks = CallbackRegistry()
        self._records: dict[str, PluginRecordV3] = {}
        self._closed = False
        if host_service_keys:
            if host_call is None:
                raise PluginKernelError("HOST_BRIDGE_UNAVAILABLE")
            from app.plugins.host_services import build_worker_host_services

            for service_key, factory in build_worker_host_services(
                host_service_keys,
                host_call,
                self.callbacks,
            ).items():
                self.kernel.install_host_service(service_key, factory)
        for spec in specs:
            if spec.plugin_id in self._records:
                self._records[spec.plugin_id].state = "failed"
                self._records[spec.plugin_id].reason_code = "PLUGIN_ID_CONFLICT"
                self._records[spec.plugin_id].sticky_failure = True
                continue
            try:
                _validate_v3_spec(spec)
            except PluginKernelError as error:
                record = PluginRecordV3(spec, "failed", error.code, sticky_failure=True)
            else:
                record = PluginRecordV3(
                    spec,
                    "waiting" if spec.enabled else "disabled",
                    "MISSING_SERVICE" if spec.enabled else "PLUGIN_DISABLED",
                )
            self._records[spec.plugin_id] = record
        self._reconcile()

    def snapshot(self) -> dict[str, Any]:
        return {
            "schemaVersion": 1,
            "plugins": [self._public_record(record) for record in self._records.values()],
            "services": self.kernel.services.published_keys(),
            "eventHandlerCount": self.kernel.events.count(),
            "transformHandlerCount": self.kernel.transforms.count(),
            "callbackHandleCount": self.callbacks.count,
        }

    def call_service(self, service_key: str, method: str, args: Sequence[Any]) -> Any:
        try:
            return self.kernel.services.call(service_key, method, args)
        except ServiceConflictError as error:
            self._mark_runtime_conflict(error.plugin_id, error.service_key)
            raise
        finally:
            self._stabilize_after_runtime()

    def emit_host_event(self, name: str, payload: Any) -> None:
        if not name.startswith(_HOST_EVENT_PREFIX):
            raise PluginKernelError("HOST_EVENT_NAME_INVALID")
        self.kernel.emit(name, payload)
        self._stabilize_after_runtime()

    def transform(self, name: str, value: Any) -> Any:
        result = self.kernel.transform(name, value)
        self._stabilize_after_runtime()
        return result

    def invoke_callback(self, handle: str, shape: str, args: Sequence[Any]) -> Any:
        try:
            return self.callbacks.invoke(handle, shape, args)
        except ServiceConflictError as error:
            self._mark_runtime_conflict(error.plugin_id, error.service_key)
            raise
        finally:
            self._stabilize_after_runtime()

    def set_enabled(self, plugin_id: str, enabled: bool) -> dict[str, Any]:
        record = self._records.get(plugin_id)
        if record is None:
            raise PluginKernelError("PLUGIN_NOT_FOUND", plugin_id=plugin_id)
        if record.spec.enabled == enabled:
            return self.snapshot()
        desired = {item.plugin_id: item.spec.enabled for item in self._records.values()}
        desired[plugin_id] = enabled
        from app.plugins.discovery import save_plugin_enabled_overrides

        save_plugin_enabled_overrides(self._app_root, desired)
        record.spec = replace(record.spec, enabled=enabled)
        record.sticky_failure = False
        record.runtime_conflict = ""
        if not enabled:
            self._deactivate_provider_and_consumers(record, "disabled", "PLUGIN_DISABLED")
        else:
            record.state = "waiting"
            record.reason_code = "MISSING_SERVICE"
        self._reconcile()
        return self.snapshot()

    def reload(self, plugin_id: str) -> dict[str, Any]:
        """Reload one enabled plugin and rebuild its required consumers."""
        record = self._records.get(plugin_id)
        if record is None:
            raise PluginKernelError("PLUGIN_NOT_FOUND", plugin_id=plugin_id)
        if not record.spec.enabled:
            raise PluginKernelError("PLUGIN_DISABLED", plugin_id=plugin_id)
        self._deactivate_provider_and_consumers(
            record,
            "waiting",
            "MISSING_SERVICE",
        )
        record.sticky_failure = False
        record.runtime_conflict = ""
        record.conflicts = ()
        record.missing_services = ()
        self._reconcile()
        return self.snapshot()

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        for record in self._dependency_order(reverse=True):
            self._dispose_record(record)
        self.callbacks.clear()
        self.kernel.close()

    def _reconcile(self) -> None:
        if self._closed:
            return
        declared_conflicts = self._declared_conflicts()
        cycle_plugins = self._dependency_cycles(declared_conflicts)

        for record in self._records.values():
            if not record.spec.enabled:
                if record.state == "active":
                    self._deactivate_provider_and_consumers(record, "disabled", "PLUGIN_DISABLED")
                record.state = "disabled"
                record.reason_code = "PLUGIN_DISABLED"
                record.missing_services = ()
                continue
            conflicts = declared_conflicts.get(record.plugin_id, ())
            if conflicts:
                if record.state == "active":
                    self._deactivate_provider_and_consumers(
                        record,
                        "conflict",
                        "SERVICE_CONFLICT",
                    )
                record.state = "conflict"
                record.reason_code = "SERVICE_CONFLICT"
                record.conflicts = tuple(conflicts)
                continue
            record.conflicts = ()
            if record.state == "conflict" and not record.runtime_conflict:
                record.state = "waiting"
                record.reason_code = "MISSING_SERVICE"
            if record.plugin_id in cycle_plugins:
                if record.state == "active":
                    self._deactivate_provider_and_consumers(
                        record,
                        "failed",
                        "DEPENDENCY_CYCLE",
                    )
                record.state = "failed"
                record.reason_code = "DEPENDENCY_CYCLE"
                continue
            if record.reason_code == "DEPENDENCY_CYCLE":
                record.state = "waiting"
                record.reason_code = "MISSING_SERVICE"
            if record.runtime_conflict:
                provider = self.kernel.services.provider_id(record.runtime_conflict)
                if provider is not None and provider != record.plugin_id:
                    record.state = "conflict"
                    record.reason_code = "SERVICE_CONFLICT"
                    record.conflicts = (record.runtime_conflict,)
                    continue
                record.runtime_conflict = ""
                record.state = "waiting"
                record.reason_code = "MISSING_SERVICE"
            if record.sticky_failure:
                continue

        changed = True
        while changed:
            changed = False
            for record in self._dependency_order():
                if record.state != "active":
                    continue
                missing = self._missing_required(record)
                if missing:
                    self._dispose_record(record)
                    record.state = "waiting"
                    record.reason_code = "MISSING_SERVICE"
                    record.missing_services = missing
                    changed = True

        progress = True
        while progress:
            progress = False
            for record in self._dependency_order():
                if (
                    not record.spec.enabled
                    or record.state in {"active", "disabled", "failed", "conflict"}
                    or record.sticky_failure
                ):
                    continue
                missing = self._missing_required(record)
                record.missing_services = missing
                if missing:
                    record.state = "waiting"
                    record.reason_code = "MISSING_SERVICE"
                    continue
                self._activate(record)
                progress = progress or record.state == "active"
                self._consume_runtime_failures()

    def _activate(self, record: PluginRecordV3) -> None:
        root = EffectScope(record.plugin_id)
        record.root_scope = root
        shutdown: Callable[[], Any] | None = None
        try:
            instance = _import_v3_plugin(self._app_root, record.spec)
            candidate_shutdown = getattr(instance, "shutdown", None)
            shutdown = candidate_shutdown if callable(candidate_shutdown) else None
            plugin_root = record.spec.plugin_root
            assert plugin_root is not None
            data_dir = StoragePaths(self._app_root).plugin_data_for(record.plugin_id)
            data_dir.mkdir(parents=True, exist_ok=True)
            context = PluginContextV3(
                self.kernel,
                record.plugin_id,
                plugin_root,
                data_dir,
                root,
            )
            setup = getattr(instance, "setup", None)
            if not callable(setup):
                raise PluginKernelError("PLUGIN_SETUP_MISSING", plugin_id=record.plugin_id)
            result = setup(context)
            if result is not None:
                raise PluginKernelError("PLUGIN_SETUP_RESULT_INVALID", plugin_id=record.plugin_id)
            missing_declared = tuple(
                key
                for key in record.spec.provides
                if not self.kernel.services.has_binding(key, record.plugin_id)
            )
            if missing_declared:
                raise PluginKernelError(
                    "DECLARED_SERVICE_MISSING",
                    plugin_id=record.plugin_id,
                    service_key=missing_declared[0],
                )
            self.callbacks.activate_plugin(record.plugin_id)
            root.commit()
            self.kernel.activate_plugin(record.plugin_id)
            record.instance = instance
            record.context = context
            record.compatibility_shutdown = shutdown
            record.state = "active"
            record.reason_code = "ACTIVE"
            record.missing_services = ()
        except ServiceConflictError as error:
            self.callbacks.deactivate_plugin(record.plugin_id)
            self.kernel.deactivate_plugin(record.plugin_id)
            self._run_compatibility_shutdown(record.plugin_id, shutdown)
            root.dispose()
            record.root_scope = None
            record.instance = None
            record.context = None
            record.compatibility_shutdown = None
            record.state = "conflict"
            record.reason_code = "SERVICE_CONFLICT"
            record.runtime_conflict = error.service_key
            record.conflicts = (error.service_key,)
        except Exception as error:  # noqa: BLE001 - expose stable status only
            self.callbacks.deactivate_plugin(record.plugin_id)
            self.kernel.deactivate_plugin(record.plugin_id)
            self._run_compatibility_shutdown(record.plugin_id, shutdown)
            root.dispose()
            record.root_scope = None
            record.instance = None
            record.context = None
            record.compatibility_shutdown = None
            record.state = "failed"
            record.reason_code = (
                error.code if isinstance(error, PluginKernelError) else "PLUGIN_SETUP_FAILED"
            )
            record.sticky_failure = True
            log_event(
                "PluginKernel",
                "v3 插件 setup 失败",
                {
                    "plugin_id": record.plugin_id,
                    "reason_code": record.reason_code,
                    "error_type": type(error).__name__,
                },
            )

    def _dispose_record(self, record: PluginRecordV3) -> None:
        self.kernel.deactivate_plugin(record.plugin_id)
        self.callbacks.deactivate_plugin(record.plugin_id)
        root = record.root_scope
        shutdown = record.compatibility_shutdown
        record.root_scope = None
        record.context = None
        record.instance = None
        record.compatibility_shutdown = None
        self._run_compatibility_shutdown(record.plugin_id, shutdown)
        if root is not None:
            root.dispose()

    @staticmethod
    def _run_compatibility_shutdown(
        plugin_id: str,
        shutdown: Callable[[], Any] | None,
    ) -> None:
        if shutdown is None:
            return
        try:
            shutdown()
        except Exception as error:  # noqa: BLE001 - Effects must still be released
            log_event(
                "PluginKernel",
                "v3 插件兼容 shutdown hook 失败",
                {
                    "plugin_id": plugin_id,
                    "error_type": type(error).__name__,
                },
            )

    def _deactivate_provider_and_consumers(
        self,
        provider: PluginRecordV3,
        state: str,
        reason_code: str,
    ) -> None:
        visited: set[str] = set()

        def dispose(record: PluginRecordV3, target: bool = False) -> None:
            if record.plugin_id in visited:
                return
            visited.add(record.plugin_id)
            provided = self.kernel.services.keys_for_plugin(record.plugin_id)
            for candidate in self._records.values():
                if candidate.state == "active" and provided.intersection(candidate.spec.requires):
                    dispose(candidate)
            self._dispose_record(record)
            record.state = state if target else "waiting"
            record.reason_code = reason_code if target else "MISSING_SERVICE"
            if not target:
                record.missing_services = self._missing_required(record)

        dispose(provider, True)

    def _mark_runtime_conflict(self, plugin_id: str, service_key: str) -> None:
        record = self._records.get(plugin_id)
        if record is None:
            return
        self._deactivate_provider_and_consumers(record, "conflict", "SERVICE_CONFLICT")
        record.runtime_conflict = service_key
        record.conflicts = (service_key,)

    def _stabilize_after_runtime(self) -> None:
        self._consume_runtime_failures()
        self._reconcile()

    def _consume_runtime_failures(self) -> None:
        for plugin_id, error, kind in self.kernel.drain_runtime_failures():
            if isinstance(error, ServiceConflictError) or kind == "conflict":
                key = error.service_key if isinstance(error, ServiceConflictError) else ""
                self._mark_runtime_conflict(plugin_id, key)
                continue
            record = self._records.get(plugin_id)
            if record is None:
                continue
            self._deactivate_provider_and_consumers(record, "failed", "PLUGIN_RUNTIME_FAILED")
            record.sticky_failure = True

    def _missing_required(self, record: PluginRecordV3) -> tuple[str, ...]:
        return tuple(
            key
            for key in record.spec.requires
            if self.kernel.services.provider_id(key) is None
        )

    def _declared_conflicts(self) -> dict[str, tuple[str, ...]]:
        providers: dict[str, list[str]] = {}
        for record in self._records.values():
            if not record.spec.enabled:
                continue
            for service_key in record.spec.provides:
                providers.setdefault(service_key, []).append(record.plugin_id)
        conflicts: dict[str, list[str]] = {}
        for service_key, plugin_ids in providers.items():
            if len(plugin_ids) < 2:
                continue
            for plugin_id in plugin_ids:
                conflicts.setdefault(plugin_id, []).append(service_key)
        return {plugin_id: tuple(sorted(keys)) for plugin_id, keys in conflicts.items()}

    def _dependency_cycles(self, conflicts: Mapping[str, Sequence[str]]) -> set[str]:
        providers: dict[str, str] = {}
        for record in self._records.values():
            if record.spec.enabled and record.plugin_id not in conflicts:
                for service_key in record.spec.provides:
                    providers[service_key] = record.plugin_id
        graph = {
            record.plugin_id: {
                providers[key]
                for key in record.spec.requires
                if key in providers
            }
            for record in self._records.values()
            if record.spec.enabled and record.plugin_id not in conflicts
        }
        color: dict[str, int] = {}
        stack: list[str] = []
        cycles: set[str] = set()

        def visit(plugin_id: str) -> None:
            color[plugin_id] = 1
            stack.append(plugin_id)
            for dependency in graph.get(plugin_id, ()):
                if color.get(dependency, 0) == 0:
                    visit(dependency)
                elif color.get(dependency) == 1:
                    cycles.update(stack[stack.index(dependency):])
            stack.pop()
            color[plugin_id] = 2

        for plugin_id in graph:
            if color.get(plugin_id, 0) == 0:
                visit(plugin_id)
        return cycles

    def _dependency_order(self, *, reverse: bool = False) -> list[PluginRecordV3]:
        records = list(self._records.values())
        providers = {
            service_key: record.plugin_id
            for record in records
            for service_key in record.spec.provides
        }
        remaining = {record.plugin_id: record for record in records}
        ordered: list[PluginRecordV3] = []
        while remaining:
            ready = sorted(
                (
                    record
                    for record in remaining.values()
                    if all(providers.get(key) not in remaining for key in record.spec.requires)
                ),
                key=lambda item: item.plugin_id,
            )
            if not ready:
                ready = [remaining[min(remaining)]]
            for record in ready:
                remaining.pop(record.plugin_id, None)
                ordered.append(record)
        return list(reversed(ordered)) if reverse else ordered

    @staticmethod
    def _public_record(record: PluginRecordV3) -> dict[str, Any]:
        root = record.root_scope
        return {
            "pluginId": record.plugin_id[:200],
            "name": (record.spec.name or record.plugin_id)[:120],
            "version": record.spec.version[:64],
            "author": record.spec.author[:120],
            "description": record.spec.description[:500],
            "apiVersion": PLUGIN_API_V3_VERSION,
            "enabled": record.spec.enabled,
            "required": False,
            "supported": True,
            "state": record.state,
            "reasonCode": record.reason_code,
            "provides": list(record.spec.provides),
            "requires": list(record.spec.requires),
            "optional": list(record.spec.optional),
            "missingServices": list(record.missing_services),
            "conflicts": list(record.conflicts),
            "effectCount": root.effect_count if root is not None else 0,
            "permissions": [],
            "unavailable": [],
            "sections": [],
        }


def _validate_v3_spec(spec: PluginSpec) -> None:
    if spec.api_version != PLUGIN_API_V3_VERSION:
        raise PluginKernelError("API_VERSION_UNSUPPORTED", plugin_id=spec.plugin_id)
    _validate_identifier(spec.plugin_id, "PLUGIN_ID_INVALID")
    if spec.plugin_root is None or not spec.entry or ":" not in spec.entry:
        raise PluginKernelError("PLUGIN_MANIFEST_INVALID", plugin_id=spec.plugin_id)
    for service_key in (*spec.provides, *spec.requires, *spec.optional):
        _validate_identifier(service_key, "SERVICE_KEY_INVALID")


def _import_v3_plugin(app_root: Path, spec: PluginSpec) -> Any:
    module_name, _, class_name = spec.entry.partition(":")
    if not module_name or not class_name:
        raise PluginKernelError("PLUGIN_ENTRY_INVALID", plugin_id=spec.plugin_id)
    from app.plugins.manager import _import_plugin_module

    module = _import_plugin_module(app_root, spec, module_name)
    plugin_type = getattr(module, class_name, None)
    if not isinstance(plugin_type, type):
        raise PluginKernelError("PLUGIN_ENTRY_INVALID", plugin_id=spec.plugin_id)
    return plugin_type()


def _validate_identifier(value: object, code: str) -> str:
    if not isinstance(value, str) or not _IDENTIFIER.fullmatch(value):
        raise PluginKernelError(code)
    return value


def _read_json_object(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return {}
    return dict(value) if isinstance(value, Mapping) else {}


def _json_compatible(value: object) -> bool:
    try:
        json.dumps(value, ensure_ascii=False)
    except (TypeError, ValueError):
        return False
    return True
