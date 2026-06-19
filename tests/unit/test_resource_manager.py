from __future__ import annotations

import sys
import threading
import time
import types

import pytest

pytest.importorskip("PySide6.QtWidgets")

from PySide6.QtCore import (  # noqa: E402
    QCoreApplication,
    QDeadlineTimer,
    QObject,
    Signal,
    Slot,
)

from app.core.resource_manager import (  # noqa: E402
    ProcessResource,
    QtWorkerResource,
    ResourceManager,
    ResourceState,
    ThreadGroupResource,
    ThreadResource,
)


def _qt_app_or_skip():  # type: ignore[no-untyped-def]
    qtwidgets = pytest.importorskip("PySide6.QtWidgets")
    return qtwidgets.QApplication.instance() or qtwidgets.QApplication([])


def _spin_until(predicate, timeout_ms: int = 2000) -> None:  # type: ignore[no-untyped-def]
    deadline = QDeadlineTimer(timeout_ms)
    while not predicate() and not deadline.hasExpired():
        QCoreApplication.processEvents()


class _SignalStub:
    def __init__(self) -> None:
        self.callbacks: list = []

    def connect(self, callback) -> None:  # type: ignore[no-untyped-def]
        self.callbacks.append(callback)

    def emit(self, *args) -> None:  # type: ignore[no-untyped-def]
        for callback in list(self.callbacks):
            callback(*args)


class _ThreadStub:
    def __init__(self, *, running: bool = True, wait_result: bool = True) -> None:
        self.finished = _SignalStub()
        self._running = running
        self._wait_result = wait_result
        self.interrupted = False
        self.quit_called = False
        self.waits: list[int] = []
        self.deleted = False

    def requestInterruption(self) -> None:
        self.interrupted = True

    def isRunning(self) -> bool:
        return self._running

    def quit(self) -> None:
        self.quit_called = True

    def wait(self, timeout: int) -> bool:
        self.waits.append(timeout)
        return self._wait_result

    def deleteLater(self) -> None:
        self.deleted = True


class _WorkerStub:
    def __init__(self) -> None:
        self.cancelled = False
        self.deleted = False

    def cancel(self) -> None:
        self.cancelled = True

    def deleteLater(self) -> None:
        self.deleted = True


class _OwnerStub:
    pass


# --- stop_qt_thread mechanics（Phase 1 入口） ------------------------------


def test_stop_qt_thread_clean_runs_cancel_interrupt_quit_wait() -> None:
    _qt_app_or_skip()
    mgr = ResourceManager()
    thread = _ThreadStub(running=True, wait_result=True)
    worker = _WorkerStub()

    assert mgr.stop_qt_thread(thread, worker, label="worker_thread") is True
    assert worker.cancelled is True
    assert thread.interrupted is True
    assert thread.quit_called is True
    assert thread.waits == [1000]
    assert mgr._lingering == []


def test_stop_qt_thread_none_thread_is_clean() -> None:
    _qt_app_or_skip()
    mgr = ResourceManager()
    assert mgr.stop_qt_thread(None, None, label="missing") is True


def test_stop_qt_thread_timeout_lingers_then_releases_on_finished() -> None:
    _qt_app_or_skip()
    mgr = ResourceManager()
    thread = _ThreadStub(running=True, wait_result=False)
    worker = _WorkerStub()

    assert mgr.stop_qt_thread(thread, worker, label="worker_thread") is False
    assert len(mgr._lingering) == 1
    assert mgr._lingering[0][0] is thread

    # 线程在后台真正结束后触发 finished，释放并 deleteLater。
    thread.finished.emit()
    assert mgr._lingering == []
    assert thread.deleted is True
    assert worker.deleted is True


# --- QtWorkerResource.stop ------------------------------------------------


def test_resource_stop_clean_finalizes_nulls_owner_and_runs_business() -> None:
    _qt_app_or_skip()
    mgr = ResourceManager()
    owner = _OwnerStub()
    thread = _ThreadStub(running=True, wait_result=True)
    worker = _WorkerStub()
    owner.t = thread  # type: ignore[attr-defined]
    owner.w = worker  # type: ignore[attr-defined]
    business: list[int] = []
    res = QtWorkerResource(
        mgr, thread, worker,
        owner=owner, thread_attr="t", worker_attr="w",
        on_finished=lambda: business.append(1), label="t",
    )
    mgr._register(res)

    assert res.stop() is True
    assert owner.t is None  # type: ignore[attr-defined]
    assert owner.w is None  # type: ignore[attr-defined]
    assert thread.deleted is True
    assert worker.deleted is True
    assert business == [1]
    assert res not in mgr._resources

    # 二次 finished 不应重复 finalize。
    res._on_thread_finished()
    assert business == [1]


