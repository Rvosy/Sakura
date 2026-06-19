from __future__ import annotations

import sys
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

from app.core.resource_manager import QtWorkerResource, ResourceManager  # noqa: E402


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
