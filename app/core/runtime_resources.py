"""Runtime v2 pure-Python lifecycle primitives."""

from __future__ import annotations

import asyncio
import inspect
import subprocess
import threading
import time
from collections.abc import Callable
from concurrent.futures import CancelledError, Future
from dataclasses import dataclass
from enum import Enum
from typing import Any, Protocol, runtime_checkable

from app.core.runtime_log import diagnostic_attributes, log_event

DEFAULT_THREAD_SHUTDOWN_WAIT_MS = 1_000
DEFAULT_PROCESS_TERMINATE_TIMEOUT_S = 5


class AsyncSubmitTimeout(TimeoutError):
    """Async submission timed out after cancellation was requested."""

    def __init__(self, message: str, *, cancel_settled: bool) -> None:
        super().__init__(message)
        self.cancel_settled = cancel_settled


class ResourceState(str, Enum):
    """Lifecycle state shared by non-Qt managed resources."""

    NEW = "new"
    STARTING = "starting"
    READY = "ready"
    DEGRADED = "degraded"
    STOPPING = "stopping"
    STOPPED = "stopped"
    FAILED = "failed"


@runtime_checkable
class StoppableResource(Protocol):
    """Minimum contract accepted by :class:`ResourceRegistry`."""

    def stop(self, timeout_ms: int = ...) -> bool:
        """Request a bounded stop and report whether it completed cleanly."""


@runtime_checkable
class ManagedResource(StoppableResource, Protocol):
    """Full runtime contract for non-Qt managed resources."""

    def is_running(self) -> bool:
        """Return whether the resource is still running."""

    def health(self) -> ResourceState:
        """Return the current resource health state."""


class _ResourceOwner(Protocol):
    """Duck-typed owner shared by the registry and the Qt adapter."""

    def _register(
        self,
        resource: StoppableResource,
        *,
        label: str = "",
        shutdown_order: int = 0,
    ) -> None: ...

    def _unregister(self, resource: StoppableResource) -> None: ...

    def _keep_lingering_thread(
        self,
        thread: threading.Thread,
        label: str,
    ) -> None: ...


@dataclass
class _ResourceEntry:
    resource: StoppableResource
    label: str
    shutdown_order: int


@runtime_checkable
class ProcessHandle(Protocol):
    """Process handle implemented by ``subprocess.Popen`` and adopted handles."""

    pid: int

    def poll(self) -> int | None:
        """Return the exit code, or ``None`` while running."""

    def terminate(self) -> None:
        """Request process termination."""

    def kill(self) -> None:
        """Force process termination."""

    def wait(self, timeout: float | None = None) -> int | None:
        """Wait for the process to exit."""


def _default_terminate_process(process: ProcessHandle, timeout_s: int) -> None:
    process.terminate()
    try:
        process.wait(timeout=timeout_s)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=timeout_s)


class ThreadResource:
    """Manage one plain Python thread and its cancellation callback."""

    def __init__(
        self,
        manager: _ResourceOwner,
        *,
        cancel: Callable[[], None] | None = None,
        label: str = "",
    ) -> None:
        self._manager = manager
        self._cancel = cancel
        self.label = label
        self.thread: threading.Thread | None = None
        self.state = ResourceState.NEW

    def track(self, thread: threading.Thread) -> None:
        self.thread = thread
        self.state = ResourceState.READY

    def is_running(self) -> bool:
        thread = self.thread
        return bool(thread is not None and thread.is_alive())

    def stop(self, timeout_ms: int = DEFAULT_THREAD_SHUTDOWN_WAIT_MS) -> bool:
        self.state = ResourceState.STOPPING
        if self._cancel is not None:
            try:
                self._cancel()
            except Exception as exc:  # noqa: BLE001
                log_event(
                    "ResourceManager",
                    "线程取消回调异常",
                    {
                        "thread": self.label,
                        **diagnostic_attributes(
                            exc,
                            reason_code="THREAD_CANCEL_CALLBACK_FAILED",
                            stage="thread_cancel",
                        ),
                    },
                )
        thread = self.thread
        if thread is None or not thread.is_alive():
            self.state = ResourceState.STOPPED
            self.thread = None
            self._manager._unregister(self)
            return True
        thread.join(timeout_ms / 1000)
        if thread.is_alive():
            log_event(
                "ResourceManager",
                "Python 线程未在退出等待时间内结束，转后台自然结束",
                {"thread": self.label, "wait_ms": timeout_ms},
            )
            self._manager._keep_lingering_thread(thread, self.label)
            self.thread = None
            self._manager._unregister(self)
            return False
        self.state = ResourceState.STOPPED
        self.thread = None
        self._manager._unregister(self)
        return True


