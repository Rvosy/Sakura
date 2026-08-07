"""Qt adapter for the application-wide runtime resource registry.

Pure Python lifecycle primitives live in :mod:`app.core.runtime_resources` so
the bundled Core and Memory can use them without importing Qt.  This module
keeps the UI-thread ``ResourceManager``/``QtWorkerResource`` adapter and
re-exports the historical public names for existing Qt-side callers.
"""

from __future__ import annotations

import asyncio
import threading
import time
from collections.abc import Callable, Sequence
from typing import Any

from PySide6.QtCore import QObject, QThread, QTimer

from app.core.runtime_log import log_event
from app.core.runtime_resources import (
    AsyncLoopResource,
    AsyncSubmitTimeout,
    DEFAULT_PROCESS_TERMINATE_TIMEOUT_S,
    DEFAULT_THREAD_SHUTDOWN_WAIT_MS,
    ManagedResource,
    ProcessHandle,
    ProcessResource,
    ResourceRegistry,
    ResourceState,
    ServiceResource,
    StoppableResource,
    ThreadGroupResource,
    ThreadResource,
)

WRAPPER_RETENTION_MS = 1_000
SignalBinding = tuple[Any, Callable[..., Any]]


def _delete_later_quietly(obj: QObject | None) -> None:
    if obj is None:
        return
    try:
        obj.deleteLater()
    except RuntimeError:
        pass


class QtWorkerResource:
    """Manage one ``QThread`` and its ``QObject`` worker."""

    def __init__(
        self,
        manager: ResourceManager,
        thread: QThread,
        worker: QObject,
        *,
        owner: QObject | None = None,
        thread_attr: str | None = None,
        worker_attr: str | None = None,
        on_finished: Callable[[], None] | None = None,
        label: str = "",
    ) -> None:
        self._manager = manager
        self.thread: QThread | None = thread
        self.worker: QObject | None = worker
        self._owner = owner
        self._thread_attr = thread_attr
        self._worker_attr = worker_attr
        self._on_finished = on_finished
        self.label = label
        self._finalized = False

    def is_running(self) -> bool:
        thread = self.thread
        if thread is None:
            return False
        try:
            return bool(thread.isRunning())
        except RuntimeError:
            return False

    def stop(self, timeout_ms: int = DEFAULT_THREAD_SHUTDOWN_WAIT_MS) -> bool:
        clean = self._manager._stop_thread_mechanics(
            self.thread,
            self.worker,
            label=self.label,
            timeout_ms=timeout_ms,
        )
        if clean:
            self._finalize(run_business=True)
            return True
        self._null_owner_attrs()
        self._finalized = True
        self._manager._unregister(self)
        self.thread = None
        self.worker = None
        return False

    def _on_thread_finished(self) -> None:
        self._finalize(run_business=True)

    def _finalize(self, *, run_business: bool) -> None:
        if self._finalized:
            return
        self._finalized = True
        thread, worker = self.thread, self.worker
        self._manager._retire_qobjects(worker, thread)
        self._null_owner_attrs()
        self.thread = None
        self.worker = None
        self._manager._unregister(self)
        if run_business and self._on_finished is not None:
            try:
                self._on_finished()
            except RuntimeError:
                pass

    def _null_owner_attrs(self) -> None:
        owner = self._owner
        if owner is None:
            return
        if self._worker_attr and getattr(owner, self._worker_attr, None) is self.worker:
            setattr(owner, self._worker_attr, None)
        if self._thread_attr and getattr(owner, self._thread_attr, None) is self.thread:
            setattr(owner, self._thread_attr, None)