def test_resource_stop_timeout_lingers_and_unregisters() -> None:
    _qt_app_or_skip()
    mgr = ResourceManager()
    owner = _OwnerStub()
    thread = _ThreadStub(running=True, wait_result=False)
    worker = _WorkerStub()
    owner.t = thread  # type: ignore[attr-defined]
    owner.w = worker  # type: ignore[attr-defined]
    res = QtWorkerResource(
        mgr, thread, worker, owner=owner, thread_attr="t", worker_attr="w", label="t"
    )
    mgr._register(res)

    assert res.stop() is False
    assert res not in mgr._resources
    assert len(mgr._lingering) == 1
    assert res.thread is None
    # lingering 路径不应清空宿主属性（与旧 _shutdown_qthread 行为一致）。
    assert owner.t is thread  # type: ignore[attr-defined]


def test_null_owner_attrs_skips_reassigned_worker() -> None:
    _qt_app_or_skip()
    mgr = ResourceManager()
    owner = _OwnerStub()
    thread = _ThreadStub(running=True, wait_result=True)
    worker = _WorkerStub()
    owner.t = thread  # type: ignore[attr-defined]
    owner.w = worker  # type: ignore[attr-defined]
    res = QtWorkerResource(
        mgr, thread, worker, owner=owner, thread_attr="t", worker_attr="w", label="t"
    )
    # 宿主已经把属性指向新的 worker（被复用），finalize 不应误伤它。
    new_worker = _WorkerStub()
    owner.w = new_worker  # type: ignore[attr-defined]
    res.stop()
    assert owner.w is new_worker  # type: ignore[attr-defined]


# --- stop_all -------------------------------------------------------------


def test_stop_all_stops_every_registered_resource() -> None:
    _qt_app_or_skip()
    mgr = ResourceManager()
    order: list[tuple[str, int]] = []

    class _Res:
        def __init__(self, name: str) -> None:
            self.name = name

        def stop(self, timeout: int) -> bool:
            order.append((self.name, timeout))
            return True

    mgr._resources.extend([_Res("a"), _Res("b"), _Res("c")])  # type: ignore[list-item]
    mgr.stop_all(500)
    assert order == [("a", 500), ("b", 500), ("c", 500)]


# --- retain_wrappers / prune ---------------------------------------------


def test_retain_wrappers_prunes_invalid(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    _qt_app_or_skip()
    mgr = ResourceManager()
    valid = QObject()
    invalid = QObject()

    fake = types.ModuleType("shiboken6")
    fake.isValid = lambda obj: obj is valid  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "shiboken6", fake)

    mgr.retain_wrappers(valid, invalid, None)
    assert valid in mgr._retired_wrappers
    assert invalid in mgr._retired_wrappers

    mgr._prune_wrappers()
    assert mgr._retired_wrappers == [valid]


# --- spawn_qt_worker（真实 QThread） --------------------------------------


def test_spawn_qt_worker_normal_completion_finalizes() -> None:
    _qt_app_or_skip()

    class _Owner(QObject):
        pass

    class _Worker(QObject):
        finished = Signal()

        @Slot()
        def run(self) -> None:
            self.finished.emit()

    owner = _Owner()
    mgr = ResourceManager()
    business: list[bool] = []
    worker = _Worker()

    res = mgr.spawn_qt_worker(
        worker,
        parent=owner,
        owner=owner,
        thread_attr="worker_thread",
        worker_attr="the_worker",
        quit_on=[worker.finished],
        on_finished=lambda: business.append(True),
        label="worker_thread",
    )

    assert owner.worker_thread is not None  # type: ignore[attr-defined]
    assert owner.the_worker is worker  # type: ignore[attr-defined]

    _spin_until(lambda: owner.worker_thread is None)  # type: ignore[attr-defined]

    assert owner.worker_thread is None  # type: ignore[attr-defined]
    assert owner.the_worker is None  # type: ignore[attr-defined]
    assert business == [True]
    assert res not in mgr._resources
    assert res.is_running() is False


def test_spawn_qt_worker_unregistered_is_skipped_by_stop_all() -> None:
    _qt_app_or_skip()

    class _Owner(QObject):
        pass

    class _Worker(QObject):
        finished = Signal()

        @Slot()
        def run(self) -> None:
            self.finished.emit()

    owner = _Owner()
    mgr = ResourceManager()
    worker = _Worker()

    res = mgr.spawn_qt_worker(
        worker,
        parent=owner,
        owner=owner,
        thread_attr="mig_thread",
        worker_attr="mig_worker",
        quit_on=[worker.finished],
        register=False,
        label="mig_thread",
    )
    # 不进入 stop_all 清单，但仍会在线程结束时自动 finalize。
    assert res not in mgr._resources
    _spin_until(lambda: owner.mig_thread is None)  # type: ignore[attr-defined]
    assert owner.mig_worker is None  # type: ignore[attr-defined]