class ThreadGroupResource:
    """Manage a reusable group of concurrently running Python threads."""

    def __init__(
        self,
        manager: _ResourceOwner,
        *,
        cancel: Callable[[], None] | None = None,
        label: str = "",
    ) -> None:
        self._manager = manager
        self._cancel = cancel
        self.label = label
        self._threads: set[threading.Thread] = set()
        self._threads_lock = threading.Lock()
        self.state = ResourceState.NEW

    def spawn(
        self,
        target: Callable[[], None],
        *,
        name: str,
        daemon: bool = False,
    ) -> threading.Thread | None:
        def run_managed() -> None:
            try:
                target()
            finally:
                self._on_thread_done(threading.current_thread())

        thread = threading.Thread(target=run_managed, name=name, daemon=daemon)
        with self._threads_lock:
            if self.state in (ResourceState.STOPPING, ResourceState.STOPPED):
                return None
            self._threads.add(thread)
            try:
                thread.start()
            except Exception:
                self._threads.discard(thread)
                raise
            self.state = ResourceState.READY
        return thread

    def is_running(self) -> bool:
        with self._threads_lock:
            return any(thread.is_alive() for thread in self._threads)

    def stop(
        self,
        timeout_ms: int | None = DEFAULT_THREAD_SHUTDOWN_WAIT_MS,
    ) -> bool:
        with self._threads_lock:
            if self.state is ResourceState.STOPPED:
                return True
            self.state = ResourceState.STOPPING

        if self._cancel is not None:
            try:
                self._cancel()
            except Exception as exc:  # noqa: BLE001
                log_event(
                    "ResourceManager",
                    "线程组取消回调异常",
                    {
                        "thread_group": self.label,
                        **diagnostic_attributes(
                            exc,
                            reason_code="THREAD_GROUP_CANCEL_CALLBACK_FAILED",
                            stage="thread_group_cancel",
                        ),
                    },
                )

        deadline = (
            None
            if timeout_ms is None
            else time.monotonic() + max(0, timeout_ms) / 1000
        )
        current = threading.current_thread()
        while True:
            with self._threads_lock:
                threads = tuple(self._threads)
            if not threads:
                self._finalize_stop()
                return True

            for thread in threads:
                if thread is current:
                    continue
                remaining = (
                    None
                    if deadline is None
                    else max(0.0, deadline - time.monotonic())
                )
                thread.join(remaining)

            with self._threads_lock:
                alive = tuple(thread for thread in self._threads if thread.is_alive())
            if not alive:
                self._finalize_stop()
                return True
            if current in alive or (
                deadline is not None and time.monotonic() >= deadline
            ):
                for thread in alive:
                    self._manager._keep_lingering_thread(
                        thread,
                        f"{self.label}:{thread.name}" if self.label else thread.name,
                    )
                self._manager._unregister(self)
                log_event(
                    "ResourceManager",
                    "Python 线程组未在退出等待时间内结束，转后台自然结束",
                    {
                        "thread_group": self.label,
                        "wait_ms": timeout_ms,
                        "remaining": len(alive),
                    },
                )
                return False

    def _on_thread_done(self, thread: threading.Thread) -> None:
        with self._threads_lock:
            self._threads.discard(thread)
            if not self._threads and self.state is ResourceState.STOPPING:
                self.state = ResourceState.STOPPED

    def _finalize_stop(self) -> None:
        with self._threads_lock:
            self.state = ResourceState.STOPPED
        self._manager._unregister(self)


