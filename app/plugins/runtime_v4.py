"""Core-owned Plugin Runtime v4 manager and per-plugin process clients."""

from __future__ import annotations

import os
import secrets
import signal
import subprocess
import sys
import threading
import time
import codecs
from copy import deepcopy
from dataclasses import dataclass, replace
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from app.plugin_sdk.sakura_process import terminate_process_tree
from app.plugins.dependencies import PluginDependencyError, PluginDependencyRoots
from app.plugins.inventory import RuntimePluginSpec
from app.plugins.models import PLUGIN_API_V4_VERSION, PluginSpec
from app.plugins.host_services import HOST_CALLER, HOST_CALLER_LOG_METADATA, HOST_CALLER_SCOPE
from app.plugins.sakura_plugin_sdk import PluginApiError, RpcPeer, json_value
from app.storage.paths import StoragePaths
from app.storage.runtime_roots import RuntimeRoots, coerce_runtime_roots


INITIALIZE_TIMEOUT_SECONDS = 8.0
CALL_TIMEOUT_SECONDS = 3.0
CLOSE_TIMEOUT_SECONDS = 0.8
TERMINATE_TIMEOUT_SECONDS = 2.0


def _process_working_directory(directory: Path) -> str:
    # Rust canonical paths may retain the Windows verbatim namespace. As a
    # process cwd it breaks root-relative probes such as distro's /etc lookup.
    # Keep argument and resource paths unchanged; only normalize this boundary.
    value = str(directory)
    if os.name == "nt" and value.startswith("\\\\?\\"):
        if value[4:8].upper() == "UNC\\":
            return "\\\\" + value[8:]
        if len(value) >= 7 and value[4].isalpha() and value[5:7] == ":\\":
            return value[4:]
    return value


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