class ResourceManager(QObject):
    """UI-thread adapter for Qt workers and a shared ``ResourceRegistry``."""

    def __init__(
        self,
        parent: QObject | None = None,
        *,
        registry: ResourceRegistry | None = None,
    ) -> None:
        super().__init__(parent)
        self._registry = registry if registry is not None else ResourceRegistry()
        self._lingering: list[tuple[QThread, QObject | None]] = []
        self._retired_wrappers: list[QObject] = []

    @property
    def registry(self) -> ResourceRegistry:
        return self._registry

    def spawn_qt_worker(
        self,
        worker: QObject,
        *,
        parent: QObject,
        owner: QObject,
        thread_attr: str,
        worker_attr: str,
        signal_bindings: Sequence[SignalBinding] = (),
        quit_on: Sequence[Any] = (),
        on_finished: Callable[[], None] | None = None,
        run_slot: Callable[[], None] | None = None,
        register: bool = True,
        label: str = "",
    ) -> QtWorkerResource:
        thread = QThread(parent)
        worker.moveToThread(thread)
        thread.started.connect(run_slot if run_slot is not None else worker.run)
        for signal, slot in signal_bindings:
            signal.connect(slot)
        for signal in quit_on:
            signal.connect(thread.quit)

        resource = QtWorkerResource(
            self,
            thread,
            worker,
            owner=owner,
            thread_attr=thread_attr,
            worker_attr=worker_attr,
            on_finished=on_finished,
            label=label or thread_attr,
        )
        thread.finished.connect(resource._on_thread_finished)

        setattr(owner, thread_attr, thread)
        setattr(owner, worker_attr, worker)
        if register:
            self._register(resource, label=label or thread_attr, shutdown_order=1000)
        thread.start()
        return resource

    def track_python_thread(
        self,
        *,
        cancel: Callable[[], None] | None = None,
        label: str = "",
        register: bool = True,
    ) -> ThreadResource:
        resource = ThreadResource(self, cancel=cancel, label=label)
        if register:
            self._register(resource, label=label, shutdown_order=1000)
        return resource

    def track_thread_group(
        self,
        *,
        cancel: Callable[[], None] | None = None,
        label: str = "",
        register: bool = True,
    ) -> ThreadGroupResource:
        resource = ThreadGroupResource(self, cancel=cancel, label=label)
        if register:
            self._register(resource, label=label, shutdown_order=1000)
        return resource

    def adopt_process(
        self,
        process: ProcessHandle | None = None,
        *,
        terminator: Callable[[ProcessHandle, int], None] | None = None,
        restart_factory: Callable[[], ProcessHandle | None] | None = None,
        terminate_timeout_s: int = DEFAULT_PROCESS_TERMINATE_TIMEOUT_S,
        label: str = "",
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
            self._register(resource, label=label, shutdown_order=800)
        return resource

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
        return self._registry.track_service(
            stop=stop,
            stop_with_timeout=stop_with_timeout,
            is_running=is_running,
            health=health,
            label=label,
            shutdown_order=shutdown_order,
            register=register,
        )

    def track_async_loop(
        self,
        *,
        loop_factory: Callable[[], asyncio.AbstractEventLoop] | None = None,
        label: str = "",
        shutdown_order: int = 900,
        register: bool = True,
    ) -> AsyncLoopResource:
        return self._registry.track_async_loop(
            loop_factory=loop_factory,
            label=label,
            shutdown_order=shutdown_order,
            register=register,
        )

    def stop_all(self, timeout_ms: int = DEFAULT_THREAD_SHUTDOWN_WAIT_MS) -> None:
        self._registry.stop_all(timeout_ms)

    def wait_for_lingering_qthreads(self, timeout_ms: int) -> bool:
        deadline = time.monotonic() + max(0, timeout_ms) / 1000
        while self._lingering:
            thread, _worker = self._lingering[0]
            remaining_ms = max(0, int((deadline - time.monotonic()) * 1000))
            try:
                if thread.isRunning() and (
                    remaining_ms <= 0 or not thread.wait(remaining_ms)
                ):
                    log_event(
                        "ResourceManager",
                        "应用退出前仍有后台线程未结束",
                        {"remaining": len(self._lingering), "wait_ms": timeout_ms},
                    )
                    return False
            except RuntimeError:
                pass
            self._release_lingering(thread)
        return True

    def _register(
        self,
        resource: StoppableResource,
        *,
        label: str = "",
        shutdown_order: int = 1000,
    ) -> None:
        self._registry._register(
            resource,
            label=label,
            shutdown_order=shutdown_order,
        )

    def _unregister(self, resource: StoppableResource) -> None:
        self._registry._unregister(resource)

    def _stop_thread_mechanics(
        self,
        thread: QThread | None,
        worker: QObject | None,
        *,
        label: str,
        timeout_ms: int,
    ) -> bool:
        if thread is None:
            return True
        log_event("ResourceManager", "准备关闭后台线程", {"thread": label})
        try:
            cancel = getattr(worker, "cancel", None)
            if callable(cancel):
                cancel()
            thread.requestInterruption()
            if thread.isRunning():
                thread.quit()
                if not thread.wait(timeout_ms):
                    log_event(
                        "ResourceManager",
                        "后台线程未在退出等待时间内结束",
                        {"thread": label, "wait_ms": timeout_ms},
                    )
                    self._keep_lingering(thread, worker)
                    return False
        except RuntimeError as exc:
            log_event(
                "ResourceManager",
                "关闭后台线程失败",
                {"thread": label, "error": str(exc)},
            )
        return True

    def _keep_lingering(self, thread: QThread, worker: QObject | None) -> None:
        if any(item_thread is thread for item_thread, _worker in self._lingering):
            return
        try:
            thread.setParent(None)
        except RuntimeError as exc:
            log_event(
                "ResourceManager",
                "后台线程脱离窗口父对象失败",
                {"error": str(exc)},
            )
        self._lingering.append((thread, worker))
        try:
            thread.finished.connect(self._release_finished_lingering)
        except RuntimeError:
            self._release_lingering(thread)

    def _keep_lingering_thread(
        self,
        thread: threading.Thread,
        label: str,
    ) -> None:
        self._registry._keep_lingering_thread(thread, label)

    def _release_finished_lingering(self) -> None:
        if isinstance(thread := self.sender(), QThread):
            self._release_lingering(thread)

    def _release_lingering(self, thread: QThread) -> None:
        remaining: list[tuple[QThread, QObject | None]] = []
        released_worker: QObject | None = None
        for item_thread, item_worker in self._lingering:
            if item_thread is thread:
                released_worker = item_worker
                continue
            remaining.append((item_thread, item_worker))
        self._lingering = remaining
        self._retire_qobjects(released_worker, thread)

    def _retire_qobjects(
        self,
        worker: QObject | None,
        thread: QThread | None,
    ) -> None:
        self._retain_wrappers(thread, worker)
        _delete_later_quietly(worker)
        _delete_later_quietly(thread)

    def _retain_wrappers(self, *objects: QObject | None) -> None:
        retained = [obj for obj in objects if obj is not None]
        if not retained:
            return
        self._retired_wrappers.extend(retained)
        QTimer.singleShot(WRAPPER_RETENTION_MS, self._prune_wrappers)

    def _prune_wrappers(self) -> None:
        if not self._retired_wrappers:
            return
        try:
            import shiboken6
        except ImportError:
            return
        alive: list[QObject] = []
        for wrapper in self._retired_wrappers:
            try:
                if shiboken6.isValid(wrapper):
                    alive.append(wrapper)
            except (RuntimeError, TypeError):
                pass
        self._retired_wrappers = alive
        if alive:
            QTimer.singleShot(WRAPPER_RETENTION_MS, self._prune_wrappers)


__all__ = [
    "AsyncLoopResource",
    "AsyncSubmitTimeout",
    "DEFAULT_PROCESS_TERMINATE_TIMEOUT_S",
    "DEFAULT_THREAD_SHUTDOWN_WAIT_MS",
    "ManagedResource",
    "ProcessHandle",
    "ProcessResource",
    "QtWorkerResource",
    "ResourceManager",
    "ResourceRegistry",
    "ResourceState",
    "ServiceResource",
    "SignalBinding",
    "StoppableResource",
    "ThreadGroupResource",
    "ThreadResource",
    "WRAPPER_RETENTION_MS",
]