class ProcessResource:
    """Manage an adopted local process handle."""

    def __init__(
        self,
        manager: _ResourceOwner,
        process: ProcessHandle | None = None,
        *,
        terminator: Callable[[ProcessHandle, int], None] | None = None,
        restart_factory: Callable[[], ProcessHandle | None] | None = None,
        terminate_timeout_s: int = DEFAULT_PROCESS_TERMINATE_TIMEOUT_S,
        label: str = "",
    ) -> None:
        self._manager = manager
        self.process = process
        self._terminator = terminator
        self._restart_factory = restart_factory
        self._terminate_timeout_s = terminate_timeout_s
        self.label = label
        self.state = ResourceState.READY if process is not None else ResourceState.NEW

    def attach(self, process: ProcessHandle | None) -> None:
        self.process = process
        self.state = ResourceState.READY if process is not None else ResourceState.NEW

    def is_running(self) -> bool:
        process = self.process
        if process is None:
            return False
        try:
            return process.poll() is None
        except Exception:  # noqa: BLE001
            return False

    def health(self) -> ResourceState:
        if self.state in (
            ResourceState.STOPPING,
            ResourceState.STOPPED,
            ResourceState.FAILED,
        ):
            return self.state
        return ResourceState.READY if self.is_running() else ResourceState.STOPPED

    def detach(self) -> ProcessHandle | None:
        process = self.process
        self.process = None
        self.state = ResourceState.STOPPED
        self._manager._unregister(self)
        return process

    def _terminate_current(self) -> None:
        process = self.process
        if process is None:
            return
        try:
            if process.poll() is not None:
                return
        except Exception:  # noqa: BLE001
            return
        log_event(
            "ResourceManager",
            "终止本地子进程",
            {"process": self.label, "pid": getattr(process, "pid", None)},
        )
        try:
            if self._terminator is not None:
                self._terminator(process, self._terminate_timeout_s)
            else:
                _default_terminate_process(process, self._terminate_timeout_s)
        except Exception as exc:  # noqa: BLE001
            log_event(
                "ResourceManager",
                "本地子进程正常终止失败，尝试强制结束",
                {
                    "process": self.label,
                    **diagnostic_attributes(
                        exc,
                        reason_code="PROCESS_TERMINATE_FAILED",
                        stage="process_terminate",
                    ),
                },
            )
            try:
                process.kill()
                process.wait(timeout=self._terminate_timeout_s)
            except Exception as kill_exc:  # noqa: BLE001
                log_event(
                    "ResourceManager",
                    "本地子进程强制结束失败",
                    {
                        "process": self.label,
                        **diagnostic_attributes(
                            kill_exc,
                            reason_code="PROCESS_KILL_FAILED",
                            stage="process_kill",
                        ),
                    },
                )

    def restart(self) -> bool:
        self.state = ResourceState.STARTING
        self._terminate_current()
        if self._restart_factory is None:
            self.process = None
            self.state = ResourceState.NEW
            return True
        try:
            self.process = self._restart_factory()
        except Exception as exc:  # noqa: BLE001
            log_event(
                "ResourceManager",
                "本地子进程重启失败",
                {
                    "process": self.label,
                    **diagnostic_attributes(
                        exc,
                        reason_code="PROCESS_RESTART_FAILED",
                        stage="process_restart",
                    ),
                },
            )
            self.process = None
            self.state = ResourceState.FAILED
            return False
        self.state = (
            ResourceState.READY if self.process is not None else ResourceState.NEW
        )
        return self.process is not None

    def stop(self, timeout_ms: int = DEFAULT_THREAD_SHUTDOWN_WAIT_MS) -> bool:
        _ = timeout_ms
        self.state = ResourceState.STOPPING
        if self.process is None:
            self.state = ResourceState.STOPPED
            self._manager._unregister(self)
            return True
        self._terminate_current()
        self.process = None
        self.state = ResourceState.STOPPED
        self._manager._unregister(self)
        return True


