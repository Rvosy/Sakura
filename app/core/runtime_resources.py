"""Runtime v2 pure-Python lifecycle primitives."""

from __future__ import annotations

import asyncio
import inspect
import threading
from collections.abc import Callable
from concurrent.futures import CancelledError, Future
from dataclasses import dataclass
from enum import Enum
from typing import Any, Protocol, runtime_checkable

from app.core.runtime_log import diagnostic_attributes, log_event

DEFAULT_THREAD_SHUTDOWN_WAIT_MS = 1_000


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


@dataclass
class _ResourceEntry:
    resource: StoppableResource
    label: str
    shutdown_order: int


class AsyncLoopResource:
    """Manage an asyncio event loop running on its own daemon thread."""

    def __init__(
        self,
        manager: ResourceRegistry,
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
    "DEFAULT_THREAD_SHUTDOWN_WAIT_MS",
    "ResourceRegistry",
    "ResourceState",
    "StoppableResource",
]
