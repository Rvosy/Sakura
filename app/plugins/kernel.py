"""Small Plugin API v3 kernel hosted inside the generation-private Worker.

The Worker scans and loads plugins once.  This module intentionally contains no
dynamic reconciliation, partial reload, session binding, injection, transform,
or compatibility lifecycle hooks.
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
_PLUGIN_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
_METHOD = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")
_HOST_EVENT_PREFIX = "sakura.host."


class PluginKernelError(RuntimeError):
    """Stable internal error projected across the private Worker bridge."""

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

    def __call__(self) -> None:
        if self._disposed:
            return
        self._disposed = True
        self._cleanup()


class _StagedEffect:
    """One registration made visible only after plugin setup succeeds."""

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
    """A plugin-local, idempotent LIFO cleanup stack."""

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
        if not callable(activate):
            raise TypeError("staged effect activation must be callable")
        if self._disposed:
            raise PluginKernelError("EFFECT_SCOPE_DISPOSED", plugin_id=self.plugin_id)
        staged = _StagedEffect(activate)
        self._staged.append(staged)
        disposer = self.effect(staged.dispose)
        if self._committed:
            try:
                staged.commit()
            except Exception:
                disposer()
                raise
        return disposer

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
                    "插件清理失败",
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


@dataclass
class _ServiceBinding:
    plugin_id: str
    value: Any
    exports: frozenset[str]
    published: bool


class _ServiceRegistry:
    def __init__(self) -> None:
        self._bindings: dict[str, _ServiceBinding] = {}

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

    def provide(
        self,
        plugin_id: str,
        service_key: str,
        value: Any,
        exports: Iterable[str],
        scope: EffectScope,
    ) -> Callable[[], None]:
        _validate_identifier(service_key, "SERVICE_KEY_INVALID")
        if service_key in self._bindings:
            raise ServiceConflictError(plugin_id, service_key)
        exported = frozenset(exports)
        for method in exported:
            if (
                not isinstance(method, str)
                or not _METHOD.fullmatch(method)
                or not callable(getattr(value, method, None))
            ):
                raise PluginKernelError(
                    "SERVICE_EXPORT_INVALID",
                    plugin_id=plugin_id,
                    service_key=service_key,
                )
        binding = _ServiceBinding(plugin_id, value, exported, False)
        self._bindings[service_key] = binding

        def remove() -> None:
            if self._bindings.get(service_key) is binding:
                del self._bindings[service_key]

        return scope.effect(remove)

    def publish_plugin(self, plugin_id: str) -> None:
        for binding in self._bindings.values():
            if binding.plugin_id == plugin_id:
                binding.published = True

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
    failure_logged: bool = False


class _EventRegistry:
    def __init__(self) -> None:
        self._handlers: dict[str, list[_Handler]] = {}

    def on(
        self,
        plugin_id: str,
        name: str,
        callback: Callable[[Any], Any],
        scope: EffectScope,
    ) -> Callable[[], None]:
        _validate_identifier(name, "EVENT_NAME_INVALID")
        if not callable(callback):
            raise TypeError("event handler must be callable")
        handler = _Handler(plugin_id, callback)

        def remove() -> None:
            handlers = self._handlers.get(name)
            if handlers is None:
                return
            self._handlers[name] = [item for item in handlers if item is not handler]
            if not self._handlers[name]:
                del self._handlers[name]

        def activate() -> Callable[[], None]:
            self._handlers.setdefault(name, []).append(handler)
            return remove

        return scope.stage(activate)

    def emit(self, name: str, value: Any) -> None:
        _validate_identifier(name, "EVENT_NAME_INVALID")
        for handler in list(self._handlers.get(name, ())):
            try:
                handler.callback(value)
            except Exception as error:  # noqa: BLE001 - one handler never stops dispatch
                if not handler.failure_logged:
                    handler.failure_logged = True
                    log_event(
                        "PluginKernel",
                        "插件事件 handler 失败",
                        {
                            "plugin_id": handler.plugin_id,
                            "name": name,
                            "error_type": type(error).__name__,
                        },
                    )


class PluginConfig:
    """Plugin-scoped JSON config with atomic user overrides."""

    def __init__(
        self,
        plugin_id: str,
        plugin_root: Path,
        data_dir: Path,
        scope: EffectScope,
    ) -> None:
        self._plugin_id = plugin_id
        self._plugin_root = plugin_root
        self._data_dir = data_dir
        self._scope = scope
        self._handlers: list[Callable[[Mapping[str, Any]], str]] = []

    def get(self) -> dict[str, Any]:
        merged = self._read(self._plugin_root / "config.json")
        merged.update(self._read(self._data_dir / "config.json"))
        return merged

    def update(self, values: Mapping[str, Any]) -> list[str]:
        self._validate(values)
        overrides = self._read(self._data_dir / "config.json")
        overrides.update(dict(values))
        return self._write(overrides)

    def replace(self, values: Mapping[str, Any]) -> list[str]:
        self._validate(values)
        return self._write(dict(values))

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

    def _validate(self, values: Mapping[str, Any]) -> None:
        if not isinstance(values, Mapping) or not _json_compatible(values):
            raise PluginKernelError("CONFIG_VALUE_INVALID", plugin_id=self._plugin_id)

    def _read(self, path: Path) -> dict[str, Any]:
        if not path.is_file():
            return {}
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
            raise PluginKernelError(
                "PLUGIN_CONFIG_INVALID",
                plugin_id=self._plugin_id,
            ) from error
        if not isinstance(value, Mapping):
            raise PluginKernelError("PLUGIN_CONFIG_INVALID", plugin_id=self._plugin_id)
        return dict(value)

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
            except Exception:  # noqa: BLE001 - config save reports a stable result
                result = "error"
            results.append(
                result
                if result in {"applied", "restart_required", "error"}
                else "error"
            )
        return results


class PluginContextV3:
    """The complete public API exposed to one Plugin API v3 instance."""

    def __init__(
        self,
        kernel: "PluginKernel",
        plugin_id: str,
        plugin_root: Path,
        data_dir: Path,
        scope: EffectScope,
    ) -> None:
        self._kernel = kernel
        self._scope = scope
        self._data_dir = data_dir
        self._plugin_id = plugin_id
        self.config = PluginConfig(plugin_id, plugin_root, data_dir, scope)

    def get(self, service_key: str) -> Any:
        return self._kernel.get(service_key, self._plugin_id, self._scope)

    def provide(
        self,
        service_key: str,
        service: Any,
        *,
        exports: Iterable[str] = (),
    ) -> Callable[[], None]:
        return self._kernel.provide(
            self._plugin_id,
            service_key,
            service,
            exports,
            self._scope,
        )

    def on(self, name: str, handler: Callable[[Any], Any]) -> Callable[[], None]:
        return self._kernel.on(self._plugin_id, name, handler, self._scope)

    def effect(self, cleanup: Callable[[], Any]) -> Callable[[], None]:
        return self._scope.effect(cleanup)

    def data_path(self, relative_path: str) -> Path:
        if not isinstance(relative_path, str) or not relative_path.strip():
            raise PluginKernelError("PLUGIN_DATA_PATH_INVALID", plugin_id=self._plugin_id)
        raw = relative_path.strip()
        lexical = Path(raw)
        if (
            lexical.is_absolute()
            or lexical.drive
            or raw.startswith(("\\", "//"))
            or ".." in lexical.parts
        ):
            raise PluginKernelError("PLUGIN_DATA_PATH_INVALID", plugin_id=self._plugin_id)
        try:
            root = self._data_dir.resolve(strict=False)
            resolved = (self._data_dir / lexical).resolve(strict=False)
            resolved.relative_to(root)
        except (OSError, ValueError) as error:
            raise PluginKernelError(
                "PLUGIN_DATA_PATH_INVALID",
                plugin_id=self._plugin_id,
            ) from error
        return resolved


class PluginKernel:
    """Small service/event mechanism shared by plugins in one Worker."""

    def __init__(self) -> None:
        self.services = _ServiceRegistry()
        self.events = _EventRegistry()

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
        )

    def get(self, service_key: str, plugin_id: str, scope: EffectScope) -> Any:
        value = self.services.get(service_key)
        if not bool(getattr(value, "_sakura_host_service_factory", False)):
            return value
        factory = getattr(value, "for_plugin", None)
        if not callable(factory):
            raise PluginKernelError("HOST_SERVICE_INVALID", plugin_id=plugin_id)
        return factory(plugin_id, scope)

    def on(
        self,
        plugin_id: str,
        name: str,
        handler: Callable[[Any], Any],
        scope: EffectScope,
    ) -> Callable[[], None]:
        return self.events.on(plugin_id, name, handler, scope)

    def emit(self, name: str, payload: Any) -> None:
        self.events.emit(name, payload)


@dataclass
class PluginRecordV3:
    spec: PluginSpec
    state: str
    reason_code: str
    root_scope: EffectScope | None = None

    @property
    def plugin_id(self) -> str:
        return self.spec.plugin_id


class PluginKernelManager:
    """Own one plugin graph and reconcile only the scopes affected by changes."""

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
        self._activation_order: list[str] = []
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
        self._prepare_records(specs)
        self._load_once()

    def snapshot(self) -> dict[str, Any]:
        return {
            "schemaVersion": 1,
            "plugins": [
                self._public_record(self._records[plugin_id])
                for plugin_id in sorted(self._records)
            ],
        }

    def call_service(self, service_key: str, method: str, args: Sequence[Any]) -> Any:
        return self.kernel.services.call(service_key, method, args)

    def emit_host_event(self, name: str, payload: Any) -> None:
        if not name.startswith(_HOST_EVENT_PREFIX):
            raise PluginKernelError("HOST_EVENT_NAME_INVALID")
        self.kernel.emit(name, payload)

    def invoke_callback(self, handle: str, shape: str, args: Sequence[Any]) -> Any:
        return self.callbacks.invoke(handle, shape, args)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        for plugin_id in reversed(self._activation_order):
            record = self._records[plugin_id]
            self.callbacks.deactivate_plugin(plugin_id)
            root = record.root_scope
            record.root_scope = None
            if root is not None:
                root.dispose()
        self._activation_order.clear()
        self.callbacks.clear()

    def reconcile(
        self,
        specs: Sequence[PluginSpec],
        *,
        reload_ids: Sequence[str] = (),
    ) -> dict[str, Any]:
        """Apply a complete target inventory without replacing the Worker.

        The target inventory is Core-owned.  Only changed plugins, explicitly
        reloaded plugins, service-conflict peers, and their transitive consumers
        are disposed and set up again.  Every unrelated active scope remains
        intact.
        """

        if self._closed:
            raise PluginKernelError("GENERATION_INVALIDATED")
        target = self._normalized_specs(specs)
        requested = set(reload_ids)
        unknown = requested - set(target)
        if unknown:
            raise PluginKernelError("PLUGIN_NOT_FOUND", plugin_id=sorted(unknown)[0])

        current_specs = {
            plugin_id: record.spec for plugin_id, record in self._records.items()
        }
        changed = {
            plugin_id
            for plugin_id in set(current_specs) | set(target)
            if current_specs.get(plugin_id) != target.get(plugin_id)
        }
        affected = self._affected_plugins(
            current_specs,
            target,
            changed | requested,
        )
        if not affected:
            return self.snapshot()

        # Activation order is topological, therefore its reverse always closes
        # consumers before providers.  Failed/disabled records have no scope.
        for plugin_id in reversed(tuple(self._activation_order)):
            if plugin_id in affected:
                self._dispose(plugin_id)

        for plugin_id in set(self._records) - set(target):
            del self._records[plugin_id]

        for plugin_id in sorted(affected & set(target)):
            spec = target[plugin_id]
            record = self._records.get(plugin_id)
            if record is None:
                record = PluginRecordV3(spec, "failed", "NOT_LOADED")
                self._records[plugin_id] = record
            else:
                record.spec = spec
                record.root_scope = None
            if not spec.enabled:
                record.state = "disabled"
                record.reason_code = "PLUGIN_DISABLED"
                continue
            try:
                _validate_v3_spec(spec)
            except PluginKernelError as error:
                record.state = "failed"
                record.reason_code = error.code
            else:
                record.state = "failed"
                record.reason_code = "NOT_LOADED"

        self._load_candidates(affected & set(target))
        return self.snapshot()

    @staticmethod
    def _normalized_specs(specs: Sequence[PluginSpec]) -> dict[str, PluginSpec]:
        counts: dict[str, int] = {}
        normalized: dict[str, PluginSpec] = {}
        for raw in specs:
            spec = raw
            if spec.required and spec.source != "user" and not spec.enabled:
                spec = replace(spec, enabled=True)
            counts[spec.plugin_id] = counts.get(spec.plugin_id, 0) + 1
            normalized.setdefault(spec.plugin_id, spec)
        duplicate = next(
            (plugin_id for plugin_id, count in sorted(counts.items()) if count > 1),
            None,
        )
        if duplicate is not None:
            raise PluginKernelError("PLUGIN_ID_CONFLICT", plugin_id=duplicate)
        return normalized

    @staticmethod
    def _affected_plugins(
        current: Mapping[str, PluginSpec],
        target: Mapping[str, PluginSpec],
        roots: set[str],
    ) -> set[str]:
        affected = set(roots)
        all_specs = {**current, **target}
        changed = True
        while changed:
            changed = False
            provided = {
                service
                for plugin_id in affected
                for spec in (current.get(plugin_id), target.get(plugin_id))
                if spec is not None
                for service in spec.provides
            }
            for plugin_id, spec in all_specs.items():
                # Consumers must be recreated after any provider replacement.
                # Providers declaring the same service participate in the same
                # conflict and therefore belong to the local reconcile too.
                if plugin_id not in affected and (
                    provided.intersection(spec.requires)
                    or provided.intersection(spec.provides)
                ):
                    affected.add(plugin_id)
                    changed = True
        return affected

    def _dispose(self, plugin_id: str) -> None:
        record = self._records.get(plugin_id)
        if record is None:
            return
        self.callbacks.deactivate_plugin(plugin_id)
        root = record.root_scope
        record.root_scope = None
        if root is not None:
            root.dispose()
        self._activation_order[:] = [
            item for item in self._activation_order if item != plugin_id
        ]

    def _load_candidates(self, requested: set[str]) -> None:
        candidates = {
            plugin_id
            for plugin_id in requested
            if plugin_id in self._records
            and self._records[plugin_id].spec.enabled
            and self._records[plugin_id].reason_code == "NOT_LOADED"
        }
        if not candidates:
            return

        host_keys = {
            key
            for key, binding in self.kernel.services._bindings.items()
            if binding.plugin_id == "sakura.kernel"
        }
        providers: dict[str, list[str]] = {}
        for plugin_id, record in self._records.items():
            if not record.spec.enabled:
                continue
            for service_key in record.spec.provides:
                providers.setdefault(service_key, []).append(plugin_id)
        for service_key, plugin_ids in providers.items():
            if len(plugin_ids) > 1 or service_key in host_keys:
                for plugin_id in plugin_ids:
                    if plugin_id in candidates:
                        self._fail(plugin_id, "SERVICE_CONFLICT")

        candidates = {
            plugin_id
            for plugin_id in candidates
            if self._records[plugin_id].reason_code == "NOT_LOADED"
        }
        unique_provider = {
            service_key: plugin_ids[0]
            for service_key, plugin_ids in providers.items()
            if len(plugin_ids) == 1
        }
        graph = {
            plugin_id: {
                unique_provider[key]
                for key in self._records[plugin_id].spec.requires
                if key in unique_provider and unique_provider[key] in candidates
            }
            for plugin_id in candidates
        }
        for plugin_id in self._cycle_members(graph):
            self._fail(plugin_id, "DEPENDENCY_CYCLE")

        remaining = {
            plugin_id
            for plugin_id in candidates
            if self._records[plugin_id].reason_code == "NOT_LOADED"
        }
        while remaining:
            ready = sorted(
                plugin_id
                for plugin_id in remaining
                if not (graph.get(plugin_id, set()) & remaining)
            )
            if not ready:
                for plugin_id in sorted(remaining):
                    self._fail(plugin_id, "DEPENDENCY_CYCLE")
                break
            for plugin_id in ready:
                remaining.remove(plugin_id)
                record = self._records[plugin_id]
                if any(
                    self.kernel.services.provider_id(key) is None
                    for key in record.spec.requires
                ):
                    self._fail(plugin_id, "MISSING_SERVICE")
                    continue
                self._activate(record)

    def _prepare_records(self, specs: Sequence[PluginSpec]) -> None:
        duplicate_ids = {
            plugin_id
            for plugin_id in {spec.plugin_id for spec in specs}
            if sum(spec.plugin_id == plugin_id for spec in specs) > 1
        }
        for spec in specs:
            if spec.plugin_id in self._records:
                continue
            if spec.required and spec.source != "user" and not spec.enabled:
                spec = replace(spec, enabled=True)
            if spec.plugin_id in duplicate_ids:
                self._records[spec.plugin_id] = PluginRecordV3(
                    spec,
                    "failed",
                    "PLUGIN_ID_CONFLICT",
                )
                continue
            try:
                _validate_v3_spec(spec)
            except PluginKernelError as error:
                self._records[spec.plugin_id] = PluginRecordV3(
                    spec,
                    "failed",
                    error.code,
                )
                continue
            self._records[spec.plugin_id] = PluginRecordV3(
                spec,
                "failed" if spec.enabled else "disabled",
                "NOT_LOADED" if spec.enabled else "PLUGIN_DISABLED",
            )

    def _load_once(self) -> None:
        candidates = {
            plugin_id
            for plugin_id, record in self._records.items()
            if record.spec.enabled and record.reason_code == "NOT_LOADED"
        }
        providers: dict[str, list[str]] = {}
        for plugin_id in candidates:
            for service_key in self._records[plugin_id].spec.provides:
                providers.setdefault(service_key, []).append(plugin_id)

        host_keys = {
            key
            for key in self.kernel.services._bindings  # private, local preflight only
        }
        for service_key, plugin_ids in providers.items():
            if len(plugin_ids) > 1 or service_key in host_keys:
                for plugin_id in plugin_ids:
                    self._fail(plugin_id, "SERVICE_CONFLICT")

        candidates = {
            plugin_id
            for plugin_id in candidates
            if self._records[plugin_id].reason_code == "NOT_LOADED"
        }
        unique_provider = {
            service_key: plugin_ids[0]
            for service_key, plugin_ids in providers.items()
            if len(plugin_ids) == 1 and plugin_ids[0] in candidates
        }
        for plugin_id in sorted(candidates):
            record = self._records[plugin_id]
            if any(
                key not in host_keys and key not in unique_provider
                for key in record.spec.requires
            ):
                self._fail(plugin_id, "MISSING_SERVICE")

        candidates = {
            plugin_id
            for plugin_id in candidates
            if self._records[plugin_id].reason_code == "NOT_LOADED"
        }
        graph = {
            plugin_id: {
                unique_provider[key]
                for key in self._records[plugin_id].spec.requires
                if key in unique_provider and unique_provider[key] in candidates
            }
            for plugin_id in candidates
        }
        for plugin_id in self._cycle_members(graph):
            self._fail(plugin_id, "DEPENDENCY_CYCLE")

        candidates = {
            plugin_id
            for plugin_id in candidates
            if self._records[plugin_id].reason_code == "NOT_LOADED"
        }
        remaining = set(candidates)
        ordered: list[str] = []
        while remaining:
            ready = sorted(
                plugin_id
                for plugin_id in remaining
                if not (graph.get(plugin_id, set()) & remaining)
            )
            if not ready:
                for plugin_id in sorted(remaining):
                    self._fail(plugin_id, "DEPENDENCY_CYCLE")
                break
            for plugin_id in ready:
                remaining.remove(plugin_id)
                ordered.append(plugin_id)

        for plugin_id in ordered:
            record = self._records[plugin_id]
            if any(
                self.kernel.services.provider_id(key) is None
                for key in record.spec.requires
            ):
                self._fail(plugin_id, "MISSING_SERVICE")
                continue
            self._activate(record)

    def _activate(self, record: PluginRecordV3) -> None:
        root = EffectScope(record.plugin_id)
        try:
            instance = _import_v3_plugin(self._app_root, record.spec)
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
                raise PluginKernelError(
                    "PLUGIN_SETUP_MISSING",
                    plugin_id=record.plugin_id,
                )
            result = setup(context)
            if result is not None:
                raise PluginKernelError(
                    "PLUGIN_SETUP_RESULT_INVALID",
                    plugin_id=record.plugin_id,
                )
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
            root.commit()
            self.callbacks.activate_plugin(record.plugin_id)
            self.kernel.services.publish_plugin(record.plugin_id)
            record.root_scope = root
            record.state = "active"
            record.reason_code = "ACTIVE"
            self._activation_order.append(record.plugin_id)
        except Exception as error:  # noqa: BLE001 - expose only a stable code
            self.callbacks.deactivate_plugin(record.plugin_id)
            root.dispose()
            record.root_scope = None
            record.state = "failed"
            record.reason_code = (
                error.code
                if isinstance(error, PluginKernelError)
                else "PLUGIN_SETUP_FAILED"
            )
            log_event(
                "PluginKernel",
                "v3 插件 setup 失败",
                {
                    "plugin_id": record.plugin_id,
                    "reason_code": record.reason_code,
                    "error_type": type(error).__name__,
                },
            )

    def _fail(self, plugin_id: str, reason_code: str) -> None:
        record = self._records[plugin_id]
        record.state = "failed"
        record.reason_code = reason_code

    @staticmethod
    def _cycle_members(graph: Mapping[str, set[str]]) -> set[str]:
        color: dict[str, int] = {}
        stack: list[str] = []
        cycles: set[str] = set()

        def visit(plugin_id: str) -> None:
            color[plugin_id] = 1
            stack.append(plugin_id)
            for dependency in sorted(graph.get(plugin_id, ())):
                if color.get(dependency, 0) == 0:
                    visit(dependency)
                elif color.get(dependency) == 1:
                    cycles.update(stack[stack.index(dependency):])
            stack.pop()
            color[plugin_id] = 2

        for plugin_id in sorted(graph):
            if color.get(plugin_id, 0) == 0:
                visit(plugin_id)
        return cycles

    @staticmethod
    def _public_record(record: PluginRecordV3) -> dict[str, Any]:
        source = (
            record.spec.source
            if record.spec.source in {"bundled", "user"}
            else "bundled"
        )
        return {
            "pluginId": record.plugin_id[:64],
            "name": (record.spec.name or record.plugin_id)[:120],
            "version": record.spec.version[:64],
            "author": record.spec.author[:120],
            "description": record.spec.description[:500],
            "enabled": record.spec.enabled,
            "required": bool(record.spec.required and source != "user"),
            "source": source,
            "canUninstall": source == "user",
            "supported": record.spec.api_version == PLUGIN_API_V3_VERSION,
            "state": record.state,
            "reasonCode": record.reason_code,
            "sections": [],
        }


def _validate_v3_spec(spec: PluginSpec) -> None:
    if spec.api_version != PLUGIN_API_V3_VERSION:
        raise PluginKernelError("API_VERSION_UNSUPPORTED", plugin_id=spec.plugin_id)
    if not isinstance(spec.plugin_id, str) or not _PLUGIN_ID.fullmatch(spec.plugin_id):
        raise PluginKernelError("PLUGIN_ID_INVALID", plugin_id=spec.plugin_id)
    if spec.plugin_id.endswith("."):
        raise PluginKernelError("PLUGIN_ID_INVALID", plugin_id=spec.plugin_id)
    if spec.source == "user" and spec.required:
        raise PluginKernelError("PLUGIN_MANIFEST_INVALID", plugin_id=spec.plugin_id)
    if spec.plugin_root is None or not spec.entry or ":" not in spec.entry:
        raise PluginKernelError("PLUGIN_MANIFEST_INVALID", plugin_id=spec.plugin_id)
    for service_key in (*spec.provides, *spec.requires):
        _validate_identifier(service_key, "SERVICE_KEY_INVALID")


def _import_v3_plugin(app_root: Path, spec: PluginSpec) -> Any:
    module_name, _, class_name = spec.entry.partition(":")
    if not module_name or not class_name:
        raise PluginKernelError("PLUGIN_ENTRY_INVALID", plugin_id=spec.plugin_id)
    from app.plugins.importer import import_plugin_module

    module = import_plugin_module(app_root, spec, module_name)
    plugin_type = getattr(module, class_name, None)
    if not isinstance(plugin_type, type):
        raise PluginKernelError("PLUGIN_ENTRY_INVALID", plugin_id=spec.plugin_id)
    return plugin_type()


def _validate_identifier(value: object, code: str) -> str:
    if not isinstance(value, str) or not _IDENTIFIER.fullmatch(value):
        raise PluginKernelError(code)
    return value


def _json_compatible(value: object) -> bool:
    try:
        json.dumps(value, ensure_ascii=False)
    except (TypeError, ValueError):
        return False
    return True


__all__ = [
    "CallbackRegistry",
    "EffectScope",
    "PluginConfig",
    "PluginContextV3",
    "PluginKernelError",
    "PluginKernelManager",
]
