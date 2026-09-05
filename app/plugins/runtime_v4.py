"""Core-owned Plugin Runtime v4 manager and per-plugin process clients."""

from __future__ import annotations

import os
import secrets
import signal
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from app.plugin_sdk.sakura_process import terminate_process_tree
from app.plugins.dependencies import PluginDependencyError, PluginDependencyRoots
from app.plugins.inventory import RuntimePluginSpec
from app.plugins.models import PLUGIN_API_V4_VERSION, PluginSpec
from app.plugins.host_services import HOST_CALLER, HOST_CALLER_LOG_METADATA
from app.plugins.sakura_plugin_sdk import PluginApiError, RpcPeer, json_value
from app.storage.paths import StoragePaths
from app.storage.runtime_roots import RuntimeRoots, coerce_runtime_roots


INITIALIZE_TIMEOUT_SECONDS = 8.0
CALL_TIMEOUT_SECONDS = 3.0
CLOSE_TIMEOUT_SECONDS = 0.8
TERMINATE_TIMEOUT_SECONDS = 2.0


def _create_windows_kill_job(process: subprocess.Popen[bytes]) -> int:
    """Own a Windows plugin tree even if the runner exits before its children."""

    import ctypes
    from ctypes import wintypes

    class _BasicLimits(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_longlong),
            ("PerJobUserTimeLimit", ctypes.c_longlong),
            ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        ]

    class _IoCounters(ctypes.Structure):
        _fields_ = [
            ("ReadOperationCount", ctypes.c_ulonglong),
            ("WriteOperationCount", ctypes.c_ulonglong),
            ("OtherOperationCount", ctypes.c_ulonglong),
            ("ReadTransferCount", ctypes.c_ulonglong),
            ("WriteTransferCount", ctypes.c_ulonglong),
            ("OtherTransferCount", ctypes.c_ulonglong),
        ]

    class _ExtendedLimits(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", _BasicLimits),
            ("IoInfo", _IoCounters),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    create_job = kernel32.CreateJobObjectW
    create_job.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    create_job.restype = wintypes.HANDLE
    set_information = kernel32.SetInformationJobObject
    set_information.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        ctypes.c_void_p,
        wintypes.DWORD,
    ]
    set_information.restype = wintypes.BOOL
    assign_process = kernel32.AssignProcessToJobObject
    assign_process.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    assign_process.restype = wintypes.BOOL
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = [wintypes.HANDLE]
    close_handle.restype = wintypes.BOOL
    handle = create_job(None, None)
    if not handle:
        raise OSError(ctypes.get_last_error(), "CreateJobObjectW failed")
    limits = _ExtendedLimits()
    limits.BasicLimitInformation.LimitFlags = 0x00002000  # KILL_ON_JOB_CLOSE
    if not set_information(
        handle,
        9,
        ctypes.byref(limits),
        ctypes.sizeof(limits),
    ):
        error = ctypes.get_last_error()
        close_handle(handle)
        raise OSError(error, "SetInformationJobObject failed")
    process_handle = wintypes.HANDLE(int(getattr(process, "_handle")))
    if not assign_process(handle, process_handle):
        error = ctypes.get_last_error()
        close_handle(handle)
        raise OSError(error, "AssignProcessToJobObject failed")
    return int(handle)


class PluginRuntimeError(RuntimeError):
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

    @classmethod
    def from_api(cls, error: PluginApiError) -> "PluginRuntimeError":
        return cls(
            error.code,
            str(error),
            plugin_id=error.plugin_id,
            service_key=error.service_key,
        )


@dataclass
class _RuntimeRecord:
    spec: PluginSpec
    state: str = "failed"
    reason_code: str = "NOT_STARTED"
    process: "_PluginProcess | None" = None
    pid: int | None = None


@dataclass(frozen=True)
class _ServiceBinding:
    provider_id: str
    exports: frozenset[str]
    process: "_PluginProcess | None" = None
    host_service: object | None = None


@dataclass(frozen=True)
class _CallbackBinding:
    plugin_id: str
    shape: str
    process: "_PluginProcess"


@dataclass(frozen=True)
class _HostRegistration:
    service_key: str
    registration_id: str


