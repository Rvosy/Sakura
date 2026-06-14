from __future__ import annotations

import importlib.util
import os
from collections.abc import Iterable
from typing import Any

import pytest


os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("PYTEST_QT_API", "pyside6")


_THREAD_ATTR_NAMES = (
    "deferred_startup_thread",
    "worker_thread",
    "memory_curation_thread",
    "_api_test_thread",
    "_tts_test_thread",
    "_memory_list_thread",
    "_character_export_thread",
)


@pytest.fixture(autouse=True)
def cleanup_qt_objects_after_test() -> Iterable[None]:
    yield
    _cleanup_qt_objects()


@pytest.fixture(autouse=True)
def block_memory_store_background_load(
    request: pytest.FixtureRequest,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterable[None]:
    """全局阻断 MemoryStore.preload 的后台加载线程。

    preload(wait=False) 会在后台线程 import sentence_transformers → transformers，
    其 native 初始化与 Qt 事件循环并发时随机 access violation（0xC0000005，
    见提交 71ea1a32 的局部修复；多个测试各自泄漏线程时崩溃概率叠加）。
    需要真实 preload 行为的测试用 @pytest.mark.allow_memory_preload 豁免，
    并自行保证 _create_memory_client 不触碰 native 库。
    """
    if request.node.get_closest_marker("allow_memory_preload"):
        yield
        return
    try:
        from app.agent.memory import MemoryStore
    except Exception:
        yield
        return

    def _blocked_create_memory_client(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("测试环境禁止初始化真实 mem0/sentence-transformers 后端")

    # 双层拦截：preload 不起线程；其余路径（_get_memory 惰性加载、reload）
    # 起的线程在触碰 native 库前立刻失败。子类 override 的 fake
    # _create_memory_client 不受基类 patch 影响。
    monkeypatch.setattr(MemoryStore, "preload", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(MemoryStore, "_create_memory_client", _blocked_create_memory_client)
    yield


def _cleanup_qt_objects() -> None:
    if importlib.util.find_spec("PySide6") is None:
        return
    try:
        from PySide6.QtCore import QCoreApplication, QEvent, QThread
        from PySide6.QtWidgets import QApplication
    except Exception:
        return

    app = QApplication.instance()
    if app is None:
        return

    widgets = _safe_qt_list(QApplication.topLevelWidgets)
    threads = _unique_threads(
        thread
        for obj in (app, *widgets)
        for thread in _collect_threads(obj, QThread)
    )
    for thread in threads:
        _stop_thread(thread, QThread)

    _drain_qt_events(app, QCoreApplication, QEvent)

    for widget in _safe_qt_list(QApplication.topLevelWidgets):
        try:
            widget.close()
            widget.deleteLater()
        except RuntimeError:
            pass

    _drain_qt_events(app, QCoreApplication, QEvent)


def _collect_threads(obj: Any, qthread_type: type, seen: set[int] | None = None) -> list[Any]:
    if seen is None:
        seen = set()
    try:
        obj_id = id(obj)
    except RuntimeError:
        return []
    if obj_id in seen:
        return []
    seen.add(obj_id)

    threads: list[Any] = []
    try:
        if isinstance(obj, qthread_type):
            threads.append(obj)
    except RuntimeError:
        return threads

    for attr_name in _THREAD_ATTR_NAMES:
        try:
            value = getattr(obj, attr_name, None)
        except RuntimeError:
            continue
        if isinstance(value, qthread_type):
            threads.append(value)

    try:
        children = list(obj.children())
    except RuntimeError:
        children = []
    for child in children:
        threads.extend(_collect_threads(child, qthread_type, seen))
    return threads


def _unique_threads(threads: Iterable[Any]) -> list[Any]:
    unique: list[Any] = []
    seen: set[int] = set()
    for thread in threads:
        thread_id = id(thread)
        if thread_id in seen:
            continue
        seen.add(thread_id)
        unique.append(thread)
    return unique


def _stop_thread(thread: Any, qthread_type: type) -> None:
    try:
        if thread == qthread_type.currentThread() or not thread.isRunning():
            return
        thread.quit()
        if not thread.wait(1000):
            thread.terminate()
            thread.wait(1000)
    except RuntimeError:
        return


def _drain_qt_events(app: Any, qcore_application: type, qevent: type) -> None:
    for _ in range(3):
        try:
            app.processEvents()
            qcore_application.sendPostedEvents(None, qevent.Type.DeferredDelete)
            app.processEvents()
        except RuntimeError:
            return


def _safe_qt_list(factory: Any) -> list[Any]:
    try:
        return list(factory())
    except RuntimeError:
        return []
