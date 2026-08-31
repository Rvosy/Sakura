"""Public, stdlib-only Sakura Plugin API v4 process SDK.

This module is imported by the standalone plugin runner.  Keep it free of
``app.*`` imports and third-party dependencies: it is part of the small Python
surface made visible inside every plugin process.
"""

from __future__ import annotations

import json
import os
import queue
import struct
import threading
import time
import uuid
from pathlib import Path
from typing import Any, BinaryIO, Callable, Iterable, Mapping, Sequence


MAX_FRAME_BYTES = 1024 * 1024
DEFAULT_CALL_TIMEOUT_SECONDS = 3.0
MAX_PENDING_REQUESTS = 32


class PluginApiError(RuntimeError):
    def __init__(
        self,
        code: str,
        message: str | None = None,
        *,
        plugin_id: str = "",
        service_key: str = "",
    ) -> None:
        super().__init__(message or code)
        self.code = code
        self.plugin_id = plugin_id
        self.service_key = service_key


def json_value(value: object) -> object:
    """Return a detached JSON value or fail at the process boundary."""

    try:
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError, OverflowError) as error:
        raise PluginApiError("SERVICE_PAYLOAD_INVALID") from error
    if len(encoded) > MAX_FRAME_BYTES:
        raise PluginApiError("PLUGIN_FRAME_TOO_LARGE")
    return json.loads(encoded.decode("utf-8"))


def read_frame(stream: BinaryIO) -> dict[str, Any]:
    header = _read_exact(stream, 4)
    size = struct.unpack(">I", header)[0]
    if size <= 0 or size > MAX_FRAME_BYTES:
        raise PluginApiError("PLUGIN_FRAME_INVALID")
    payload = _read_exact(stream, size)
    try:
        value = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise PluginApiError("PLUGIN_FRAME_INVALID") from error
    if not isinstance(value, dict):
        raise PluginApiError("PLUGIN_FRAME_INVALID")
    return value


def write_frame(stream: BinaryIO, value: Mapping[str, Any]) -> None:
    detached = json_value(dict(value))
    assert isinstance(detached, dict)
    payload = json.dumps(
        detached,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
    ).encode("utf-8")
    stream.write(struct.pack(">I", len(payload)))
    stream.write(payload)
    stream.flush()


def _read_exact(stream: BinaryIO, size: int) -> bytes:
    chunks: list[bytes] = []
    remaining = size
    while remaining:
        chunk = stream.read(remaining)
        if not chunk:
            raise EOFError
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


class _Pending:
    def __init__(self) -> None:
        self.done = threading.Event()
        self.result: object = None
        self.error: PluginApiError | None = None