class _PluginProcess:
    def __init__(
        self,
        *,
        roots: RuntimeRoots,
        generation_id: str,
        spec: PluginSpec,
        dependency_root: Path | None,
        request_handler: Callable[[str, Mapping[str, Any]], object],
        on_exit: Callable[[str, "_PluginProcess"], None],
        call_timeout: float,
    ) -> None:
        assert spec.plugin_root is not None
        self._roots = roots
        self._generation_id = generation_id
        self._spec = spec
        self._dependency_root = dependency_root
        self._request_handler = request_handler
        self._on_exit = on_exit
        self._call_timeout = call_timeout
        self._process: subprocess.Popen[bytes] | None = None
        self._peer: RpcPeer | None = None
        self._windows_job: int | None = None
        self._watcher: threading.Thread | None = None
        self._closing = False
        self._exit_reported = False
        self._spawn_lock = threading.Lock()
        self._state_lock = threading.Lock()

    @property
    def pid(self) -> int | None:
        process = self._process
        return process.pid if process is not None and process.poll() is None else None

    def start(self) -> dict[str, Any]:
        plugin_root = Path(self._spec.plugin_root).resolve()
        data_dir = StoragePaths(self._roots.user_root).plugin_data_for(self._spec.plugin_id)
        data_dir.mkdir(parents=True, exist_ok=True)
        runner = Path(__file__).with_name("plugin_runner_v4.py").resolve()
        command = [
            sys.executable,
            "-I",
            "-S",
            str(runner),
            "--plugin-id",
            self._spec.plugin_id,
            "--generation-id",
            self._generation_id,
            "--plugin-root",
            str(plugin_root),
            "--data-dir",
            str(data_dir),
            "--entry",
            self._spec.entry,
        ]
        if self._dependency_root is not None:
            command.extend(["--dependency-root", str(self._dependency_root)])
        environment = os.environ.copy()
        environment.pop("PYTHONPATH", None)
        environment.pop("PYTHONHOME", None)
        environment["PYTHONNOUSERSITE"] = "1"
        environment["PYTHONDONTWRITEBYTECODE"] = "1"
        with self._spawn_lock:
            with self._state_lock:
                if self._closing:
                    raise PluginRuntimeError(
                        "GENERATION_INVALIDATED",
                        plugin_id=self._spec.plugin_id,
                    )
            try:
                process = subprocess.Popen(
                    command,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                    # Keep the process working directory outside plugin code.
                    # Windows cannot atomically quarantine an installed plugin
                    # while a live or recently stopped process has that code
                    # directory open as its CWD. API v4 exposes explicit
                    # plugin data/config paths, so the private data directory
                    # is the stable working directory for the runner.
                    cwd=data_dir,
                    env=environment,
                    bufsize=0,
                    start_new_session=os.name != "nt",
                    creationflags=(
                        getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
                        if os.name == "nt"
                        else 0
                    ),
                )
            except OSError as error:
                raise PluginRuntimeError(
                    "PLUGIN_PROCESS_START_FAILED",
                    plugin_id=self._spec.plugin_id,
                ) from error
            if process.stdin is None or process.stdout is None:
                terminate_process_tree(process, timeout=TERMINATE_TIMEOUT_SECONDS)
                raise PluginRuntimeError(
                    "PLUGIN_PROCESS_START_FAILED",
                    plugin_id=self._spec.plugin_id,
                )
            windows_job: int | None = None
            if os.name == "nt":
                try:
                    windows_job = _create_windows_kill_job(process)
                except OSError as error:
                    terminate_process_tree(process, timeout=TERMINATE_TIMEOUT_SECONDS)
                    raise PluginRuntimeError(
                        "PLUGIN_PROCESS_START_FAILED",
                        plugin_id=self._spec.plugin_id,
                    ) from error
            peer = RpcPeer(
                process.stdout,
                process.stdin,
                generation_id=self._generation_id,
                plugin_id=self._spec.plugin_id,
                request_handler=self._request_handler,
                on_eof=self._process_exited,
            )
            watcher = threading.Thread(
                target=self._wait_for_process_exit,
                args=(process,),
                name=f"sakura-plugin-{self._spec.plugin_id}-process-waiter",
                daemon=True,
            )
            with self._state_lock:
                self._process = process
                self._peer = peer
                self._windows_job = windows_job
                self._watcher = watcher
            peer.start(thread_name=f"sakura-plugin-{self._spec.plugin_id}-core-reader")
            watcher.start()
        try:
            result = peer.request(
                "runtime.initialize",
                {},
                timeout=INITIALIZE_TIMEOUT_SECONDS,
            )
        except PluginApiError as error:
            self.close()
            raise PluginRuntimeError.from_api(error) from error
        if not isinstance(result, Mapping):
            self.close()
            raise PluginRuntimeError("PLUGIN_RESPONSE_INVALID", plugin_id=self._spec.plugin_id)
        return dict(result)

    def _wait_for_process_exit(self, process: subprocess.Popen[bytes]) -> None:
        try:
            process.wait()
        except OSError:
            pass
        self._process_exited()

    def _snapshot_for_close(
        self,
    ) -> tuple[subprocess.Popen[bytes] | None, RpcPeer | None] | None:
        with self._spawn_lock, self._state_lock:
            if self._closing:
                return None
            self._closing = True
            return self._process, self._peer

    def call_service(
        self,
        service_key: str,
        method: str,
        args: Sequence[Any],
        *,
        timeout: float | None = None,
    ) -> object:
        peer = self._peer
        if peer is None:
            raise PluginRuntimeError("PLUGIN_PROCESS_UNAVAILABLE", plugin_id=self._spec.plugin_id)
        try:
            return peer.request(
                "service.call",
                {
                    "serviceKey": service_key,
                    "method": method,
                    "args": list(args),
                },
                timeout=self._call_timeout if timeout is None else timeout,
            )
        except PluginApiError as error:
            raise PluginRuntimeError.from_api(error) from error

    def emit(self, name: str, payload: object) -> None:
        peer = self._peer
        if peer is None:
            raise PluginRuntimeError("PLUGIN_PROCESS_UNAVAILABLE", plugin_id=self._spec.plugin_id)
        try:
            peer.request(
                "event.emit",
                {"name": name, "payload": payload},
                timeout=self._call_timeout,
            )
        except PluginApiError as error:
            raise PluginRuntimeError.from_api(error) from error

    def invoke_callback(
        self,
        handle: str,
        shape: str,
        args: Sequence[Any],
        *,
        timeout: float | None = None,
    ) -> object:
        peer = self._peer
        if peer is None:
            raise PluginRuntimeError("PLUGIN_PROCESS_UNAVAILABLE", plugin_id=self._spec.plugin_id)
        try:
            return peer.request(
                "callback.invoke",
                {"handle": handle, "shape": shape, "args": list(args)},
                timeout=self._call_timeout if timeout is None else timeout,
            )
        except PluginApiError as error:
            raise PluginRuntimeError.from_api(error) from error

    def apply_config(self, values: Mapping[str, Any]) -> dict[str, Any]:
        peer = self._peer
        if peer is None:
            raise PluginRuntimeError("PLUGIN_PROCESS_UNAVAILABLE", plugin_id=self._spec.plugin_id)
        try:
            result = peer.request(
                "config.apply",
                {"values": dict(values)},
                timeout=self._call_timeout,
            )
        except PluginApiError as error:
            raise PluginRuntimeError.from_api(error) from error
        if not isinstance(result, Mapping):
            raise PluginRuntimeError("PLUGIN_RESPONSE_INVALID", plugin_id=self._spec.plugin_id)
        return dict(result)

    def close(self, *, deadline: float | None = None) -> None:
        snapshot = self._snapshot_for_close()
        if snapshot is None:
            return
        process, peer = snapshot
        close_deadline = (
            time.monotonic() + CLOSE_TIMEOUT_SECONDS
            if deadline is None
            else deadline
        )
        if process is not None and process.poll() is None and peer is not None:
            remaining = close_deadline - time.monotonic()
            if remaining > 0:
                try:
                    peer.request(
                        "runtime.close",
                        {},
                        timeout=remaining,
                    )
                except PluginApiError:
                    pass
        if peer is not None:
            peer.close("GENERATION_INVALIDATED")
        if process is not None and process.stdin is not None:
            try:
                process.stdin.close()
            except OSError:
                pass
        if process is not None:
            if process.poll() is None:
                try:
                    process.wait(timeout=max(0.0, close_deadline - time.monotonic()))
                except subprocess.TimeoutExpired:
                    pass
            if process.poll() is None:
                self._terminate_owned_descendants(process, deadline=close_deadline)
        self._close_windows_job()
        for stream in (process.stdout if process is not None else None,):
            try:
                if stream is not None:
                    stream.close()
            except OSError:
                pass

    def terminate_after_transport_failure(self) -> None:
        with self._state_lock:
            self._closing = True
        process = self._process
        if process is None:
            return
        self._terminate_owned_descendants(process)
        self._close_windows_job()
        peer = self._peer
        if peer is not None:
            peer.close("PLUGIN_PROCESS_UNAVAILABLE")
        for stream in (process.stdin, process.stdout):
            try:
                if stream is not None:
                    stream.close()
            except OSError:
                pass

    @staticmethod
    def _terminate_owned_descendants(
        process: subprocess.Popen[bytes],
        *,
        deadline: float | None = None,
    ) -> None:
        remaining = (
            TERMINATE_TIMEOUT_SECONDS
            if deadline is None
            else max(0.0, deadline - time.monotonic())
        )
        if os.name == "nt":
            if process.poll() is None:
                terminate_process_tree(process, timeout=remaining)
            return
        # The runner is a dedicated session leader, so its process group remains
        # an exact owned scope even if the runner crashes before its children.
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except (OSError, ProcessLookupError):
            pass
        if remaining > 0:
            try:
                process.wait(timeout=min(0.3, remaining))
            except subprocess.TimeoutExpired:
                pass
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except (OSError, ProcessLookupError):
            pass
        remaining = (
            0.5
            if deadline is None
            else max(0.0, deadline - time.monotonic())
        )
        if remaining > 0:
            try:
                process.wait(timeout=min(0.5, remaining))
            except (OSError, subprocess.TimeoutExpired):
                pass

    def _process_exited(self) -> None:
        with self._state_lock:
            if self._exit_reported:
                return
            self._exit_reported = True
            closing = self._closing
        if not closing:
            self._on_exit(self._spec.plugin_id, self)

    def _close_windows_job(self) -> None:
        handle = self._windows_job
        self._windows_job = None
        if handle is None or os.name != "nt":
            return
        import ctypes
        from ctypes import wintypes

        close_handle = ctypes.WinDLL("kernel32", use_last_error=True).CloseHandle
        close_handle.argtypes = [wintypes.HANDLE]
        close_handle.restype = wintypes.BOOL
        close_handle(wintypes.HANDLE(handle))