class ServiceResource:
    """Bring an existing service object's stop callbacks into the registry."""

    def __init__(
        self,
        manager: _ResourceOwner,
        *,
        stop: Callable[[], Any] | None = None,
        stop_with_timeout: Callable[[int], Any] | None = None,
        is_running: Callable[[], bool] | None = None,
        health: Callable[[], ResourceState] | None = None,
        label: str = "",
    ) -> None:
        self._manager = manager
        self._stop = stop
        self._stop_with_timeout = stop_with_timeout
        self._is_running = is_running
        self._health = health
        self.label = label
        self.state = ResourceState.READY

    def is_running(self) -> bool:
        if self.state in (ResourceState.STOPPED, ResourceState.FAILED):
            return False
        if self._is_running is None:
            return self.state not in (ResourceState.STOPPING, ResourceState.STOPPED)
        try:
            return bool(self._is_running())
        except Exception as exc:  # noqa: BLE001
            log_event(
                "ResourceManager",
                "服务运行态查询失败",
                {
                    "service": self.label,
                    **diagnostic_attributes(
                        exc,
                        reason_code="SERVICE_STATE_QUERY_FAILED",
                        stage="service_state",
                    ),
                },
            )
            return False

    def health(self) -> ResourceState:
        if self._health is not None:
            try:
                return self._health()
            except Exception as exc:  # noqa: BLE001
                log_event(
                    "ResourceManager",
                    "服务健康检查失败",
                    {
                        "service": self.label,
                        **diagnostic_attributes(
                            exc,
                            reason_code="SERVICE_HEALTH_CHECK_FAILED",
                            stage="service_health",
                        ),
                    },
                )
                return ResourceState.DEGRADED
        if self.state in (
            ResourceState.STOPPING,
            ResourceState.STOPPED,
            ResourceState.FAILED,
        ):
            return self.state
        return ResourceState.READY if self.is_running() else ResourceState.STOPPED

    def stop(self, timeout_ms: int = DEFAULT_THREAD_SHUTDOWN_WAIT_MS) -> bool:
        if self.state in (ResourceState.STOPPING, ResourceState.STOPPED):
            return True
        self.state = ResourceState.STOPPING
        clean = True
        try:
            if self._stop_with_timeout is not None:
                result = self._stop_with_timeout(timeout_ms)
            elif self._stop is not None:
                result = self._stop()
            else:
                result = None
            if isinstance(result, bool):
                clean = result
        except Exception as exc:  # noqa: BLE001
            clean = False
            self.state = ResourceState.FAILED
            log_event(
                "ResourceManager",
                "服务关闭失败",
                {
                    "service": self.label,
                    **diagnostic_attributes(
                        exc,
                        reason_code="SERVICE_CLOSE_FAILED",
                        stage="service_close",
                    ),
                },
            )
        else:
            self.state = ResourceState.STOPPED if clean else ResourceState.DEGRADED
        finally:
            self._manager._unregister(self)
        return clean

    def detach(self) -> None:
        self.state = ResourceState.STOPPED
        self._manager._unregister(self)