@dataclass(frozen=True)
class _DrainingProcess:
    process: "_PluginProcess"
    deadline: float


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
        self.scope_id = secrets.token_hex(16)
        self._dependency_root = dependency_root
        self._request_handler = request_handler
        self._on_exit = on_exit
        self._call_timeout = call_timeout
        self._process: subprocess.Popen[bytes] | None = None
        self._peer: RpcPeer | None = None
        self._windows_job: int | None = None
        self._watcher: threading.Thread | None = None
        self._stderr_reader: threading.Thread | None = None
        self._closing = False
        self._cleanup_complete = threading.Event()
        self._cleanup_error: BaseException | None = None
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
                    stderr=subprocess.PIPE,
                    # Keep the process working directory outside plugin code.
                    # Windows cannot atomically quarantine an installed plugin
                    # while a live or recently stopped process has that code
                    # directory open as its CWD. API v4 exposes explicit
                    # plugin data/config paths, so the private data directory
                    # is the stable working directory for the runner.
                    cwd=_process_working_directory(data_dir),
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
            self._stderr_reader = threading.Thread(
                target=self._drain_stderr, args=(process,),
                name=f"sakura-plugin-{self._spec.plugin_id}-stderr", daemon=True,
            )
            self._stderr_reader.start()
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

    def _drain_stderr(self, process: subprocess.Popen[bytes]) -> None:
        """Always drain the pipe; the bounded log queue owns downstream drops."""
        from app.core.runtime_log import log_message
        from app.core.diagnostics import safe_diagnostic_text

        stream = process.stderr
        if stream is None:
            return
        decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        pending = ""

        def emit(text: str) -> None:
            if text.strip():
                # stderr also carries redirected print output and download progress;
                # the stream alone does not establish a warning or failure.
                log_message("info", "插件诊断输出", component="plugin",
                    plugin_id=self._spec.plugin_id, plugin_name=self._spec.name,
                    fields={"event": "plugin.process.stderr", "stage": "stderr",
                            "diagnostic": safe_diagnostic_text(text)})

        try:
            while chunk := stream.read(4096):
                pending += decoder.decode(chunk)
                if "\n" in pending:
                    complete, _, pending = pending.rpartition("\n")
                    emit(complete)
                if len(pending) >= 4096:
                    emit(pending)
                    pending = ""
            emit(pending + decoder.decode(b"", final=True))
        except (OSError, ValueError):
            pass
        finally:
            stream.close()

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
        caller_id: str = "sakura.core",
        caller_scope: str | None = None,
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
                    "callerId": caller_id,
                    "callerScope": caller_scope,
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

    def notify(self, name: str, payload: object) -> None:
        peer = self._peer
        if peer is None:
            raise PluginRuntimeError("PLUGIN_PROCESS_UNAVAILABLE", plugin_id=self._spec.plugin_id)
        try:
            peer.notify("event.emit", {"name": name, "payload": payload})
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
            self.wait_for_cleanup()
            return
        self._run_cleanup(lambda: self._close_owned_process(snapshot, deadline))

    def _close_owned_process(self, snapshot, deadline) -> None:
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
                except PluginApiError as error:
                    from app.core.diagnostics import exception_diagnostics
                    from app.core.runtime_log import log_message

                    log_message("warning", "插件协作清理失败，正在回收进程", component="plugin",
                        plugin_id=self._spec.plugin_id, plugin_name=self._spec.name,
                        fields={"event": "plugin.cleanup.failed", **exception_diagnostics(
                            error, reason_code=error.code, stage="cleanup")})
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
            # A cooperative runner can exit while its children still own resources.
            self._terminate_owned_descendants(process, deadline=close_deadline)
        self._close_windows_job()
        if self._stderr_reader is not None:
            self._stderr_reader.join(timeout=0.3)
        for stream in (process.stdout if process is not None else None,):
            try:
                if stream is not None:
                    stream.close()
            except OSError:
                pass
        if process is not None and process.poll() is None:
            process.wait(timeout=TERMINATE_TIMEOUT_SECONDS)

    def terminate_after_transport_failure(self) -> None:
        snapshot = self._snapshot_for_close()
        if snapshot is None:
            self.wait_for_cleanup()
            return
        self._run_cleanup(lambda: self._terminate_failed_process(snapshot))

    def _terminate_failed_process(self, snapshot) -> None:
        process, peer = snapshot
        if process is None:
            return
        self._terminate_owned_descendants(process)
        self._close_windows_job()
        if peer is not None:
            peer.close("PLUGIN_PROCESS_UNAVAILABLE")
        for stream in (process.stdin, process.stdout):
            try:
                if stream is not None:
                    stream.close()
            except OSError:
                pass
        if process.poll() is None:
            process.wait(timeout=TERMINATE_TIMEOUT_SECONDS)

    def _run_cleanup(self, cleanup: Callable[[], None]) -> None:
        try:
            cleanup()
        except BaseException as error:
            self._cleanup_error = error
            raise PluginRuntimeError("PLUGIN_CLEANUP_FAILED", plugin_id=self._spec.plugin_id) from error
        finally:
            # Completion means the attempt returned, not that stopping succeeded.
            self._cleanup_complete.set()

    def wait_for_cleanup(self) -> None:
        """A removed Service is not evidence that its owned process has stopped."""
        self._cleanup_complete.wait()
        if self._cleanup_error is not None:
            raise PluginRuntimeError("PLUGIN_CLEANUP_FAILED", plugin_id=self._spec.plugin_id) from self._cleanup_error

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
        self._draining_processes: dict[str, _DrainingProcess] = {}
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

    def start(self, *, services: Sequence[str] | None = None) -> dict[str, Any]:
        """Start an unattempted dependency slice, or finish the remaining graph."""
        with self._start_lock:
            with self._lock:
                if self._closed:
                    raise PluginRuntimeError("GENERATION_INVALIDATED")
            # Plugin setup may synchronously call an already-active Service.
            # Never hold the routing lock while waiting for initialize/setup.
            self._start_graph(services)
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
        detached_args = deepcopy(list(args))
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

    def call_bound_service(
        self, service_key: str, identity: Mapping[str, str], method: str,
        *args: object, timeout: float | None = None,
    ) -> object:
        """Route to one process lifetime; a reload never adopts an active job."""
        return self._route_service_call(
            "sakura.core", service_key, method, args,
            timeout=timeout, expected_identity=identity,
        )

    def commit_bound_service(self, service_key, identity, commit):
        """Keep a short, local result commit atomic with service invalidation."""
        with self._lock:
            if self._closed or self.service_identity(service_key) != identity:
                raise PluginRuntimeError("SERVICE_BINDING_EXPIRED", service_key=service_key)
            return commit()

    def commit_plugin_scope(self, plugin_id: str, scope_id: str, commit):
        """Publish a small Host grant atomically with the receiver's invalidation."""
        with self._lock:
            record = self._records.get(plugin_id)
            process = record.process if record else None
            if (self._closed or process is None or process.scope_id != scope_id
                    or record.state != "active" and record.reason_code != "PLUGIN_STARTING"):
                raise PluginRuntimeError("SERVICE_BINDING_EXPIRED", plugin_id=plugin_id)
            return commit()

    def abort_bound_service(
        self, service_key: str, identity: Mapping[str, str], *, reason: str,
    ) -> bool:
        """Finish one uncertain call's process lifetime before releasing its inputs."""
        provider_id, scope_id = identity.get("providerId"), identity.get("scopeId")
        if not provider_id or not scope_id:
            raise PluginRuntimeError("SERVICE_BINDING_EXPIRED", service_key=service_key)
        with self._operation_lock:
            with self._lock:
                record = self._records.get(provider_id)
                if record is None or service_key not in record.spec.provides:
                    return False
                draining = self._draining_processes.get(provider_id)
                process = record.process or (draining.process if draining else None)
                if process is None or process.scope_id != scope_id:
                    # A replacement lifetime cannot inherit the uncertain call.
                    return False
                consumers = self._hard_dependents_locked(provider_id)
            deadline = time.monotonic() + CLOSE_TIMEOUT_SECONDS
            first_error = None
            for plugin_id in [*consumers, provider_id]:
                try:
                    self._stop_process(
                        plugin_id,
                        reason=reason if plugin_id == provider_id else "DEPENDENCY_FAILED",
                        failed=True,
                        deadline=deadline,
                    )
                except Exception as error:
                    if first_error is None:
                        first_error = error
            if first_error is not None:
                raise first_error
            return True

    def service_identity(self, service_key: str, *, include_starting: bool = False) -> dict[str, str]:
        """Host-only identity for an active Service's exact process lifetime."""
        with self._lock:
            binding = self._services.get(service_key)
            if binding is not None and binding.process is not None:
                return {"providerId": binding.provider_id, "scopeId": binding.process.scope_id}
            if include_starting:
                candidates = [record for record in self._records.values()
                              if service_key in record.spec.provides and record.process is not None
                              and record.reason_code == "PLUGIN_STARTING"]
                if len(candidates) == 1:
                    record = candidates[0]
                    return {"providerId": record.spec.plugin_id, "scopeId": record.process.scope_id}
        raise PluginRuntimeError("SERVICE_MISSING", service_key=service_key)

    def service_exports(self, service_key: str) -> frozenset[str]:
        with self._lock:
            binding = self._services.get(service_key)
            return binding.exports if binding is not None else frozenset()

    def owns_callback(self, handle: str) -> bool:
        with self._lock:
            return handle in self._callbacks

    @contextmanager
    def pause_service_providers(self, prefix: str):
        ids = [item["pluginId"] for item in self.snapshot()["plugins"]
               if item["state"] == "active" and any(key.startswith(prefix) for key in item["provides"])]
        with self.pause_plugins(ids) as errors:
            yield errors

    @contextmanager
    def pause_plugins(self, plugin_ids: Sequence[str]):
        """Pause active readers and their dependents without changing enablement.

        The caller inspects restoration errors separately from its file transaction.
        """
        errors = []
        with self._operation_lock:
            with self._lock:
                affected = set()
                for plugin_id in plugin_ids:
                    record = self._records[plugin_id]
                    if record.state == "active":
                        affected.add(plugin_id)
                        affected.update(self._hard_dependents_locked(plugin_id))
                order = [plugin_id for plugin_id in self._activation_order if plugin_id in affected]
            try:
                stopped = []
                for plugin_id in reversed(order):
                    self._stop_process(plugin_id, reason="PLUGIN_RELOADING", failed=False)
                    stopped.append(plugin_id)
                yield errors
            finally:
                for plugin_id in reversed(stopped):
                    try:
                        record = self._records[plugin_id]
                        if not self._start_one(record):
                            raise PluginRuntimeError(record.reason_code, plugin_id=plugin_id)
                    except Exception as error:
                        errors.append(error)

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
                    self._log_lifecycle(record, "plugin.start.blocked", "插件无法启动", failed=True)
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
                    self._log_lifecycle(record, "plugin.start.blocked", "插件无法启动", failed=True)
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

    def notify_host_event(self, name: str, payload: object) -> None:
        """Wake optional observers of facts they can reread from their owner."""
        if (not isinstance(name, str) or not name.startswith("sakura.host.")
                or name == "sakura.host.scope.closed"):
            raise PluginRuntimeError("HOST_EVENT_NAME_INVALID")
        detached = json_value(payload)
        with self._lock:
            recipients = [(record, record.process) for record in self._records.values()
                          if record.state == "active" and record.process is not None]
        for record, process in recipients:
            try:
                process.notify(name, detached)
            except PluginRuntimeError as error:
                self._log_lifecycle(record, "plugin.notification.dropped", "插件观察通知未入队",
                    diagnostics={"event_name": name, "notification_reason": error.code})

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            plugins = [
                {
                    "pluginId": record.spec.plugin_id,
                    "enabled": record.spec.enabled,
                    "state": (
                        "starting" if record.spec.enabled and record.reason_code in {"NOT_STARTED", "PLUGIN_STARTING"}
                        else "disabled" if not record.spec.enabled and record.reason_code == "NOT_STARTED"
                        else record.state
                    ),
                    "reasonCode": record.reason_code,
                    "provides": list(record.spec.provides),
                    "requires": list(record.spec.requires),
                    "pid": record.pid,
                }
                for record in sorted(self._records.values(), key=lambda item: item.spec.plugin_id)
            ]
            state, reason = (
                ("stopped", "PLUGIN_RUNTIME_STOPPED") if self._closed
                else ("starting", "PLUGIN_STARTING") if any(item["state"] == "starting" for item in plugins)
                else ("degraded", "PLUGIN_START_FAILED") if any(item["state"] == "failed" for item in plugins)
                else ("ready", "READY")
            )
        return {"schemaVersion": 1, "state": state, "reasonCode": reason, "plugins": plugins}

    def close(self) -> None:
        with self._lock:
            self._closed = True
            order = list(reversed(self._activation_order))
            order.extend(
                plugin_id
                for plugin_id, record in self._records.items()
                if record.process is not None and plugin_id not in order
            )
            # A concurrent disable or close removes the active record before
            # its owned process exits. Every close caller must join that cleanup.
            order.extend(plugin_id for plugin_id in self._draining_processes if plugin_id not in order)
        deadline = time.monotonic() + CLOSE_TIMEOUT_SECONDS
        first_error = None
        for plugin_id in order:
            try:
                self._stop_process(
                    plugin_id,
                    reason="PLUGIN_STOPPED",
                    failed=False,
                    deadline=deadline,
                )
            except Exception as error:
                if first_error is None:
                    first_error = error
        if first_error is not None:
            # Keep failed process scopes available for diagnosis; one failure
            # must not leave the remaining plugins running during Core shutdown.
            raise first_error
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

    def _start_graph(self, services: Sequence[str] | None = None) -> None:
        with self._lock:
            unstarted = {plugin_id for plugin_id, record in self._records.items()
                         if record.reason_code == "NOT_STARTED"}
            enabled = {
                plugin_id: record
                for plugin_id, record in self._records.items()
                if record.spec.enabled
            }
            for record in self._records.values():
                if record.reason_code != "NOT_STARTED":
                    continue
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
                binding = self._services.get(service_key)
                if len(plugin_ids) > 1 or (binding is not None and binding.provider_id != plugin_ids[0]):
                    for plugin_id in plugin_ids:
                        record = enabled[plugin_id]
                        if record.reason_code == "NOT_STARTED":
                            record.state = "failed"
                            record.reason_code = "SERVICE_CONFLICT"
                        candidates.discard(plugin_id)

            unique_provider = {
                service_key: plugin_ids[0]
                for service_key, plugin_ids in providers.items()
                if len(plugin_ids) == 1
            }
            # Every slice resolves against the full enabled inventory so a later
            # optional provider cannot silently replace an earlier selected one.
            # Failed/disabled processes are retried only by explicit management.
            candidates = {plugin_id for plugin_id in candidates
                          if enabled[plugin_id].reason_code == "NOT_STARTED"}
            remaining = set(candidates)
            if services is not None:
                remaining = set()
                pending = list(services)
                while pending:
                    provider = unique_provider.get(pending.pop())
                    if provider in candidates and provider not in remaining:
                        remaining.add(provider)
                        pending.extend(enabled[provider].spec.requires)
        while remaining:
            with self._lock:
                if self._closed:
                    return
                remaining = {plugin_id for plugin_id in remaining
                             if self._records.get(plugin_id) is enabled[plugin_id]
                             and enabled[plugin_id].reason_code == "NOT_STARTED"}
                ready = sorted(
                    plugin_id
                    for plugin_id in remaining
                    if all(
                        required in self._services
                        or unique_provider.get(required) not in remaining
                        for required in enabled[plugin_id].spec.requires
                    )
                )
                if not ready:
                    for plugin_id in sorted(remaining):
                        record = enabled[plugin_id]
                        record.state = "failed"
                        record.reason_code = "DEPENDENCY_CYCLE"
                    break
            for plugin_id in ready:
                remaining.remove(plugin_id)
                record = enabled[plugin_id]
                with self._lock:
                    if self._records.get(plugin_id) is not record or record.reason_code != "NOT_STARTED":
                        continue
                    if any(required not in self._services for required in record.spec.requires):
                        record.state = "failed"
                        record.reason_code = "MISSING_SERVICE"
                        continue
                self._start_one(record, only_unstarted=True)
        for plugin_id in unstarted & enabled.keys():
            record = enabled[plugin_id]
            if record.reason_code in {"API_VERSION_UNSUPPORTED", "SERVICE_CONFLICT", "DEPENDENCY_CYCLE", "MISSING_SERVICE"}:
                self._log_lifecycle(record, "plugin.start.blocked", "插件无法启动", failed=True)

    def _log_lifecycle(self, record: _RuntimeRecord, event: str, message: str, *, failed: bool = False, diagnostics: Mapping[str, object] | None = None) -> None:
        from app.core.runtime_log import log_message

        log_message("error" if failed else "info", message, component="plugin",
            plugin_id=record.spec.plugin_id, plugin_name=record.spec.name,
            fields={**(diagnostics or {}), "event": event, "state": record.state, "reason_code": record.reason_code})

    def _start_one(self, record: _RuntimeRecord, *, only_unstarted: bool = False) -> bool:
        diagnostics: dict[str, object] = {}
        started = self._start_one_impl(record, diagnostics, only_unstarted=only_unstarted)
        if started is False and record.reason_code != "GENERATION_INVALIDATED":
            self._log_lifecycle(record, "plugin.start.failed", "插件启动失败", failed=True, diagnostics=diagnostics)
        return bool(started)

    def _start_one_impl(self, record: _RuntimeRecord, diagnostics: dict[str, object], *, only_unstarted: bool = False) -> bool | None:
        from app.core.diagnostics import exception_diagnostics

        spec = record.spec
        assert spec.plugin_root is not None
        with self._lock:
            if (self._records.get(spec.plugin_id) is not record or not record.spec.enabled
                    or record.spec is not spec or (only_unstarted and record.reason_code != "NOT_STARTED")):
                return None
            if record.process is not None:
                return True if record.state == "active" else None
            if self._closed:
                record.state = "failed"
                record.reason_code = "GENERATION_INVALIDATED"
                return False
            if spec.plugin_id in self._draining_processes:
                # A failed stop retains its exact process until Core shutdown.
                record.state = "failed"
                record.reason_code = "PLUGIN_CLEANUP_FAILED"
                return False
            if self._service_conflict_participants_locked(spec.plugin_id):
                record.state = "failed"
                record.reason_code = "SERVICE_CONFLICT"
                return False
            if any(required not in self._services for required in spec.requires):
                record.state = "failed"
                record.reason_code = "MISSING_SERVICE"
                return False
        try:
            dependency_root = self._dependencies.verified_root(
                spec.plugin_id,
                spec.plugin_root,
                source=spec.source,
            )
        except PluginDependencyError as error:
            diagnostics.update(exception_diagnostics(error, reason_code=error.code, stage="dependencies"))
            with self._lock:
                if (self._records.get(spec.plugin_id) is not record or record.spec is not spec
                        or (only_unstarted and record.reason_code != "NOT_STARTED")):
                    return None
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
            if (self._records.get(spec.plugin_id) is not record or not record.spec.enabled
                    or record.spec is not spec or (only_unstarted and record.reason_code != "NOT_STARTED")
                    or spec.plugin_id in self._draining_processes):
                return None
            if record.process is not None:
                return True if record.state == "active" else None
            if self._service_conflict_participants_locked(spec.plugin_id):
                record.state = "failed"
                record.reason_code = "SERVICE_CONFLICT"
                return False
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
            diagnostics.update(exception_diagnostics(error, reason_code=error.code, stage="initialize"))
            process.close()
            with self._lock:
                if record.process is not process:
                    return None
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
            superseded = self._records.get(spec.plugin_id) is not record or record.process is not process
            missing_dependency = any(
                required not in self._services for required in record.spec.requires
            )
            if (
                self._closed
                or superseded
                or process.pid is None
                or missing_dependency
            ):
                if record.process is process:
                    record.process = None
                    record.pid = None
                if not superseded:
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
            return None if superseded else False
        from app.core.runtime_log import log_event

        log_event(
            "PluginManager",
            "插件已加载",
            {},
            event="plugin.loaded",
            plugin_id=spec.plugin_id,
            plugin_name=spec.name,
            severity="info",
            verbosity=1,
        )
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
                draining = self._draining_processes.get(caller_id)
                if (record is None or record.process is not calling_process) and (
                    draining is None or draining.process is not calling_process
                ):
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
        if name not in {"service.bind", "service.call"}:
            raise PluginApiError("PLUGIN_REQUEST_UNKNOWN", plugin_id=caller_id)
        service_key = payload.get("serviceKey")
        if not isinstance(service_key, str):
            raise PluginApiError("PLUGIN_PROTOCOL_INVALID", plugin_id=caller_id)
        method = payload.get("method")
        args = payload.get("args")
        if name == "service.call" and (not isinstance(method, str) or not isinstance(args, list)):
            raise PluginApiError("PLUGIN_PROTOCOL_INVALID", plugin_id=caller_id)
        identity = payload.get("binding")
        if "binding" in payload and (
            not isinstance(identity, Mapping)
            or set(identity) != {"providerId", "scopeId"}
            or any(not isinstance(value, str) or not value for value in identity.values())
        ):
            raise PluginApiError("PLUGIN_PROTOCOL_INVALID", plugin_id=caller_id)
        try:
            if name == "service.bind":
                with self._lock:
                    if self._closed:
                        raise PluginRuntimeError("GENERATION_INVALIDATED")
                    binding = self._services.get(service_key)
                    if binding is not None and binding.process is None:
                        raise PluginRuntimeError("SERVICE_BINDING_UNSUPPORTED", service_key=service_key)
                    return self.service_identity(service_key)
            return self._route_service_call(
                caller_id, service_key, method, args, expected_identity=identity,
                caller_scope=calling_process.scope_id if calling_process else None,
            )
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
        expected_identity: Mapping[str, str] | None = None,
        caller_scope: str | None = None,
    ) -> object:
        detached_args = deepcopy(list(args))
        with self._lock:
            binding = self._services.get(service_key)
            draining = self._draining_processes.get(caller_id)
            caller_record = self._records.get(caller_id)
            provider_record = self._records.get(binding.provider_id) if binding is not None else None
            draining_log = (binding is not None
                and getattr(binding.host_service, "allow_during_shutdown", False) is True
                and draining is not None)
            # A closing worker must be able to release its own registrations
            # before Core performs the final scope sweep.
            draining_unregister = (binding is not None and binding.host_service is not None
                and draining is not None and method == "unregister"
                and len(detached_args) == 1
                and any(item.service_key == service_key and item.registration_id == detached_args[0]
                        for item in self._host_registrations.get(caller_id, ())))
            # Reverse dependency shutdown keeps a worker's declared services
            # alive until its cleanup finishes. Unrelated or stale callers
            # must still lose access as soon as the generation closes.
            draining_dependency = (
                draining is not None and caller_record is not None
                and service_key in caller_record.spec.requires
                and binding is not None and binding.process is not None
                and provider_record is not None and provider_record.state == "active"
                and provider_record.process is binding.process
            )
            if self._closed and not (draining_log or draining_unregister or draining_dependency):
                raise PluginRuntimeError("GENERATION_INVALIDATED")
            if draining_dependency:
                remaining = draining.deadline - time.monotonic()
                if remaining <= 0:
                    raise PluginRuntimeError("PLUGIN_CALL_TIMEOUT", service_key=service_key)
                timeout = min(self._call_timeout if timeout is None else timeout, remaining)
            if expected_identity is not None and (
                binding is None or binding.process is None
                or {"providerId": binding.provider_id, "scopeId": binding.process.scope_id}
                != expected_identity
            ):
                raise PluginRuntimeError("SERVICE_BINDING_EXPIRED", service_key=service_key)
        if binding is None:
            raise PluginRuntimeError("SERVICE_MISSING", service_key=service_key)
        if method not in binding.exports:
            raise PluginRuntimeError(
                "SERVICE_METHOD_NOT_EXPORTED",
                plugin_id=binding.provider_id,
                service_key=service_key,
            )
        if binding.process is not None:
            with self._lock:
                caller_record = self._records.get(caller_id)
                caller_process = caller_record.process if caller_record else None
                caller_scope = caller_scope or (caller_process.scope_id if caller_process else None)
            result = binding.process.call_service(
                service_key,
                method,
                detached_args,
                timeout=timeout,
                caller_id=caller_id,
                caller_scope=caller_scope,
            )
            if expected_identity is not None:
                with self._lock:
                    if self._closed or self._services.get(service_key) is not binding:
                        raise PluginRuntimeError("SERVICE_BINDING_EXPIRED", service_key=service_key)
            return result
        callback = getattr(binding.host_service, method, None)
        if not callable(callback):
            raise PluginRuntimeError("SERVICE_METHOD_NOT_EXPORTED", service_key=service_key)
        with self._lock:
            caller_record = self._records.get(caller_id)
            spec = caller_record.spec if caller_record else None
            log_metadata = (spec.name, spec.provides) if spec else ("", ())
            caller_process = caller_record.process if caller_record else None
            caller_scope = caller_scope or (caller_process.scope_id if caller_process else None)
        metadata_token = HOST_CALLER_LOG_METADATA.set(log_metadata)
        caller_token = HOST_CALLER.set(caller_id)
        scope_token = HOST_CALLER_SCOPE.set(caller_scope)
        try:
            result = callback(*detached_args)
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
            HOST_CALLER_SCOPE.reset(scope_token)
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

    def _clear_plugin_scope(self, plugin_id: str, scope_id: str | None = None) -> None:
        from app.core.runtime_log import log_message

        def log_cleanup_failure(stage: str, service_key: str) -> None:
            record = self._records.get(plugin_id)
            log_message("warning", "插件资源清理失败", component="plugin",
                plugin_id=plugin_id, plugin_name=record.spec.name if record else plugin_id,
                fields={"event": "plugin.cleanup.failed", "stage": stage, "service_key": service_key})

        with self._lock:
            registrations = list(reversed(self._host_registrations.pop(plugin_id, [])))
            record = self._records.get(plugin_id)
            process = record.process if record else None
            scope_id = scope_id or (process.scope_id if process else None)
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
                    log_cleanup_failure("unregister", registration.service_key)
        for service_key, binding in host_bindings.items():
            callback = getattr(binding.host_service, "revoke_scope", None)
            if callable(callback):
                try:
                    callback(plugin_id)
                except Exception:
                    log_cleanup_failure("revoke_scope", service_key)
        if scope_id is not None:
            self.emit_host_event("sakura.host.scope.closed", {"pluginId": plugin_id, "scopeId": scope_id})

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
        # Service bindings are already unavailable. Finish the process tree
        # before releasing Host files that a surviving native reader may hold.
        with self._lock:
            if record.process is process:
                record.state = "failed"
                record.reason_code = "PLUGIN_PROCESS_EXITING"
        try:
            process.terminate_after_transport_failure()
        except Exception as error:
            with self._lock:
                if record.process is process:
                    record.process = None
                    record.state = "failed"
                    record.reason_code = "PLUGIN_CLEANUP_FAILED"
                    self._draining_processes[plugin_id] = _DrainingProcess(process, time.monotonic())
            from app.core.diagnostics import exception_diagnostics
            from app.core.runtime_log import log_event

            log_event(
                "PluginManager", "插件进程未能完成清理", {
                    "plugin_id": plugin_id,
                    **exception_diagnostics(error, reason_code="PLUGIN_CLEANUP_FAILED", stage="plugin.cleanup"),
                }, event="plugin.cleanup.failed", severity="error",
            )
            return
        # Cleanup completion can release a concurrent reload. Serialize only
        # this tail with lifecycle changes, then recheck who owns the scope.
        # Actual process cleanup stays outside this lock so reload can wait
        # for it without blocking the cleanup owner.
        with self._operation_lock:
            with self._lock:
                if self._closed or record.process is not process:
                    return
            self._clear_plugin_scope(plugin_id)
            with self._lock:
                record.process = None
                record.pid = None
                record.reason_code = "PLUGIN_PROCESS_EXITED"
            self._log_lifecycle(record, "plugin.process.exited", "插件进程意外退出", failed=True)
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
            already_draining = process is None
            if already_draining:
                draining = self._draining_processes.get(plugin_id)
                process = draining.process if draining is not None else None
            else:
                deadline = time.monotonic() + CLOSE_TIMEOUT_SECONDS if deadline is None else deadline
                self._draining_processes[plugin_id] = _DrainingProcess(process, deadline)
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
                if already_draining:
                    process.wait_for_cleanup()
                else:
                    process.close(deadline=deadline)
            except BaseException:
                with self._lock:
                    record.state = "failed"
                    record.reason_code = "PLUGIN_CLEANUP_FAILED"
                raise
            else:
                with self._lock:
                    draining = self._draining_processes.get(plugin_id)
                    if draining is not None and draining.process is process:
                        self._draining_processes.pop(plugin_id, None)
        self._clear_plugin_scope(plugin_id, process.scope_id if process else None)
        if process is not None or failed:
            self._log_lifecycle(record, "plugin.stopped", "插件已停止", failed=failed)


__all__ = ["PluginRuntimeError", "PluginRuntimeManager"]