class RpcPeer:
    """Small symmetric request/response peer for one private stdio channel."""

    def __init__(
        self,
        input_stream: BinaryIO,
        output_stream: BinaryIO,
        *,
        generation_id: str,
        plugin_id: str,
        request_handler: Callable[[str, Mapping[str, Any]], object],
        on_eof: Callable[[], None] | None = None,
    ) -> None:
        self._input = input_stream
        self._output = output_stream
        self._generation_id = generation_id
        self._plugin_id = plugin_id
        self._request_handler = request_handler
        self._on_eof = on_eof
        self._state_lock = threading.Lock()
        self._pending: dict[str, _Pending] = {}
        self._outgoing_slots = threading.BoundedSemaphore(MAX_PENDING_REQUESTS)
        self._incoming_slots = threading.BoundedSemaphore(MAX_PENDING_REQUESTS)
        self._outgoing: queue.Queue[Mapping[str, Any] | None] = queue.Queue(
            maxsize=MAX_PENDING_REQUESTS * 2
        )
        self._closed = threading.Event()
        self._reader: threading.Thread | None = None
        self._writer: threading.Thread | None = None

    @property
    def closed(self) -> bool:
        return self._closed.is_set()

    def start(self, *, thread_name: str) -> None:
        if self._reader is not None:
            return
        self._writer = threading.Thread(
            target=self._write_loop,
            name=f"{thread_name}-writer",
            daemon=True,
        )
        self._writer.start()
        self._reader = threading.Thread(
            target=self._read_loop,
            name=thread_name,
            daemon=True,
        )
        self._reader.start()

    def request(
        self,
        name: str,
        payload: Mapping[str, Any],
        *,
        timeout: float = DEFAULT_CALL_TIMEOUT_SECONDS,
    ) -> object:
        if self.closed:
            raise PluginApiError("PLUGIN_PROCESS_UNAVAILABLE")
        if not self._outgoing_slots.acquire(blocking=False):
            raise PluginApiError("PLUGIN_QUEUE_FULL")
        deadline = time.monotonic() + max(0.01, float(timeout))
        request_id = uuid.uuid4().hex
        pending = _Pending()
        try:
            with self._state_lock:
                if self.closed:
                    raise PluginApiError("PLUGIN_PROCESS_UNAVAILABLE")
                self._pending[request_id] = pending
            self._write(
                {
                    "type": "request",
                    "id": request_id,
                    "generationId": self._generation_id,
                    "pluginId": self._plugin_id,
                    "name": name,
                    "payload": dict(payload),
                },
                timeout=max(0.0, deadline - time.monotonic()),
            )
            if not pending.done.wait(max(0.0, deadline - time.monotonic())):
                raise PluginApiError("PLUGIN_CALL_TIMEOUT")
            if pending.error is not None:
                raise pending.error
            return pending.result
        finally:
            with self._state_lock:
                self._pending.pop(request_id, None)
            self._outgoing_slots.release()

    def respond(
        self,
        request_id: str,
        *,
        result: object = None,
        error: PluginApiError | None = None,
    ) -> None:
        value: dict[str, Any] = {
            "type": "response",
            "id": request_id,
            "generationId": self._generation_id,
            "pluginId": self._plugin_id,
            "ok": error is None,
        }
        if error is None:
            value["result"] = result
        else:
            value["error"] = {
                "code": error.code,
                "message": str(error),
                "pluginId": error.plugin_id,
                "serviceKey": error.service_key,
            }
        self._write(value)

    def close(self, code: str = "PLUGIN_PROCESS_UNAVAILABLE") -> None:
        if self._closed.is_set():
            return
        self._closed.set()
        try:
            self._outgoing.put_nowait(None)
        except queue.Full:
            pass
        with self._state_lock:
            pending = list(self._pending.values())
        for item in pending:
            item.error = PluginApiError(code)
            item.done.set()

    def wait(self, timeout: float | None = None) -> bool:
        return self._closed.wait(timeout)

    def _write(self, value: Mapping[str, Any], *, timeout: float = 0.0) -> None:
        if self.closed:
            raise PluginApiError("PLUGIN_PROCESS_UNAVAILABLE")
        try:
            self._outgoing.put(dict(value), timeout=max(0.0, timeout))
        except queue.Full as error:
            raise PluginApiError("PLUGIN_QUEUE_FULL") from error

    def _write_loop(self) -> None:
        try:
            while not self.closed:
                value = self._outgoing.get()
                if value is None:
                    return
                write_frame(self._output, value)
        except (BrokenPipeError, OSError, ValueError, PluginApiError):
            self.close()

    def _read_loop(self) -> None:
        try:
            while not self.closed:
                message = read_frame(self._input)
                self._accept(message)
        except (EOFError, OSError, PluginApiError):
            pass
        finally:
            self.close("PLUGIN_PROCESS_EOF")
            if self._on_eof is not None:
                self._on_eof()

    def _accept(self, message: Mapping[str, Any]) -> None:
        if (
            message.get("generationId") != self._generation_id
            or message.get("pluginId") != self._plugin_id
        ):
            raise PluginApiError("GENERATION_INVALIDATED")
        message_type = message.get("type")
        request_id = message.get("id")
        if not isinstance(request_id, str) or not request_id:
            raise PluginApiError("PLUGIN_PROTOCOL_INVALID")
        if message_type == "response":
            with self._state_lock:
                pending = self._pending.get(request_id)
            if pending is None:
                return
            if message.get("ok") is True:
                pending.result = json_value(message.get("result"))
            else:
                raw = message.get("error")
                if not isinstance(raw, Mapping):
                    pending.error = PluginApiError("PLUGIN_PROTOCOL_INVALID")
                else:
                    pending.error = PluginApiError(
                        str(raw.get("code") or "PLUGIN_CALL_FAILED"),
                        str(raw.get("message") or raw.get("code") or "PLUGIN_CALL_FAILED"),
                        plugin_id=str(raw.get("pluginId") or ""),
                        service_key=str(raw.get("serviceKey") or ""),
                    )
            pending.done.set()
            return
        if message_type != "request":
            raise PluginApiError("PLUGIN_PROTOCOL_INVALID")
        name = message.get("name")
        payload = message.get("payload")
        if not isinstance(name, str) or not isinstance(payload, Mapping):
            raise PluginApiError("PLUGIN_PROTOCOL_INVALID")
        if not self._incoming_slots.acquire(blocking=False):
            self.respond(request_id, error=PluginApiError("PLUGIN_QUEUE_FULL"))
            return
        threading.Thread(
            target=self._dispatch_request,
            args=(request_id, name, dict(payload)),
            name=f"sakura-plugin-rpc-{self._plugin_id}",
            daemon=True,
        ).start()

    def _dispatch_request(
        self,
        request_id: str,
        name: str,
        payload: Mapping[str, Any],
    ) -> None:
        try:
            result = self._request_handler(name, payload)
            result = json_value(result)
        except PluginApiError as error:
            try:
                self.respond(request_id, error=error)
            except PluginApiError:
                pass
        except Exception as error:  # plugin exception becomes a stable remote failure
            try:
                self.respond(
                    request_id,
                    error=PluginApiError(
                        "PLUGIN_CALL_FAILED",
                        type(error).__name__,
                        plugin_id=self._plugin_id,
                    ),
                )
            except PluginApiError:
                pass
        else:
            try:
                self.respond(request_id, result=result)
            except PluginApiError:
                pass
        finally:
            self._incoming_slots.release()