# --- ThreadResource（裸 Python 线程） -------------------------------------


def test_thread_resource_track_and_is_running() -> None:
    _qt_app_or_skip()
    mgr = ResourceManager()
    res = mgr.track_python_thread(label="synth")
    assert isinstance(res, ThreadResource)
    assert res in mgr._resources
    assert res.is_running() is False

    done = threading.Event()
    thread = threading.Thread(target=done.set)
    thread.start()
    thread.join()
    res.track(thread)
    assert res.state is ResourceState.READY
    assert res.is_running() is False


def test_thread_resource_stop_clean_when_thread_done() -> None:
    _qt_app_or_skip()
    mgr = ResourceManager()
    res = mgr.track_python_thread(label="synth")
    thread = threading.Thread(target=lambda: None)
    thread.start()
    thread.join()
    res.track(thread)

    assert res.stop() is True
    assert res.state is ResourceState.STOPPED
    assert res not in mgr._resources
    assert res.thread is None
    assert mgr._lingering_threads == []


def test_thread_resource_stop_cancel_unblocks_then_joins() -> None:
    _qt_app_or_skip()
    mgr = ResourceManager()
    block = threading.Event()
    cancelled: list[bool] = []

    def _cancel() -> None:
        cancelled.append(True)
        block.set()

    res = mgr.track_python_thread(cancel=_cancel, label="synth")
    thread = threading.Thread(target=lambda: block.wait(5), daemon=True)
    thread.start()
    res.track(thread)
    assert res.is_running() is True

    # cancel 会设事件让线程退出，join 成功 → 干净停止。
    assert res.stop(timeout_ms=2000) is True
    assert cancelled == [True]
    assert res not in mgr._resources
    assert mgr._lingering_threads == []


def test_thread_resource_stop_timeout_lingers() -> None:
    _qt_app_or_skip()
    mgr = ResourceManager()
    block = threading.Event()
    thread = threading.Thread(target=lambda: block.wait(5), daemon=True)
    res = mgr.track_python_thread(label="synth")
    thread.start()
    res.track(thread)

    # 没有 cancel，线程仍阻塞 → join 超时转 lingering。
    assert res.stop(timeout_ms=100) is False
    assert res not in mgr._resources
    assert thread in mgr._lingering_threads
    assert res.thread is None
    block.set()  # 收尾，避免线程残留
    thread.join(2)


# --- ThreadGroupResource（并发裸 Python 线程） ----------------------------


def test_thread_group_spawn_tracks_non_daemon_thread() -> None:
    _qt_app_or_skip()
    mgr = ResourceManager()
    release = threading.Event()
    res = mgr.track_thread_group(label="backchannel")

    thread = res.spawn(lambda: release.wait(2), name="backchannel-1")

    assert isinstance(res, ThreadGroupResource)
    assert res in mgr._resources
    assert thread is not None
    assert thread.daemon is False
    assert res.state is ResourceState.READY
    assert res.is_running() is True
    release.set()
    thread.join(2)


def test_thread_group_completed_thread_removes_itself() -> None:
    _qt_app_or_skip()
    mgr = ResourceManager()
    res = mgr.track_thread_group(label="backchannel")
    thread = res.spawn(lambda: None, name="backchannel-1")

    assert thread is not None
    thread.join(2)
    assert res.is_running() is False
    assert res._threads == set()
    # 线程自然结束后资源仍可复用，直到显式 stop。
    assert res in mgr._resources


def test_thread_group_stop_cancel_unblocks_all_threads() -> None:
    _qt_app_or_skip()
    mgr = ResourceManager()
    release = threading.Event()
    cancelled: list[bool] = []

    def cancel() -> None:
        cancelled.append(True)
        release.set()

    res = mgr.track_thread_group(cancel=cancel, label="backchannel")
    first = res.spawn(lambda: release.wait(2), name="backchannel-1")
    second = res.spawn(lambda: release.wait(2), name="backchannel-2")

    assert first is not None and second is not None
    assert res.stop(timeout_ms=1000) is True
    assert cancelled == [True]
    assert res.state is ResourceState.STOPPED
    assert res not in mgr._resources
    assert mgr._lingering_threads == []


def test_thread_group_stop_uses_one_deadline_and_lingers() -> None:
    _qt_app_or_skip()
    mgr = ResourceManager()
    release = threading.Event()
    res = mgr.track_thread_group(label="backchannel")
    first = res.spawn(lambda: release.wait(2), name="backchannel-1")
    second = res.spawn(lambda: release.wait(2), name="backchannel-2")
    assert first is not None and second is not None

    started_at = time.monotonic()
    assert res.stop(timeout_ms=100) is False
    elapsed = time.monotonic() - started_at

    assert elapsed < 0.25
    assert res not in mgr._resources
    assert first in mgr._lingering_threads
    assert second in mgr._lingering_threads
    assert res.is_running() is True
    release.set()
    first.join(2)
    second.join(2)
    assert res.is_running() is False
    assert res.state is ResourceState.STOPPED


