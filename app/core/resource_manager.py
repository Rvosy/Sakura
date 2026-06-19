"""运行时资源管理器（ResourceManager）。

对应 issue #94 第 1+2 阶段：把原本散落在 ``PetWindow`` 里逐字重复的
「创建 QThread → moveToThread → 接线 → quit → cleanup → 关闭」样板，以及
lingering 线程与 Shiboken wrapper 保留这两个 native 安全机制，集中到一个
活在 UI 主线程的 ``ResourceManager``（``QObject``）里。

设计与路线图见 ``docs/RUNTIME_RESOURCE_MANAGER_PLAN.md``。本模块只实现
QThread worker 生命周期所需的最小子集；Service/Process/async-loop 类资源
留待后续阶段。
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any

from PySide6.QtCore import QObject, QThread, QTimer

from app.core.debug_log import debug_log

# 停止后台线程时的默认等待时长，与 PetWindow.THREAD_SHUTDOWN_WAIT_MS 对齐。
DEFAULT_THREAD_SHUTDOWN_WAIT_MS = 1_000
# 退役 QObject wrapper 的保留时长，避开 Shiboken double-destruction 竞态窗口。
WRAPPER_RETENTION_MS = 1_000

# (signal, slot)：把 worker 的某个信号连接到一个槽。
SignalBinding = tuple[Any, Callable[..., Any]]


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


class ResourceManager(QObject):
    """集中托管 QThread worker 生命周期、lingering 线程与退役 wrapper。

    活在 UI 主线程，通常作为 ``PetWindow`` 的子对象创建。
    """

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._resources: list[QtWorkerResource] = []
        self._lingering: list[tuple[QThread, QObject | None]] = []
        self._retired_wrappers: list[QObject] = []

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
            self._register(resource)
        thread.start()
        return resource

    def stop_all(self, timeout_ms: int = DEFAULT_THREAD_SHUTDOWN_WAIT_MS) -> None:
        """停止所有受管 worker。线程之间相互独立，关闭顺序不影响正确性。"""
        for resource in tuple(self._resources):
            resource.stop(timeout_ms)

    def _register(self, resource: QtWorkerResource) -> None:
        self._resources.append(resource)

    def _unregister(self, resource: QtWorkerResource) -> None:
        try:
            self._resources.remove(resource)
        except ValueError:
            pass

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
        debug_log("ResourceManager", "准备关闭后台线程", {"thread": label})
        try:
            cancel = getattr(worker, "cancel", None)
            if callable(cancel):
                cancel()
            thread.requestInterruption()
            if thread.isRunning():
                thread.quit()
                if not thread.wait(timeout_ms):
                    debug_log(
                        "ResourceManager",
                        "后台线程未在退出等待时间内结束",
                        {"thread": label, "wait_ms": timeout_ms},
                    )
                    self._keep_lingering(thread, worker)
                    return False
        except RuntimeError as exc:
            debug_log(
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
