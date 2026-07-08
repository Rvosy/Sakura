"""运行时资源管理器（ResourceManager）。

对应 issue #94：把原本散落在 ``PetWindow`` 里的 QThread worker、裸
Python 线程、子进程、asyncio loop 与服务关闭链路，集中到 App 级
``ResourceRegistry`` 与 UI 主线程上的 ``ResourceManager``（``QObject`` wrapper）。

设计与路线图见 ``docs/RUNTIME_RESOURCE_MANAGER_PLAN.md``。本模块同时保留
lingering 线程与 Shiboken wrapper 保留这两个 native 安全机制。
"""

from __future__ import annotations

import asyncio
import subprocess
import threading
import time
from collections.abc import Callable, Sequence
from concurrent.futures import Future
from dataclasses import dataclass
from enum import Enum
from typing import Any, Protocol, runtime_checkable

from PySide6.QtCore import QObject, QThread, QTimer

from app.core.runtime_log import log_event

# 停止后台线程时的默认等待时长，与 PetWindow.THREAD_SHUTDOWN_WAIT_MS 对齐。
DEFAULT_THREAD_SHUTDOWN_WAIT_MS = 1_000
# 退役 QObject wrapper 的保留时长，避开 Shiboken double-destruction 竞态窗口。
WRAPPER_RETENTION_MS = 1_000
# 本地子进程终止的默认宽限时长（秒），沿用旧 _stop_local_service 的 5 秒。
DEFAULT_PROCESS_TERMINATE_TIMEOUT_S = 5

# (signal, slot)：把 worker 的某个信号连接到一个槽。
SignalBinding = tuple[Any, Callable[..., Any]]


class ResourceState(str, Enum):
    """受管资源的统一生命周期状态。

    对应 ``docs/RUNTIME_RESOURCE_MANAGER_PLAN.md`` 的状态机：
    ``NEW → STARTING → READY → STOPPING → STOPPED``，进程类资源额外可进入
    ``DEGRADED``（健康检查失败但仍存活，可 ``restart()``）。``QtWorkerResource``
    沿用旧实现、不显式标状态，故仅 Thread/Process 资源使用本枚举。
    """

    NEW = "new"
    STARTING = "starting"
    READY = "ready"
    DEGRADED = "degraded"
    STOPPING = "stopping"
    STOPPED = "stopped"
    FAILED = "failed"


@runtime_checkable
class StoppableResource(Protocol):
    """``ResourceManager`` 注册表与 :meth:`ResourceManager.stop_all` 的最小契约。

    任何实现 ``stop(timeout_ms) -> bool`` 的对象都可纳入统一关闭清单
    （``QtWorkerResource`` / ``ThreadResource`` / ``ProcessResource`` 均满足）。
    """

    def stop(self, timeout_ms: int = ...) -> bool:
        """请求停止；返回是否在 ``timeout_ms`` 内干净停止。"""


@runtime_checkable
class ManagedResource(StoppableResource, Protocol):
    """受管资源的完整运行时契约。

    第五阶段用于服务、asyncio loop、Python thread、进程等非 Qt 资源；现有
    ``QtWorkerResource`` 只需满足 ``stop`` 最小契约即可继续被 registry 管理。
    """

    def is_running(self) -> bool:
        """资源是否仍处于运行态。"""

    def health(self) -> ResourceState:
        """返回资源健康状态。"""


@dataclass
class _ResourceEntry:
    resource: StoppableResource
    label: str
    shutdown_order: int


@runtime_checkable
class ProcessHandle(Protocol):
    """本地子进程句柄协议（``subprocess.Popen`` 与 TTS 的接管句柄都满足）。"""

    pid: int

    def poll(self) -> int | None:
        """返回退出码；仍在运行时返回 ``None``。"""

    def terminate(self) -> None:
        """请求终止进程。"""

    def kill(self) -> None:
        """强制终止进程。"""

    def wait(self, timeout: float | None = None) -> int | None:
        """等待进程退出。"""


def _default_terminate_process(process: ProcessHandle, timeout_s: int) -> None:
    """缺省进程终止策略：``terminate → wait → kill``。

    TTS 在 Windows 上需要按进程树清理（taskkill /T），届时通过 ``terminator``
    注入更强的策略；此处只提供与平台无关的兜底。
    """
    process.terminate()
    try:
        process.wait(timeout=timeout_s)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=timeout_s)


