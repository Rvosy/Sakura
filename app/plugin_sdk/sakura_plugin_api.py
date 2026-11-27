"""Public type surface available to Plugin API v4 code.

Plugins receive the runtime ``context`` object from their entry's ``setup``
method.  These protocols are optional authoring aids and deliberately contain
no transport, process, or bootstrap implementation.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Protocol, Literal, overload


class PluginLogger(Protocol):
    """Returns local acceptance only; logging never changes plugin business results."""

    def debug(self, message: str, *, fields: Mapping[str, Any] | None = None) -> bool: ...
    def info(self, message: str, *, fields: Mapping[str, Any] | None = None) -> bool: ...
    def warning(self, message: str, *, fields: Mapping[str, Any] | None = None) -> bool: ...
    def error(self, message: str, *, fields: Mapping[str, Any] | None = None) -> bool: ...
    def model_call(self, candidate: Mapping[str, Any]) -> object: ...


class Service(Protocol):
    """Marker protocol for a JSON-facing plugin service implementation."""


class BoundService(Protocol):
    @property
    def identity(self) -> dict[str, str]: ...

    def invoke(self, method: str, *args: object, timeout_seconds: float | None = None) -> Any: ...

    def __getattr__(self, method: str) -> Callable[..., Any]: ...


class PluginConfig(Protocol):
    def get(self) -> dict[str, Any]: ...

    def replace(self, values: Mapping[str, Any]) -> str: ...

    def update(self, values: Mapping[str, Any]) -> str: ...

    def on_change(
        self,
        handler: Callable[[dict[str, Any]], str],
    ) -> Callable[[], None]: ...


class PluginContext(Protocol):
    plugin_id: str
    config: PluginConfig

    @overload
    def get(self, service_key: Literal["sakura.host.logging"]) -> PluginLogger: ...
    @overload
    def get(self, service_key: str) -> Any: ...

    def bind(self, service_key: str) -> BoundService: ...

    @property
    def caller_id(self) -> str | None: ...

    @property
    def caller_scope(self) -> str | None: ...

    @staticmethod
    def exception_diagnostics(error: BaseException, *, secrets: Iterable[str] = ()) -> dict[str, object]: ...

    def provide(
        self,
        service_key: str,
        service: object,
        *,
        exports: Iterable[str] = (),
    ) -> Callable[[], None]: ...

    def on(
        self,
        name: str,
        handler: Callable[[object], object],
    ) -> Callable[[], None]: ...

    def effect(self, cleanup: Callable[[], object]) -> Callable[[], None]: ...

    def data_path(self, relative_path: str) -> Path: ...


__all__ = ["BoundService", "PluginConfig", "PluginContext", "PluginLogger", "Service"]
