"""Private subprocess runtime that is the only Runtime v2 plugin importer."""

from __future__ import annotations

import argparse
import json
import secrets
import sys
import threading
from collections import deque
from pathlib import Path
from typing import Any, BinaryIO, Callable, Mapping, Sequence

from app.plugins.discovery import PluginDiscovery
from app.plugins.inventory import RuntimePluginSpec
from app.plugins.kernel import PluginKernelError, PluginKernelManager
from app.storage.runtime_roots import RuntimeRoots, coerce_runtime_roots

from .plugin_worker import _read_private_frame, _write_private_frame
from .runtime_logging import (
    CORE_BRIDGE_PREFIX,
    RuntimeLoggingBridge,
    install_runtime_logging,
)


_ALLOWED_EVENTS = frozenset({
    "app.start", "message.user", "message.ai", "tool.started", "tool.finished", "tool.failed",
})


class WorkerRuntimeError(ValueError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class _WorkerBridge:
    """Worker side of the framed channel, including nested Host calls."""

    def __init__(
        self,
        input_stream: BinaryIO,
        output_stream: BinaryIO,
        generation_id: str,
        token: str,
    ) -> None:
        self._input = input_stream
        self._output = output_stream
        self._generation_id = generation_id
        self._token = token
        self._queued_requests: deque[dict[str, Any]] = deque()
        self._owner_thread_id = threading.get_ident()
        self._output_lock = threading.Lock()

    def read_request(self) -> dict[str, Any] | None:
        if self._queued_requests:
            return self._queued_requests.popleft()
        return _read_private_frame(self._input)

    def write_response(self, response: Mapping[str, Any]) -> None:
        self._write_message(response)

    def write_runtime_log(self, payload: Mapping[str, Any]) -> None:
        self._write_message(
            {
                "kind": "runtime.log",
                "generationId": self._generation_id,
                "token": self._token,
                "payload": dict(payload),
            }
        )

    def _write_message(self, message: Mapping[str, Any]) -> None:
        with self._output_lock:
            _write_private_frame(self._output, message)

    def host_call(self, service_key: str, method: str, args: Sequence[Any]) -> object:
        if threading.get_ident() != self._owner_thread_id:
            raise PluginKernelError("HOST_CALL_THREAD_INVALID")
        if not _json_value(list(args)):
            raise PluginKernelError("HOST_ARGUMENTS_INVALID")
        request_id = secrets.token_hex(12)
        self.write_response(
            {
                "kind": "host.request",
                "generationId": self._generation_id,
                "token": self._token,
                "id": request_id,
                "name": "host.call",
                "payload": {
                    "serviceKey": service_key,
                    "method": method,
                    "args": list(args),
                },
            }
        )
        while True:
            frame = _read_private_frame(self._input)
            if frame is None:
                raise PluginKernelError("HOST_BRIDGE_UNAVAILABLE")
            if frame.get("kind") != "host.response":
                if frame.get("kind") is None:
                    self._queued_requests.append(frame)
                    continue
                raise PluginKernelError("HOST_PROTOCOL_INVALID")
            valid = (
                frame.get("generationId") == self._generation_id
                and frame.get("token") == self._token
                and frame.get("id") == request_id
                and isinstance(frame.get("ok"), bool)
            )
            if not valid:
                raise PluginKernelError("HOST_PROTOCOL_INVALID")
            if frame["ok"]:
                return frame.get("payload")
            error = frame.get("error") if isinstance(frame.get("error"), Mapping) else {}
            code = error.get("code")
            raise PluginKernelError(
                code if isinstance(code, str) and code else "HOST_CALL_FAILED"
            )


class _WorkerRuntimeLogStream:
    """Turn bounded Core bridge lines into private worker diagnostic frames."""

    def __init__(self, bridge: _WorkerBridge) -> None:
        self._bridge = bridge

    def write(self, data: bytes) -> int:
        if not isinstance(data, bytes) or not data.startswith(CORE_BRIDGE_PREFIX):
            raise OSError("invalid worker runtime log record")
        raw = data[len(CORE_BRIDGE_PREFIX) :].strip()
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError) as error:
            raise OSError("invalid worker runtime log payload") from error
        if not isinstance(payload, Mapping):
            raise OSError("worker runtime log payload must be an object")
        self._bridge.write_runtime_log(payload)
        return len(data)

    def flush(self) -> None:
        return None