class AsyncLoopResource:
    """Manage an asyncio event loop running on its own daemon thread."""

    def __init__(
        self,
        manager: _ResourceOwner,
        *,
        loop_factory: Callable[[], asyncio.AbstractEventLoop] | None = None,
        label: str = "",
    ) -> None:
        self._manager = manager
        self._loop_factory = loop_factory or asyncio.new_event_loop
        self.label = label
        self._lock = threading.Lock()
        self._ready = threading.Event()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._thread_name = label or "async-loop"
        self._daemon = True
        self.state = ResourceState.NEW

    @property
    def loop(self) -> asyncio.AbstractEventLoop | None:
        with self._lock:
            return self._loop

    @property
    def thread(self) -> threading.Thread | None:
        with self._lock:
            return self._thread

    def start(
        self,
        *,
        name: str | None = None,
        daemon: bool = True,
        ready_timeout_s: float = 5.0,
    ) -> asyncio.AbstractEventLoop:
        with self._lock:
            if (
                self._thread is not None
                and self._thread.is_alive()
                and self._loop is not None
            ):
                return self._loop
            self._thread_name = name or self._thread_name
            self._daemon = daemon
            self._ready.clear()
            self.state = ResourceState.STARTING
            thread = threading.Thread(
                target=self._run_loop,
                name=self._thread_name,
                daemon=daemon,
            )
            self._thread = thread
            thread.start()
        if not self._ready.wait(timeout=ready_timeout_s):
            self.state = ResourceState.FAILED
            raise TimeoutError(
                f"asyncio 事件循环启动超时：{self.label or self._thread_name}"
            )
        loop = self.loop
        if loop is None:
            self.state = ResourceState.FAILED
            raise RuntimeError(
                f"asyncio 事件循环启动失败：{self.label or self._thread_name}"
            )
        self.state = ResourceState.READY
        return loop

    def submit(self, coro: Any, *, timeout: float) -> Any:
        loop = self.loop
        if loop is None or self.state in (
            ResourceState.STOPPING,
            ResourceState.STOPPED,
        ):
            _close_awaitable_quietly(coro)
            raise RuntimeError("asyncio 事件循环尚未运行。")
        try:
            future: Future[Any] = asyncio.run_coroutine_threadsafe(coro, loop)
        except RuntimeError:
            _close_awaitable_quietly(coro)
            raise
        try:
            return future.result(timeout=timeout)
        except TimeoutError as exc:
            future.cancel()
            cancel_settled = False
            try:
                future.result(timeout=min(0.5, max(0.05, timeout)))
            except CancelledError:
                cancel_settled = True
            except TimeoutError:
                cancel_settled = False
            except BaseException:
                cancel_settled = True
            raise AsyncSubmitTimeout(
                f"异步操作超时：{self.label or self._thread_name}",
                cancel_settled=cancel_settled,
            ) from exc

    def is_running(self) -> bool:
        thread = self.thread
        return bool(thread is not None and thread.is_alive())

    def health(self) -> ResourceState:
        if self.state in (
            ResourceState.STOPPING,
            ResourceState.STOPPED,
            ResourceState.FAILED,
        ):
            return self.state
        loop = self.loop
        return (
            ResourceState.READY
            if self.is_running() and loop is not None and not loop.is_closed()
            else ResourceState.STOPPED
        )

    def restart(self, *, reason: str = "") -> bool:
        if reason:
            log_event(
                "ResourceManager",
                "重启 asyncio 事件循环",
                {
                    "loop": self.label,
                    "reason_code": str(reason),
                    "stage": "event_loop_restart",
                    "error_type": "EventLoopRestart",
                    "diagnostic": "asyncio 事件循环需要重启",
                },
            )
        self.stop(DEFAULT_THREAD_SHUTDOWN_WAIT_MS)
        self._manager._register(self, label=self.label, shutdown_order=900)
        self.start(name=self._thread_name, daemon=self._daemon)
        return self.is_running()

    def stop(self, timeout_ms: int = DEFAULT_THREAD_SHUTDOWN_WAIT_MS) -> bool:
        with self._lock:
            if self.state is ResourceState.STOPPED:
                return True
            already_stopping = self.state is ResourceState.STOPPING
            self.state = ResourceState.STOPPING
            loop = self._loop
            thread = self._thread
        if loop is None or thread is None:
            self._finalize_stop()
            return True
        if not already_stopping:
            try:
                loop.call_soon_threadsafe(loop.stop)
            except RuntimeError:
                self._finalize_stop()
                return True
        if thread is threading.current_thread():
            self._manager._keep_lingering_thread(thread, self.label or thread.name)
            self._manager._unregister(self)
            return False
        thread.join(timeout_ms / 1000)
        if thread.is_alive():
            self._manager._keep_lingering_thread(thread, self.label or thread.name)
            self._manager._unregister(self)
            log_event(
                "ResourceManager",
                "asyncio 事件循环线程未在退出等待时间内结束",
                {"loop": self.label, "wait_ms": timeout_ms},
            )
            return False
        self._finalize_stop()
        return True

    def _run_loop(self) -> None:
        loop = self._loop_factory()
        with self._lock:
            self._loop = loop
        asyncio.set_event_loop(loop)
        self._ready.set()
        try:
            loop.run_forever()
        finally:
            pending = asyncio.all_tasks(loop)
            for task in pending:
                task.cancel()
            if pending:
                cleanup = asyncio.gather(*pending, return_exceptions=True)
                cleanup.add_done_callback(lambda _future: loop.stop())
                while not cleanup.done():
                    loop.run_forever()
            loop.close()
            with self._lock:
                self._loop = None
            if self.state is not ResourceState.STOPPING:
                self._finalize_stop()

    def _finalize_stop(self) -> None:
        with self._lock:
            self._thread = None
            self._loop = None
            self.state = ResourceState.STOPPED
        self._manager._unregister(self)


def _close_awaitable_quietly(value: Any) -> None:
    if not inspect.iscoroutine(value):
        return
    try:
        value.close()
    except RuntimeError:
        pass