class ServiceProxy:
    def __init__(self, service_key: str, call: Callable[[str, str, Sequence[Any]], object]) -> None:
        self._service_key = service_key
        self._call = call

    def __getattr__(self, method: str) -> Callable[..., object]:
        if not method or method.startswith("_"):
            raise AttributeError(method)

        def invoke(*args: object) -> object:
            return self._call(self._service_key, method, args)

        return invoke


class _LocalServiceProxy:
    def __init__(self, service_key: str, service: object, exports: frozenset[str]) -> None:
        self._service_key = service_key
        self._service = service
        self._exports = exports

    def __getattr__(self, method: str) -> Callable[..., object]:
        if method not in self._exports:
            raise PluginApiError(
                "SERVICE_METHOD_NOT_EXPORTED",
                service_key=self._service_key,
            )
        callback = getattr(self._service, method, None)
        if not callable(callback):
            raise PluginApiError(
                "SERVICE_METHOD_NOT_EXPORTED",
                service_key=self._service_key,
            )
        return callback


class _StagedEffect:
    def __init__(self, activate: Callable[[], Callable[[], object]]) -> None:
        self._activate = activate
        self._cleanup: Callable[[], object] | None = None
        self._disposed = False

    def commit(self) -> None:
        if self._disposed or self._cleanup is not None:
            return
        cleanup = self._activate()
        if not callable(cleanup):
            raise PluginApiError("EFFECT_INVALID")
        self._cleanup = cleanup

    def dispose(self) -> None:
        if self._disposed:
            return
        self._disposed = True
        cleanup = self._cleanup
        self._cleanup = None
        if cleanup is not None:
            cleanup()