class PluginWorkerRuntime:
    def __init__(
        self,
        roots: RuntimeRoots | Path,
        generation_id: str,
        *,
        host_call: Callable[[str, str, Sequence[Any]], Any] | None = None,
    ) -> None:
        self._roots = coerce_runtime_roots(roots)
        self._user_root = self._roots.user_root
        self._generation_id = generation_id
        self._host_call = host_call
        self._kernel: PluginKernelManager | None = None
        self._initialized = False
        self._closed = False
        self._snapshot: dict[str, Any] | None = None

    def handle(self, name: str, payload: Mapping[str, Any]) -> object:
        if self._closed and name != "worker.close":
            raise WorkerRuntimeError("GENERATION_INVALIDATED")
        if name == "worker.initialize":
            raw_host_services = payload.get("hostServices", [])
            raw_runtime_plugins = payload.get("runtimePlugins", [])
            if (
                not isinstance(raw_host_services, list)
                or len(raw_host_services) > 16
                or any(
                    not isinstance(item, str) or not item.startswith("sakura.host.")
                    for item in raw_host_services
                )
            ):
                raise WorkerRuntimeError("HOST_SERVICES_INVALID")
            if not isinstance(raw_runtime_plugins, list) or len(raw_runtime_plugins) > 64:
                raise WorkerRuntimeError("PLUGIN_RUNTIME_SPEC_INVALID")
            try:
                runtime_specs = tuple(
                    RuntimePluginSpec.from_private_dict(item)
                    for item in raw_runtime_plugins
                    if isinstance(item, Mapping)
                )
            except ValueError as error:
                raise WorkerRuntimeError("PLUGIN_RUNTIME_SPEC_INVALID") from error
            if len(runtime_specs) != len(raw_runtime_plugins):
                raise WorkerRuntimeError("PLUGIN_RUNTIME_SPEC_INVALID")
            return self.initialize(
                tuple(dict.fromkeys(raw_host_services)),
                runtime_specs=runtime_specs,
            )
        if not self._initialized:
            raise WorkerRuntimeError("PLUGIN_NOT_READY")
        if name == "status.get":
            return self._status_snapshot()
        if name == "service.call":
            kernel = self._require_kernel()
            service_key = _identifier(payload.get("serviceKey"), "SERVICE_KEY_INVALID")
            method = _identifier(payload.get("method"), "SERVICE_METHOD_INVALID")
            args = payload.get("args", [])
            if not isinstance(args, list) or len(args) > 32 or not _json_value(args):
                raise WorkerRuntimeError("SERVICE_ARGUMENTS_INVALID")
            try:
                result = kernel.call_service(service_key, method, args)
            except PluginKernelError as error:
                raise WorkerRuntimeError(error.code) from error
            if not _json_value(result):
                raise WorkerRuntimeError("SERVICE_RESULT_INVALID")
            return result
        if name == "callback.invoke":
            kernel = self._require_kernel()
            handle = _identifier(payload.get("handle"), "CALLBACK_INVALID")
            shape = _identifier(payload.get("shape"), "CALLBACK_SHAPE_INVALID")
            args = payload.get("args", [])
            if not isinstance(args, list) or len(args) > 32 or not _json_value(args):
                raise WorkerRuntimeError("CALLBACK_ARGUMENTS_INVALID")
            try:
                result = kernel.invoke_callback(handle, shape, args)
            except PluginKernelError as error:
                raise WorkerRuntimeError(error.code) from error
            result_limit = (
                256 * 1024
                if shape.startswith("settings.collection.")
                else 64 * 1024
            )
            if not _json_value(result, maximum=result_limit):
                raise WorkerRuntimeError("CALLBACK_RESULT_INVALID")
            return result
        if name == "lifecycle.reconcile":
            raw_runtime_plugins = payload.get("runtimePlugins", [])
            raw_reload_ids = payload.get("reloadPluginIds", [])
            if (
                not isinstance(raw_runtime_plugins, list)
                or len(raw_runtime_plugins) > 64
                or not isinstance(raw_reload_ids, list)
                or len(raw_reload_ids) > 64
            ):
                raise WorkerRuntimeError("PLUGIN_RUNTIME_SPEC_INVALID")
            try:
                runtime_specs = tuple(
                    RuntimePluginSpec.from_private_dict(item)
                    for item in raw_runtime_plugins
                    if isinstance(item, Mapping)
                )
            except ValueError as error:
                raise WorkerRuntimeError("PLUGIN_RUNTIME_SPEC_INVALID") from error
            if len(runtime_specs) != len(raw_runtime_plugins):
                raise WorkerRuntimeError("PLUGIN_RUNTIME_SPEC_INVALID")
            reload_ids = tuple(
                _plugin_identifier(item) for item in raw_reload_ids
            )
            try:
                self._require_kernel().reconcile(
                    [spec.to_plugin_spec(self._roots) for spec in runtime_specs],
                    reload_ids=reload_ids,
                )
            except PluginKernelError as error:
                raise WorkerRuntimeError(error.code) from error
            self._refresh_snapshot()
            return self._status_snapshot()
        if name == "event.emit":
            event_type = _identifier(payload.get("eventType"), "EVENT_INVALID")
            is_host_event = event_type.startswith("sakura.host.")
            if event_type not in _ALLOWED_EVENTS and not is_host_event:
                raise WorkerRuntimeError("EVENT_INVALID")
            event_payload = dict(_object(payload.get("payload"), "EVENT_INVALID"))
            if not _json_value(event_payload):
                raise WorkerRuntimeError("EVENT_INVALID")
            kernel = self._require_kernel()
            try:
                kernel.emit_host_event(
                    event_type if is_host_event else _host_event_name(event_type),
                    event_payload,
                )
            except PluginKernelError as error:
                raise WorkerRuntimeError(error.code) from error
            return {"accepted": True}
        if name == "worker.close":
            self.close()
            return {"closed": True}
        raise WorkerRuntimeError("PLUGIN_COMMAND_UNKNOWN")

    def initialize(
        self,
        host_service_keys: Sequence[str] = (),
        *,
        runtime_specs: Sequence[RuntimePluginSpec] | None = None,
    ) -> dict[str, Any]:
        if self._snapshot is not None:
            return self._snapshot
        discovered = (
            [spec.to_plugin_spec(self._roots) for spec in runtime_specs]
            if runtime_specs is not None
            else PluginDiscovery(self._roots).discover()
        )
        self._kernel = PluginKernelManager(
            self._user_root,
            discovered,
            host_service_keys=host_service_keys,
            host_call=self._host_call,
        )
        self._initialized = True
        self._kernel.emit_host_event(
            "sakura.host.app.started",
            {"generationId": self._generation_id},
        )
        self._refresh_snapshot()
        assert self._snapshot is not None
        return self._snapshot

    def settings_snapshot(self) -> dict[str, Any]:
        return self._status_snapshot()

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._kernel is not None:
            self._kernel.close()
            self._kernel = None

    def _require_kernel(self) -> PluginKernelManager:
        if self._kernel is None:
            raise WorkerRuntimeError("PLUGIN_NOT_READY")
        return self._kernel

    def _refresh_snapshot(self) -> None:
        if self._kernel is None:
            return
        plugins = self._kernel.snapshot()["plugins"]
        degraded = any(item["state"] == "failed" for item in plugins)
        self._snapshot = {
            "schemaVersion": 1,
            "state": "degraded" if degraded else "ready",
            "reasonCode": "PLUGIN_LOAD_PARTIAL" if degraded else "READY",
            "plugins": plugins,
        }

    def _status_snapshot(self) -> dict[str, Any]:
        self._refresh_snapshot()
        assert self._snapshot is not None
        return json.loads(json.dumps(self._snapshot, ensure_ascii=False))


