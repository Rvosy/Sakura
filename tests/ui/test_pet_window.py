from __future__ import annotations

import json
import os
import threading
import time
import zipfile
from dataclasses import replace
from datetime import datetime
from pathlib import Path
import uuid

import pytest

from app.agent.mcp import MCPRuntimeSettings
from app.config.settings_service import DebugLogSettings, StartupSettings
from app.llm.api_client import ApiSettings
from app.agent import AgentEvent, AgentResult
from app.llm.chat_reply import ChatReply, ChatSegment
from app.ui.portrait_utils import portrait_kind_key, should_crossfade_portrait
from app.ui.theme import (
    DEFAULT_THEME_SETTINGS,
    ThemeSettings,
    build_message_box_stylesheet,
    build_pet_window_stylesheet,
)
from app.agent.proactive_care import ProactiveCareSettings
from app.agent.screen_awareness import ScreenAwarenessSettings
from app.agent.screen_observation import ScreenObservation
from app.voice.tts_settings import GPTSoVITSTTSSettings
from app.storage.visual_observation import VisualObservationRecord, VisualObservationStore


def test_portrait_kind_key_uses_filename_suffix_group() -> None:
    assert portrait_kind_key(Path("portraits/A020.png")) == "A"
    assert portrait_kind_key(Path("portraits/B180.png")) == "B"
    assert portrait_kind_key(Path("portraits/I010.png")) == "I"


def test_same_portrait_kind_crossfades_when_file_changes() -> None:
    assert should_crossfade_portrait(
        Path("portraits/A020.png"),
        Path("portraits/A150.png"),
    )
    assert should_crossfade_portrait(
        Path("portraits/I010.png"),
        Path("portraits/I180.png"),
    )


def test_different_portrait_kind_crossfades() -> None:
    assert should_crossfade_portrait(
        Path("portraits/A020.png"),
        Path("portraits/B180.png"),
    )


def test_same_portrait_file_does_not_crossfade() -> None:
    assert not should_crossfade_portrait(
        Path("portraits/A020.png"),
        Path("portraits/A020.png"),
    )


def test_pet_window_menu_keeps_only_allowed_checkable_switches() -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qtwidgets = pytest.importorskip("PySide6.QtWidgets")
    if not hasattr(qtwidgets, "QApplication") or not hasattr(qtwidgets, "QWidget"):
        pytest.skip("当前测试环境只提供了 PySide6 stub。")

    from app.ui.pet_window import PetWindow, SUBTITLE_LANGUAGE_ZH

    QApplication = qtwidgets.QApplication
    QWidget = qtwidgets.QWidget
    app = QApplication.instance() or QApplication([])
    host = QWidget()
    host.subtitle_language = SUBTITLE_LANGUAGE_ZH
    host.free_access_enabled = True
    host.always_on_top_enabled = False
    host._hide_to_tray = lambda: None
    host._show_from_tray = lambda: None
    host._toggle_chinese_subtitles = lambda _checked: None
    host._toggle_free_access = lambda _checked: None
    host._toggle_always_on_top = lambda _checked: None
    host.show_history = lambda: None
    host.show_runtime_log = lambda: None
    host.show_settings = lambda: None
    host.show()
    app.processEvents()

    menu = PetWindow._build_menu(host)  # type: ignore[arg-type]
    actions = [action for action in menu.actions() if not action.isSeparator()]
    texts = [action.text() for action in actions]
    checkable_texts = [action.text() for action in actions if action.isCheckable()]

    assert texts[0] == "隐藏至托盘"
    assert "启用模型视觉" not in texts
    assert "允许自主看屏幕" not in texts
    assert "自由访问权限" not in texts
    assert "运行日志" in texts
    assert "显示中文字幕" in checkable_texts
    assert "完整访问权限" in checkable_texts
    assert "保持置顶" in checkable_texts
    assert len(checkable_texts) == 3
    stylesheet = build_pet_window_stylesheet(DEFAULT_THEME_SETTINGS)
    assert "QMenu {" in stylesheet
    assert "QMenu::item:selected" in stylesheet
    assert "QMenu::separator" in stylesheet
    assert "QMenu::indicator:checked" in stylesheet
    assert "menu-check.svg" in stylesheet

    menu.deleteLater()
    host.deleteLater()
    app.processEvents()


def test_pet_window_menu_shows_restore_action_when_hidden() -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qtwidgets = pytest.importorskip("PySide6.QtWidgets")
    if not hasattr(qtwidgets, "QApplication") or not hasattr(qtwidgets, "QWidget"):
        pytest.skip("当前测试环境只提供了 PySide6 stub。")

    from app.ui.pet_window import PetWindow, SUBTITLE_LANGUAGE_ZH

    QApplication = qtwidgets.QApplication
    QWidget = qtwidgets.QWidget
    app = QApplication.instance() or QApplication([])
    host = QWidget()
    host.subtitle_language = SUBTITLE_LANGUAGE_ZH
    host.free_access_enabled = True
    host.always_on_top_enabled = False
    host._hide_to_tray = lambda: None
    host._show_from_tray = lambda: None
    host._toggle_chinese_subtitles = lambda _checked: None
    host._toggle_free_access = lambda _checked: None
    host._toggle_always_on_top = lambda _checked: None
    host.show_history = lambda: None
    host.show_runtime_log = lambda: None
    host.show_settings = lambda: None

    menu = PetWindow._build_menu(host)  # type: ignore[arg-type]
    actions = [action for action in menu.actions() if not action.isSeparator()]

    assert actions[0].text() == "显示桌宠"

    menu.deleteLater()
    host.deleteLater()
    app.processEvents()


def test_show_runtime_log_uses_non_modal_show(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qtwidgets = pytest.importorskip("PySide6.QtWidgets")
    if not hasattr(qtwidgets, "QWidget"):
        pytest.skip("当前测试环境只提供了 PySide6 stub。")

    import app.ui.pet_window as pet_window_module

    events: list[str] = []

    class RuntimeLogWindowStub:
        def __init__(self, **kwargs):  # type: ignore[no-untyped-def]
            self.kwargs = kwargs
            self.visible = False

        def set_theme_settings(self, settings):  # type: ignore[no-untyped-def]
            events.append("theme")
            self.theme_settings = settings

        def refresh(self, *, reset: bool = False) -> None:
            events.append(f"refresh:{reset}")

        def show(self) -> None:
            events.append("show")
            self.visible = True

        def raise_(self) -> None:
            events.append("raise")

        def activateWindow(self) -> None:
            events.append("activate")

        def exec(self):  # type: ignore[no-untyped-def]
            raise AssertionError("运行日志窗口不应使用 exec() 打开")

    class Host(qtwidgets.QWidget):
        show_runtime_log = pet_window_module.PetWindow.show_runtime_log
        _any_dialog_open = pet_window_module.PetWindow._any_dialog_open

    monkeypatch.setattr(pet_window_module, "RuntimeLogWindow", RuntimeLogWindowStub)

    app = qtwidgets.QApplication.instance() or qtwidgets.QApplication([])
    host = Host()
    host.theme_settings = DEFAULT_THEME_SETTINGS
    host.runtime_log_window = None
    host.settings_dialog = None
    host.history_window = None

    host.show_runtime_log()

    assert events == ["theme", "refresh:True", "show", "raise", "activate"]
    assert host.runtime_log_window.kwargs["parent"] is host
    assert host._any_dialog_open() is False

    host.deleteLater()
    app.processEvents()


def test_runtime_log_window_is_non_modal() -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qtwidgets = pytest.importorskip("PySide6.QtWidgets")
    qtcore = pytest.importorskip("PySide6.QtCore")
    if not hasattr(qtwidgets, "QApplication"):
        pytest.skip("当前测试环境只提供了 PySide6 stub。")

    from app.ui.log_window import RuntimeLogWindow

    app = qtwidgets.QApplication.instance() or qtwidgets.QApplication([])
    window = RuntimeLogWindow(theme_settings=DEFAULT_THEME_SETTINGS)

    assert window.windowModality() == qtcore.Qt.WindowModality.NonModal
    assert window.tabs.count() == 2
    assert window.tabs.tabText(0) == "软件"
    assert window.tabs.tabText(1) == "TTS"
    assert "runtimeLogPage" in window.styleSheet()
    assert "QCheckBox::indicator:checked" in window.styleSheet()
    assert DEFAULT_THEME_SETTINGS.page_background_color in window.styleSheet()

    window.close()
    window.deleteLater()
    app.processEvents()


def test_runtime_log_window_collapses_consecutive_duplicate_rows() -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qtwidgets = pytest.importorskip("PySide6.QtWidgets")
    qtcore = pytest.importorskip("PySide6.QtCore")
    if not hasattr(qtwidgets, "QApplication"):
        pytest.skip("当前测试环境只提供了 PySide6 stub。")

    from app.core.gui_log import GUI_LOG_LEVEL_INFO, GUI_LOG_SCOPE_PROGRAM, GuiLogBuffer
    from app.ui.log_window import RuntimeLogWindow

    buffer = GuiLogBuffer()
    for _index in range(2):
        buffer.append(
            timestamp="2026-06-11T18:43:44+08:00",
            scope=GUI_LOG_SCOPE_PROGRAM,
            level=GUI_LOG_LEVEL_INFO,
            category="TTS",
            message="准备：GPT-SoVITS 服务已就绪",
        )
    buffer.append(
        timestamp="2026-06-11T18:43:45+08:00",
        scope=GUI_LOG_SCOPE_PROGRAM,
        level=GUI_LOG_LEVEL_INFO,
        category="TTS",
        message="准备：TTS 角色权重切换完成",
    )

    app = qtwidgets.QApplication.instance() or qtwidgets.QApplication([])
    window = RuntimeLogWindow(log_buffer=buffer, theme_settings=DEFAULT_THEME_SETTINGS)
    window.refresh(reset=True)

    assert window.program_list.count() == 2
    first_item = window.program_list.item(0)
    assert first_item.text() == "18:43:44  [TTS]  准备：GPT-SoVITS 服务已就绪  ×2"
    assert "信息" not in first_item.text()
    assert "连续重复：2 次" in str(first_item.data(qtcore.Qt.ItemDataRole.UserRole))

    window.close()
    window.deleteLater()
    app.processEvents()


def test_runtime_log_window_row_shows_category_level_and_detail_summary() -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qtwidgets = pytest.importorskip("PySide6.QtWidgets")
    if not hasattr(qtwidgets, "QApplication"):
        pytest.skip("当前测试环境只提供了 PySide6 stub。")

    from app.core.gui_log import (
        GUI_LOG_LEVEL_ERROR,
        GUI_LOG_LEVEL_INFO,
        GUI_LOG_SCOPE_PROGRAM,
        GuiLogBuffer,
    )
    from app.ui.log_window import RuntimeLogWindow

    buffer = GuiLogBuffer()
    buffer.append(
        timestamp="2026-06-11T18:51:27+08:00",
        scope=GUI_LOG_SCOPE_PROGRAM,
        level=GUI_LOG_LEVEL_INFO,
        category="API",
        message="发送请求",
        detail='{"model": "gpt-4o-mini", "stream": true, "messages": {"type": "list", "items": 4}}',
    )
    buffer.append(
        timestamp="2026-06-11T18:51:31+08:00",
        scope=GUI_LOG_SCOPE_PROGRAM,
        level=GUI_LOG_LEVEL_ERROR,
        category="API",
        message="请求失败",
        detail='{"error": "connection timeout", "api_key": "<redacted>"}',
    )

    app = qtwidgets.QApplication.instance() or qtwidgets.QApplication([])
    window = RuntimeLogWindow(log_buffer=buffer, theme_settings=DEFAULT_THEME_SETTINGS)
    window.refresh(reset=True)

    info_item = window.program_list.item(0)
    # 行内带分类标签，detail 中的标量字段提取为行尾摘要，嵌套结构与脱敏值不出现
    assert info_item.text() == "18:51:27  [API]  发送请求  model=gpt-4o-mini · stream=True"
    error_item = window.program_list.item(1)
    assert error_item.text() == "18:51:31  [API]  错误  请求失败  error=connection timeout"
    assert "<redacted>" not in error_item.text()

    # 两个列表都应使用自定义 delegate 做分层着色
    assert window.program_list.itemDelegate() is window._item_delegate
    assert window.tts_list.itemDelegate() is window._item_delegate

    window.close()
    window.deleteLater()
    app.processEvents()


def test_runtime_log_window_shows_tts_text_preview_as_detail() -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qtwidgets = pytest.importorskip("PySide6.QtWidgets")
    if not hasattr(qtwidgets, "QApplication"):
        pytest.skip("当前测试环境只提供了 PySide6 stub。")

    from app.core.gui_log import GUI_LOG_LEVEL_INFO, GUI_LOG_SCOPE_PROGRAM, GuiLogBuffer
    from app.ui.log_window import RuntimeLogWindow

    buffer = GuiLogBuffer()
    buffer.append(
        timestamp="2026-06-11T18:51:35+08:00",
        scope=GUI_LOG_SCOPE_PROGRAM,
        level=GUI_LOG_LEVEL_INFO,
        category="TTS",
        message="开始播放",
        detail='{"audio_path": "x.wav"}',
        text_preview="今天天气真好喵",
    )

    app = qtwidgets.QApplication.instance() or qtwidgets.QApplication([])
    window = RuntimeLogWindow(log_buffer=buffer, theme_settings=DEFAULT_THEME_SETTINGS)
    window.refresh(reset=True)

    item = window.program_list.item(0)
    # 合成/播放记录的灰字摘要优先显示文本内容而不是 detail 字段
    assert item.text() == "18:51:35  [TTS]  开始播放  「今天天气真好喵」"
    assert "文本：今天天气真好喵" in item.toolTip()

    window.close()
    window.deleteLater()
    app.processEvents()


def test_runtime_log_window_updates_progress_rows_in_place() -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qtwidgets = pytest.importorskip("PySide6.QtWidgets")
    if not hasattr(qtwidgets, "QApplication"):
        pytest.skip("当前测试环境只提供了 PySide6 stub。")

    from app.core.gui_log import GUI_LOG_LEVEL_INFO, GUI_LOG_SCOPE_TTS, GuiLogBuffer
    from app.ui.log_window import RuntimeLogWindow

    buffer = GuiLogBuffer()
    buffer.append(
        timestamp="2026-06-11T18:51:31+08:00",
        scope=GUI_LOG_SCOPE_TTS,
        level=GUI_LOG_LEVEL_INFO,
        category="GPT-SoVITS 服务",
        message="语义 token 预测 4%（60/1500，104.91 it/s）",
        merge_key="semantic-token-progress",
    )

    app = qtwidgets.QApplication.instance() or qtwidgets.QApplication([])
    window = RuntimeLogWindow(log_buffer=buffer, theme_settings=DEFAULT_THEME_SETTINGS)
    window.refresh(reset=True)
    assert window.tts_list.count() == 1

    # 已展示的进度行在新进度到达后应原地刷新，而不是追加新行
    buffer.append(
        timestamp="2026-06-11T18:51:34+08:00",
        scope=GUI_LOG_SCOPE_TTS,
        level=GUI_LOG_LEVEL_INFO,
        category="GPT-SoVITS 服务",
        message="语义 token 预测 24%（363/1500，105.23 it/s）",
        merge_key="semantic-token-progress",
    )
    window.refresh()

    assert window.tts_list.count() == 1
    assert "24%" in window.tts_list.item(0).text()

    window.close()
    window.deleteLater()
    app.processEvents()


def test_pet_window_status_tray_icon_is_not_empty() -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qtwidgets = pytest.importorskip("PySide6.QtWidgets")
    if not hasattr(qtwidgets, "QApplication"):
        pytest.skip("当前测试环境只提供了 PySide6 stub。")

    from app.ui.pet_window import _build_status_tray_icon

    QApplication = qtwidgets.QApplication
    app = QApplication.instance() or QApplication([])

    icon = _build_status_tray_icon("#d55b91")

    assert not icon.isNull()
    app.processEvents()


def test_memory_status_does_not_use_tray_balloon(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    import app.ui.pet_window as pet_window_module
    from app.ui.pet_window import PetWindow

    class TrayIconStub:
        def __init__(self) -> None:
            self.messages: list[tuple[object, ...]] = []

        def isVisible(self) -> bool:
            return True

        def showMessage(self, *args) -> None:  # type: ignore[no-untyped-def]
            self.messages.append(args)

    class SubtitleControllerStub:
        def __init__(self) -> None:
            self.messages: list[str] = []

        def show_text_immediately(self, message: str) -> None:
            self.messages.append(message)

    single_shots: list[tuple[int, object]] = []
    warnings: list[tuple[str, str]] = []
    monkeypatch.setattr(
        pet_window_module.QTimer,
        "singleShot",
        lambda delay, callback: single_shots.append((delay, callback)),
    )
    monkeypatch.setattr(
        pet_window_module,
        "show_themed_warning",
        lambda _parent, title, text, **_kwargs: warnings.append((title, text)),
    )
    window = type("WindowStub", (), {})()
    window.memory_status_message_active = False
    window.memory_status_last_status = ""
    window.memory_status_last_message = ""
    window.memory_failure_dialog_last_message = ""
    window.memory_failure_dialog_pending_message = ""
    window.startup_initializing = False
    window.active_interaction_id = None
    window.reply_history_review_active = False
    window.subtitle_controller = SubtitleControllerStub()
    window.tray_icon = TrayIconStub()
    window.isVisible = lambda: True
    window._restore_memory_status_speech = lambda: None
    window._should_defer_memory_failure_dialog = (
        lambda: PetWindow._should_defer_memory_failure_dialog(window)
    )
    window._display_memory_failure_dialog = (
        lambda message: PetWindow._display_memory_failure_dialog(window, message)
    )
    window._show_memory_failure_dialog = lambda message: PetWindow._show_memory_failure_dialog(window, message)

    for status in ("loading", "reloading", "failed"):
        PetWindow._show_memory_status_message(window, status, f"{status} message")
    PetWindow._show_memory_ready_message(window, "ready message")

    assert window.tray_icon.messages == []
    assert window.subtitle_controller.messages == [
        "loading message",
        "reloading message",
        "failed message",
    ]
    assert warnings == [("记忆模型下载失败", "failed message")]
    assert single_shots == [(pet_window_module.MEMORY_STATUS_DISPLAY_MS, window._restore_memory_status_speech)]


def test_memory_failure_dialog_is_deferred_until_startup_window_is_visible(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    import app.ui.pet_window as pet_window_module
    from app.ui.pet_window import PetWindow

    class SubtitleControllerStub:
        def __init__(self) -> None:
            self.messages: list[str] = []

        def show_text_immediately(self, message: str) -> None:
            self.messages.append(message)

    warnings: list[tuple[str, str]] = []
    monkeypatch.setattr(
        pet_window_module,
        "show_themed_warning",
        lambda _parent, title, text, **_kwargs: warnings.append((title, text)),
    )
    window = type("WindowStub", (), {})()
    window.memory_status_message_active = False
    window.memory_status_last_status = ""
    window.memory_status_last_message = ""
    window.memory_failure_dialog_last_message = ""
    window.memory_failure_dialog_pending_message = ""
    window.startup_initializing = True
    window.active_interaction_id = None
    window.reply_history_review_active = False
    window.subtitle_controller = SubtitleControllerStub()
    visible = {"value": False}
    window.isVisible = lambda: visible["value"]
    window._should_defer_memory_failure_dialog = (
        lambda: PetWindow._should_defer_memory_failure_dialog(window)
    )
    window._display_memory_failure_dialog = (
        lambda message: PetWindow._display_memory_failure_dialog(window, message)
    )
    window._show_memory_failure_dialog = lambda message: PetWindow._show_memory_failure_dialog(window, message)
    window._show_pending_memory_failure_dialog = (
        lambda: PetWindow._show_pending_memory_failure_dialog(window)
    )

    PetWindow._show_memory_status_message(window, "failed", "download failed")

    assert warnings == []
    assert window.subtitle_controller.messages == []
    assert window.memory_failure_dialog_pending_message == "download failed"

    window.startup_initializing = False
    visible["value"] = True
    PetWindow._show_pending_memory_status_after_startup(window)

    assert window.subtitle_controller.messages == ["download failed"]
    assert warnings == [("记忆模型下载失败", "download failed")]
    assert window.memory_failure_dialog_pending_message == ""


def test_message_box_stylesheet_contains_configured_theme_colors() -> None:
    theme = ThemeSettings(
        primary_color="#112233",
        primary_hover_color="#223344",
        accent_color="#334455",
        text_color="#445566",
        page_background_color="#ddeeff",
        border_color="#556677",
    )

    stylesheet = build_message_box_stylesheet(theme)

    assert "#112233" in stylesheet
    assert "#223344" in stylesheet
    assert "#334455" in stylesheet
    assert "#445566" in stylesheet
    assert "#ddeeff" in stylesheet


def test_pet_window_hide_and_show_to_tray_tracks_hidden_state() -> None:
    from app.ui.pet_window import PetWindow
    from app.agent.runtime_events import RuntimeEventQueue

    class MinimalWindow:
        _hide_to_tray = PetWindow._hide_to_tray
        _show_from_tray = PetWindow._show_from_tray
        emit_runtime_event = PetWindow.emit_runtime_event

        def __init__(self) -> None:
            self.hidden_to_tray = False
            self.startup_initializing = False
            self.pet_hidden_at = None
            self.runtime_event_queue = RuntimeEventQueue()
            self.runtime_event_log = None
            self.events: list[str] = []

        def hide(self) -> None:
            self.events.append("hide")

        def show(self) -> None:
            self.events.append("show")

        def raise_(self) -> None:
            self.events.append("raise")

        def activateWindow(self) -> None:
            self.events.append("activate")

        def _refresh_tray_menu(self) -> None:
            self.events.append("refresh")

    window = MinimalWindow()

    window._hide_to_tray()
    assert window.hidden_to_tray is True
    assert window.events == ["hide", "refresh"]

    window._show_from_tray()
    assert window.hidden_to_tray is False
    assert window.events == ["hide", "refresh", "show", "raise", "activate", "refresh"]


class _RecordingEventLog:
    """记录被落盘事件的假 RuntimeEventLog，用于断言 emit 行为。"""

    def __init__(self) -> None:
        self.appended: list = []

    def append(self, event) -> None:  # type: ignore[no-untyped-def]
        self.appended.append(event)

    def load_startup_carryover(self):  # type: ignore[no-untyped-def]
        return None


def test_hide_to_tray_emits_pet_hidden_runtime_event() -> None:
    from app.ui.pet_window import PetWindow
    from app.agent.runtime_events import PET_HIDDEN, RuntimeEventQueue

    class MinimalWindow:
        _hide_to_tray = PetWindow._hide_to_tray
        emit_runtime_event = PetWindow.emit_runtime_event

        def __init__(self) -> None:
            self.hidden_to_tray = False
            self.pet_hidden_at = None
            self.runtime_event_queue = RuntimeEventQueue()
            self.runtime_event_log = _RecordingEventLog()

        def hide(self) -> None:
            pass

        def _refresh_tray_menu(self) -> None:
            pass

    window = MinimalWindow()
    window._hide_to_tray()

    assert window.pet_hidden_at is not None
    assert [e.event_type for e in window.runtime_event_queue.peek()] == [PET_HIDDEN]
    assert [e.event_type for e in window.runtime_event_log.appended] == [PET_HIDDEN]


def test_show_from_tray_emits_reopened_with_hidden_duration() -> None:
    import time

    from app.ui.pet_window import PetWindow
    from app.agent.runtime_events import PET_REOPENED, RuntimeEventQueue

    class MinimalWindow:
        _show_from_tray = PetWindow._show_from_tray
        emit_runtime_event = PetWindow.emit_runtime_event

        def __init__(self) -> None:
            self.hidden_to_tray = True
            self.startup_initializing = False
            self.pet_hidden_at = time.perf_counter() - 3
            self.runtime_event_queue = RuntimeEventQueue()
            self.runtime_event_log = _RecordingEventLog()

        def show(self) -> None:
            pass

        def raise_(self) -> None:
            pass

        def activateWindow(self) -> None:
            pass

        def _refresh_tray_menu(self) -> None:
            pass

    window = MinimalWindow()
    window._show_from_tray()

    drained = window.runtime_event_queue.drain()
    assert len(drained) == 1
    assert drained[0].event_type == PET_REOPENED
    assert drained[0].metadata["hidden_duration"] >= 2
    assert window.pet_hidden_at is None


def test_show_from_tray_skips_reopened_during_startup() -> None:
    from app.ui.pet_window import PetWindow
    from app.agent.runtime_events import RuntimeEventQueue

    class MinimalWindow:
        _show_from_tray = PetWindow._show_from_tray
        emit_runtime_event = PetWindow.emit_runtime_event

        def __init__(self) -> None:
            self.hidden_to_tray = True
            self.startup_initializing = True
            self.pet_hidden_at = None
            self.runtime_event_queue = RuntimeEventQueue()
            self.runtime_event_log = _RecordingEventLog()

        def show(self) -> None:
            pass

        def raise_(self) -> None:
            pass

        def activateWindow(self) -> None:
            pass

        def _refresh_tray_menu(self) -> None:
            pass

    window = MinimalWindow()
    window._show_from_tray()

    assert window.runtime_event_queue.drain() == []
    assert window.runtime_event_log.appended == []


def test_emit_app_closed_event_logs_once_with_interrupted_flag() -> None:
    from app.ui.pet_window import PetWindow
    from app.agent.runtime_events import APP_CLOSED, RuntimeEventQueue

    class MinimalWindow:
        emit_runtime_event = PetWindow.emit_runtime_event
        _emit_app_closed_event = PetWindow._emit_app_closed_event

        def __init__(self) -> None:
            self.worker_thread = object()  # 模拟回复进行中被关闭
            self._runtime_app_closed_logged = False
            self.runtime_event_queue = RuntimeEventQueue()
            self.runtime_event_log = _RecordingEventLog()

    window = MinimalWindow()
    window._emit_app_closed_event()
    window._emit_app_closed_event()  # 退出链路多次触发，应被一次性保护拦截

    appended = window.runtime_event_log.appended
    assert [e.event_type for e in appended] == [APP_CLOSED]
    assert appended[0].metadata["interrupted_reply"] is True
    # app.closed 用 inject=False，不进内存队列
    assert len(window.runtime_event_queue) == 0


def test_close_external_tools_cancels_and_keeps_lingering_thread() -> None:
    from app.ui.pet_window import PetWindow, TRANSIENT_PROGRESS_MESSAGE_KEY

    class SignalStub:
        def __init__(self) -> None:
            self.callbacks = []

        def connect(self, callback):  # type: ignore[no-untyped-def]
            self.callbacks.append(callback)

    class ThreadStub:
        def __init__(self) -> None:
            self.finished = SignalStub()
            self.interrupted = False
            self.quit_called = False
            self.waits: list[int] = []

        def requestInterruption(self) -> None:
            self.interrupted = True

        def isRunning(self) -> bool:
            return True

        def quit(self) -> None:
            self.quit_called = True

        def wait(self, timeout: int) -> bool:
            self.waits.append(timeout)
            return False

    class WorkerStub:
        def __init__(self) -> None:
            self.cancelled = False

        def cancel(self) -> None:
            self.cancelled = True

    class SubtitleStub:
        def __init__(self) -> None:
            self.cancelled = False

        def cancel_reply_flow(self) -> None:
            self.cancelled = True

    class MinimalWindow:
        close_external_tools = PetWindow.close_external_tools
        _shutdown_qthread = PetWindow._shutdown_qthread
        _keep_shutdown_lingering_thread = PetWindow._keep_shutdown_lingering_thread
        _release_shutdown_lingering_thread = PetWindow._release_shutdown_lingering_thread

    window = MinimalWindow()
    thread = ThreadStub()
    worker = WorkerStub()
    subtitle = SubtitleStub()
    window._shutdown_in_progress = False
    window._shutdown_lingering_threads = []
    window.messages = [
        {"role": "assistant", "content": "途中", TRANSIENT_PROGRESS_MESSAGE_KEY: True}
    ]
    window.subtitle_controller = subtitle
    window.worker_thread = thread
    window.worker = worker
    window.memory_curation_thread = None
    window.memory_curation_worker = None
    window.deferred_startup_thread = None
    window.deferred_startup_worker = None
    window.tts_ready_warmup_thread = None
    window.tts_ready_warmup_worker = None
    window.screen_observation_encode_thread = None
    window.screen_observation_encode_worker = None
    window._emit_app_closed_event = lambda: None
    window._stop_speaking_state_watchdog = lambda: None
    window.close_tts_tools = lambda: None
    window.close_mcp_tools = lambda: None
    window.close_plugins = lambda: None

    window.close_external_tools()

    assert window._shutdown_in_progress is True
    assert worker.cancelled is True
    assert thread.interrupted is True
    assert thread.quit_called is True
    assert thread.waits == [1000]
    assert window._shutdown_lingering_threads == [(thread, worker)]
    assert window.messages == []
    assert subtitle.cancelled is True


def test_shutdown_ignores_late_progress_and_reply() -> None:
    from app.agent import AgentProgress, AgentResult
    from app.llm.chat_reply import parse_chat_reply
    from app.ui.pet_window import PetWindow, TRANSIENT_PROGRESS_MESSAGE_KEY

    class MinimalWindow:
        _handle_progress_reply = PetWindow._handle_progress_reply
        _handle_reply = PetWindow._handle_reply

    window = MinimalWindow()
    window._shutdown_in_progress = True
    window.messages = [
        {"role": "assistant", "content": "途中", TRANSIENT_PROGRESS_MESSAGE_KEY: True}
    ]
    progress = AgentProgress(
        reply=parse_chat_reply('{"segments":[{"ja":"見るね。","zh":"我看看。","tone":"中性"}]}')
    )
    result = AgentResult(
        reply=parse_chat_reply('{"segments":[{"ja":"終わり。","zh":"结束。","tone":"中性"}]}')
    )

    window._handle_progress_reply(progress)
    window._handle_reply(result)

    assert window.messages == []


def test_event_error_cleans_transient_progress_during_shutdown() -> None:
    from app.ui.pet_window import PetWindow, TRANSIENT_PROGRESS_MESSAGE_KEY

    class MinimalWindow:
        _handle_event_error = PetWindow._handle_event_error

        def _clear_active_event(self) -> None:
            self.active_event_type = ""

    window = MinimalWindow()
    window._shutdown_in_progress = True
    window.active_event_type = "custom"
    window.messages = [
        {"role": "assistant", "content": "途中", TRANSIENT_PROGRESS_MESSAGE_KEY: True}
    ]

    window._handle_event_error("late error")

    assert window.messages == []
    assert window.active_event_type == ""


def test_speaking_state_timeout_cancels_reply_and_ends_interaction() -> None:
    from app.ui.pet_window import PetWindow
    from app.ui.state import PetUiState

    class UiStateStub:
        state = PetUiState.SPEAKING

        def finish(self, reason: str) -> None:
            raise AssertionError(f"active interaction should use _end_interaction, got {reason}")

    class SubtitleStub:
        def __init__(self) -> None:
            self.cancelled = False

        def is_reply_sequence_active(self) -> bool:
            return True

        def cancel_reply_flow(self) -> None:
            self.cancelled = True

    class MinimalWindow:
        _handle_speaking_state_timeout = PetWindow._handle_speaking_state_timeout

    window = MinimalWindow()
    subtitle = SubtitleStub()
    outcomes = []
    window.ui_state = UiStateStub()
    window.subtitle_controller = subtitle
    window.active_interaction_id = "interaction-1"
    window._end_interaction = lambda outcome: outcomes.append(outcome)

    window._handle_speaking_state_timeout()

    assert subtitle.cancelled is True
    assert outcomes == ["speaking_timeout"]


def test_pet_window_application_activation_restores_when_hidden_to_tray(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    import app.ui.pet_window as pet_window_module
    from app.ui.pet_window import PetWindow

    events: list[str] = []
    monkeypatch.setattr(
        pet_window_module.QTimer,
        "singleShot",
        lambda delay, callback: events.append(f"timer:{delay}") or callback(),
    )

    class MinimalWindow:
        _handle_application_activated = PetWindow._handle_application_activated

        def __init__(self) -> None:
            self.hidden_to_tray = True

        def _show_from_tray(self) -> None:
            self.hidden_to_tray = False
            events.append("show")

    window = MinimalWindow()

    window._handle_application_activated()

    assert window.hidden_to_tray is False
    assert events == ["timer:0", "show"]


def test_pet_window_application_activation_ignores_visible_window(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    import app.ui.pet_window as pet_window_module
    from app.ui.pet_window import PetWindow

    events: list[str] = []
    monkeypatch.setattr(
        pet_window_module.QTimer,
        "singleShot",
        lambda _delay, _callback: events.append("timer"),
    )

    class MinimalWindow:
        _handle_application_activated = PetWindow._handle_application_activated
        hidden_to_tray = False

        def _show_from_tray(self) -> None:
            events.append("show")

    MinimalWindow()._handle_application_activated()

    assert events == []


def test_pet_window_context_menu_opens_on_right_release_not_press() -> None:
    qtcore = pytest.importorskip("PySide6.QtCore")
    from app.ui.pet_window import PetWindow

    class MouseEventStub:
        def __init__(self) -> None:
            self.accepted = False

        def button(self):  # type: ignore[no-untyped-def]
            return qtcore.Qt.MouseButton.RightButton

        def position(self):  # type: ignore[no-untyped-def]
            return qtcore.QPointF(12, 24)

        def accept(self) -> None:
            self.accepted = True

    class MinimalWindow:
        _handle_mouse_press = PetWindow._handle_mouse_press
        _handle_mouse_release = PetWindow._handle_mouse_release

        def __init__(self) -> None:
            self.context_menu_positions: list[object] = []

        def _show_context_menu(self, position) -> None:  # type: ignore[no-untyped-def]
            self.context_menu_positions.append(position)

    window = MinimalWindow()
    press_event = MouseEventStub()
    release_event = MouseEventStub()

    assert window._handle_mouse_press(press_event) is True
    assert press_event.accepted
    assert window.context_menu_positions == []

    assert window._handle_mouse_release(release_event) is True
    assert release_event.accepted
    assert window.context_menu_positions == [release_event.position().toPoint()]


def test_pet_window_left_press_clears_input_focus_without_clearing_text() -> None:
    qtcore = pytest.importorskip("PySide6.QtCore")
    from app.ui.pet_window import PetWindow

    class MouseEventStub:
        def __init__(self) -> None:
            self.accepted = False
            self._position = qtcore.QPointF(40, 60)

        def button(self):  # type: ignore[no-untyped-def]
            return qtcore.Qt.MouseButton.LeftButton

        def position(self):  # type: ignore[no-untyped-def]
            return self._position

        def accept(self) -> None:
            self.accepted = True

    class InputStub:
        def __init__(self) -> None:
            self._text = "还没发出去的话"
            self.focused = True
            self.clear_focus_count = 0

        def hasFocus(self) -> bool:  # noqa: N802 - Qt API 兼容命名。
            return self.focused

        def clearFocus(self) -> None:  # noqa: N802 - Qt API 兼容命名。
            self.focused = False
            self.clear_focus_count += 1

        def text(self) -> str:
            return self._text

    class MinimalWindow:
        _handle_mouse_press = PetWindow._handle_mouse_press
        _drag_anchor_from_event = PetWindow._drag_anchor_from_event
        _clear_input_focus_for_pet_interaction = PetWindow._clear_input_focus_for_pet_interaction

        def __init__(self) -> None:
            self.input_edit = InputStub()
            self.drag_anchor = None

    window = MinimalWindow()
    press_event = MouseEventStub()

    assert window._handle_mouse_press(press_event) is True
    assert window.input_edit.clear_focus_count == 1
    assert not window.input_edit.hasFocus()
    assert window.input_edit.text() == "还没发出去的话"
    assert window.drag_anchor == qtcore.QPoint(40, 60)
    assert press_event.accepted


def test_pet_window_drag_uses_window_local_anchor_not_frame_geometry() -> None:
    qtcore = pytest.importorskip("PySide6.QtCore")
    from app.ui.pet_window import PetWindow

    class MouseEventStub:
        def __init__(
            self,
            *,
            position: tuple[int, int],
            global_position: tuple[int, int],
            button=None,  # type: ignore[no-untyped-def]
            buttons=None,  # type: ignore[no-untyped-def]
        ) -> None:
            self.accepted = False
            self._position = qtcore.QPointF(*position)
            self._global_position = qtcore.QPointF(*global_position)
            self._button = button or qtcore.Qt.MouseButton.LeftButton
            self._buttons = buttons or qtcore.Qt.MouseButton.LeftButton

        def button(self):  # type: ignore[no-untyped-def]
            return self._button

        def buttons(self):  # type: ignore[no-untyped-def]
            return self._buttons

        def position(self):  # type: ignore[no-untyped-def]
            return self._position

        def globalPosition(self):  # type: ignore[no-untyped-def]
            return self._global_position

        def accept(self) -> None:
            self.accepted = True

    class _DragAnimatorStub:
        def suspend_for_drag(self) -> None:
            pass

        def resume_after_drag(self) -> None:
            pass

    class MinimalWindow:
        _handle_mouse_press = PetWindow._handle_mouse_press
        _handle_mouse_move = PetWindow._handle_mouse_move
        _handle_mouse_release = PetWindow._handle_mouse_release
        _drag_anchor_from_event = PetWindow._drag_anchor_from_event
        _clear_input_focus_for_pet_interaction = PetWindow._clear_input_focus_for_pet_interaction

        def __init__(self) -> None:
            self.drag_anchor = None
            self._dragging = False
            self.input_bar_animator = _DragAnimatorStub()
            self.move_positions: list[object] = []

        def frameGeometry(self):  # type: ignore[no-untyped-def]
            raise AssertionError("拖拽不应依赖 frameGeometry")

        def move(self, position) -> None:  # type: ignore[no-untyped-def]
            self.move_positions.append(position)

        def _finish_drag_resume(self) -> None:
            pass

    window = MinimalWindow()
    press_event = MouseEventStub(position=(40, 60), global_position=(240, 160))
    move_event = MouseEventStub(position=(45, 65), global_position=(300, 220))
    release_event = MouseEventStub(position=(45, 65), global_position=(300, 220))

    assert window._handle_mouse_press(press_event) is True
    assert window.drag_anchor == qtcore.QPoint(40, 60)
    assert press_event.accepted

    assert window._handle_mouse_move(move_event) is True
    assert window.move_positions == [qtcore.QPoint(260, 160)]
    assert move_event.accepted

    assert window._handle_mouse_release(release_event) is True
    assert window.drag_anchor is None
    assert release_event.accepted


def test_pet_window_drag_maps_child_widget_anchor_to_window_coordinates() -> None:
    qtcore = pytest.importorskip("PySide6.QtCore")
    from app.ui.pet_window import PetWindow

    class MouseEventStub:
        accepted = False

        def __init__(self, position: tuple[int, int], global_position: tuple[int, int]) -> None:
            self._position = qtcore.QPointF(*position)
            self._global_position = qtcore.QPointF(*global_position)

        def button(self):  # type: ignore[no-untyped-def]
            return qtcore.Qt.MouseButton.LeftButton

        def buttons(self):  # type: ignore[no-untyped-def]
            return qtcore.Qt.MouseButton.LeftButton

        def position(self):  # type: ignore[no-untyped-def]
            return self._position

        def globalPosition(self):  # type: ignore[no-untyped-def]
            return self._global_position

        def accept(self) -> None:
            self.accepted = True

    class ChildWidgetStub:
        def mapToGlobal(self, position):  # type: ignore[no-untyped-def]
            return position + qtcore.QPoint(200, 160)

    class _DragAnimatorStub:
        def suspend_for_drag(self) -> None:
            pass

        def resume_after_drag(self) -> None:
            pass

    class MinimalWindow:
        _handle_mouse_press = PetWindow._handle_mouse_press
        _handle_mouse_move = PetWindow._handle_mouse_move
        _drag_anchor_from_event = PetWindow._drag_anchor_from_event
        _clear_input_focus_for_pet_interaction = PetWindow._clear_input_focus_for_pet_interaction

        def __init__(self) -> None:
            self.drag_anchor = None
            self._dragging = False
            self.input_bar_animator = _DragAnimatorStub()
            self.move_positions: list[object] = []

        def mapFromGlobal(self, position):  # type: ignore[no-untyped-def]
            return position - qtcore.QPoint(100, 80)

        def move(self, position) -> None:  # type: ignore[no-untyped-def]
            self.move_positions.append(position)

    window = MinimalWindow()
    child = ChildWidgetStub()
    press_event = MouseEventStub(position=(10, 15), global_position=(300, 200))
    move_event = MouseEventStub(position=(15, 20), global_position=(350, 260))

    assert window._handle_mouse_press(press_event, child) is True
    assert window.drag_anchor == qtcore.QPoint(110, 95)

    assert window._handle_mouse_move(move_event) is True
    assert window.move_positions == [qtcore.QPoint(240, 165)]


def test_pet_window_screen_change_restores_stage_geometry(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    qtcore = pytest.importorskip("PySide6.QtCore")
    qtwidgets = pytest.importorskip("PySide6.QtWidgets")
    if not hasattr(qtcore.QEvent.Type, "ScreenChangeInternal"):
        pytest.skip("当前 Qt 版本不提供 ScreenChangeInternal。")
    if not hasattr(qtwidgets, "QApplication") or not hasattr(qtwidgets, "QWidget"):
        pytest.skip("当前测试环境只提供了 PySide6 stub。")

    import app.ui.pet_window as pet_window_module
    from app.ui.pet_window import PetWindow

    QApplication = qtwidgets.QApplication
    QWidget = qtwidgets.QWidget
    app = QApplication.instance() or QApplication([])
    scheduled_callbacks: list[tuple[int, object]] = []
    monkeypatch.setattr(
        pet_window_module.QTimer,
        "singleShot",
        lambda delay, callback: scheduled_callbacks.append((delay, callback)),
    )

    class MinimalScreenChangeWindow(PetWindow):
        def __init__(self) -> None:
            QWidget.__init__(self)
            self.stage_size = (321, 234)
            self.layout_count = 0
            self.topmost_sync_count = 0

        def _apply_pet_layout(self, *, anchor_global=None) -> None:  # type: ignore[no-untyped-def]
            # 换屏恢复走统一布局；最小窗口无立绘，这里直接按 stage_size 复位并计数。
            self.resize(*self.stage_size)
            self.layout_count += 1

        def _schedule_native_topmost_sync(self) -> None:
            self.topmost_sync_count += 1

    window = MinimalScreenChangeWindow()
    window.resize(111, 222)
    window.layout_count = 0

    window.event(qtcore.QEvent(qtcore.QEvent.Type.ScreenChangeInternal))
    assert len(scheduled_callbacks) == 1
    assert scheduled_callbacks[0][0] == 0

    scheduled_callbacks[0][1]()

    assert window.size() == qtcore.QSize(321, 234)
    assert window.layout_count >= 1
    assert window.topmost_sync_count == 1

    window.deleteLater()
    app.processEvents()


def test_screen_change_event_check_tolerates_missing_qt_enum(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    import app.ui.pet_window as pet_window_module
    from app.ui.pet_window import _is_screen_change_event

    class FakeQEvent:
        class Type:
            pass

    class EventStub:
        def type(self) -> object:
            return object()

    monkeypatch.setattr(pet_window_module, "QEvent", FakeQEvent)

    assert not _is_screen_change_event(EventStub())


def test_reply_history_controls_use_capsule_sizing() -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qtwidgets = pytest.importorskip("PySide6.QtWidgets")
    if not all(hasattr(qtwidgets, name) for name in ("QApplication", "QFrame", "QToolButton")):
        pytest.skip("当前测试环境只提供了 PySide6 stub。")

    from app.ui.pet_window import (
        REPLY_HISTORY_BUTTON_SIZE,
        REPLY_HISTORY_NEXT_SYMBOL,
        REPLY_HISTORY_PANEL_HEIGHT,
        REPLY_HISTORY_PANEL_WIDTH,
        REPLY_HISTORY_PREVIOUS_SYMBOL,
        _configure_reply_history_button,
        _configure_reply_history_panel,
    )

    QApplication = qtwidgets.QApplication
    QFrame = qtwidgets.QFrame
    QToolButton = qtwidgets.QToolButton
    app = QApplication.instance() or QApplication([])
    panel = QFrame()
    previous_button = QToolButton(panel)
    next_button = QToolButton(panel)

    _configure_reply_history_panel(panel)
    _configure_reply_history_button(
        previous_button,
        text=REPLY_HISTORY_PREVIOUS_SYMBOL,
        tooltip="上一条历史消息",
    )
    _configure_reply_history_button(
        next_button,
        text=REPLY_HISTORY_NEXT_SYMBOL,
        tooltip="下一条历史消息",
    )

    assert panel.objectName() == "replyHistoryPanel"
    assert panel.minimumWidth() == REPLY_HISTORY_PANEL_WIDTH
    assert panel.maximumWidth() == REPLY_HISTORY_PANEL_WIDTH
    assert panel.minimumHeight() == REPLY_HISTORY_PANEL_HEIGHT
    assert panel.maximumHeight() == REPLY_HISTORY_PANEL_HEIGHT
    assert previous_button.objectName() == "replyHistoryButton"
    assert previous_button.text() == "▲"
    assert previous_button.toolTip() == "上一条历史消息"
    assert previous_button.minimumWidth() == REPLY_HISTORY_BUTTON_SIZE
    assert previous_button.maximumWidth() == REPLY_HISTORY_BUTTON_SIZE
    assert not previous_button.autoRaise()
    assert next_button.text() == "▼"
    assert next_button.toolTip() == "下一条历史消息"
    assert not next_button.autoRaise()
    stylesheet = build_pet_window_stylesheet(DEFAULT_THEME_SETTINGS)
    hover_start = stylesheet.index("#replyHistoryButton:hover")
    hover_end = stylesheet.index("#replyHistoryButton:disabled")
    hover_stylesheet = stylesheet[hover_start:hover_end]
    assert "background: transparent" in hover_stylesheet
    assert f"color: {DEFAULT_THEME_SETTINGS.accent_color}" in hover_stylesheet

    panel.deleteLater()
    app.processEvents()


def test_portrait_controller_scales_pixmap_by_configured_percent() -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qtwidgets = pytest.importorskip("PySide6.QtWidgets")
    qtgui = pytest.importorskip("PySide6.QtGui")
    qtcore = pytest.importorskip("PySide6.QtCore")
    if not all(
        hasattr(qtwidgets, name)
        for name in ("QApplication", "QGraphicsOpacityEffect", "QLabel", "QWidget")
    ):
        pytest.skip("当前测试环境只提供了 PySide6 stub。")

    from app.config.character_loader import CharacterProfile
    from app.ui.portrait_controller import PortraitController

    QApplication = qtwidgets.QApplication
    QGraphicsOpacityEffect = qtwidgets.QGraphicsOpacityEffect
    QLabel = qtwidgets.QLabel
    QWidget = qtwidgets.QWidget
    QPixmap = qtgui.QPixmap
    Qt = qtcore.Qt
    app = QApplication.instance() or QApplication([])

    tmp_path = (
        Path(__file__).resolve().parents[2]
        / "temp"
        / "test_runtime"
        / "portrait_scale"
        / uuid.uuid4().hex
    )
    tmp_path.mkdir(parents=True, exist_ok=True)
    portrait_path = tmp_path / "portrait.png"
    source = QPixmap(1000, 1000)
    source.fill(Qt.GlobalColor.white)
    assert source.save(str(portrait_path))

    profile = CharacterProfile(
        id="demo",
        display_name="Demo",
        package_dir=tmp_path,
        card_path=tmp_path / "card.md",
        initial_message="hello",
        default_portrait_path=portrait_path,
    )
    host = QWidget()
    main_label = QLabel(host)
    transition_label = QLabel(host)
    controller = PortraitController(
        profile=profile,
        parent_widget=host,
        main_label=main_label,
        transition_label=transition_label,
        main_opacity_effect=QGraphicsOpacityEffect(main_label),
        transition_opacity_effect=QGraphicsOpacityEffect(transition_label),
        stage_size=(860, 640),
        relayout=lambda: None,
        raise_foreground=lambda: None,
        on_portrait_changed=lambda _pixmap: None,
    )

    expected_sizes = {
        50: (280, 280),
        100: (560, 560),
        150: (840, 840),
    }
    for percent, expected_size in expected_sizes.items():
        controller.set_portrait_scale_percent(percent)
        controller.apply_current()
        scaled = main_label.pixmap()
        assert scaled is not None
        assert (scaled.width(), scaled.height()) == expected_size

    host.deleteLater()
    app.processEvents()


def test_portrait_controller_never_resizes_parent_window() -> None:
    """方案2 契约：PortraitController 只贴立绘 + relayout，绝不 resize 主窗口。

    主窗口几何统一由 PetWindow 以底边为锚点管理；若控制器再做左上锚点 resize，
    会与底边锚点几何相互打架，产生切表情/缩放时的偶发跳闪。此处把宿主尺寸设成与
    stage_size 不同的哨兵值，验证 apply_current 后宿主尺寸保持不变，且 relayout 仍被调用。
    """
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qtwidgets = pytest.importorskip("PySide6.QtWidgets")
    qtgui = pytest.importorskip("PySide6.QtGui")
    qtcore = pytest.importorskip("PySide6.QtCore")
    if not all(
        hasattr(qtwidgets, name)
        for name in ("QApplication", "QGraphicsOpacityEffect", "QLabel", "QWidget")
    ):
        pytest.skip("当前测试环境只提供了 PySide6 stub。")

    from app.config.character_loader import CharacterProfile
    from app.ui.portrait_controller import PortraitController

    QApplication = qtwidgets.QApplication
    QGraphicsOpacityEffect = qtwidgets.QGraphicsOpacityEffect
    QLabel = qtwidgets.QLabel
    QWidget = qtwidgets.QWidget
    QPixmap = qtgui.QPixmap
    Qt = qtcore.Qt
    app = QApplication.instance() or QApplication([])

    tmp_path = (
        Path(__file__).resolve().parents[2]
        / "temp"
        / "test_runtime"
        / "portrait_no_resize"
        / uuid.uuid4().hex
    )
    tmp_path.mkdir(parents=True, exist_ok=True)
    portrait_path = tmp_path / "portrait.png"
    source = QPixmap(1000, 1000)
    source.fill(Qt.GlobalColor.white)
    assert source.save(str(portrait_path))

    profile = CharacterProfile(
        id="demo",
        display_name="Demo",
        package_dir=tmp_path,
        card_path=tmp_path / "card.md",
        initial_message="hello",
        default_portrait_path=portrait_path,
    )
    host = QWidget()
    main_label = QLabel(host)
    transition_label = QLabel(host)
    relayout_calls = {"count": 0}

    def _relayout() -> None:
        relayout_calls["count"] += 1

    controller = PortraitController(
        profile=profile,
        parent_widget=host,
        main_label=main_label,
        transition_label=transition_label,
        main_opacity_effect=QGraphicsOpacityEffect(main_label),
        transition_opacity_effect=QGraphicsOpacityEffect(transition_label),
        # stage_size 故意区别于下面的哨兵尺寸，若控制器误 resize 会被立即发现。
        stage_size=(860, 640),
        relayout=_relayout,
        raise_foreground=lambda: None,
        on_portrait_changed=lambda _pixmap: None,
    )

    sentinel_size = qtcore.QSize(321, 234)
    host.resize(sentinel_size)
    assert host.size() == sentinel_size

    controller.apply_current()

    # 关键断言：宿主尺寸未被改成 stage_size，仍是哨兵尺寸；relayout 仍被调用。
    assert host.size() == sentinel_size
    assert relayout_calls["count"] >= 1

    host.deleteLater()
    app.processEvents()


def test_pet_window_loads_normalized_portrait_scale_percent() -> None:
    from app.ui.pet_window import PetWindow

    class MinimalWindow:
        _load_portrait_scale_percent = PetWindow._load_portrait_scale_percent

        def __init__(self, values):  # type: ignore[no-untyped-def]
            self.values = values

        def _load_system_config_values(self, section: str):  # type: ignore[no-untyped-def]
            assert section == "ui"
            return self.values

    assert MinimalWindow({})._load_portrait_scale_percent() == 100
    assert MinimalWindow({"portrait_scale_percent": "invalid"})._load_portrait_scale_percent() == 100
    assert MinimalWindow({"portrait_scale_percent": 20})._load_portrait_scale_percent() == 50
    assert MinimalWindow({"portrait_scale_percent": 180})._load_portrait_scale_percent() == 150


def test_control_panel_layout_normalization() -> None:
    from app.ui.control_panel_layout import (
        DEFAULT_BUBBLE_HEIGHT,
        DEFAULT_CONTROL_PANEL_VERTICAL_OFFSET,
        DEFAULT_CONTROL_PANEL_WIDTH,
        MAX_BUBBLE_HEIGHT,
        MAX_CONTROL_PANEL_VERTICAL_OFFSET,
        MAX_CONTROL_PANEL_WIDTH,
        MIN_BUBBLE_HEIGHT,
        MIN_CONTROL_PANEL_VERTICAL_OFFSET,
        MIN_CONTROL_PANEL_WIDTH,
        normalize_bubble_height,
        normalize_control_panel_vertical_offset,
        normalize_control_panel_width,
    )

    # 非法输入回退默认值
    assert normalize_control_panel_width("invalid") == DEFAULT_CONTROL_PANEL_WIDTH
    assert normalize_bubble_height(None) == DEFAULT_BUBBLE_HEIGHT
    assert normalize_control_panel_vertical_offset("x") == DEFAULT_CONTROL_PANEL_VERTICAL_OFFSET

    # 越界裁剪到上下限
    assert normalize_control_panel_width(1) == MIN_CONTROL_PANEL_WIDTH
    assert normalize_control_panel_width(9999) == MAX_CONTROL_PANEL_WIDTH
    assert normalize_bubble_height(1) == MIN_BUBBLE_HEIGHT
    assert normalize_bubble_height(9999) == MAX_BUBBLE_HEIGHT
    assert normalize_control_panel_vertical_offset(-9999) == MIN_CONTROL_PANEL_VERTICAL_OFFSET
    assert normalize_control_panel_vertical_offset(9999) == MAX_CONTROL_PANEL_VERTICAL_OFFSET

    # 合法值（含字符串/0/负值）原样保留
    assert normalize_control_panel_width(512) == 512
    assert normalize_control_panel_width("700") == 700
    assert normalize_bubble_height(180) == 180
    assert normalize_control_panel_vertical_offset(40) == 40
    assert normalize_control_panel_vertical_offset(-40) == -40
    assert normalize_control_panel_vertical_offset(0) == 0


def test_pet_window_defaults_subtitle_language_to_chinese() -> None:
    from app.ui.pet_window import PetWindow, SUBTITLE_LANGUAGE_JA, SUBTITLE_LANGUAGE_ZH

    class MinimalWindow:
        _load_subtitle_language = PetWindow._load_subtitle_language

        def __init__(self, values):  # type: ignore[no-untyped-def]
            self.values = values

        def _load_system_config_values(self, section: str):  # type: ignore[no-untyped-def]
            assert section == "ui"
            return self.values

    assert MinimalWindow({})._load_subtitle_language() == SUBTITLE_LANGUAGE_ZH
    assert MinimalWindow({"subtitle_language": "ja"})._load_subtitle_language() == SUBTITLE_LANGUAGE_JA
    assert MinimalWindow({"subtitle_language": "invalid"})._load_subtitle_language() == SUBTITLE_LANGUAGE_ZH


def test_pet_window_loads_normalized_subtitle_display_speed() -> None:
    from app.ui.pet_window import PetWindow

    class MinimalWindow:
        _load_subtitle_display_speed = PetWindow._load_subtitle_display_speed

        def __init__(self, values):  # type: ignore[no-untyped-def]
            self.values = values

        def _load_system_config_values(self, section: str):  # type: ignore[no-untyped-def]
            assert section == "ui"
            return self.values

    assert MinimalWindow({})._load_subtitle_display_speed() == (35, 100)
    assert MinimalWindow(
        {
            "subtitle_typing_interval_ms": "invalid",
            "reply_segment_pause_ms": "invalid",
        }
    )._load_subtitle_display_speed() == (35, 100)
    assert MinimalWindow(
        {
            "subtitle_typing_interval_ms": 1,
            "reply_segment_pause_ms": -1,
        }
    )._load_subtitle_display_speed() == (5, 0)
    assert MinimalWindow(
        {
            "subtitle_typing_interval_ms": 250,
            "reply_segment_pause_ms": 4000,
        }
    )._load_subtitle_display_speed() == (200, 3000)


def test_pet_window_loads_always_on_top_disabled_by_default() -> None:
    from app.ui.pet_window import PetWindow

    class MinimalWindow:
        _load_always_on_top_enabled = PetWindow._load_always_on_top_enabled

        def __init__(self, values):  # type: ignore[no-untyped-def]
            self.values = values

        def _load_system_config_values(self, section: str):  # type: ignore[no-untyped-def]
            assert section == "ui"
            return self.values

    assert MinimalWindow({})._load_always_on_top_enabled() is False
    assert MinimalWindow({"always_on_top_enabled": "invalid"})._load_always_on_top_enabled() is False
    assert MinimalWindow({"always_on_top_enabled": True})._load_always_on_top_enabled() is True
    assert MinimalWindow({"always_on_top_enabled": "on"})._load_always_on_top_enabled() is True


def test_pet_window_defaults_free_access_to_enabled() -> None:
    from app.ui.pet_window import PetWindow

    class MinimalWindow:
        _load_free_access_enabled = PetWindow._load_free_access_enabled

        def __init__(self, values):  # type: ignore[no-untyped-def]
            self.values = values

        def _load_system_config_values(self, section: str):  # type: ignore[no-untyped-def]
            assert section == "ui"
            return self.values

    assert MinimalWindow({})._load_free_access_enabled() is True
    assert MinimalWindow({"free_access_enabled": False})._load_free_access_enabled() is False
    assert MinimalWindow({"free_access_enabled": "off"})._load_free_access_enabled() is False
    assert MinimalWindow({"free_access_enabled": "invalid"})._load_free_access_enabled() is True


def test_pet_window_defaults_autonomous_screen_observation_to_enabled() -> None:
    from app.ui.pet_window import PetWindow

    class MinimalWindow:
        _load_autonomous_screen_observation_enabled = (
            PetWindow._load_autonomous_screen_observation_enabled
        )

        screen_observation_enabled = True

        def _load_system_config_values(self, section: str):  # type: ignore[no-untyped-def]
            assert section == "screen_observation"
            return {}

    assert MinimalWindow()._load_autonomous_screen_observation_enabled()


def test_pet_window_locks_controls_during_startup_initialization(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qtwidgets = pytest.importorskip("PySide6.QtWidgets")
    qtgui = pytest.importorskip("PySide6.QtGui")
    qtcore = pytest.importorskip("PySide6.QtCore")
    if not hasattr(qtwidgets, "QApplication"):
        pytest.skip("当前测试环境只提供了 PySide6 stub。")

    from app.agent.memory import MemoryStore
    from app.core.bootstrap import build_initial_app_context
    from app.ui.pet_window import PetWindow, STARTUP_INITIALIZING_TEXT

    QApplication = qtwidgets.QApplication
    app = QApplication.instance() or QApplication([])
    root = _build_runtime_root_with_character(qtgui.QPixmap, qtcore.Qt)
    monkeypatch.setattr(MemoryStore, "preload", lambda *a, **kw: None)
    context = build_initial_app_context(root)
    monkeypatch.setattr(PetWindow, "_maybe_start_memory_backfill", lambda _self: None)
    window = PetWindow(context)

    assert window.startup_initializing
    assert window.speech_label.text() == STARTUP_INITIALIZING_TEXT
    assert not window.input_edit.isEnabled()
    assert not window.send_button.isEnabled()
    assert not window.screenshot_button.isEnabled()
    assert window.screenshot_button.text() == ""
    assert window.screenshot_button.minimumWidth() == 38
    assert window.screenshot_button.maximumWidth() == 38
    assert not window.screenshot_button.icon().isNull()

    menu = window._build_menu()
    settings_action = next(action for action in menu.actions() if action.text() == "设置")
    quit_action = next(action for action in menu.actions() if action.text() == "退出")
    assert not settings_action.isEnabled()
    assert quit_action.isEnabled()

    menu.deleteLater()
    window.close()
    window.deleteLater()
    app.processEvents()


def test_pet_window_unlocks_after_deferred_services_are_applied(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qtwidgets = pytest.importorskip("PySide6.QtWidgets")
    qtgui = pytest.importorskip("PySide6.QtGui")
    qtcore = pytest.importorskip("PySide6.QtCore")
    if not hasattr(qtwidgets, "QApplication"):
        pytest.skip("当前测试环境只提供了 PySide6 stub。")

    from app.agent.memory import MemoryStore
    from app.core.bootstrap import DeferredStartupServices, build_initial_app_context
    from app.core.extensions import ExtensionRegistry
    from app.plugins.manager import PluginManager
    from app.ui.pet_window import PetWindow
    from app.voice.tts import NullTTSProvider

    class WarmableTTSProvider(NullTTSProvider):
        def __init__(self) -> None:
            self.warm_up_count = 0

        def warm_up_playback(self) -> None:
            self.warm_up_count += 1

    QApplication = qtwidgets.QApplication
    app = QApplication.instance() or QApplication([])
    root = _build_runtime_root_with_character(qtgui.QPixmap, qtcore.Qt)
    monkeypatch.setattr(MemoryStore, "preload", lambda *a, **kw: None)
    context = build_initial_app_context(root)
    monkeypatch.setattr(PetWindow, "_maybe_start_memory_backfill", lambda _self: None)
    window = PetWindow(context)
    tts_provider = WarmableTTSProvider()
    services = DeferredStartupServices(
        tts_provider=tts_provider,
        tool_registry=context.tool_registry,
        extension_registry=ExtensionRegistry(),
        plugin_manager=PluginManager(base_dir=root),
        mcp_settings=context.mcp_settings,
        mcp_tool_provider=None,
        errors=("TTS 配置无效，已禁用：参考音频不存在",),
    )

    window.apply_deferred_services(services)
    app.processEvents()

    assert not window.startup_initializing
    assert window.input_edit.isEnabled()
    assert window.send_button.isEnabled()
    assert window.screenshot_button.isEnabled()
    assert window.subtitle_controller.speech_text == window.character_profile.initial_message
    assert not window.tts_error_label.isHidden()
    assert "TTS 配置无效" in window.tts_error_label.text()
    assert tts_provider.warm_up_count == 1

    window.close()
    window.deleteLater()
    app.processEvents()


def test_shutdown_closes_late_deferred_services() -> None:
    from app.ui.pet_window import PetWindow

    class CloseableStub:
        def __init__(self) -> None:
            self.closed = 0

        def close(self) -> None:
            self.closed += 1

    class PluginManagerStub:
        def __init__(self) -> None:
            self.shutdowns = 0

        def shutdown_all(self) -> None:
            self.shutdowns += 1

    class ServicesStub:
        def __init__(self) -> None:
            self.tts_provider = CloseableStub()
            self.mcp_tool_provider = CloseableStub()
            self.plugin_manager = PluginManagerStub()

    class WindowStub:
        _shutdown_in_progress = True
        apply_deferred_services = PetWindow.apply_deferred_services
        _close_deferred_services = PetWindow._close_deferred_services

    services = ServicesStub()
    window = WindowStub()

    window.apply_deferred_services(services)

    assert services.tts_provider.closed == 1
    assert services.mcp_tool_provider.closed == 1
    assert services.plugin_manager.shutdowns == 1


def test_deferred_startup_worker_closes_services_when_cancelled_after_move(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    import main as main_module
    from main import DeferredStartupWorker

    class CloseableStub:
        def __init__(self) -> None:
            self.closed = 0

        def close(self) -> None:
            self.closed += 1

    class PluginManagerStub:
        def __init__(self) -> None:
            self.shutdowns = 0

        def shutdown_all(self) -> None:
            self.shutdowns += 1

    class ServicesStub:
        def __init__(self) -> None:
            self.tts_provider = CloseableStub()
            self.mcp_tool_provider = CloseableStub()
            self.plugin_manager = PluginManagerStub()

    services = ServicesStub()
    monkeypatch.setattr(
        main_module,
        "build_deferred_services",
        lambda *_args, **_kwargs: services,
    )

    worker = DeferredStartupWorker(Path("."), object())  # type: ignore[arg-type]
    monkeypatch.setattr(
        worker,
        "_move_service_objects_to_ui_thread",
        lambda _services: worker.cancel(),
    )
    cancelled: list[bool] = []
    finished: list[object] = []
    failed: list[str] = []
    worker.cancelled.connect(lambda: cancelled.append(True))
    worker.finished.connect(lambda value: finished.append(value))
    worker.failed.connect(lambda message: failed.append(message))

    worker.run()

    assert cancelled == [True]
    assert finished == []
    assert failed == []
    assert services.tts_provider.closed == 1
    assert services.mcp_tool_provider.closed == 1
    assert services.plugin_manager.shutdowns == 1


def test_settings_dialog_disables_proactive_intervals_when_screen_context_disabled() -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qtwidgets = pytest.importorskip("PySide6.QtWidgets")
    if not hasattr(qtwidgets, "QApplication"):
        pytest.skip("当前测试环境只提供了 PySide6 stub。")

    from app.ui.settings_dialog import SettingsDialog

    QApplication = qtwidgets.QApplication
    app = QApplication.instance() or QApplication([])
    dialog = SettingsDialog(
        api_settings=ApiSettings(
            base_url="https://api.example.com/v1",
            api_key="test-key",
            model="test-model",
        ),
        tts_settings=_minimal_tts_settings(),
        base_dir=Path("."),
        proactive_care_settings=ProactiveCareSettings(
            screen_context_enabled=False,
            check_interval_minutes=20,
            cooldown_minutes=10,
            screen_context_batch_limit=6,
        ),
    )

    assert dialog.proactive_screen_context_enabled_check.text() == "启用主动屏幕感知（会定期获取屏幕信息）"
    assert not dialog.proactive_check_interval_spin.isEnabled()
    assert not dialog.proactive_cooldown_spin.isEnabled()
    assert not dialog.proactive_batch_limit_spin.isEnabled()

    dialog.proactive_screen_context_enabled_check.setChecked(True)
    app.processEvents()
    assert dialog.proactive_check_interval_spin.isEnabled()
    assert dialog.proactive_cooldown_spin.isEnabled()
    assert dialog.proactive_batch_limit_spin.isEnabled()

    dialog.proactive_screen_context_enabled_check.setChecked(False)
    app.processEvents()
    assert not dialog.proactive_check_interval_spin.isEnabled()
    assert not dialog.proactive_cooldown_spin.isEnabled()
    assert not dialog.proactive_batch_limit_spin.isEnabled()

    dialog.deleteLater()
    app.processEvents()


def test_settings_dialog_disables_tts_settings_when_tts_disabled() -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qtwidgets = pytest.importorskip("PySide6.QtWidgets")
    if not hasattr(qtwidgets, "QApplication"):
        pytest.skip("当前测试环境只提供了 PySide6 stub。")

    from app.ui.settings_dialog import SettingsDialog

    QApplication = qtwidgets.QApplication
    app = QApplication.instance() or QApplication([])
    root = _ui_runtime_root("tts_disabled_controls")
    dialog = SettingsDialog(
        api_settings=ApiSettings(
            base_url="https://api.example.com/v1",
            api_key="test-key",
            model="test-model",
        ),
        tts_settings=_minimal_tts_settings(),
        base_dir=root,
        **_settings_dialog_character_kwargs(root),
        proactive_care_settings=ProactiveCareSettings(screen_context_enabled=True),
        mcp_settings=MCPRuntimeSettings(windows_enabled=False),
    )

    controlled_widgets = (
        dialog.tts_provider_combo,
        dialog.tts_api_url_edit,
        dialog.tts_work_dir_edit,
        dialog.tts_python_path_edit,
        dialog.tts_config_path_edit,
        dialog.ref_lang_edit,
        dialog.text_lang_edit,
        dialog.tts_timeout_spin,
    )
    assert all(not widget.isEnabled() for widget in controlled_widgets)
    assert dialog.tts_bundle_download_button.isEnabled()

    dialog.tts_enabled_check.setChecked(True)
    app.processEvents()
    assert dialog.tts_provider_combo.isEnabled()
    assert all(
        not widget.isEnabled()
        for widget in (
            dialog.tts_api_url_edit,
            dialog.tts_work_dir_edit,
            dialog.tts_python_path_edit,
            dialog.tts_config_path_edit,
        )
    )
    assert dialog.ref_lang_edit.isEnabled()
    assert dialog.text_lang_edit.isEnabled()
    assert dialog.tts_timeout_spin.isEnabled()
    assert dialog.tts_bundle_download_button.isEnabled()

    dialog.tts_enabled_check.setChecked(False)
    app.processEvents()
    assert all(not widget.isEnabled() for widget in controlled_widgets)
    assert dialog.tts_bundle_download_button.isEnabled()

    dialog.deleteLater()
    app.processEvents()


def test_settings_dialog_adds_plugin_settings_panel() -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qtwidgets = pytest.importorskip("PySide6.QtWidgets")
    if not all(hasattr(qtwidgets, name) for name in ("QApplication", "QLabel", "QListWidget")):
        pytest.skip("当前测试环境只提供了 PySide6 stub。")

    from app.plugins.models import SettingsPanelContribution
    from app.ui.settings_dialog import SettingsDialog

    QApplication = qtwidgets.QApplication
    QLabel = qtwidgets.QLabel
    QListWidget = qtwidgets.QListWidget
    app = QApplication.instance() or QApplication([])
    dialog = SettingsDialog(
        api_settings=ApiSettings(
            base_url="https://api.example.com/v1",
            api_key="test-key",
            model="test-model",
        ),
        tts_settings=_minimal_tts_settings(),
        base_dir=Path("."),
        settings_panel_contributions=[
            SettingsPanelContribution(
                section_id="demo",
                title="Demo 插件",
                build=lambda parent=None: QLabel("插件设置", parent),
            )
        ],
    )

    nav = dialog.findChild(QListWidget, "settingsNavList")
    assert nav is not None
    assert "插件" in [nav.item(index).text() for index in range(nav.count())]
    assert any(
        isinstance(label, QLabel) and label.text() == "插件设置"
        for label in dialog.findChildren(QLabel)
    )

    dialog.deleteLater()
    app.processEvents()


def test_settings_dialog_manages_plugin_enabled_state() -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qtcore = pytest.importorskip("PySide6.QtCore")
    qtwidgets = pytest.importorskip("PySide6.QtWidgets")
    if not all(hasattr(qtwidgets, name) for name in ("QApplication", "QTableWidget", "QCheckBox")):
        pytest.skip("当前测试环境只提供了 PySide6 stub。")

    from app.plugins.discovery import PluginDiscovery
    from app.ui.settings_dialog import SettingsDialog

    QApplication = qtwidgets.QApplication
    QTableWidget = qtwidgets.QTableWidget
    QCheckBox = qtwidgets.QCheckBox
    Qt = qtcore.Qt
    app = QApplication.instance() or QApplication([])
    root = _ui_runtime_root("plugin_manager_dialog")
    plugin_dir = root / "plugins" / "demo"
    plugin_dir.mkdir(parents=True, exist_ok=True)
    (plugin_dir / "plugin.yaml").write_text(
        """
api_version: 1
id: demo
name: Demo 插件
description: 用于测试插件管理页。
version: 1.0.0
entry: plugin:DemoPlugin
enabled: true
priority: 10
permissions:
  - settings_panel
""".strip(),
        encoding="utf-8",
    )

    dialog = SettingsDialog(
        api_settings=ApiSettings(
            base_url="https://api.example.com/v1",
            api_key="test-key",
            model="test-model",
        ),
        tts_settings=_minimal_tts_settings(),
        base_dir=root,
        **_settings_dialog_character_kwargs(root),
        proactive_care_settings=ProactiveCareSettings(screen_context_enabled=True),
        mcp_settings=MCPRuntimeSettings(windows_enabled=False),
    )

    table = dialog.findChild(QTableWidget, "pluginManagerTable")
    assert table is not None
    assert table.rowCount() == 1
    assert table.item(0, 1).text() == "Demo 插件"
    assert table.item(0, 5).text() == "用于测试插件管理页。"

    checkbox = table.cellWidget(0, 0).findChild(QCheckBox)
    assert checkbox is not None
    checkbox.setCheckState(Qt.CheckState.Unchecked)
    dialog.accept()

    specs = PluginDiscovery(root).discover()
    assert specs[0].plugin_id == "demo"
    assert specs[0].enabled is False
    assert dialog.result_plugin_config_changed is True
    dialog.deleteLater()
    app.processEvents()


def test_settings_dialog_uses_grouped_top_level_tabs() -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qtwidgets = pytest.importorskip("PySide6.QtWidgets")
    if not all(
        hasattr(qtwidgets, name)
        for name in ("QApplication", "QComboBox", "QListWidget", "QStackedWidget")
    ):
        pytest.skip("当前测试环境只提供了 PySide6 stub。")

    from app.ui.settings_dialog import SettingsDialog

    QApplication = qtwidgets.QApplication
    QComboBox = qtwidgets.QComboBox
    QListWidget = qtwidgets.QListWidget
    QStackedWidget = qtwidgets.QStackedWidget
    app = QApplication.instance() or QApplication([])
    app_stylesheet_before = app.styleSheet()
    root = _ui_runtime_root("grouped_settings_tabs")
    dialog = SettingsDialog(
        api_settings=ApiSettings(
            base_url="https://api.example.com/v1",
            api_key="test-key",
            model="test-model",
        ),
        tts_settings=_minimal_tts_settings(),
        base_dir=root,
        **_settings_dialog_character_kwargs(root),
        proactive_care_settings=ProactiveCareSettings(screen_context_enabled=True),
        mcp_settings=MCPRuntimeSettings(windows_enabled=False),
    )

    nav = dialog.findChild(QListWidget, "settingsNavList")
    stack = dialog.findChild(QStackedWidget, "settingsNavStack")
    assert nav is not None
    assert stack is not None
    assert [nav.item(index).text() for index in range(nav.count())] == [
        "角色",
        "外观",
        "模型",
        "语音",
        "隐私",
        "工具",
        "插件",
        "系统",
    ]
    assert stack.count() == nav.count()
    assert dialog.minimumWidth() >= 680
    assert dialog.minimumHeight() >= 500
    assert dialog.width() >= 820
    assert dialog.height() >= 640
    assert "QWidget#settingsScrollViewport" in dialog.styleSheet()
    assert "QWidget#settingsPluginTab" in dialog.styleSheet()
    assert "combobox-popup: 0;" in dialog.styleSheet()
    assert "QComboBox::drop-down" in dialog.styleSheet()
    assert "QComboBox::down-arrow" in dialog.styleSheet()
    assert "QComboBox QAbstractItemView" in dialog.styleSheet()
    assert "QComboBox QAbstractItemView::item:selected" in dialog.styleSheet()
    assert "QComboBox QAbstractItemView::item:selected:!active" in dialog.styleSheet()
    assert "QComboBoxPrivateContainer" not in dialog.styleSheet()
    assert "settingsComboPopup" not in dialog.styleSheet()
    assert "QSpinBox::up-button" in dialog.styleSheet()
    assert "QSpinBox::down-button" in dialog.styleSheet()
    assert "QLineEdit:disabled" in dialog.styleSheet()
    assert 'QLineEdit[readOnly="true"]' in dialog.styleSheet()
    assert "QComboBox:disabled" in dialog.styleSheet()
    assert "QSpinBox::up-button:disabled" in dialog.styleSheet()
    assert "QGroupBox QWidget" not in dialog.styleSheet()
    assert isinstance(dialog.character_combo, QComboBox)
    assert isinstance(dialog.model_edit, QComboBox)
    assert isinstance(dialog.tts_provider_combo, QComboBox)
    assert isinstance(dialog.theme_visual_effect_combo, QComboBox)
    assert not hasattr(dialog.character_combo, "_popup_frame")
    assert not hasattr(dialog.model_edit, "_popup_frame")
    assert app.styleSheet() == app_stylesheet_before

    combo_bottom = dialog.character_combo.mapToGlobal(dialog.character_combo.rect().bottomLeft()).y()
    dialog.character_combo.showPopup()
    app.processEvents()
    assert dialog.character_combo.view().window().geometry().top() >= combo_bottom
    dialog.character_combo.hidePopup()

    dialog.deleteLater()
    app.processEvents()


def test_settings_dialog_defers_memory_load_until_memory_tab_selected(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qtwidgets = pytest.importorskip("PySide6.QtWidgets")
    if not all(hasattr(qtwidgets, name) for name in ("QApplication", "QListWidget")):
        pytest.skip("当前测试环境只提供了 PySide6 stub。")

    from app.ui.settings_dialog import SettingsDialog

    QApplication = qtwidgets.QApplication
    QListWidget = qtwidgets.QListWidget
    app = QApplication.instance() or QApplication([])
    root = _ui_runtime_root("settings_memory_lazy_load")
    load_calls: list[str] = []
    monkeypatch.setattr(
        SettingsDialog,
        "_load_memory_entries",
        lambda self: load_calls.append("load"),
    )

    dialog = SettingsDialog(
        api_settings=ApiSettings(
            base_url="https://api.example.com/v1",
            api_key="test-key",
            model="test-model",
        ),
        tts_settings=_minimal_tts_settings(),
        base_dir=root,
        memory_store=object(),  # type: ignore[arg-type]
        **_settings_dialog_character_kwargs(root),
        proactive_care_settings=ProactiveCareSettings(screen_context_enabled=True),
        mcp_settings=MCPRuntimeSettings(windows_enabled=False),
    )
    app.processEvents()

    nav = dialog.findChild(QListWidget, "settingsNavList")
    assert nav is not None
    titles = [nav.item(index).text() for index in range(nav.count())]
    memory_row = titles.index("记忆")
    assert load_calls == []

    nav.setCurrentRow(memory_row)
    app.processEvents()
    assert load_calls == ["load"]

    nav.setCurrentRow(0)
    nav.setCurrentRow(memory_row)
    app.processEvents()
    assert load_calls == ["load"]

    dialog.deleteLater()
    app.processEvents()


def _open_settings_memory_tab(dialog, qtwidgets, app=None) -> None:  # type: ignore[no-untyped-def]
    QListWidget = getattr(qtwidgets, "QListWidget", None)
    if QListWidget is None:
        pytest.skip("当前测试环境只提供了 PySide6 stub。")
    nav = dialog.findChild(QListWidget, "settingsNavList")
    assert nav is not None
    memory_row = [nav.item(index).text() for index in range(nav.count())].index("记忆")
    nav.setCurrentRow(memory_row)
    if app is not None:
        app.processEvents()


def test_settings_dialog_groupbox_title_indicator_has_vertical_room() -> None:
    from app.ui.theme import DEFAULT_THEME_SETTINGS, build_settings_dialog_stylesheet

    stylesheet = build_settings_dialog_stylesheet(DEFAULT_THEME_SETTINGS)

    assert "QGroupBox#advancedParamsGroup {" in stylesheet
    assert "QGroupBox#advancedParamsGroup::title" in stylesheet
    assert "QGroupBox#advancedParamsGroup::indicator" in stylesheet
    assert "margin-bottom: 2px;" in stylesheet


def test_settings_dialog_insets_advanced_params_group() -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qtwidgets = pytest.importorskip("PySide6.QtWidgets")
    if not all(hasattr(qtwidgets, name) for name in ("QApplication", "QGroupBox")):
        pytest.skip("当前测试环境只提供了 PySide6 stub。")

    from app.ui.settings_dialog import SettingsDialog

    QApplication = qtwidgets.QApplication
    QGroupBox = qtwidgets.QGroupBox
    app = QApplication.instance() or QApplication([])
    root = _ui_runtime_root("advanced_params_group_insets")
    dialog = SettingsDialog(
        api_settings=ApiSettings(
            base_url="https://api.example.com/v1",
            api_key="test-key",
            model="test-model",
        ),
        tts_settings=_minimal_tts_settings(),
        base_dir=root,
        **_settings_dialog_character_kwargs(root),
        proactive_care_settings=ProactiveCareSettings(screen_context_enabled=True),
        mcp_settings=MCPRuntimeSettings(windows_enabled=False),
    )

    group = dialog.findChild(QGroupBox, "advancedParamsGroup")
    assert group is not None
    layout = group.parentWidget().layout()
    margins = layout.contentsMargins()
    assert (margins.left(), margins.top(), margins.right(), margins.bottom()) == (
        16,
        18,
        16,
        16,
    )

    dialog.deleteLater()
    app.processEvents()


def test_pet_window_syncs_plugin_chat_ui_widgets() -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qtwidgets = pytest.importorskip("PySide6.QtWidgets")
    if not all(hasattr(qtwidgets, name) for name in ("QApplication", "QFrame", "QHBoxLayout", "QLineEdit", "QPushButton")):
        pytest.skip("当前测试环境只提供了 PySide6 stub。")

    from app.plugins.models import ChatUIWidgetContribution
    from app.ui.pet_window import PetWindow

    QApplication = qtwidgets.QApplication
    QFrame = qtwidgets.QFrame
    QHBoxLayout = qtwidgets.QHBoxLayout
    QLineEdit = qtwidgets.QLineEdit
    QPushButton = qtwidgets.QPushButton
    app = QApplication.instance() or QApplication([])

    def build_button(parent=None):  # type: ignore[no-untyped-def]
        button = QPushButton("插件", parent)
        button.setObjectName("demoPluginButton")
        return button

    class PluginManagerStub:
        chat_ui_widgets = [
            ChatUIWidgetContribution(
                widget_id="demo_widget",
                build=build_button,
                order=10,
            )
        ]

    host = QFrame()
    host.input_bar = QFrame(host)
    host.input_edit = QLineEdit(host.input_bar)
    host.plugin_manager = PluginManagerStub()
    host.plugin_chat_ui_widget_instances = []

    layout = QHBoxLayout()
    layout.addWidget(host.input_edit)
    layout.addWidget(QPushButton("截图", host.input_bar))
    layout.addWidget(QPushButton("发送", host.input_bar))
    host.input_bar.setLayout(layout)

    PetWindow._sync_plugin_chat_ui_widgets(host)  # type: ignore[arg-type]

    assert layout.itemAt(1).widget().objectName() == "demoPluginButton"

    host.deleteLater()
    app.processEvents()


def test_settings_dialog_exposes_experimental_windows_mcp_restart_setting() -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qtwidgets = pytest.importorskip("PySide6.QtWidgets")
    if not hasattr(qtwidgets, "QApplication"):
        pytest.skip("当前测试环境只提供了 PySide6 stub。")

    from app.ui.settings_dialog import SettingsDialog

    QApplication = qtwidgets.QApplication
    app = QApplication.instance() or QApplication([])
    root = _ui_runtime_root("mcp_restart_dialog")
    dialog = SettingsDialog(
        api_settings=ApiSettings(
            base_url="https://api.example.com/v1",
            api_key="test-key",
            model="test-model",
        ),
        tts_settings=_minimal_tts_settings(),
        base_dir=root,
        **_settings_dialog_character_kwargs(root),
        proactive_care_settings=ProactiveCareSettings(screen_context_enabled=True),
        mcp_settings=MCPRuntimeSettings(windows_enabled=False),
    )

    labels = [label.text() for label in dialog.findChildren(qtwidgets.QLabel)]

    assert not dialog.windows_mcp_enabled_check.isChecked()
    assert dialog.windows_mcp_enabled_check.isEnabled()
    assert "实验性" in dialog.windows_mcp_enabled_check.text()
    assert "实验性功能" in dialog.windows_mcp_enabled_check.toolTip()
    assert any("实验性功能" in text for text in labels)
    assert any("重启 Sakura" in text for text in labels)

    dialog.windows_mcp_enabled_check.setChecked(True)
    dialog.accept()

    assert dialog.result_mcp_settings == MCPRuntimeSettings(windows_enabled=True)
    dialog.deleteLater()
    app.processEvents()


def test_settings_dialog_exposes_tts_bundle_controls(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qtwidgets = pytest.importorskip("PySide6.QtWidgets")
    if not hasattr(qtwidgets, "QApplication"):
        pytest.skip("当前测试环境只提供了 PySide6 stub。")

    import app.ui.settings_dialog as settings_dialog_module
    from app.ui.settings_dialog import SettingsDialog

    QApplication = qtwidgets.QApplication
    app = QApplication.instance() or QApplication([])
    root = _ui_runtime_root("tts_bundle_controls")
    bundle_work_dir = root / "tts" / "gpt"
    monkeypatch.setattr(
        settings_dialog_module,
        "default_provider_bundle_work_dir",
        lambda _provider, _base_dir: bundle_work_dir,
    )
    dialog = SettingsDialog(
        api_settings=ApiSettings(
            base_url="https://api.example.com/v1",
            api_key="test-key",
            model="test-model",
        ),
        tts_settings=_minimal_tts_settings(),
        base_dir=root,
        **_settings_dialog_character_kwargs(root),
        proactive_care_settings=ProactiveCareSettings(screen_context_enabled=True),
        mcp_settings=MCPRuntimeSettings(windows_enabled=False),
    )

    labels = [label.text() for label in dialog.findChildren(qtwidgets.QLabel)]
    custom_index = dialog.tts_provider_combo.findData("custom-gpt-sovits")

    assert any("TTS 工作目录" in text for text in labels)
    assert any("TTS 提供器" in text for text in labels)
    assert any("TTS Python" in text for text in labels)
    assert any("推理配置" in text for text in labels)
    assert custom_index >= 0
    assert "macOS/Linux" in dialog.tts_provider_combo.itemText(custom_index)
    assert dialog.tts_bundle_download_button.text() == "一键下载 TTS 整合包"
    assert dialog.tts_bundle_download_button.parentWidget() is dialog.tts_enabled_check.parentWidget()
    assert dialog.tts_voice_import_button.parentWidget() is dialog.character_import_button.parentWidget()
    assert not dialog.tts_provider_combo.isEnabled()
    assert dialog.tts_bundle_download_button.isEnabled()
    dialog.tts_enabled_check.setChecked(True)
    app.processEvents()
    assert dialog.tts_provider_combo.isEnabled()
    bundled_edits = (
        dialog.tts_api_url_edit,
        dialog.tts_work_dir_edit,
        dialog.tts_python_path_edit,
        dialog.tts_config_path_edit,
    )
    assert all(edit.isReadOnly() for edit in bundled_edits)
    assert all(not edit.isEnabled() for edit in bundled_edits)
    assert all(edit.text().strip() for edit in bundled_edits)
    assert dialog.tts_api_url_edit.text() == "http://127.0.0.1:9880/tts"
    assert dialog.tts_work_dir_edit.text().endswith(("tts\\gpt", "tts/gpt", "tts\\g50", "tts/g50"))
    assert dialog.tts_python_path_edit.text().endswith(("runtime\\python.exe", "runtime/python.exe"))
    assert dialog.tts_config_path_edit.text().endswith((
        "GPT_SoVITS\\configs\\tts_infer.yaml",
        "GPT_SoVITS/configs/tts_infer.yaml",
    ))
    for edit in bundled_edits:
        label = dialog._tts_form_layout.labelForField(edit)
        assert label is not None
        assert label.isEnabled()
    settings = dialog._validated_tts_settings()
    assert settings is not None
    assert settings.python_path is None
    assert settings.tts_config_path is None
    dialog.tts_provider_combo.setCurrentIndex(custom_index)
    app.processEvents()
    assert all(edit.isEnabled() for edit in bundled_edits)
    assert not dialog.tts_python_path_edit.isReadOnly()
    assert not dialog.tts_config_path_edit.isReadOnly()
    dialog.deleteLater()
    app.processEvents()


def test_settings_dialog_download_success_fills_tts_work_dir(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qtwidgets = pytest.importorskip("PySide6.QtWidgets")
    if not hasattr(qtwidgets, "QApplication"):
        pytest.skip("当前测试环境只提供了 PySide6 stub。")

    import app.ui.settings_dialog as settings_dialog_module
    from app.ui.settings_dialog import SettingsDialog

    root = _ui_runtime_root("tts_bundle_ui")
    root.mkdir(parents=True, exist_ok=True)

    class DialogStub:
        def __init__(self, *_args, **_kwargs) -> None:
            self.downloaded_work_dir = root / "tts" / "gpt"

        def exec(self):  # type: ignore[no-untyped-def]
            return settings_dialog_module.QDialog.DialogCode.Accepted

    monkeypatch.setattr(settings_dialog_module, "TTSBundleDownloadDialog", DialogStub)

    QApplication = qtwidgets.QApplication
    app = QApplication.instance() or QApplication([])
    dialog = SettingsDialog(
        api_settings=ApiSettings(
            base_url="https://api.example.com/v1",
            api_key="test-key",
            model="test-model",
        ),
        tts_settings=_minimal_tts_settings(),
        base_dir=root,
        **_settings_dialog_character_kwargs(root),
        proactive_care_settings=ProactiveCareSettings(screen_context_enabled=True),
        mcp_settings=MCPRuntimeSettings(windows_enabled=False),
    )

    dialog._download_gpt_sovits_bundle()

    assert dialog.tts_enabled_check.isChecked()
    assert dialog.tts_api_url_edit.text() == "http://127.0.0.1:9880/tts"
    assert dialog.tts_work_dir_edit.text().endswith(("tts\\gpt", "tts/gpt"))
    monkeypatch.setattr(
        dialog,
        "_start_tts_settings_test",
        lambda _settings, accept_values: dialog._complete_accept(accept_values),
    )

    dialog.accept()

    assert dialog.result_tts_settings is not None
    assert dialog.result_tts_settings.work_dir == root / "tts" / "gpt"
    dialog.deleteLater()
    app.processEvents()


def test_settings_dialog_download_success_fills_genie_provider(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qtwidgets = pytest.importorskip("PySide6.QtWidgets")
    if not hasattr(qtwidgets, "QApplication"):
        pytest.skip("当前测试环境只提供了 PySide6 stub。")

    import app.ui.settings_dialog as settings_dialog_module
    from app.ui.settings_dialog import SettingsDialog

    root = _ui_runtime_root("tts_bundle_ui")
    root.mkdir(parents=True, exist_ok=True)

    class DialogStub:
        def __init__(self, *_args, **_kwargs) -> None:
            self.downloaded_work_dir = root / "tts" / "cpu"
            self.downloaded_provider = "genie-tts"

        def exec(self):  # type: ignore[no-untyped-def]
            return settings_dialog_module.QDialog.DialogCode.Accepted

    monkeypatch.setattr(settings_dialog_module, "TTSBundleDownloadDialog", DialogStub)

    QApplication = qtwidgets.QApplication
    app = QApplication.instance() or QApplication([])
    dialog = SettingsDialog(
        api_settings=ApiSettings(
            base_url="https://api.example.com/v1",
            api_key="test-key",
            model="test-model",
        ),
        tts_settings=_minimal_tts_settings(),
        base_dir=root,
        **_settings_dialog_character_kwargs(root),
        proactive_care_settings=ProactiveCareSettings(screen_context_enabled=True),
        mcp_settings=MCPRuntimeSettings(windows_enabled=False),
    )

    dialog._download_gpt_sovits_bundle()

    assert dialog.tts_enabled_check.isChecked()
    assert dialog.tts_provider_combo.currentData() == "genie-tts"
    assert dialog.tts_api_url_edit.text() == "http://127.0.0.1:9881/"
    assert dialog.tts_work_dir_edit.text().endswith(("tts\\cpu", "tts/cpu"))
    monkeypatch.setattr(
        dialog,
        "_start_tts_settings_test",
        lambda _settings, accept_values: dialog._complete_accept(accept_values),
    )

    dialog.accept()

    assert dialog.result_tts_settings is not None
    assert dialog.result_tts_settings.provider == "genie-tts"
    assert dialog.result_tts_settings.work_dir == root / "tts" / "cpu"
    assert dialog.result_tts_settings.onnx_model_dir == root / "data" / "tts_bundles" / "onnx" / "sakura"
    dialog.deleteLater()
    app.processEvents()


def test_settings_dialog_download_success_fills_macos_gptsovits_paths(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qtwidgets = pytest.importorskip("PySide6.QtWidgets")
    if not hasattr(qtwidgets, "QApplication"):
        pytest.skip("当前测试环境只提供了 PySide6 stub。")

    import app.ui.settings_dialog as settings_dialog_module
    from app.ui.settings_dialog import SettingsDialog

    root = _ui_runtime_root("tts_bundle_macos_ui")
    work_dir = root / "data" / "tts_bundles" / "installed" / "gpt_sovits_macos" / "GPT-SoVITS"
    python_path = (
        root
        / "data"
        / "tts_bundles"
        / "installed"
        / "gpt_sovits_macos"
        / "miniforge3"
        / "envs"
        / "gpt-sovits310"
        / "bin"
        / "python"
    )
    tts_config_path = work_dir / "GPT_SoVITS" / "configs" / "tts_infer_sakura_macos.yaml"
    work_dir.mkdir(parents=True, exist_ok=True)
    python_path.parent.mkdir(parents=True, exist_ok=True)
    python_path.write_text("fake", encoding="utf-8")
    tts_config_path.parent.mkdir(parents=True, exist_ok=True)
    tts_config_path.write_text("custom: {}", encoding="utf-8")

    class DialogStub:
        def __init__(self, *_args, **_kwargs) -> None:
            self.downloaded_work_dir = work_dir
            self.downloaded_provider = "custom-gpt-sovits"
            self.downloaded_python_path = python_path
            self.downloaded_tts_config_path = tts_config_path

        def exec(self):  # type: ignore[no-untyped-def]
            return settings_dialog_module.QDialog.DialogCode.Accepted

    monkeypatch.setattr(settings_dialog_module, "TTSBundleDownloadDialog", DialogStub)

    QApplication = qtwidgets.QApplication
    app = QApplication.instance() or QApplication([])
    dialog = SettingsDialog(
        api_settings=ApiSettings(
            base_url="https://api.example.com/v1",
            api_key="test-key",
            model="test-model",
        ),
        tts_settings=_minimal_tts_settings(),
        base_dir=root,
        **_settings_dialog_character_kwargs(root),
        proactive_care_settings=ProactiveCareSettings(screen_context_enabled=True),
        mcp_settings=MCPRuntimeSettings(windows_enabled=False),
    )

    dialog._download_gpt_sovits_bundle()

    assert dialog.tts_enabled_check.isChecked()
    assert dialog.tts_provider_combo.currentData() == "custom-gpt-sovits"
    assert dialog.tts_work_dir_edit.text() == str(work_dir)
    assert dialog.tts_python_path_edit.text() == str(python_path)
    assert dialog.tts_config_path_edit.text() == str(tts_config_path)
    monkeypatch.setattr(
        dialog,
        "_start_tts_settings_test",
        lambda _settings, accept_values: dialog._complete_accept(accept_values),
    )

    dialog.accept()

    assert dialog.result_tts_settings is not None
    assert dialog.result_tts_settings.provider == "custom-gpt-sovits"
    assert dialog.result_tts_settings.work_dir == work_dir
    assert dialog.result_tts_settings.python_path == python_path
    assert dialog.result_tts_settings.tts_config_path == tts_config_path
    dialog.deleteLater()
    app.processEvents()


def test_settings_dialog_skips_tts_test_when_tts_disabled(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qtwidgets = pytest.importorskip("PySide6.QtWidgets")
    if not hasattr(qtwidgets, "QApplication"):
        pytest.skip("当前测试环境只提供了 PySide6 stub。")

    from app.ui.settings_dialog import SettingsDialog

    QApplication = qtwidgets.QApplication
    app = QApplication.instance() or QApplication([])
    root = _ui_runtime_root("tts_save_disabled")
    dialog = SettingsDialog(
        api_settings=ApiSettings(
            base_url="https://api.example.com/v1",
            api_key="test-key",
            model="test-model",
        ),
        tts_settings=_minimal_tts_settings(),
        base_dir=root,
        **_settings_dialog_character_kwargs(root),
        proactive_care_settings=ProactiveCareSettings(screen_context_enabled=True),
        mcp_settings=MCPRuntimeSettings(windows_enabled=False),
    )
    monkeypatch.setattr(
        dialog,
        "_start_tts_settings_test",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("TTS 关闭时不应检测")),
    )

    dialog.accept()

    assert dialog.result_tts_settings is not None
    assert not dialog.result_tts_settings.enabled
    dialog.deleteLater()
    app.processEvents()


def test_settings_dialog_enabled_tts_skips_test_when_settings_unchanged(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qtwidgets = pytest.importorskip("PySide6.QtWidgets")
    if not hasattr(qtwidgets, "QApplication"):
        pytest.skip("当前测试环境只提供了 PySide6 stub。")

    import app.ui.settings_dialog as settings_dialog_module
    from app.ui.settings_dialog import SettingsDialog

    QApplication = qtwidgets.QApplication
    app = QApplication.instance() or QApplication([])
    root = _ui_runtime_root("tts_save_success")
    character_kwargs = _settings_dialog_character_kwargs(root)
    profile = character_kwargs["current_character"]
    assert hasattr(profile, "voice")
    assert profile.voice is not None
    tts_settings = GPTSoVITSTTSSettings.from_character_profile(
        character_profile=profile,
        enabled=True,
        api_url="http://127.0.0.1:9880/tts",
        ref_lang=profile.voice.ref_lang,
        text_lang=profile.voice.text_lang,
        timeout_seconds=1,
        work_dir=root / "tts" / "g50",
    )
    monkeypatch.setattr(
        settings_dialog_module,
        "default_provider_bundle_work_dir",
        lambda _provider, _base_dir: root / "data" / "tts_bundles" / "installed" / "gpt_sovits_v2pro",
    )
    dialog = SettingsDialog(
        api_settings=ApiSettings(
            base_url="https://api.example.com/v1",
            api_key="test-key",
            model="test-model",
        ),
        tts_settings=tts_settings,
        base_dir=root,
        **character_kwargs,
        proactive_care_settings=ProactiveCareSettings(screen_context_enabled=True),
        mcp_settings=MCPRuntimeSettings(windows_enabled=False),
    )
    monkeypatch.setattr(
        dialog,
        "_start_tts_settings_test",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("TTS 配置未变时不应检测")),
    )

    dialog.accept()

    assert dialog.result_tts_settings is not None
    assert dialog.result_tts_settings.enabled
    dialog.deleteLater()
    app.processEvents()


def test_settings_dialog_enabled_tts_tests_when_provider_changes(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qtwidgets = pytest.importorskip("PySide6.QtWidgets")
    if not hasattr(qtwidgets, "QApplication"):
        pytest.skip("当前测试环境只提供了 PySide6 stub。")

    from app.ui.settings_dialog import SettingsDialog

    QApplication = qtwidgets.QApplication
    app = QApplication.instance() or QApplication([])
    root = _ui_runtime_root("tts_save_provider_changed")
    dialog = SettingsDialog(
        api_settings=ApiSettings(
            base_url="https://api.example.com/v1",
            api_key="test-key",
            model="test-model",
        ),
        tts_settings=replace(_minimal_tts_settings(), enabled=True),
        base_dir=root,
        **_settings_dialog_character_kwargs(root),
        proactive_care_settings=ProactiveCareSettings(screen_context_enabled=True),
        mcp_settings=MCPRuntimeSettings(windows_enabled=False),
    )
    genie_index = dialog.tts_provider_combo.findData("genie-tts")
    assert genie_index >= 0
    dialog.tts_provider_combo.setCurrentIndex(genie_index)
    calls: list[str] = []

    def fake_start_tts_test(settings, accept_values):  # type: ignore[no-untyped-def]
        calls.append(settings.provider)
        dialog._complete_accept(accept_values)

    monkeypatch.setattr(dialog, "_start_tts_settings_test", fake_start_tts_test)

    dialog.accept()

    assert calls == ["genie-tts"]
    assert dialog.result_tts_settings is not None
    assert dialog.result_tts_settings.provider == "genie-tts"
    dialog.deleteLater()
    app.processEvents()


def test_settings_dialog_enabled_tts_tests_when_character_changes(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qtwidgets = pytest.importorskip("PySide6.QtWidgets")
    if not hasattr(qtwidgets, "QApplication"):
        pytest.skip("当前测试环境只提供了 PySide6 stub。")

    from app.config.character_loader import CharacterRegistry
    from app.ui.settings_dialog import SettingsDialog

    QApplication = qtwidgets.QApplication
    app = QApplication.instance() or QApplication([])
    root = _ui_runtime_root("tts_save_character_changed")
    current_profile = _build_settings_dialog_character(root, "sakura", "Sakura")
    _build_settings_dialog_character(root, "nanami", "Nanami")
    dialog = SettingsDialog(
        api_settings=ApiSettings(
            base_url="https://api.example.com/v1",
            api_key="test-key",
            model="test-model",
        ),
        tts_settings=_minimal_tts_settings(),
        base_dir=root,
        character_registry=CharacterRegistry(root),
        current_character=current_profile,
        proactive_care_settings=ProactiveCareSettings(screen_context_enabled=True),
        mcp_settings=MCPRuntimeSettings(windows_enabled=False),
    )
    dialog.tts_enabled_check.setChecked(True)
    nanami_index = dialog.character_combo.findData("nanami")
    assert nanami_index >= 0
    dialog.character_combo.setCurrentIndex(nanami_index)
    calls: list[str] = []

    def fake_start_tts_test(settings, accept_values):  # type: ignore[no-untyped-def]
        calls.append(settings.character_name)
        dialog._complete_accept(accept_values)

    monkeypatch.setattr(dialog, "_start_tts_settings_test", fake_start_tts_test)

    dialog.accept()

    assert calls == ["Nanami"]
    assert dialog.result_character_id == "nanami"
    assert dialog.result_tts_settings is not None
    assert dialog.result_tts_settings.enabled
    dialog.deleteLater()
    app.processEvents()


def test_settings_dialog_disables_tts_when_selected_character_has_no_voice(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qtwidgets = pytest.importorskip("PySide6.QtWidgets")
    if not hasattr(qtwidgets, "QApplication"):
        pytest.skip("当前测试环境只提供了 PySide6 stub。")

    import app.ui.settings_dialog as settings_dialog_module
    from app.config.character_loader import CharacterRegistry
    from app.ui.settings_dialog import SettingsDialog

    QApplication = qtwidgets.QApplication
    app = QApplication.instance() or QApplication([])
    root = _ui_runtime_root("tts_save_no_voice")
    current_profile = _build_settings_dialog_character(root, "sakura", "Sakura", with_voice=False)
    dialog = SettingsDialog(
        api_settings=ApiSettings(
            base_url="https://api.example.com/v1",
            api_key="test-key",
            model="test-model",
        ),
        tts_settings=_minimal_tts_settings(),
        base_dir=root,
        character_registry=CharacterRegistry(root),
        current_character=current_profile,
        proactive_care_settings=ProactiveCareSettings(screen_context_enabled=True),
        mcp_settings=MCPRuntimeSettings(windows_enabled=False),
    )
    dialog.tts_enabled_check.setChecked(True)
    warnings: list[str] = []
    monkeypatch.setattr(
        settings_dialog_module.QMessageBox,
        "warning",
        lambda _parent, _title, message: warnings.append(message),
    )
    monkeypatch.setattr(
        dialog,
        "_start_tts_settings_test",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("无语音角色不应进入 TTS 服务检测")),
    )

    dialog.accept()

    assert warnings and "当前角色没有语音包" in warnings[0]
    assert "参考文本" not in warnings[0]
    assert not dialog.tts_enabled_check.isChecked()
    assert dialog.result_tts_settings is not None
    assert not dialog.result_tts_settings.enabled
    dialog.deleteLater()
    app.processEvents()


def test_settings_dialog_tts_test_failure_keeps_enabled_settings(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qtwidgets = pytest.importorskip("PySide6.QtWidgets")
    if not hasattr(qtwidgets, "QApplication"):
        pytest.skip("当前测试环境只提供了 PySide6 stub。")

    import app.ui.settings_dialog as settings_dialog_module
    from app.config.character_loader import CharacterRegistry
    from app.ui.settings_dialog import SettingsDialog

    QApplication = qtwidgets.QApplication
    app = QApplication.instance() or QApplication([])
    root = _ui_runtime_root("tts_save_failure")
    current_profile = _build_settings_dialog_character(root, "sakura", "Sakura")
    _build_settings_dialog_character(root, "nanami", "Nanami")
    work_dir = root / "tts" / "g50"
    work_dir.mkdir(parents=True, exist_ok=True)
    dialog = SettingsDialog(
        api_settings=ApiSettings(
            base_url="https://api.example.com/v1",
            api_key="test-key",
            model="test-model",
        ),
        tts_settings=_minimal_tts_settings(),
        base_dir=root,
        character_registry=CharacterRegistry(root),
        current_character=current_profile,
        proactive_care_settings=ProactiveCareSettings(screen_context_enabled=True),
        mcp_settings=MCPRuntimeSettings(windows_enabled=False),
    )
    dialog.tts_enabled_check.setChecked(True)
    dialog.tts_provider_combo.setCurrentIndex(dialog.tts_provider_combo.findData("gpt-sovits"))
    dialog.tts_work_dir_edit.setText(str(work_dir))
    nanami_index = dialog.character_combo.findData("nanami")
    assert nanami_index >= 0
    dialog.character_combo.setCurrentIndex(nanami_index)
    warnings: list[str] = []
    monkeypatch.setattr(
        settings_dialog_module.QMessageBox,
        "warning",
        lambda _parent, _title, message: warnings.append(message),
    )

    def fake_start_tts_test(_settings, accept_values):  # type: ignore[no-untyped-def]
        dialog._pending_accept_values = accept_values
        dialog._handle_tts_test_failed("服务启动失败")

    monkeypatch.setattr(dialog, "_start_tts_settings_test", fake_start_tts_test)

    dialog.accept()

    assert warnings and "服务启动失败" in warnings[0]
    assert "TTS 设置已保留" in warnings[0]
    assert dialog.tts_enabled_check.isChecked()
    assert dialog.result_tts_settings is not None
    assert dialog.result_tts_settings.enabled
    assert dialog.result_tts_settings.provider == "gpt-sovits"
    assert dialog.result_tts_settings.work_dir == work_dir
    dialog.deleteLater()
    app.processEvents()


def test_settings_dialog_skips_api_test_when_api_unchanged(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qtwidgets = pytest.importorskip("PySide6.QtWidgets")
    if not hasattr(qtwidgets, "QApplication"):
        pytest.skip("当前测试环境只提供了 PySide6 stub。")

    from app.ui.settings_dialog import SettingsDialog

    QApplication = qtwidgets.QApplication
    app = QApplication.instance() or QApplication([])
    root = _ui_runtime_root("api_save_unchanged")
    dialog = SettingsDialog(
        api_settings=ApiSettings(
            base_url="https://api.example.com/v1",
            api_key="test-key",
            model="test-model",
        ),
        tts_settings=_minimal_tts_settings(),
        base_dir=root,
        **_settings_dialog_character_kwargs(root),
        proactive_care_settings=ProactiveCareSettings(screen_context_enabled=True),
        mcp_settings=MCPRuntimeSettings(windows_enabled=False),
    )
    monkeypatch.setattr(
        dialog,
        "_start_api_settings_test",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("API 未变时不应自动测试")),
    )

    dialog.accept()

    assert dialog.result_api_settings is not None
    assert dialog.result_api_settings.model == "test-model"
    assert dialog.result_api_settings.temperature is None
    dialog.deleteLater()
    app.processEvents()


def test_settings_dialog_tests_api_when_api_changes(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qtwidgets = pytest.importorskip("PySide6.QtWidgets")
    if not hasattr(qtwidgets, "QApplication"):
        pytest.skip("当前测试环境只提供了 PySide6 stub。")

    from app.ui.settings_dialog import SettingsDialog

    QApplication = qtwidgets.QApplication
    app = QApplication.instance() or QApplication([])
    root = _ui_runtime_root("api_save_changed")
    dialog = SettingsDialog(
        api_settings=ApiSettings(
            base_url="https://api.example.com/v1",
            api_key="test-key",
            model="test-model",
        ),
        tts_settings=_minimal_tts_settings(),
        base_dir=root,
        **_settings_dialog_character_kwargs(root),
        proactive_care_settings=ProactiveCareSettings(screen_context_enabled=True),
        mcp_settings=MCPRuntimeSettings(windows_enabled=False),
    )
    dialog.model_edit.setText("new-model")
    calls: list[str] = []

    def fake_start_api_test(settings, accept_values=None):  # type: ignore[no-untyped-def]
        calls.append(settings.model)
        assert accept_values is not None
        dialog._continue_accept_after_api_test(accept_values)

    monkeypatch.setattr(dialog, "_start_api_settings_test", fake_start_api_test)

    dialog.accept()

    assert calls == ["new-model"]
    assert dialog.result_api_settings is not None
    assert dialog.result_api_settings.model == "new-model"
    dialog.deleteLater()
    app.processEvents()


def test_settings_dialog_model_combo_saves_manual_input(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qtwidgets = pytest.importorskip("PySide6.QtWidgets")
    if not hasattr(qtwidgets, "QApplication"):
        pytest.skip("当前测试环境只提供了 PySide6 stub。")

    dialog, app = _build_api_settings_dialog("api_manual_model")
    dialog.model_edit.setText("manual-model")
    monkeypatch.setattr(dialog, "_start_api_settings_test", lambda settings, accept_values=None: dialog._continue_accept_after_api_test(accept_values))

    dialog.accept()

    assert dialog.result_api_settings is not None
    assert dialog.result_api_settings.model == "manual-model"
    dialog.deleteLater()
    app.processEvents()


def test_settings_dialog_model_probe_populates_candidates_and_selects_first(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qtwidgets = pytest.importorskip("PySide6.QtWidgets")
    if not hasattr(qtwidgets, "QApplication"):
        pytest.skip("当前测试环境只提供了 PySide6 stub。")

    import app.ui.settings_dialog as settings_dialog_module

    dialog, app = _build_api_settings_dialog("api_model_probe_empty", model="")
    infos: list[str] = []
    monkeypatch.setattr(
        settings_dialog_module.QMessageBox,
        "information",
        lambda _parent, _title, message: infos.append(message),
    )

    dialog._handle_api_model_probe_success(["z-model", "a-model"])

    assert dialog.model_edit.currentText() == "z-model"
    assert [dialog.model_edit.itemText(index) for index in range(dialog.model_edit.count())] == ["z-model", "a-model"]
    assert not hasattr(dialog.model_edit, "_popup_list")
    assert infos and "2" in infos[0]
    dialog.deleteLater()
    app.processEvents()


def test_settings_dialog_model_popups_follow_current_theme_stylesheet() -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qtwidgets = pytest.importorskip("PySide6.QtWidgets")
    if not hasattr(qtwidgets, "QApplication"):
        pytest.skip("当前测试环境只提供了 PySide6 stub。")

    from app.ui.settings_dialog import SettingsDialog
    from app.ui.theme import rgba

    themed = ThemeSettings(
        input_background_color="#102030",
        panel_background_color="#203040",
        text_color="#ddeeff",
    )
    QApplication = qtwidgets.QApplication
    app = QApplication.instance() or QApplication([])
    root = _ui_runtime_root("api_model_popup_theme")
    dialog = SettingsDialog(
        api_settings=ApiSettings(
            base_url="https://api.example.com/v1",
            api_key="test-key",
            model="",
        ),
        tts_settings=_minimal_tts_settings(),
        base_dir=root,
        **_settings_dialog_character_kwargs(root),
        proactive_care_settings=ProactiveCareSettings(screen_context_enabled=True),
        mcp_settings=MCPRuntimeSettings(windows_enabled=False),
        theme_settings=themed,
    )

    dialog.model_edit.set_model_names(["alpha-model", "beta-model"])
    stylesheet = dialog.styleSheet()

    assert "QComboBox QAbstractItemView" in stylesheet
    assert rgba("#102030", 246) in stylesheet
    assert rgba(themed.primary_color, 43) in stylesheet
    assert "#ddeeff" in stylesheet
    assert [dialog.model_edit.itemText(index) for index in range(dialog.model_edit.count())] == [
        "alpha-model",
        "beta-model",
    ]
    assert not hasattr(dialog.model_edit, "_popup_list")

    dialog.deleteLater()
    app.processEvents()


def test_settings_dialog_model_probe_keeps_current_input(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qtwidgets = pytest.importorskip("PySide6.QtWidgets")
    if not hasattr(qtwidgets, "QApplication"):
        pytest.skip("当前测试环境只提供了 PySide6 stub。")

    import app.ui.settings_dialog as settings_dialog_module

    dialog, app = _build_api_settings_dialog("api_model_probe_keep_input", model="custom-model")
    monkeypatch.setattr(settings_dialog_module.QMessageBox, "information", lambda *_args: None)

    dialog._handle_api_model_probe_success(["a-model", "b-model"])

    assert dialog.model_edit.currentText() == "custom-model"
    assert dialog.model_edit.completer().completionModel().rowCount() == 2
    dialog.deleteLater()
    app.processEvents()


def test_settings_dialog_model_probe_failure_keeps_current_model(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qtwidgets = pytest.importorskip("PySide6.QtWidgets")
    if not hasattr(qtwidgets, "QApplication"):
        pytest.skip("当前测试环境只提供了 PySide6 stub。")

    import app.ui.settings_dialog as settings_dialog_module

    dialog, app = _build_api_settings_dialog("api_model_probe_failure", model="current-model")
    warnings: list[str] = []
    monkeypatch.setattr(
        settings_dialog_module.QMessageBox,
        "warning",
        lambda _parent, _title, message: warnings.append(message),
    )

    dialog._handle_api_model_probe_failed("无法连接")

    assert warnings == ["无法连接"]
    assert dialog.model_edit.currentText() == "current-model"
    assert dialog.result_api_settings is None
    dialog.deleteLater()
    app.processEvents()


def test_settings_dialog_model_probe_busy_state_disables_actions() -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qtwidgets = pytest.importorskip("PySide6.QtWidgets")
    if not hasattr(qtwidgets, "QApplication"):
        pytest.skip("当前测试环境只提供了 PySide6 stub。")

    from PySide6.QtWidgets import QDialogButtonBox

    dialog, app = _build_api_settings_dialog("api_model_probe_busy")
    save_button = dialog.button_box.button(QDialogButtonBox.StandardButton.Save)

    dialog._set_api_model_probe_busy(True)

    assert not dialog.api_model_probe_button.isEnabled()
    assert not dialog.api_test_button.isEnabled()
    assert save_button is not None
    assert not save_button.isEnabled()
    assert save_button.text() == "检测模型..."

    dialog._set_api_model_probe_busy(False)

    assert dialog.api_model_probe_button.isEnabled()
    assert dialog.api_test_button.isEnabled()
    assert save_button.isEnabled()
    dialog.deleteLater()
    app.processEvents()


def test_settings_dialog_api_test_failure_blocks_save(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qtwidgets = pytest.importorskip("PySide6.QtWidgets")
    if not hasattr(qtwidgets, "QApplication"):
        pytest.skip("当前测试环境只提供了 PySide6 stub。")

    import app.ui.settings_dialog as settings_dialog_module
    from app.ui.settings_dialog import SettingsDialog

    QApplication = qtwidgets.QApplication
    app = QApplication.instance() or QApplication([])
    root = _ui_runtime_root("api_save_failure")
    dialog = SettingsDialog(
        api_settings=ApiSettings(
            base_url="https://api.example.com/v1",
            api_key="test-key",
            model="test-model",
        ),
        tts_settings=_minimal_tts_settings(),
        base_dir=root,
        **_settings_dialog_character_kwargs(root),
        proactive_care_settings=ProactiveCareSettings(screen_context_enabled=True),
        mcp_settings=MCPRuntimeSettings(windows_enabled=False),
    )
    dialog.model_edit.setText("bad-model")
    warnings: list[str] = []
    monkeypatch.setattr(
        settings_dialog_module.QMessageBox,
        "warning",
        lambda _parent, _title, message: warnings.append(message),
    )

    def fake_start_api_test(_settings, accept_values=None):  # type: ignore[no-untyped-def]
        dialog._pending_api_accept_values = accept_values
        dialog._handle_api_test_failed("模型不可用")

    monkeypatch.setattr(dialog, "_start_api_settings_test", fake_start_api_test)

    dialog.accept()

    assert warnings and "模型不可用" in warnings[0]
    assert dialog.result_api_settings is None
    assert dialog.result_tts_settings is None
    dialog.deleteLater()
    app.processEvents()


def test_settings_dialog_api_success_continues_to_tts_test(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qtwidgets = pytest.importorskip("PySide6.QtWidgets")
    if not hasattr(qtwidgets, "QApplication"):
        pytest.skip("当前测试环境只提供了 PySide6 stub。")

    from app.config.character_loader import CharacterRegistry
    from app.ui.settings_dialog import SettingsDialog

    QApplication = qtwidgets.QApplication
    app = QApplication.instance() or QApplication([])
    root = _ui_runtime_root("api_success_then_tts")
    current_profile = _build_settings_dialog_character(root, "sakura", "Sakura")
    _build_settings_dialog_character(root, "nanami", "Nanami")
    dialog = SettingsDialog(
        api_settings=ApiSettings(
            base_url="https://api.example.com/v1",
            api_key="test-key",
            model="test-model",
        ),
        tts_settings=_minimal_tts_settings(),
        base_dir=root,
        character_registry=CharacterRegistry(root),
        current_character=current_profile,
        proactive_care_settings=ProactiveCareSettings(screen_context_enabled=True),
        mcp_settings=MCPRuntimeSettings(windows_enabled=False),
    )
    dialog.model_edit.setText("new-model")
    dialog.tts_enabled_check.setChecked(True)
    nanami_index = dialog.character_combo.findData("nanami")
    assert nanami_index >= 0
    dialog.character_combo.setCurrentIndex(nanami_index)
    calls: list[str] = []

    def fake_start_api_test(_settings, accept_values=None):  # type: ignore[no-untyped-def]
        calls.append("api")
        assert accept_values is not None
        dialog._continue_accept_after_api_test(accept_values)

    def fake_start_tts_test(_settings, accept_values):  # type: ignore[no-untyped-def]
        calls.append("tts")
        dialog._complete_accept(accept_values)

    monkeypatch.setattr(dialog, "_start_api_settings_test", fake_start_api_test)
    monkeypatch.setattr(dialog, "_start_tts_settings_test", fake_start_tts_test)

    dialog.accept()

    assert calls == ["api", "tts"]
    assert dialog.result_api_settings is not None
    assert dialog.result_api_settings.model == "new-model"
    assert dialog.result_character_id == "nanami"
    dialog.deleteLater()
    app.processEvents()


def test_settings_dialog_blocks_save_while_tts_test_is_running(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qtwidgets = pytest.importorskip("PySide6.QtWidgets")
    if not hasattr(qtwidgets, "QApplication"):
        pytest.skip("当前测试环境只提供了 PySide6 stub。")

    import app.ui.settings_dialog as settings_dialog_module
    from app.ui.settings_dialog import SettingsDialog

    QApplication = qtwidgets.QApplication
    app = QApplication.instance() or QApplication([])
    root = _ui_runtime_root("tts_save_running")
    dialog = SettingsDialog(
        api_settings=ApiSettings(
            base_url="https://api.example.com/v1",
            api_key="test-key",
            model="test-model",
        ),
        tts_settings=_minimal_tts_settings(),
        base_dir=root,
        **_settings_dialog_character_kwargs(root),
        proactive_care_settings=ProactiveCareSettings(screen_context_enabled=True),
        mcp_settings=MCPRuntimeSettings(windows_enabled=False),
    )
    messages: list[str] = []
    monkeypatch.setattr(
        settings_dialog_module.QMessageBox,
        "information",
        lambda _parent, _title, message: messages.append(message),
    )
    dialog._tts_test_thread = object()  # type: ignore[assignment]

    dialog.accept()

    assert messages and "TTS 服务检测仍在进行" in messages[0]
    assert dialog.result_tts_settings is None
    dialog._tts_test_thread = None
    dialog.deleteLater()
    app.processEvents()


def test_settings_dialog_import_character_archive_refreshes_combo(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qtwidgets = pytest.importorskip("PySide6.QtWidgets")
    if not hasattr(qtwidgets, "QApplication"):
        pytest.skip("当前测试环境只提供了 PySide6 stub。")

    import app.ui.settings_dialog as settings_dialog_module
    from app.config.character_archive import export_character_archive
    from app.config.character_loader import CharacterRegistry
    from app.ui.settings_dialog import SettingsDialog

    case_root = _ui_runtime_root("char_import_refresh")
    root = case_root / "runtime"
    source_root = case_root / "source"
    current_profile = _build_settings_dialog_character(root, "sakura", "Sakura")
    imported_profile = _build_settings_dialog_character(source_root, "nanami", "Nanami")
    archive_path = case_root / "nanami.char"
    export_character_archive(imported_profile, archive_path)

    QApplication = qtwidgets.QApplication
    app = QApplication.instance() or QApplication([])
    dialog = SettingsDialog(
        api_settings=ApiSettings(
            base_url="https://api.example.com/v1",
            api_key="test-key",
            model="test-model",
        ),
        tts_settings=_minimal_tts_settings(),
        base_dir=root,
        character_registry=CharacterRegistry(root),
        current_character=current_profile,
        proactive_care_settings=ProactiveCareSettings(screen_context_enabled=True),
        mcp_settings=MCPRuntimeSettings(windows_enabled=False),
    )
    monkeypatch.setattr(
        settings_dialog_module.QFileDialog,
        "getOpenFileName",
        lambda *_args, **_kwargs: (str(archive_path), ""),
    )
    warnings: list[str] = []
    monkeypatch.setattr(settings_dialog_module.QMessageBox, "information", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        settings_dialog_module.QMessageBox,
        "warning",
        lambda _parent, _title, message: warnings.append(message),
    )

    dialog._import_character_archive()

    assert warnings == []
    assert dialog.character_combo.currentData() == "nanami"
    assert dialog._selected_character_profile().display_name == "Nanami"

    dialog.deleteLater()
    app.processEvents()


def test_settings_dialog_import_voice_less_character_archive_disables_tts(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qtwidgets = pytest.importorskip("PySide6.QtWidgets")
    if not hasattr(qtwidgets, "QApplication"):
        pytest.skip("当前测试环境只提供了 PySide6 stub。")

    import app.ui.settings_dialog as settings_dialog_module
    from app.config.character_archive import export_character_archive
    from app.config.character_loader import CharacterRegistry
    from app.ui.settings_dialog import SettingsDialog

    case_root = _ui_runtime_root("char_import_no_voice")
    root = case_root / "runtime"
    source_root = case_root / "source"
    current_profile = _build_settings_dialog_character(root, "sakura", "Sakura")
    imported_profile = _build_settings_dialog_character(source_root, "nanami", "Nanami", with_voice=False)
    archive_path = case_root / "nanami.char"
    export_character_archive(imported_profile, archive_path)

    QApplication = qtwidgets.QApplication
    app = QApplication.instance() or QApplication([])
    dialog = SettingsDialog(
        api_settings=ApiSettings(
            base_url="https://api.example.com/v1",
            api_key="test-key",
            model="test-model",
        ),
        tts_settings=_minimal_tts_settings(),
        base_dir=root,
        character_registry=CharacterRegistry(root),
        current_character=current_profile,
        proactive_care_settings=ProactiveCareSettings(screen_context_enabled=True),
        mcp_settings=MCPRuntimeSettings(windows_enabled=False),
    )
    dialog.tts_enabled_check.setChecked(True)
    messages: list[str] = []
    monkeypatch.setattr(
        settings_dialog_module.QFileDialog,
        "getOpenFileName",
        lambda *_args, **_kwargs: (str(archive_path), ""),
    )
    monkeypatch.setattr(
        settings_dialog_module.QMessageBox,
        "information",
        lambda _parent, _title, message: messages.append(message),
    )
    monkeypatch.setattr(settings_dialog_module.QMessageBox, "warning", lambda *_args, **_kwargs: None)

    dialog._import_character_archive()

    assert dialog.character_combo.currentData() == "nanami"
    assert dialog._selected_character_profile().voice is None
    assert not dialog.tts_enabled_check.isChecked()
    assert messages and "没有语音包" in messages[-1]
    assert "TTS 已自动关闭" in messages[-1]
    dialog.deleteLater()
    app.processEvents()


def test_pet_window_retires_tts_provider_by_closing_it() -> None:
    from app.ui.pet_window import PetWindow

    calls: list[str] = []

    class ProviderStub:
        def close(self) -> None:
            calls.append("close")

    class MinimalWindow:
        _retire_tts_provider = PetWindow._retire_tts_provider

    window = MinimalWindow()
    window.retired_tts_providers = []
    provider = ProviderStub()

    window._retire_tts_provider(provider)

    assert calls == ["close"]
    assert window.retired_tts_providers == [provider]


def test_pet_window_retires_tts_provider_without_stopping_kept_service() -> None:
    from app.ui.pet_window import PetWindow

    calls: list[str] = []

    class ProviderStub:
        def detach_local_service(self) -> None:
            calls.append("detach")

        def close(self) -> None:
            calls.append("close")

    class MinimalWindow:
        _retire_tts_provider = PetWindow._retire_tts_provider

    window = MinimalWindow()
    window.retired_tts_providers = []
    provider = ProviderStub()

    window._retire_tts_provider(provider, keep_local_service=True)

    assert calls == ["detach", "close"]
    assert window.retired_tts_providers == [provider]


def test_tts_local_service_reuse_requires_same_runtime() -> None:
    from app.ui.pet_window import _should_keep_tts_local_service

    class ProviderStub:
        def __init__(self, settings: GPTSoVITSTTSSettings) -> None:
            self.settings = settings

    root = _ui_runtime_root("tts_local_service_reuse")
    settings = replace(
        _minimal_tts_settings(),
        enabled=True,
        work_dir=root / "tts" / "g50",
    )

    assert _should_keep_tts_local_service(ProviderStub(settings), ProviderStub(settings))
    assert not _should_keep_tts_local_service(
        ProviderStub(settings),
        ProviderStub(replace(settings, api_url="http://127.0.0.1:9881/tts")),
    )
    assert not _should_keep_tts_local_service(
        ProviderStub(settings),
        ProviderStub(replace(settings, work_dir=root / "tts" / "cpu")),
    )


def _process_events_until(app, predicate, timeout_ms: int = 1500):  # type: ignore[no-untyped-def]
    deadline = time.monotonic() + timeout_ms / 1000
    while time.monotonic() < deadline:
        app.processEvents()
        if predicate():
            return True
        time.sleep(0.01)
    app.processEvents()
    return predicate()


def _ui_runtime_root(name: str) -> Path:
    root = (
        Path(__file__).resolve().parents[2]
        / "temp"
        / "test_runtime"
        / name
        / uuid.uuid4().hex
    )
    root.mkdir(parents=True, exist_ok=True)
    return root


def _write_fake_runtime_python(path: Path, content: str = "fake") -> None:
    path.write_text(content, encoding="utf-8")
    path.chmod(0o755)


def test_settings_dialog_allows_import_without_existing_character_registry(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qtwidgets = pytest.importorskip("PySide6.QtWidgets")
    if not hasattr(qtwidgets, "QApplication"):
        pytest.skip("当前测试环境只提供了 PySide6 stub。")

    import app.ui.settings_dialog as settings_dialog_module
    from app.config.character_archive import export_character_archive
    from app.ui.settings_dialog import SettingsDialog

    case_root = _ui_runtime_root("empty_char_import")
    root = case_root / "runtime"
    source_root = case_root / "source"
    imported_profile = _build_settings_dialog_character(source_root, "nanami", "Nanami")
    archive_path = case_root / "nanami.char"
    export_character_archive(imported_profile, archive_path)

    QApplication = qtwidgets.QApplication
    app = QApplication.instance() or QApplication([])
    dialog = SettingsDialog(
        api_settings=ApiSettings(
            base_url="https://api.example.com/v1",
            api_key="test-key",
            model="test-model",
        ),
        tts_settings=_minimal_tts_settings(),
        base_dir=root,
        character_registry=None,
        current_character=None,
        proactive_care_settings=ProactiveCareSettings(screen_context_enabled=True),
        mcp_settings=MCPRuntimeSettings(windows_enabled=False),
    )
    monkeypatch.setattr(
        settings_dialog_module.QFileDialog,
        "getOpenFileName",
        lambda *_args, **_kwargs: (str(archive_path), ""),
    )
    warnings: list[str] = []
    monkeypatch.setattr(settings_dialog_module.QMessageBox, "information", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        settings_dialog_module.QMessageBox,
        "warning",
        lambda *_args, **_kwargs: warnings.append(str(_args[2] if len(_args) > 2 else "")),
    )

    assert dialog.character_combo.currentText() == "尚未导入角色"
    assert not dialog.character_combo.isEnabled()
    assert not dialog.character_empty_label.isHidden()
    assert not dialog.character_export_button.isEnabled()

    dialog._import_character_archive()

    assert warnings == []
    assert dialog.character_combo.isEnabled()
    assert dialog.character_combo.currentData() == "nanami"
    assert dialog._selected_character_profile().display_name == "Nanami"
    assert dialog.character_export_button.isEnabled()

    dialog.deleteLater()
    app.processEvents()


def test_settings_dialog_imports_voice_archive_for_selected_character(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qtwidgets = pytest.importorskip("PySide6.QtWidgets")
    if not hasattr(qtwidgets, "QApplication"):
        pytest.skip("当前测试环境只提供了 PySide6 stub。")

    import app.ui.settings_dialog as settings_dialog_module
    from app.config.character_loader import CharacterRegistry
    from app.ui.settings_dialog import SettingsDialog

    case_root = _ui_runtime_root("voice_archive_import")
    root = case_root / "runtime"
    profile = _build_settings_dialog_character(root, "sakura", "Sakura")
    archive_path = _build_settings_dialog_voice_archive(case_root)

    QApplication = qtwidgets.QApplication
    app = QApplication.instance() or QApplication([])
    dialog = SettingsDialog(
        api_settings=ApiSettings(
            base_url="https://api.example.com/v1",
            api_key="test-key",
            model="test-model",
        ),
        tts_settings=_minimal_tts_settings(),
        base_dir=root,
        character_registry=CharacterRegistry(root),
        current_character=profile,
        proactive_care_settings=ProactiveCareSettings(screen_context_enabled=True),
        mcp_settings=MCPRuntimeSettings(windows_enabled=False),
    )
    monkeypatch.setattr(
        settings_dialog_module.QFileDialog,
        "getOpenFileName",
        lambda *_args, **_kwargs: (str(archive_path), ""),
    )
    monkeypatch.setattr(settings_dialog_module.QMessageBox, "information", lambda *_args, **_kwargs: None)
    warnings: list[str] = []
    monkeypatch.setattr(
        settings_dialog_module.QMessageBox,
        "warning",
        lambda _parent, _title, message: warnings.append(message),
    )

    dialog._import_character_voice_archive()

    imported = CharacterRegistry(root).get("sakura")
    assert warnings == []
    assert dialog.character_combo.currentData() == "sakura"
    assert imported.voice is not None
    assert imported.voice.gpt_model_path is not None
    assert imported.voice.gpt_model_path.read_bytes() == b"imported-gpt"
    assert dialog.ref_lang_edit.text() == "zh"
    assert dialog.text_lang_edit.text() == "zh"

    dialog.deleteLater()
    app.processEvents()


def test_settings_dialog_export_button_uses_menu_actions(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qtcore = pytest.importorskip("PySide6.QtCore")
    qtwidgets = pytest.importorskip("PySide6.QtWidgets")
    if not hasattr(qtwidgets, "QApplication"):
        pytest.skip("当前测试环境只提供了 PySide6 stub。")

    import app.ui.settings_dialog as settings_dialog_module
    from app.config.character_loader import CharacterRegistry
    from app.ui.settings_dialog import SettingsDialog

    Qt = qtcore.Qt
    case_root = _ui_runtime_root("char_export_menu")
    root = case_root / "runtime"
    profile = _build_settings_dialog_character(root, "sakura", "Sakura")
    _build_settings_dialog_character(root, "nanami", "Nanami", with_voice=False)
    _build_settings_dialog_character(root, "yuki", "Yuki", with_voice_models=False)

    QApplication = qtwidgets.QApplication
    app = QApplication.instance() or QApplication([])
    dialog = SettingsDialog(
        api_settings=ApiSettings(
            base_url="https://api.example.com/v1",
            api_key="test-key",
            model="test-model",
        ),
        tts_settings=_minimal_tts_settings(),
        base_dir=root,
        character_registry=CharacterRegistry(root),
        current_character=profile,
        proactive_care_settings=ProactiveCareSettings(screen_context_enabled=True),
        mcp_settings=MCPRuntimeSettings(windows_enabled=False),
    )
    save_dialogs: list[tuple[str, str, str]] = []

    def fake_get_save_file_name(_parent, title, default_path, file_filter):  # type: ignore[no-untyped-def]
        save_dialogs.append((title, Path(default_path).name, file_filter))
        return ("", "")

    monkeypatch.setattr(settings_dialog_module.QFileDialog, "getSaveFileName", fake_get_save_file_name)

    assert dialog.character_export_button.text() == "导出"
    assert dialog.character_export_button.menu() is dialog.character_export_menu
    assert bool(dialog.character_export_menu.windowFlags() & Qt.WindowType.FramelessWindowHint)
    assert bool(dialog.character_export_menu.windowFlags() & Qt.WindowType.NoDropShadowWindowHint)
    assert dialog.character_export_menu.testAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
    assert [action.text() for action in dialog.character_export_menu.actions()] == [
        "导出完整包 (.char)",
        "导出单角色包 (.char)",
        "导出语音包 (.voice)",
    ]
    assert dialog.character_export_full_action.isEnabled()
    assert dialog.character_export_card_action.isEnabled()
    assert dialog.character_export_voice_action.isEnabled()

    dialog.character_export_full_action.trigger()
    dialog.character_export_card_action.trigger()
    dialog.character_export_voice_action.trigger()

    assert save_dialogs == [
        ("导出 Sakura 完整角色包", "sakura.char", "Sakura 角色包 (*.char)"),
        ("导出 Sakura 单角色包", "sakura.card.char", "Sakura 角色包 (*.char)"),
        ("导出 Sakura TTS 模型包", "sakura.voice", "Sakura TTS 模型包 (*.voice)"),
    ]

    nanami_index = dialog.character_combo.findData("nanami")
    assert nanami_index >= 0
    dialog.character_combo.setCurrentIndex(nanami_index)
    assert not dialog.character_export_full_action.isEnabled()
    assert dialog.character_export_card_action.isEnabled()
    assert not dialog.character_export_voice_action.isEnabled()
    assert "没有完整语音模型" in dialog.character_export_full_action.toolTip()
    assert "没有可导出的语音模型" in dialog.character_export_voice_action.toolTip()

    yuki_index = dialog.character_combo.findData("yuki")
    assert yuki_index >= 0
    dialog.character_combo.setCurrentIndex(yuki_index)
    assert not dialog.character_export_full_action.isEnabled()
    assert dialog.character_export_card_action.isEnabled()
    assert not dialog.character_export_voice_action.isEnabled()
    assert "没有完整语音模型" in dialog.character_export_full_action.toolTip()
    assert "没有可导出的语音模型" in dialog.character_export_voice_action.toolTip()

    dialog.deleteLater()
    app.processEvents()


def test_settings_dialog_exports_character_archive_in_background(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qtwidgets = pytest.importorskip("PySide6.QtWidgets")
    if not hasattr(qtwidgets, "QApplication"):
        pytest.skip("当前测试环境只提供了 PySide6 stub。")

    import app.ui.settings_dialog as settings_dialog_module
    import app.ui.settings.workers as settings_workers
    from app.config.character_loader import CharacterRegistry
    from app.ui.settings_dialog import SettingsDialog

    case_root = _ui_runtime_root("char_export_background")
    root = case_root / "runtime"
    profile = _build_settings_dialog_character(root, "sakura", "Sakura")
    output_path = case_root / "sakura.char"
    started = threading.Event()
    release = threading.Event()
    exported: dict[str, object] = {}
    messages: list[str] = []

    def fake_export_character_archive(export_profile, export_path, *, include_voice=True):  # type: ignore[no-untyped-def]
        exported["profile"] = export_profile
        exported["path"] = export_path
        exported["include_voice"] = include_voice
        started.set()
        assert release.wait(2)
        Path(export_path).write_text("done", encoding="utf-8")

    QApplication = qtwidgets.QApplication
    QDialogButtonBox = qtwidgets.QDialogButtonBox
    app = QApplication.instance() or QApplication([])
    dialog = SettingsDialog(
        api_settings=ApiSettings(
            base_url="https://api.example.com/v1",
            api_key="test-key",
            model="test-model",
        ),
        tts_settings=_minimal_tts_settings(),
        base_dir=root,
        character_registry=CharacterRegistry(root),
        current_character=profile,
        proactive_care_settings=ProactiveCareSettings(screen_context_enabled=True),
        mcp_settings=MCPRuntimeSettings(windows_enabled=False),
    )
    monkeypatch.setattr(
        settings_dialog_module.QFileDialog,
        "getSaveFileName",
        lambda *_args, **_kwargs: (str(output_path), ""),
    )
    monkeypatch.setattr(
        settings_workers,
        "export_character_archive",
        fake_export_character_archive,
    )
    monkeypatch.setattr(
        settings_dialog_module.QMessageBox,
        "information",
        lambda *_args, **_kwargs: messages.append(str(_args[2])),
    )

    dialog._export_current_character_archive()

    try:
        assert started.wait(1.5)
        app.processEvents()
        assert dialog._character_export_thread is not None
        assert exported["profile"] == profile
        assert exported["path"] == output_path
        assert exported["include_voice"] is True
        assert not dialog.character_import_button.isEnabled()
        assert not dialog.character_export_button.isEnabled()
        assert not dialog.button_box.button(QDialogButtonBox.StandardButton.Save).isEnabled()
        assert not dialog.button_box.button(QDialogButtonBox.StandardButton.Cancel).isEnabled()
    finally:
        release.set()

    assert _process_events_until(app, lambda: dialog._character_export_thread is None)

    assert output_path.read_text(encoding="utf-8") == "done"
    assert dialog.character_import_button.isEnabled()
    assert dialog.character_export_button.isEnabled()
    assert dialog.button_box.button(QDialogButtonBox.StandardButton.Save).isEnabled()
    assert dialog.button_box.button(QDialogButtonBox.StandardButton.Cancel).isEnabled()
    assert any(str(output_path) in message for message in messages)

    dialog.deleteLater()
    app.processEvents()


def test_settings_dialog_formats_memory_time_as_local_timezone() -> None:
    from app.ui.settings_dialog import _format_memory_time

    utc_time = "2026-06-01T18:42:27Z"
    expected = datetime.fromisoformat("2026-06-01T18:42:27+00:00").astimezone().strftime(
        "%Y-%m-%d %H:%M:%S"
    )

    assert _format_memory_time(utc_time) == expected
    assert _format_memory_time("2026-06-02T02:42:27+08:00") == expected


def test_settings_dialog_loads_memory_after_memory_tab_selected_in_background() -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qtwidgets = pytest.importorskip("PySide6.QtWidgets")
    if not all(hasattr(qtwidgets, name) for name in ("QApplication", "QListWidget")):
        pytest.skip("当前测试环境只提供了 PySide6 stub。")

    from app.ui.settings_dialog import SettingsDialog

    class MemoryStoreStub:
        def __init__(self) -> None:
            self.list_calls = 0

        def list_memories(self, *, limit: int = 20):  # type: ignore[no-untyped-def]
            self.list_calls += 1
            return [
                {
                    "id": "memory-001",
                    "content": "主人喜欢精简的管理界面",
                    "updated_at": "2026-06-02T01:00:00Z",
                }
            ]

    QApplication = qtwidgets.QApplication
    app = QApplication.instance() or QApplication([])
    memory_store = MemoryStoreStub()
    dialog = SettingsDialog(
        api_settings=ApiSettings(
            base_url="https://api.example.com/v1",
            api_key="test-key",
            model="test-model",
        ),
        tts_settings=_minimal_tts_settings(),
        base_dir=Path("."),
        proactive_care_settings=ProactiveCareSettings(screen_context_enabled=True),
        mcp_settings=MCPRuntimeSettings(windows_enabled=False),
        memory_store=memory_store,  # type: ignore[arg-type]
    )

    assert dialog.memory_status_label.text() == "打开记忆页时读取长期记忆。"
    assert memory_store.list_calls == 0
    nav = dialog.findChild(qtwidgets.QListWidget, "settingsNavList")
    assert nav is not None
    memory_row = [nav.item(index).text() for index in range(nav.count())].index("记忆")
    nav.setCurrentRow(memory_row)

    assert dialog.memory_status_label.text() == "正在读取长期记忆..."
    assert _process_events_until(app, lambda: memory_store.list_calls == 1)
    assert _process_events_until(app, lambda: dialog._memory_list_thread is None)
    assert dialog.memory_status_label.text() == "已加载 1 条记忆"
    assert dialog.memory_table.item(0, 1).text() == "主人喜欢精简的管理界面"
    dialog.deleteLater()
    app.processEvents()


def test_settings_dialog_sorts_memory_by_latest_time_on_top() -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qtwidgets = pytest.importorskip("PySide6.QtWidgets")
    if not all(hasattr(qtwidgets, name) for name in ("QApplication", "QListWidget")):
        pytest.skip("当前测试环境只提供了 PySide6 stub。")

    from app.ui.settings_dialog import SettingsDialog

    class MemoryStoreStub:
        def __init__(self) -> None:
            self.list_calls = 0

        def list_memories(self, *, limit: int = 20):  # type: ignore[no-untyped-def]
            self.list_calls += 1
            return [
                {
                    "id": "memory-old",
                    "content": "较旧记忆",
                    "updated_at": "2026-06-01T10:00:00+08:00",
                },
                {"id": "memory-missing-time", "content": "缺少时间记忆"},
                {
                    "id": "memory-created",
                    "content": "按创建时间兜底的记忆",
                    "created_at": "2026-06-02T09:00:00+08:00",
                },
                {
                    "id": "memory-new",
                    "content": "最新记忆",
                    "updated_at": "2026-06-03T12:00:00+08:00",
                },
            ]

    QApplication = qtwidgets.QApplication
    app = QApplication.instance() or QApplication([])
    memory_store = MemoryStoreStub()
    dialog = SettingsDialog(
        api_settings=ApiSettings(
            base_url="https://api.example.com/v1",
            api_key="test-key",
            model="test-model",
        ),
        tts_settings=_minimal_tts_settings(),
        base_dir=Path("."),
        proactive_care_settings=ProactiveCareSettings(screen_context_enabled=True),
        mcp_settings=MCPRuntimeSettings(windows_enabled=False),
        memory_store=memory_store,  # type: ignore[arg-type]
    )

    assert memory_store.list_calls == 0
    nav = dialog.findChild(qtwidgets.QListWidget, "settingsNavList")
    assert nav is not None
    memory_row = [nav.item(index).text() for index in range(nav.count())].index("记忆")
    nav.setCurrentRow(memory_row)

    assert _process_events_until(app, lambda: dialog._memory_list_thread is None)
    assert [dialog.memory_table.item(row, 1).text() for row in range(4)] == [
        "最新记忆",
        "按创建时间兜底的记忆",
        "较旧记忆",
        "缺少时间记忆",
    ]
    dialog.deleteLater()
    app.processEvents()


def test_settings_dialog_memory_loader_thread_is_not_dialog_child() -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qtwidgets = pytest.importorskip("PySide6.QtWidgets")
    if not all(hasattr(qtwidgets, name) for name in ("QApplication", "QListWidget")):
        pytest.skip("当前测试环境只提供了 PySide6 stub。")

    from app.ui.settings_dialog import SettingsDialog

    class BlockingMemoryStore:
        def __init__(self) -> None:
            self.started = threading.Event()
            self.release = threading.Event()

        def list_memories(self, *, limit: int = 20):  # type: ignore[no-untyped-def]
            self.started.set()
            assert self.release.wait(2)
            return []

    QApplication = qtwidgets.QApplication
    app = QApplication.instance() or QApplication([])
    memory_store = BlockingMemoryStore()
    dialog = SettingsDialog(
        api_settings=ApiSettings(
            base_url="https://api.example.com/v1",
            api_key="test-key",
            model="test-model",
        ),
        tts_settings=_minimal_tts_settings(),
        base_dir=Path("."),
        proactive_care_settings=ProactiveCareSettings(screen_context_enabled=True),
        mcp_settings=MCPRuntimeSettings(windows_enabled=False),
        memory_store=memory_store,  # type: ignore[arg-type]
    )

    try:
        nav = dialog.findChild(qtwidgets.QListWidget, "settingsNavList")
        assert nav is not None
        memory_row = [nav.item(index).text() for index in range(nav.count())].index("记忆")
        nav.setCurrentRow(memory_row)
        assert _process_events_until(app, lambda: memory_store.started.is_set())
        assert dialog._memory_list_thread is not None
        assert dialog._memory_list_thread.parent() is None
    finally:
        memory_store.release.set()

    assert _process_events_until(app, lambda: dialog._memory_list_thread is None)
    dialog.deleteLater()
    app.processEvents()


def test_settings_dialog_shows_memory_dependency_download_hint() -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qtwidgets = pytest.importorskip("PySide6.QtWidgets")
    if not all(hasattr(qtwidgets, name) for name in ("QApplication", "QListWidget")):
        pytest.skip("当前测试环境只提供了 PySide6 stub。")

    from app.ui.settings_dialog import SettingsDialog

    class MemoryStoreStub:
        def __init__(self) -> None:
            self.list_calls = 0

        def needs_embedding_model_download(self) -> bool:
            return True

        def list_memories(self, *, limit: int = 20):  # type: ignore[no-untyped-def]
            self.list_calls += 1
            return []

    QApplication = qtwidgets.QApplication
    app = QApplication.instance() or QApplication([])
    memory_store = MemoryStoreStub()
    dialog = SettingsDialog(
        api_settings=ApiSettings(
            base_url="https://api.example.com/v1",
            api_key="test-key",
            model="test-model",
        ),
        tts_settings=_minimal_tts_settings(),
        base_dir=Path("."),
        proactive_care_settings=ProactiveCareSettings(screen_context_enabled=True),
        mcp_settings=MCPRuntimeSettings(windows_enabled=False),
        memory_store=memory_store,  # type: ignore[arg-type]
    )

    expected = "长期记忆系统正在初始化，首次启动可能需要下载本地嵌入模型，请稍等。"
    assert dialog.memory_status_label.text() == "打开记忆页时读取长期记忆。"
    assert memory_store.list_calls == 0
    nav = dialog.findChild(qtwidgets.QListWidget, "settingsNavList")
    assert nav is not None
    memory_row = [nav.item(index).text() for index in range(nav.count())].index("记忆")
    nav.setCurrentRow(memory_row)

    assert dialog.memory_status_label.text() == expected
    assert dialog.memory_table.item(0, 1).text() == expected
    assert _process_events_until(app, lambda: memory_store.list_calls == 1)
    assert _process_events_until(app, lambda: dialog._memory_list_thread is None)
    assert dialog.memory_status_label.text() == "已加载 0 条记忆"
    dialog.deleteLater()
    app.processEvents()


def test_settings_dialog_imports_memory_model_archive(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qtwidgets = pytest.importorskip("PySide6.QtWidgets")
    if not all(hasattr(qtwidgets, name) for name in ("QApplication", "QListWidget")):
        pytest.skip("当前测试环境只提供了 PySide6 stub。")

    from app.ui import settings_dialog as settings_dialog_module
    from app.ui.settings_dialog import SettingsDialog

    class ImportResult:
        model_name = "sentence-transformers/all-MiniLM-L6-v2"
        cache_folder = Path("runtime/hf-cache/hub")
        snapshot_count = 1

    class MemoryStoreStub:
        def __init__(self) -> None:
            self.list_calls = 0
            self.imported_paths: list[Path] = []

        def needs_embedding_model_download(self) -> bool:
            return True

        def list_memories(self, *, limit: int = 20):  # type: ignore[no-untyped-def]
            self.list_calls += 1
            return []

        def import_embedding_model_archive(self, archive_path: Path) -> ImportResult:
            self.imported_paths.append(archive_path)
            return ImportResult()

    QApplication = qtwidgets.QApplication
    app = QApplication.instance() or QApplication([])
    root = _ui_runtime_root("memory_model_import_dialog")
    archive_path = root / "models--sentence-transformers--all-MiniLM-L6-v2.zip"
    archive_path.write_bytes(b"zip")
    memory_store = MemoryStoreStub()
    monkeypatch.setattr(
        settings_dialog_module.QFileDialog,
        "getOpenFileName",
        lambda *_args, **_kwargs: (str(archive_path), "记忆模型 ZIP (*.zip)"),
    )
    monkeypatch.setattr(settings_dialog_module.QMessageBox, "information", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(settings_dialog_module.QMessageBox, "warning", lambda *_args, **_kwargs: None)
    dialog = SettingsDialog(
        api_settings=ApiSettings(
            base_url="https://api.example.com/v1",
            api_key="test-key",
            model="test-model",
        ),
        tts_settings=_minimal_tts_settings(),
        base_dir=root,
        proactive_care_settings=ProactiveCareSettings(screen_context_enabled=True),
        mcp_settings=MCPRuntimeSettings(windows_enabled=False),
        memory_store=memory_store,  # type: ignore[arg-type]
    )

    assert hasattr(dialog, "memory_import_model_button")
    nav = dialog.findChild(qtwidgets.QListWidget, "settingsNavList")
    assert nav is not None
    memory_row = [nav.item(index).text() for index in range(nav.count())].index("记忆")
    nav.setCurrentRow(memory_row)
    assert _process_events_until(app, lambda: dialog._memory_list_thread is None)

    dialog.memory_import_model_button.click()

    assert _process_events_until(app, lambda: dialog._memory_model_import_thread is None)
    assert memory_store.imported_paths == [archive_path]
    assert _process_events_until(app, lambda: dialog._memory_list_thread is None)
    assert memory_store.list_calls >= 2
    dialog.deleteLater()
    app.processEvents()


def test_settings_dialog_filters_memory_locally() -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qtwidgets = pytest.importorskip("PySide6.QtWidgets")
    if not hasattr(qtwidgets, "QApplication"):
        pytest.skip("当前测试环境只提供了 PySide6 stub。")

    from app.ui.settings_dialog import SettingsDialog

    class MemoryStoreStub:
        def __init__(self) -> None:
            self.list_calls = 0

        def list_memories(self, *, limit: int = 20):  # type: ignore[no-untyped-def]
            self.list_calls += 1
            return [
                {"id": "alpha-001", "content": "偏好浅色界面"},
                {"id": "beta-002", "content": "喜欢批量管理记忆"},
            ]

    QApplication = qtwidgets.QApplication
    app = QApplication.instance() or QApplication([])
    memory_store = MemoryStoreStub()
    dialog = SettingsDialog(
        api_settings=ApiSettings(
            base_url="https://api.example.com/v1",
            api_key="test-key",
            model="test-model",
        ),
        tts_settings=_minimal_tts_settings(),
        base_dir=Path("."),
        proactive_care_settings=ProactiveCareSettings(screen_context_enabled=True),
        mcp_settings=MCPRuntimeSettings(windows_enabled=False),
        memory_store=memory_store,  # type: ignore[arg-type]
    )

    _open_settings_memory_tab(dialog, qtwidgets, app)
    assert _process_events_until(app, lambda: dialog._memory_list_thread is None)
    assert memory_store.list_calls == 1

    dialog.memory_search_edit.setText("批量")
    app.processEvents()

    assert memory_store.list_calls == 1
    assert dialog.memory_table.rowCount() == 1
    assert dialog.memory_table.item(0, 1).text() == "喜欢批量管理记忆"
    dialog.deleteLater()
    app.processEvents()


def test_settings_dialog_deletes_selected_memories(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qtwidgets = pytest.importorskip("PySide6.QtWidgets")
    if not hasattr(qtwidgets, "QApplication"):
        pytest.skip("当前测试环境只提供了 PySide6 stub。")

    from app.ui import settings_dialog as settings_dialog_module
    from app.ui.settings_dialog import SettingsDialog

    class MemoryStoreStub:
        def __init__(self) -> None:
            self.list_calls = 0
            self.deleted: list[str] = []
            self.records = [
                {"id": "memory-001", "content": "第一条记忆"},
                {"id": "memory-002", "content": "第二条记忆"},
                {"id": "memory-003", "content": "第三条记忆"},
            ]

        def list_memories(self, *, limit: int = 20):  # type: ignore[no-untyped-def]
            self.list_calls += 1
            return [record for record in self.records if record["id"] not in self.deleted]

        def forget_memory(self, arguments):  # type: ignore[no-untyped-def]
            self.deleted.append(arguments["id"])
            return {"forgotten": {"id": arguments["id"]}}

    QApplication = qtwidgets.QApplication
    app = QApplication.instance() or QApplication([])
    memory_store = MemoryStoreStub()
    monkeypatch.setattr(
        settings_dialog_module.QMessageBox,
        "question",
        lambda *_args, **_kwargs: settings_dialog_module.QMessageBox.StandardButton.Yes,
    )
    dialog = SettingsDialog(
        api_settings=ApiSettings(
            base_url="https://api.example.com/v1",
            api_key="test-key",
            model="test-model",
        ),
        tts_settings=_minimal_tts_settings(),
        base_dir=Path("."),
        proactive_care_settings=ProactiveCareSettings(screen_context_enabled=True),
        mcp_settings=MCPRuntimeSettings(windows_enabled=False),
        memory_store=memory_store,  # type: ignore[arg-type]
    )
    _open_settings_memory_tab(dialog, qtwidgets, app)
    assert _process_events_until(app, lambda: dialog._memory_list_thread is None)

    dialog._set_memory_checked(0, True)
    dialog._set_memory_checked(1, True)
    app.processEvents()

    assert dialog.memory_delete_button.isEnabled()
    assert dialog._selected_memory_ids == {"memory-001", "memory-002"}
    dialog._delete_memory_entry()

    assert memory_store.deleted == ["memory-001", "memory-002"]
    assert _process_events_until(app, lambda: dialog._memory_list_thread is None)
    assert memory_store.list_calls == 2
    dialog.deleteLater()
    app.processEvents()


def test_settings_dialog_reports_partial_memory_delete_failure(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qtwidgets = pytest.importorskip("PySide6.QtWidgets")
    if not hasattr(qtwidgets, "QApplication"):
        pytest.skip("当前测试环境只提供了 PySide6 stub。")

    from app.ui import settings_dialog as settings_dialog_module
    from app.ui.settings_dialog import SettingsDialog

    class MemoryStoreStub:
        def __init__(self) -> None:
            self.deleted: list[str] = []
            self.records = [
                {"id": "memory-001", "content": "第一条记忆"},
                {"id": "memory-002", "content": "第二条记忆"},
            ]

        def list_memories(self, *, limit: int = 20):  # type: ignore[no-untyped-def]
            return [record for record in self.records if record["id"] not in self.deleted]

        def forget_memory(self, arguments):  # type: ignore[no-untyped-def]
            if arguments["id"] == "memory-002":
                raise RuntimeError("后端删除失败")
            self.deleted.append(arguments["id"])
            return {"forgotten": {"id": arguments["id"]}}

    QApplication = qtwidgets.QApplication
    app = QApplication.instance() or QApplication([])
    warnings: list[str] = []
    monkeypatch.setattr(
        settings_dialog_module.QMessageBox,
        "question",
        lambda *_args, **_kwargs: settings_dialog_module.QMessageBox.StandardButton.Yes,
    )
    monkeypatch.setattr(
        settings_dialog_module.QMessageBox,
        "warning",
        lambda _parent, _title, message: warnings.append(message),
    )
    dialog = SettingsDialog(
        api_settings=ApiSettings(
            base_url="https://api.example.com/v1",
            api_key="test-key",
            model="test-model",
        ),
        tts_settings=_minimal_tts_settings(),
        base_dir=Path("."),
        proactive_care_settings=ProactiveCareSettings(screen_context_enabled=True),
        mcp_settings=MCPRuntimeSettings(windows_enabled=False),
        memory_store=MemoryStoreStub(),  # type: ignore[arg-type]
    )
    _open_settings_memory_tab(dialog, qtwidgets, app)
    assert _process_events_until(app, lambda: dialog._memory_list_thread is None)

    dialog._set_memory_checked(0, True)
    dialog._set_memory_checked(1, True)
    dialog._delete_memory_entry()

    assert warnings
    assert "已删除 1 条，失败 1 条" in warnings[-1]
    assert "后端删除失败" in warnings[-1]
    assert _process_events_until(app, lambda: dialog._memory_list_thread is None)
    dialog.deleteLater()
    app.processEvents()


def test_settings_dialog_selects_all_visible_memories_without_native_selection() -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qtwidgets = pytest.importorskip("PySide6.QtWidgets")
    if not hasattr(qtwidgets, "QApplication"):
        pytest.skip("当前测试环境只提供了 PySide6 stub。")

    from app.ui.settings_dialog import SettingsDialog

    class MemoryStoreStub:
        def list_memories(self, *, limit: int = 20):  # type: ignore[no-untyped-def]
            return [
                {"id": "memory-001", "content": "第一条记忆"},
                {"id": "memory-002", "content": "第二条记忆"},
            ]

    QApplication = qtwidgets.QApplication
    app = QApplication.instance() or QApplication([])
    dialog = SettingsDialog(
        api_settings=ApiSettings(
            base_url="https://api.example.com/v1",
            api_key="test-key",
            model="test-model",
        ),
        tts_settings=_minimal_tts_settings(),
        base_dir=Path("."),
        proactive_care_settings=ProactiveCareSettings(screen_context_enabled=True),
        mcp_settings=MCPRuntimeSettings(windows_enabled=False),
        memory_store=MemoryStoreStub(),  # type: ignore[arg-type]
    )
    _open_settings_memory_tab(dialog, qtwidgets, app)
    assert _process_events_until(app, lambda: dialog._memory_list_thread is None)

    dialog._toggle_select_all_visible_memories()

    assert dialog._selected_memory_ids == {"memory-001", "memory-002"}
    assert dialog.memory_select_all_check.isChecked()
    row_checkbox = dialog.memory_table.cellWidget(0, 0).findChild(qtwidgets.QCheckBox)
    assert row_checkbox is not None
    assert row_checkbox.isChecked()
    assert dialog.memory_table.selectionModel().selectedRows() == []
    assert dialog.memory_editor_container.isHidden()
    dialog.deleteLater()
    app.processEvents()


def test_settings_dialog_select_all_only_affects_filtered_results() -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qtwidgets = pytest.importorskip("PySide6.QtWidgets")
    if not hasattr(qtwidgets, "QApplication"):
        pytest.skip("当前测试环境只提供了 PySide6 stub。")

    from app.ui.settings_dialog import SettingsDialog

    class MemoryStoreStub:
        def list_memories(self, *, limit: int = 20):  # type: ignore[no-untyped-def]
            return [
                {"id": "alpha-001", "content": "浅色界面"},
                {"id": "beta-002", "content": "批量管理"},
            ]

    QApplication = qtwidgets.QApplication
    app = QApplication.instance() or QApplication([])
    dialog = SettingsDialog(
        api_settings=ApiSettings(
            base_url="https://api.example.com/v1",
            api_key="test-key",
            model="test-model",
        ),
        tts_settings=_minimal_tts_settings(),
        base_dir=Path("."),
        proactive_care_settings=ProactiveCareSettings(screen_context_enabled=True),
        mcp_settings=MCPRuntimeSettings(windows_enabled=False),
        memory_store=MemoryStoreStub(),  # type: ignore[arg-type]
    )
    _open_settings_memory_tab(dialog, qtwidgets, app)
    assert _process_events_until(app, lambda: dialog._memory_list_thread is None)

    dialog.memory_search_edit.setText("批量")
    dialog._toggle_select_all_visible_memories()

    assert dialog._selected_memory_ids == {"beta-002"}
    assert dialog.memory_table.rowCount() == 1
    assert dialog.memory_select_all_check.isChecked()
    dialog.deleteLater()
    app.processEvents()


def test_settings_dialog_single_selection_opens_editor_and_updates_memory(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qtwidgets = pytest.importorskip("PySide6.QtWidgets")
    if not hasattr(qtwidgets, "QApplication"):
        pytest.skip("当前测试环境只提供了 PySide6 stub。")

    from app.ui import settings_dialog as settings_dialog_module
    from app.ui.settings_dialog import SettingsDialog

    class MemoryStoreStub:
        def __init__(self) -> None:
            self.updated: list[dict[str, str]] = []
            self.records = [{"id": "memory-001", "content": "旧内容"}]

        def list_memories(self, *, limit: int = 20):  # type: ignore[no-untyped-def]
            return list(self.records)

        def update_memory(self, arguments, *, allow_sensitive=False):  # type: ignore[no-untyped-def]
            self.updated.append(
                {
                    "id": arguments["id"],
                    "content": arguments["content"],
                    "allow_sensitive": str(allow_sensitive),
                }
            )
            self.records[0]["content"] = arguments["content"]
            return {"memory": self.records[0]}

    QApplication = qtwidgets.QApplication
    app = QApplication.instance() or QApplication([])
    memory_store = MemoryStoreStub()
    monkeypatch.setattr(settings_dialog_module.QMessageBox, "information", lambda *_args, **_kwargs: None)
    dialog = SettingsDialog(
        api_settings=ApiSettings(
            base_url="https://api.example.com/v1",
            api_key="test-key",
            model="test-model",
        ),
        tts_settings=_minimal_tts_settings(),
        base_dir=Path("."),
        proactive_care_settings=ProactiveCareSettings(screen_context_enabled=True),
        mcp_settings=MCPRuntimeSettings(windows_enabled=False),
        memory_store=memory_store,  # type: ignore[arg-type]
    )
    _open_settings_memory_tab(dialog, qtwidgets, app)
    assert _process_events_until(app, lambda: dialog._memory_list_thread is None)

    dialog._open_memory_editor(0)

    assert not dialog.memory_editor_container.isHidden()
    assert dialog.memory_save_button.text() == "保存修改"
    assert dialog.memory_content_edit.toPlainText() == "旧内容"
    assert dialog.memory_preview_label.text() == ""

    dialog.memory_content_edit.setPlainText("新内容")
    dialog._save_memory_entry()

    assert memory_store.updated == [
        {"id": "memory-001", "content": "新内容", "allow_sensitive": "True"}
    ]
    assert dialog._selected_memory_ids == {"memory-001"}
    assert _process_events_until(app, lambda: dialog._memory_list_thread is None)
    dialog.deleteLater()
    app.processEvents()


def test_settings_dialog_content_click_switches_to_single_selection() -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qtwidgets = pytest.importorskip("PySide6.QtWidgets")
    if not hasattr(qtwidgets, "QApplication"):
        pytest.skip("当前测试环境只提供了 PySide6 stub。")

    from app.ui.settings_dialog import SettingsDialog

    class MemoryStoreStub:
        def list_memories(self, *, limit: int = 20):  # type: ignore[no-untyped-def]
            return [
                {"id": "memory-001", "content": "第一条记忆"},
                {"id": "memory-002", "content": "第二条记忆"},
            ]

    QApplication = qtwidgets.QApplication
    app = QApplication.instance() or QApplication([])
    dialog = SettingsDialog(
        api_settings=ApiSettings(
            base_url="https://api.example.com/v1",
            api_key="test-key",
            model="test-model",
        ),
        tts_settings=_minimal_tts_settings(),
        base_dir=Path("."),
        proactive_care_settings=ProactiveCareSettings(screen_context_enabled=True),
        mcp_settings=MCPRuntimeSettings(windows_enabled=False),
        memory_store=MemoryStoreStub(),  # type: ignore[arg-type]
    )
    _open_settings_memory_tab(dialog, qtwidgets, app)
    assert _process_events_until(app, lambda: dialog._memory_list_thread is None)

    dialog._set_memory_checked(0, True)
    dialog._handle_memory_item_clicked(dialog.memory_table.item(1, 1))

    assert dialog._selected_memory_ids == {"memory-002"}
    assert dialog._editing_memory_id == "memory-002"
    assert dialog.memory_content_edit.toPlainText() == "第二条记忆"
    assert dialog.memory_selection_label.text() == "已选择 1 条"
    dialog.deleteLater()
    app.processEvents()


def test_settings_dialog_first_column_click_toggles_check_only() -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qtwidgets = pytest.importorskip("PySide6.QtWidgets")
    if not hasattr(qtwidgets, "QApplication"):
        pytest.skip("当前测试环境只提供了 PySide6 stub。")

    from app.ui.settings_dialog import SettingsDialog

    class MemoryStoreStub:
        def list_memories(self, *, limit: int = 20):  # type: ignore[no-untyped-def]
            return [{"id": "memory-001", "content": "第一条记忆"}]

    QApplication = qtwidgets.QApplication
    app = QApplication.instance() or QApplication([])
    dialog = SettingsDialog(
        api_settings=ApiSettings(
            base_url="https://api.example.com/v1",
            api_key="test-key",
            model="test-model",
        ),
        tts_settings=_minimal_tts_settings(),
        base_dir=Path("."),
        proactive_care_settings=ProactiveCareSettings(screen_context_enabled=True),
        mcp_settings=MCPRuntimeSettings(windows_enabled=False),
        memory_store=MemoryStoreStub(),  # type: ignore[arg-type]
    )
    _open_settings_memory_tab(dialog, qtwidgets, app)
    assert _process_events_until(app, lambda: dialog._memory_list_thread is None)

    dialog._handle_memory_item_clicked(dialog.memory_table.item(0, 0))

    assert dialog._selected_memory_ids == {"memory-001"}
    assert dialog._editing_memory_id is None
    assert dialog.memory_editor_container.isHidden()
    dialog.deleteLater()
    app.processEvents()


def test_settings_dialog_multiple_checked_rows_keep_current_editor() -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qtwidgets = pytest.importorskip("PySide6.QtWidgets")
    if not hasattr(qtwidgets, "QApplication"):
        pytest.skip("当前测试环境只提供了 PySide6 stub。")

    from app.ui.settings_dialog import SettingsDialog

    class MemoryStoreStub:
        def list_memories(self, *, limit: int = 20):  # type: ignore[no-untyped-def]
            return [
                {"id": "memory-001", "content": "第一条记忆"},
                {"id": "memory-002", "content": "第二条记忆"},
            ]

    QApplication = qtwidgets.QApplication
    app = QApplication.instance() or QApplication([])
    dialog = SettingsDialog(
        api_settings=ApiSettings(
            base_url="https://api.example.com/v1",
            api_key="test-key",
            model="test-model",
        ),
        tts_settings=_minimal_tts_settings(),
        base_dir=Path("."),
        proactive_care_settings=ProactiveCareSettings(screen_context_enabled=True),
        mcp_settings=MCPRuntimeSettings(windows_enabled=False),
        memory_store=MemoryStoreStub(),  # type: ignore[arg-type]
    )
    _open_settings_memory_tab(dialog, qtwidgets, app)
    assert _process_events_until(app, lambda: dialog._memory_list_thread is None)

    dialog._open_memory_editor(0)
    assert not dialog.memory_editor_container.isHidden()
    dialog._set_memory_checked(0, True)
    dialog._set_memory_checked(1, True)

    assert not dialog.memory_editor_container.isHidden()
    assert dialog.memory_delete_button.isEnabled()
    assert dialog.memory_selection_label.text() == "已选择 2 条"
    assert dialog.memory_content_edit.toPlainText() == "第一条记忆"
    dialog.deleteLater()
    app.processEvents()


def test_settings_dialog_collapses_manual_memory_entry_after_save(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qtwidgets = pytest.importorskip("PySide6.QtWidgets")
    if not hasattr(qtwidgets, "QApplication"):
        pytest.skip("当前测试环境只提供了 PySide6 stub。")

    from app.ui import settings_dialog as settings_dialog_module
    from app.ui.settings_dialog import SettingsDialog

    class MemoryStoreStub:
        def __init__(self) -> None:
            self.created: list[str] = []
            self.records: list[dict[str, str]] = []

        def list_memories(self, *, limit: int = 20):  # type: ignore[no-untyped-def]
            return list(self.records)

        def create_memory(self, arguments, *, allow_sensitive=False):  # type: ignore[no-untyped-def]
            self.created.append(arguments["content"])
            self.records.append({"id": "manual-001", "content": arguments["content"]})
            return {"memory": self.records[-1], "ok": True}

    QApplication = qtwidgets.QApplication
    app = QApplication.instance() or QApplication([])
    memory_store = MemoryStoreStub()
    monkeypatch.setattr(settings_dialog_module.QMessageBox, "information", lambda *_args, **_kwargs: None)
    dialog = SettingsDialog(
        api_settings=ApiSettings(
            base_url="https://api.example.com/v1",
            api_key="test-key",
            model="test-model",
        ),
        tts_settings=_minimal_tts_settings(),
        base_dir=Path("."),
        proactive_care_settings=ProactiveCareSettings(screen_context_enabled=True),
        mcp_settings=MCPRuntimeSettings(windows_enabled=False),
        memory_store=memory_store,  # type: ignore[arg-type]
    )
    _open_settings_memory_tab(dialog, qtwidgets, app)
    assert _process_events_until(app, lambda: dialog._memory_list_thread is None)
    assert not dialog.memory_editor_container.isVisible()

    dialog._all_memories = [{"id": "existing-001", "content": "已有记忆"}]
    dialog._refresh_memory_table()
    dialog._set_memory_checked(0, True)
    dialog.memory_new_button.setChecked(True)
    dialog.memory_content_edit.setPlainText("手动新增的长期记忆")
    dialog._save_memory_entry()

    assert "existing-001" not in dialog._selected_memory_ids
    assert memory_store.created == ["手动新增的长期记忆"]
    assert not dialog.memory_new_button.isChecked()
    assert dialog.memory_content_edit.toPlainText() == ""
    assert _process_events_until(app, lambda: dialog._memory_list_thread is None)
    dialog.deleteLater()
    app.processEvents()


def test_show_settings_does_not_save_or_reload_api_when_unchanged(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    import app.ui.pet_window as pet_window_module
    from app.ui.pet_window import PetWindow

    api_settings = ApiSettings("https://api.example.com/v1", "test-key", "test-model")
    tts_settings = _minimal_tts_settings()
    calls: dict[str, int] = {"save_api": 0, "update_api": 0, "reload_memory": 0}

    class SettingsServiceStub:
        def load_tts_settings(self, **_kwargs):  # type: ignore[no-untyped-def]
            return tts_settings

        def save_api_settings(self, _settings):  # type: ignore[no-untyped-def]
            calls["save_api"] += 1

        def save_tts_settings(self, _settings):  # type: ignore[no-untyped-def]
            pass

        def save_current_character_id(self, *_args):  # type: ignore[no-untyped-def]
            pass

        def save_proactive_care_settings(self, _settings):  # type: ignore[no-untyped-def]
            pass

        def save_mcp_runtime_settings(self, _settings):  # type: ignore[no-untyped-def]
            pass

        def save_debug_log_settings(self, _settings):  # type: ignore[no-untyped-def]
            pass

        def save_bubble_settings(self, _settings):  # type: ignore[no-untyped-def]
            pass

        def save_system_values(self, *_args):  # type: ignore[no-untyped-def]
            pass

    class ApiClientStub:
        settings = api_settings

        def update_settings(self, _settings):  # type: ignore[no-untyped-def]
            calls["update_api"] += 1

    class MemoryStoreStub:
        def reload_api_settings(self, *_args, **_kwargs):  # type: ignore[no-untyped-def]
            calls["reload_memory"] += 1

    class DialogStub(_NonModalSettingsDialogStub):
        def __init__(self, *_args, **_kwargs) -> None:
            super().__init__()
            self.result_api_settings = api_settings
            self.result_tts_settings = tts_settings
            self.result_character_id = "sakura"
            self.result_proactive_care_settings = ProactiveCareSettings(screen_context_enabled=True)
            self.result_mcp_settings = MCPRuntimeSettings(windows_enabled=False)
            self.result_debug_log_settings = DebugLogSettings()
            self.result_portrait_scale_percent = 100

    window = _minimal_settings_window(
        PetWindow,
        SettingsServiceStub(),
        ApiClientStub(),
        MemoryStoreStub(),
    )
    monkeypatch.setattr(pet_window_module, "SettingsDialog", DialogStub)
    monkeypatch.setattr(pet_window_module, "show_themed_information", lambda *_args, **_kwargs: None)

    window.show_settings()

    assert calls == {"save_api": 0, "update_api": 0, "reload_memory": 0}


def test_show_settings_applies_launch_at_login_change(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    import app.ui.pet_window as pet_window_module
    from app.ui.pet_window import PetWindow

    api_settings = ApiSettings("https://api.example.com/v1", "test-key", "test-model")
    tts_settings = _minimal_tts_settings()
    saved_startup_settings: list[StartupSettings] = []
    applied: list[tuple[Path, bool]] = []

    class SettingsServiceStub:
        def load_tts_settings(self, **_kwargs):  # type: ignore[no-untyped-def]
            return tts_settings

        def save_api_settings(self, _settings):  # type: ignore[no-untyped-def]
            pass

        def save_tts_settings(self, _settings):  # type: ignore[no-untyped-def]
            pass

        def save_current_character_id(self, *_args):  # type: ignore[no-untyped-def]
            pass

        def save_proactive_care_settings(self, _settings):  # type: ignore[no-untyped-def]
            pass

        def save_mcp_runtime_settings(self, _settings):  # type: ignore[no-untyped-def]
            pass

        def save_debug_log_settings(self, _settings):  # type: ignore[no-untyped-def]
            pass

        def save_bubble_settings(self, _settings):  # type: ignore[no-untyped-def]
            pass

        def save_startup_settings(self, settings):  # type: ignore[no-untyped-def]
            saved_startup_settings.append(settings)

        def save_system_values(self, *_args):  # type: ignore[no-untyped-def]
            pass

    class ApiClientStub:
        settings = api_settings

        def update_settings(self, _settings):  # type: ignore[no-untyped-def]
            pass

    class MemoryStoreStub:
        def reload_api_settings(self, *_args, **_kwargs):  # type: ignore[no-untyped-def]
            pass

    class DialogStub(_NonModalSettingsDialogStub):
        def __init__(self, *_args, **_kwargs) -> None:
            super().__init__()
            self.result_api_settings = api_settings
            self.result_tts_settings = tts_settings
            self.result_character_id = "sakura"
            self.result_proactive_care_settings = ProactiveCareSettings(screen_context_enabled=True)
            self.result_mcp_settings = MCPRuntimeSettings(windows_enabled=False)
            self.result_debug_log_settings = DebugLogSettings()
            self.result_startup_settings = StartupSettings(launch_at_login=True)
            self.result_portrait_scale_percent = 100

    window = _minimal_settings_window(
        PetWindow,
        SettingsServiceStub(),
        ApiClientStub(),
        MemoryStoreStub(),
    )
    window.startup_settings = StartupSettings(launch_at_login=False)
    monkeypatch.setattr(pet_window_module, "SettingsDialog", DialogStub)
    monkeypatch.setattr(
        pet_window_module,
        "set_launch_at_login_enabled",
        lambda base_dir, enabled: applied.append((base_dir, enabled)),
    )
    monkeypatch.setattr(pet_window_module, "show_themed_information", lambda *_args, **_kwargs: None)

    window.show_settings()

    assert applied == [(Path("."), True)]
    assert saved_startup_settings == [StartupSettings(launch_at_login=True)]
    assert window.startup_settings == StartupSettings(launch_at_login=True)


def test_show_settings_reuses_active_dialog_from_tray(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    import app.ui.pet_window as pet_window_module
    from app.ui.pet_window import PetWindow

    api_settings = ApiSettings("https://api.example.com/v1", "test-key", "test-model")
    tts_settings = _minimal_tts_settings()
    events: list[str] = []

    class SettingsServiceStub:
        def load_tts_settings(self, **_kwargs):  # type: ignore[no-untyped-def]
            events.append("load_tts")
            return tts_settings

    class ApiClientStub:
        settings = api_settings

    class MemoryStoreStub:
        pass

    class DialogStub:
        def __init__(self, *_args, **_kwargs) -> None:
            events.append("dialog_init")
            self.finished = _NonModalSettingsDialogStub._FinishedSignal()

        def show(self) -> None:
            # 非模态：show 后窗口保持打开，不触发 finished（不清空 settings_dialog）。
            events.append("show")

        def raise_(self) -> None:
            events.append("raise")

        def activateWindow(self) -> None:
            events.append("activate")

    window = _minimal_settings_window(
        PetWindow,
        SettingsServiceStub(),
        ApiClientStub(),
        MemoryStoreStub(),
    )
    monkeypatch.setattr(pet_window_module, "SettingsDialog", DialogStub)

    # 第一次打开：非模态创建并显示窗口。
    window.show_settings()
    # 第二次打开（窗口仍开着）：应激活已有窗口，而不是再建一个。
    window.show_settings()

    assert events == [
        "load_tts",
        "dialog_init",
        "show",
        "raise",
        "activate",
        "show",
        "raise",
        "activate",
    ]
    # 窗口仍打开（非模态未关闭），settings_dialog 持有引用。
    assert getattr(window, "settings_dialog", None) is not None


def test_show_settings_saves_and_applies_subtitle_display_speed(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    import app.ui.pet_window as pet_window_module
    from app.ui.pet_window import PetWindow

    api_settings = ApiSettings("https://api.example.com/v1", "test-key", "test-model")
    tts_settings = _minimal_tts_settings()
    saved_ui_values: dict[str, object] = {}

    class SettingsServiceStub:
        def load_tts_settings(self, **_kwargs):  # type: ignore[no-untyped-def]
            return tts_settings

        def save_api_settings(self, _settings):  # type: ignore[no-untyped-def]
            pass

        def save_tts_settings(self, _settings):  # type: ignore[no-untyped-def]
            pass

        def save_current_character_id(self, *_args):  # type: ignore[no-untyped-def]
            pass

        def save_proactive_care_settings(self, _settings):  # type: ignore[no-untyped-def]
            pass

        def save_mcp_runtime_settings(self, _settings):  # type: ignore[no-untyped-def]
            pass

        def save_debug_log_settings(self, _settings):  # type: ignore[no-untyped-def]
            pass

        def save_bubble_settings(self, _settings):  # type: ignore[no-untyped-def]
            pass

        def save_system_values(self, section, values):  # type: ignore[no-untyped-def]
            assert section == "ui"
            saved_ui_values.update(values)

    class ApiClientStub:
        settings = api_settings

        def update_settings(self, _settings):  # type: ignore[no-untyped-def]
            pass

    class MemoryStoreStub:
        def reload_api_settings(self, *_args, **_kwargs):  # type: ignore[no-untyped-def]
            pass

    class DialogStub(_NonModalSettingsDialogStub):
        def __init__(self, *_args, **_kwargs) -> None:
            super().__init__()
            self.result_api_settings = api_settings
            self.result_tts_settings = tts_settings
            self.result_character_id = "sakura"
            self.result_proactive_care_settings = ProactiveCareSettings(screen_context_enabled=True)
            self.result_mcp_settings = MCPRuntimeSettings(windows_enabled=False)
            self.result_debug_log_settings = DebugLogSettings()
            self.result_portrait_scale_percent = 100
            self.result_subtitle_typing_interval_ms = 80
            self.result_reply_segment_pause_ms = 900

    window = _minimal_settings_window(
        PetWindow,
        SettingsServiceStub(),
        ApiClientStub(),
        MemoryStoreStub(),
    )
    monkeypatch.setattr(pet_window_module, "SettingsDialog", DialogStub)
    monkeypatch.setattr(pet_window_module, "show_themed_information", lambda *_args, **_kwargs: None)

    window.show_settings()

    assert saved_ui_values == {
        "portrait_scale_percent": 100,
        "subtitle_typing_interval_ms": 80,
        "reply_segment_pause_ms": 900,
    }
    assert window.subtitle_typing_interval_ms == 80
    assert window.reply_segment_pause_ms == 900
    assert window.subtitle_controller.display_speeds == [(80, 900)]


def test_history_clear_keeps_history_when_memory_curation_returns_nothing(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    import app.ui.pet_window as pet_window_module
    from app.agent.memory_curator import MemoryCurationResult
    from app.ui.pet_window import PetWindow

    warnings: list[tuple[str, str]] = []

    class HistoryStoreStub:
        def __init__(self) -> None:
            self.clear_calls = 0

        def clear(self) -> None:
            self.clear_calls += 1

    class MemoryCurationStateStub:
        def __init__(self) -> None:
            self.cleared = False

        def mark_history_cleared(self) -> None:
            self.cleared = True

    class WindowStub:
        _handle_memory_curation_finished = PetWindow._handle_memory_curation_finished

    window = WindowStub()
    window.memory_curation_mode = "history_clear"
    window.memory_curation_target_history_count = 3
    window.memory_curation_consumed_turns = 0
    window.history_store = HistoryStoreStub()
    window.memory_curation_state = MemoryCurationStateStub()
    window.history_window = None

    monkeypatch.setattr(
        pet_window_module,
        "show_themed_warning",
        lambda _parent, title, text: warnings.append((title, text)),
    )
    monkeypatch.setattr(pet_window_module, "show_themed_information", lambda *_args, **_kwargs: None)

    window._handle_memory_curation_finished(
        MemoryCurationResult(ignored=3, processed_entries=3, returned=0)
    )

    assert window.history_store.clear_calls == 0
    assert window.memory_curation_state.cleared is False
    assert warnings == [("整理失败", "记忆整理没有写入任何结果，已保留聊天历史。请检查日志后再重试。")]


def test_history_clear_queues_while_auto_memory_curation_is_running(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    import app.ui.pet_window as pet_window_module
    from app.ui.pet_window import PetWindow

    messages: list[tuple[str, str]] = []

    class HistoryWindowStub:
        def __init__(self) -> None:
            self.busy_calls: list[bool] = []

        def set_memory_save_busy(self, busy: bool) -> None:
            self.busy_calls.append(busy)

    class WindowStub:
        _save_history_to_memory_and_clear = PetWindow._save_history_to_memory_and_clear

        def __init__(self) -> None:
            self.memory_curation_thread = object()
            self.memory_curation_mode = "backfill"
            self.pending_history_clear_after_curation = False
            self.history_window = HistoryWindowStub()
            self.worker_thread = None

    monkeypatch.setattr(
        pet_window_module,
        "show_themed_information",
        lambda _parent, title, text, **_kwargs: messages.append((title, text)),
    )

    window = WindowStub()
    window._save_history_to_memory_and_clear()

    assert window.pending_history_clear_after_curation is True
    assert window.history_window.busy_calls == []
    assert messages == [("整理中", "当前正在自动整理记忆，结束后会继续清空并保存历史。")]


def test_queued_history_clear_starts_after_auto_curation_cleanup(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    import app.ui.pet_window as pet_window_module
    from app.ui.pet_window import PetWindow

    timer_calls: list[tuple[object, object]] = []
    monkeypatch.setattr(
        pet_window_module.QTimer,
        "singleShot",
        lambda delay, callback: timer_calls.append((delay, callback)),
    )

    class DisposableStub:
        def deleteLater(self) -> None:
            pass

    class HistoryStoreStub:
        def load(self) -> list[object]:
            return [object()]

    class MemoryStoreStub:
        def is_ready(self) -> bool:
            return True

    class MemoryCurationStateStub:
        def pending_turns(self) -> int:
            return 2

    class HistoryWindowStub:
        def __init__(self) -> None:
            self.busy_calls: list[bool] = []

        def set_memory_save_busy(self, busy: bool) -> None:
            self.busy_calls.append(busy)

    class WindowStub:
        _cleanup_memory_curation_worker = PetWindow._cleanup_memory_curation_worker
        _start_pending_history_clear_after_curation = PetWindow._start_pending_history_clear_after_curation
        _memory_store_ready_for_history_clear = PetWindow._memory_store_ready_for_history_clear
        _reset_memory_curation_cache_for_history_clear = (
            PetWindow._reset_memory_curation_cache_for_history_clear
        )

        def __init__(self) -> None:
            self.memory_curation_worker = DisposableStub()
            self.memory_curation_thread = DisposableStub()
            self.memory_curation_mode = "backfill"
            self.memory_curation_target_history_count = 5
            self.memory_curation_consumed_turns = 0
            self.pending_history_clear_after_curation = True
            self.history_window = HistoryWindowStub()
            self.history_store = HistoryStoreStub()
            self.memory_store = MemoryStoreStub()
            self.memory_curation_state = MemoryCurationStateStub()
            self.start_calls: list[dict[str, object]] = []

        def _memory_curation_can_start(self) -> bool:
            return True

        def _start_memory_curation(self, entries, *, mode, target_history_count, consumed_turns):  # type: ignore[no-untyped-def]
            self.start_calls.append(
                {
                    "entry_count": len(entries),
                    "mode": mode,
                    "target_history_count": target_history_count,
                    "consumed_turns": consumed_turns,
                }
            )
            self.memory_curation_thread = object()

    window = WindowStub()
    window._cleanup_memory_curation_worker()

    assert window.pending_history_clear_after_curation is False
    assert window.start_calls == [
        {
            "entry_count": 1,
            "mode": "history_clear",
            "target_history_count": 1,
            "consumed_turns": 2,
        }
    ]
    assert window.history_window.busy_calls == []
    assert timer_calls == []


def test_history_clear_reports_when_memory_store_is_not_ready(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    import app.ui.pet_window as pet_window_module
    from app.ui.pet_window import PetWindow

    messages: list[tuple[str, str]] = []

    class HistoryStoreStub:
        def load(self) -> list[object]:
            return [object()]

    class MemoryStoreStub:
        def is_ready(self) -> bool:
            return False

    class HistoryWindowStub:
        def __init__(self) -> None:
            self.busy_calls: list[bool] = []

        def set_memory_save_busy(self, busy: bool) -> None:
            self.busy_calls.append(busy)

    class WindowStub:
        _save_history_to_memory_and_clear = PetWindow._save_history_to_memory_and_clear
        _memory_store_ready_for_history_clear = PetWindow._memory_store_ready_for_history_clear
        _show_memory_not_ready_for_history_clear = PetWindow._show_memory_not_ready_for_history_clear

        def __init__(self) -> None:
            self.memory_curation_thread = None
            self.memory_curation_mode = ""
            self.worker_thread = None
            self.history_store = HistoryStoreStub()
            self.memory_store = MemoryStoreStub()
            self.history_window = HistoryWindowStub()
            self.memory_status_last_message = "长期记忆系统正在初始化，首次启动可能需要下载本地嵌入模型，请稍等。"
            self.start_calls = 0

        def _start_memory_curation(self, *_args, **_kwargs) -> None:
            self.start_calls += 1

    monkeypatch.setattr(
        pet_window_module,
        "show_themed_information",
        lambda _parent, title, text, **_kwargs: messages.append((title, text)),
    )

    window = WindowStub()
    window._save_history_to_memory_and_clear()

    assert window.start_calls == 0
    assert window.history_window.busy_calls == [False]
    assert messages == [("记忆初始化中", "长期记忆系统正在初始化，首次启动可能需要下载本地嵌入模型，请稍等。")]


def test_history_clear_resets_mem0_curation_cache_before_start() -> None:  # type: ignore[no-untyped-def]
    from app.ui.pet_window import PetWindow

    events: list[str] = []

    class HistoryStoreStub:
        def load(self) -> list[object]:
            return [object()]

    class MemoryStoreStub:
        def is_ready(self) -> bool:
            return True

        def reset_curation_cache(self) -> dict[str, int]:
            events.append("reset")
            return {"messages": 1, "history": 0}

    class MemoryCurationStateStub:
        def pending_turns(self) -> int:
            return 2

    class WindowStub:
        _save_history_to_memory_and_clear = PetWindow._save_history_to_memory_and_clear
        _memory_store_ready_for_history_clear = PetWindow._memory_store_ready_for_history_clear
        _reset_memory_curation_cache_for_history_clear = (
            PetWindow._reset_memory_curation_cache_for_history_clear
        )

        def __init__(self) -> None:
            self.memory_curation_thread = None
            self.memory_curation_mode = ""
            self.worker_thread = None
            self.history_store = HistoryStoreStub()
            self.memory_store = MemoryStoreStub()
            self.history_window = None
            self.memory_curation_state = MemoryCurationStateStub()

        def _start_memory_curation(self, *_args, **_kwargs) -> None:
            events.append("start")

    window = WindowStub()
    window._save_history_to_memory_and_clear()

    assert events == ["reset", "start"]


def test_show_settings_reloads_memory_in_background_when_api_changes(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    import app.ui.pet_window as pet_window_module
    from app.ui.pet_window import PetWindow

    old_settings = ApiSettings("https://old.example.com/v1", "old-key", "old-model")
    new_settings = ApiSettings("https://new.example.com/v1", "new-key", "new-model")
    tts_settings = _minimal_tts_settings()
    calls: dict[str, object] = {"save_api": 0, "updated": None, "reloaded": None, "wait": None}

    class SettingsServiceStub:
        def load_tts_settings(self, **_kwargs):  # type: ignore[no-untyped-def]
            return tts_settings

        def save_api_settings(self, settings):  # type: ignore[no-untyped-def]
            calls["save_api"] = int(calls["save_api"]) + 1
            calls["saved"] = settings

        def save_tts_settings(self, _settings):  # type: ignore[no-untyped-def]
            pass

        def save_current_character_id(self, *_args):  # type: ignore[no-untyped-def]
            pass

        def save_proactive_care_settings(self, _settings):  # type: ignore[no-untyped-def]
            pass

        def save_mcp_runtime_settings(self, _settings):  # type: ignore[no-untyped-def]
            pass

        def save_debug_log_settings(self, _settings):  # type: ignore[no-untyped-def]
            pass

        def save_bubble_settings(self, _settings):  # type: ignore[no-untyped-def]
            pass

        def save_system_values(self, *_args):  # type: ignore[no-untyped-def]
            pass

    class ApiClientStub:
        settings = old_settings

        def update_settings(self, settings):  # type: ignore[no-untyped-def]
            calls["updated"] = settings
            self.settings = settings

    class MemoryStoreStub:
        def reload_api_settings(self, settings, *, wait=False):  # type: ignore[no-untyped-def]
            calls["reloaded"] = settings
            calls["wait"] = wait

    class DialogStub(_NonModalSettingsDialogStub):
        def __init__(self, *_args, **_kwargs) -> None:
            super().__init__()
            self.result_api_settings = new_settings
            self.result_tts_settings = tts_settings
            self.result_character_id = "sakura"
            self.result_proactive_care_settings = ProactiveCareSettings(screen_context_enabled=True)
            self.result_mcp_settings = MCPRuntimeSettings(windows_enabled=False)
            self.result_debug_log_settings = DebugLogSettings()
            self.result_portrait_scale_percent = 100

    messages: list[str] = []
    window = _minimal_settings_window(
        PetWindow,
        SettingsServiceStub(),
        ApiClientStub(),
        MemoryStoreStub(),
    )
    monkeypatch.setattr(pet_window_module, "SettingsDialog", DialogStub)
    monkeypatch.setattr(
        pet_window_module,
        "show_themed_information",
        lambda _parent, _title, text, **_kwargs: messages.append(str(text)),
    )

    window.show_settings()

    assert calls["save_api"] == 1
    assert calls["saved"] == new_settings
    assert calls["updated"] == new_settings
    assert calls["reloaded"] == new_settings
    assert calls["wait"] is False
    assert "长期记忆系统正在后台刷新 API 配置" in messages[-1]


def test_show_settings_uses_dialog_refreshed_character_registry(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    import app.ui.pet_window as pet_window_module
    from app.ui.pet_window import PetWindow

    api_settings = ApiSettings("https://api.example.com/v1", "test-key", "test-model")
    tts_settings = _minimal_tts_settings()

    class ImportedProfile:
        id = "imported"
        display_name = "Imported"

    imported_profile = ImportedProfile()

    class ImportedRegistry:
        def get(self, character_id: str):  # type: ignore[no-untyped-def]
            assert character_id == "imported"
            return imported_profile

    imported_registry = ImportedRegistry()

    class SettingsServiceStub:
        def load_tts_settings(self, **_kwargs):  # type: ignore[no-untyped-def]
            return tts_settings

        def save_api_settings(self, _settings):  # type: ignore[no-untyped-def]
            pass

        def save_tts_settings(self, _settings):  # type: ignore[no-untyped-def]
            pass

        def save_current_character_id(self, registry, character_id):  # type: ignore[no-untyped-def]
            assert registry is imported_registry
            assert character_id == "imported"

        def save_proactive_care_settings(self, _settings):  # type: ignore[no-untyped-def]
            pass

        def save_mcp_runtime_settings(self, _settings):  # type: ignore[no-untyped-def]
            pass

        def save_debug_log_settings(self, _settings):  # type: ignore[no-untyped-def]
            pass

        def save_bubble_settings(self, _settings):  # type: ignore[no-untyped-def]
            pass

        def save_system_values(self, *_args):  # type: ignore[no-untyped-def]
            pass

    class ApiClientStub:
        settings = api_settings

        def update_settings(self, _settings):  # type: ignore[no-untyped-def]
            pass

    class MemoryStoreStub:
        def reload_api_settings(self, *_args, **_kwargs):  # type: ignore[no-untyped-def]
            pass

    class DialogStub(_NonModalSettingsDialogStub):
        def __init__(self, *_args, **_kwargs) -> None:
            super().__init__()
            self.character_registry = imported_registry
            self.result_api_settings = api_settings
            self.result_tts_settings = tts_settings
            self.result_character_id = "imported"
            self.result_proactive_care_settings = ProactiveCareSettings(screen_context_enabled=True)
            self.result_mcp_settings = MCPRuntimeSettings(windows_enabled=False)
            self.result_debug_log_settings = DebugLogSettings()
            self.result_portrait_scale_percent = 100

    window = _minimal_settings_window(
        PetWindow,
        SettingsServiceStub(),
        ApiClientStub(),
        MemoryStoreStub(),
    )
    monkeypatch.setattr(pet_window_module, "SettingsDialog", DialogStub)
    monkeypatch.setattr(pet_window_module, "show_themed_information", lambda *_args, **_kwargs: None)

    window.show_settings()

    assert window.character_registry is imported_registry
    assert window.character_profile is imported_profile


def test_main_detects_missing_character_packages() -> None:
    import main as sakura_main

    root = _ui_runtime_root("missing_characters")
    assert sakura_main._character_packages_missing(root)
    (root / "characters").mkdir()
    assert sakura_main._character_packages_missing(root)
    character_dir = root / "characters" / "demo"
    character_dir.mkdir()
    (character_dir / "character.json").write_text("{}", encoding="utf-8")
    assert not sakura_main._character_packages_missing(root)


def test_main_selfcheck_runs_before_single_instance_guard(monkeypatch: pytest.MonkeyPatch) -> None:
    import main as sakura_main

    root = _ui_runtime_root("selfcheck_before_lock")
    (root / "data").write_text("not a directory", encoding="utf-8")
    critical_messages: list[str] = []

    class AppStub:
        def __init__(self, _argv):  # type: ignore[no-untyped-def]
            pass

        def setApplicationName(self, _name):  # type: ignore[no-untyped-def]
            pass

        def setQuitOnLastWindowClosed(self, _enabled):  # type: ignore[no-untyped-def]
            pass

    class GuardShouldNotRun:
        def __init__(self, _base_dir):  # type: ignore[no-untyped-def]
            raise AssertionError("SingleInstanceGuard should not run before fatal selfcheck")

    monkeypatch.setattr(sakura_main, "BASE_DIR", root)
    monkeypatch.setattr(sakura_main, "QApplication", AppStub)
    monkeypatch.setattr(sakura_main, "_configure_windows_high_dpi", lambda: None)
    monkeypatch.setattr(sakura_main, "_force_light_palette", lambda _app: None)
    monkeypatch.setattr(sakura_main, "qInstallMessageHandler", lambda _handler: None)
    monkeypatch.setattr(sakura_main, "SingleInstanceGuard", GuardShouldNotRun)
    monkeypatch.setattr(
        sakura_main.QMessageBox,
        "critical",
        lambda _parent, _title, message: critical_messages.append(message),
    )

    assert sakura_main.main() == 1
    assert critical_messages
    assert "目录无法写入" in critical_messages[0]


def test_main_detects_legacy_tts_migration_even_when_tts_disabled() -> None:
    import main as sakura_main

    root = _ui_runtime_root("disabled_tts_migration")
    api_config = root / "data" / "config" / "api.yaml"
    api_config.parent.mkdir(parents=True)
    api_config.write_text(
        """
tts:
  provider: none
  enabled: false
  gpt_sovits:
    api_url: http://127.0.0.1:9880/tts
    work_dir: data/tts_bundles/installed/gpt_sovits_nvidia50/GPT-SoVITS-v2pro-20250604-nvidia50
    ref_lang: ja
    text_lang: ja
""".lstrip(),
        encoding="utf-8",
    )
    runtime_python = (
        root
        / "data"
        / "tts_bundles"
        / "installed"
        / "gpt_sovits_nvidia50"
        / "GPT-SoVITS-v2pro-20250604-nvidia50"
        / "runtime"
        / "python.exe"
    )
    runtime_python.parent.mkdir(parents=True)
    _write_fake_runtime_python(runtime_python)

    migrations = sakura_main._pending_startup_tts_migrations(root)

    assert len(migrations) == 1
    assert migrations[0].target_dir == root / "tts" / "g50"


def test_main_detects_other_legacy_tts_bundle_when_current_provider_is_migrated() -> None:
    import main as sakura_main

    root = _ui_runtime_root("multi_tts_migration")
    api_config = root / "data" / "config" / "api.yaml"
    api_config.parent.mkdir(parents=True)
    api_config.write_text(
        """
tts:
  provider: gpt-sovits
  enabled: true
  gpt_sovits:
    api_url: http://127.0.0.1:9880/tts
    work_dir: tts/g50
    ref_lang: ja
    text_lang: ja
""".lstrip(),
        encoding="utf-8",
    )
    gpt_runtime = root / "tts" / "g50" / "runtime" / "python.exe"
    gpt_runtime.parent.mkdir(parents=True)
    _write_fake_runtime_python(gpt_runtime, "gpt")
    genie_runtime = (
        root
        / "data"
        / "tts_bundles"
        / "installed"
        / "genie_tts_server"
        / "Genie-TTS Server"
        / "runtime"
        / "python.exe"
    )
    genie_runtime.parent.mkdir(parents=True)
    _write_fake_runtime_python(genie_runtime, "genie")

    migrations = sakura_main._pending_startup_tts_migrations(root)

    assert [migration.entry.key for migration in migrations] == ["genie_tts_server"]
    assert migrations[0].target_dir == root / "tts" / "cpu"


def test_tts_migration_dialog_shows_concise_copy_and_progress() -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qtwidgets = pytest.importorskip("PySide6.QtWidgets")
    if not all(hasattr(qtwidgets, name) for name in ("QApplication", "QLabel", "QProgressBar", "QWidget")):
        pytest.skip("当前测试环境只提供了 PySide6 stub。")

    import main as sakura_main
    from app.voice import tts_bundle

    QApplication = qtwidgets.QApplication
    QLabel = qtwidgets.QLabel
    QProgressBar = qtwidgets.QProgressBar
    QWidget = qtwidgets.QWidget
    app = QApplication.instance() or QApplication([])
    parent = QWidget()
    root = _ui_runtime_root("tts_migration_dialog")
    dialog = sakura_main.TTSBundleMigrationDialog(root, parent)  # type: ignore[arg-type]
    progress = tts_bundle.TTSBundleMigrationProgress(
        entry=tts_bundle.GPT_SOVITS_NVIDIA50,
        current_file="runtime/python.exe",
        completed_files=3,
        total_files=6,
        copied_bytes=30,
        total_bytes=60,
    )

    dialog.set_current_item("正在迁移：GPT-SoVITS v2pro NVIDIA 50 系整合包")
    dialog.set_progress(progress)
    labels = [label.text() for label in dialog.findChildren(QLabel)]
    bars = dialog.findChildren(QProgressBar)

    assert any("新版本修复了 Windows 下可能出现的路径过长问题。" in text for text in labels)
    assert any("Sakura 正在努力搬运中" in text for text in labels)
    assert any("正在迁移：GPT-SoVITS v2pro NVIDIA 50 系整合包" in text for text in labels)
    assert any("50%（3/6 个文件）" in text for text in labels)
    assert bars and bars[0].value() == 50
    dialog.deleteLater()
    parent.deleteLater()
    app.processEvents()


def test_tts_migration_dialog_marks_fast_migration_done() -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qtwidgets = pytest.importorskip("PySide6.QtWidgets")
    if not all(hasattr(qtwidgets, name) for name in ("QApplication", "QLabel", "QProgressBar", "QPushButton", "QWidget")):
        pytest.skip("当前测试环境只提供了 PySide6 stub。")

    import main as sakura_main

    QApplication = qtwidgets.QApplication
    QLabel = qtwidgets.QLabel
    QProgressBar = qtwidgets.QProgressBar
    QPushButton = qtwidgets.QPushButton
    QWidget = qtwidgets.QWidget
    app = QApplication.instance() or QApplication([])
    parent = QWidget()
    root = _ui_runtime_root("tts_fast_migration_dialog")
    dialog = sakura_main.TTSBundleMigrationDialog(root, parent)  # type: ignore[arg-type]

    dialog.finish_migration([])
    labels = [label.text() for label in dialog.findChildren(QLabel)]
    bars = dialog.findChildren(QProgressBar)
    buttons = dialog.findChildren(QPushButton)

    assert any("迁移完成，点击确定继续启动" in text for text in labels)
    assert any("100%（迁移完成）" in text for text in labels)
    assert bars and bars[0].value() == 100
    assert buttons and buttons[0].isEnabled()
    assert buttons[0].text() == "确定"
    dialog.deleteLater()
    parent.deleteLater()
    app.processEvents()


def test_main_first_run_settings_saves_imported_character_and_builds_context(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    import main as sakura_main
    from app.config.character_loader import CharacterRegistry

    root = _ui_runtime_root("first_run_settings") / "runtime"
    api_settings = ApiSettings("https://api.example.com/v1", "test-key", "test-model")
    tts_settings = _minimal_tts_settings()

    class DialogStub:
        def __init__(self, *args, **kwargs) -> None:  # type: ignore[no-untyped-def]
            self.base_dir = kwargs["base_dir"]
            _build_settings_dialog_character(self.base_dir, "imported", "Imported")
            self.character_registry = CharacterRegistry(self.base_dir)
            self.result_api_settings = api_settings
            self.result_tts_settings = tts_settings
            self.result_character_id = "imported"
            self.result_proactive_care_settings = ProactiveCareSettings(screen_context_enabled=True)
            self.result_mcp_settings = MCPRuntimeSettings(windows_enabled=False)
            self.result_debug_log_settings = DebugLogSettings(enabled=True, body_enabled=False)
            self.result_startup_settings = StartupSettings()
            self.result_portrait_scale_percent = 125
            self.result_subtitle_typing_interval_ms = 70
            self.result_reply_segment_pause_ms = 800

        def exec(self):  # type: ignore[no-untyped-def]
            return sakura_main.QDialog.DialogCode.Accepted

    monkeypatch.setattr(sakura_main, "SettingsDialog", DialogStub)

    context = sakura_main._open_first_run_settings(root)

    assert context is not None
    assert context.character_profile.id == "imported"
    assert context.character_profile.display_name == "Imported"
    assert (root / "data" / "config" / "characters.yaml").read_text(encoding="utf-8").strip() == (
        "current_character_id: imported"
    )
    assert "portrait_scale_percent: 125" in (
        root / "data" / "config" / "system_config.yaml"
    ).read_text(encoding="utf-8")
    system_config = (root / "data" / "config" / "system_config.yaml").read_text(encoding="utf-8")
    assert "subtitle_typing_interval_ms: 70" in system_config
    assert "reply_segment_pause_ms: 800" in system_config


def test_settings_dialog_returns_portrait_scale_percent() -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qtwidgets = pytest.importorskip("PySide6.QtWidgets")
    if not hasattr(qtwidgets, "QApplication"):
        pytest.skip("当前测试环境只提供了 PySide6 stub。")

    from app.config.character_loader import CharacterProfile
    from app.ui.settings_dialog import SettingsDialog

    class CharacterRegistryStub:
        def __init__(self, profile: CharacterProfile) -> None:
            self.profile = profile

        def all(self) -> list[CharacterProfile]:
            return [self.profile]

        def get(self, character_id: str) -> CharacterProfile:
            assert character_id == self.profile.id
            return self.profile

    QApplication = qtwidgets.QApplication
    app = QApplication.instance() or QApplication([])
    profile = CharacterProfile(
        id="sakura",
        display_name="夜乃桜",
        package_dir=Path("."),
        card_path=Path("card.md"),
        initial_message="hello",
        default_portrait_path=Path("portrait.png"),
    )
    dialog = SettingsDialog(
        api_settings=ApiSettings(
            base_url="https://api.example.com/v1",
            api_key="test-key",
            model="test-model",
        ),
        tts_settings=_minimal_tts_settings(),
        base_dir=Path("."),
        character_registry=CharacterRegistryStub(profile),  # type: ignore[arg-type]
        current_character=profile,
        proactive_care_settings=ProactiveCareSettings(screen_context_enabled=True),
        mcp_settings=MCPRuntimeSettings(windows_enabled=False),
        portrait_scale_percent=100,
    )

    dialog.portrait_scale_spin.setValue(125)
    dialog.accept()

    assert dialog.result_portrait_scale_percent == 125
    assert dialog.portrait_scale_slider.value() == 125
    dialog.deleteLater()
    app.processEvents()


def test_settings_dialog_returns_control_panel_layout() -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qtwidgets = pytest.importorskip("PySide6.QtWidgets")
    if not hasattr(qtwidgets, "QApplication"):
        pytest.skip("当前测试环境只提供了 PySide6 stub。")

    from app.ui.settings_dialog import SettingsDialog

    QApplication = qtwidgets.QApplication
    app = QApplication.instance() or QApplication([])
    root = _ui_runtime_root("control_panel_layout_dialog")
    dialog = SettingsDialog(
        api_settings=ApiSettings(
            base_url="https://api.example.com/v1",
            api_key="test-key",
            model="test-model",
        ),
        tts_settings=_minimal_tts_settings(),
        base_dir=root,
        **_settings_dialog_character_kwargs(root),
        proactive_care_settings=ProactiveCareSettings(screen_context_enabled=True),
        mcp_settings=MCPRuntimeSettings(windows_enabled=False),
        control_panel_width=600,
        bubble_height=150,
        control_panel_vertical_offset=30,
    )

    # 构造时把当前值回填到控件
    assert dialog.control_panel_width_spin.value() == 600
    assert dialog.bubble_height_spin.value() == 150
    assert dialog.control_panel_offset_spin.value() == 30

    # 修改后 accept 回收归一化结果
    dialog.control_panel_width_spin.setValue(720)
    dialog.bubble_height_spin.setValue(200)
    dialog.control_panel_offset_spin.setValue(-40)
    dialog.accept()

    assert dialog.result_control_panel_width == 720
    assert dialog.result_bubble_height == 200
    assert dialog.result_control_panel_vertical_offset == -40
    dialog.deleteLater()
    app.processEvents()


def test_settings_dialog_emits_control_panel_layout_preview() -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qtwidgets = pytest.importorskip("PySide6.QtWidgets")
    if not hasattr(qtwidgets, "QApplication"):
        pytest.skip("当前测试环境只提供了 PySide6 stub。")

    from app.ui.settings_dialog import SettingsDialog

    QApplication = qtwidgets.QApplication
    app = QApplication.instance() or QApplication([])
    root = _ui_runtime_root("control_panel_preview_dialog")
    previews: list[tuple[int, int, int, int, int]] = []
    dialog = SettingsDialog(
        api_settings=ApiSettings(
            base_url="https://api.example.com/v1",
            api_key="test-key",
            model="test-model",
        ),
        tts_settings=_minimal_tts_settings(),
        base_dir=root,
        **_settings_dialog_character_kwargs(root),
        proactive_care_settings=ProactiveCareSettings(screen_context_enabled=True),
        mcp_settings=MCPRuntimeSettings(windows_enabled=False),
        control_panel_width=600,
        bubble_height=150,
        control_panel_vertical_offset=0,
        on_layout_preview=lambda p, w, h, o, i: previews.append((p, w, h, o, i)),
    )

    # 构造时的初始 setValue 不应触发预览（信号连接在赋值之后）
    assert previews == []

    # 修改某个滑块即实时触发预览，参数为当前三项取值
    dialog.bubble_height_spin.setValue(180)
    assert previews
    assert previews[-1] == (100, 600, 180, 0, 0)

    dialog.control_panel_width_spin.setValue(720)
    assert previews[-1] == (100, 720, 180, 0, 0)

    # 立绘缩放也接入实时预览
    dialog.portrait_scale_spin.setValue(120)
    assert previews[-1] == (120, 720, 180, 0, 0)

    # 输入框下移也接入实时预览
    dialog.input_bar_offset_spin.setValue(40)
    assert previews[-1] == (120, 720, 180, 0, 40)

    dialog.deleteLater()
    app.processEvents()


def test_settings_dialog_returns_subtitle_display_speed() -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qtwidgets = pytest.importorskip("PySide6.QtWidgets")
    if not hasattr(qtwidgets, "QApplication"):
        pytest.skip("当前测试环境只提供了 PySide6 stub。")

    from app.ui.settings_dialog import SettingsDialog

    QApplication = qtwidgets.QApplication
    app = QApplication.instance() or QApplication([])
    root = _ui_runtime_root("subtitle_display_speed_dialog")
    dialog = SettingsDialog(
        api_settings=ApiSettings(
            base_url="https://api.example.com/v1",
            api_key="test-key",
            model="test-model",
        ),
        tts_settings=_minimal_tts_settings(),
        base_dir=root,
        **_settings_dialog_character_kwargs(root),
        proactive_care_settings=ProactiveCareSettings(screen_context_enabled=True),
        mcp_settings=MCPRuntimeSettings(windows_enabled=False),
        subtitle_typing_interval_ms=60,
        reply_segment_pause_ms=500,
    )

    assert dialog.subtitle_typing_interval_spin.value() == 60
    assert dialog.reply_segment_pause_spin.value() == 500

    dialog.subtitle_typing_interval_spin.setValue(90)
    dialog.reply_segment_pause_spin.setValue(1200)
    dialog.accept()

    assert dialog.result_subtitle_typing_interval_ms == 90
    assert dialog.result_reply_segment_pause_ms == 1200
    dialog.deleteLater()
    app.processEvents()


def test_settings_dialog_returns_launch_at_login_setting(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qtwidgets = pytest.importorskip("PySide6.QtWidgets")
    if not hasattr(qtwidgets, "QApplication"):
        pytest.skip("当前测试环境只提供了 PySide6 stub。")

    import app.ui.settings_dialog as settings_dialog_module
    from app.ui.settings_dialog import SettingsDialog

    monkeypatch.setattr(settings_dialog_module, "is_launch_at_login_supported", lambda: True)
    QApplication = qtwidgets.QApplication
    app = QApplication.instance() or QApplication([])
    root = _ui_runtime_root("launch_at_login_dialog")
    dialog = SettingsDialog(
        api_settings=ApiSettings(
            base_url="https://api.example.com/v1",
            api_key="test-key",
            model="test-model",
        ),
        tts_settings=_minimal_tts_settings(),
        base_dir=root,
        **_settings_dialog_character_kwargs(root),
        proactive_care_settings=ProactiveCareSettings(screen_context_enabled=True),
        mcp_settings=MCPRuntimeSettings(windows_enabled=False),
        startup_settings=StartupSettings(launch_at_login=True),
    )

    assert dialog.launch_at_login_check.isChecked()

    dialog.launch_at_login_check.setChecked(False)
    dialog.accept()

    assert dialog.result_startup_settings == StartupSettings(launch_at_login=False)
    dialog.deleteLater()
    app.processEvents()


def _theme_settings(*, ai_enabled: bool = False) -> ThemeSettings:
    return ThemeSettings(
        primary_color="#112233",
        primary_hover_color="#223344",
        accent_color="#445566",
        text_color="#070809",
        secondary_text_color="#111213",
        muted_text_color="#141516",
        page_background_color="#f1f2f3",
        panel_background_color="#e1e2e3",
        input_background_color="#ffffff",
        bubble_background_color="#d1d2d3",
        border_color="#c1c2c3",
        ai_enabled=ai_enabled,
    )


def _theme_json() -> str:
    theme = _theme_settings()
    return json.dumps(
        {
            "primary_color": theme.primary_color,
            "primary_hover_color": theme.primary_hover_color,
            "accent_color": theme.accent_color,
            "text_color": theme.text_color,
            "secondary_text_color": theme.secondary_text_color,
            "muted_text_color": theme.muted_text_color,
            "page_background_color": theme.page_background_color,
            "panel_background_color": theme.panel_background_color,
            "input_background_color": theme.input_background_color,
            "bubble_background_color": theme.bubble_background_color,
            "border_color": theme.border_color,
        }
    )


def test_settings_dialog_returns_theme_settings() -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qtwidgets = pytest.importorskip("PySide6.QtWidgets")
    if not hasattr(qtwidgets, "QApplication"):
        pytest.skip("当前测试环境只提供了 PySide6 stub。")

    from app.ui.settings_dialog import SettingsDialog

    QApplication = qtwidgets.QApplication
    app = QApplication.instance() or QApplication([])
    root = _ui_runtime_root("theme_settings_dialog")
    dialog = SettingsDialog(
        api_settings=ApiSettings(
            base_url="https://api.example.com/v1",
            api_key="test-key",
            model="test-model",
        ),
        tts_settings=_minimal_tts_settings(),
        base_dir=root,
        **_settings_dialog_character_kwargs(root),
        proactive_care_settings=ProactiveCareSettings(screen_context_enabled=True),
        mcp_settings=MCPRuntimeSettings(windows_enabled=False),
        theme_settings=ThemeSettings(
            primary_color="#112233",
            primary_hover_color="#223344",
            accent_color="#445566",
            text_color="#070809",
            ai_enabled=False,
        ),
    )

    dialog.theme_primary_edit.setText("#223344")
    dialog.theme_accent_edit.setText("#556677")
    dialog.theme_text_edit.setText("#111111")
    dialog.theme_color_edits["primary_hover_color"].setText("#334455")
    dialog.theme_color_edits["secondary_text_color"].setText("#222222")
    dialog.theme_color_edits["muted_text_color"].setText("#333333")
    dialog.theme_color_edits["page_background_color"].setText("#f8f8f8")
    dialog.theme_color_edits["panel_background_color"].setText("#eeeeee")
    dialog.theme_color_edits["input_background_color"].setText("#ffffff")
    dialog.theme_color_edits["bubble_background_color"].setText("#dddddd")
    dialog.theme_color_edits["border_color"].setText("#cccccc")
    dialog.accept()

    assert dialog.result_theme_settings == ThemeSettings(
        primary_color="#223344",
        primary_hover_color="#334455",
        accent_color="#556677",
        text_color="#111111",
        secondary_text_color="#222222",
        muted_text_color="#333333",
        page_background_color="#f8f8f8",
        panel_background_color="#eeeeee",
        input_background_color="#ffffff",
        bubble_background_color="#dddddd",
        border_color="#cccccc",
        ai_enabled=False,
    )
    dialog.deleteLater()
    app.processEvents()


def test_settings_dialog_downgrades_saved_windows_acrylic_to_gaussian(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qtwidgets = pytest.importorskip("PySide6.QtWidgets")
    if not hasattr(qtwidgets, "QApplication"):
        pytest.skip("当前测试环境只提供了 PySide6 stub。")

    from app.ui import window_backdrop as window_backdrop_module
    from app.ui.settings_dialog import SettingsDialog
    from app.ui.window_backdrop import VisualEffectMode

    monkeypatch.setattr(window_backdrop_module.sys, "platform", "win32")
    monkeypatch.setattr(window_backdrop_module, "_windows_build", lambda: 22631)

    QApplication = qtwidgets.QApplication
    app = QApplication.instance() or QApplication([])
    root = _ui_runtime_root("theme_settings_windows_acrylic_downgrade")
    dialog = SettingsDialog(
        api_settings=ApiSettings(
            base_url="https://api.example.com/v1",
            api_key="test-key",
            model="test-model",
        ),
        tts_settings=_minimal_tts_settings(),
        base_dir=root,
        **_settings_dialog_character_kwargs(root),
        proactive_care_settings=ProactiveCareSettings(screen_context_enabled=True),
        mcp_settings=MCPRuntimeSettings(windows_enabled=False),
        theme_settings=ThemeSettings(visual_effect_mode=VisualEffectMode.WINDOWS_ACRYLIC),
    )

    labels = [
        dialog.theme_visual_effect_combo.itemText(index)
        for index in range(dialog.theme_visual_effect_combo.count())
    ]
    assert "Windows 亚克力模糊" not in labels
    assert dialog.theme_visual_effect_combo.currentData() == VisualEffectMode.GAUSSIAN_BLUR

    dialog.accept()

    assert dialog.result_theme_settings is not None
    assert dialog.result_theme_settings.visual_effect_mode == VisualEffectMode.GAUSSIAN_BLUR

    dialog.deleteLater()
    app.processEvents()


def test_settings_dialog_character_selection_loads_character_theme() -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qtwidgets = pytest.importorskip("PySide6.QtWidgets")
    if not hasattr(qtwidgets, "QApplication"):
        pytest.skip("当前测试环境只提供了 PySide6 stub。")

    from app.config.character_loader import CharacterRegistry
    from app.ui.settings_dialog import SettingsDialog

    root = _ui_runtime_root("character_theme_switch")
    sakura = _build_settings_dialog_character(
        root,
        "sakura",
        "Sakura",
        theme={"primary_color": "#102030", "accent_color": "#405060", "source": "package"},
    )
    _build_settings_dialog_character(
        root,
        "nanami",
        "Nanami",
        theme={"primary_color": "#abcdef", "accent_color": "#123456", "source": "package"},
    )

    QApplication = qtwidgets.QApplication
    app = QApplication.instance() or QApplication([])
    dialog = SettingsDialog(
        api_settings=ApiSettings(
            base_url="https://api.example.com/v1",
            api_key="test-key",
            model="test-model",
        ),
        tts_settings=_minimal_tts_settings(),
        base_dir=root,
        character_registry=CharacterRegistry(root),
        current_character=sakura,
        proactive_care_settings=ProactiveCareSettings(screen_context_enabled=True),
        mcp_settings=MCPRuntimeSettings(windows_enabled=False),
        theme_settings=ThemeSettings(primary_color="#999999"),
    )

    assert dialog.theme_primary_edit.text() == "#102030"
    assert dialog.theme_accent_edit.text() == "#405060"

    target_index = dialog.character_combo.findData("nanami")
    assert target_index >= 0
    dialog.character_combo.setCurrentIndex(target_index)
    app.processEvents()
    dialog.accept()

    assert dialog.theme_primary_edit.text() == "#abcdef"
    assert dialog.theme_accent_edit.text() == "#123456"
    assert dialog.result_theme_write_mode == "character"

    dialog.deleteLater()
    app.processEvents()


def test_settings_dialog_resets_default_theme_colors() -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qtwidgets = pytest.importorskip("PySide6.QtWidgets")
    if not hasattr(qtwidgets, "QApplication"):
        pytest.skip("当前测试环境只提供了 PySide6 stub。")

    from app.ui.settings_dialog import SettingsDialog

    QApplication = qtwidgets.QApplication
    app = QApplication.instance() or QApplication([])
    root = _ui_runtime_root("theme_reset_dialog")
    dialog = SettingsDialog(
        api_settings=ApiSettings(
            base_url="https://api.example.com/v1",
            api_key="test-key",
            model="test-model",
        ),
        tts_settings=_minimal_tts_settings(),
        base_dir=root,
        **_settings_dialog_character_kwargs(root),
        proactive_care_settings=ProactiveCareSettings(screen_context_enabled=True),
        mcp_settings=MCPRuntimeSettings(windows_enabled=False),
        theme_settings=ThemeSettings(
            primary_color="#112233",
            primary_hover_color="#223344",
            accent_color="#445566",
            text_color="#070809",
            ai_enabled=True,
        ),
    )

    dialog._reset_theme_colors()

    assert dialog.theme_primary_edit.text() == DEFAULT_THEME_SETTINGS.primary_color
    assert dialog.theme_accent_edit.text() == DEFAULT_THEME_SETTINGS.accent_color
    assert dialog.theme_text_edit.text() == DEFAULT_THEME_SETTINGS.text_color
    assert dialog.theme_color_edits["input_background_color"].text() == DEFAULT_THEME_SETTINGS.input_background_color
    assert dialog._selected_theme_settings() == ThemeSettings(ai_enabled=False).normalized()
    dialog.deleteLater()
    app.processEvents()


def test_settings_dialog_resets_to_character_package_theme() -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qtwidgets = pytest.importorskip("PySide6.QtWidgets")
    if not hasattr(qtwidgets, "QApplication"):
        pytest.skip("当前测试环境只提供了 PySide6 stub。")

    from app.config.character_loader import CharacterRegistry
    from app.ui.settings_dialog import SettingsDialog

    root = _ui_runtime_root("theme_reset_character_package")
    profile = _build_settings_dialog_character(
        root,
        "sakura",
        "Sakura",
        theme={"primary_color": "#102030", "accent_color": "#405060", "source": "package"},
    )

    QApplication = qtwidgets.QApplication
    app = QApplication.instance() or QApplication([])
    dialog = SettingsDialog(
        api_settings=ApiSettings(
            base_url="https://api.example.com/v1",
            api_key="test-key",
            model="test-model",
        ),
        tts_settings=_minimal_tts_settings(),
        base_dir=root,
        character_registry=CharacterRegistry(root),
        current_character=profile,
        proactive_care_settings=ProactiveCareSettings(screen_context_enabled=True),
        mcp_settings=MCPRuntimeSettings(windows_enabled=False),
        theme_settings=ThemeSettings(primary_color="#999999", accent_color="#888888", ai_enabled=True),
    )

    dialog._reset_theme_colors()

    assert dialog.theme_primary_edit.text() == "#102030"
    assert dialog.theme_accent_edit.text() == "#405060"
    assert dialog._selected_theme_settings() == profile.theme_settings

    dialog.deleteLater()
    app.processEvents()


def test_theme_write_rule_persists_manual_and_ai_theme() -> None:
    from app.config.character_loader import (
        THEME_SOURCE_COMPAT_DEFAULT,
        THEME_SOURCE_PACKAGE,
        CharacterProfile,
    )
    from app.ui.pet_window import _should_write_character_theme

    root = _ui_runtime_root("theme_write_rule")
    profile = CharacterProfile(
        id="demo",
        display_name="Demo",
        package_dir=root,
        card_path=root / "card.md",
        initial_message="hello",
        default_portrait_path=root / "portrait.png",
        theme_settings=DEFAULT_THEME_SETTINGS,
        theme_source=THEME_SOURCE_PACKAGE,
    )
    compat_profile = CharacterProfile(
        id="legacy",
        display_name="Legacy",
        package_dir=root,
        card_path=root / "card.md",
        initial_message="hello",
        default_portrait_path=root / "portrait.png",
        theme_settings=DEFAULT_THEME_SETTINGS,
        theme_source=THEME_SOURCE_COMPAT_DEFAULT,
    )

    assert _should_write_character_theme("ai", profile)
    assert _should_write_character_theme("ai", compat_profile)
    assert _should_write_character_theme("manual", profile)
    assert not _should_write_character_theme("reset", profile)
    assert not _should_write_character_theme("character", profile)


def test_theme_ai_worker_sends_portrait_image_and_prompt(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    pytest.importorskip("PySide6.QtCore")
    import app.ui.settings_dialog as settings_dialog

    root = _ui_runtime_root("theme_ai_worker")
    profile = _build_settings_dialog_character(root, "sakura", "Sakura")
    calls: dict[str, object] = {}

    class FakeClient:
        def __init__(self, settings: ApiSettings) -> None:
            calls["settings"] = settings

        def complete_raw(self, system_prompt, messages, **kwargs):  # type: ignore[no-untyped-def]
            calls["system_prompt"] = system_prompt
            calls["messages"] = messages
            calls["kwargs"] = kwargs
            return _theme_json()

    import app.ui.settings.workers as settings_workers
    monkeypatch.setattr(settings_workers, "OpenAICompatibleClient", FakeClient)
    events: list[ThemeSettings] = []
    worker = settings_workers.ThemeAiWorker(
        ApiSettings("https://api.example.com/v1", "test-key", "test-model"),
        profile,
        ai_enabled=True,
    )
    worker.succeeded.connect(events.append)
    worker.run()

    assert events == [_theme_settings(ai_enabled=True)]
    assert "只返回 JSON" in str(calls["system_prompt"])
    assert "primary_hover_color" in str(calls["system_prompt"])
    content = calls["messages"][0]["content"]  # type: ignore[index]
    assert content[1]["type"] == "image_url"
    assert content[1]["image_url"]["url"].startswith("data:image/png;base64,")


def test_settings_dialog_ai_theme_success_and_failure_keep_current(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qtcore = pytest.importorskip("PySide6.QtCore")
    qtwidgets = pytest.importorskip("PySide6.QtWidgets")
    if not hasattr(qtwidgets, "QApplication"):
        pytest.skip("当前测试环境只提供了 PySide6 stub。")

    from app.ui.settings_dialog import SettingsDialog

    QApplication = qtwidgets.QApplication
    app = QApplication.instance() or QApplication([])
    root = _ui_runtime_root("theme_ai_dialog")

    class FakeThread(qtcore.QObject):
        started = qtcore.Signal()
        finished = qtcore.Signal()

        def __init__(self, *_args, **_kwargs) -> None:
            super().__init__()

        def start(self) -> None:
            self.started.emit()

        def quit(self) -> None:
            self.finished.emit()

    class SuccessWorker(qtcore.QObject):
        succeeded = qtcore.Signal(object)
        failed = qtcore.Signal(str)
        finished = qtcore.Signal()

        def __init__(self, *_args, **_kwargs) -> None:
            super().__init__()

        def moveToThread(self, _thread) -> None:  # type: ignore[no-untyped-def]
            pass

        def run(self) -> None:
            self.succeeded.emit(_theme_settings(ai_enabled=True))
            self.finished.emit()

    class FailedWorker(SuccessWorker):
        def run(self) -> None:
            self.failed.emit("vision unsupported")
            self.finished.emit()

    dialog = SettingsDialog(
        api_settings=ApiSettings(
            base_url="https://api.example.com/v1",
            api_key="test-key",
            model="test-model",
        ),
        tts_settings=_minimal_tts_settings(),
        base_dir=root,
        **_settings_dialog_character_kwargs(root),
        proactive_care_settings=ProactiveCareSettings(screen_context_enabled=True),
        mcp_settings=MCPRuntimeSettings(windows_enabled=False),
        theme_settings=ThemeSettings(
            primary_color="#aabbcc",
            primary_hover_color="#aa99bb",
            accent_color="#bbccdd",
            text_color="#111111",
            secondary_text_color="#222222",
            muted_text_color="#333333",
            page_background_color="#f8f8f8",
            panel_background_color="#eeeeee",
            input_background_color="#ffffff",
            bubble_background_color="#dddddd",
            border_color="#cccccc",
            ai_enabled=True,
        ),
    )
    monkeypatch.setattr("app.ui.settings_dialog.QThread", FakeThread)
    monkeypatch.setattr("app.ui.settings.workers.ThemeAiWorker", SuccessWorker)

    dialog._generate_ai_theme()

    assert dialog.theme_primary_edit.text() == "#112233"
    assert dialog.theme_accent_edit.text() == "#445566"
    assert dialog.theme_text_edit.text() == "#070809"

    monkeypatch.setattr("app.ui.settings.workers.ThemeAiWorker", FailedWorker)
    dialog.theme_primary_edit.setText("#aabbcc")
    dialog.theme_accent_edit.setText("#bbccdd")
    dialog.theme_text_edit.setText("#111111")
    dialog._generate_ai_theme()

    assert dialog.theme_primary_edit.text() == "#aabbcc"
    assert dialog.theme_accent_edit.text() == "#bbccdd"
    assert dialog.theme_text_edit.text() == "#111111"
    assert "保留当前配色" in dialog.theme_status_label.text()
    dialog.deleteLater()
    app.processEvents()


def test_proactive_care_batches_screenshots_until_cooldown(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    import app.ui.pet_window as pet_window_module

    current_time = {"value": 0.0}
    captures: list[str] = []
    events = []
    history = []
    window = _build_minimal_proactive_window(
        screen_context_enabled=True,
        check_interval_minutes=1,
        cooldown_minutes=2,
        events=events,
        history=history,
    )

    observations: list[ScreenObservation] = []

    def fake_capture(_window):  # type: ignore[no-untyped-def]
        index = len(captures) + 1
        data_url = f"data:image/jpeg;base64,{index}"
        captures.append(data_url)
        observation = ScreenObservation(
            data_url=data_url,
            width=800,
            height=600,
            captured_at=f"2026-05-30T12:0{index}:00+08:00",
            screen_name="DISPLAY1",
        )
        observations.append(observation)
        return object()

    monkeypatch.setattr(pet_window_module.time, "perf_counter", lambda: current_time["value"])
    monkeypatch.setattr(pet_window_module, "capture_screen_image", fake_capture)
    window._start_screen_observation_encode = lambda _captured, context: (
        window._finish_proactive_screen_context(context, observations[-1]) or True
    )

    current_time["value"] = 60
    window._check_proactive_care()
    assert captures == ["data:image/jpeg;base64,1"]
    assert events == []

    current_time["value"] = 120
    window._check_proactive_care()
    assert captures == ["data:image/jpeg;base64,1", "data:image/jpeg;base64,2"]
    assert events == []

    current_time["value"] = 180
    window._check_proactive_care()

    assert captures == [
        "data:image/jpeg;base64,1",
        "data:image/jpeg;base64,2",
        "data:image/jpeg;base64,3",
    ]
    assert len(events) == 1
    assert [context["data_url"] for context in events[0].payload["screen_contexts"]] == captures
    assert events[0].payload["screen_context_count"] == 3
    assert history
    assert window.proactive_screen_contexts == []


def test_screen_awareness_batches_screenshots_until_cooldown(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    import app.ui.pet_window as pet_window_module

    current_time = {"value": 0.0}
    captures: list[str] = []
    events = []
    history = []
    window = _build_minimal_screen_awareness_window(
        screen_context_enabled=True,
        check_interval_minutes=1,
        cooldown_minutes=2,
        events=events,
        history=history,
    )

    observations: list[ScreenObservation] = []

    def fake_capture(_window):  # type: ignore[no-untyped-def]
        index = len(captures) + 1
        data_url = f"data:image/jpeg;base64,{index}"
        captures.append(data_url)
        observation = ScreenObservation(
            data_url=data_url,
            width=800,
            height=600,
            captured_at=f"2026-05-30T12:0{index}:00+08:00",
            screen_name="DISPLAY1",
        )
        observations.append(observation)
        return object()

    monkeypatch.setattr(pet_window_module.time, "perf_counter", lambda: current_time["value"])
    monkeypatch.setattr(pet_window_module, "capture_screen_image", fake_capture)
    window._start_screen_observation_encode = lambda _captured, context: (
        window._finish_screen_awareness_context(context, observations[-1]) or True
    )

    current_time["value"] = 60
    window._check_screen_awareness()
    assert captures == ["data:image/jpeg;base64,1"]
    assert events == []

    current_time["value"] = 120
    window._check_screen_awareness()
    assert captures == ["data:image/jpeg;base64,1", "data:image/jpeg;base64,2"]
    assert events == []

    current_time["value"] = 180
    window._check_screen_awareness()

    assert events[0].type == "screen_awareness_check"
    assert [context["data_url"] for context in events[0].payload["screen_contexts"]] == captures
    assert events[0].payload["screen_context_count"] == 3
    assert history
    assert window.screen_awareness_contexts == []


def test_proactive_care_event_includes_recent_conversation() -> None:
    from app.agent.proactive_care import PROACTIVE_SCREEN_CONTEXT_HISTORY_MARKER
    from app.ui.pet_window import PROACTIVE_RECENT_CONVERSATION_SUMMARY_HINT

    window = _build_minimal_proactive_window(
        screen_context_enabled=True,
        check_interval_minutes=1,
        cooldown_minutes=2,
    )
    window.messages = [
        {"role": "system", "content": PROACTIVE_SCREEN_CONTEXT_HISTORY_MARKER},
        {"role": "user", "content": "访问 GitHub 看看 Sakura 内容"},
        {"role": "assistant", "content": "我打开看看。"},
        {"role": "assistant", "content": "稍微休息一下吧。"},
    ]

    event = window._build_proactive_care_event(300.0)

    assert event.payload["recent_conversation"] == [
        {"role": "user", "content": "访问 GitHub 看看 Sakura 内容"},
        {"role": "assistant", "content": "我打开看看。"},
        {"role": "assistant", "content": "稍微休息一下吧。"},
    ]
    assert event.payload["recent_conversation_summary_hint"] == (
        PROACTIVE_RECENT_CONVERSATION_SUMMARY_HINT
    )
    assert PROACTIVE_SCREEN_CONTEXT_HISTORY_MARKER not in str(
        event.payload["recent_conversation"]
    )


def test_proactive_care_event_reads_recent_conversation_from_history_store() -> None:
    from app.agent.proactive_care import PROACTIVE_SCREEN_CONTEXT_HISTORY_MARKER
    from app.storage.chat_history import ChatHistoryStore
    from app.ui.pet_window import PROACTIVE_RECENT_CONVERSATION_SUMMARY_HINT

    window = _build_minimal_proactive_window(
        screen_context_enabled=True,
        check_interval_minutes=1,
        cooldown_minutes=2,
    )
    history_path = (
        Path(__file__).resolve().parents[2]
        / "temp"
        / "test_runtime"
        / "proactive_history"
        / uuid.uuid4().hex
        / "history.jsonl"
    )
    store = ChatHistoryStore(history_path)
    store.append("system", PROACTIVE_SCREEN_CONTEXT_HISTORY_MARKER)
    store.append("user", "刚才已经提醒过我喝水了")
    store.append("assistant", "水を飲んでって言ったばかりだよ。", "我刚提醒过你喝水。")
    window.history_store = store
    window.subtitle_language = "zh"
    window.messages = []

    event = window._build_proactive_care_event(300.0)

    assert event.payload["recent_conversation"] == [
        {"role": "user", "content": "刚才已经提醒过我喝水了"},
        {"role": "assistant", "content": "我刚提醒过你喝水。"},
    ]
    assert event.payload["recent_conversation_summary_hint"] == (
        PROACTIVE_RECENT_CONVERSATION_SUMMARY_HINT
    )
    assert PROACTIVE_SCREEN_CONTEXT_HISTORY_MARKER not in str(
        event.payload["recent_conversation"]
    )


def test_proactive_recent_conversation_limits_count_and_content() -> None:
    from app.ui.pet_window import PROACTIVE_RECENT_CONVERSATION_CONTENT_LIMIT

    window = _build_minimal_proactive_window(
        screen_context_enabled=True,
        check_interval_minutes=1,
        cooldown_minutes=2,
    )
    window.messages = [
        {"role": "user", "content": f"第 {index} 条"}
        for index in range(13)
    ]
    window.messages.append({"role": "assistant", "content": "很长" * 500})

    event = window._build_proactive_care_event(300.0)
    recent_conversation = event.payload["recent_conversation"]

    assert len(recent_conversation) == 12
    assert recent_conversation[0] == {"role": "user", "content": "第 2 条"}
    assert recent_conversation[-1]["role"] == "assistant"
    assert len(recent_conversation[-1]["content"]) == (
        PROACTIVE_RECENT_CONVERSATION_CONTENT_LIMIT
    )
    assert recent_conversation[-1]["content"].endswith("…")


def test_proactive_care_capture_interval_allows_timer_jitter() -> None:
    window = _build_minimal_proactive_window(
        screen_context_enabled=True,
        check_interval_minutes=1,
        cooldown_minutes=10,
    )
    window.last_user_activity_at = 0.0

    assert not window._should_capture_proactive_screen_context(58.9)
    assert window._should_capture_proactive_screen_context(59.2)

    window.last_proactive_screen_context_at = 60.0
    assert not window._should_capture_proactive_screen_context(118.9)
    assert window._should_capture_proactive_screen_context(119.2)


def test_proactive_care_keeps_recent_screenshot_batch(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    import app.ui.pet_window as pet_window_module

    captures = []
    window = _build_minimal_proactive_window(
        screen_context_enabled=True,
        check_interval_minutes=1,
        cooldown_minutes=10,
    )

    observations: list[ScreenObservation] = []

    def fake_capture(_window):  # type: ignore[no-untyped-def]
        index = len(captures) + 1
        captures.append(index)
        observation = ScreenObservation(
            data_url=f"data:image/jpeg;base64,{index}",
            width=800,
            height=600,
            captured_at=f"2026-05-30T12:{index:02d}:00+08:00",
            screen_name="DISPLAY1",
        )
        observations.append(observation)
        return object()

    monkeypatch.setattr(pet_window_module, "capture_screen_image", fake_capture)
    window._start_screen_observation_encode = lambda _captured, context: (
        window._finish_proactive_screen_context(context, observations[-1]) or True
    )

    for index in range(8):
        window._capture_proactive_screen_context(float(index * 60))

    assert len(window.proactive_screen_contexts) == 6
    assert window.proactive_screen_context_dropped_count == 2
    assert [context["data_url"] for context in window.proactive_screen_contexts] == [
        "data:image/jpeg;base64,3",
        "data:image/jpeg;base64,4",
        "data:image/jpeg;base64,5",
        "data:image/jpeg;base64,6",
        "data:image/jpeg;base64,7",
        "data:image/jpeg;base64,8",
    ]


def test_proactive_care_uses_configured_screenshot_batch_limit(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    import app.ui.pet_window as pet_window_module

    captures = []
    window = _build_minimal_proactive_window(
        screen_context_enabled=True,
        check_interval_minutes=1,
        cooldown_minutes=10,
        screen_context_batch_limit=3,
    )

    observations: list[ScreenObservation] = []

    def fake_capture(_window):  # type: ignore[no-untyped-def]
        index = len(captures) + 1
        captures.append(index)
        observation = ScreenObservation(
            data_url=f"data:image/jpeg;base64,{index}",
            width=800,
            height=600,
            captured_at=f"2026-05-30T12:{index:02d}:00+08:00",
            screen_name="DISPLAY1",
        )
        observations.append(observation)
        return object()

    monkeypatch.setattr(pet_window_module, "capture_screen_image", fake_capture)
    window._start_screen_observation_encode = lambda _captured, context: (
        window._finish_proactive_screen_context(context, observations[-1]) or True
    )

    for index in range(5):
        window._capture_proactive_screen_context(float(index * 60))

    assert len(window.proactive_screen_contexts) == 3
    assert window.proactive_screen_context_dropped_count == 2
    assert [context["data_url"] for context in window.proactive_screen_contexts] == [
        "data:image/jpeg;base64,3",
        "data:image/jpeg;base64,4",
        "data:image/jpeg;base64,5",
    ]


def test_proactive_care_disabled_does_not_capture_or_send(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    import app.ui.pet_window as pet_window_module

    current_time = {"value": 600.0}
    events = []
    window = _build_minimal_proactive_window(
        screen_context_enabled=False,
        check_interval_minutes=1,
        cooldown_minutes=1,
        events=events,
    )

    def fail_capture(_window):  # type: ignore[no-untyped-def]
        raise AssertionError("关闭主动屏幕获取时不应该截图")

    monkeypatch.setattr(pet_window_module.time, "perf_counter", lambda: current_time["value"])
    monkeypatch.setattr(pet_window_module, "capture_screen_image", fail_capture)

    window._check_proactive_care()

    assert events == []
    assert window.proactive_screen_contexts == []


def test_screen_awareness_limits_night_health_reminders(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    import app.ui.pet_window as pet_window_module
    from app.ui.pet_window import PetWindow

    runtime_root = (
        Path(__file__).resolve().parents[2]
        / "temp"
        / "test_runtime"
        / "screen_awareness_health"
        / uuid.uuid4().hex
    )

    class MinimalWindow:
        _filter_screen_awareness_reply = PetWindow._filter_screen_awareness_reply
        _screen_awareness_health_reminder_seen = PetWindow._screen_awareness_health_reminder_seen
        _record_screen_awareness_health_reminder = PetWindow._record_screen_awareness_health_reminder
        _screen_awareness_state_path = PetWindow._screen_awareness_state_path
        _load_screen_awareness_state = PetWindow._load_screen_awareness_state
        _save_screen_awareness_state = PetWindow._save_screen_awareness_state

        def __init__(self) -> None:
            self.base_dir = runtime_root
            self._logged = []

        def _log_interaction_stage(self, *args):  # type: ignore[no-untyped-def]
            self._logged.append(args)

    monkeypatch.setattr(pet_window_module, "_screen_awareness_night_key", lambda: "2026-06-12")
    window = MinimalWindow()
    event = AgentEvent(type="screen_awareness_check", payload={})
    result = AgentResult(
        reply=ChatReply(
            [
                ChatSegment(
                    text="少し休んでもいいよ。",
                    translation="稍微休息一下也可以。",
                )
            ]
        ),
        actions=[],
    )

    first = window._filter_screen_awareness_reply(result, event)
    second = window._filter_screen_awareness_reply(result, event)

    assert first.reply.translation == "稍微休息一下也可以。"
    assert second.reply.segments == []


def test_user_activity_keeps_pending_proactive_screenshot_batch(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    import app.ui.pet_window as pet_window_module

    window = _build_minimal_proactive_window(
        screen_context_enabled=True,
        check_interval_minutes=1,
        cooldown_minutes=10,
    )
    window.proactive_screen_contexts = [{"data_url": "data:image/jpeg;base64,old"}]
    window.proactive_screen_context_batch_started_at = 60
    window.last_proactive_screen_context_at = 60
    window.proactive_screen_context_dropped_count = 2
    monkeypatch.setattr(pet_window_module.time, "perf_counter", lambda: 300.0)

    window._mark_user_activity()

    assert window.last_user_activity_at == 300.0
    assert window.proactive_screen_contexts == [{"data_url": "data:image/jpeg;base64,old"}]
    assert window.proactive_screen_context_batch_started_at == 60
    assert window.last_proactive_screen_context_at == 60
    assert window.proactive_screen_context_dropped_count == 2


def test_send_message_clears_pending_proactive_screenshot_batch(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    import app.ui.pet_window as pet_window_module

    window = _build_minimal_manual_screenshot_window("发送这条")
    minimal_window, requests, _history = window
    minimal_window.pending_manual_screen_observation = None
    minimal_window.proactive_screen_contexts = [{"data_url": "data:image/jpeg;base64,old"}]
    minimal_window.proactive_screen_context_batch_started_at = 60
    minimal_window.last_proactive_screen_context_at = 60
    minimal_window.proactive_screen_context_dropped_count = 2
    minimal_window._clear_proactive_screen_context_batch = (
        pet_window_module.PetWindow._clear_proactive_screen_context_batch.__get__(
            minimal_window,
            type(minimal_window),
        )
    )
    monkeypatch.setattr(pet_window_module.time, "perf_counter", lambda: 300.0)

    minimal_window.send_message("test")

    assert len(requests) == 1
    assert minimal_window.proactive_screen_contexts == []
    assert minimal_window.proactive_screen_context_batch_started_at is None
    assert minimal_window.last_proactive_screen_context_at is None
    assert minimal_window.proactive_screen_context_dropped_count == 0


class _DummyTextInput:
    def text(self) -> str:
        return ""


class _DummyEditableInput:
    def __init__(self, text: str) -> None:
        self._text = text
        self.cleared = False
        self.enabled = True
        self.placeholder = ""
        self.properties: dict[str, object] = {}

    def text(self) -> str:
        return self._text

    def hasFocus(self) -> bool:
        return False

    def clear(self) -> None:
        self.cleared = True
        self._text = ""

    def setEnabled(self, enabled: bool) -> None:
        self.enabled = enabled

    def setPlaceholderText(self, text: str) -> None:
        self.placeholder = text

    def property(self, name: str) -> object:
        return self.properties.get(name)

    def setProperty(self, name: str, value: object) -> None:
        self.properties[name] = value


class _DummyTimer:
    def isActive(self) -> bool:
        return False


class _DummyButton:
    def __init__(self) -> None:
        self.enabled = True
        self.text = ""
        self.properties: dict[str, object] = {}

    def setVisible(self, _visible: bool) -> None:
        pass

    def setEnabled(self, enabled: bool) -> None:
        self.enabled = enabled

    def setText(self, text: str) -> None:
        self.text = text

    def property(self, name: str) -> object:
        return self.properties.get(name)

    def setProperty(self, name: str, value: object) -> None:
        self.properties[name] = value


class _DummySubtitleController:
    def __init__(self) -> None:
        self.cancelled_with: list[str | None] = []
        self.waiting_started = 0
        self.active = False
        self.segments = []
        self.shown_immediately: list[str] = []
        self.subtitle_languages: list[str] = []
        self.restarted = False
        self.display_speeds: list[tuple[int, int]] = []

    def cancel_reply_flow(self, placeholder_text: str | None = None) -> None:
        self.cancelled_with.append(placeholder_text)

    def start_waiting_indicator(self) -> None:
        self.waiting_started += 1

    def show_segments(self, segments):  # type: ignore[no-untyped-def]
        self.segments.append(segments)

    def show_text_immediately(self, text: str) -> None:
        self.shown_immediately.append(text)

    def is_reply_sequence_active(self) -> bool:
        return self.active

    def set_subtitle_language(self, subtitle_language: str) -> None:
        self.subtitle_languages.append(subtitle_language)

    def restart_current_segment_speech(self) -> None:
        self.restarted = True

    def set_display_speed(self, typing_interval_ms: int, segment_pause_ms: int) -> None:
        self.display_speeds.append((typing_interval_ms, segment_pause_ms))


class _DummyBubbleAutoHide:
    def __init__(self) -> None:
        self.speaking_count = 0

    def notify_speaking(self) -> None:
        self.speaking_count += 1


class _DummyInputBarAnimator:
    def __init__(self) -> None:
        self.sync_count = 0
        self.feedback_count = 0

    def sync(self) -> None:
        self.sync_count += 1

    def play_send_feedback(self) -> None:
        self.feedback_count += 1


def test_manual_screenshot_empty_input_sends_default_text() -> None:
    window, requests, history = _build_minimal_manual_screenshot_window("")

    window.send_message("test")

    assert len(requests) == 1
    content = requests[0][-1]["content"]
    assert isinstance(content, list)
    assert content[0]["text"].startswith("请根据我框选的截图继续对话。")
    assert content[1]["image_url"]["url"] == "data:image/jpeg;base64,manual"
    assert window.subtitle_controller.waiting_started == 1
    assert window.subtitle_controller.cancelled_with == []
    assert window.bubble_auto_hide.speaking_count == 1
    assert window.input_edit.placeholder == "Sakura正在思考中…"
    assert window.input_edit.properties["replyWaiting"] is True
    assert window.send_button.properties["replyWaiting"] is True
    assert window.input_bar_animator.sync_count == 1
    assert window.pending_manual_screen_observation is None
    assert history
    assert "data:image/jpeg;base64" not in history[0][1]


def test_manual_screenshot_text_input_records_marker_without_image_data() -> None:
    window, requests, history = _build_minimal_manual_screenshot_window("帮我看这里")

    window.send_message("test")

    assert len(requests) == 1
    content = requests[0][-1]["content"]
    assert isinstance(content, list)
    assert content[0]["text"].startswith("帮我看这里")
    assert content[1]["image_url"]["url"] == "data:image/jpeg;base64,manual"
    assert window.messages[-1]["content"].startswith("帮我看这里")
    assert "已附加手动框选截图" in window.messages[-1]["content"]
    assert "visual_id=vis_" in window.messages[-1]["content"]
    assert window.pending_visual_observation_jobs[0].source == "manual_screenshot"
    assert "data:image/jpeg;base64" not in window.messages[-1]["content"]
    assert "data:image/jpeg;base64" not in history[0][1]


def test_visual_context_is_injected_for_screenshot_followup() -> None:
    from app.ui.pet_window import _add_visual_context_to_messages

    path = Path("data") / f"test_visual_context_{uuid.uuid4().hex}.jsonl"
    try:
        store = VisualObservationStore(path)
        store.append(
            VisualObservationRecord(
                id="vis_recent",
                created_at=datetime.now().astimezone().isoformat(timespec="seconds"),
                source="manual_screenshot",
                user_text="帮我看这里",
                screen_name="manual-selection",
                width=320,
                height=180,
                summary="截图里是聊天气泡。",
                visible_texts=["屏幕上的那句台词"],
                uncertain_texts=[],
                notable_elements=["聊天窗口"],
                confidence=0.9,
            )
        )

        messages = _add_visual_context_to_messages(
            [{"role": "user", "content": "刚才截图里有什么台词？"}],
            user_text="刚才截图里有什么台词？",
            store=store,
            has_current_image=False,
        )

        assert len(messages) == 2
        assert messages[0]["role"] == "system"
        assert "visual_id=vis_recent" in messages[0]["content"]
        assert "屏幕上的那句台词" in messages[0]["content"]
        assert messages[1]["content"] == "刚才截图里有什么台词？"
    finally:
        path.unlink(missing_ok=True)


def test_set_busy_disables_manual_screenshot_button() -> None:
    from app.ui.pet_window import PetWindow

    class MinimalBusyWindow:
        _set_busy = PetWindow._set_busy
        _set_reply_waiting_ui = PetWindow._set_reply_waiting_ui
        _normal_input_placeholder_text = PetWindow._normal_input_placeholder_text
        _reply_waiting_placeholder_text = PetWindow._reply_waiting_placeholder_text
        _sync_input_bar_waiting_visibility = PetWindow._sync_input_bar_waiting_visibility
        _set_widget_dynamic_property = PetWindow._set_widget_dynamic_property

    window = MinimalBusyWindow()
    window.character_profile = type("CharacterProfile", (), {"display_name": "Sakura"})()
    window.startup_initializing = False
    window.input_edit = _DummyEditableInput("")
    window.screenshot_button = _DummyButton()
    window.send_button = _DummyButton()
    window.confirm_action_button = _DummyButton()
    window.cancel_action_button = _DummyButton()
    window.input_bar_animator = _DummyInputBarAnimator()
    window._log_interaction_stage = lambda *_args, **_kwargs: None

    window._set_busy(True)
    assert window.input_edit.enabled
    assert not window.screenshot_button.enabled
    assert not window.send_button.enabled
    assert window.send_button.text == "等待"
    assert window.input_edit.placeholder == "Sakura正在思考中…"
    assert window.input_edit.properties["replyWaiting"] is True
    assert window.send_button.properties["replyWaiting"] is True
    assert window.input_bar_animator.sync_count == 1

    window._set_busy(False)
    assert window.screenshot_button.enabled
    assert window.send_button.enabled
    assert window.send_button.text == "发送"
    assert window.input_edit.placeholder == "和Sakura说点什么..."
    assert window.input_edit.properties["replyWaiting"] is False
    assert window.send_button.properties["replyWaiting"] is False
    assert window.input_bar_animator.sync_count == 2


def test_input_bar_pinned_while_waiting_reply() -> None:
    from app.ui.pet_window import PetWindow

    class MinimalInputBarWindow:
        _input_bar_pinned = PetWindow._input_bar_pinned
        _any_dialog_open = lambda _self: False
        _input_bar_foreground_allowed = lambda _self: True

    window = MinimalInputBarWindow()
    window.input_edit = _DummyEditableInput("")
    window.pending_tool_action = None
    window.reply_waiting_ui_active = False

    assert not window._input_bar_pinned()

    window.reply_waiting_ui_active = True

    assert window._input_bar_pinned()


def test_progress_reply_displays_and_records_assistant_message() -> None:
    from app.agent import AgentProgress
    from app.llm.chat_reply import parse_chat_reply
    from app.ui.pet_window import PetWindow, TRANSIENT_PROGRESS_MESSAGE_KEY

    class MinimalProgressWindow:
        _handle_progress_reply = PetWindow._handle_progress_reply

    window = MinimalProgressWindow()
    from app.ui.state import PetUiStateStore
    window.ui_state = PetUiStateStore()
    history = []
    window.messages = [{"role": "user", "content": "查一下"}]
    window._log_interaction_stage = lambda *_args, **_kwargs: None
    window._record_history = lambda *args, **_kwargs: history.append(args)
    window._record_assistant_reply_history = (
        PetWindow._record_assistant_reply_history.__get__(window, type(window))
    )

    window._handle_progress_reply(
        AgentProgress(
            reply=parse_chat_reply(
                '{"segments":[{"ja":"調べるね。","zh":"我查一下。","tone":"中性"}]}'
            )
        )
    )

    assert window.messages[-1]["role"] == "assistant"
    assert window.messages[-1]["content"] == "調べるね。"
    assert window.messages[-1][TRANSIENT_PROGRESS_MESSAGE_KEY] is True
    assert history[-1] == ("assistant", "調べるね。", "我查一下。", "中性", "")


def test_progress_reply_records_segments_as_separate_history_entries() -> None:
    from app.agent import AgentProgress
    from app.llm.chat_reply import parse_chat_reply
    from app.ui.pet_window import PetWindow, TRANSIENT_PROGRESS_MESSAGE_KEY

    class MinimalProgressWindow:
        _handle_progress_reply = PetWindow._handle_progress_reply
        _record_assistant_reply_history = PetWindow._record_assistant_reply_history

    window = MinimalProgressWindow()
    from app.ui.state import PetUiStateStore
    window.ui_state = PetUiStateStore()
    history = []
    window.messages = [{"role": "user", "content": "查一下"}]
    window._log_interaction_stage = lambda *_args, **_kwargs: None
    window._record_history = lambda *args, **_kwargs: history.append(args)

    window._handle_progress_reply(
        AgentProgress(
            reply=parse_chat_reply(
                '{"segments":['
                '{"ja":"一つ目。","zh":"第一段。","tone":"中性"},'
                '{"ja":"二つ目。","zh":"第二段。","tone":"中性"}'
                "]}"
            )
        )
    )

    assert window.messages[-1]["role"] == "assistant"
    assert window.messages[-1]["content"] == "一つ目。\n二つ目。"
    assert window.messages[-1][TRANSIENT_PROGRESS_MESSAGE_KEY] is True
    assert history == [
        ("assistant", "一つ目。", "第一段。", "中性", ""),
        ("assistant", "二つ目。", "第二段。", "中性", ""),
    ]


def test_assistant_reply_history_records_tone_and_portrait() -> None:
    from app.llm.chat_reply import ChatReply
    from app.ui.pet_window import PetWindow

    class MinimalHistoryWindow:
        _record_assistant_reply_history = PetWindow._record_assistant_reply_history

    window = MinimalHistoryWindow()
    history = []
    window._record_history = lambda *args, **_kwargs: history.append(args)

    window._record_assistant_reply_history(
        ChatReply(
            [
                ChatSegment(
                    "どうしたの？",
                    "困惑",
                    "怎么了？",
                    "张嘴疑问",
                )
            ]
        )
    )

    assert history == [("assistant", "どうしたの？", "怎么了？", "困惑", "张嘴疑问")]


def test_chat_history_store_round_trips_tone_and_portrait() -> None:
    from app.storage.chat_history import ChatHistoryStore

    history_path = (
        Path(__file__).resolve().parents[2]
        / "temp"
        / "test_runtime"
        / "chat_history_segments"
        / uuid.uuid4().hex
        / "history.jsonl"
    )
    store = ChatHistoryStore(history_path)

    store.append("assistant", "どうしたの？", "怎么了？", "困惑", "张嘴疑问")

    entries = store.load()
    assert len(entries) == 1
    assert entries[0].content == "どうしたの？"
    assert entries[0].translation == "怎么了？"
    assert entries[0].tone == "困惑"
    assert entries[0].portrait == "张嘴疑问"


def test_chat_history_store_loads_legacy_entries_without_tone_or_portrait() -> None:
    from app.storage.chat_history import ChatHistoryStore

    history_path = (
        Path(__file__).resolve().parents[2]
        / "temp"
        / "test_runtime"
        / "chat_history_legacy"
        / uuid.uuid4().hex
        / "history.jsonl"
    )
    history_path.parent.mkdir(parents=True, exist_ok=True)
    history_path.write_text(
        '{"created_at":"2026-06-01T10:00:00+08:00","role":"assistant",'
        '"content":"古い履歴。","translation":"旧历史。"}\n',
        encoding="utf-8",
    )

    entries = ChatHistoryStore(history_path).load()

    assert len(entries) == 1
    assert entries[0].content == "古い履歴。"
    assert entries[0].translation == "旧历史。"
    assert entries[0].tone == ""
    assert entries[0].portrait == ""


def test_reply_history_segments_load_from_persisted_history_entries() -> None:
    from app.storage.chat_history import ChatHistoryEntry
    from app.ui.pet_window import _reply_history_segments_from_entries

    segments = _reply_history_segments_from_entries(
        [
            ChatHistoryEntry("2026-06-01T10:00:00+08:00", "user", "你好"),
            ChatHistoryEntry(
                "2026-06-01T10:00:01+08:00",
                "assistant",
                "古い履歴。",
                "旧历史。",
            ),
            ChatHistoryEntry(
                "2026-06-01T10:00:02+08:00",
                "assistant",
                "表情付き。",
                "带表情。",
                "困惑",
                "张嘴疑问",
            ),
        ]
    )

    assert segments == [
        ChatSegment("古い履歴。", translation="旧历史。"),
        ChatSegment("表情付き。", "困惑", "带表情。", "张嘴疑问"),
    ]


def test_reply_history_segments_recover_json_string_history_entry() -> None:
    from app.storage.chat_history import ChatHistoryEntry
    from app.ui.pet_window import _reply_history_segments_from_entries

    segments = _reply_history_segments_from_entries(
        [
            ChatHistoryEntry(
                "2026-06-01T10:00:01+08:00",
                "assistant",
                '{"segments":[{"ja":"一つ目。","zh":"第一段。","tone":"中性","portrait":"站立待机"},'
                '{"ja":"二つ目。","zh":"第二段。","tone":"请求","portrait":"伸手命令"}]}',
            ),
        ]
    )

    assert segments == [
        ChatSegment("一つ目。", "中性", "第一段。", "站立待机"),
        ChatSegment("二つ目。", "请求", "第二段。", "伸手命令"),
    ]


def test_reply_history_reload_uses_history_store_entries() -> None:
    from app.storage.chat_history import ChatHistoryEntry
    from app.ui.pet_window import PetWindow

    class FakeHistoryStore:
        def load(self):  # type: ignore[no-untyped-def]
            return [
                ChatHistoryEntry(
                    "2026-06-01T10:00:00+08:00",
                    "assistant",
                    "再起動後も戻れる。",
                    "重启后也能回看。",
                    "中性",
                    "站立待机",
                )
            ]

    class MinimalHistoryWindow:
        _load_reply_history_from_store = PetWindow._load_reply_history_from_store
        _normalized_reply_history_index = PetWindow._normalized_reply_history_index
        _can_review_reply_history = PetWindow._can_review_reply_history
        _update_reply_history_buttons = PetWindow._update_reply_history_buttons

    window = MinimalHistoryWindow()
    window.history_store = FakeHistoryStore()
    window.reply_history_segments = []
    window.reply_history_index = None
    window.reply_history_review_active = True
    window.reply_history_previous_button = _DummyButton()
    window.reply_history_next_button = _DummyButton()
    window.worker_thread = None
    window.subtitle_controller = _DummySubtitleController()
    window._log_interaction_stage = lambda *_args, **_kwargs: None

    window._load_reply_history_from_store()

    assert window.reply_history_segments == [
        ChatSegment("再起動後も戻れる。", "中性", "重启后也能回看。", "站立待机")
    ]
    assert window.reply_history_index == 0
    assert not window.reply_history_review_active


def test_reply_history_buttons_review_segments_without_tts_or_history() -> None:
    from app.ui.pet_window import PetWindow, SUBTITLE_LANGUAGE_ZH

    class DummyPortraitController:
        def __init__(self) -> None:
            self.applied: list[ChatSegment] = []

        def apply_for_segment(self, segment: ChatSegment) -> None:
            self.applied.append(segment)

    class MinimalReplyHistoryWindow:
        _remember_reply_history_segments = PetWindow._remember_reply_history_segments
        _show_reply_segments = PetWindow._show_reply_segments
        _show_previous_reply_history = PetWindow._show_previous_reply_history
        _show_next_reply_history = PetWindow._show_next_reply_history
        _show_reply_history_at = PetWindow._show_reply_history_at
        _exit_reply_history_review = PetWindow._exit_reply_history_review
        _normalized_reply_history_index = PetWindow._normalized_reply_history_index
        _can_review_reply_history = PetWindow._can_review_reply_history
        _update_reply_history_buttons = PetWindow._update_reply_history_buttons

    window = MinimalReplyHistoryWindow()
    window.reply_history_segments = []
    window.reply_history_index = None
    window.reply_history_review_active = False
    window.worker_thread = None
    window.subtitle_language = SUBTITLE_LANGUAGE_ZH
    window.subtitle_controller = _DummySubtitleController()
    window.portrait_controller = DummyPortraitController()
    window.reply_history_previous_button = _DummyButton()
    window.reply_history_next_button = _DummyButton()
    window.messages = [{"role": "assistant", "content": "既存"}]
    window._record_history = lambda *_args: (_ for _ in ()).throw(AssertionError("回看不应写历史"))
    window._log_interaction_stage = lambda *_args, **_kwargs: None

    first = ChatSegment("一つ目。", "中性", "第一段。", "站立待机")
    second = ChatSegment("二つ目。", "困惑", "第二段。", "张嘴疑问")

    window._show_reply_segments([first, second])
    assert window.subtitle_controller.segments == [[first, second]]
    assert window.reply_history_previous_button.enabled
    assert not window.reply_history_next_button.enabled

    window._show_previous_reply_history()
    assert window.reply_history_index == 0
    assert window.reply_history_review_active
    assert window.subtitle_controller.shown_immediately[-1] == "第一段。"
    assert window.portrait_controller.applied[-1] == first
    assert window.messages == [{"role": "assistant", "content": "既存"}]
    assert not window.reply_history_previous_button.enabled
    assert window.reply_history_next_button.enabled

    window._show_next_reply_history()
    assert window.reply_history_index == 1
    assert window.subtitle_controller.shown_immediately[-1] == "第二段。"
    assert window.portrait_controller.applied[-1] == second


def test_consume_agent_result_shows_segments_for_tts_flow() -> None:
    from app.agent import AgentResult
    from app.llm.chat_reply import ChatReply
    from app.ui.pet_window import PetWindow, TRANSIENT_PROGRESS_MESSAGE_KEY

    class MinimalConsumeWindow:
        _consume_agent_result = PetWindow._consume_agent_result

    window = MinimalConsumeWindow()
    segment = ChatSegment("時間だよ。水を飲んで。", "请求", "到时间了，喝水。", "伸手命令")
    shown_segments = []
    applied_results = []
    history = []
    window.messages = [
        {
            "role": "assistant",
            "content": "途中経過。",
            TRANSIENT_PROGRESS_MESSAGE_KEY: True,
        }
    ]
    window._log_interaction_stage = lambda *_args, **_kwargs: None
    window._record_assistant_reply_history = lambda reply, _debug=None: history.append((reply, _debug))
    window._show_reply_segments = lambda segments: shown_segments.append(segments)
    window._apply_pending_action_from_result = lambda result: applied_results.append(result)

    result = AgentResult(reply=ChatReply([segment]), _debug={"source": "reminder_due"})

    window._consume_agent_result(result)

    assert window.messages == [{"role": "assistant", "content": segment.text}]
    assert history == [(result.reply, {"source": "reminder_due"})]
    assert shown_segments == [[segment]]
    assert applied_results == [result]


def test_reply_history_buttons_disable_while_busy_or_playing() -> None:
    from app.ui.pet_window import PetWindow

    class MinimalReplyHistoryWindow:
        _normalized_reply_history_index = PetWindow._normalized_reply_history_index
        _can_review_reply_history = PetWindow._can_review_reply_history
        _update_reply_history_buttons = PetWindow._update_reply_history_buttons

    window = MinimalReplyHistoryWindow()
    window.reply_history_segments = [ChatSegment("一つ目。"), ChatSegment("二つ目。")]
    window.reply_history_index = 1
    window.reply_history_previous_button = _DummyButton()
    window.reply_history_next_button = _DummyButton()
    window.subtitle_controller = _DummySubtitleController()

    window.worker_thread = object()
    window._update_reply_history_buttons()
    assert not window.reply_history_previous_button.enabled
    assert not window.reply_history_next_button.enabled

    window.worker_thread = None
    window.subtitle_controller.active = True
    window._update_reply_history_buttons()
    assert not window.reply_history_previous_button.enabled
    assert not window.reply_history_next_button.enabled

    window.subtitle_controller.active = False
    window._update_reply_history_buttons()
    assert window.reply_history_previous_button.enabled
    assert not window.reply_history_next_button.enabled


def test_reply_history_review_text_refreshes_when_subtitle_language_changes(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    import app.ui.pet_window as pet_window_module
    from app.ui.pet_window import PetWindow, SUBTITLE_LANGUAGE_ZH

    class MinimalReplyHistoryWindow:
        _toggle_chinese_subtitles = PetWindow._toggle_chinese_subtitles
        _save_system_config_values = PetWindow._save_system_config_values
        _refresh_reply_history_review_text = PetWindow._refresh_reply_history_review_text
        _normalized_reply_history_index = PetWindow._normalized_reply_history_index

    window = MinimalReplyHistoryWindow()
    window.subtitle_language = "ja"
    window.subtitle_controller = _DummySubtitleController()
    window.history_window = None
    window.reply_history_review_active = True
    window.reply_history_index = 0
    window.reply_history_segments = [ChatSegment("原文", "中性", "译文")]
    window._apply_speech_font = lambda: None

    class SettingsServiceStub:
        def save_system_values(self, section, values):  # type: ignore[no-untyped-def]
            assert section == "ui"
            assert values == {"subtitle_language": SUBTITLE_LANGUAGE_ZH}

    window.settings_service = SettingsServiceStub()

    window._toggle_chinese_subtitles(True)

    assert window.subtitle_language == SUBTITLE_LANGUAGE_ZH
    assert window.subtitle_controller.subtitle_languages == [SUBTITLE_LANGUAGE_ZH]
    assert window.subtitle_controller.shown_immediately == ["译文"]
    assert not window.subtitle_controller.restarted


def test_pet_window_toggle_always_on_top_saves_and_applies() -> None:
    from app.ui.pet_window import PetWindow

    class MinimalWindow:
        _toggle_always_on_top = PetWindow._toggle_always_on_top

        def __init__(self) -> None:
            self.always_on_top_enabled = False
            self.saved_values: list[tuple[str, dict[str, bool]]] = []
            self.apply_count = 0
            self.raise_count = 0

        def _save_system_config_values(self, section: str, values: dict[str, bool]) -> None:
            self.saved_values.append((section, values))

        def _apply_window_flags(self) -> None:
            self.apply_count += 1

        def raise_(self) -> None:
            self.raise_count += 1

    window = MinimalWindow()

    window._toggle_always_on_top(True)

    assert window.always_on_top_enabled is True
    assert window.saved_values == [("ui", {"always_on_top_enabled": True})]
    assert window.apply_count == 1
    assert window.raise_count == 1

    window._toggle_always_on_top(False)

    assert window.always_on_top_enabled is False
    assert window.saved_values[-1] == ("ui", {"always_on_top_enabled": False})
    assert window.apply_count == 2
    assert window.raise_count == 1


def test_pet_window_apply_window_flags_syncs_native_topmost_state() -> None:
    from app.ui.pet_window import PetWindow

    class MinimalWindow:
        _apply_window_flags = PetWindow._apply_window_flags

        def __init__(self) -> None:
            self.visible = True
            self.show_count = 0
            self.sync_count = 0
            self.applied_flags = None

        def isVisible(self) -> bool:
            return self.visible

        def _window_flags(self):  # type: ignore[no-untyped-def]
            return "flags"

        def setWindowFlags(self, flags) -> None:  # type: ignore[no-untyped-def]
            self.applied_flags = flags

        def show(self) -> None:
            self.show_count += 1

        def _schedule_native_topmost_sync(self) -> None:
            self.sync_count += 1

        def _sync_card_window_topmost_flags(self) -> None:
            pass

        def _raise_foreground_controls(self) -> None:
            pass

    window = MinimalWindow()

    window._apply_window_flags()

    assert window.applied_flags == "flags"
    assert window.show_count == 1
    assert window.sync_count == 1


def test_pet_window_apply_window_flags_does_not_sync_native_state_before_visible() -> None:
    from app.ui.pet_window import PetWindow

    class MinimalWindow:
        _apply_window_flags = PetWindow._apply_window_flags

        def __init__(self) -> None:
            self.show_count = 0
            self.sync_count = 0

        def isVisible(self) -> bool:
            return False

        def _window_flags(self):  # type: ignore[no-untyped-def]
            return "flags"

        def setWindowFlags(self, _flags) -> None:  # type: ignore[no-untyped-def]
            return None

        def show(self) -> None:
            self.show_count += 1

        def _schedule_native_topmost_sync(self) -> None:
            self.sync_count += 1

        def _sync_card_window_topmost_flags(self) -> None:
            pass

        def _raise_foreground_controls(self) -> None:
            pass

    window = MinimalWindow()

    window._apply_window_flags()

    assert window.show_count == 0
    assert window.sync_count == 0


def test_pet_window_schedules_native_topmost_sync_on_macos(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    import app.ui.pet_window as pet_window_module
    from app.ui.pet_window import PetWindow

    events: list[str] = []
    monkeypatch.setattr(pet_window_module.sys, "platform", "darwin")
    monkeypatch.setattr(
        pet_window_module.QTimer,
        "singleShot",
        lambda _delay, callback: events.append("timer") or callback(),
    )

    class MinimalWindow:
        _schedule_native_topmost_sync = PetWindow._schedule_native_topmost_sync

        def _sync_native_topmost_state(self) -> None:
            events.append("sync")

    MinimalWindow()._schedule_native_topmost_sync()

    assert events == ["timer", "sync"]


def test_pet_window_context_menu_resyncs_topmost_after_menu_closes() -> None:
    from app.ui.pet_window import PetWindow

    class MenuStub:
        def __init__(self, events: list[str]) -> None:
            self.events = events

        def exec(self, _position) -> None:  # type: ignore[no-untyped-def]
            self.events.append("exec")

    class MinimalWindow:
        _show_context_menu = PetWindow._show_context_menu

        def __init__(self) -> None:
            self.events: list[str] = []

        def _build_menu(self) -> MenuStub:
            return MenuStub(self.events)

        def _sync_native_topmost_state(self) -> None:
            self.events.append("sync")

    window = MinimalWindow()

    window._show_context_menu(object())  # type: ignore[arg-type]

    assert window.events == ["exec", "sync"]


def test_pet_window_syncs_macos_native_topmost_state(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    import app.ui.pet_window as pet_window_module
    from app.ui.pet_window import PetWindow

    calls: list[tuple[int, bool]] = []
    monkeypatch.setattr(pet_window_module.sys, "platform", "darwin")
    monkeypatch.setattr(
        pet_window_module,
        "_set_macos_window_topmost",
        lambda window_id, enabled: calls.append((window_id, enabled)),
    )

    class MinimalWindow:
        _sync_native_topmost_state = PetWindow._sync_native_topmost_state
        _topmost_sync_windows = PetWindow._topmost_sync_windows

        always_on_top_enabled = True

        def isVisible(self) -> bool:
            return True

        def winId(self) -> int:
            return 123

    MinimalWindow()._sync_native_topmost_state()

    assert calls == [(123, True)]


def test_pet_window_skips_macos_native_topmost_sync_when_hidden(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    import app.ui.pet_window as pet_window_module
    from app.ui.pet_window import PetWindow

    calls: list[tuple[int, bool]] = []
    monkeypatch.setattr(pet_window_module.sys, "platform", "darwin")
    monkeypatch.setattr(
        pet_window_module,
        "_set_macos_window_topmost",
        lambda window_id, enabled: calls.append((window_id, enabled)),
    )

    class MinimalWindow:
        _sync_native_topmost_state = PetWindow._sync_native_topmost_state

        always_on_top_enabled = True

        def isVisible(self) -> bool:
            return False

        def winId(self) -> int:
            return 123

    MinimalWindow()._sync_native_topmost_state()

    assert calls == []


def test_screen_observation_followup_uses_last_user_message_after_progress(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from app.agent import AgentAction, AgentResult
    from app.llm.chat_reply import parse_chat_reply
    import app.ui.pet_window as pet_window_module
    from app.ui.pet_window import PetWindow

    class MinimalScreenFollowupWindow:
        _queue_screen_observation_followup = PetWindow._queue_screen_observation_followup
        _finish_chat_screen_observation_followup = PetWindow._finish_chat_screen_observation_followup
        _resume_screen_observation_followup_cleanup = lambda self: None

    window = MinimalScreenFollowupWindow()
    history = []
    window.messages = [
        {"role": "user", "content": "早上好"},
        {"role": "assistant", "content": "少し見るね。"},
    ]
    window.screen_observation_enabled = True
    window.model_vision_enabled = True
    window.autonomous_screen_observation_enabled = True
    window._log_interaction_stage = lambda *_args, **_kwargs: None
    window._record_history = lambda *args: history.append(args)
    window._consume_agent_result = lambda _result: None
    window._start_screen_observation_encode = lambda _captured, context: (
        window._finish_chat_screen_observation_followup(context, observation) or True
    )
    observation = ScreenObservation(
        data_url="data:image/jpeg;base64,screen",
        width=640,
        height=360,
        captured_at="2026-05-31T12:00:00+08:00",
        screen_name="DISPLAY1",
    )
    monkeypatch.setattr(pet_window_module, "capture_screen_image", lambda _window: object())

    queued = window._queue_screen_observation_followup(
        AgentResult(
            reply=parse_chat_reply(
                '{"segments":[{"ja":"見るね。","zh":"我看看。","tone":"中性"}]}'
            ),
            actions=[AgentAction(type="screen_observation_request", payload={"reason": "看屏幕"})],
        )
    )

    assert queued
    assert "已自主观察屏幕" in window.messages[0]["content"]
    assert window.messages[1]["content"] == "少し見るね。"
    assert window.pending_screen_observation_messages[-1]["role"] == "user"
    assert isinstance(window.pending_screen_observation_messages[-1]["content"], list)
    assert len(window.pending_screen_observation_messages) == 1
    assert history[-1][0] == "system"


def test_screen_observation_followup_keeps_large_image_after_progress(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from app.agent import AgentAction, AgentResult
    from app.llm.chat_reply import parse_chat_reply
    import app.ui.pet_window as pet_window_module
    from app.ui.pet_window import PetWindow

    class MinimalScreenFollowupWindow:
        _queue_screen_observation_followup = PetWindow._queue_screen_observation_followup
        _finish_chat_screen_observation_followup = PetWindow._finish_chat_screen_observation_followup
        _resume_screen_observation_followup_cleanup = lambda self: None

    window = MinimalScreenFollowupWindow()
    window.messages = [
        {"role": "user", "content": "下午好"},
        {"role": "assistant", "content": "少し見るね。"},
    ]
    window.screen_observation_enabled = True
    window.model_vision_enabled = True
    window.autonomous_screen_observation_enabled = True
    window._log_interaction_stage = lambda *_args, **_kwargs: None
    window._record_history = lambda *_args: None
    window._consume_agent_result = lambda _result: None
    window._start_screen_observation_encode = lambda _captured, context: (
        window._finish_chat_screen_observation_followup(context, observation) or True
    )
    observation = ScreenObservation(
        data_url=f"data:image/jpeg;base64,{'a' * 50000}",
        width=640,
        height=360,
        captured_at="2026-05-31T12:00:00+08:00",
        screen_name="DISPLAY1",
    )
    monkeypatch.setattr(pet_window_module, "capture_screen_image", lambda _window: object())

    queued = window._queue_screen_observation_followup(
        AgentResult(
            reply=parse_chat_reply(
                '{"segments":[{"ja":"見るね。","zh":"我看看。","tone":"中性"}]}'
            ),
            actions=[AgentAction(type="screen_observation_request", payload={"reason": "看屏幕"})],
        )
    )

    assert queued
    assert len(window.pending_screen_observation_messages) == 1
    content = window.pending_screen_observation_messages[0]["content"]
    assert isinstance(content, list)
    assert content[1]["type"] == "image_url"


def _build_minimal_manual_screenshot_window(text: str):
    from app.ui.pet_window import PetWindow

    class MinimalManualScreenshotWindow:
        send_message = PetWindow.send_message
        _show_waiting_reply_placeholder = PetWindow._show_waiting_reply_placeholder
        _set_reply_waiting_ui = PetWindow._set_reply_waiting_ui
        _normal_input_placeholder_text = PetWindow._normal_input_placeholder_text
        _reply_waiting_placeholder_text = PetWindow._reply_waiting_placeholder_text
        _sync_input_bar_waiting_visibility = PetWindow._sync_input_bar_waiting_visibility
        _set_widget_dynamic_property = PetWindow._set_widget_dynamic_property
        _record_user_message = PetWindow._record_user_message

    window = MinimalManualScreenshotWindow()
    requests = []
    history = []
    window.input_edit = _DummyEditableInput(text)
    window.worker_thread = None
    window.pending_manual_screen_observation = ScreenObservation(
        data_url="data:image/jpeg;base64,manual",
        width=320,
        height=180,
        captured_at="2026-05-31T12:00:00+08:00",
        screen_name="manual-selection",
    )
    window.screen_observation_enabled = True
    window.messages = []
    window.active_interaction_id = ""
    window.startup_initializing = False
    window.character_profile = type("CharacterProfile", (), {"display_name": "Sakura"})()
    window.send_button = _DummyButton()
    window.input_bar_animator = _DummyInputBarAnimator()
    window.subtitle_controller = _DummySubtitleController()
    window.bubble_auto_hide = _DummyBubbleAutoHide()
    window._mark_user_activity = lambda: None
    window._begin_interaction = lambda _source: setattr(window, "active_interaction_id", "test")
    window._log_interaction_stage = lambda *_args, **_kwargs: None
    window._end_interaction = lambda _outcome: None
    window._set_pending_tool_action = lambda _action: None
    window._record_history = lambda *args: history.append(args)
    window._clear_proactive_screen_context_batch = lambda _reason: None
    window._start_chat_worker = lambda request_messages: requests.append(request_messages)
    window._update_manual_screenshot_button = lambda: None
    window._clear_manual_screen_observation = lambda: setattr(
        window,
        "pending_manual_screen_observation",
        None,
    )
    window._collapse_auto_fit_bubble_height = lambda: None
    return window, requests, history



def _build_minimal_proactive_window(
    *,
    screen_context_enabled: bool,
    check_interval_minutes: int,
    cooldown_minutes: int,
    screen_context_batch_limit: int = 6,
    events=None,  # type: ignore[no-untyped-def]
    history=None,  # type: ignore[no-untyped-def]
):
    from app.ui.pet_window import PetWindow

    class MinimalProactiveWindow:
        _can_run_proactive_care = PetWindow._can_run_proactive_care
        _check_proactive_care = PetWindow._check_proactive_care
        _should_capture_proactive_screen_context = (
            PetWindow._should_capture_proactive_screen_context
        )
        _capture_proactive_screen_context = PetWindow._capture_proactive_screen_context
        _finish_proactive_screen_context = PetWindow._finish_proactive_screen_context
        _should_send_proactive_care_batch = PetWindow._should_send_proactive_care_batch
        _build_proactive_care_event = PetWindow._build_proactive_care_event
        _proactive_screen_context_allowed = PetWindow._proactive_screen_context_allowed
        _clear_proactive_screen_context_batch = PetWindow._clear_proactive_screen_context_batch
        _mark_user_activity = PetWindow._mark_user_activity

    window = MinimalProactiveWindow()
    window.proactive_care_settings = ProactiveCareSettings(
        enabled=screen_context_enabled,
        screen_context_enabled=screen_context_enabled,
        check_interval_minutes=check_interval_minutes,
        cooldown_minutes=cooldown_minutes,
        screen_context_batch_limit=screen_context_batch_limit,
    )
    window.worker_thread = None
    window.active_reminder_id = None
    window.active_event_type = ""
    window.pending_tool_action = None
    window.pending_screen_observation_messages = None
    window.screen_observation_followup_in_progress = False
    window.screen_observation_encode_thread = None
    window.active_interaction_id = ""
    window.input_edit = _DummyTextInput()
    window.speech_timer = _DummyTimer()
    window.current_segment_sequence_id = None
    window.current_segment_speech_done = True
    window.current_segment_tts_done = True
    window.last_user_activity_at = 0.0
    window.last_proactive_care_at = None
    window.last_proactive_screen_context_at = None
    window.proactive_screen_context_batch_started_at = None
    window.proactive_screen_contexts = []
    window.proactive_screen_context_dropped_count = 0
    window.confirm_action_button = _DummyButton()
    window.cancel_action_button = _DummyButton()
    captured_events = events if events is not None else []
    captured_history = history if history is not None else []
    window._run_event_worker = lambda event, reminder_id=None: captured_events.append(event)
    window._record_history = lambda *args: captured_history.append(args)
    return window


def _build_minimal_screen_awareness_window(
    *,
    screen_context_enabled: bool,
    check_interval_minutes: int,
    cooldown_minutes: int,
    screen_context_batch_limit: int = 6,
    events=None,  # type: ignore[no-untyped-def]
    history=None,  # type: ignore[no-untyped-def]
):
    from app.ui.pet_window import PetWindow

    class MinimalScreenAwarenessWindow:
        _current_screen_awareness_settings = PetWindow._current_screen_awareness_settings
        _can_run_screen_awareness = PetWindow._can_run_screen_awareness
        _check_screen_awareness = PetWindow._check_screen_awareness
        _should_capture_screen_awareness_context = (
            PetWindow._should_capture_screen_awareness_context
        )
        _capture_screen_awareness_context = PetWindow._capture_screen_awareness_context
        _finish_screen_awareness_context = PetWindow._finish_screen_awareness_context
        _should_send_screen_awareness_batch = PetWindow._should_send_screen_awareness_batch
        _build_screen_awareness_event = PetWindow._build_screen_awareness_event
        _screen_awareness_context_allowed = PetWindow._screen_awareness_context_allowed
        _clear_screen_awareness_context_batch = PetWindow._clear_screen_awareness_context_batch

    window = MinimalScreenAwarenessWindow()
    window.screen_awareness_settings = ScreenAwarenessSettings(
        enabled=screen_context_enabled,
        screen_context_enabled=screen_context_enabled,
        check_interval_minutes=check_interval_minutes,
        cooldown_minutes=cooldown_minutes,
        screen_context_batch_limit=screen_context_batch_limit,
    )
    window.worker_thread = None
    window.active_reminder_id = None
    window.active_event_type = ""
    window.pending_tool_action = None
    window.pending_screen_observation_messages = None
    window.screen_observation_followup_in_progress = False
    window.screen_observation_encode_thread = None
    window.active_interaction_id = ""
    window.input_edit = _DummyTextInput()
    window.speech_timer = _DummyTimer()
    window.current_segment_sequence_id = None
    window.current_segment_speech_done = True
    window.current_segment_tts_done = True
    window.last_user_activity_at = 0.0
    window.last_screen_awareness_at = None
    window.last_screen_awareness_context_at = None
    window.screen_awareness_context_batch_started_at = None
    window.screen_awareness_contexts = []
    window.screen_awareness_context_dropped_count = 0
    captured_events = events if events is not None else []
    captured_history = history if history is not None else []
    window._run_event_worker = lambda event, reminder_id=None: captured_events.append(event)
    window._record_history = lambda *args: captured_history.append(args)
    return window


def _build_runtime_root_with_character(QPixmap, Qt):  # type: ignore[no-untyped-def]
    root = (
        Path(__file__).resolve().parents[2]
        / "temp"
        / "test_runtime"
        / "pet_window_startup"
        / uuid.uuid4().hex
    )
    config_dir = root / "data" / "config"
    character_dir = root / "characters" / "demo"
    config_dir.mkdir(parents=True, exist_ok=True)
    character_dir.mkdir(parents=True, exist_ok=True)

    (config_dir / "api.yaml").write_text(
        """
llm:
  base_url: https://api.example.com/v1
  api_key: test-key
  model: test-model
tts:
  provider: none
  enabled: false
""".strip(),
        encoding="utf-8",
    )
    (config_dir / "characters.yaml").write_text(
        "current_character_id: demo\n",
        encoding="utf-8",
    )
    (config_dir / "system_config.yaml").write_text(
        """
ui:
  portrait_scale_percent: 100
memory_curation:
  enabled: false
""".strip(),
        encoding="utf-8",
    )
    (character_dir / "card.md").write_text("system prompt", encoding="utf-8")
    portrait_path = character_dir / "portrait.png"
    portrait = QPixmap(320, 480)
    portrait.fill(Qt.GlobalColor.white)
    assert portrait.save(str(portrait_path))
    (character_dir / "character.json").write_text(
        """
{
  "id": "demo",
  "display_name": "Demo",
  "initial_message": "hello",
  "card": "card.md",
  "portrait": {
    "default": "portrait.png"
  }
}
""".strip(),
        encoding="utf-8",
    )
    return root


def _build_settings_dialog_character(
    root: Path,
    character_id: str,
    display_name: str,
    theme: dict[str, object] | None = None,
    *,
    with_voice: bool = True,
    with_voice_models: bool = True,
):
    from app.config.character_loader import CharacterRegistry

    character_dir = root / "characters" / character_id
    character_dir.mkdir(parents=True, exist_ok=True)
    (character_dir / "card.md").write_text("system prompt", encoding="utf-8")
    (character_dir / "portrait.png").write_bytes(b"portrait")
    character_data = {
        "id": character_id,
        "display_name": display_name,
        "initial_message": "hello",
        "card": "card.md",
        "portrait": {
            "default": "portrait.png",
        },
    }
    if with_voice:
        (character_dir / "voice" / "models").mkdir(parents=True, exist_ok=True)
        (character_dir / "voice" / "refs" / "tone_refs").mkdir(parents=True, exist_ok=True)
        (character_dir / "voice" / "refs" / "tone_refs" / "neutral.wav").write_bytes(b"wav")
        (character_dir / "voice" / "refs" / "ref.txt").write_text(
            "voice/refs/tone_refs/neutral.wav|JA|テスト|中性\n",
            encoding="utf-8",
        )
        voice_data = {
            "tone_refs": "voice/refs/ref.txt",
            "ref_lang": "ja",
            "text_lang": "ja",
        }
        if with_voice_models:
            (character_dir / "voice" / "models" / "gpt.ckpt").write_bytes(b"gpt")
            (character_dir / "voice" / "models" / "sovits.pth").write_bytes(b"sovits")
            voice_data.update(
                {
                    "gpt_model": "voice/models/gpt.ckpt",
                    "sovits_model": "voice/models/sovits.pth",
                }
            )
        character_data["voice"] = voice_data
    if theme is not None:
        character_data["theme"] = theme
    (character_dir / "character.json").write_text(
        json.dumps(
            character_data,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return CharacterRegistry(root).get(character_id)


def _build_settings_dialog_voice_archive(root: Path) -> Path:
    from app.config.character_archive import VOICE_ARCHIVE_FORMAT, VOICE_ARCHIVE_VERSION

    archive_path = root / f"voice_{uuid.uuid4().hex}.voice"
    with zipfile.ZipFile(archive_path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(
            "manifest.json",
            json.dumps(
                {
                    "format": VOICE_ARCHIVE_FORMAT,
                    "version": VOICE_ARCHIVE_VERSION,
                    "voice": {
                        "gpt_model": "voice/models/imported.ckpt",
                        "sovits_model": "voice/models/imported.pth",
                        "tone_refs": "voice/refs/ref.txt",
                        "ref_lang": "zh",
                        "text_lang": "zh",
                    },
                },
                ensure_ascii=False,
            ),
        )
        zf.writestr("voice/models/imported.ckpt", b"imported-gpt")
        zf.writestr("voice/models/imported.pth", b"imported-sovits")
        zf.writestr("voice/refs/tone_refs/imported.wav", b"wav")
        zf.writestr("voice/refs/ref.txt", "voice/refs/tone_refs/imported.wav|ZH|你好|中性\n")
    return archive_path


class _NonModalSettingsDialogStub:
    """非模态设置窗口的测试桩：模拟 finished 信号与 show/raise_ 等接口。

    show_settings 已从模态 exec() 改为非模态 show() + finished 信号回调，
    这里在 show() 时同步 emit finished(Accepted)，保持「调用 show_settings 后
    结果处理同步完成」的测试语义。子类只需在 __init__ 里填好 result_* 属性。
    """

    class _FinishedSignal:
        def __init__(self) -> None:
            self._slots: list = []

        def connect(self, slot) -> None:  # type: ignore[no-untyped-def]
            self._slots.append(slot)

        def emit(self, result: int) -> None:
            for slot in list(self._slots):
                slot(result)

    _dialog_result = None  # 子类可覆盖；None 表示用 QDialog.DialogCode.Accepted

    def __init__(self, *_args, **_kwargs) -> None:
        self.finished = _NonModalSettingsDialogStub._FinishedSignal()

    def _resolved_result(self) -> int:
        import app.ui.pet_window as pet_window_module

        if self._dialog_result is not None:
            return self._dialog_result
        return pet_window_module.QDialog.DialogCode.Accepted

    def show(self) -> None:
        # 非模态打开后立即「关闭」，同步触发结果处理回调。
        self.finished.emit(self._resolved_result())

    def raise_(self) -> None:  # noqa: N802 - 匹配 Qt 接口名
        pass

    def activateWindow(self) -> None:  # noqa: N802 - 匹配 Qt 接口名
        pass


def _settings_dialog_character_kwargs(root: Path) -> dict[str, object]:
    from app.config.character_loader import CharacterRegistry

    profile = _build_settings_dialog_character(root, "sakura", "Sakura")
    return {
        "character_registry": CharacterRegistry(root),
        "current_character": profile,
    }


def _build_api_settings_dialog(name: str, *, model: str = "test-model"):
    qtwidgets = pytest.importorskip("PySide6.QtWidgets")
    from app.ui.settings_dialog import SettingsDialog

    QApplication = qtwidgets.QApplication
    app = QApplication.instance() or QApplication([])
    root = _ui_runtime_root(name)
    dialog = SettingsDialog(
        api_settings=ApiSettings(
            base_url="https://api.example.com/v1",
            api_key="test-key",
            model=model,
        ),
        tts_settings=_minimal_tts_settings(),
        base_dir=root,
        **_settings_dialog_character_kwargs(root),
        proactive_care_settings=ProactiveCareSettings(screen_context_enabled=True),
        mcp_settings=MCPRuntimeSettings(windows_enabled=False),
    )
    return dialog, app


def _minimal_tts_settings() -> GPTSoVITSTTSSettings:
    root = _ui_runtime_root("minimal_tts")
    ref_audio_path = root / "voice" / "refs" / "tone_refs" / "neutral.wav"
    ref_audio_path.parent.mkdir(parents=True, exist_ok=True)
    ref_audio_path.write_bytes(b"wav")
    ref_text_path = root / "voice" / "refs" / "ref.txt"
    ref_text_path.write_text(
        "voice/refs/tone_refs/neutral.wav|JA|テスト|中性\n",
        encoding="utf-8",
    )
    return GPTSoVITSTTSSettings(
        enabled=False,
        api_url="http://127.0.0.1:9880/tts",
        ref_audio_path=ref_audio_path,
        ref_text_path=ref_text_path,
        ref_text="テスト",
        ref_lang="ja",
        text_lang="ja",
        timeout_seconds=1,
    )


def test_tts_ready_warmup_worker_calls_ensure_ready_success() -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    pytest.importorskip("PySide6.QtCore")
    from app.ui.pet_window import TTSReadyWarmupWorker

    events: list[tuple[str, str]] = []

    class FakeProvider:
        def ensure_ready(self) -> tuple[bool, str]:
            events.append(("called", ""))
            return True, "ready"

    worker = TTSReadyWarmupWorker(FakeProvider())  # type: ignore[arg-type]
    worker.succeeded.connect(lambda message: events.append(("succeeded", message)))
    worker.failed.connect(lambda message: events.append(("failed", message)))
    worker.finished.connect(lambda: events.append(("finished", "")))

    worker.run()

    assert events == [("called", ""), ("succeeded", "ready"), ("finished", "")]


def test_tts_ready_warmup_worker_reports_failure() -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    pytest.importorskip("PySide6.QtCore")
    from app.ui.pet_window import TTSReadyWarmupWorker

    events: list[tuple[str, str]] = []

    class FakeProvider:
        def ensure_ready(self) -> tuple[bool, str]:
            return False, "启动失败"

    worker = TTSReadyWarmupWorker(FakeProvider())  # type: ignore[arg-type]
    worker.succeeded.connect(lambda message: events.append(("succeeded", message)))
    worker.failed.connect(lambda message: events.append(("failed", message)))
    worker.finished.connect(lambda: events.append(("finished", "")))

    worker.run()

    assert events == [("failed", "启动失败"), ("finished", "")]


def test_tts_test_worker_keeps_provider_after_success(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    pytest.importorskip("PySide6.QtCore")
    import app.ui.settings_dialog as settings_dialog

    closed: list[bool] = []
    created: list[tuple[GPTSoVITSTTSSettings, Path | None, bool]] = []

    class FakeProvider:
        def __init__(self, settings: GPTSoVITSTTSSettings) -> None:
            self.settings = settings

        def ensure_ready(self) -> tuple[bool, str]:
            return True, "ok"

        def close(self) -> None:
            closed.append(True)

    import app.ui.settings.workers as settings_workers
    base_dir = Path("runtime-root")

    def fake_create_tts_provider(
        settings: GPTSoVITSTTSSettings,
        *,
        base_dir: Path | None = None,
        adopt_existing_service: bool = True,
    ) -> FakeProvider:
        created.append((settings, base_dir, adopt_existing_service))
        return FakeProvider(settings)

    monkeypatch.setattr(settings_workers, "create_tts_provider", fake_create_tts_provider)

    worker = settings_workers.TTSTestWorker(_minimal_tts_settings(), base_dir=base_dir)
    worker.run()

    assert closed == []
    assert created and created[0][1] == base_dir
    assert created[0][2] is False


def test_tts_test_worker_closes_provider_after_failure(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    pytest.importorskip("PySide6.QtCore")
    import app.ui.settings_dialog as settings_dialog

    events: list[str] = []
    created: list[tuple[GPTSoVITSTTSSettings, Path | None, bool]] = []

    class FakeProvider:
        def __init__(self, settings: GPTSoVITSTTSSettings) -> None:
            self.settings = settings

        def ensure_ready(self) -> tuple[bool, str]:
            return False, "启动失败"

        def close(self) -> None:
            events.append("closed")

    import app.ui.settings.workers as settings_workers
    base_dir = Path("runtime-root")

    def fake_create_tts_provider(
        settings: GPTSoVITSTTSSettings,
        *,
        base_dir: Path | None = None,
        adopt_existing_service: bool = True,
    ) -> FakeProvider:
        created.append((settings, base_dir, adopt_existing_service))
        return FakeProvider(settings)

    monkeypatch.setattr(settings_workers, "create_tts_provider", fake_create_tts_provider)

    worker = settings_workers.TTSTestWorker(_minimal_tts_settings(), base_dir=base_dir)
    worker.failed.connect(lambda _message: events.append("failed"))
    worker.finished.connect(lambda: events.append("finished"))
    worker.run()

    assert events == ["failed", "closed", "finished"]
    assert created and created[0][1] == base_dir
    assert created[0][2] is False


def _minimal_settings_window(pet_window_cls, settings_service, api_client, memory_store):  # type: ignore[no-untyped-def]
    import app.ui.pet_window as pet_window_module

    class CharacterProfileStub:
        id = "sakura"
        display_name = "Sakura"

    class CharacterRegistryStub:
        def get(self, character_id: str):  # type: ignore[no-untyped-def]
            assert character_id == "sakura"
            return CharacterProfileStub()

    class PluginManagerStub:
        tools_tabs = []

    class VoicePlaybackControllerStub:
        def set_provider(self, _provider):  # type: ignore[no-untyped-def]
            pass

    class MinimalSettingsWindow:
        show_settings = pet_window_cls.show_settings
        _on_settings_dialog_finished = pet_window_cls._on_settings_dialog_finished
        _activate_settings_dialog = pet_window_cls._activate_settings_dialog
        _preview_layout = pet_window_cls._preview_layout
        _retire_tts_provider = pet_window_cls._retire_tts_provider
        _apply_subtitle_display_speed = pet_window_cls._apply_subtitle_display_speed
        _apply_launch_at_login_settings = pet_window_cls._apply_launch_at_login_settings
        _apply_bubble_settings = pet_window_cls._apply_bubble_settings

        def _apply_stage_debug_overlay(self, enabled: bool, *, refresh: bool = False) -> None:
            self.stage_debug_overlay_applied = (enabled, refresh)

        def _apply_stage_collision_mask(self, enabled: bool, *, refresh: bool = False) -> None:
            self.stage_collision_mask_applied = (enabled, refresh)

        def _create_tts_provider_from_settings(self, _settings):  # type: ignore[no-untyped-def]
            return object()

        def _apply_layout_settings(  # type: ignore[no-untyped-def]
            self,
            *,
            portrait_scale_percent,
            control_panel_width,
            bubble_height,
            vertical_offset,
            input_bar_offset,
            persist: bool,
        ) -> None:
            self.portrait_scale_percent = portrait_scale_percent
            self.control_panel_width = control_panel_width
            self.bubble_height = bubble_height
            self.control_panel_vertical_offset = vertical_offset
            self.input_bar_offset = input_bar_offset
            self.layout_persisted = persist

        def _sync_proactive_care_timer(self) -> None:
            pass

        def _apply_character(self, profile):  # type: ignore[no-untyped-def]
            self.character_profile = profile

        def _warm_up_tts_playback(self, provider):  # type: ignore[no-untyped-def]
            self.warmed_tts_provider = provider

        def _save_system_config_values(self, section, values):  # type: ignore[no-untyped-def]
            self.settings_service.save_system_values(section, values)

    window = MinimalSettingsWindow()
    window.settings_service = settings_service
    window.api_client = api_client
    window.base_dir = Path(".")
    window.character_registry = CharacterRegistryStub()
    window.character_profile = CharacterProfileStub()
    window.proactive_care_settings = ProactiveCareSettings(screen_context_enabled=True)
    window.mcp_settings = MCPRuntimeSettings(windows_enabled=False)
    window.debug_log_settings = DebugLogSettings()
    window.startup_settings = StartupSettings()
    window.memory_store = memory_store
    window.plugin_manager = PluginManagerStub()
    window.portrait_scale_percent = 100
    window.control_panel_width = 640
    window.bubble_height = 128
    window.control_panel_vertical_offset = 0
    window.input_bar_offset = 0
    window.subtitle_typing_interval_ms = 35
    window.reply_segment_pause_ms = 100
    window.retired_tts_providers = []
    window.tts_provider = object()
    window.warmed_tts_provider = None
    window.voice_playback_controller = VoicePlaybackControllerStub()
    window.subtitle_controller = _DummySubtitleController()
    return window


def test_reply_segments_queue_while_current_segment_is_active() -> None:
    class DummyTTS:
        def __init__(self) -> None:
            self.spoken: list[str] = []

        def speak(self, text, tone, on_finished=None, on_started=None):  # type: ignore[no-untyped-def]
            self.spoken.append(text)

        def discard_prepared(self, _handle):  # type: ignore[no-untyped-def]
            pass

    from app.ui.subtitle_controller import SubtitleController
    from app.voice import VoicePlaybackController

    class DummyLabel:
        def clear(self) -> None:
            pass

        def setText(self, _text: str) -> None:
            pass

    ended = []
    controller = SubtitleController(
        DummyLabel(),  # type: ignore[arg-type]
        VoicePlaybackController(DummyTTS(), lambda *_args, **_kwargs: None),
        "zh",
        lambda *_args, **_kwargs: None,
        lambda _segment: None,
        lambda: ended.append("reply_completed"),
        lambda: True,
    )

    first = ChatSegment("先找到了", "中性", "先找到了")
    second = ChatSegment("执行前确认", "请求", "执行前确认")

    controller.show_segments([first])
    assert controller.current_segment == first

    controller.show_segments([second])
    assert controller.current_segment == first
    assert controller.queued_reply_segment_batches == [[second]]
    assert ended == []

    controller.current_segment_speech_done = True
    controller.current_segment_tts_done = True
    controller._end_interaction_if_reply_done()

    assert controller.current_segment == second
    assert controller.queued_reply_segment_batches == []
    assert ended == []


def test_action_resolution_clears_queued_reply_batches() -> None:
    from app.ui.subtitle_controller import SubtitleController
    from app.voice import VoicePlaybackController

    class DummyLabel:
        def clear(self) -> None:
            pass

        def setText(self, _text: str) -> None:
            pass

    class DummyTTS:
        def discard_prepared(self, _handle):  # type: ignore[no-untyped-def]
            pass

    stages = []
    controller = SubtitleController(
        DummyLabel(),  # type: ignore[arg-type]
        VoicePlaybackController(DummyTTS(), lambda stage, payload=None: stages.append((stage, payload))),
        "zh",
        lambda stage, payload=None: stages.append((stage, payload)),
        lambda _segment: None,
        lambda: None,
        lambda: True,
    )
    controller.queued_reply_segment_batches = [
        [ChatSegment("先打开运行窗口")],
        [ChatSegment("执行前确认")],
    ]

    controller.clear_queued_reply_segments_for_action_resolution()

    assert controller.queued_reply_segment_batches == []
    assert stages == [
        (
            "queued_reply_segments_cleared_for_action",
            {"cleared_batch_count": 2},
        )
    ]


def test_subtitle_controller_updates_display_speed() -> None:
    from app.ui.subtitle_controller import SubtitleController
    from app.voice import VoicePlaybackController

    class DummyLabel:
        def clear(self) -> None:
            pass

        def setText(self, _text: str) -> None:
            pass

    class DummyTTS:
        def discard_prepared(self, _handle):  # type: ignore[no-untyped-def]
            pass

    controller = SubtitleController(
        DummyLabel(),  # type: ignore[arg-type]
        VoicePlaybackController(DummyTTS(), lambda *_args, **_kwargs: None),
        "zh",
        lambda *_args, **_kwargs: None,
        lambda _segment: None,
        lambda: None,
        lambda: True,
        typing_interval_ms=70,
        segment_pause_ms=800,
    )

    assert controller.typing_interval_ms == 70
    assert controller.segment_pause_ms == 800
    assert controller.speech_timer.interval() == 70

    controller.set_display_speed(90, 1200)

    assert controller.typing_interval_ms == 90
    assert controller.segment_pause_ms == 1200
    assert controller.speech_timer.interval() == 90


def test_subtitle_controller_show_text_immediately_does_not_use_tts() -> None:
    from app.ui.subtitle_controller import SubtitleController
    from app.voice import VoicePlaybackController

    class DummyLabel:
        def __init__(self) -> None:
            self.text = ""
            self.cleared = False

        def clear(self) -> None:
            self.cleared = True

        def setText(self, text: str) -> None:
            self.text = text

    class FailingTTS:
        def speak(self, *_args, **_kwargs):  # type: ignore[no-untyped-def]
            raise AssertionError("立即显示历史文本不应调用 TTS")

        def discard_prepared(self, _handle):  # type: ignore[no-untyped-def]
            pass

    stages = []
    label = DummyLabel()
    controller = SubtitleController(
        label,  # type: ignore[arg-type]
        VoicePlaybackController(FailingTTS(), lambda *_args, **_kwargs: None),
        "zh",
        lambda stage, payload=None: stages.append((stage, payload)),
        lambda _segment: None,
        lambda: None,
        lambda: True,
    )

    controller.show_text_immediately("  第一段。  第二段。 ")

    assert label.text == "第一段。 第二段。"
    assert controller.speech_text == "第一段。 第二段。"
    assert controller.speech_index == len("第一段。 第二段。")
    assert stages[-1] == (
        "speech_text_shown_immediately",
        {"text": "第一段。 第二段。"},
    )


def test_subtitle_waiting_indicator_animates_and_stops_on_text() -> None:
    from app.ui.subtitle_controller import SubtitleController
    from app.voice import VoicePlaybackController

    class DummyLabel:
        def __init__(self) -> None:
            self.text = ""

        def clear(self) -> None:
            self.text = ""

        def setText(self, text: str) -> None:
            self.text = text

    class DummyTTS:
        def discard_prepared(self, _handle):  # type: ignore[no-untyped-def]
            pass

    _qt_app_or_skip()
    label = DummyLabel()
    controller = SubtitleController(
        label,  # type: ignore[arg-type]
        VoicePlaybackController(DummyTTS(), lambda *_args, **_kwargs: None),
        "zh",
        lambda *_args, **_kwargs: None,
        lambda _segment: None,
        lambda: None,
        lambda: True,
    )

    controller.start_waiting_indicator()
    assert label.text == "."
    assert controller.is_reply_sequence_active()

    frames = []
    for _ in range(8):
        controller._show_next_waiting_indicator_frame()
        frames.append(label.text)

    assert frames == ["..", "...", "....", ".....", "......", ".....", "......", "....."]

    controller.show_text_immediately("回复到了")

    assert not controller.waiting_indicator_active
    assert not controller.waiting_indicator_timer.isActive()
    assert label.text == "回复到了"


def test_subtitle_waiting_indicator_continues_until_tts_starts() -> None:
    from app.ui.subtitle_controller import SubtitleController
    from app.voice import VoicePlaybackController

    class DummyLabel:
        def __init__(self) -> None:
            self.text = ""

        def clear(self) -> None:
            self.text = ""

        def setText(self, text: str) -> None:
            self.text = text

    class DelayedTTS:
        def __init__(self) -> None:
            self.on_started = None
            self.on_finished = None

        def speak(self, _text, _tone, on_finished=None, on_started=None):  # type: ignore[no-untyped-def]
            self.on_started = on_started
            self.on_finished = on_finished

        def discard_prepared(self, _handle):  # type: ignore[no-untyped-def]
            pass

    _qt_app_or_skip()
    label = DummyLabel()
    tts = DelayedTTS()
    controller = SubtitleController(
        label,  # type: ignore[arg-type]
        VoicePlaybackController(tts, lambda *_args, **_kwargs: None),
        "zh",
        lambda *_args, **_kwargs: None,
        lambda _segment: None,
        lambda: None,
        lambda: True,
    )

    controller.start_waiting_indicator()
    controller._show_next_waiting_indicator_frame()
    controller.show_segments([ChatSegment("第一段回复", "中性", "第一段回复")])

    assert controller.waiting_indicator_active
    assert label.text == ".."
    assert controller.current_segment is not None
    assert tts.on_started is not None

    tts.on_started()

    assert not controller.waiting_indicator_active
    assert not controller.waiting_indicator_timer.isActive()
    assert controller.speech_text == "第一段回复"
    controller.cancel_reply_flow()


def test_send_message_injects_runtime_event_context_before_user_message() -> None:
    from app.agent.runtime_events import PET_REOPENED, RuntimeEvent, RuntimeEventQueue

    window, requests, history = _build_minimal_manual_screenshot_window("继续刚才的话题")
    window.pending_manual_screen_observation = None
    window.runtime_event_queue = RuntimeEventQueue()
    window.runtime_event_queue.push(
        RuntimeEvent(PET_REOPENED, metadata={"hidden_duration": 300})
    )

    window.send_message("test")

    assert len(requests) == 1
    request = requests[0]
    # 事件上下文应作为 system 消息插在历史与当前用户消息之间
    assert request[0]["role"] == "system"
    assert "重新打开" in request[0]["content"]
    assert request[-1] == {"role": "user", "content": "继续刚才的话题"}
    # 只进 request_messages：不污染 self.messages
    assert window.messages == [{"role": "user", "content": "继续刚才的话题"}]
    # 不污染聊天历史
    assert history == [("user", "继续刚才的话题")]
    # 队列已被一次性消费
    assert len(window.runtime_event_queue) == 0


def _qt_app_or_skip():  # type: ignore[no-untyped-def]
    """统一获取/创建 QApplication；stub 环境下跳过。"""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qtwidgets = pytest.importorskip("PySide6.QtWidgets")
    if not hasattr(qtwidgets, "QApplication") or not hasattr(qtwidgets, "QWidget"):
        pytest.skip("当前测试环境只提供了 PySide6 stub。")
    return qtwidgets.QApplication.instance() or qtwidgets.QApplication([])


def test_pet_input_stylesheet_reduces_white_overlay() -> None:
    stylesheet = build_pet_window_stylesheet(DEFAULT_THEME_SETTINGS)
    normal_start = stylesheet.index("#petInput {")
    focus_start = stylesheet.index("#petInput:focus")
    normal_block = stylesheet[normal_start:focus_start]
    focus_block = stylesheet[focus_start:focus_start + 200]
    # 普通态/聚焦态白底 alpha 应明显低于原始厚白（96/132），靠背后强模糊提供玻璃质感。
    assert ", 55)" in normal_block
    assert ", 90)" in focus_block


def test_pet_input_stylesheet_has_solid_visual_effect_state() -> None:
    stylesheet = build_pet_window_stylesheet(DEFAULT_THEME_SETTINGS)

    assert '#inputBar[visualEffectMode="solid"]' in stylesheet
    assert '#petInput[visualEffectMode="solid"]' in stylesheet
    assert '#petInput[visualEffectMode="solid"]:focus' in stylesheet


def test_pet_input_stylesheet_has_waiting_send_button_state() -> None:
    stylesheet = build_pet_window_stylesheet(DEFAULT_THEME_SETTINGS)

    assert '#petInput[replyWaiting="true"]' not in stylesheet
    assert "waitingBreath" not in stylesheet
    assert '#sendButton[replyWaiting="true"]:disabled' in stylesheet


def test_pet_window_applies_visual_effect_dynamic_property() -> None:
    _qt_app_or_skip()
    from PySide6.QtWidgets import QFrame, QLineEdit

    from app.ui.pet_window import PetWindow
    from app.ui.window_backdrop import VisualEffectMode

    window = PetWindow.__new__(PetWindow)
    window.input_bar = QFrame()
    window.input_edit = QLineEdit(window.input_bar)

    PetWindow._apply_input_bar_visual_effect_property(window, VisualEffectMode.SOLID)

    assert window.input_bar.property("visualEffectMode") == VisualEffectMode.SOLID
    assert window.input_edit.property("visualEffectMode") == VisualEffectMode.SOLID

    window.input_bar.deleteLater()


def test_sync_input_bar_backdrop_toggles_software_blur_layer_by_mode() -> None:
    """单窗口重构后：纯色模式不挂软件模糊背景层，高斯模式挂载并绑定截图回调。"""
    from app.ui.pet_window import PetWindow
    from app.ui.theme import ThemeSettings
    from app.ui.window_backdrop import VisualEffectMode

    class CardStub:
        def __init__(self) -> None:
            self.layer = "untouched"

        def set_background_layer(self, layer) -> None:  # type: ignore[no-untyped-def]
            self.layer = layer

    class AnimatorStub:
        def __init__(self) -> None:
            self.before_show = "untouched"

        def set_before_show(self, callback) -> None:  # type: ignore[no-untyped-def]
            self.before_show = callback

    blur_bg = object()

    def _make_window(mode: str):  # type: ignore[no-untyped-def]
        window = PetWindow.__new__(PetWindow)
        window.theme_settings = ThemeSettings(visual_effect_mode=mode)
        window.input_card = CardStub()
        window.input_blur_background = blur_bg
        window.input_bar = None
        window.input_edit = None
        window.input_bar_animator = AnimatorStub()
        return window

    # 纯色：不挂背景层、无截图回调。
    solid = _make_window(VisualEffectMode.SOLID)
    PetWindow._sync_input_bar_backdrop(solid)
    assert solid.input_card.layer is None
    assert solid.input_bar_animator.before_show is None

    # 高斯模糊：挂软件模糊背景层 + 截图回调。
    blur = _make_window(VisualEffectMode.GAUSSIAN_BLUR)
    PetWindow._sync_input_bar_backdrop(blur)
    assert blur.input_card.layer is blur_bg
    assert blur.input_bar_animator.before_show == blur._refresh_input_blur_background


def test_input_bar_windows_acrylic_config_degrades_to_software_blur(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """旧 windows_acrylic 配置不再回显原生亚克力，运行时降级为高斯模糊。"""
    import app.ui.pet_window as pet_window_module
    from app.ui.pet_window import PetWindow
    from app.ui.theme import ThemeSettings
    from app.ui.window_backdrop import VisualEffectMode

    monkeypatch.setattr(pet_window_module.sys, "platform", "win32")

    class CardStub:
        def __init__(self) -> None:
            self.layer = "untouched"

        def set_background_layer(self, layer) -> None:  # type: ignore[no-untyped-def]
            self.layer = layer

    class AnimatorStub:
        def __init__(self) -> None:
            self.before_show = "untouched"
            self.after_show = "untouched"
            self.before_hide = "untouched"

        def set_before_show(self, callback) -> None:  # type: ignore[no-untyped-def]
            self.before_show = callback

        def set_after_show(self, callback) -> None:  # type: ignore[no-untyped-def]
            self.after_show = callback

        def set_before_hide(self, callback) -> None:  # type: ignore[no-untyped-def]
            self.before_hide = callback

    blur_bg = object()
    window = PetWindow.__new__(PetWindow)
    window.theme_settings = ThemeSettings(visual_effect_mode=VisualEffectMode.WINDOWS_ACRYLIC)
    window.input_card = CardStub()
    window.input_blur_background = blur_bg
    window.input_bar = None
    window.input_edit = None
    window.input_bar_animator = AnimatorStub()

    PetWindow._sync_input_bar_backdrop(window)

    assert PetWindow._input_bar_visual_effect_mode(window) == VisualEffectMode.GAUSSIAN_BLUR
    assert window.input_card.layer is blur_bg
    assert window.input_bar_animator.before_show == window._refresh_input_blur_background
    assert window.input_bar_animator.after_show is None
    assert window.input_bar_animator.before_hide is None


def test_sync_input_bar_backdrop_uses_macos_native_backdrop(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """macOS 原生毛玻璃模式不走软件截图模糊，而是挂载 NSVisualEffectView backdrop。"""
    import app.ui.pet_window as pet_window_module
    from app.ui.pet_window import PetWindow
    from app.ui.theme import ThemeSettings
    from app.ui.window_backdrop import VisualEffectMode

    monkeypatch.setattr(pet_window_module.sys, "platform", "darwin")

    class CardStub:
        def __init__(self) -> None:
            self.layer = "untouched"
            self.visible = True

        def set_background_layer(self, layer) -> None:  # type: ignore[no-untyped-def]
            self.layer = layer

        def isVisible(self) -> bool:  # noqa: N802 - Qt API 兼容命名。
            return self.visible

    class BackdropStub:
        def __init__(self) -> None:
            self.applied: list[object] = []
            self.removed: list[object] = []

        def apply(self, window, _tint) -> None:  # type: ignore[no-untyped-def]
            self.applied.append(window)

        def remove(self, window) -> None:  # type: ignore[no-untyped-def]
            self.removed.append(window)

    class AnimatorStub:
        def __init__(self) -> None:
            self.before_show = "untouched"
            self.after_show = "untouched"
            self.before_hide = "untouched"

        def set_before_show(self, callback) -> None:  # type: ignore[no-untyped-def]
            self.before_show = callback

        def set_after_show(self, callback) -> None:  # type: ignore[no-untyped-def]
            self.after_show = callback

        def set_before_hide(self, callback) -> None:  # type: ignore[no-untyped-def]
            self.before_hide = callback

    blur_bg = object()
    backdrop = BackdropStub()
    window = PetWindow.__new__(PetWindow)
    window.theme_settings = ThemeSettings(visual_effect_mode=VisualEffectMode.MACOS_VISUAL_EFFECT)
    window.input_card = CardStub()
    window.input_blur_background = blur_bg
    window.input_native_backdrop = backdrop
    window.input_bar = None
    window.input_edit = None
    window.input_bar_animator = AnimatorStub()

    PetWindow._sync_input_bar_backdrop(window)

    assert window.input_card.layer is None
    assert window.input_bar_animator.before_show is None
    assert window.input_bar_animator.after_show == window._apply_input_bar_native_backdrop
    assert window.input_bar_animator.before_hide == window._remove_input_bar_native_backdrop
    assert backdrop.applied == [window.input_card]

    window.theme_settings = ThemeSettings(visual_effect_mode=VisualEffectMode.GAUSSIAN_BLUR)
    PetWindow._sync_input_bar_backdrop(window)

    assert window.input_card.layer is blur_bg
    assert window.input_bar_animator.before_show == window._refresh_input_blur_background
    assert window.input_bar_animator.after_show is None
    assert window.input_bar_animator.before_hide is None
    assert backdrop.removed == [window.input_card]


def test_local_rect_to_global_keeps_size_and_uses_main_window_origin() -> None:
    _qt_app_or_skip()
    from PySide6.QtCore import QPoint, QRect
    from PySide6.QtWidgets import QWidget
    from app.ui.pet_window import PetWindow

    host = QWidget()
    host.move(100, 200)
    rect = QRect(10, 20, 300, 128)

    # 子窗口定位：本地矩形按主窗口原点转换为全局坐标，尺寸不变。
    result = PetWindow._local_rect_to_global(host, rect)  # type: ignore[arg-type]

    assert result.size() == rect.size()
    assert result.topLeft() == host.mapToGlobal(QPoint(10, 20))
    host.deleteLater()


def test_cursor_in_pet_region_requires_exposed_pet_window(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    qtcore = pytest.importorskip("PySide6.QtCore")
    import app.ui.pet_window as pet_window_module
    from app.ui.pet_window import PetWindow

    class MinimalWindow:
        _cursor_in_pet_region = PetWindow._cursor_in_pet_region
        _cursor_over_exposed_pet_window = PetWindow._cursor_over_exposed_pet_window
        _input_bar_foreground_allowed = PetWindow._input_bar_foreground_allowed
        always_on_top_enabled = True

        def _any_dialog_open(self) -> bool:
            return False

        def isVisible(self) -> bool:  # noqa: N802 - Qt API 兼容命名。
            return True

        def frameGeometry(self):  # type: ignore[no-untyped-def]
            return qtcore.QRect(0, 0, 120, 80)

        def windowHandle(self):  # type: ignore[no-untyped-def]
            return object()

        def isAncestorOf(self, _widget) -> bool:  # noqa: N802 - Qt API 兼容命名。
            return False

    pos = qtcore.QPoint(20, 20)
    window = MinimalWindow()
    monkeypatch.setattr(pet_window_module.QCursor, "pos", lambda: pos)
    monkeypatch.setattr(pet_window_module.QApplication, "widgetAt", lambda _pos: None)
    monkeypatch.setattr(pet_window_module.QApplication, "topLevelAt", lambda _pos: None, raising=False)

    # 坐标在窗口矩形内，但光标下方实际不是桌宠时，不应触发 hover 浮现。
    assert window._cursor_in_pet_region() is False


def test_cursor_in_pet_region_accepts_exposed_child_widget(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    qtcore = pytest.importorskip("PySide6.QtCore")
    import app.ui.pet_window as pet_window_module
    from app.ui.pet_window import PetWindow

    class MinimalWindow:
        _cursor_in_pet_region = PetWindow._cursor_in_pet_region
        _cursor_over_exposed_pet_window = PetWindow._cursor_over_exposed_pet_window
        _input_bar_foreground_allowed = PetWindow._input_bar_foreground_allowed
        always_on_top_enabled = True

        def _any_dialog_open(self) -> bool:
            return False

        def isVisible(self) -> bool:  # noqa: N802 - Qt API 兼容命名。
            return True

        def frameGeometry(self):  # type: ignore[no-untyped-def]
            return qtcore.QRect(0, 0, 120, 80)

        def isAncestorOf(self, _widget) -> bool:  # noqa: N802 - Qt API 兼容命名。
            return False

    class ChildWidget:
        def __init__(self, window: MinimalWindow) -> None:
            self._window = window

        def window(self) -> MinimalWindow:
            return self._window

    pos = qtcore.QPoint(20, 20)
    window = MinimalWindow()
    child = ChildWidget(window)
    monkeypatch.setattr(pet_window_module.QCursor, "pos", lambda: pos)
    monkeypatch.setattr(pet_window_module.QApplication, "widgetAt", lambda _pos: child)

    assert window._cursor_in_pet_region() is True


def test_cursor_in_pet_region_blocks_non_topmost_background_window(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    qtcore = pytest.importorskip("PySide6.QtCore")
    import app.ui.pet_window as pet_window_module
    from app.ui.pet_window import PetWindow

    class MinimalWindow:
        _cursor_in_pet_region = PetWindow._cursor_in_pet_region
        _cursor_over_exposed_pet_window = PetWindow._cursor_over_exposed_pet_window
        _input_bar_foreground_allowed = PetWindow._input_bar_foreground_allowed
        _is_pet_foreground_window = PetWindow._is_pet_foreground_window
        always_on_top_enabled = False

        def _any_dialog_open(self) -> bool:
            return False

        def isVisible(self) -> bool:  # noqa: N802 - Qt API 兼容命名。
            return True

        def frameGeometry(self):  # type: ignore[no-untyped-def]
            return qtcore.QRect(0, 0, 120, 80)

        def isAncestorOf(self, _widget) -> bool:  # noqa: N802 - Qt API 兼容命名。
            return False

        def isActiveWindow(self) -> bool:  # noqa: N802 - Qt API 兼容命名。
            return False

    class ChildWidget:
        def __init__(self, window: MinimalWindow) -> None:
            self._window = window

        def window(self) -> MinimalWindow:
            return self._window

    pos = qtcore.QPoint(20, 20)
    window = MinimalWindow()
    child = ChildWidget(window)
    monkeypatch.setattr(pet_window_module.sys, "platform", "linux")
    monkeypatch.setattr(pet_window_module.QCursor, "pos", lambda: pos)
    monkeypatch.setattr(pet_window_module.QApplication, "activeWindow", lambda: None)
    monkeypatch.setattr(pet_window_module.QApplication, "widgetAt", lambda _pos: child)

    assert window._cursor_in_pet_region() is False


def test_cursor_in_pet_region_allows_non_topmost_foreground_window(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    qtcore = pytest.importorskip("PySide6.QtCore")
    import app.ui.pet_window as pet_window_module
    from app.ui.pet_window import PetWindow

    class MinimalWindow:
        _cursor_in_pet_region = PetWindow._cursor_in_pet_region
        _cursor_over_exposed_pet_window = PetWindow._cursor_over_exposed_pet_window
        _input_bar_foreground_allowed = PetWindow._input_bar_foreground_allowed
        _is_pet_foreground_window = PetWindow._is_pet_foreground_window
        always_on_top_enabled = False

        def _any_dialog_open(self) -> bool:
            return False

        def isVisible(self) -> bool:  # noqa: N802 - Qt API 兼容命名。
            return True

        def frameGeometry(self):  # type: ignore[no-untyped-def]
            return qtcore.QRect(0, 0, 120, 80)

        def isAncestorOf(self, _widget) -> bool:  # noqa: N802 - Qt API 兼容命名。
            return False

    class ChildWidget:
        def __init__(self, window: MinimalWindow) -> None:
            self._window = window

        def window(self) -> MinimalWindow:
            return self._window

    pos = qtcore.QPoint(20, 20)
    window = MinimalWindow()
    child = ChildWidget(window)
    monkeypatch.setattr(pet_window_module.sys, "platform", "linux")
    monkeypatch.setattr(pet_window_module.QCursor, "pos", lambda: pos)
    monkeypatch.setattr(pet_window_module.QApplication, "activeWindow", lambda: child)
    monkeypatch.setattr(pet_window_module.QApplication, "widgetAt", lambda _pos: child)

    assert window._cursor_in_pet_region() is True


def test_input_bar_pinned_requires_foreground_when_not_topmost() -> None:
    from app.ui.pet_window import PetWindow

    class InputStub:
        def hasFocus(self) -> bool:  # noqa: N802 - Qt API 兼容命名。
            return False

        def text(self) -> str:
            return "未发送文本"

    class MinimalWindow:
        _input_bar_pinned = PetWindow._input_bar_pinned
        _input_bar_foreground_allowed = PetWindow._input_bar_foreground_allowed
        always_on_top_enabled = False
        input_edit = InputStub()
        reply_waiting_ui_active = False
        pending_tool_action = None

        def _any_dialog_open(self) -> bool:
            return False

        def _is_pet_foreground_window(self) -> bool:
            return False

    assert MinimalWindow()._input_bar_pinned() is False


def test_input_bar_animator_visibility_follows_hover_and_pin() -> None:
    _qt_app_or_skip()
    from PySide6.QtWidgets import QGraphicsOpacityEffect, QWidget
    from app.ui.input_bar_animator import InputBarAnimator

    bar = QWidget()
    card = QWidget()
    effect = QGraphicsOpacityEffect(card)
    pinned = {"value": False}
    hover = {"value": False}
    animator = InputBarAnimator(
        bar,
        card,
        effect,
        lambda: pinned["value"],
        lambda: hover["value"],
    )

    animator._hover = False
    assert animator._target_visible() is False

    animator._hover = True
    assert animator._target_visible() is True

    # 鼠标移开但 pinned（有文本/待确认动作）时仍保持可见，不被收起。
    animator._hover = False
    pinned["value"] = True
    assert animator._target_visible() is True

    bar.deleteLater()
    card.deleteLater()


def test_input_bar_animator_send_feedback_starts_animation() -> None:
    _qt_app_or_skip()
    from PySide6.QtWidgets import QGraphicsOpacityEffect, QWidget
    from app.ui.input_bar_animator import InputBarAnimator

    bar = QWidget()
    card = QWidget()
    effect = QGraphicsOpacityEffect(card)
    animator = InputBarAnimator(bar, card, effect, lambda: False, lambda: False)

    # 脉冲复用卡片 effect，仅在卡片可见时触发。
    animator._shown = True
    animator.play_send_feedback()
    assert animator._send_anim is not None

    bar.deleteLater()
    card.deleteLater()


class _StubVoicePlayback:
    def discard_prepared(self) -> None:
        pass

    def speak_segment(self, *args, **kwargs) -> None:  # type: ignore[no-untyped-def]
        pass

    def prepare_next(self, *args, **kwargs) -> None:  # type: ignore[no-untyped-def]
        pass


def _build_subtitle_controller(effect):  # type: ignore[no-untyped-def]
    from PySide6.QtWidgets import QLabel
    from app.ui.subtitle_controller import SubtitleController

    return SubtitleController(
        QLabel(),
        _StubVoicePlayback(),
        "zh",
        lambda *args: None,
        lambda *args: None,
        lambda: None,
        lambda: False,
        bubble_opacity_effect=effect,
    )


def test_subtitle_cancel_without_transition_keeps_bubble_opaque() -> None:
    _qt_app_or_skip()
    from PySide6.QtWidgets import QGraphicsOpacityEffect

    effect = QGraphicsOpacityEffect()
    effect.setOpacity(1.0)
    controller = _build_subtitle_controller(effect)

    # 发送占位等高频路径 transition=False，不应触发气泡脉冲。
    controller.cancel_reply_flow("......", transition=False)

    assert controller._bubble_fade_anim is None
    assert effect.opacity() == 1.0


def test_subtitle_segment_pulse_creates_bubble_animation() -> None:
    _qt_app_or_skip()
    from PySide6.QtWidgets import QGraphicsOpacityEffect

    effect = QGraphicsOpacityEffect()
    effect.setOpacity(1.0)
    controller = _build_subtitle_controller(effect)

    # 分段台词开始（pulse=True）应创建一次气泡浮现脉冲动画。
    controller.set_speech("一段台词", pulse=True)

    assert controller._bubble_fade_anim is not None


def _make_character_profile(theme_settings: ThemeSettings | None, theme_source: str):  # type: ignore[no-untyped-def]
    from app.config.character_loader import CharacterProfile

    return CharacterProfile(
        id="test",
        display_name="Test",
        package_dir=Path("."),
        card_path=Path("card.md"),
        initial_message="",
        default_portrait_path=Path("portrait.png"),
        theme_settings=theme_settings,
        theme_source=theme_source,  # type: ignore[arg-type]
    )


def test_merge_theme_with_character_keeps_user_level_fields() -> None:
    # 角色包主题只贡献配色；visual_effect_mode 和 ai_enabled 是用户级偏好，必须沿用已保存值。
    from app.config.character_loader import THEME_SOURCE_PACKAGE
    from app.ui.theme import merge_theme_with_character

    saved = ThemeSettings(visual_effect_mode="macos_visual_effect", primary_color="#aa11bb", ai_enabled=True)
    package_theme = ThemeSettings(primary_color="#123456")
    profile = _make_character_profile(package_theme, THEME_SOURCE_PACKAGE)

    merged = merge_theme_with_character(saved, profile)

    assert merged.visual_effect_mode == "macos_visual_effect"
    assert merged.ai_enabled is True
    assert merged.primary_color == "#123456"


def test_merge_theme_with_character_compat_default_returns_saved() -> None:
    from app.config.character_loader import THEME_SOURCE_COMPAT_DEFAULT
    from app.ui.theme import merge_theme_with_character

    saved = ThemeSettings(visual_effect_mode="windows_acrylic", primary_color="#aa11bb")
    profile = _make_character_profile(ThemeSettings(), THEME_SOURCE_COMPAT_DEFAULT)

    merged = merge_theme_with_character(saved, profile)

    assert merged.visual_effect_mode == "windows_acrylic"
    assert merged.primary_color == "#aa11bb"


def test_merge_theme_with_character_without_profile() -> None:
    from app.ui.theme import merge_theme_with_character

    saved = ThemeSettings(visual_effect_mode="solid")

    assert merge_theme_with_character(saved, None).visual_effect_mode == "solid"


def test_character_theme_round_trip_never_stores_visual_effect_mode() -> None:
    # character.json 的 theme 块设计上不携带 visual_effect_mode（用户级/角色级分离）。
    from app.config.character_loader import character_theme_from_mapping, character_theme_to_mapping

    theme = ThemeSettings(primary_color="#123456", visual_effect_mode="macos_visual_effect")
    mapping = character_theme_to_mapping(theme)

    assert "visual_effect_mode" not in mapping

    restored, source, missing = character_theme_from_mapping(mapping)
    assert restored.visual_effect_mode == "gaussian_blur"
    assert restored.primary_color == "#123456"
    assert source == "package"
    assert missing is False