class ResourceRegistry:
    """Thread-safe, Qt-free application resource registry."""

    def __init__(self) -> None:
        self._entries: list[_ResourceEntry] = []
        self._lock = threading.RLock()
        self._lingering_threads: list[threading.Thread] = []

    @property
    def _resources(self) -> list[StoppableResource]:
        with self._lock:
            return [entry.resource for entry in self._entries]

    def stop_all(self, timeout_ms: int = DEFAULT_THREAD_SHUTDOWN_WAIT_MS) -> None:
        with self._lock:
            entries = tuple(
                sorted(
                    self._entries,
                    key=lambda entry: entry.shutdown_order,
                    reverse=True,
                )
            )
        for entry in entries:
            try:
                entry.resource.stop(timeout_ms)
            except Exception as exc:  # noqa: BLE001
                log_event(
                    "ResourceManager",
                    "受管资源关闭异常",
                    {
                        "resource": entry.label,
                        **diagnostic_attributes(
                            exc,
                            reason_code="RESOURCE_CLOSE_FAILED",
                            stage="resource_close",
                        ),
                    },
                )
            finally:
                self._unregister(entry.resource)

    def track_service(
        self,
        *,
        stop: Callable[[], Any] | None = None,
        stop_with_timeout: Callable[[int], Any] | None = None,
        is_running: Callable[[], bool] | None = None,
        health: Callable[[], ResourceState] | None = None,
        label: str = "",
        shutdown_order: int = 0,
        register: bool = True,
    ) -> ServiceResource:
        resource = ServiceResource(
            self,
            stop=stop,
            stop_with_timeout=stop_with_timeout,
            is_running=is_running,
            health=health,
            label=label,
        )
        if register:
            self._register(resource, label=label, shutdown_order=shutdown_order)
        return resource

    def track_async_loop(
        self,
        *,
        loop_factory: Callable[[], asyncio.AbstractEventLoop] | None = None,
        label: str = "",
        shutdown_order: int = 900,
        register: bool = True,
    ) -> AsyncLoopResource:
        resource = AsyncLoopResource(self, loop_factory=loop_factory, label=label)
        if register:
            self._register(resource, label=label, shutdown_order=shutdown_order)
        return resource

    def track_python_thread(
        self,
        *,
        cancel: Callable[[], None] | None = None,
        label: str = "",
        shutdown_order: int = 1000,
        register: bool = True,
    ) -> ThreadResource:
        resource = ThreadResource(self, cancel=cancel, label=label)
        if register:
            self._register(resource, label=label, shutdown_order=shutdown_order)
        return resource

    def track_thread_group(
        self,
        *,
        cancel: Callable[[], None] | None = None,
        label: str = "",
        shutdown_order: int = 1000,
        register: bool = True,
    ) -> ThreadGroupResource:
        resource = ThreadGroupResource(self, cancel=cancel, label=label)
        if register:
            self._register(resource, label=label, shutdown_order=shutdown_order)
        return resource

    def adopt_process(
        self,
        process: ProcessHandle | None = None,
        *,
        terminator: Callable[[ProcessHandle, int], None] | None = None,
        restart_factory: Callable[[], ProcessHandle | None] | None = None,
        terminate_timeout_s: int = DEFAULT_PROCESS_TERMINATE_TIMEOUT_S,
        label: str = "",
        shutdown_order: int = 800,
        register: bool = True,
    ) -> ProcessResource:
        resource = ProcessResource(
            self,
            process,
            terminator=terminator,
            restart_factory=restart_factory,
            terminate_timeout_s=terminate_timeout_s,
            label=label,
        )
        if register:
            self._register(resource, label=label, shutdown_order=shutdown_order)
        return resource

    def _register(
        self,
        resource: StoppableResource,
        *,
        label: str = "",
        shutdown_order: int = 0,
    ) -> None:
        with self._lock:
            for entry in self._entries:
                if entry.resource is resource:
                    entry.label = label or entry.label
                    entry.shutdown_order = shutdown_order
                    return
            self._entries.append(_ResourceEntry(resource, label, shutdown_order))

    def _unregister(self, resource: StoppableResource) -> None:
        with self._lock:
            self._entries = [
                entry for entry in self._entries if entry.resource is not resource
            ]

    def _keep_lingering_thread(
        self,
        thread: threading.Thread,
        label: str,
    ) -> None:
        with self._lock:
            if thread in self._lingering_threads:
                return
            self._lingering_threads.append(thread)
        log_event(
            "ResourceManager",
            "登记 lingering Python 线程",
            {"thread": label},
        )


__all__ = [
    "AsyncLoopResource",
    "AsyncSubmitTimeout",
    "DEFAULT_PROCESS_TERMINATE_TIMEOUT_S",
    "DEFAULT_THREAD_SHUTDOWN_WAIT_MS",
    "ManagedResource",
    "ProcessHandle",
    "ProcessResource",
    "ResourceRegistry",
    "ResourceState",
    "ServiceResource",
    "StoppableResource",
    "ThreadGroupResource",
    "ThreadResource",
]