def _delete_later_quietly(obj: QObject | None) -> None:
    if obj is None:
        return
    try:
        obj.deleteLater()
    except RuntimeError:
        pass


class QtWorkerResource:
    """托管一对 ``QThread + QObject worker`` 的完整生命周期。

    通过 :meth:`ResourceManager.spawn_qt_worker` 创建。正常结束时由
    ``thread.finished`` 触发 :meth:`_finalize`（保留 wrapper → deleteLater →
    清空宿主属性 → 运行业务回调）；关闭时由 :meth:`stop` 复刻
    ``cancel → requestInterruption → quit → wait → linger`` 序列。
    """

    def __init__(
        self,
        manager: "ResourceManager",
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
        """请求停止并在 ``timeout_ms`` 内等待。

        返回 ``True`` 表示线程已干净停止（或本就未运行）；``False`` 表示超时，
        线程转入 manager 的 lingering 列表，在后台自然结束，不阻塞 UI 退出。
        """
        clean = self._manager._stop_thread_mechanics(
            self.thread, self.worker, label=self.label, timeout_ms=timeout_ms
        )
        if clean:
            self._finalize(run_business=True)
            return True
        # 超时 lingering：manager 已持有 (thread, worker) 引用并接管 deleteLater；
        # 标记 finalized 以避免线程真正结束时与 lingering 释放重复清理。
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
        self._manager.retain_wrappers(thread, worker)
        _delete_later_quietly(worker)
        _delete_later_quietly(thread)
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
        # 只在属性仍指向本资源时才置空，避免误伤已经被复用赋值的新 worker。
        if self._worker_attr and getattr(owner, self._worker_attr, None) is self.worker:
            setattr(owner, self._worker_attr, None)
        if self._thread_attr and getattr(owner, self._thread_attr, None) is self.thread:
            setattr(owner, self._thread_attr, None)


class ThreadResource:
    """托管一个裸 Python 线程/worker（``PYTHON_THREAD`` 线程域）。

    用于 TTS 合成这类「每请求一次性 daemon 线程」的场景：单个资源代表当前在飞
    线程，队列每次 spawn 时调用 :meth:`track` 刷新登记。:meth:`stop` 复刻
    ``cancel → join(timeout) → linger`` 序列——与 :class:`QtWorkerResource`
    语义对齐：join 超时不强杀（Python 无法安全强杀线程），转 lingering 让 daemon
    线程随进程自然结束，不阻塞 UI 退出。
    """

    def __init__(
        self,
        manager: "ResourceManager",
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
        """登记当前在飞线程（每次队列 spawn 时调用以刷新）。"""
        self.thread = thread
        self.state = ResourceState.READY

    def is_running(self) -> bool:
        thread = self.thread
        return bool(thread is not None and thread.is_alive())

    def stop(self, timeout_ms: int = DEFAULT_THREAD_SHUTDOWN_WAIT_MS) -> bool:
        """请求取消并在 ``timeout_ms`` 内 join。

        返回 ``True`` 表示线程已结束（或本就空闲）；``False`` 表示 join 超时，
        线程转入 manager 的 lingering 列表，在后台自然结束。
        """
        self.state = ResourceState.STOPPING
        if self._cancel is not None:
            try:
                self._cancel()
            except Exception as exc:  # noqa: BLE001
                log_event(
                    "ResourceManager",
                    "线程取消回调异常",
                    {"thread": self.label, "error": str(exc)},
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
    """托管一组可并发运行的裸 Python 线程。

    与只追踪单个当前线程的 :class:`ThreadResource` 不同，本资源通过
    :meth:`spawn` 统一完成线程创建、登记和启动，并在线程 target 退出时自动
    摘除。关闭时所有线程共享同一个等待截止时间，避免线程数量增加后成倍延长
    UI 退出等待。
    """

    def __init__(
        self,
        manager: "ResourceManager",
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
        """创建、登记并启动线程；资源停止后拒绝新线程。

        登记与 ``start`` 在同一把锁内完成，避免关闭恰好发生在两者之间时漏掉
        已启动线程。target 无论正常返回还是抛出异常，都会在 ``finally`` 中从
        在飞集合摘除。
        """

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
        """取消新任务并等待全部在飞线程，所有线程共享一个截止时间。

        ``timeout_ms=None`` 保留调用方显式无限等待的语义。超时线程不会被强杀，
        而是登记到 manager 的 lingering 列表中后台自然完成。
        """
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
                    {"thread_group": self.label, "error": str(exc)},
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
            if current in alive or (deadline is not None and time.monotonic() >= deadline):
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
    """托管本地子进程句柄（``PROCESS`` 线程域，如 GPT-SoVITS / Genie）。

    :meth:`stop` 复刻旧 ``_stop_local_service`` 的「进程树终止 + 兜底 kill」；
    :meth:`restart` 供 Broken pipe 后重启复用；:meth:`detach` 交出所有权但不杀
    进程（对齐旧 ``detach_local_service``，供新 Provider 后台接管）。
    """

    def __init__(
        self,
        manager: "ResourceManager",
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
        """登记/替换受管进程句柄。"""
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
        """轻量健康投影：进程仍在跑即 ``READY``，否则 ``STOPPED``。

        更细的 HTTP 层健康检查留待 supervisor 层实现，本资源只看进程存活。
        """
        if self.state in (ResourceState.STOPPING, ResourceState.STOPPED, ResourceState.FAILED):
            return self.state
        return ResourceState.READY if self.is_running() else ResourceState.STOPPED

    def detach(self) -> ProcessHandle | None:
        """交出进程所有权但不终止它，返回原句柄。"""
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
        log_event("ResourceManager", "终止本地子进程", {"process": self.label, "pid": getattr(process, "pid", None)})
        try:
            if self._terminator is not None:
                self._terminator(process, self._terminate_timeout_s)
            else:
                _default_terminate_process(process, self._terminate_timeout_s)
        except Exception as exc:  # noqa: BLE001
            log_event(
                "ResourceManager",
                "本地子进程正常终止失败，尝试强制结束",
                {"process": self.label, "error": str(exc)},
            )
            try:
                process.kill()
                process.wait(timeout=self._terminate_timeout_s)
            except Exception as kill_exc:  # noqa: BLE001
                log_event(
                    "ResourceManager",
                    "本地子进程强制结束失败",
                    {"process": self.label, "error": str(kill_exc)},
                )

    def restart(self) -> bool:
        """终止当前进程并经 ``restart_factory`` 重建。

        无 ``restart_factory`` 时只终止现有进程并清空句柄（由调用方在就绪流程里
        重新拉起），返回 ``True``。
        """
        self.state = ResourceState.STARTING
        self._terminate_current()
        if self._restart_factory is None:
            self.process = None
            self.state = ResourceState.NEW
            return True
        try:
            self.process = self._restart_factory()
        except Exception as exc:  # noqa: BLE001
            log_event("ResourceManager", "本地子进程重启失败", {"process": self.label, "error": str(exc)})
            self.process = None
            self.state = ResourceState.FAILED
            return False
        self.state = ResourceState.READY if self.process is not None else ResourceState.NEW
        return self.process is not None

    def stop(self, timeout_ms: int = DEFAULT_THREAD_SHUTDOWN_WAIT_MS) -> bool:
        """终止受管进程；返回 ``True`` 表示已停止（或本就无进程/已退出）。

        进程终止用自身的 ``terminate_timeout_s`` 宽限，不受 ``timeout_ms`` 约束
        （后者是 QThread 风格的快速关闭节奏，对本地大模型进程过短）。
        """
        _ = timeout_ms
        self.state = ResourceState.STOPPING
        process = self.process
        if process is None:
            self.state = ResourceState.STOPPED
            self._manager._unregister(self)
            return True
        self._terminate_current()
        self.process = None
        self.state = ResourceState.STOPPED
        self._manager._unregister(self)
        return True


class ServiceResource:
    """把已有服务对象的关闭函数纳入统一资源域。"""

    def __init__(
        self,
        manager: "ResourceRegistry | ResourceManager",
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
            log_event("ResourceManager", "服务运行态查询失败", {"service": self.label, "error": str(exc)})
            return False

    def health(self) -> ResourceState:
        if self._health is not None:
            try:
                return self._health()
            except Exception as exc:  # noqa: BLE001
                log_event("ResourceManager", "服务健康检查失败", {"service": self.label, "error": str(exc)})
                return ResourceState.DEGRADED
        if self.state in (ResourceState.STOPPING, ResourceState.STOPPED, ResourceState.FAILED):
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
            log_event("ResourceManager", "服务关闭失败", {"service": self.label, "error": str(exc)})
        else:
            self.state = ResourceState.STOPPED if clean else ResourceState.DEGRADED
        finally:
            self._manager._unregister(self)
        return clean

    def detach(self) -> None:
        """仅从 registry 移除，不触发关闭；用于宿主手动完成退役后的同步。"""
        self.state = ResourceState.STOPPED
        self._manager._unregister(self)


class AsyncLoopResource:
    """托管独立 asyncio event loop 及其 daemon 线程。"""

    def __init__(
        self,
        manager: "ResourceRegistry | ResourceManager",
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
            if self._thread is not None and self._thread.is_alive() and self._loop is not None:
                return self._loop
            self._thread_name = name or self._thread_name
            self._daemon = daemon
            self._ready.clear()
            self.state = ResourceState.STARTING
            thread = threading.Thread(target=self._run_loop, name=self._thread_name, daemon=daemon)
            self._thread = thread
            thread.start()
        if not self._ready.wait(timeout=ready_timeout_s):
            self.state = ResourceState.FAILED
            raise TimeoutError(f"asyncio 事件循环启动超时：{self.label or self._thread_name}")
        loop = self.loop
        if loop is None:
            self.state = ResourceState.FAILED
            raise RuntimeError(f"asyncio 事件循环启动失败：{self.label or self._thread_name}")
        self.state = ResourceState.READY
        return loop

    def submit(self, coro: Any, *, timeout: float) -> Any:
        loop = self.loop
        if loop is None or self.state in (ResourceState.STOPPING, ResourceState.STOPPED):
            raise RuntimeError("asyncio 事件循环尚未运行。")
        future: Future[Any] = asyncio.run_coroutine_threadsafe(coro, loop)
        return future.result(timeout=timeout)

    def is_running(self) -> bool:
        thread = self.thread
        return bool(thread is not None and thread.is_alive())

    def health(self) -> ResourceState:
        if self.state in (ResourceState.STOPPING, ResourceState.STOPPED, ResourceState.FAILED):
            return self.state
        loop = self.loop
        return ResourceState.READY if self.is_running() and loop is not None and not loop.is_closed() else ResourceState.STOPPED

    def restart(self, *, reason: str = "") -> bool:
        if reason:
            log_event("ResourceManager", "重启 asyncio 事件循环", {"loop": self.label, "reason": reason})
        self.stop(DEFAULT_THREAD_SHUTDOWN_WAIT_MS)
        self._manager._register(self, label=self.label, shutdown_order=900)
        self.start(name=self._thread_name, daemon=self._daemon)
        return self.is_running()

    def stop(self, timeout_ms: int = DEFAULT_THREAD_SHUTDOWN_WAIT_MS) -> bool:
        with self._lock:
            if self.state is ResourceState.STOPPED:
                return True
            self.state = ResourceState.STOPPING
            loop = self._loop
            thread = self._thread
        if loop is None or thread is None:
            self._finalize_stop()
            return True
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
                loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
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


class ResourceRegistry:
    """纯 Python、线程安全的 App 级资源注册表。"""

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
            entries = tuple(sorted(self._entries, key=lambda entry: entry.shutdown_order, reverse=True))
        for entry in entries:
            try:
                entry.resource.stop(timeout_ms)
            except Exception as exc:  # noqa: BLE001
                log_event(
                    "ResourceManager",
                    "受管资源关闭异常",
                    {"resource": entry.label, "error": str(exc)},
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
            self._entries = [entry for entry in self._entries if entry.resource is not resource]

    def _keep_lingering_thread(self, thread: threading.Thread, label: str) -> None:
        with self._lock:
            if thread in self._lingering_threads:
                return
            self._lingering_threads.append(thread)
        log_event("ResourceManager", "登记 lingering Python 线程", {"thread": label})


class ResourceManager(QObject):
    """集中托管 QThread worker 生命周期、lingering 线程与退役 wrapper。

    活在 UI 主线程，通常作为 ``PetWindow`` 的子对象创建。
    """

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
        """当前窗口/协调器共享的 App 级资源域。"""
        return self._registry

    @property
    def _resources(self) -> list[StoppableResource]:
        return self._registry._resources

    @property
    def _lingering_threads(self) -> list[threading.Thread]:
        return self._registry._lingering_threads

    # ---- Phase 2：worker 工厂与批量关闭 ----------------------------------

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
        """创建并启动一个受管 QThread worker。

        - 在 ``parent`` 下创建 ``QThread`` 并把 ``worker`` 移入；
        - ``started`` → ``run_slot``（默认 ``worker.run``）；
        - 按 ``signal_bindings`` 连接 worker 信号到 UI 槽；
        - ``quit_on`` 中的终结信号 → ``thread.quit``；
        - ``thread.finished`` → 资源 finalize（保留 wrapper / deleteLater /
          清空宿主属性 / 运行 ``on_finished`` 业务回调）；
        - 把 ``thread``/``worker`` 写入 ``owner`` 的 ``thread_attr``/``worker_attr``，
          以兼容现有处理器与测试断言。

        ``register=False`` 时不纳入 :meth:`stop_all` 的关闭清单（用于启动期一次性、
        不应在退出时被打断的任务，如 TTS 整合包迁移），但仍会在线程结束时自动 finalize。
        """
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
        """创建一个托管裸 Python 线程的 :class:`ThreadResource`。

        资源先登记进 :meth:`stop_all` 清单（``register=True``），调用方随后用
        ``resource.track(thread)`` 在每次 spawn 时刷新当前在飞线程。
        """
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
        """创建可并发托管多个裸 Python 线程的资源。"""
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
        """把一个本地子进程句柄纳入托管，返回 :class:`ProcessResource`。"""
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
        """停止所有受管资源；按 shutdown_order 从高到低执行。"""
        self._registry.stop_all(timeout_ms)

    def _register(
        self,
        resource: StoppableResource,
        *,
        label: str = "",
        shutdown_order: int = 1000,
    ) -> None:
        self._registry._register(resource, label=label, shutdown_order=shutdown_order)

    def _unregister(self, resource: StoppableResource) -> None:
        self._registry._unregister(resource)

    # ---- Phase 1：关闭机制、lingering 线程、wrapper 保留 -----------------

    def stop_qt_thread(
        self,
        thread: QThread | None,
        worker: QObject | None,
        *,
        label: str,
        timeout_ms: int = DEFAULT_THREAD_SHUTDOWN_WAIT_MS,
    ) -> bool:
        """停止一个未经 spawn 注册的裸 QThread（Phase 1 委托入口）。

        返回 ``True`` 表示已干净停止（或线程为空 / RuntimeError）；``False`` 表示
        超时转入 lingering。调用方据此决定是否清空自身持有的 thread/worker 属性。
        """
        return self._stop_thread_mechanics(
            thread, worker, label=label, timeout_ms=timeout_ms
        )

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
        self._lingering.append((thread, worker))
        try:
            thread.finished.connect(
                lambda _thread=thread: self._release_lingering(_thread)
            )
        except RuntimeError:
            self._release_lingering(thread)

    def _keep_lingering_thread(self, thread: threading.Thread, label: str) -> None:
        """登记一个 join 超时的裸 Python 线程。"""
        self._registry._keep_lingering_thread(thread, label)

    def _release_lingering(self, thread: QThread) -> None:
        remaining: list[tuple[QThread, QObject | None]] = []
        released_worker: QObject | None = None
        for item_thread, item_worker in self._lingering:
            if item_thread is thread:
                released_worker = item_worker
                continue
            remaining.append((item_thread, item_worker))
        self._lingering = remaining
        _delete_later_quietly(released_worker)
        _delete_later_quietly(thread)

    def retain_wrappers(self, *objects: QObject | None) -> None:
        """退役 QObject wrapper 暂存 1 秒后再 prune，避开 Shiboken 双重析构窗口。

        queued 信号可能在 Qt 正在销毁同一 QObject 时到达 Python；若此刻丢掉最后一个
        Python wrapper 引用，Shiboken 可能去销毁一个 C++ 生命周期已由 Qt 接管的对象。
        """
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