class PluginConfig:
    """Plugin-owned JSON config with explicit on-change results."""

    def __init__(
        self,
        plugin_id: str,
        plugin_root: Path,
        data_dir: Path,
        effect: Callable[[Callable[[], object]], Callable[[], None]],
    ) -> None:
        self._plugin_id = plugin_id
        self._plugin_root = plugin_root
        self._data_dir = data_dir
        self._effect = effect
        self._handlers: list[Callable[[dict[str, Any]], str]] = []

    def get(self) -> dict[str, Any]:
        merged = self._read(self._plugin_root / "config.json")
        merged.update(self._read(self._data_dir / "config.json"))
        return merged

    def update(self, values: Mapping[str, Any]) -> str:
        detached = json_value(dict(values)) if isinstance(values, Mapping) else None
        if not isinstance(detached, dict):
            raise PluginApiError("CONFIG_VALUE_INVALID", plugin_id=self._plugin_id)
        overrides = self._read(self._data_dir / "config.json")
        overrides.update(detached)
        return self._write(overrides)

    def replace(self, values: Mapping[str, Any]) -> str:
        detached = json_value(dict(values)) if isinstance(values, Mapping) else None
        if not isinstance(detached, dict):
            raise PluginApiError("CONFIG_VALUE_INVALID", plugin_id=self._plugin_id)
        return self._write(detached)

    def on_change(
        self,
        handler: Callable[[dict[str, Any]], str],
    ) -> Callable[[], None]:
        if not callable(handler):
            raise PluginApiError("CONFIG_HANDLER_INVALID", plugin_id=self._plugin_id)
        self._handlers.append(handler)

        def remove() -> None:
            self._handlers[:] = [item for item in self._handlers if item is not handler]

        return self._effect(remove)

    def _read(self, path: Path) -> dict[str, Any]:
        if not path.is_file():
            return {}
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
            raise PluginApiError("PLUGIN_CONFIG_INVALID", plugin_id=self._plugin_id) from error
        if not isinstance(value, dict):
            raise PluginApiError("PLUGIN_CONFIG_INVALID", plugin_id=self._plugin_id)
        return value

    def _write(self, overrides: Mapping[str, Any]) -> str:
        self._data_dir.mkdir(parents=True, exist_ok=True)
        target = self._data_dir / "config.json"
        temporary = self._data_dir / f".config-{uuid.uuid4().hex}.tmp"
        try:
            temporary.write_text(
                json.dumps(dict(overrides), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            os.replace(temporary, target)
        finally:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass
        effective = self.get()
        if not self._handlers:
            return "restart_required"
        results: list[str] = []
        for handler in list(self._handlers):
            try:
                result = handler(dict(effective))
            except Exception:
                result = "error"
            results.append(
                result if result in {"applied", "restart_required", "error"} else "error"
            )
        if "error" in results:
            return "error"
        if "restart_required" in results:
            return "restart_required"
        return "applied"


class _HostRegistrationProxy:
    def __init__(
        self,
        context: "PluginContext",
        service_key: str,
        callback_shape: str,
    ) -> None:
        self._context = context
        self._service_key = service_key
        self._callback_shape = callback_shape

    def register(
        self,
        descriptor: Mapping[str, Any],
        callback: Callable[..., object],
    ) -> Callable[[], None]:
        if not isinstance(descriptor, Mapping) or not callable(callback):
            raise PluginApiError(
                "HOST_DESCRIPTOR_INVALID",
                plugin_id=self._context.plugin_id,
            )
        handle, dispose_callback = self._context._register_callback(
            self._callback_shape,
            callback,
        )

        def activate() -> Callable[[], object]:
            result = self._context._remote_call(
                self._service_key,
                "register",
                [dict(descriptor), handle],
            )
            registration_id = result.get("registrationId") if isinstance(result, Mapping) else None
            if not isinstance(registration_id, str) or not registration_id:
                raise PluginApiError(
                    "HOST_REGISTRATION_INVALID",
                    plugin_id=self._context.plugin_id,
                )

            def cleanup() -> object:
                return self._context._remote_call(
                    self._service_key,
                    "unregister",
                    [registration_id],
                )

            return cleanup

        try:
            return self._context._stage(activate)
        except Exception:
            dispose_callback()
            raise


class _CharacterProxy:
    def __init__(self, context: "PluginContext") -> None:
        self._context = context

    def current(self) -> dict[str, str]:
        result = self._context._remote_call(
            "sakura.host.character",
            "current",
            [self._context.plugin_id],
        )
        if (
            not isinstance(result, Mapping)
            or set(result) != {"id", "systemPrompt"}
            or not isinstance(result.get("id"), str)
            or not result.get("id")
            or not isinstance(result.get("systemPrompt"), str)
            or not result.get("systemPrompt")
        ):
            raise PluginApiError(
                "CHARACTER_RESPONSE_INVALID",
                plugin_id=self._context.plugin_id,
            )
        return {"id": result["id"], "systemPrompt": result["systemPrompt"]}

    def get(self, character_id: str) -> dict[str, Any]:
        result = self._context._remote_call(
            "sakura.host.character",
            "get",
            [self._context.plugin_id, character_id],
        )
        if not isinstance(result, Mapping):
            raise PluginApiError(
                "CHARACTER_EXTENSION_INVALID",
                plugin_id=self._context.plugin_id,
            )
        return dict(result)

    def update(self, character_id: str, values: Mapping[str, Any]) -> dict[str, Any]:
        if not isinstance(values, Mapping):
            raise PluginApiError(
                "CHARACTER_EXTENSION_INVALID",
                plugin_id=self._context.plugin_id,
            )
        result = self._context._remote_call(
            "sakura.host.character",
            "update",
            [self._context.plugin_id, character_id, dict(values)],
        )
        if not isinstance(result, Mapping):
            raise PluginApiError(
                "CHARACTER_EXTENSION_INVALID",
                plugin_id=self._context.plugin_id,
            )
        return dict(result)

    def resolve_resource(self, character_id: str, relative_path: str) -> str:
        result = self._context._remote_call(
            "sakura.host.character",
            "resolve_resource",
            [self._context.plugin_id, character_id, relative_path],
        )
        if not isinstance(result, str) or not result:
            raise PluginApiError(
                "CHARACTER_RESOURCE_INVALID",
                plugin_id=self._context.plugin_id,
            )
        return result


class _ArtifactsProxy:
    def __init__(self, context: "PluginContext") -> None:
        self._context = context
        self._allocations: dict[str, tuple[Callable[[], None], dict[str, bool]]] = {}

    def allocate(self, descriptor: Mapping[str, Any]) -> dict[str, Any]:
        if not isinstance(descriptor, Mapping):
            raise PluginApiError(
                "ARTIFACT_DESCRIPTOR_INVALID",
                plugin_id=self._context.plugin_id,
            )
        result = self._context._remote_call(
            "sakura.host.artifacts",
            "allocate",
            [self._context.plugin_id, dict(descriptor)],
        )
        artifact_id = result.get("artifactId") if isinstance(result, Mapping) else None
        path = result.get("path") if isinstance(result, Mapping) else None
        if not isinstance(artifact_id, str) or not isinstance(path, str) or not path:
            raise PluginApiError(
                "ARTIFACT_DESCRIPTOR_INVALID",
                plugin_id=self._context.plugin_id,
            )
        ownership = {"transferred": False}

        def cleanup() -> None:
            self._allocations.pop(artifact_id, None)
            if ownership["transferred"]:
                return
            self._context._remote_call(
                "sakura.host.artifacts",
                "release",
                [self._context.plugin_id, artifact_id],
            )

        disposer = self._context.effect(cleanup)
        self._allocations[artifact_id] = (disposer, ownership)
        return dict(result)

    def commit(self, artifact_id: str) -> dict[str, Any]:
        binding = self._allocations.get(artifact_id)
        if binding is None:
            raise PluginApiError("ARTIFACT_NOT_FOUND", plugin_id=self._context.plugin_id)
        result = self._context._remote_call(
            "sakura.host.artifacts",
            "commit",
            [self._context.plugin_id, artifact_id],
        )
        if not isinstance(result, Mapping):
            raise PluginApiError(
                "ARTIFACT_DESCRIPTOR_INVALID",
                plugin_id=self._context.plugin_id,
            )
        disposer, ownership = binding
        ownership["transferred"] = True
        disposer()
        return dict(result)

    def release(self, artifact_id: str) -> bool:
        binding = self._allocations.pop(artifact_id, None)
        if binding is None:
            return False
        binding[0]()
        return True


class _StorageProxy:
    def __init__(self, context: "PluginContext") -> None:
        self._context = context

    def resolve(self, scope: str, name: str) -> Path:
        result = self._context._remote_call(
            "sakura.host.storage",
            "resolve",
            [
                self._context.plugin_id,
                {"scope": scope, "name": name},
            ],
        )
        if (
            not isinstance(result, Mapping)
            or result.get("scope") != scope
            or result.get("name") != name
            or not isinstance(result.get("path"), str)
            or not result.get("path")
        ):
            raise PluginApiError(
                "STORAGE_DESCRIPTOR_INVALID",
                plugin_id=self._context.plugin_id,
            )
        return Path(result["path"])


class _DiagnosticsProxy:
    """Submit bounded diagnostics through the Core-owned Runtime log bridge."""

    def __init__(self, context: "PluginContext") -> None:
        self._context = context

    def emit(self, descriptor: Mapping[str, Any]) -> bool:
        if not isinstance(descriptor, Mapping):
            raise PluginApiError(
                "DIAGNOSTIC_DESCRIPTOR_INVALID",
                plugin_id=self._context.plugin_id,
            )
        result = self._context._remote_call(
            "sakura.host.diagnostics",
            "emit",
            [self._context.plugin_id, dict(descriptor)],
        )
        if not isinstance(result, Mapping) or set(result) != {"accepted"}:
            raise PluginApiError(
                "DIAGNOSTIC_RESULT_INVALID",
                plugin_id=self._context.plugin_id,
            )
        return bool(result["accepted"])


class _SettingsRegistrationProxy:
    def __init__(self, context: "PluginContext") -> None:
        self._context = context

    def register(
        self,
        descriptor: Mapping[str, Any],
        *,
        load: Callable[[], object] | None = None,
        save: Callable[[Mapping[str, Any]], object] | None = None,
        actions: Mapping[str, Callable[[Mapping[str, Any]], object]] | None = None,
    ) -> Callable[[], None]:
        if not isinstance(descriptor, Mapping):
            raise PluginApiError(
                "HOST_DESCRIPTOR_INVALID",
                plugin_id=self._context.plugin_id,
            )
        if load is not None and not callable(load):
            raise PluginApiError(
                "SETTINGS_CALLBACK_INVALID",
                plugin_id=self._context.plugin_id,
            )
        if save is not None and not callable(save):
            raise PluginApiError(
                "SETTINGS_CALLBACK_INVALID",
                plugin_id=self._context.plugin_id,
            )
        action_callbacks = dict(actions or {})
        if any(
            not isinstance(action_id, str) or not callable(callback)
            for action_id, callback in action_callbacks.items()
        ):
            raise PluginApiError(
                "SETTINGS_CALLBACK_INVALID",
                plugin_id=self._context.plugin_id,
            )
        callback_disposers: list[Callable[[], None]] = []

        def bind(shape: str, callback: Callable[..., object] | None) -> str | None:
            if callback is None:
                return None
            handle, disposer = self._context._register_callback(shape, callback)
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

        def activate() -> Callable[[], object]:
            result = self._context._remote_call(
                "sakura.host.settings",
                "register",
                [self._context.plugin_id, dict(descriptor), handles],
            )
            registration_id = (
                result.get("registrationId") if isinstance(result, Mapping) else None
            )
            if not isinstance(registration_id, str) or not registration_id:
                raise PluginApiError(
                    "HOST_REGISTRATION_INVALID",
                    plugin_id=self._context.plugin_id,
                )

            def cleanup() -> object:
                return self._context._remote_call(
                    "sakura.host.settings",
                    "unregister",
                    [registration_id],
                )

            return cleanup

        try:
            return self._context._stage(activate)
        except Exception:
            for disposer in reversed(callback_disposers):
                disposer()
            raise


class _SettingsSurfaceProxy:
    def __init__(self, context: "PluginContext") -> None:
        self._context = context

    def register(self, section_id: str, surface: str) -> Callable[[], None]:
        def activate() -> Callable[[], object]:
            result = self._context._remote_call(
                "sakura.host.settings.surface-v0",
                "register",
                [self._context.plugin_id, section_id, surface],
            )
            registration_id = (
                result.get("registrationId") if isinstance(result, Mapping) else None
            )
            if not isinstance(registration_id, str) or not registration_id:
                raise PluginApiError(
                    "HOST_REGISTRATION_INVALID",
                    plugin_id=self._context.plugin_id,
                )

            def cleanup() -> object:
                return self._context._remote_call(
                    "sakura.host.settings.surface-v0",
                    "unregister",
                    [registration_id],
                )

            return cleanup

        return self._context._stage(activate)


class _SettingsCollectionProxy:
    def __init__(self, context: "PluginContext") -> None:
        self._context = context

    def register(
        self,
        section_id: str,
        descriptor: Mapping[str, Any],
        *,
        query: Callable[..., object],
        create: Callable[..., object] | None = None,
        update: Callable[..., object] | None = None,
        delete: Callable[..., object] | None = None,
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
            or any(
                callback is not None and not callable(callback)
                for callback in callbacks.values()
            )
        ):
            raise PluginApiError(
                "SETTINGS_CALLBACK_INVALID",
                plugin_id=self._context.plugin_id,
            )
        callback_disposers: list[Callable[[], None]] = []
        handles: dict[str, str | None] = {}
        for operation, callback in callbacks.items():
            if callback is None:
                handles[operation] = None
                continue
            handle, disposer = self._context._register_callback(
                f"settings.collection.{operation}",
                callback,
            )
            callback_disposers.append(disposer)
            handles[operation] = handle

        def activate() -> Callable[[], object]:
            result = self._context._remote_call(
                "sakura.host.settings.collection-v0",
                "register",
                [self._context.plugin_id, section_id, dict(descriptor), handles],
            )
            registration_id = (
                result.get("registrationId") if isinstance(result, Mapping) else None
            )
            if not isinstance(registration_id, str) or not registration_id:
                raise PluginApiError(
                    "HOST_REGISTRATION_INVALID",
                    plugin_id=self._context.plugin_id,
                )

            def cleanup() -> object:
                return self._context._remote_call(
                    "sakura.host.settings.collection-v0",
                    "unregister",
                    [registration_id],
                )

            return cleanup

        try:
            return self._context._stage(activate)
        except Exception:
            for disposer in reversed(callback_disposers):
                disposer()
            raise


class _ModelSlotsProxy:
    def __init__(self, context: "PluginContext") -> None:
        self._context = context

    def catalog(self) -> list[dict[str, object]]:
        result = self._context._remote_call(
            "sakura.host.model_slots",
            "catalog",
            [],
        )
        detached = json_value(result)
        if not isinstance(detached, list) or any(
            not isinstance(item, dict) for item in detached
        ):
            raise PluginApiError(
                "MODEL_CATALOG_INVALID",
                plugin_id=self._context.plugin_id,
            )
        return detached

    def resolve(self, selection: Mapping[str, Any]) -> dict[str, object]:
        if not isinstance(selection, Mapping):
            raise PluginApiError(
                "MODEL_SLOT_SELECTION_INVALID",
                plugin_id=self._context.plugin_id,
            )
        result = self._context._remote_call(
            "sakura.host.model_slots",
            "resolve",
            [dict(selection)],
        )
        detached = json_value(result)
        if not isinstance(detached, dict):
            raise PluginApiError(
                "MODEL_CATALOG_INVALID",
                plugin_id=self._context.plugin_id,
            )
        return detached

    def register(
        self,
        descriptor: Mapping[str, Any],
        *,
        load: Callable[[], object],
        save: Callable[[Mapping[str, Any]], object],
    ) -> Callable[[], None]:
        if not isinstance(descriptor, Mapping) or not callable(load) or not callable(save):
            raise PluginApiError(
                "MODEL_SLOT_REGISTRATION_INVALID",
                plugin_id=self._context.plugin_id,
            )
        callback_disposers: list[Callable[[], None]] = []

        def bind(shape: str, callback: Callable[..., object]) -> str:
            handle, disposer = self._context._register_callback(shape, callback)
            callback_disposers.append(disposer)
            return handle

        handles = {
            "load": bind("model_slots.load", load),
            "save": bind("model_slots.save", save),
        }

        def activate() -> Callable[[], object]:
            result = self._context._remote_call(
                "sakura.host.model_slots",
                "register",
                [self._context.plugin_id, dict(descriptor), handles],
            )
            registration_id = (
                result.get("registrationId") if isinstance(result, Mapping) else None
            )
            if not isinstance(registration_id, str) or not registration_id:
                raise PluginApiError(
                    "HOST_REGISTRATION_INVALID",
                    plugin_id=self._context.plugin_id,
                )

            def cleanup() -> object:
                return self._context._remote_call(
                    "sakura.host.model_slots",
                    "unregister",
                    [registration_id],
                )

            return cleanup

        try:
            return self._context._stage(activate)
        except Exception:
            for disposer in reversed(callback_disposers):
                disposer()
            raise


class PluginContext:
    """The complete public object passed to one Plugin API v4 entry."""

    def __init__(
        self,
        plugin_id: str,
        plugin_root: Path,
        data_dir: Path,
        remote_call: Callable[[str, str, Sequence[Any]], object],
        remote_request: Callable[[str, Mapping[str, Any]], object],
    ) -> None:
        self.plugin_id = plugin_id
        self._plugin_root = plugin_root
        self._data_dir = data_dir
        self._remote_call = remote_call
        self._remote_request = remote_request
        self._services: dict[str, tuple[object, frozenset[str]]] = {}
        self._events: dict[str, list[Callable[[object], object]]] = {}
        self._effects: list[Callable[[], object]] = []
        self._staged: list[_StagedEffect] = []
        self._callbacks: dict[str, tuple[str, Callable[..., object]]] = {}
        self._closed = False
        self.config = PluginConfig(plugin_id, plugin_root, data_dir, self.effect)

    def get(self, service_key: str) -> object:
        key = _identifier(service_key, "SERVICE_KEY_INVALID")
        local = self._services.get(key)
        if local is not None:
            return _LocalServiceProxy(service_key, local[0], local[1])
        if key == "sakura.host.character":
            return _CharacterProxy(self)
        if key == "sakura.host.artifacts":
            return _ArtifactsProxy(self)
        if key == "sakura.host.settings":
            return _SettingsRegistrationProxy(self)
        if key == "sakura.host.settings.surface-v0":
            return _SettingsSurfaceProxy(self)
        if key == "sakura.host.settings.collection-v0":
            return _SettingsCollectionProxy(self)
        if key == "sakura.host.model_slots":
            return _ModelSlotsProxy(self)
        if key == "sakura.host.storage":
            return _StorageProxy(self)
        if key == "sakura.host.diagnostics":
            return _DiagnosticsProxy(self)
        callback_shape = {
            "sakura.host.tools": "tools.handler",
            "sakura.host.context": "context.contributor",
        }.get(key)
        if callback_shape is not None:
            return _HostRegistrationProxy(self, key, callback_shape)
        return ServiceProxy(service_key, self._remote_call)

    def provide(
        self,
        service_key: str,
        service: object,
        *,
        exports: Iterable[str] = (),
    ) -> Callable[[], None]:
        key = _identifier(service_key, "SERVICE_KEY_INVALID")
        if key in self._services:
            raise PluginApiError("SERVICE_CONFLICT", plugin_id=self.plugin_id, service_key=key)
        exported = frozenset(_method(item) for item in exports)
        if any(not callable(getattr(service, item, None)) for item in exported):
            raise PluginApiError(
                "SERVICE_EXPORT_INVALID",
                plugin_id=self.plugin_id,
                service_key=key,
            )
        binding = (service, exported)
        self._services[key] = binding

        def remove() -> None:
            if self._services.get(key) is binding:
                del self._services[key]

        self.effect(remove)
        return remove

    def on(self, name: str, handler: Callable[[object], object]) -> Callable[[], None]:
        event_name = _identifier(name, "EVENT_NAME_INVALID")
        if not callable(handler):
            raise PluginApiError("EVENT_HANDLER_INVALID", plugin_id=self.plugin_id)
        self._events.setdefault(event_name, []).append(handler)

        def remove() -> None:
            handlers = self._events.get(event_name)
            if handlers is None:
                return
            self._events[event_name] = [item for item in handlers if item is not handler]
            if not self._events[event_name]:
                del self._events[event_name]

        self.effect(remove)
        return remove

    def effect(self, cleanup: Callable[[], object]) -> Callable[[], None]:
        if self._closed or not callable(cleanup):
            raise PluginApiError("EFFECT_INVALID", plugin_id=self.plugin_id)
        active = True
        self._effects.append(cleanup)

        def dispose() -> None:
            nonlocal active
            if not active:
                return
            active = False
            try:
                self._effects.remove(cleanup)
            except ValueError:
                pass
            cleanup()

        return dispose

    def _stage(
        self,
        activate: Callable[[], Callable[[], object]],
    ) -> Callable[[], None]:
        if self._closed or not callable(activate):
            raise PluginApiError("EFFECT_INVALID", plugin_id=self.plugin_id)
        staged = _StagedEffect(activate)
        self._staged.append(staged)
        return self.effect(staged.dispose)

    def commit(self) -> None:
        for staged in self._staged:
            staged.commit()

    def _register_callback(
        self,
        shape: str,
        callback: Callable[..., object],
    ) -> tuple[str, Callable[[], None]]:
        result = self._remote_request("callback.register", {"shape": shape})
        handle = result.get("handle") if isinstance(result, Mapping) else None
        if not isinstance(handle, str) or not handle:
            raise PluginApiError("CALLBACK_INVALID", plugin_id=self.plugin_id)
        self._callbacks[handle] = (shape, callback)

        def remove() -> None:
            binding = self._callbacks.pop(handle, None)
            if binding is None:
                return
            try:
                self._remote_request("callback.unregister", {"handle": handle})
            except PluginApiError:
                pass

        return handle, self.effect(remove)

    def invoke_callback(self, handle: str, shape: str, args: Sequence[Any]) -> object:
        binding = self._callbacks.get(handle)
        if binding is None:
            raise PluginApiError("CALLBACK_INVALID", plugin_id=self.plugin_id)
        expected_shape, callback = binding
        if expected_shape != shape:
            raise PluginApiError("CALLBACK_SHAPE_INVALID", plugin_id=self.plugin_id)
        return callback(*args)

    def data_path(self, relative_path: str) -> Path:
        if not isinstance(relative_path, str) or not relative_path.strip():
            raise PluginApiError("PLUGIN_DATA_PATH_INVALID", plugin_id=self.plugin_id)
        relative = Path(relative_path.strip())
        if relative.is_absolute() or relative.drive or ".." in relative.parts:
            raise PluginApiError("PLUGIN_DATA_PATH_INVALID", plugin_id=self.plugin_id)
        root = self._data_dir.resolve(strict=False)
        target = (root / relative).resolve(strict=False)
        try:
            target.relative_to(root)
        except ValueError as error:
            raise PluginApiError("PLUGIN_DATA_PATH_INVALID", plugin_id=self.plugin_id) from error
        return target

    def service_exports(self) -> dict[str, list[str]]:
        return {
            key: sorted(exports)
            for key, (_service, exports) in self._services.items()
        }

    def call_local(self, service_key: str, method: str, args: Sequence[Any]) -> object:
        binding = self._services.get(service_key)
        if binding is None:
            raise PluginApiError("SERVICE_MISSING", service_key=service_key)
        service, exports = binding
        if method not in exports:
            raise PluginApiError(
                "SERVICE_METHOD_NOT_EXPORTED",
                plugin_id=self.plugin_id,
                service_key=service_key,
            )
        return getattr(service, method)(*args)

    def emit(self, name: str, payload: object) -> None:
        for handler in list(self._events.get(name, ())):
            try:
                handler(payload)
            except Exception:
                continue

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        while self._effects:
            cleanup = self._effects.pop()
            try:
                cleanup()
            except Exception:
                pass
        self._services.clear()
        self._events.clear()
        self._callbacks.clear()


def _identifier(value: object, code: str) -> str:
    if not isinstance(value, str) or not value or len(value) > 200:
        raise PluginApiError(code)
    if not value[0].isalnum() or any(not (char.isalnum() or char in "_.-") for char in value):
        raise PluginApiError(code)
    return value


def _method(value: object) -> str:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > 128
        or not (value[0].isalpha() or value[0] == "_")
        or any(not (char.isalnum() or char == "_") for char in value)
    ):
        raise PluginApiError("SERVICE_EXPORT_INVALID")
    return value


__all__ = [
    "DEFAULT_CALL_TIMEOUT_SECONDS",
    "MAX_FRAME_BYTES",
    "PluginApiError",
    "PluginContext",
    "RpcPeer",
    "ServiceProxy",
    "json_value",
    "read_frame",
    "write_frame",
]