class PluginRuntimeManager:
    """One generation's generic plugin graph; no reconcile or recovery loop."""

    def __init__(
        self,
        roots: RuntimeRoots | Path,
        generation_id: str,
        specs: Sequence[PluginSpec | RuntimePluginSpec],
        *,
        call_timeout: float = CALL_TIMEOUT_SECONDS,
    ) -> None:
        if not isinstance(generation_id, str) or not generation_id:
            raise ValueError("generation_id must not be empty")
        self._roots = coerce_runtime_roots(roots)
        self._generation_id = generation_id
        self._call_timeout = max(0.05, float(call_timeout))
        self._dependencies = PluginDependencyRoots(
            self._roots.user_root,
            distribution_root=self._roots.distribution_root,
        )
        self._records: dict[str, _RuntimeRecord] = {}
        self._services: dict[str, _ServiceBinding] = {}
        self._callbacks: dict[str, _CallbackBinding] = {}
        self._host_registrations: dict[str, list[_HostRegistration]] = {}
        self._activation_order: list[str] = []
        self._lock = threading.RLock()
        self._start_lock = threading.Lock()
        self._operation_lock = threading.Lock()
        self._closed = False
        self._draining_processes: dict[str, _PluginProcess] = {}
        for value in specs:
            spec = value.to_plugin_spec(self._roots) if isinstance(value, RuntimePluginSpec) else value
            if spec.plugin_id in self._records:
                raise PluginRuntimeError("PLUGIN_ID_CONFLICT", plugin_id=spec.plugin_id)
            self._records[spec.plugin_id] = _RuntimeRecord(spec)

    def install_host_service(
        self,
        service_key: str,
        service: object,
        *,
        exports: Sequence[str],
    ) -> None:
        with self._lock:
            if self._closed:
                raise PluginRuntimeError("GENERATION_INVALIDATED")
            if service_key in self._services:
                raise PluginRuntimeError("SERVICE_CONFLICT", service_key=service_key)
            exported = frozenset(exports)
            if any(not callable(getattr(service, method, None)) for method in exported):
                raise PluginRuntimeError("SERVICE_EXPORT_INVALID", service_key=service_key)
            self._services[service_key] = _ServiceBinding(
                "sakura.core",
                exported,
                host_service=service,
            )

    def start(self) -> dict[str, Any]:
        with self._start_lock:
            with self._lock:
                if self._closed:
                    raise PluginRuntimeError("GENERATION_INVALIDATED")
                if self._activation_order:
                    return self.snapshot()
            # Plugin setup may synchronously call an already-active Service.
            # Never hold the routing lock while waiting for initialize/setup.
            self._start_graph()
            return self.snapshot()

    def call_service(
        self,
        service_key: str,
        method: str,
        *args: object,
        timeout: float | None = None,
    ) -> object:
        return self._route_service_call(
            "sakura.core",
            service_key,
            method,
            args,
            timeout=timeout,
        )

    def invoke_callback(
        self,
        handle: str,
        shape: str,
        *args: object,
        timeout: float | None = None,
    ) -> object:
        detached_args = json_value(list(args))
        assert isinstance(detached_args, list)
        with self._lock:
            binding = self._callbacks.get(handle)
            if binding is None:
                raise PluginRuntimeError("CALLBACK_INVALID")
            if binding.shape != shape:
                raise PluginRuntimeError(
                    "CALLBACK_SHAPE_INVALID",
                    plugin_id=binding.plugin_id,
                )
        return binding.process.invoke_callback(
            handle,
            shape,
            detached_args,
            timeout=timeout,
        )

    def owns_callback(self, handle: str) -> bool:
        with self._lock:
            return handle in self._callbacks

    def set_enabled(self, plugin_id: str, enabled: bool) -> dict[str, Any]:
        with self._operation_lock:
            with self._lock:
                record = self._records.get(plugin_id)
                if record is None:
                    raise PluginRuntimeError("PLUGIN_NOT_FOUND", plugin_id=plugin_id)
                record.spec = replace(record.spec, enabled=enabled)
                if self._closed:
                    raise PluginRuntimeError("GENERATION_INVALIDATED", plugin_id=plugin_id)
                if not enabled:
                    consumers = self._hard_dependents_locked(plugin_id)
                else:
                    consumers = []
            if not enabled:
                for consumer_id in consumers:
                    self._stop_process(
                        consumer_id,
                        reason="DEPENDENCY_FAILED",
                        failed=True,
                    )
                self._stop_process(plugin_id, reason="PLUGIN_DISABLED", failed=False)
                return self.snapshot()
            if self._fail_service_conflicts(plugin_id):
                return self.snapshot()
            with self._lock:
                if record.process is not None:
                    return self.snapshot()
                missing = any(key not in self._services for key in record.spec.requires)
                if missing:
                    record.state = "failed"
                    record.reason_code = "MISSING_SERVICE"
                    return self.snapshot()
            self._start_one(record)
            return self.snapshot()

    def install_plugin(self, value: PluginSpec | RuntimePluginSpec) -> dict[str, Any]:
        """Add one explicitly installed plugin without rebuilding unrelated processes."""

        spec = value.to_plugin_spec(self._roots) if isinstance(value, RuntimePluginSpec) else value
        with self._operation_lock:
            with self._lock:
                if self._closed:
                    raise PluginRuntimeError("GENERATION_INVALIDATED", plugin_id=spec.plugin_id)
                if spec.api_version != PLUGIN_API_V4_VERSION:
                    raise PluginRuntimeError("API_VERSION_UNSUPPORTED", plugin_id=spec.plugin_id)
                if spec.plugin_id in self._records:
                    raise PluginRuntimeError("PLUGIN_ID_CONFLICT", plugin_id=spec.plugin_id)
                record = _RuntimeRecord(spec)
                self._records[spec.plugin_id] = record
                if not spec.enabled:
                    record.state = "disabled"
                    record.reason_code = "PLUGIN_DISABLED"
                    return self.snapshot()
            if self._fail_service_conflicts(spec.plugin_id):
                return self.snapshot()
            with self._lock:
                missing = any(key not in self._services for key in spec.requires)
                if missing:
                    record.state = "failed"
                    record.reason_code = "MISSING_SERVICE"
                    return self.snapshot()
            self._start_one(record)
            return self.snapshot()

    def uninstall_plugin(self, plugin_id: str) -> dict[str, Any]:
        """Stop and forget one explicitly uninstalled plugin."""

        with self._operation_lock:
            with self._lock:
                if self._closed:
                    raise PluginRuntimeError("GENERATION_INVALIDATED", plugin_id=plugin_id)
                record = self._records.get(plugin_id)
                if record is None:
                    return self.snapshot()
                if record.spec.required:
                    raise PluginRuntimeError("REQUIRED_PLUGIN_LOCKED", plugin_id=plugin_id)
                consumers = self._hard_dependents_locked(plugin_id)
            for consumer_id in consumers:
                self._stop_process(
                    consumer_id,
                    reason="DEPENDENCY_FAILED",
                    failed=True,
                )
            self._stop_process(plugin_id, reason="PLUGIN_UNINSTALLED", failed=False)
            with self._lock:
                self._records.pop(plugin_id, None)
                self._activation_order[:] = [
                    item for item in self._activation_order if item != plugin_id
                ]
            return self.snapshot()

    def reload_plugin(self, plugin_id: str) -> dict[str, Any]:
        with self._operation_lock:
            self._reload_plugin_locked(plugin_id)
            return self.snapshot()

    def apply_config(self, plugin_id: str, values: Mapping[str, Any]) -> dict[str, Any]:
        detached = json_value(dict(values))
        if not isinstance(detached, dict):
            raise PluginRuntimeError("CONFIG_VALUE_INVALID", plugin_id=plugin_id)
        with self._operation_lock:
            with self._lock:
                record = self._records.get(plugin_id)
                process = record.process if record is not None else None
                if process is None or record is None or record.state != "active":
                    raise PluginRuntimeError("PLUGIN_PROCESS_UNAVAILABLE", plugin_id=plugin_id)
            result = process.apply_config(detached)
            state = result.get("applicationState")
            if state == "restart_required":
                self._reload_plugin_locked(plugin_id)
                return {"applicationState": "applied", "reasonCode": "READY"}
            if state not in {"applied", "error"}:
                raise PluginRuntimeError("CONFIG_APPLY_FAILED", plugin_id=plugin_id)
            return result

    def emit_host_event(self, name: str, payload: object) -> None:
        if not isinstance(name, str) or not name.startswith("sakura.host."):
            raise PluginRuntimeError("HOST_EVENT_NAME_INVALID")
        detached = json_value(payload)
        with self._lock:
            processes = [
                record.process
                for record in self._records.values()
                if record.state == "active" and record.process is not None
            ]
        for process in processes:
            try:
                process.emit(name, detached)
            except PluginRuntimeError:
                continue

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            plugins = [
                {
                    "pluginId": record.spec.plugin_id,
                    "enabled": record.spec.enabled,
                    "state": record.state,
                    "reasonCode": record.reason_code,
                    "provides": list(record.spec.provides),
                    "requires": list(record.spec.requires),
                    "pid": record.pid,
                }
                for record in sorted(self._records.values(), key=lambda item: item.spec.plugin_id)
            ]
        return {"schemaVersion": 1, "state": "ready", "reasonCode": "READY", "plugins": plugins}

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            order = list(reversed(self._activation_order))
            order.extend(
                plugin_id
                for plugin_id, record in self._records.items()
                if record.process is not None and plugin_id not in order
            )
        deadline = time.monotonic() + CLOSE_TIMEOUT_SECONDS
        for plugin_id in order:
            self._stop_process(
                plugin_id,
                reason="PLUGIN_STOPPED",
                failed=False,
                deadline=deadline,
            )
        with self._lock:
            self._services.clear()
            self._callbacks.clear()
            self._host_registrations.clear()
            self._activation_order.clear()

    def _reload_plugin_locked(self, plugin_id: str) -> None:
        with self._lock:
            record = self._records.get(plugin_id)
            if record is None:
                raise PluginRuntimeError("PLUGIN_NOT_FOUND", plugin_id=plugin_id)
            if self._closed:
                raise PluginRuntimeError("GENERATION_INVALIDATED", plugin_id=plugin_id)
        if self._fail_service_conflicts(plugin_id):
            raise PluginRuntimeError("SERVICE_CONFLICT", plugin_id=plugin_id)
        with self._lock:
            consumers_to_stop = self._hard_dependents_locked(plugin_id)
            consumers_to_restart = list(reversed(consumers_to_stop))
        for consumer_id in consumers_to_stop:
            self._stop_process(
                consumer_id,
                reason="DEPENDENCY_RELOADING",
                failed=True,
            )
        self._stop_process(plugin_id, reason="PLUGIN_RELOADING", failed=True)
        with self._lock:
            enabled = record.spec.enabled
        if enabled and not self._start_one(record):
            for consumer_id in consumers_to_restart:
                self._stop_process(
                    consumer_id,
                    reason="DEPENDENCY_FAILED",
                    failed=True,
                )
            raise PluginRuntimeError(record.reason_code, plugin_id=plugin_id)
        for index, consumer_id in enumerate(consumers_to_restart):
            with self._lock:
                consumer = self._records[consumer_id]
                can_start = consumer.spec.enabled and all(
                    required in self._services for required in consumer.spec.requires
                )
            if not can_start:
                for remaining_id in consumers_to_restart[index:]:
                    self._stop_process(
                        remaining_id,
                        reason="DEPENDENCY_FAILED",
                        failed=True,
                    )
                raise PluginRuntimeError("DEPENDENCY_FAILED", plugin_id=consumer_id)
            if not self._start_one(consumer):
                for remaining_id in consumers_to_restart[index + 1 :]:
                    self._stop_process(
                        remaining_id,
                        reason="DEPENDENCY_FAILED",
                        failed=True,
                    )
                raise PluginRuntimeError(consumer.reason_code, plugin_id=consumer_id)

    def _service_conflict_participants_locked(self, plugin_id: str) -> set[str]:
        record = self._records.get(plugin_id)
        if record is None or not record.spec.enabled:
            return set()
        participants: set[str] = set()
        for service_key in record.spec.provides:
            plugin_providers = {
                candidate_id
                for candidate_id, candidate in self._records.items()
                if candidate.spec.enabled
                and candidate.spec.api_version == PLUGIN_API_V4_VERSION
                and service_key in candidate.spec.provides
            }
            binding = self._services.get(service_key)
            host_conflict = binding is not None and binding.provider_id == "sakura.core"
            if len(plugin_providers) > 1 or host_conflict:
                participants.update(plugin_providers)
        return participants

    def _fail_service_conflicts(self, plugin_id: str) -> bool:
        with self._lock:
            participants = self._service_conflict_participants_locked(plugin_id)
            if not participants:
                return False
            consumers: list[str] = []
            for participant_id in participants:
                for consumer_id in self._hard_dependents_locked(participant_id):
                    if consumer_id not in participants and consumer_id not in consumers:
                        consumers.append(consumer_id)
            active_participants = [
                item for item in reversed(self._activation_order) if item in participants
            ]
            active_participants.extend(
                item for item in sorted(participants) if item not in active_participants
            )
        for consumer_id in consumers:
            self._stop_process(
                consumer_id,
                reason="DEPENDENCY_FAILED",
                failed=True,
            )
        for participant_id in active_participants:
            self._stop_process(
                participant_id,
                reason="SERVICE_CONFLICT",
                failed=True,
            )
        return True

    def _start_graph(self) -> None:
        enabled = {
            plugin_id: record
            for plugin_id, record in self._records.items()
            if record.spec.enabled
        }
        for record in self._records.values():
            if not record.spec.enabled:
                record.state = "disabled"
                record.reason_code = "PLUGIN_DISABLED"
            elif record.spec.api_version != PLUGIN_API_V4_VERSION:
                record.state = "failed"
                record.reason_code = "API_VERSION_UNSUPPORTED"
        candidates = {
            plugin_id
            for plugin_id, record in enabled.items()
            if record.spec.api_version == PLUGIN_API_V4_VERSION
        }
        providers: dict[str, list[str]] = {}
        for plugin_id in candidates:
            for service_key in enabled[plugin_id].spec.provides:
                providers.setdefault(service_key, []).append(plugin_id)
        for service_key, plugin_ids in providers.items():
            if len(plugin_ids) > 1 or service_key in self._services:
                for plugin_id in plugin_ids:
                    record = self._records[plugin_id]
                    record.state = "failed"
                    record.reason_code = "SERVICE_CONFLICT"
                    candidates.discard(plugin_id)

        unique_provider = {
            service_key: plugin_ids[0]
            for service_key, plugin_ids in providers.items()
            if len(plugin_ids) == 1
        }
        remaining = set(candidates)
        while remaining:
            ready = sorted(
                plugin_id
                for plugin_id in remaining
                if all(
                    required in self._services
                    or unique_provider.get(required) not in remaining
                    for required in self._records[plugin_id].spec.requires
                )
            )
            if not ready:
                for plugin_id in sorted(remaining):
                    record = self._records[plugin_id]
                    record.state = "failed"
                    record.reason_code = "DEPENDENCY_CYCLE"
                break
            for plugin_id in ready:
                remaining.remove(plugin_id)
                record = self._records[plugin_id]
                if any(required not in self._services for required in record.spec.requires):
                    record.state = "failed"
                    record.reason_code = "MISSING_SERVICE"
                    continue
                self._start_one(record)

    def _start_one(self, record: _RuntimeRecord) -> bool:
        spec = record.spec
        assert spec.plugin_root is not None
        with self._lock:
            if self._closed:
                record.state = "failed"
                record.reason_code = "GENERATION_INVALIDATED"
                return False
            if self._service_conflict_participants_locked(spec.plugin_id):
                record.state = "failed"
                record.reason_code = "SERVICE_CONFLICT"
                return False
        try:
            dependency_root = self._dependencies.verified_root(
                spec.plugin_id,
                spec.plugin_root,
                source=spec.source,
            )
        except PluginDependencyError as error:
            record.state = "failed"
            record.reason_code = error.code
            return False
        process = _PluginProcess(
            roots=self._roots,
            generation_id=self._generation_id,
            spec=spec,
            dependency_root=dependency_root,
            request_handler=lambda name, payload: self._handle_plugin_request(
                spec.plugin_id,
                name,
                payload,
                calling_process=process,
            ),
            on_exit=self._plugin_exited,
            call_timeout=self._call_timeout,
        )
        with self._lock:
            if self._closed:
                record.state = "failed"
                record.reason_code = "GENERATION_INVALIDATED"
                return
            record.process = process
            record.pid = None
            record.state = "failed"
            record.reason_code = "PLUGIN_STARTING"
        try:
            result = process.start()
            raw_provides = result.get("provides")
            if not isinstance(raw_provides, Mapping):
                raise PluginRuntimeError("PLUGIN_RESPONSE_INVALID", plugin_id=spec.plugin_id)
            declared = set(spec.provides)
            if set(raw_provides) != declared:
                raise PluginRuntimeError("PLUGIN_PROVIDES_MISMATCH", plugin_id=spec.plugin_id)
            bindings: dict[str, _ServiceBinding] = {}
            for service_key, raw_exports in raw_provides.items():
                if (
                    not isinstance(service_key, str)
                    or not isinstance(raw_exports, list)
                    or any(not isinstance(item, str) for item in raw_exports)
                ):
                    raise PluginRuntimeError("PLUGIN_RESPONSE_INVALID", plugin_id=spec.plugin_id)
                bindings[service_key] = _ServiceBinding(
                    spec.plugin_id,
                    frozenset(raw_exports),
                    process=process,
                )
        except PluginRuntimeError as error:
            process.close()
            with self._lock:
                if record.process is process:
                    missing_dependency = any(
                        required not in self._services
                        for required in record.spec.requires
                    )
                    record.process = None
                    record.pid = None
                    record.state = "failed"
                    record.reason_code = (
                        "DEPENDENCY_FAILED" if missing_dependency else error.code
                    )
            return False
        should_close = False
        with self._lock:
            missing_dependency = any(
                required not in self._services for required in record.spec.requires
            )
            if (
                self._closed
                or record.process is not process
                or process.pid is None
                or missing_dependency
            ):
                if record.process is process:
                    record.process = None
                    record.pid = None
                record.state = "failed"
                record.reason_code = (
                    "GENERATION_INVALIDATED"
                    if self._closed
                    else "DEPENDENCY_FAILED"
                    if missing_dependency
                    else "PLUGIN_PROCESS_EXITED"
                )
                should_close = True
            else:
                record.pid = process.pid
                record.state = "active"
                record.reason_code = "READY"
                self._services.update(bindings)
                self._activation_order.append(spec.plugin_id)
        if should_close:
            process.close()
            return False
        return True

    def _handle_plugin_request(
        self,
        caller_id: str,
        name: str,
        payload: Mapping[str, Any],
        *,
        calling_process: _PluginProcess | None = None,
    ) -> object:
        if calling_process is not None:
            with self._lock:
                record = self._records.get(caller_id)
                if (record is None or record.process is not calling_process) and self._draining_processes.get(caller_id) is not calling_process:
                    raise PluginApiError("GENERATION_INVALIDATED", plugin_id=caller_id)
        if name == "callback.register":
            shape = payload.get("shape")
            if not isinstance(shape, str) or not shape or len(shape) > 128:
                raise PluginApiError("CALLBACK_SHAPE_INVALID", plugin_id=caller_id)
            with self._lock:
                record = self._records.get(caller_id)
                process = record.process if record is not None else None
                if process is None:
                    raise PluginApiError("PLUGIN_PROCESS_UNAVAILABLE", plugin_id=caller_id)
                handle = ""
                while not handle or handle in self._callbacks:
                    handle = f"cb_{secrets.token_hex(16)}"
                self._callbacks[handle] = _CallbackBinding(caller_id, shape, process)
            return {"handle": handle}
        if name == "callback.unregister":
            handle = payload.get("handle")
            if not isinstance(handle, str):
                raise PluginApiError("CALLBACK_INVALID", plugin_id=caller_id)
            with self._lock:
                binding = self._callbacks.get(handle)
                removed = binding is not None and binding.plugin_id == caller_id
                if removed:
                    del self._callbacks[handle]
            return {"removed": removed}
        if name != "service.call":
            raise PluginApiError("PLUGIN_REQUEST_UNKNOWN", plugin_id=caller_id)
        service_key = payload.get("serviceKey")
        method = payload.get("method")
        args = payload.get("args")
        if (
            not isinstance(service_key, str)
            or not isinstance(method, str)
            or not isinstance(args, list)
        ):
            raise PluginApiError("PLUGIN_PROTOCOL_INVALID", plugin_id=caller_id)
        try:
            return self._route_service_call(caller_id, service_key, method, args)
        except PluginRuntimeError as error:
            raise PluginApiError(
                error.code,
                str(error),
                plugin_id=error.plugin_id,
                service_key=error.service_key,
            ) from error

    def _route_service_call(
        self,
        caller_id: str,
        service_key: str,
        method: str,
        args: Sequence[Any],
        *,
        timeout: float | None = None,
    ) -> object:
        detached_args = json_value(list(args))
        assert isinstance(detached_args, list)
        with self._lock:
            binding = self._services.get(service_key)
            draining_log = (binding is not None
                and getattr(binding.host_service, "allow_during_shutdown", False) is True
                and caller_id in self._draining_processes)
            if self._closed and not draining_log:
                raise PluginRuntimeError("GENERATION_INVALIDATED")
        if binding is None:
            raise PluginRuntimeError("SERVICE_MISSING", service_key=service_key)
        if method not in binding.exports:
            raise PluginRuntimeError(
                "SERVICE_METHOD_NOT_EXPORTED",
                plugin_id=binding.provider_id,
                service_key=service_key,
            )
        if binding.process is not None:
            return binding.process.call_service(
                service_key,
                method,
                detached_args,
                timeout=timeout,
            )
        callback = getattr(binding.host_service, method, None)
        if not callable(callback):
            raise PluginRuntimeError("SERVICE_METHOD_NOT_EXPORTED", service_key=service_key)
        with self._lock:
            caller_record = self._records.get(caller_id)
            spec = caller_record.spec if caller_record else None
            log_metadata = (spec.name, spec.provides) if spec else ("", ())
        metadata_token = HOST_CALLER_LOG_METADATA.set(log_metadata)
        caller_token = HOST_CALLER.set(caller_id)
        try:
            result = json_value(callback(*detached_args))
            self._track_host_effect(caller_id, service_key, method, detached_args, result)
            return result
        except PluginRuntimeError:
            raise
        except Exception as error:
            code = getattr(error, "code", "HOST_SERVICE_CALL_FAILED")
            if not isinstance(code, str) or not code or len(code) > 80:
                code = "HOST_SERVICE_CALL_FAILED"
            raise PluginRuntimeError(
                code,
                service_key=service_key,
            ) from error
        finally:
            HOST_CALLER.reset(caller_token)
            HOST_CALLER_LOG_METADATA.reset(metadata_token)

    def _track_host_effect(
        self,
        caller_id: str,
        service_key: str,
        method: str,
        args: Sequence[Any],
        result: object,
    ) -> None:
        if caller_id == "sakura.core" or not service_key.startswith("sakura.host."):
            return
        if method == "register" and isinstance(result, Mapping):
            registration_id = result.get("registrationId")
            if isinstance(registration_id, str) and registration_id:
                with self._lock:
                    self._host_registrations.setdefault(caller_id, []).append(
                        _HostRegistration(service_key, registration_id)
                    )
            return
        if method == "unregister" and len(args) == 1 and isinstance(args[0], str):
            with self._lock:
                registrations = self._host_registrations.get(caller_id, [])
                self._host_registrations[caller_id] = [
                    item for item in registrations if item.registration_id != args[0]
                ]

    def _clear_plugin_scope(self, plugin_id: str) -> None:
        with self._lock:
            registrations = list(reversed(self._host_registrations.pop(plugin_id, [])))
            self._callbacks = {
                handle: binding
                for handle, binding in self._callbacks.items()
                if binding.plugin_id != plugin_id
            }
            host_bindings = {
                key: binding
                for key, binding in self._services.items()
                if binding.host_service is not None
            }
        for registration in registrations:
            binding = host_bindings.get(registration.service_key)
            callback = (
                getattr(binding.host_service, "unregister", None)
                if binding is not None
                else None
            )
            if callable(callback):
                try:
                    callback(registration.registration_id)
                except Exception:
                    pass
        for binding in host_bindings.values():
            callback = getattr(binding.host_service, "revoke_scope", None)
            if callable(callback):
                try:
                    callback(plugin_id)
                except Exception:
                    pass

    def _plugin_exited(self, plugin_id: str, process: _PluginProcess) -> None:
        with self._lock:
            record = self._records.get(plugin_id)
            if self._closed or record is None or record.process is not process:
                return
            self._activation_order[:] = [item for item in self._activation_order if item != plugin_id]
            self._services = {
                key: binding
                for key, binding in self._services.items()
                if binding.provider_id != plugin_id
            }
            consumers = self._hard_dependents_locked(plugin_id)
        # Revoke Host-owned resources before publishing the failed state. A
        # snapshot that says ``failed`` must not still expose artifacts, tools,
        # settings contributions, or other effects from the crashed process.
        self._clear_plugin_scope(plugin_id)
        with self._lock:
            if record.process is process:
                record.state = "failed"
                record.reason_code = "PLUGIN_PROCESS_EXITING"
        process.terminate_after_transport_failure()
        with self._lock:
            if record.process is process:
                record.process = None
                record.pid = None
                record.reason_code = "PLUGIN_PROCESS_EXITED"
        for consumer_id in consumers:
            self._stop_process(
                consumer_id,
                reason="DEPENDENCY_FAILED",
                failed=True,
            )

    def _hard_dependents_locked(self, provider_id: str) -> list[str]:
        affected = {provider_id}
        changed = True
        while changed:
            changed = False
            provided = {
                service_key
                for plugin_id in affected
                for service_key in self._records[plugin_id].spec.provides
            }
            for plugin_id, record in self._records.items():
                if (
                    plugin_id not in affected
                    and record.process is not None
                    and provided.intersection(record.spec.requires)
                ):
                    affected.add(plugin_id)
                    changed = True
        return [
            plugin_id
            for plugin_id in reversed(self._activation_order)
            if plugin_id in affected and plugin_id != provider_id
        ]

    def _stop_process(
        self,
        plugin_id: str,
        *,
        reason: str,
        failed: bool,
        deadline: float | None = None,
    ) -> None:
        with self._lock:
            record = self._records.get(plugin_id)
            if record is None:
                return
            process = record.process
            if process is not None:
                self._draining_processes[plugin_id] = process
            record.process = None
            record.pid = None
            record.state = "failed" if failed else "disabled"
            record.reason_code = reason
            self._services = {
                key: binding
                for key, binding in self._services.items()
                if binding.provider_id != plugin_id
            }
            self._activation_order[:] = [item for item in self._activation_order if item != plugin_id]
        if process is not None:
            try:
                process.close(deadline=deadline)
            finally:
                with self._lock:
                    self._draining_processes.pop(plugin_id, None)
        self._clear_plugin_scope(plugin_id)


__all__ = ["PluginRuntimeError", "PluginRuntimeManager"]
