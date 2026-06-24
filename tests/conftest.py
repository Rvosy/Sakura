from __future__ import annotations

import importlib.util
import os
from pathlib import Path
from collections.abc import Iterable
from typing import Any

import pytest


os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("PYTEST_QT_API", "pyside6")

_ORIGINAL_PATH_MKDIR = Path.mkdir
_PYTEST_BASETEMP = (Path.cwd() / ".pytest-basetemp").resolve()


def _is_pytest_basetemp_path(path: Path) -> bool:
    try:
        resolved = path.resolve()
    except OSError:
        resolved = path.absolute()
    return resolved == _PYTEST_BASETEMP or _PYTEST_BASETEMP in resolved.parents


def _mkdir_without_private_windows_acl(
    self: Path,
    mode: int = 0o777,
    parents: bool = False,
    exist_ok: bool = False,
) -> None:
    # pytest's 0o700 temp dirs can become unreadable in the Windows sandbox.
    if os.name == "nt" and mode == 0o700 and _is_pytest_basetemp_path(self):
        mode = 0o777
    return _ORIGINAL_PATH_MKDIR(self, mode=mode, parents=parents, exist_ok=exist_ok)


if os.name == "nt":
    Path.mkdir = _mkdir_without_private_windows_acl  # type: ignore[method-assign]


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
def cleanup_tts_providers_before_qt(
    cleanup_qt_objects_after_test: None,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterable[None]:
    """保持真实 TTS Provider 存活，并在 Qt 对象清理前统一关闭。"""
    _ = cleanup_qt_objects_after_test
    try:
        from app.voice.tts import GPTSoVITSTTSProvider
    except Exception:
        yield
        return

    providers: list[Any] = []
    original_init = GPTSoVITSTTSProvider.__init__

    def _tracked_init(provider: Any, *args: object, **kwargs: object) -> None:
        original_init(provider, *args, **kwargs)
        providers.append(provider)

    monkeypatch.setattr(GPTSoVITSTTSProvider, "__init__", _tracked_init)
    yield

    for provider in reversed(providers):
        try:
            is_closed = getattr(provider, "_is_closed", None)
            if callable(is_closed) and is_closed():
                continue
            provider.close()
        except RuntimeError:
            # 测试主动删除过底层 QObject 时，Python wrapper 可能已失效。
            continue


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

    for widget in _safe_qt_list(QApplication.topLevelWidgets):
        try:
            widget.close()
        except Exception:  # noqa: BLE001
            # 部分 UI 测试只构造精简窗口，closeEvent 可能依赖未初始化字段。
            pass
        try:
            widget.deleteLater()
        except RuntimeError:
            pass

    # 只处理延迟删除；全局 processEvents 会执行跨测试残留的普通 queued event，
    # 曾在 Windows/PySide6 中随机触发 0xC0000005。
    try:
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    except RuntimeError:
        pass


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



def _safe_qt_list(factory: Any) -> list[Any]:
    try:
        return list(factory())
    except RuntimeError:
        return []