def _host_event_name(event_type: str) -> str:
    return {
        "app.start": "sakura.host.app.started",
        "message.user": "sakura.host.message.received",
        "message.ai": "sakura.host.message.sent",
        "tool.started": "sakura.host.tool.started",
        "tool.finished": "sakura.host.tool.finished",
        "tool.failed": "sakura.host.tool.failed",
    }[event_type]


def _object(value: object, code: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise WorkerRuntimeError(code)
    return value


def _identifier(value: object, code: str) -> str:
    if not isinstance(value, str) or not value or len(value) > 200:
        raise WorkerRuntimeError(code)
    return value


def _plugin_identifier(value: object) -> str:
    value = _identifier(value, "PLUGIN_ID_INVALID")
    if len(value) > 64:
        raise WorkerRuntimeError("PLUGIN_ID_INVALID")
    return value


def _json_value(value: object, *, maximum: int = 64 * 1024) -> bool:
    try:
        encoded = json.dumps(value, ensure_ascii=False)
    except (TypeError, ValueError):
        return False
    return len(encoded.encode("utf-8")) <= maximum


def _run(
    bridge: _WorkerBridge,
    runtime: PluginWorkerRuntime,
    generation_id: str,
    token: str,
) -> None:
    while True:
        request = bridge.read_request()
        if request is None:
            break
        request_id = request.get("id")
        valid = (
            request.get("generationId") == generation_id
            and request.get("token") == token
            and isinstance(request_id, str)
            and isinstance(request.get("name"), str)
            and isinstance(request.get("payload"), Mapping)
        )
        should_close = request.get("name") == "worker.close"
        try:
            if not valid:
                raise WorkerRuntimeError("PLUGIN_PROTOCOL_INVALID")
            payload = runtime.handle(str(request["name"]), request["payload"])
            response = {
                "generationId": generation_id,
                "token": token,
                "id": request_id,
                "ok": True,
                "payload": payload,
            }
        except WorkerRuntimeError as error:
            response = {
                "generationId": generation_id,
                "token": token,
                "id": request_id or "invalid",
                "ok": False,
                "error": {
                    "code": error.code,
                    "retryable": error.code.endswith("TIMEOUT"),
                },
            }
        except Exception as error:
            response = {
                "generationId": generation_id,
                "token": token,
                "id": request_id or "invalid",
                "ok": False,
                "error": {
                    "code": _callback_failure_code(request, error),
                    "retryable": isinstance(error, TimeoutError),
                },
            }
        bridge.write_response(response)
        if should_close:
            break
    runtime.close()


def _callback_failure_code(request: Mapping[str, Any], error: Exception) -> str:
    """Classify callback failures without returning exception text, paths or values."""
    if isinstance(error, TimeoutError):
        return "PLUGIN_CALLBACK_TIMEOUT"
    if isinstance(error, ImportError):
        return "PLUGIN_DEPENDENCY_UNAVAILABLE"
    if isinstance(error, OSError):
        return "PLUGIN_CALLBACK_IO_FAILED"
    if isinstance(error, (TypeError, ValueError, KeyError)):
        return "PLUGIN_CALLBACK_DATA_INVALID"
    return "PLUGIN_CALLBACK_FAILED"


def main() -> int:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--distribution-root", required=True)
    parser.add_argument("--user-root", required=True)
    parser.add_argument("--generation-id", required=True)
    parser.add_argument("--token", required=True)
    args = parser.parse_args()
    # Plugin code may print.  Protocol bytes retain the original stdout handle;
    # all plugin text is redirected away from the private framed channel.
    protocol_output = sys.stdout.buffer
    sys.stdout = sys.stderr
    bridge = _WorkerBridge(
        sys.stdin.buffer,
        protocol_output,
        args.generation_id,
        args.token,
    )
    runtime_logging: RuntimeLoggingBridge | None = None
    try:
        runtime_logging = install_runtime_logging(_WorkerRuntimeLogStream(bridge))
    except Exception:
        # The worker process is marked external-only by its parent, so a
        # Bridge setup failure drops diagnostics without enabling fallback I/O.
        pass
    try:
        runtime = PluginWorkerRuntime(
            RuntimeRoots(
                Path(args.distribution_root).resolve(),
                Path(args.user_root).resolve(),
            ),
            args.generation_id,
            host_call=bridge.host_call,
        )
        _run(bridge, runtime, args.generation_id, args.token)
    finally:
        if runtime_logging is not None:
            runtime_logging.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