def test_thread_group_stop_is_terminal_and_rejects_new_threads() -> None:
    _qt_app_or_skip()
    mgr = ResourceManager()
    res = mgr.track_thread_group(label="backchannel")

    assert res.stop() is True
    assert res.stop() is True
    assert res.spawn(lambda: None, name="too-late") is None


# --- ProcessResource（本地子进程句柄） ------------------------------------


class _ProcStub:
    def __init__(self, *, alive: bool = True) -> None:
        self.pid = 4321
        self._alive = alive
        self.terminated = False
        self.killed = False
        self.waited = False

    def poll(self) -> int | None:
        return None if self._alive else 0

    def terminate(self) -> None:
        self.terminated = True
        self._alive = False

    def kill(self) -> None:
        self.killed = True
        self._alive = False

    def wait(self, timeout: float | None = None) -> int | None:
        self.waited = True
        return 0


def test_process_resource_stop_terminates() -> None:
    _qt_app_or_skip()
    mgr = ResourceManager()
    proc = _ProcStub(alive=True)
    res = mgr.adopt_process(proc, label="gpt_sovits")
    assert isinstance(res, ProcessResource)
    assert res.state is ResourceState.READY
    assert res.is_running() is True

    assert res.stop() is True
    assert proc.terminated is True
    assert res.process is None
    assert res.state is ResourceState.STOPPED
    assert res not in mgr._resources


def test_process_resource_stop_when_already_exited_skips_terminate() -> None:
    _qt_app_or_skip()
    mgr = ResourceManager()
    proc = _ProcStub(alive=False)
    res = mgr.adopt_process(proc, label="x")
    assert res.is_running() is False

    assert res.stop() is True
    assert proc.terminated is False
    assert res not in mgr._resources


def test_process_resource_stop_uses_custom_terminator() -> None:
    _qt_app_or_skip()
    mgr = ResourceManager()
    calls: list[tuple[object, int]] = []

    def terminator(process: object, timeout: int) -> None:
        calls.append((process, timeout))
        process.terminate()  # type: ignore[attr-defined]

    proc = _ProcStub(alive=True)
    res = mgr.adopt_process(proc, terminator=terminator, terminate_timeout_s=7, label="x")
    res.stop()
    assert calls and calls[0][0] is proc and calls[0][1] == 7
    assert proc.terminated is True


def test_process_resource_detach_keeps_process_alive() -> None:
    _qt_app_or_skip()
    mgr = ResourceManager()
    proc = _ProcStub(alive=True)
    res = mgr.adopt_process(proc, label="x")

    handle = res.detach()
    assert handle is proc
    assert proc.terminated is False
    assert proc.killed is False
    assert res.process is None
    assert res not in mgr._resources
    # detach 之后 stop 是干净的 no-op。
    assert res.stop() is True


def test_process_resource_restart_replaces_handle() -> None:
    _qt_app_or_skip()
    mgr = ResourceManager()
    old = _ProcStub(alive=True)
    new = _ProcStub(alive=True)
    res = mgr.adopt_process(old, restart_factory=lambda: new, label="x")

    assert res.restart() is True
    assert old.terminated is True
    assert res.process is new
    assert res.state is ResourceState.READY


def test_process_resource_restart_without_factory_clears_handle() -> None:
    _qt_app_or_skip()
    mgr = ResourceManager()
    old = _ProcStub(alive=True)
    res = mgr.adopt_process(old, label="x")

    assert res.restart() is True
    assert old.terminated is True
    assert res.process is None
    assert res.state is ResourceState.NEW


def test_process_resource_health_reflects_liveness() -> None:
    _qt_app_or_skip()
    mgr = ResourceManager()
    proc = _ProcStub(alive=True)
    res = mgr.adopt_process(proc, label="x")
    assert res.health() is ResourceState.READY

    proc._alive = False
    assert res.health() is ResourceState.STOPPED


def test_stop_all_mixes_qt_thread_and_process_resources() -> None:
    _qt_app_or_skip()
    mgr = ResourceManager()
    proc = _ProcStub(alive=True)
    process_res = mgr.adopt_process(proc, label="proc")
    thread_res = mgr.track_python_thread(label="synth")
    thread = threading.Thread(target=lambda: None)
    thread.start()
    thread.join()
    thread_res.track(thread)

    mgr.stop_all()
    assert proc.terminated is True
    assert process_res not in mgr._resources
    assert thread_res not in mgr._resources
