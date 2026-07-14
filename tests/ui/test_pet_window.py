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
from app.agent.runtime_limits import RuntimeLoopSettings
from app.config.settings_service import BackchannelSettings, BubbleSettings, DebugLogSettings, StartupSettings
from app.llm.api_client import ApiSettings
from app.agent import AgentEvent, AgentResult
from app.llm.chat_reply import ChatReply, ChatSegment
from app.ui.portrait_utils import portrait_kind_key, should_crossfade_portrait
from app.ui.theme import (
    DEFAULT_THEME_SETTINGS,
    ThemeSettings,
    build_message_box_stylesheet,
    build_pet_window_stylesheet,
    build_settings_dialog_stylesheet,
)
from app.agent.screen_awareness import (
    SCREEN_AWARENESS_CONTEXT_HISTORY_MARKER,
    ScreenAwarenessSettings,
)
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


class _DummyPortraitLabel:
    def __init__(self) -> None:
        self.visible = True

    def hide(self) -> None:
        self.visible = False

    def show(self) -> None:
        self.visible = True


def test_apply_character_syncs_memory_curator_prompt(monkeypatch) -> None:
    import app.ui.pet_window as pet_window_module
    from app.ui.pet_window import PetWindow

    events: list[tuple[str, object]] = []

    class ProfileStub:
        id = "new-character"
        display_name = "New Character"
        initial_message = "你好"
        reply_tones = ["calm"]
        portrait_choices = ["default"]

    class PreviousProfileStub:
        id = "old-character"

    class MemoryCuratorStub:
        def set_system_prompt(self, system_prompt: str) -> None:
            events.append(("curator_prompt", system_prompt))

    class MemoryStoreStub:
        def set_scope(self, scope: str) -> None:
            events.append(("memory_scope", scope))

    class AgentRuntimeStub:
        def update_character(  # type: ignore[no-untyped-def]
            self,
            system_prompt,
            reply_tones,
            portrait_choices,
            *,
            character_id=None,
            character_name=None,
        ):
            events.append(
                (
                    "runtime_character",
                    (system_prompt, reply_tones, portrait_choices, character_id, character_name),
                )
            )

        def set_history_store(self, history_store):  # type: ignore[no-untyped-def]
            events.append(("history_store", history_store))

    class TextSink:
        def setText(self, text: str) -> None:
            events.append(("label", text))

        def setPlaceholderText(self, text: str) -> None:
            events.append(("placeholder", text))

    class PortraitControllerStub:
        def set_profile(self, profile):  # type: ignore[no-untyped-def]
            events.append(("portrait", profile.id))

    class SubtitleControllerStub:
        def cancel_reply_flow(self, initial_message: str) -> None:
            events.append(("subtitle", initial_message))

    class MinimalWindow:
        _apply_character = PetWindow._apply_character

        def setWindowTitle(self, title: str) -> None:
            events.append(("title", title))

        def _normal_input_placeholder_text(self, profile):  # type: ignore[no-untyped-def]
            return f"Message {profile.display_name}"

        def _portrait_anchor_global(self):  # type: ignore[no-untyped-def]
            return "anchor"

        def updatesEnabled(self) -> bool:
            return True

        def setUpdatesEnabled(self, enabled: bool) -> None:
            events.append(("updates", enabled))

        def _apply_pet_layout(self, *, anchor_global):  # type: ignore[no-untyped-def]
            events.append(("layout", anchor_global))

        def _load_backchannel_manifest_for(self, profile):  # type: ignore[no-untyped-def]
            events.append(("backchannel", profile.id))

        def _create_history_store(self, profile):  # type: ignore[no-untyped-def]
            return f"history:{profile.id}"

        def _create_runtime_event_log(self, profile):  # type: ignore[no-untyped-def]
            return f"events:{profile.id}"

        def _create_visual_observation_store(self, profile):  # type: ignore[no-untyped-def]
            return f"visual:{profile.id}"

        def _load_reply_history_from_store(self) -> None:
            events.append(("reply_history", None))

        def _collapse_auto_fit_bubble_height(self) -> None:
            events.append(("collapse", None))

        def _emit_plugin_event(self, event_type, payload, *, source):  # type: ignore[no-untyped-def]
            events.append(("plugin", (event_type, payload, source)))

    monkeypatch.setattr(
        pet_window_module,
        "load_character_system_prompt",
        lambda _profile: "新角色人格卡",
    )

    window = MinimalWindow()
    window.character_profile = PreviousProfileStub()
    window.memory_curator = MemoryCuratorStub()
    window.memory_store = MemoryStoreStub()
    window.agent_runtime = AgentRuntimeStub()
    window.name_label = TextSink()
    window.input_edit = TextSink()
    window.portrait_controller = PortraitControllerStub()
    window.history_window = None
    window.subtitle_controller = SubtitleControllerStub()
    window.messages = ["旧消息"]

    window._apply_character(ProfileStub())

    assert window.system_prompt == "新角色人格卡"
    assert ("curator_prompt", "新角色人格卡") in events
    assert ("memory_scope", "new-character") in events
    assert (
        "runtime_character",
        ("新角色人格卡", ["calm"], ["default"], "new-character", "New Character"),
    ) in events


def test_start_memory_curation_sets_context_before_spawning_worker(
    pet_window,
    monkeypatch,
    tmp_path,
) -> None:  # type: ignore[no-untyped-def]
    from app.storage.chat_history import ChatHistoryEntry
    from app.ui.pet_window import _MemoryCurationRunContext

    _configure_memory_curation_window(pet_window, tmp_path)
    captured = {}

    def capture(worker, **_kwargs):  # type: ignore[no-untyped-def]
        captured["run_at_spawn"] = pet_window.memory_curation_run
        captured["worker"] = worker

    monkeypatch.setattr(pet_window.resource_manager, "spawn_qt_worker", capture)
    started_prompt = pet_window.system_prompt
    entries = [
        ChatHistoryEntry("2026-07-11T10:00:00+08:00", "user", "旧角色对话")
    ]

    pet_window._start_memory_curation(
        entries,
        mode="auto",
        target_history_count=8,
        consumed_turns=3,
    )

    run = pet_window.memory_curation_run
    assert run == _MemoryCurationRunContext(
        mode="auto",
        character_id=pet_window.character_profile.id,
        target_history_count=8,
        consumed_turns=3,
    )
    assert captured["run_at_spawn"] is run
    pet_window.memory_store.set_scope("new-character")
    pet_window.memory_curator.set_system_prompt("新角色人格卡")
    assert captured["worker"].curator.system_prompt == started_prompt
    assert captured["worker"].curator.memory_store.scope_id == run.character_id


def test_renderer_replaces_default_portrait_suppresses_png_labels() -> None:
    from app.ui.pet_window import PetWindow

    window = type("WindowStub", (), {})()
    window.label = _DummyPortraitLabel()
    window.portrait_transition_label = _DummyPortraitLabel()
    window._set_portrait_overlay_suppressed = (  # type: ignore[attr-defined]
        lambda suppressed: PetWindow._set_portrait_overlay_suppressed(window, suppressed)
    )
    window.renderer_manager = type(
        "ManagerStub",
        (),
        {"is_overlay_active": True, "replaces_default_portrait": True},
    )()

    PetWindow._resuppress_portrait_if_renderer_active(window)

    assert not window.label.visible
    assert not window.portrait_transition_label.visible


def test_renderer_without_replacement_keeps_png_labels_visible() -> None:
    from app.ui.pet_window import PetWindow

    window = type("WindowStub", (), {})()
    window.label = _DummyPortraitLabel()
    window.portrait_transition_label = _DummyPortraitLabel()
    window._set_portrait_overlay_suppressed = (  # type: ignore[attr-defined]
        lambda suppressed: PetWindow._set_portrait_overlay_suppressed(window, suppressed)
    )
    window.renderer_manager = type(
        "ManagerStub",
        (),
        {"is_overlay_active": True, "replaces_default_portrait": False},
    )()

    PetWindow._resuppress_portrait_if_renderer_active(window)

    assert window.label.visible
    assert window.portrait_transition_label.visible


def test_close_renderer_manager_closes_renderer_and_restores_png_label() -> None:
    from app.ui.pet_window import PetWindow

    class ManagerStub:
        def __init__(self) -> None:
            self.closed = False

        def close(self) -> None:
            self.closed = True

    manager = ManagerStub()
    window = type("WindowStub", (), {})()
    window.label = _DummyPortraitLabel()
    window.portrait_transition_label = _DummyPortraitLabel()
    window._set_portrait_overlay_suppressed = (  # type: ignore[attr-defined]
        lambda suppressed: PetWindow._set_portrait_overlay_suppressed(window, suppressed)
    )
    window.renderer_manager = manager
    window._stop_gaze_tracking = lambda: None  # type: ignore[attr-defined]
    window.label.hide()
    window.portrait_transition_label.hide()

    PetWindow._close_renderer_manager(window)

    assert manager.closed
    assert window.label.visible
    # 转场 label 只在需要时显示，关闭 renderer 不应强制显示。
    assert not window.portrait_transition_label.visible


def test_activate_renderer_manager_assigns_before_gaze_tracking() -> None:
    from app.ui.pet_window import PetWindow

    manager = object()
    window = type("WindowStub", (), {})()
    window.renderer_manager = None
    window.closed = False
    window.gaze_started = False

    def close_renderer_manager() -> None:
        window.closed = True
        window.renderer_manager = None

    def init_renderer_manager() -> object:
        return manager

    def start_gaze_tracking() -> None:
        assert window.renderer_manager is manager
        window.gaze_started = True

    window._close_renderer_manager = close_renderer_manager  # type: ignore[attr-defined]
    window._init_renderer_manager = init_renderer_manager  # type: ignore[attr-defined]
    window._start_gaze_tracking = start_gaze_tracking  # type: ignore[attr-defined]

    assert PetWindow._activate_renderer_manager(window) is manager
    assert window.closed
    assert window.gaze_started
    assert window.renderer_manager is manager


def test_pet_window_menu_keeps_only_allowed_checkable_switches() -> None:
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
        _prepare_secondary_window = pet_window_module.PetWindow._prepare_secondary_window
        _present_registered_secondary_window = (
            pet_window_module.PetWindow._present_registered_secondary_window
        )
        _register_secondary_window = pet_window_module.PetWindow._register_secondary_window
        _sync_secondary_window_state = pet_window_module.PetWindow._sync_secondary_window_state
        _is_secondary_window_visible = pet_window_module.PetWindow._is_secondary_window_visible
        _set_secondary_windows_topmost_suppressed = (
            pet_window_module.PetWindow._set_secondary_windows_topmost_suppressed
        )

    monkeypatch.setattr(pet_window_module, "RuntimeLogWindow", RuntimeLogWindowStub)

    app = qtwidgets.QApplication.instance() or qtwidgets.QApplication([])
    host = Host()
    host.theme_settings = DEFAULT_THEME_SETTINGS
    host.runtime_log_window = None
    host.history_window = None

    host.show_runtime_log()

    assert events == ["theme", "refresh:True", "show", "raise", "activate"]
    assert host.runtime_log_window.kwargs["parent"] is host

    host.deleteLater()
    app.processEvents()


def test_runtime_log_window_is_non_modal() -> None:
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
    assert "selection-dot.svg" in window.styleSheet()
    assert DEFAULT_THEME_SETTINGS.page_background_color in window.styleSheet()

    window.close()
    window.deleteLater()
    app.processEvents()


def test_runtime_log_window_collapses_consecutive_duplicate_rows() -> None:
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
    assert len(warnings) == 1
    assert warnings[0][0] == "记忆模型下载失败"
    assert "发生了什么" in warnings[0][1]
    assert "处理建议" in warnings[0][1]
    assert "诊断信息（截图时请保留）" in warnings[0][1]
    assert "failed message" in warnings[0][1]
    assert single_shots == [(pet_window_module.MEMORY_STATUS_DISPLAY_MS, window._restore_memory_status_speech)]


def test_auto_memory_turn_log_includes_trigger_progress(monkeypatch, tmp_path) -> None:  # type: ignore[no-untyped-def]
    import app.ui.pet_window as pet_window_module
    from app.agent.memory_curator import MemoryCurationSettings, MemoryCurationState
    from app.ui.pet_window import PetWindow

    logs: list[tuple[str, str, dict[str, object] | None]] = []
    monkeypatch.setattr(
        pet_window_module,
        "log_event",
        lambda channel, message, payload=None, **_kwargs: logs.append((channel, message, payload)),
    )
    window = type("WindowStub", (), {})()
    window.memory_curation_settings = MemoryCurationSettings(enabled=True, trigger_turns=3)
    window.memory_curation_state = MemoryCurationState(tmp_path / "memory_curation_state.json")

    PetWindow._record_completed_memory_turn(window)

    assert logs == [
        (
            "Memory",
            "自动记忆轮次已累计",
            {"pending_turns": 1, "trigger_turns": 3, "remaining_turns": 2},
        )
    ]


class _MemoryRetryHistoryStore:
    def __init__(self, entries) -> None:  # type: ignore[no-untyped-def]
        self.entries = list(entries)

    def load(self):  # type: ignore[no-untyped-def]
        return list(self.entries)


def _configure_memory_curation_window(
    pet_window,
    tmp_path,
    *,
    trigger_turns: int = 3,
    entries=None,
):  # type: ignore[no-untyped-def]
    from app.agent.memory_curator import MemoryCurationSettings, MemoryCurationState
    from app.storage.chat_history import ChatHistoryEntry

    if entries is None:
        entries = [
            ChatHistoryEntry("2026-06-28T21:09:14+08:00", "user", "第一轮"),
            ChatHistoryEntry("2026-06-28T21:09:20+08:00", "assistant", "第二轮"),
        ]
    pet_window.memory_curation_settings = MemoryCurationSettings(
        enabled=True,
        trigger_turns=trigger_turns,
    )
    pet_window.memory_curation_state = MemoryCurationState(
        tmp_path / "memory_curation_state.json"
    )
    pet_window.history_store = _MemoryRetryHistoryStore(entries)
    pet_window.worker_thread = None
    pet_window.memory_curation_thread = None
    pet_window.pending_tool_action = None
    pet_window.pending_screen_observation_messages = None
    pet_window.pending_screen_observation_event = None
    pet_window.screen_observation_followup_in_progress = False
    pet_window.memory_curation_run = None
    pet_window._auto_memory_curation_failure_attempts = 0
    pet_window._suppress_auto_memory_curation_restart = False
    return pet_window


def _set_memory_curation_run(
    pet_window,
    *,
    mode: str = "auto",
    character_id: str | None = None,
    target_history_count: int = 8,
    consumed_turns: int = 3,
):  # type: ignore[no-untyped-def]
    from app.ui.pet_window import _MemoryCurationRunContext

    run = _MemoryCurationRunContext(
        mode=mode,
        character_id=character_id or pet_window.character_profile.id,
        target_history_count=target_history_count,
        consumed_turns=consumed_turns,
    )
    pet_window.memory_curation_run = run
    return run


def test_memory_curation_run_context_is_frozen() -> None:
    from dataclasses import FrozenInstanceError
    from app.ui.pet_window import _MemoryCurationRunContext

    run = _MemoryCurationRunContext("auto", "demo", 8, 3)
    with pytest.raises(FrozenInstanceError):
        run.mode = "backfill"  # type: ignore[misc]


def test_start_memory_curation_clears_context_when_spawn_fails(
    pet_window,
    monkeypatch,
    tmp_path,
) -> None:  # type: ignore[no-untyped-def]
    from app.storage.chat_history import ChatHistoryEntry

    _configure_memory_curation_window(pet_window, tmp_path)

    def fail_spawn(worker, **kwargs):  # type: ignore[no-untyped-def]
        raise RuntimeError("spawn failed")

    monkeypatch.setattr(pet_window.resource_manager, "spawn_qt_worker", fail_spawn)

    with pytest.raises(RuntimeError, match="spawn failed"):
        pet_window._start_memory_curation(
            [ChatHistoryEntry("2026-07-11T10:00:00+08:00", "user", "对话")],
            mode="auto",
            target_history_count=1,
            consumed_turns=1,
        )

    assert pet_window.memory_curation_run is None


def test_memory_curation_finished_without_run_context_logs_state_error(
    pet_window,
    monkeypatch,
    tmp_path,
) -> None:  # type: ignore[no-untyped-def]
    from app.agent.memory_curator import MemoryCurationResult
    import app.ui.pet_window as pet_window_module

    _configure_memory_curation_window(pet_window, tmp_path)
    logs = []
    monkeypatch.setattr(
        pet_window_module,
        "log_event",
        lambda channel, message, payload=None, **kwargs: logs.append(
            (channel, message, payload)
        ),
    )
    pet_window.memory_curation_run = None
    pet_window._auto_memory_curation_failure_attempts = 2
    pet_window._suppress_auto_memory_curation_restart = True
    before = pet_window.memory_curation_state.snapshot()

    pet_window._handle_memory_curation_finished(
        MemoryCurationResult(processed_entries=3)
    )

    assert pet_window.memory_curation_state.snapshot() == before
    assert pet_window._auto_memory_curation_failure_attempts == 2
    assert pet_window._suppress_auto_memory_curation_restart is True
    assert (
        "Memory",
        "记忆整理回调缺少运行上下文",
        {"callback": "finished"},
    ) in logs


def test_memory_curation_failed_without_run_context_skips_retry(
    pet_window,
    monkeypatch,
    tmp_path,
) -> None:  # type: ignore[no-untyped-def]
    import app.ui.pet_window as pet_window_module

    _configure_memory_curation_window(pet_window, tmp_path)
    for _ in range(3):
        pet_window.memory_curation_state.increment_pending_turns()
    before = pet_window.memory_curation_state.snapshot()
    pet_window._auto_memory_curation_failure_attempts = 1
    pet_window.memory_curation_run = None
    logs = []
    messages = []
    monkeypatch.setattr(
        pet_window_module,
        "log_event",
        lambda channel, message, payload=None, **kwargs: logs.append(
            (channel, message, payload)
        ),
    )
    monkeypatch.setattr(
        pet_window.subtitle_controller,
        "show_text_immediately",
        messages.append,
    )

    pet_window._handle_memory_curation_failed("network error")

    assert pet_window.memory_curation_state.snapshot() == before
    assert pet_window._auto_memory_curation_failure_attempts == 1
    assert pet_window._suppress_auto_memory_curation_restart is False
    assert messages == []
    assert (
        "Memory",
        "记忆整理回调缺少运行上下文",
        {"callback": "failed"},
    ) in logs


def test_memory_curation_cleanup_clears_context_before_auto_restart(
    pet_window,
    monkeypatch,
) -> None:  # type: ignore[no-untyped-def]
    from app.ui.pet_window import _MemoryCurationRunContext
    import app.ui.pet_window as pet_window_module

    observed = []
    pet_window.memory_curation_run = _MemoryCurationRunContext("auto", "demo", 8, 3)
    monkeypatch.setattr(
        pet_window,
        "_maybe_start_auto_memory_curation",
        lambda: observed.append(pet_window.memory_curation_run),
    )
    monkeypatch.setattr(
        pet_window_module.QTimer,
        "singleShot",
        lambda _delay, callback: callback(),
    )

    pet_window._cleanup_memory_curation_worker()

    assert observed == [None]


def test_memory_curation_cleanup_during_shutdown_clears_without_restart(
    pet_window,
    monkeypatch,
) -> None:  # type: ignore[no-untyped-def]
    import app.ui.pet_window as pet_window_module

    _set_memory_curation_run(pet_window)
    timers = []
    monkeypatch.setattr(
        pet_window_module.QTimer,
        "singleShot",
        lambda delay, callback: timers.append((delay, callback)),
    )
    pet_window._shutdown_in_progress = True
    try:
        pet_window._cleanup_memory_curation_worker()
    finally:
        pet_window._shutdown_in_progress = False

    assert pet_window.memory_curation_run is None
    assert timers == []


def test_memory_curation_late_callbacks_during_shutdown_skip_context_error(
    pet_window,
    monkeypatch,
) -> None:  # type: ignore[no-untyped-def]
    from app.agent.memory_curator import MemoryCurationResult
    import app.ui.pet_window as pet_window_module

    logs = []
    monkeypatch.setattr(
        pet_window_module,
        "log_event",
        lambda channel, message, payload=None, **kwargs: logs.append(message),
    )
    pet_window.memory_curation_run = None
    pet_window._shutdown_in_progress = True
    try:
        pet_window._handle_memory_curation_finished(
            MemoryCurationResult(processed_entries=1)
        )
        pet_window._handle_memory_curation_failed("late error")
    finally:
        pet_window._shutdown_in_progress = False

    assert "记忆整理回调缺少运行上下文" not in logs


def test_apply_character_keeps_inflight_memory_curation_context(
    pet_window,
    monkeypatch,
) -> None:  # type: ignore[no-untyped-def]
    run = _set_memory_curation_run(pet_window)
    next_profile = replace(
        pet_window.character_profile,
        id="character-b",
        display_name="Character B",
    )
    monkeypatch.setattr(pet_window, "_emit_plugin_event", lambda *args, **kwargs: None)

    pet_window._apply_character(next_profile)

    assert pet_window.memory_curation_run is run
    pet_window.memory_curation_run = None


def test_auto_memory_curation_failure_retries_first_two_attempts(
    pet_window,
    monkeypatch,
    tmp_path,
) -> None:  # type: ignore[no-untyped-def]
    import app.ui.pet_window as pet_window_module

    timers = []
    monkeypatch.setattr(
        pet_window_module.QTimer,
        "singleShot",
        lambda delay, callback: timers.append((delay, callback)),
    )
    _configure_memory_curation_window(pet_window, tmp_path)

    for _ in range(2):
        run = _set_memory_curation_run(pet_window, consumed_turns=9)
        pet_window._handle_memory_curation_failed('API 返回格式无法解析：{"choices":[]}')
        assert pet_window.memory_curation_run is run
        pet_window._cleanup_memory_curation_worker()
        assert pet_window.memory_curation_run is None

    assert len(timers) == 2
    assert [callback.__name__ for _delay, callback in timers] == [
        "_maybe_start_auto_memory_curation",
        "_maybe_start_auto_memory_curation",
    ]
    assert pet_window._auto_memory_curation_failure_attempts == 2


def test_auto_memory_curation_third_failure_stops_restart_and_consumes_pending(
    pet_window,
    monkeypatch,
    tmp_path,
) -> None:  # type: ignore[no-untyped-def]
    import app.ui.pet_window as pet_window_module

    _configure_memory_curation_window(pet_window, tmp_path)
    for _ in range(9):
        pet_window.memory_curation_state.increment_pending_turns()
    timers = []
    monkeypatch.setattr(
        pet_window_module.QTimer,
        "singleShot",
        lambda delay, callback: timers.append((delay, callback)),
    )
    messages = []
    monkeypatch.setattr(
        pet_window.subtitle_controller,
        "show_text_immediately",
        messages.append,
    )
    run = _set_memory_curation_run(pet_window, consumed_turns=9)
    pet_window._auto_memory_curation_failure_attempts = 2
    pet_window._handle_memory_curation_failed("insufficient_user_quota")
    assert pet_window.memory_curation_state.pending_turns() == 0
    assert pet_window._auto_memory_curation_failure_attempts == 0
    assert pet_window._suppress_auto_memory_curation_restart is True
    assert pet_window.memory_curation_run is run
    pet_window._cleanup_memory_curation_worker()
    assert pet_window.memory_curation_run is None
    assert timers == []
    assert messages == [
        "自动记忆整理连续失败，已停止本轮，稍后会在下次整理时再试"
    ]


def test_auto_memory_curation_success_resets_failure_count(
    pet_window,
    tmp_path,
) -> None:  # type: ignore[no-untyped-def]
    from app.agent.memory_curator import MemoryCurationResult

    _configure_memory_curation_window(pet_window, tmp_path)
    for _ in range(3):
        pet_window.memory_curation_state.increment_pending_turns()
    pet_window._auto_memory_curation_failure_attempts = 2
    pet_window._suppress_auto_memory_curation_restart = True
    run = _set_memory_curation_run(pet_window)

    pet_window._handle_memory_curation_finished(MemoryCurationResult(processed_entries=3))

    snapshot = pet_window.memory_curation_state.snapshot()
    assert pet_window._auto_memory_curation_failure_attempts == 0
    assert pet_window._suppress_auto_memory_curation_restart is False
    assert snapshot["processed_history_count"] == 8
    assert snapshot["pending_turns"] == 0
    assert pet_window.memory_curation_run is run


def test_auto_memory_curation_finish_after_character_switch_skips_progress(
    pet_window,
    tmp_path,
) -> None:  # type: ignore[no-untyped-def]
    from app.agent.memory_curator import MemoryCurationResult

    _configure_memory_curation_window(pet_window, tmp_path)
    pet_window.memory_curation_state.mark_processed(12)
    for _ in range(3):
        pet_window.memory_curation_state.increment_pending_turns()
    pet_window._auto_memory_curation_failure_attempts = 2
    pet_window._suppress_auto_memory_curation_restart = True
    run = _set_memory_curation_run(pet_window, character_id="character-a")
    pet_window.character_profile = replace(pet_window.character_profile, id="character-b")

    pet_window._handle_memory_curation_finished(MemoryCurationResult(processed_entries=3))

    snapshot = pet_window.memory_curation_state.snapshot()
    assert pet_window._auto_memory_curation_failure_attempts == 0
    assert pet_window._suppress_auto_memory_curation_restart is False
    assert snapshot["processed_history_count"] == 12
    assert snapshot["pending_turns"] == 3
    assert pet_window.memory_curation_run is run


def test_auto_memory_curation_third_failure_after_character_switch_keeps_pending(
    pet_window,
    monkeypatch,
    tmp_path,
) -> None:  # type: ignore[no-untyped-def]
    import app.ui.pet_window as pet_window_module

    _configure_memory_curation_window(pet_window, tmp_path)
    pet_window.memory_curation_state.mark_processed(12)
    for _ in range(9):
        pet_window.memory_curation_state.increment_pending_turns()
    timers = []
    monkeypatch.setattr(
        pet_window_module.QTimer,
        "singleShot",
        lambda delay, callback: timers.append((delay, callback)),
    )
    run = _set_memory_curation_run(
        pet_window,
        character_id="character-a",
        consumed_turns=9,
    )
    pet_window.character_profile = replace(pet_window.character_profile, id="character-b")
    pet_window._auto_memory_curation_failure_attempts = 2
    pet_window._handle_memory_curation_failed("insufficient_user_quota")
    assert pet_window.memory_curation_state.pending_turns() == 9
    assert pet_window._auto_memory_curation_failure_attempts == 0
    assert pet_window._suppress_auto_memory_curation_restart is False
    assert pet_window.memory_curation_run is run
    pet_window._cleanup_memory_curation_worker()
    assert pet_window.memory_curation_run is None
    assert len(timers) == 1


def test_auto_memory_curation_can_start_after_next_trigger_turns(
    pet_window,
    monkeypatch,
    tmp_path,
) -> None:  # type: ignore[no-untyped-def]
    import app.ui.pet_window as pet_window_module

    timers = []
    monkeypatch.setattr(
        pet_window_module.QTimer,
        "singleShot",
        lambda delay, callback: timers.append((delay, callback)),
    )
    _configure_memory_curation_window(pet_window, tmp_path, trigger_turns=2)
    started = []
    monkeypatch.setattr(
        pet_window,
        "_start_memory_curation",
        lambda entries, **kwargs: started.append({"entries": entries, **kwargs}),
    )
    for _ in range(2):
        pet_window.memory_curation_state.increment_pending_turns()
    _set_memory_curation_run(pet_window, consumed_turns=2)
    pet_window._auto_memory_curation_failure_attempts = 2
    pet_window._handle_memory_curation_failed("API 返回格式无法解析")
    pet_window._cleanup_memory_curation_worker()
    assert pet_window.memory_curation_state.pending_turns() == 0
    assert timers == []

    pet_window._record_completed_memory_turn()
    pet_window._record_completed_memory_turn()

    assert len(timers) == 1
    timers[0][1]()
    assert len(started) == 1
    assert started[0]["mode"] == "auto"
    assert started[0]["consumed_turns"] == 2


def test_auto_memory_choices_empty_failures_stop_after_three_requests(
    pet_window,
    monkeypatch,
    tmp_path,
) -> None:  # type: ignore[no-untyped-def]
    import app.ui.pet_window as pet_window_module
    from app.core.retry_policy import MAX_AUTO_RETRY_ATTEMPTS

    callbacks = []
    monkeypatch.setattr(
        pet_window_module.QTimer,
        "singleShot",
        lambda _delay, callback: callbacks.append(callback),
    )
    _configure_memory_curation_window(pet_window, tmp_path, trigger_turns=3)
    for _ in range(3):
        pet_window.memory_curation_state.increment_pending_turns()
    request_count = 0

    def fail_start(
        entries,  # type: ignore[no-untyped-def]
        *,
        mode: str,
        target_history_count: int,
        consumed_turns: int,
    ) -> None:
        nonlocal request_count
        _ = entries
        request_count += 1
        _set_memory_curation_run(
            pet_window,
            mode=mode,
            target_history_count=target_history_count,
            consumed_turns=consumed_turns,
        )
        pet_window._handle_memory_curation_failed('API 返回格式无法解析：{"choices":[]}')
        pet_window._cleanup_memory_curation_worker()

    monkeypatch.setattr(pet_window, "_start_memory_curation", fail_start)
    callbacks.append(pet_window._maybe_start_auto_memory_curation)

    iterations = 0
    while callbacks and iterations < 10:
        iterations += 1
        callback = callbacks.pop(0)
        callback()

    assert request_count == MAX_AUTO_RETRY_ATTEMPTS
    assert callbacks == []
    assert pet_window.memory_curation_state.pending_turns() == 0


def test_pet_window_memory_curation_has_single_context(pet_window) -> None:
    import app.ui.pet_window as pet_window_module

    source = Path(pet_window_module.__file__).read_text(encoding="utf-8")
    assert pet_window.memory_curation_run is None
    for name in (
        "memory_curation_mode",
        "memory_curation_character_id",
        "memory_curation_target_history_count",
        "memory_curation_consumed_turns",
    ):
        assert name not in source


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
    assert len(warnings) == 1
    assert warnings[0][0] == "记忆模型下载失败"
    assert "处理建议" in warnings[0][1]
    assert "download failed" in warnings[0][1]
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


def test_close_event_waits_for_running_tts_migration(
    pet_window, monkeypatch
) -> None:  # type: ignore[no-untyped-def]
    from PySide6.QtGui import QCloseEvent

    import app.ui.pet_window as pet_window_module

    class MigrationThreadStub:
        def isRunning(self) -> bool:
            return True

    closed: list[bool] = []
    messages: list[tuple[str, str]] = []
    monkeypatch.setattr(
        pet_window,
        "tts_migration_thread",
        MigrationThreadStub(),
        raising=False,
    )
    monkeypatch.setattr(
        pet_window_module,
        "has_active_tts_bundle_download",
        lambda: False,
    )
    monkeypatch.setattr(
        pet_window,
        "close_external_tools",
        lambda: closed.append(True),
    )
    monkeypatch.setattr(
        pet_window_module.QMessageBox,
        "information",
        lambda _parent, title, message: messages.append((title, message)),
    )
    event = QCloseEvent()

    pet_window.closeEvent(event)

    assert not event.isAccepted()
    assert closed == []
    assert messages == [
        ("TTS 数据迁移中", "请等待 TTS 数据迁移完成后再退出 Sakura。")
    ]


def test_close_event_continues_for_invalid_tts_migration_wrapper(
    pet_window, monkeypatch
) -> None:  # type: ignore[no-untyped-def]
    from PySide6.QtGui import QCloseEvent

    import app.ui.pet_window as pet_window_module

    class InvalidMigrationThreadStub:
        def isRunning(self) -> bool:
            raise RuntimeError("wrapped C/C++ object has been deleted")

    closed: list[bool] = []
    monkeypatch.setattr(
        pet_window,
        "tts_migration_thread",
        InvalidMigrationThreadStub(),
        raising=False,
    )
    monkeypatch.setattr(
        pet_window_module,
        "has_active_tts_bundle_download",
        lambda: False,
    )
    monkeypatch.setattr(
        pet_window,
        "close_external_tools",
        lambda: closed.append(True),
    )
    event = QCloseEvent()

    pet_window.closeEvent(event)

    assert event.isAccepted()
    assert closed == [True]


def test_close_external_tools_cancels_and_keeps_lingering_thread() -> None:
    from app.core.resource_manager import QtWorkerResource, ResourceManager
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
            self.parent_value: object | None = object()

        def requestInterruption(self) -> None:
            self.interrupted = True

        def isRunning(self) -> bool:
            return True

        def quit(self) -> None:
            self.quit_called = True

        def wait(self, timeout: int) -> bool:
            self.waits.append(timeout)
            return False

        def setParent(self, parent: object | None) -> None:
            self.parent_value = parent

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

    order: list[str] = []

    class BackchannelStub:
        def cancel(self) -> None:
            order.append("backchannel_cancel")

    class RecordingResourceManager(ResourceManager):
        def stop_all(self, timeout_ms: int = 1000) -> None:
            order.append("stop_all")
            super().stop_all(timeout_ms)

    class MinimalWindow:
        close_external_tools = PetWindow.close_external_tools

    window = MinimalWindow()
    manager = RecordingResourceManager()
    thread = ThreadStub()
    worker = WorkerStub()
    subtitle = SubtitleStub()
    window._shutdown_in_progress = False
    window.resource_manager = manager
    window.messages = [
        {"role": "assistant", "content": "途中", TRANSIENT_PROGRESS_MESSAGE_KEY: True}
    ]
    window.subtitle_controller = subtitle
    window.backchannel_controller = BackchannelStub()
    window.worker_thread = thread
    window.worker = worker
    # close_external_tools 通过 resource_manager.stop_all 关闭已注册的 worker。
    manager._register(
        QtWorkerResource(
            manager,
            thread,
            worker,
            owner=window,
            thread_attr="worker_thread",
            worker_attr="worker",
            label="worker_thread",
        )
    )
    window._emit_app_closed_event = lambda: None
    window._stop_speaking_state_watchdog = lambda: None
    window.close_tts_tools = lambda: order.append("tts_close")
    window.close_mcp_tools = lambda: order.append("mcp_close")
    window.close_plugins = lambda: order.append("plugins_close")
    window._close_renderer_manager = lambda: order.append("renderer_close")

    window.close_external_tools()

    assert window._shutdown_in_progress is True
    assert worker.cancelled is True
    assert thread.interrupted is True
    assert thread.quit_called is True
    assert thread.waits == [1000]
    assert manager._lingering == [(thread, worker)]
    assert thread.parent_value is None
    assert window.worker_thread is None
    assert window.worker is None
    assert window.messages == []
    assert subtitle.cancelled is True
    assert order == ["backchannel_cancel", "stop_all"]


def test_close_external_tools_shutdowns_active_tauri_settings() -> None:
    from app.ui.pet_window import PetWindow

    class ResourceManagerStub:
        def __init__(self) -> None:
            self.stopped = False

        def stop_all(self, _timeout_ms: int) -> None:
            self.stopped = True

    class SubtitleStub:
        def cancel_reply_flow(self) -> None:
            pass

    class TauriProcessStub:
        def __init__(self) -> None:
            self.shutdown_called = False

        def shutdown(self) -> None:
            self.shutdown_called = True

    class MinimalWindow:
        close_external_tools = PetWindow.close_external_tools
        _close_tauri_settings_process_for_shutdown = (
            PetWindow._close_tauri_settings_process_for_shutdown
        )
        _restore_tauri_layout_preview = PetWindow._restore_tauri_layout_preview
        _restore_tauri_font_preview = PetWindow._restore_tauri_font_preview
        _restore_tauri_settings_preview = PetWindow._restore_tauri_settings_preview
        _release_tauri_preview_force_state = PetWindow._release_tauri_preview_force_state

        def _preview_layout(self, *layout):  # type: ignore[no-untyped-def]
            self.restored_layout = layout

        def _sync_secondary_window_state(self) -> None:
            self.synced = True

    process = TauriProcessStub()
    window = MinimalWindow()
    window._shutdown_in_progress = False
    window.tauri_settings_process = process
    window._tauri_initial_tts_settings = object()
    window._tauri_original_layout = (100, 640, 128, 0, 0)
    window.resource_manager = ResourceManagerStub()
    window.messages = []
    window.subtitle_controller = SubtitleStub()
    window.backchannel_controller = None
    window.worker_thread = None
    window._emit_app_closed_event = lambda: None
    window._stop_speaking_state_watchdog = lambda: None

    window.close_external_tools()

    assert process.shutdown_called is True
    assert window.tauri_settings_process is None
    assert window._tauri_initial_tts_settings is None
    assert window.restored_layout == (100, 640, 128, 0, 0)
    assert window.synced is True
    assert window.resource_manager.stopped is True


def test_pet_window_registers_runtime_services_in_registry_order() -> None:
    from app.core.resource_manager import ResourceManager, ResourceRegistry
    from app.ui.pet_window import PetWindow

    order: list[str] = []

    class MemoryStoreStub:
        def close(self) -> None:
            order.append("memory")

    class MinimalWindow:
        _register_runtime_service_resources = PetWindow._register_runtime_service_resources

        def close_tts_tools(self) -> None:
            order.append("tts")

        def close_mcp_tools(self) -> None:
            order.append("mcp")

        def _close_renderer_manager(self) -> None:
            order.append("renderer")

        def close_plugins(self) -> None:
            order.append("plugins")

    registry = ResourceRegistry()
    window = MinimalWindow()
    window.memory_store = MemoryStoreStub()
    window.resource_manager = ResourceManager(registry=registry)

    window._register_runtime_service_resources()
    registry.stop_all()

    assert order == ["memory", "tts", "mcp", "renderer", "plugins"]


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


def test_silent_screen_awareness_reply_ends_interaction(
    pet_window,
    monkeypatch,
) -> None:  # type: ignore[no-untyped-def]
    from app.ui.pet_window import TRANSIENT_PROGRESS_MESSAGE_KEY

    pet_window.messages = [
        {"role": "assistant", "content": "途中", TRANSIENT_PROGRESS_MESSAGE_KEY: True}
    ]
    pet_window.active_event = AgentEvent(type="screen_awareness_check", payload={})
    pet_window.active_interaction_id = "interaction-1"
    ended = []
    consumed = []
    monkeypatch.setattr(
        pet_window,
        "_queue_event_screen_observation_followup",
        lambda result, event: False,
    )
    monkeypatch.setattr(
        pet_window,
        "_filter_screen_awareness_reply",
        lambda result, event: result,
    )
    monkeypatch.setattr(pet_window, "_consume_agent_result", consumed.append)

    def end(outcome: str) -> None:
        ended.append(outcome)
        pet_window.active_interaction_id = ""

    monkeypatch.setattr(pet_window, "_end_interaction", end)

    pet_window._handle_event_reply(AgentResult(reply=ChatReply([]), actions=[]))

    assert pet_window.messages == []
    assert pet_window.active_event is None
    assert consumed == []
    assert ended == ["event_silent"]


def test_event_error_cleans_transient_progress_during_shutdown(pet_window) -> None:
    from app.ui.pet_window import TRANSIENT_PROGRESS_MESSAGE_KEY

    pet_window.active_event = AgentEvent(type="custom", payload={})
    pet_window.messages = [
        {"role": "assistant", "content": "途中", TRANSIENT_PROGRESS_MESSAGE_KEY: True}
    ]
    pet_window._shutdown_in_progress = True
    try:
        pet_window._handle_event_error("late error")
    finally:
        pet_window._shutdown_in_progress = False

    assert pet_window.messages == []
    assert pet_window.active_event is None


def test_screen_awareness_event_error_ends_interaction(
    pet_window,
    monkeypatch,
) -> None:  # type: ignore[no-untyped-def]
    pet_window.active_event = AgentEvent(type="screen_awareness_check", payload={})
    pet_window.active_interaction_id = "interaction-3"
    ended = []

    def end(outcome: str) -> None:
        ended.append(outcome)
        pet_window.active_interaction_id = ""

    monkeypatch.setattr(pet_window, "_end_interaction", end)

    pet_window._handle_event_error("API 请求超时。")

    assert pet_window.active_event is None
    assert pet_window.active_interaction_id == ""
    assert ended == ["screen_awareness_error_silent"]


def test_reminder_event_error_uses_active_event_payload(pet_window, monkeypatch) -> None:
    pet_window.active_event = AgentEvent(
        type="reminder_due",
        payload={"id": "reminder-1", "text": "喝水"},
    )
    completed: list[str] = []
    consumed: list[AgentResult] = []
    monkeypatch.setattr(pet_window, "_mark_reminder_completed", completed.append)
    monkeypatch.setattr(pet_window, "_consume_agent_result", consumed.append)

    pet_window._handle_event_error("network error")

    assert pet_window.active_event is None
    assert completed == ["reminder-1"]
    assert consumed[0].reply.segments[0].translation == "到时间了：喝水"
    for name in ("active_event_type", "active_reminder_id", "active_reminder_text"):
        assert not hasattr(pet_window, name)


def test_reminder_event_reply_marks_payload_id_after_consuming_result(
    pet_window,
    monkeypatch,
) -> None:  # type: ignore[no-untyped-def]
    event = AgentEvent(
        type="reminder_due",
        payload={"id": "reminder-1", "text": "喝水"},
    )
    result = AgentResult(reply=ChatReply([ChatSegment("時間だよ。", translation="到时间了。")]))
    order = []
    pet_window.active_event = event
    monkeypatch.setattr(
        pet_window,
        "_queue_event_screen_observation_followup",
        lambda current, active_event: False,
    )
    monkeypatch.setattr(
        pet_window,
        "_filter_screen_awareness_reply",
        lambda current, active_event: current,
    )

    def consume(current: AgentResult) -> None:
        assert pet_window.active_event is None
        assert current is result
        order.append("consume")

    def complete(reminder_id: str) -> None:
        assert pet_window.active_event is None
        order.append(("complete", reminder_id))

    monkeypatch.setattr(pet_window, "_consume_agent_result", consume)
    monkeypatch.setattr(pet_window, "_mark_reminder_completed", complete)

    pet_window._handle_event_reply(result)

    assert order == ["consume", ("complete", "reminder-1")]


def test_due_reminder_passes_single_agent_event_argument(
    pet_window,
    monkeypatch,
) -> None:  # type: ignore[no-untyped-def]
    from types import SimpleNamespace

    pet_window.reminder_store = SimpleNamespace(
        due_reminders=lambda: [
            {
                "id": "reminder-1",
                "text": "喝水",
                "trigger_at": "2026-07-11T12:00:00+08:00",
            }
        ]
    )
    events = []
    monkeypatch.setattr(pet_window, "_run_event_worker", events.append)

    pet_window._check_due_reminders()

    assert events == [
        AgentEvent(
            type="reminder_due",
            payload={
                "id": "reminder-1",
                "text": "喝水",
                "trigger_at": "2026-07-11T12:00:00+08:00",
            },
        )
    ]


def test_due_reminder_does_not_start_while_active_event_exists(pet_window) -> None:
    class ReminderStore:
        def due_reminders(self):  # type: ignore[no-untyped-def]
            raise AssertionError("active event 应在读取提醒前阻止本轮检查")

    pet_window.active_event = AgentEvent(type="screen_awareness_check", payload={})
    pet_window.reminder_store = ReminderStore()

    pet_window._check_due_reminders()


def test_screen_awareness_does_not_start_while_active_event_exists(
    pet_window,
    monkeypatch,
) -> None:  # type: ignore[no-untyped-def]
    pet_window.active_event = AgentEvent(type="reminder_due", payload={"id": "r1"})
    pet_window.input_edit.clear()
    pet_window.speech_timer.stop()
    pet_window.active_interaction_id = ""
    monkeypatch.setattr(pet_window, "_screen_awareness_context_allowed", lambda: True)
    monkeypatch.setattr(
        pet_window.subtitle_controller,
        "current_segment_in_progress",
        lambda: False,
    )

    assert not pet_window._can_run_screen_awareness()


def test_cleanup_worker_restarts_pending_event_from_payload_only(
    pet_window,
    monkeypatch,
) -> None:  # type: ignore[no-untyped-def]
    event = AgentEvent(
        type="reminder_due",
        payload={"id": "reminder-1", "text": "喝水", "screen_context": {}},
    )
    pet_window.pending_screen_observation_event = event
    restarted: list[AgentEvent] = []
    monkeypatch.setattr(pet_window, "_run_event_worker", restarted.append)

    pet_window._cleanup_worker()

    assert restarted == [event]
    assert not hasattr(pet_window, "pending_screen_observation_event_reminder_id")


def _event_followup_inputs() -> tuple[AgentEvent, ScreenObservation]:
    return (
        AgentEvent(
            type="screen_awareness_check",
            payload={"id": "reminder-1", "text": "喝水"},
        ),
        ScreenObservation(
            data_url="data:image/jpeg;base64,screen",
            width=320,
            height=180,
            captured_at="2026-07-11T12:00:01+08:00",
            screen_name="primary",
        ),
    )


def test_event_followup_restarts_once_when_worker_finishes_before_encode(
    pet_window,
    monkeypatch,
) -> None:  # type: ignore[no-untyped-def]
    import app.ui.pet_window as pet_window_module

    event, observation = _event_followup_inputs()
    restarted = []
    busy = []
    pet_window.worker_thread = None
    pet_window.screen_observation_followup_in_progress = True
    monkeypatch.setattr(pet_window, "_run_event_worker", restarted.append)
    monkeypatch.setattr(pet_window, "_set_busy", busy.append)
    monkeypatch.setattr(pet_window, "_record_history", lambda *args: None)
    monkeypatch.setattr(
        pet_window_module.QTimer,
        "singleShot",
        lambda delay, callback: callback(),
    )

    pet_window._cleanup_worker()
    assert restarted == []
    assert busy == []

    pet_window._finish_event_screen_observation_followup(
        {"event": event, "reason": "看看屏幕"},
        observation,
    )

    assert len(restarted) == 1
    assert restarted[0].payload["id"] == "reminder-1"
    assert busy == []


def test_event_followup_waits_for_worker_finalizer_when_encode_finishes_first(
    pet_window,
    monkeypatch,
) -> None:  # type: ignore[no-untyped-def]
    event, observation = _event_followup_inputs()
    restarted = []
    busy = []
    pet_window.worker_thread = object()
    pet_window.screen_observation_followup_in_progress = True
    monkeypatch.setattr(pet_window, "_run_event_worker", restarted.append)
    monkeypatch.setattr(pet_window, "_set_busy", busy.append)
    monkeypatch.setattr(pet_window, "_record_history", lambda *args: None)

    pet_window._finish_event_screen_observation_followup(
        {"event": event, "reason": "看看屏幕"},
        observation,
    )
    assert restarted == []
    assert busy == []

    pet_window.worker_thread = None
    pet_window._cleanup_worker()

    assert len(restarted) == 1
    assert restarted[0].payload["id"] == "reminder-1"
    assert busy == []


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
            self._using_system_drag = False

        def windowHandle(self):  # type: ignore[no-untyped-def]
            return None

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
            self._using_system_drag = False
            self.input_bar_animator = _DragAnimatorStub()
            self.move_positions: list[object] = []

        def frameGeometry(self):  # type: ignore[no-untyped-def]
            raise AssertionError("拖拽不应依赖 frameGeometry")

        def windowHandle(self):  # type: ignore[no-untyped-def]
            return None  # 非 QWidget 环境，回退 self.move 路径

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
            self._using_system_drag = False
            self.input_bar_animator = _DragAnimatorStub()
            self.move_positions: list[object] = []

        def windowHandle(self):  # type: ignore[no-untyped-def]
            return None

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


def test_pet_window_drag_uses_start_system_move_when_window_handle_supports() -> None:  # type: ignore[no-untyped-def]
    """验证当 windowHandle 支持 startSystemMove 时优先走系统拖拽、不调用 self.move()."""
    qtcore = pytest.importorskip("PySide6.QtCore")
    from app.ui.pet_window import PetWindow

    class MouseEventStub:
        accepted = False

        def __init__(self, position, global_position, buttons=None):  # type: ignore[no-untyped-def]
            self._position = qtcore.QPointF(*position)
            self._global_position = qtcore.QPointF(*global_position)
            self._buttons = buttons or qtcore.Qt.MouseButton.LeftButton

        def button(self):  # type: ignore[no-untyped-def]
            return qtcore.Qt.MouseButton.LeftButton

        def buttons(self):  # type: ignore[no-untyped-def]
            return self._buttons

        def position(self):  # type: ignore[no-untyped-def]
            return self._position

        def globalPosition(self):  # type: ignore[no-untyped-def]
            return self._global_position

        def accept(self) -> None:
            self.accepted = True

    class _WindowHandleStub:
        def __init__(self) -> None:
            self.system_move_called = False

        def startSystemMove(self) -> bool:
            self.system_move_called = True
            return True

    class _DragAnimatorStub:
        def __init__(self) -> None:
            self.suspend_called = False

        def suspend_for_drag(self) -> None:
            self.suspend_called = True

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
            self._using_system_drag = False
            self.input_bar_animator = _DragAnimatorStub()
            self.window_handle = _WindowHandleStub()
            self.move_positions: list[object] = []

        def windowHandle(self):  # type: ignore[no-untyped-def]
            return self.window_handle

        def move(self, position) -> None:  # type: ignore[no-untyped-def]
            self.move_positions.append(position)

        def _finish_drag_resume(self) -> None:
            pass

        def _check_system_drag_timeout(self) -> None:
            """测试桩：不依赖 QTimer，直接清理。"""
            if not self._using_system_drag:
                return
            self._using_system_drag = False
            self._dragging = False
            self._finish_drag_resume()

    window = MinimalWindow()
    press_event = MouseEventStub(position=(40, 60), global_position=(240, 160))
    move_event = MouseEventStub(position=(45, 65), global_position=(300, 220))
    second_move = MouseEventStub(position=(50, 70), global_position=(320, 260))

    # Press → anchor 正确记录
    assert window._handle_mouse_press(press_event) is True
    assert window.drag_anchor == qtcore.QPoint(40, 60)
    assert not window.window_handle.system_move_called
    assert not window._using_system_drag
    assert not window._dragging
    assert window.input_bar_animator.suspend_called is False

    # 首次 Move → 触发 startSystemMove，不调用 self.move()
    assert window._handle_mouse_move(move_event) is True
    assert window.window_handle.system_move_called
    assert window._using_system_drag
    assert window._dragging
    assert window.input_bar_animator.suspend_called
    assert window.move_positions == []

    # 后续 Move（_using_system_drag 已置位）→ 跳过 self.move()
    assert window._handle_mouse_move(second_move) is True
    assert window.move_positions == []


def test_pet_window_new_press_recovers_stale_system_drag_suspension() -> None:  # type: ignore[no-untyped-def]
    """验证旧系统拖拽缺失 release 时，新交互会先恢复挂起的输入栏。"""
    qtcore = pytest.importorskip("PySide6.QtCore")
    from app.ui.pet_window import PetWindow

    class MouseEventStub:
        accepted = False

        def button(self):  # type: ignore[no-untyped-def]
            return qtcore.Qt.MouseButton.LeftButton

        def position(self):  # type: ignore[no-untyped-def]
            return qtcore.QPointF(12, 18)

        def globalPosition(self):  # type: ignore[no-untyped-def]
            return qtcore.QPointF(112, 118)

        def accept(self) -> None:
            self.accepted = True

    class AnimatorStub:
        def __init__(self) -> None:
            self._suspended = True
            self.resume_calls = 0

        def resume_after_drag(self) -> None:
            if not self._suspended:
                return
            self._suspended = False
            self.resume_calls += 1

    class MinimalWindow:
        _handle_mouse_press = PetWindow._handle_mouse_press
        _drag_anchor_from_event = PetWindow._drag_anchor_from_event
        _clear_input_focus_for_pet_interaction = PetWindow._clear_input_focus_for_pet_interaction
        _finish_drag_resume = PetWindow._finish_drag_resume

        def __init__(self) -> None:
            self.drag_anchor = qtcore.QPoint(1, 1)
            self._dragging = True
            self._using_system_drag = True
            self._drag_release_pending = True
            self.input_bar_animator = AnimatorStub()

    window = MinimalWindow()
    event = MouseEventStub()

    assert window._handle_mouse_press(event) is True
    assert window.input_bar_animator.resume_calls == 1
    assert window.input_bar_animator._suspended is False
    assert window._using_system_drag is False
    assert window._dragging is False
    assert window._drag_release_pending is False
    assert window.drag_anchor == qtcore.QPoint(12, 18)


def test_pet_window_late_release_after_system_drag_timeout_is_not_click(
    monkeypatch,
) -> None:  # type: ignore[no-untyped-def]
    """验证看门狗完成拖拽后补发的 release 不会被误判为单击。"""
    qtcore = pytest.importorskip("PySide6.QtCore")
    import app.ui.pet_window as pet_window_module
    from app.ui.pet_window import PetWindow

    scheduled_callbacks: list[tuple[int, object]] = []

    class ApplicationStub:
        @staticmethod
        def instance():  # type: ignore[no-untyped-def]
            return None

    class MouseEventStub:
        accepted = False

        def button(self):  # type: ignore[no-untyped-def]
            return qtcore.Qt.MouseButton.LeftButton

        def accept(self) -> None:
            self.accepted = True

    class AnimatorStub:
        def __init__(self) -> None:
            self._suspended = True
            self.resume_calls = 0

        def resume_after_drag(self) -> None:
            if not self._suspended:
                return
            self._suspended = False
            self.resume_calls += 1

    class MinimalWindow:
        _check_system_drag_timeout = PetWindow._check_system_drag_timeout
        _handle_mouse_release = PetWindow._handle_mouse_release
        _finish_drag_resume = PetWindow._finish_drag_resume

        def __init__(self) -> None:
            self.drag_anchor = qtcore.QPoint(20, 30)
            self._dragging = True
            self._using_system_drag = True
            self._drag_release_pending = True
            self.input_bar_animator = AnimatorStub()
            self.pet_clicks = 0

        def _handle_pet_click(self) -> None:
            self.pet_clicks += 1

    monkeypatch.setattr(pet_window_module, "QApplication", ApplicationStub)
    monkeypatch.setattr(
        pet_window_module.QTimer,
        "singleShot",
        lambda delay, callback: scheduled_callbacks.append((delay, callback)),
    )

    window = MinimalWindow()
    window._check_system_drag_timeout()

    assert window._dragging is False
    assert window._using_system_drag is False
    assert window._drag_release_pending is True
    assert window.drag_anchor is None
    assert len(scheduled_callbacks) == 1

    event = MouseEventStub()
    assert window._handle_mouse_release(event) is True
    assert event.accepted
    assert window.pet_clicks == 0
    assert window._drag_release_pending is False

    for _delay, callback in scheduled_callbacks:
        callback()  # type: ignore[operator]
    assert window.input_bar_animator.resume_calls == 1


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


def test_apply_pet_layout_refreshes_mask_when_window_size_unchanged() -> None:
    qtwidgets = pytest.importorskip("PySide6.QtWidgets")
    if not hasattr(qtwidgets, "QApplication") or not hasattr(qtwidgets, "QWidget"):
        pytest.skip("当前测试环境只提供了 PySide6 stub。")

    from app.ui.control_panel_layout import PetLayout
    from app.ui.pet_window import PetWindow

    QApplication = qtwidgets.QApplication
    QWidget = qtwidgets.QWidget
    app = QApplication.instance() or QApplication([])

    # 窗口尺寸固定，使 _apply_pet_layout 内的 resize() 为同尺寸 no-op，不派发 resizeEvent，
    # 从而排除 _layout_stage 那条旁路：遮罩若仍被刷新，只能来自 _apply_pet_layout 本身。
    layout = PetLayout(
        window_size=(400, 300),
        portrait_rect=(50, 0, 300, 240),
        bubble_rect=(60, 0, 280, 60),
        input_rect=(60, 250, 280, 40),
        portrait_anchor=(200, 280),
    )

    class MinimalLayoutWindow(PetWindow):
        def __init__(self) -> None:
            QWidget.__init__(self)
            self.mask_calls = 0
            self.overlay_calls = 0

        def _compute_pet_layout(self) -> PetLayout:  # type: ignore[override]
            return layout

        def _place_pet_children(self, _layout) -> None:  # type: ignore[no-untyped-def]
            pass

        def _update_stage_mask(self, _layout) -> None:  # type: ignore[no-untyped-def]
            self.mask_calls += 1

        def _update_stage_debug_overlay(self, _layout) -> None:  # type: ignore[no-untyped-def]
            self.overlay_calls += 1

    window = MinimalLayoutWindow()
    window.resize(*layout.window_size)
    app.processEvents()
    # 复位：构造期 resize 触发的任何 resizeEvent 不应计入待测调用。
    window.mask_calls = 0
    window.overlay_calls = 0

    window._apply_pet_layout()

    assert window.mask_calls == 1
    assert window.overlay_calls == 1

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
        / uuid.uuid4().hex
        / "portrait_scale"
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
        / uuid.uuid4().hex
        / "portrait_no_resize"
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


def test_pet_window_locks_controls_during_startup_initialization(startup_pet_window) -> None:  # type: ignore[no-untyped-def]
    from app.ui.pet_window import STARTUP_INITIALIZING_TEXT

    window = startup_pet_window

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


def test_pet_window_unlocks_after_deferred_services_are_applied(
    startup_pet_window,
    qtbot,
) -> None:  # type: ignore[no-untyped-def]
    from app.core.bootstrap import DeferredStartupServices
    from app.core.extensions import ExtensionRegistry
    from app.plugins.manager import PluginManager
    from app.voice.tts import NullTTSProvider

    class ServiceReadyTTSProvider(NullTTSProvider):
        def __init__(self) -> None:
            self.playback_warmup_calls = 0

        def warm_up_playback(self) -> None:
            self.playback_warmup_calls += 1

    window = startup_pet_window
    tts_provider = ServiceReadyTTSProvider()
    services = DeferredStartupServices(
        tts_provider=tts_provider,
        tool_registry=window.tool_registry,
        extension_registry=ExtensionRegistry(),
        plugin_manager=PluginManager(base_dir=window.base_dir),
        mcp_settings=window.mcp_settings,
        mcp_tool_provider=None,
        errors=("TTS 配置无效，已禁用：参考音频不存在",),
    )

    window.apply_deferred_services(services)
    qtbot.wait(1)

    assert not window.startup_initializing
    assert window.input_edit.isEnabled()
    assert window.send_button.isEnabled()
    assert window.screenshot_button.isEnabled()
    assert window.subtitle_controller.speech_text == window.character_profile.initial_message
    assert not window.tts_error_label.isHidden()
    assert "TTS 配置无效" in window.tts_error_label.text()
    assert tts_provider.playback_warmup_calls == 0



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


def test_pet_window_backchannel_disk_cache_persists_and_replays(tmp_path: Path) -> None:
    """合成音频持久化与动态链接:空闲落盘 → 跳过重复合成 → 重启等价场景直接命中磁盘缓存播放。"""
    from app.backchannel.audio_cache import BackchannelAudioCache
    from app.backchannel.models import (
        BackchannelManifest,
        BackchannelTemplate,
        BackchannelVariant,
    )
    from app.backchannel.resolver import BackchannelChoice
    from app.ui.pet_window import PetWindow
    from app.voice.tts import TTSPreparedAudio

    class ProviderStub:
        def __init__(self) -> None:
            self.prepared: list[str] = []
            self.spoken_paths: list[Path | None] = []
            self.discarded: list[str] = []

        def prepare(self, text: str, tone: str | None = None) -> TTSPreparedAudio:
            self.prepared.append(text)
            return TTSPreparedAudio(text=text, tone=tone)

        def speak_prepared(self, handle, on_started=None, on_finished=None):  # type: ignore[no-untyped-def]
            self.spoken_paths.append(handle.audio_path)

        def discard_prepared(self, handle: TTSPreparedAudio) -> None:
            self.discarded.append(handle.text)

    class WindowStub:
        _backchannel_tts_wanted = PetWindow._backchannel_tts_wanted
        _backchannel_tts_active = PetWindow._backchannel_tts_active
        _backchannel_audio_key = PetWindow._backchannel_audio_key
        _prepare_backchannel_audio_cache = PetWindow._prepare_backchannel_audio_cache
        _backchannel_variant_audio_available = PetWindow._backchannel_variant_audio_available
        _resolve_backchannel_audio_path = PetWindow._resolve_backchannel_audio_path
        _copy_backchannel_audio_for_playback = PetWindow._copy_backchannel_audio_for_playback
        _play_backchannel_audio = PetWindow._play_backchannel_audio
        _request_backchannel_audio_playback = PetWindow._request_backchannel_audio_playback

        def __init__(self) -> None:
            self.backchannel_settings = BackchannelSettings(enabled=True, tts_enabled=True)
            self.tts_provider = ProviderStub()
            self._active_backchannel_audio = None
            self.logged: list[tuple[str, dict[str, object] | None]] = []
            template = BackchannelTemplate(
                id="greeting",
                tone="中性",
                portrait="高兴满足",
                variants=(BackchannelVariant(ja="……おかえり。", zh="……欢迎回来。"),),
                intent="greeting_return",
                emotion="neutral",
            )
            self.backchannel_manifest = BackchannelManifest(templates=(template,))
            self.choice = BackchannelChoice(template, template.variants[0])
            self._backchannel_audio_cache = BackchannelAudioCache(tmp_path / "audio", "fp")
            synth = tmp_path / "synth.wav"
            synth.write_bytes(b"wav-bytes")
            self._backchannel_prepared_audio = {
                ("greeting", "中性", "……おかえり。"): TTSPreparedAudio(
                    text="……おかえり。", tone="中性", audio_path=synth
                )
            }

        def _log_interaction_stage(self, stage: str, payload=None):  # type: ignore[no-untyped-def]
            self.logged.append((stage, payload))

    window = WindowStub()
    cache = window._backchannel_audio_cache

    # 空闲补合成入口:已合成句柄落盘、从内存丢弃、不重复提交合成
    window._prepare_backchannel_audio_cache()
    cached = cache.lookup("中性", "……おかえり。")
    assert cached is not None and cached.read_bytes() == b"wav-bytes"
    assert window._backchannel_prepared_audio == {}
    assert window.tts_provider.discarded == ["……おかえり。"]
    assert window.tts_provider.prepared == []  # 磁盘已有 → 不再合成

    # 播放走磁盘缓存分支:播的是临时副本而非缓存本体,缓存在播后存活
    window._play_backchannel_audio(window.choice)
    assert len(window.tts_provider.spoken_paths) == 1
    played = window.tts_provider.spoken_paths[0]
    assert played is not None and played != cached
    assert cached.exists()
    played.unlink(missing_ok=True)


def test_pet_window_backchannel_synth_persisted_on_play(tmp_path: Path) -> None:
    """磁盘缓存未命中时播放内存句柄,播放前落盘——下次直接复用。"""
    from app.backchannel.audio_cache import BackchannelAudioCache
    from app.backchannel.models import BackchannelTemplate, BackchannelVariant
    from app.backchannel.resolver import BackchannelChoice
    from app.ui.pet_window import PetWindow
    from app.voice.tts import TTSPreparedAudio

    class ProviderStub:
        def __init__(self) -> None:
            self.spoken: list[str] = []

        def speak_prepared(self, handle, on_started=None, on_finished=None):  # type: ignore[no-untyped-def]
            self.spoken.append(handle.text)

    class WindowStub:
        _backchannel_tts_wanted = PetWindow._backchannel_tts_wanted
        _backchannel_audio_key = PetWindow._backchannel_audio_key
        _resolve_backchannel_audio_path = PetWindow._resolve_backchannel_audio_path
        _copy_backchannel_audio_for_playback = PetWindow._copy_backchannel_audio_for_playback
        _play_backchannel_audio = PetWindow._play_backchannel_audio
        _request_backchannel_audio_playback = PetWindow._request_backchannel_audio_playback

        def __init__(self) -> None:
            self.backchannel_settings = BackchannelSettings(enabled=True, tts_enabled=True)
            self.tts_provider = ProviderStub()
            self._active_backchannel_audio = None
            self.backchannel_manifest = None
            self.logged: list[tuple[str, dict[str, object] | None]] = []
            template = BackchannelTemplate(
                id="greeting",
                tone="中性",
                portrait="高兴满足",
                variants=(BackchannelVariant(ja="……おかえり。", zh="……欢迎回来。"),),
                intent="greeting_return",
                emotion="neutral",
            )
            self.choice = BackchannelChoice(template, template.variants[0])
            self._backchannel_audio_cache = BackchannelAudioCache(tmp_path / "audio", "fp")
            synth = tmp_path / "synth.wav"
            synth.write_bytes(b"wav-bytes")
            self._backchannel_prepared_audio = {
                ("greeting", "中性", "……おかえり。"): TTSPreparedAudio(
                    text="……おかえり。", tone="中性", audio_path=synth
                )
            }

        def _log_interaction_stage(self, stage: str, payload=None):  # type: ignore[no-untyped-def]
            self.logged.append((stage, payload))

    window = WindowStub()
    window._play_backchannel_audio(window.choice)
    assert window.tts_provider.spoken == ["……おかえり。"]
    # 播放前已持久化,句柄已从内存移除
    assert window._backchannel_audio_cache.lookup("中性", "……おかえり。") is not None
    assert window._backchannel_prepared_audio == {}


def test_pet_window_backchannel_audio_uses_prepared_tts() -> None:
    from app.backchannel.models import (
        BackchannelManifest,
        BackchannelTemplate,
        BackchannelVariant,
    )
    from app.backchannel.resolver import BackchannelChoice
    from app.ui.pet_window import PetWindow
    from app.voice.tts import TTSPreparedAudio

    class ProviderStub:
        def __init__(self) -> None:
            self.prepared: list[tuple[str, str | None]] = []
            self.spoken: list[tuple[str, str | None]] = []

        def prepare(self, text: str, tone: str | None = None) -> TTSPreparedAudio:
            self.prepared.append((text, tone))
            return TTSPreparedAudio(text=text, tone=tone, audio_path=Path("ready.wav"))

        def speak_prepared(
            self,
            handle: TTSPreparedAudio,
            on_started=None,  # type: ignore[no-untyped-def]
            on_finished=None,  # type: ignore[no-untyped-def]
        ) -> None:
            self.spoken.append((handle.text, handle.tone))
            if on_started is not None:
                on_started()
            _ = on_finished

        def discard_prepared(self, _handle: TTSPreparedAudio) -> None:
            pass

    class PortraitControllerStub:
        def __init__(self) -> None:
            self.segments: list[ChatSegment] = []

        def apply_for_segment(self, segment: ChatSegment) -> None:
            self.segments.append(segment)

    class SubtitleControllerStub:
        def __init__(self) -> None:
            self.texts: list[str] = []

        def set_speech(self, text: str, *, pulse: bool = False) -> None:
            self.texts.append(text)
            assert pulse is True

    class WindowStub:
        _display_backchannel = PetWindow._display_backchannel
        _backchannel_tts_wanted = PetWindow._backchannel_tts_wanted
        _backchannel_tts_active = PetWindow._backchannel_tts_active
        _backchannel_audio_key = PetWindow._backchannel_audio_key
        _prepare_backchannel_audio_cache = PetWindow._prepare_backchannel_audio_cache
        _backchannel_variant_audio_available = PetWindow._backchannel_variant_audio_available
        _resolve_backchannel_audio_path = PetWindow._resolve_backchannel_audio_path
        _play_backchannel_audio = PetWindow._play_backchannel_audio
        _request_backchannel_audio_playback = PetWindow._request_backchannel_audio_playback
        _handle_backchannel_audio_finished = PetWindow._handle_backchannel_audio_finished
        _discard_active_backchannel_audio = PetWindow._discard_active_backchannel_audio

        def __init__(self) -> None:
            self.backchannel_settings = BackchannelSettings(enabled=True, tts_enabled=True)
            self.tts_provider = ProviderStub()
            self._backchannel_prepared_audio = {}
            self._active_backchannel_audio = None
            self.subtitle_language = "zh"
            self.portrait_controller = PortraitControllerStub()
            self.subtitle_controller = SubtitleControllerStub()
            self.logged: list[tuple[str, dict[str, object] | None]] = []
            template = BackchannelTemplate(
                id="greeting",
                tone="中性",
                portrait="高兴满足",
                variants=(BackchannelVariant(ja="……おかえり。", zh="……欢迎回来。"),),
                intent="greeting_return",
                emotion="neutral",
            )
            self.backchannel_manifest = BackchannelManifest(templates=(template,))
            self.choice = BackchannelChoice(template, template.variants[0])

        def _log_interaction_stage(
            self,
            stage: str,
            payload: dict[str, object] | None = None,
        ) -> None:
            self.logged.append((stage, payload))

    window = WindowStub()
    window._prepare_backchannel_audio_cache()
    window._display_backchannel(window.choice)

    assert window.tts_provider.prepared == [("……おかえり。", "中性")]
    assert window.tts_provider.spoken == [("……おかえり。", "中性")]
    assert window.subtitle_controller.texts == ["……欢迎回来。"]
    assert ("backchannel_tts_requested", {"template": "greeting", "tone": "中性"}) in window.logged

    # 接话音频播完不立即补合成:对话中补合成会抢占回复分段的串行合成队列,
    # 统一推迟到 reply_completed 空闲时机。
    played_handle = window._active_backchannel_audio
    prepared_before = list(window.tts_provider.prepared)
    window._handle_backchannel_audio_finished(played_handle)
    assert window.tts_provider.prepared == prepared_before
    assert window._active_backchannel_audio is None


def test_pet_window_backchannel_audio_uses_manifest_audio_copy(tmp_path: Path) -> None:
    from app.backchannel.models import (
        BackchannelManifest,
        BackchannelTemplate,
        BackchannelVariant,
    )
    from app.backchannel.resolver import BackchannelChoice
    from app.ui.pet_window import PetWindow
    from app.voice.tts import TTSPreparedAudio

    source_audio = tmp_path / "line.wav"
    source_audio.write_bytes(b"RIFFbackchannel")
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text("{}", encoding="utf-8")

    class ProviderStub:
        def __init__(self) -> None:
            self.prepared: list[tuple[str, str | None]] = []
            self.spoken: list[TTSPreparedAudio] = []

        def prepare(self, text: str, tone: str | None = None) -> TTSPreparedAudio:
            self.prepared.append((text, tone))
            return TTSPreparedAudio(text=text, tone=tone)

        def speak_prepared(
            self,
            handle: TTSPreparedAudio,
            on_started=None,  # type: ignore[no-untyped-def]
            on_finished=None,  # type: ignore[no-untyped-def]
        ) -> None:
            self.spoken.append(handle)
            if on_finished is not None:
                on_finished()

    class WindowStub:
        _backchannel_tts_wanted = PetWindow._backchannel_tts_wanted
        _resolve_backchannel_audio_path = PetWindow._resolve_backchannel_audio_path
        _copy_backchannel_audio_for_playback = PetWindow._copy_backchannel_audio_for_playback
        _play_backchannel_audio = PetWindow._play_backchannel_audio
        _request_backchannel_audio_playback = PetWindow._request_backchannel_audio_playback
        _handle_backchannel_audio_finished = PetWindow._handle_backchannel_audio_finished

        def __init__(self) -> None:
            self.backchannel_settings = BackchannelSettings(enabled=True, tts_enabled=True)
            self.tts_provider = ProviderStub()
            self._active_backchannel_audio = None
            self._backchannel_prepared_audio = {}
            template = BackchannelTemplate(
                id="greeting",
                tone="中性",
                portrait="高兴满足",
                variants=(
                    BackchannelVariant(
                        ja="……おかえり。",
                        zh="……欢迎回来。",
                        audio="line.wav",
                    ),
                ),
                intent="greeting_return",
                emotion="neutral",
            )
            self.backchannel_manifest = BackchannelManifest(
                templates=(template,),
                source_path=manifest_path,
            )
            self.choice = BackchannelChoice(template, template.variants[0])
            self.logged: list[tuple[str, dict[str, object] | None]] = []

        def _log_interaction_stage(
            self,
            stage: str,
            payload: dict[str, object] | None = None,
        ) -> None:
            self.logged.append((stage, payload))

    window = WindowStub()
    window._play_backchannel_audio(window.choice)

    assert window.tts_provider.prepared == []
    assert len(window.tts_provider.spoken) == 1
    played = window.tts_provider.spoken[0]
    assert played.audio_path is not None
    assert played.audio_path != source_audio
    assert played.audio_path.read_bytes() == source_audio.read_bytes()
    assert ("backchannel_tts_requested", {"template": "greeting", "tone": "中性"}) in window.logged


def test_pet_window_backchannel_unready_audio_plays_subtitle_only() -> None:
    """缓存里音频未合成完(audio_path=None)时只显示字幕,不在对话中触发补合成,
    且未就绪句柄留在缓存中等待合成完成。"""
    from app.backchannel.models import (
        BackchannelManifest,
        BackchannelTemplate,
        BackchannelVariant,
    )
    from app.backchannel.resolver import BackchannelChoice
    from app.ui.pet_window import PetWindow
    from app.voice.tts import TTSPreparedAudio

    class ProviderStub:
        def __init__(self) -> None:
            self.prepared: list[str] = []
            self.spoken: list[str] = []

        def prepare(self, text: str, tone: str | None = None) -> TTSPreparedAudio:
            self.prepared.append(text)
            return TTSPreparedAudio(text=text, tone=tone)

        def speak_prepared(self, handle, on_started=None, on_finished=None):  # type: ignore[no-untyped-def]
            self.spoken.append(handle.text)

    class WindowStub:
        _backchannel_tts_wanted = PetWindow._backchannel_tts_wanted
        _backchannel_tts_active = PetWindow._backchannel_tts_active
        _backchannel_audio_key = PetWindow._backchannel_audio_key
        _resolve_backchannel_audio_path = PetWindow._resolve_backchannel_audio_path
        _play_backchannel_audio = PetWindow._play_backchannel_audio
        _request_backchannel_audio_playback = PetWindow._request_backchannel_audio_playback

        def __init__(self) -> None:
            self.backchannel_settings = BackchannelSettings(enabled=True, tts_enabled=True)
            self.tts_provider = ProviderStub()
            template = BackchannelTemplate(
                id="greeting",
                tone="中性",
                portrait="高兴满足",
                variants=(BackchannelVariant(ja="……おかえり。", zh="……欢迎回来。"),),
                intent="greeting_return",
                emotion="neutral",
            )
            self.choice = BackchannelChoice(template, template.variants[0])
            # 合成尚未完成的句柄(audio_path 为 None)
            unready = TTSPreparedAudio(text="……おかえり。", tone="中性")
            self._backchannel_prepared_audio = {
                ("greeting", "中性", "……おかえり。"): unready
            }

    window = WindowStub()
    window._play_backchannel_audio(window.choice)

    assert window.tts_provider.spoken == []      # 未就绪不播
    assert window.tts_provider.prepared == []    # 对话中不补合成
    assert len(window._backchannel_prepared_audio) == 1  # 句柄留在缓存继续等合成


def test_pet_window_reply_arrival_discards_unready_backchannel_prepares() -> None:
    """正式回复开始时,未合成完的接话请求让位(discard),已就绪音频保留。"""
    from app.ui.pet_window import PetWindow
    from app.voice.tts import TTSPreparedAudio

    class ProviderStub:
        def __init__(self) -> None:
            self.discarded: list[str] = []

        def discard_prepared(self, handle: TTSPreparedAudio) -> None:
            self.discarded.append(handle.text)

    class ControllerStub:
        def cancel(self) -> None:
            pass

    class WindowStub:
        _cancel_backchannel = PetWindow._cancel_backchannel
        _discard_unready_backchannel_audio = PetWindow._discard_unready_backchannel_audio
        _discard_active_backchannel_audio = PetWindow._discard_active_backchannel_audio

        def __init__(self) -> None:
            self.tts_provider = ProviderStub()
            self.backchannel_controller = ControllerStub()
            self._active_backchannel_audio = None
            ready = TTSPreparedAudio(text="ready", tone="中性", audio_path=Path("ok.wav"))
            unready = TTSPreparedAudio(text="unready", tone="中性")
            failed = TTSPreparedAudio(text="failed", tone="中性")
            failed.failed = True
            self._backchannel_prepared_audio = {
                ("a", "中性", "ready"): ready,
                ("b", "中性", "unready"): unready,
                ("c", "中性", "failed"): failed,
            }

    window = WindowStub()
    window._cancel_backchannel()

    assert sorted(window.tts_provider.discarded) == ["failed", "unready"]
    remaining = list(window._backchannel_prepared_audio.values())
    assert len(remaining) == 1 and remaining[0].text == "ready"


def test_pet_window_show_reply_segments_discards_backchannel_before_play() -> None:
    """回复分段进入串行 TTS 队列前,未就绪的接话 prepare 必须先让位。

    否则回复音频会排在一整队接话 prepare 之后,on_started 迟迟不触发,
    等待动效停不下来(回复卡在"等待中"),被让位的接话还会反复重排合成。
    """
    from app.ui.pet_window import PetWindow
    from app.voice.tts import TTSPreparedAudio

    class ProviderStub:
        def __init__(self) -> None:
            self.discarded: list[str] = []

        def discard_prepared(self, handle: TTSPreparedAudio) -> None:
            self.discarded.append(handle.text)

    class ControllerStub:
        def __init__(self) -> None:
            self.cancelled = 0

        def cancel(self) -> None:
            self.cancelled += 1

    class SubtitleStub:
        def __init__(self, provider: ProviderStub) -> None:
            self._provider = provider
            self.shown: list[list[ChatSegment]] = []
            # 记录 show_segments 触发那一刻"已被丢弃的接话",用于验证让位发生在排队之前
            self.discarded_before_show: list[str] | None = None

        def show_segments(self, segments: list[ChatSegment]) -> None:
            self.discarded_before_show = list(self._provider.discarded)
            self.shown.append(segments)

    class WindowStub:
        _show_reply_segments = PetWindow._show_reply_segments
        _cancel_backchannel = PetWindow._cancel_backchannel
        _discard_unready_backchannel_audio = PetWindow._discard_unready_backchannel_audio
        _discard_active_backchannel_audio = PetWindow._discard_active_backchannel_audio

        def __init__(self) -> None:
            self.tts_provider = ProviderStub()
            self.backchannel_controller = ControllerStub()
            self.subtitle_controller = SubtitleStub(self.tts_provider)
            self._active_backchannel_audio = None
            unready = TTSPreparedAudio(text="つなぎ", tone="中性")
            self._backchannel_prepared_audio = {("b", "中性", "つなぎ"): unready}
            self._exit_reply_history_review = lambda update_buttons=False: None
            self._remember_reply_history_segments = lambda segments: None

    window = WindowStub()
    segment = ChatSegment("本題だよ。", "中性", "正题来了。", "站立待机")
    window._show_reply_segments([segment])

    # 接话控制器已取消、未就绪 prepare 已丢弃,且二者都发生在 show_segments 之前
    assert window.backchannel_controller.cancelled == 1
    assert window.tts_provider.discarded == ["つなぎ"]
    assert window.subtitle_controller.discarded_before_show == ["つなぎ"]
    assert window.subtitle_controller.shown == [[segment]]


def test_pet_window_backchannel_audio_waits_for_tts_service_ready() -> None:
    """服务未就绪时预生成被门控跳过,预热成功回调后补做(避免首批 HTTP 静默失败)。"""
    from app.backchannel.models import (
        BackchannelManifest,
        BackchannelTemplate,
        BackchannelVariant,
    )
    from app.ui.pet_window import PetWindow
    from app.voice.tts import TTSPreparedAudio

    class ColdProviderStub:
        def __init__(self) -> None:
            self.service_ready = False
            self.prepared: list[tuple[str, str | None]] = []

        def prepare(self, text: str, tone: str | None = None) -> TTSPreparedAudio:
            self.prepared.append((text, tone))
            return TTSPreparedAudio(text=text, tone=tone, audio_path=Path("ready.wav"))

    class WindowStub:
        _apply_backchannel_settings = PetWindow._apply_backchannel_settings
        _backchannel_tts_wanted = PetWindow._backchannel_tts_wanted
        _backchannel_tts_active = PetWindow._backchannel_tts_active
        _prepare_backchannel_audio_cache = PetWindow._prepare_backchannel_audio_cache
        _backchannel_variant_audio_available = PetWindow._backchannel_variant_audio_available
        _resolve_backchannel_audio_path = PetWindow._resolve_backchannel_audio_path
        _handle_tts_ready_warmup_succeeded = PetWindow._handle_tts_ready_warmup_succeeded

        def __init__(self) -> None:
            self.backchannel_settings = BackchannelSettings(enabled=True, tts_enabled=True)
            self.tts_provider = ColdProviderStub()
            self._backchannel_prepared_audio: dict = {}
            self.discarded = 0
            template = BackchannelTemplate(
                id="greeting",
                tone="中性",
                portrait="高兴满足",
                variants=(BackchannelVariant(ja="……おかえり。", zh="……欢迎回来。"),),
                intent="greeting_return",
                emotion="neutral",
            )
            self.backchannel_manifest = BackchannelManifest(templates=(template,))

        def _discard_backchannel_audio_cache(self) -> None:
            self.discarded += 1

    window = WindowStub()
    # 服务冷启动中:应用设置不触发任何 prepare,也不丢弃缓存(配置仍然需要语音)。
    # stub 未提供 _start_tts_ready_warmup：调用不抛错即证明
    # _apply_backchannel_settings 不再重复预热（由调用方负责）。
    window._apply_backchannel_settings(window.backchannel_settings)
    assert window.tts_provider.prepared == []
    assert window.discarded == 0

    # 预热成功 → 回调补做首批合成。
    window.tts_provider.service_ready = True
    window._handle_tts_ready_warmup_succeeded("TTS 服务已就绪。")
    assert window.tts_provider.prepared == [("……おかえり。", "中性")]


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


def test_pet_window_syncs_plugin_chat_ui_widgets() -> None:
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


class _SignalStub:
    def __init__(self) -> None:
        self._callbacks = []

    def connect(self, callback) -> None:  # type: ignore[no-untyped-def]
        self._callbacks.append(callback)

    def emit(self, *args) -> None:  # type: ignore[no-untyped-def]
        for callback in list(self._callbacks):
            callback(*args)


class _TTSBundleResultStub:
    def __init__(
        self,
        work_dir: Path,
        provider: str = "gpt-sovits",
        python_path: Path | None = None,
        tts_config_path: Path | None = None,
    ) -> None:
        self.work_dir = work_dir
        self.provider = provider
        self.python_path = python_path
        self.tts_config_path = tts_config_path


def _make_tts_bundle_dialog_stub():
    class DialogStub:
        last = None
        instances = []

        def __init__(self, *_args, **_kwargs) -> None:
            self.args = _args
            self.kwargs = _kwargs
            self.succeeded = _SignalStub()
            self.finished = _SignalStub()
            self.show_count = 0
            self.raised = False
            self.activated = False
            self.stylesheets: list[str] = []
            DialogStub.last = self
            DialogStub.instances.append(self)

        def setStyleSheet(self, stylesheet: str) -> None:  # noqa: N802 - 匹配 Qt 接口名
            self.stylesheets.append(stylesheet)

        def styleSheet(self) -> str:  # noqa: N802 - 匹配 Qt 接口名
            return self.stylesheets[-1] if self.stylesheets else ""

        def show(self) -> None:
            self.show_count += 1

        def raise_(self) -> None:
            self.raised = True

        def activateWindow(self) -> None:
            self.activated = True

        def is_download_running(self) -> bool:
            return False

    return DialogStub


class _SignalStub:
    def __init__(self) -> None:
        self._slots: list = []

    def connect(self, slot) -> None:  # type: ignore[no-untyped-def]
        self._slots.append(slot)

    def emit(self, *args) -> None:  # type: ignore[no-untyped-def]
        for slot in list(self._slots):
            slot(*args)


def _install_tauri_settings_process_stub(  # type: ignore[no-untyped-def]
    monkeypatch,
    pet_window_module,
    *,
    start_result: bool = True,
    on_start=None,
):
    instances = []

    class TauriSettingsProcessStub:
        def __init__(self, *_args, **_kwargs) -> None:
            self.kwargs = _kwargs
            self.completed = _SignalStub()
            self.applied = _SignalStub()
            self.apply_requested = _SignalStub()
            self.cancelled = _SignalStub()
            self.failed = _SignalStub()
            self.layout_preview = _SignalStub()
            self.shutdown_called = False
            self.apply_responses: list[tuple[str, bool, str]] = []
            instances.append(self)

        def start(self) -> bool:
            if on_start is not None:
                on_start()
            return start_result

        def shutdown(self) -> None:
            self.shutdown_called = True

        def resolve_apply_request(self, request_id: str, *, ok: bool, error: str = "") -> None:
            self.apply_responses.append((request_id, ok, error))

    monkeypatch.setattr(pet_window_module, "TauriSettingsProcess", TauriSettingsProcessStub)
    return instances


def _build_tauri_settings_result(
    *,
    api_settings: ApiSettings | None = None,
    character_id: str = "sakura",
    portrait_scale_percent: int = 150,
    control_panel_width: int = 700,
    bubble_height: int = 180,
    control_panel_vertical_offset: int = 25,
    input_bar_offset: int = 10,
):
    from app.config.models import ApiConfigProfile, ModelSelectionSettings, ModelSlotSelection
    from app.ui.tauri_settings import (
        TauriApiResult,
        TauriCharacterResult,
        TauriPluginResult,
        TauriSettingsResult,
        TauriSystemBasicResult,
        TauriSystemExtraResult,
        TauriTtsResult,
    )

    settings = api_settings or ApiSettings(
        "https://api.changed.example.com/v1",
        "changed-key",
        "changed-model",
    )
    api_profile = ApiConfigProfile(
        id="default",
        alias="默认",
        base_url=settings.base_url,
        api_key=settings.api_key,
        models=(settings.model,),
    )
    return TauriSettingsResult(
        screen_awareness=ScreenAwarenessSettings(),
        mcp=MCPRuntimeSettings(),
        runtime_loop=RuntimeLoopSettings(max_agent_steps_per_turn=6),
        system_basic=TauriSystemBasicResult(),
        theme=DEFAULT_THEME_SETTINGS,
        character=TauriCharacterResult(
            character_id=character_id,
            portrait_scale_percent=portrait_scale_percent,
            control_panel_width=control_panel_width,
            bubble_height=bubble_height,
            control_panel_vertical_offset=control_panel_vertical_offset,
            input_bar_offset=input_bar_offset,
        ),
        api=TauriApiResult(
            settings=settings,
            profiles=[api_profile],
            model_selection=ModelSelectionSettings(
                chat=ModelSlotSelection(profile_id="default", model=settings.model)
            ),
        ),
        tts=TauriTtsResult(enabled=False, provider="none"),
        system_extra=TauriSystemExtraResult(),
    )


def test_show_settings_tauri_trial_layout_preview_applies_then_restores(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    import app.ui.pet_window as pet_window_module
    from app.ui.pet_window import PetWindow

    class SettingsServiceStub:
        def load_tts_settings(self, **_kwargs):  # type: ignore[no-untyped-def]
            return _minimal_tts_settings()

    class ApiClientStub:
        settings = ApiSettings("https://api.example.com/v1", "test-key", "test-model")

    class InputAnimatorStub:
        def __init__(self) -> None:
            self.force_visible = False

        def set_force_visible(self, value: bool) -> None:
            self.force_visible = value

    instances = _install_tauri_settings_process_stub(monkeypatch, pet_window_module)
    monkeypatch.setattr(
        pet_window_module,
        "resolve_tauri_settings_binary",
        lambda _base_dir: Path("sakura-settings.exe"),
    )

    window = _minimal_settings_window(
        PetWindow,
        SettingsServiceStub(),
        ApiClientStub(),
        object(),
    )
    window.input_bar_animator = InputAnimatorStub()
    window.show_settings()

    # 滑块拖动：实时应用但不持久化。
    instances[0].layout_preview.emit(
        {
            "portrait_scale_percent": 150,
            "control_panel_width": 720,
            "bubble_height": 200,
            "control_panel_vertical_offset": 30,
            "input_bar_offset": 12,
            "speech_font_size": 24,
            "name_font_size": 20,
            "input_font_size": 20,
            "button_font_size": 20,
        }
    )
    assert window.portrait_scale_percent == 150
    assert window.control_panel_width == 720
    assert window.bubble_height == 200
    assert window.control_panel_vertical_offset == 30
    assert window.input_bar_offset == 12
    assert window.speech_font_size == 24
    assert window.name_font_size == 20
    assert window.input_font_size == 20
    assert window.button_font_size == 20
    assert window.input_bar_animator.force_visible is True
    assert window.layout_persisted is False

    # 取消：回滚到打开设置前的布局，且仍不持久化。
    instances[0].cancelled.emit()
    assert window.portrait_scale_percent == 100
    assert window.control_panel_width == 640
    assert window.bubble_height == 128
    assert window.control_panel_vertical_offset == 0
    assert window.input_bar_offset == 0
    assert window.speech_font_size == 19
    assert window.name_font_size == 13
    assert window.input_font_size == 15
    assert window.button_font_size == 15
    assert window.input_bar_animator.force_visible is False
    assert window.layout_persisted is False
    assert window.tauri_settings_process is None


def test_show_settings_tauri_failure_restores_font_preview_and_force_state(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    import app.ui.pet_window as pet_window_module
    from app.ui.pet_window import PetWindow

    class SettingsServiceStub:
        def load_tts_settings(self, **_kwargs):  # type: ignore[no-untyped-def]
            return _minimal_tts_settings()

    class ApiClientStub:
        settings = ApiSettings("https://api.example.com/v1", "test-key", "test-model")

    class InputAnimatorStub:
        def __init__(self) -> None:
            self.force_visible = False

        def set_force_visible(self, value: bool) -> None:
            self.force_visible = value

    warnings: list[str] = []
    monkeypatch.setattr(
        pet_window_module,
        "resolve_tauri_settings_binary",
        lambda _base_dir: Path("sakura-settings.exe"),
    )
    monkeypatch.setattr(
        pet_window_module,
        "show_themed_warning",
        lambda _parent, _title, message: warnings.append(message),
    )
    instances = _install_tauri_settings_process_stub(monkeypatch, pet_window_module)
    window = _minimal_settings_window(
        PetWindow,
        SettingsServiceStub(),
        ApiClientStub(),
        object(),
    )
    window.input_bar_animator = InputAnimatorStub()
    window.show_settings()

    instances[0].layout_preview.emit({"speech_font_size": 24})
    assert window.speech_font_size == 24
    assert window.input_bar_animator.force_visible is True

    instances[0].failed.emit("settings crashed")

    assert window.speech_font_size == 19
    assert window.input_bar_animator.force_visible is False
    assert window.tauri_settings_process is None
    assert instances[0].shutdown_called is True
    assert warnings == ["settings crashed"]


def test_show_settings_suppresses_pet_topmost_before_process_start(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    import app.ui.pet_window as pet_window_module
    from app.ui.pet_window import PetWindow

    class SettingsServiceStub:
        def load_tts_settings(self, **_kwargs):  # type: ignore[no-untyped-def]
            return _minimal_tts_settings()

    class ApiClientStub:
        settings = ApiSettings("https://api.example.com/v1", "test-key", "test-model")

    topmost_at_start: list[bool] = []
    monkeypatch.setattr(
        pet_window_module,
        "resolve_tauri_settings_binary",
        lambda _base_dir: Path("sakura-settings.exe"),
    )
    instances = _install_tauri_settings_process_stub(
        monkeypatch,
        pet_window_module,
        on_start=lambda: topmost_at_start.append(
            bool(window._secondary_windows_suppress_topmost)
        ),
    )
    window = _minimal_settings_window(
        PetWindow,
        SettingsServiceStub(),
        ApiClientStub(),
        object(),
    )
    window._secondary_windows_suppress_topmost = False

    window.show_settings()

    assert topmost_at_start == [True]
    assert window._secondary_windows_suppress_topmost is True

    instances[0].cancelled.emit()

    assert window._secondary_windows_suppress_topmost is False


def test_show_settings_start_failure_restores_pet_topmost(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    import app.ui.pet_window as pet_window_module
    from app.ui.pet_window import PetWindow

    class SettingsServiceStub:
        def load_tts_settings(self, **_kwargs):  # type: ignore[no-untyped-def]
            return _minimal_tts_settings()

    class ApiClientStub:
        settings = ApiSettings("https://api.example.com/v1", "test-key", "test-model")

    topmost_at_start: list[bool] = []
    monkeypatch.setattr(
        pet_window_module,
        "resolve_tauri_settings_binary",
        lambda _base_dir: Path("sakura-settings.exe"),
    )
    _install_tauri_settings_process_stub(
        monkeypatch,
        pet_window_module,
        start_result=False,
        on_start=lambda: topmost_at_start.append(
            bool(window._secondary_windows_suppress_topmost)
        ),
    )
    window = _minimal_settings_window(
        PetWindow,
        SettingsServiceStub(),
        ApiClientStub(),
        object(),
    )
    window._secondary_windows_suppress_topmost = False

    assert window._try_show_tauri_settings() is False
    assert topmost_at_start == [True]
    assert window._secondary_windows_suppress_topmost is False


def test_show_settings_tauri_trial_save_failure_restores_preview_and_closes_provider(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    import app.ui.pet_window as pet_window_module
    from app.ui.pet_window import PetWindow
    from app.config.models import ApiConfigProfile, ModelSelectionSettings, ModelSlotSelection
    from app.ui.tauri_settings import (
        TauriApiResult,
        TauriCharacterResult,
        TauriSettingsResult,
        TauriSystemBasicResult,
        TauriSystemExtraResult,
        TauriTtsResult,
    )

    critical_messages: list[str] = []

    class SettingsServiceStub:
        def save_api_settings(self, _settings):  # type: ignore[no-untyped-def]
            raise OSError("api.yaml locked")

    class ApiClientStub:
        settings = ApiSettings("https://api.example.com/v1", "test-key", "test-model")

    class ProviderStub:
        def __init__(self) -> None:
            self.closed = False

        def close(self) -> None:
            self.closed = True

    provider = ProviderStub()
    monkeypatch.setattr(
        pet_window_module,
        "show_themed_critical",
        lambda _parent, _title, message: critical_messages.append(message),
    )

    window = _minimal_settings_window(
        PetWindow,
        SettingsServiceStub(),
        ApiClientStub(),
        object(),
    )
    window._create_tts_provider_from_settings = lambda _settings: provider
    window._tauri_original_layout = (100, 640, 128, 0, 0)
    window._tauri_initial_tts_settings = _minimal_tts_settings()
    window.portrait_scale_percent = 150
    window.control_panel_width = 700
    window.bubble_height = 180
    window.control_panel_vertical_offset = 25
    window.input_bar_offset = 10

    api_profile = ApiConfigProfile(
        id="default",
        alias="默认",
        base_url="https://api.changed.example.com/v1",
        api_key="changed-key",
        models=("changed-model",),
    )
    api_settings = ApiSettings(
        "https://api.changed.example.com/v1",
        "changed-key",
        "changed-model",
    )
    result = TauriSettingsResult(
        screen_awareness=ScreenAwarenessSettings(),
        mcp=MCPRuntimeSettings(),
        runtime_loop=RuntimeLoopSettings(),
        system_basic=TauriSystemBasicResult(),
        theme=DEFAULT_THEME_SETTINGS,
        character=TauriCharacterResult(
            character_id="sakura",
            portrait_scale_percent=150,
            control_panel_width=700,
            bubble_height=180,
            control_panel_vertical_offset=25,
            input_bar_offset=10,
        ),
        api=TauriApiResult(
            settings=api_settings,
            profiles=[api_profile],
            model_selection=ModelSelectionSettings(
                chat=ModelSlotSelection(profile_id="default", model="changed-model")
            ),
        ),
        tts=TauriTtsResult(enabled=False, provider="none"),
        system_extra=TauriSystemExtraResult(),
    )

    window._on_tauri_settings_completed(result)

    assert critical_messages
    assert provider.closed is True
    assert window.portrait_scale_percent == 100
    assert window.control_panel_width == 640
    assert window.bubble_height == 128
    assert window.control_panel_vertical_offset == 0
    assert window.input_bar_offset == 0
    assert window.layout_persisted is False
    assert window._tauri_initial_tts_settings is None
    assert window._tauri_original_layout is None


def test_show_settings_tauri_trial_layout_persist_failure_rolls_back_runtime(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    import app.ui.pet_window as pet_window_module
    from app.ui.pet_window import PetWindow

    critical_messages: list[str] = []

    class SettingsServiceStub:
        def save_api_settings(self, _settings):  # type: ignore[no-untyped-def]
            pass

        def save_api_profiles(self, _profiles):  # type: ignore[no-untyped-def]
            pass

        def save_model_selection(self, _selection):  # type: ignore[no-untyped-def]
            pass

        def save_tts_settings(self, _settings):  # type: ignore[no-untyped-def]
            pass

        def save_current_character_id(self, _registry, _character_id):  # type: ignore[no-untyped-def]
            pass

        def save_screen_awareness_settings(self, _settings):  # type: ignore[no-untyped-def]
            pass

        def save_mcp_runtime_settings(self, _settings):  # type: ignore[no-untyped-def]
            pass

        def save_runtime_loop_settings(self, _settings):  # type: ignore[no-untyped-def]
            pass

        def save_debug_log_settings(self, _settings):  # type: ignore[no-untyped-def]
            pass

        def save_system_values(self, _section, _values):  # type: ignore[no-untyped-def]
            pass

        def save_bubble_settings(self, _settings):  # type: ignore[no-untyped-def]
            pass

    class ApiClientStub:
        settings = ApiSettings("https://api.example.com/v1", "test-key", "test-model")

    class ProviderStub:
        def __init__(self) -> None:
            self.closed = False

        def close(self) -> None:
            self.closed = True

    provider = ProviderStub()
    monkeypatch.setattr(
        pet_window_module,
        "show_themed_critical",
        lambda _parent, _title, message: critical_messages.append(message),
    )

    window = _minimal_settings_window(
        PetWindow,
        SettingsServiceStub(),
        ApiClientStub(),
        object(),
    )
    window._create_tts_provider_from_settings = lambda _settings: provider
    window.raise_layout_persist_error = True
    window._tauri_original_layout = (100, 640, 128, 0, 0)
    window._tauri_initial_tts_settings = _minimal_tts_settings()
    window.portrait_scale_percent = 150
    window.control_panel_width = 700
    window.bubble_height = 180
    window.control_panel_vertical_offset = 25
    window.input_bar_offset = 10

    window._on_tauri_settings_completed(_build_tauri_settings_result())

    assert critical_messages
    assert provider.closed is True
    assert window.api_client.settings == ApiClientStub.settings
    assert window.agent_runtime.runtime_loop_settings == RuntimeLoopSettings()
    assert window.portrait_scale_percent == 100
    assert window.control_panel_width == 640
    assert window.bubble_height == 128
    assert window.control_panel_vertical_offset == 0
    assert window.input_bar_offset == 0
    assert window.layout_persisted is False
    assert window._tauri_initial_tts_settings is None
    assert window._tauri_original_layout is None


def test_tauri_settings_save_failure_rolls_back_config_files(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    import app.ui.pet_window as pet_window_module
    from app.ui.pet_window import PetWindow

    root = _ui_runtime_root("tauri_save_config_rollback")
    config_dir = root / "data" / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    api_path = config_dir / "api.yaml"
    mcp_path = config_dir / "mcp.yaml"
    old_api = "llm:\n  model: old-model\n"
    api_path.write_text(old_api, encoding="utf-8")
    critical_messages: list[str] = []

    class SettingsServiceStub:
        def save_api_settings(self, _settings):  # type: ignore[no-untyped-def]
            api_path.write_text("llm:\n  model: changed-model\n", encoding="utf-8")

        def save_api_profiles(self, _profiles):  # type: ignore[no-untyped-def]
            mcp_path.write_text("created: true\n", encoding="utf-8")

        def save_model_selection(self, _selection):  # type: ignore[no-untyped-def]
            pass

        def save_tts_settings(self, _settings):  # type: ignore[no-untyped-def]
            raise OSError("api.yaml locked")

    class ApiClientStub:
        settings = ApiSettings("https://api.example.com/v1", "test-key", "old-model")

    monkeypatch.setattr(
        pet_window_module,
        "show_themed_critical",
        lambda _parent, _title, message: critical_messages.append(message),
    )
    window = _minimal_settings_window(
        PetWindow,
        SettingsServiceStub(),
        ApiClientStub(),
        object(),
    )
    window.base_dir = root

    window._apply_tauri_settings_result(_build_tauri_settings_result(), final=True)

    assert critical_messages
    assert api_path.read_text(encoding="utf-8") == old_api
    assert not mcp_path.exists()
    assert window.api_client.settings == ApiClientStub.settings


def test_tauri_settings_apply_character_theme_saves_character_override() -> None:
    from app.ui.pet_window import PetWindow

    saved_theme_preferences: list[ThemeSettings] = []
    saved_overrides: list[tuple[str, ThemeSettings]] = []

    class SettingsServiceStub:
        def save_api_settings(self, _settings):  # type: ignore[no-untyped-def]
            pass

        def save_api_profiles(self, _profiles):  # type: ignore[no-untyped-def]
            pass

        def save_model_selection(self, _selection):  # type: ignore[no-untyped-def]
            pass

        def save_tts_settings(self, _settings):  # type: ignore[no-untyped-def]
            pass

        def save_current_character_id(self, _registry, _character_id):  # type: ignore[no-untyped-def]
            pass

        def save_screen_awareness_settings(self, _settings):  # type: ignore[no-untyped-def]
            pass

        def save_mcp_runtime_settings(self, _settings):  # type: ignore[no-untyped-def]
            pass

        def save_runtime_loop_settings(self, _settings):  # type: ignore[no-untyped-def]
            pass

        def save_theme_settings(self, settings):  # type: ignore[no-untyped-def]
            saved_theme_preferences.append(settings.normalized())

        def save_character_theme_override(self, character_id, settings):  # type: ignore[no-untyped-def]
            saved_overrides.append((character_id, settings.normalized()))

        def save_debug_log_settings(self, _settings):  # type: ignore[no-untyped-def]
            pass

        def save_system_values(self, _section, _values):  # type: ignore[no-untyped-def]
            pass

        def save_bubble_settings(self, _settings):  # type: ignore[no-untyped-def]
            pass

        def save_backchannel_settings(self, _settings):  # type: ignore[no-untyped-def]
            pass

        def save_memory_curation_settings(self, _settings):  # type: ignore[no-untyped-def]
            pass

    class ApiClientStub:
        settings = ApiSettings("https://api.example.com/v1", "test-key", "test-model")

    window = _minimal_settings_window(
        PetWindow,
        SettingsServiceStub(),
        ApiClientStub(),
        object(),
    )
    theme = ThemeSettings(primary_color="#123456")
    result = replace(_build_tauri_settings_result(), theme=theme, theme_changed=True)

    assert window._apply_tauri_settings_result(result, final=True) is True

    assert saved_theme_preferences == [theme.normalized()]
    assert saved_overrides == [("sakura", theme.normalized())]
    assert window.theme_settings == theme.normalized()


def test_tauri_settings_apply_default_theme_deletes_character_override() -> None:
    from app.ui.pet_window import PetWindow

    deleted_overrides: list[str] = []

    class SettingsServiceStub:
        def __getattr__(self, name: str):  # type: ignore[no-untyped-def]
            if name.startswith("save_"):
                return lambda *_args, **_kwargs: None
            raise AttributeError(name)

        def delete_character_theme_override(self, character_id):  # type: ignore[no-untyped-def]
            deleted_overrides.append(character_id)

    class ApiClientStub:
        settings = ApiSettings("https://api.example.com/v1", "test-key", "test-model")

    window = _minimal_settings_window(
        PetWindow,
        SettingsServiceStub(),
        ApiClientStub(),
        object(),
    )
    result = replace(
        _build_tauri_settings_result(),
        theme=DEFAULT_THEME_SETTINGS,
        theme_changed=True,
    )

    assert window._apply_tauri_settings_result(result, final=True) is True

    assert deleted_overrides == ["sakura"]


def test_tauri_settings_apply_updates_font_preview_rollback_baseline() -> None:
    from app.ui.pet_window import PetWindow
    from app.ui.tauri_settings import TauriSystemBasicResult

    class SettingsServiceStub:
        def __getattr__(self, name: str):  # type: ignore[no-untyped-def]
            if name.startswith("save_"):
                return lambda *_args, **_kwargs: None
            raise AttributeError(name)

    class ApiClientStub:
        settings = ApiSettings("https://api.example.com/v1", "test-key", "test-model")

    window = _minimal_settings_window(
        PetWindow,
        SettingsServiceStub(),
        ApiClientStub(),
        object(),
    )
    window._tauri_original_font_sizes = (19, 13, 15, 15)
    result = replace(
        _build_tauri_settings_result(),
        system_basic=TauriSystemBasicResult(
            speech_font_size=22,
            name_font_size=18,
            input_font_size=17,
            button_font_size=16,
        ),
    )

    assert window._apply_tauri_settings_result(result, final=False) is True
    assert window._tauri_original_font_sizes == (22, 18, 17, 16)

    window._apply_fonts_values(24, 20, 20, 20)
    window._on_tauri_settings_cancelled()

    assert (
        window.speech_font_size,
        window.name_font_size,
        window.input_font_size,
        window.button_font_size,
    ) == (22, 18, 17, 16)


def test_load_font_sizes_falls_back_and_clamps_invalid_config() -> None:
    from app.ui.pet_window import PetWindow

    class WindowStub:
        _load_speech_font_size = PetWindow._load_speech_font_size
        _load_name_font_size = PetWindow._load_name_font_size
        _load_input_font_size = PetWindow._load_input_font_size
        _load_button_font_size = PetWindow._load_button_font_size

        def _load_system_config_values(self, _section: str) -> dict[str, object]:
            return {
                "speech_font_size": "large",
                "name_font_size": 999,
                "input_font_size": -1,
                "button_font_size": "16",
            }

    window = WindowStub()

    assert window._load_speech_font_size() == 19
    assert window._load_name_font_size() == 20
    assert window._load_input_font_size() == 12
    assert window._load_button_font_size() == 16


def _tauri_settings_result_payload(theme_payload: dict[str, object]) -> dict[str, object]:
    return {
                "version": 3,
                "nonce": "nonce",
                "screen_awareness": {
                    "enabled": True,
                    "screen_context_enabled": True,
                    "check_interval_minutes": 5,
                    "cooldown_minutes": 8,
                    "screen_context_batch_limit": 4,
                    "screen_context_resolution": "1080p",
                },
                "mcp": {
                    "windows_enabled": True,
                },
                "runtime_loop": {
                    "max_agent_steps_per_turn": 99,
                    "max_tool_calls_per_step": 8,
                    "max_tool_calls_per_turn": 2,
                },
                "system_basic": {
                    "debug_log": {
                        "enabled": False,
                        "body_enabled": True,
                        "file_enabled": True,
                        "profile": "trace",
                        "stage_debug_overlay": True,
                        "stage_collision_mask": False,
                    },
                    "ui": {
                        "subtitle_typing_interval_ms": 0,
                        "reply_segment_pause_ms": 9999,
                    },
                    "bubble": {
                        "auto_hide_enabled": True,
                        "auto_hide_delay_seconds": 0,
                    },
                },
        "theme": theme_payload,
        "character": {
            "current_character_id": "sakura",
            "layout": {
                "portrait_scale_percent": 100,
                "control_panel_width": 640,
                "bubble_height": 128,
                "control_panel_vertical_offset": 0,
                "input_bar_offset": 0,
            },
        },
        "api": {
            "settings": {
                "timeout_seconds": 60,
                "temperature": None,
                "top_p": None,
                "max_tokens": None,
            },
            "profiles": [
                {
                    "id": "default",
                    "alias": "默认",
                    "base_url": "https://api.example.com/v1",
                    "api_key": "test-key",
                    "models": ["test-model"],
                }
            ],
            "model_selection": {
                "slots": {
                    "chat": {"profile_id": "default", "model": "test-model"},
                    "vision_chat": {"profile_id": "", "model": ""},
                    "memory_curation": {"profile_id": "", "model": ""},
                }
            },
        },
        "tts": {
            "enabled": False,
            "provider": "none",
            "api_url": "http://127.0.0.1:9880/tts",
            "work_dir": "",
            "python_path": "",
            "tts_config_path": "",
            "timeout_seconds": 60,
        },
        "system_extra": {
            "startup": {
                "launch_at_login": False,
                "launch_at_login_supported": True,
            },
            "backchannel": {
                "enabled": False,
                "mode": "rules",
                "delay_ms": 600,
                "probability": 1.0,
                "tts_enabled": False,
                "timeout_ms": 400,
            },
        },
        "memory": {
            "curation": {
                "trigger_turns": 8,
                "backfill_limit": 200,
            }
        },
        "plugins": {"enabled_by_id": {}},
    }


def test_tauri_settings_result_parser_normalizes_runtime_loop() -> None:
    from app.ui.tauri_settings import parse_tauri_settings_payload
    from app.ui.theme import theme_to_mapping

    theme_payload = theme_to_mapping(ThemeSettings(primary_color="#123456", ai_enabled=True))
    theme_payload["primary_color"] = "invalid"
    theme_payload["visual_effect_mode"] = "invalid"
    payload = _tauri_settings_result_payload(theme_payload)
    payload["character"]["layout"]["portrait_scale_percent"] = 999  # type: ignore[index]
    payload["api"]["settings"]["timeout_seconds"] = 999  # type: ignore[index]
    payload["system_extra"]["backchannel"]["delay_ms"] = 99999  # type: ignore[index]
    payload["memory"]["curation"]["trigger_turns"] = 999  # type: ignore[index]

    result = parse_tauri_settings_payload(payload, expected_nonce="nonce")

    assert result.mcp == MCPRuntimeSettings(windows_enabled=True)
    assert result.screen_awareness.screen_context_resolution == "1080p"
    assert result.runtime_loop == RuntimeLoopSettings(
        max_agent_steps_per_turn=12,
        max_tool_calls_per_step=8,
        max_tool_calls_per_turn=8,
    )
    assert result.system_basic.debug_log == DebugLogSettings(
        enabled=False,
        body_enabled=False,
        file_enabled=True,
        profile="trace",
        stage_debug_overlay=True,
        stage_collision_mask=False,
    )
    assert result.system_basic.subtitle_typing_interval_ms == 5
    assert result.system_basic.reply_segment_pause_ms == 3000
    assert result.system_basic.bubble == BubbleSettings(
        auto_hide_enabled=True,
        auto_hide_delay_seconds=1,
    )
    assert result.system_basic.speech_font_size == 19
    assert result.system_basic.name_font_size == 13
    assert result.system_basic.input_font_size == 15
    assert result.system_basic.button_font_size == 15
    assert result.theme.primary_color == DEFAULT_THEME_SETTINGS.primary_color
    assert result.theme.ai_enabled is True
    assert result.theme.visual_effect_mode == "gaussian_blur"
    assert result.character.portrait_scale_percent == 150
    assert result.api.settings.timeout_seconds == 600
    assert result.system_extra.backchannel.delay_ms == 5000
    assert result.memory_curation.enabled is True
    assert result.memory_curation.trigger_turns == 50


def test_tauri_settings_result_parser_accepts_hidden_empty_tts_config_path() -> None:
    from app.ui.tauri_settings import parse_tauri_settings_payload
    from app.ui.theme import theme_to_mapping

    payload = _tauri_settings_result_payload(theme_to_mapping(DEFAULT_THEME_SETTINGS))
    payload["tts"] = {
        "enabled": True,
        "provider": "gpt-sovits",
        "api_url": "http://127.0.0.1:9880/tts",
        "work_dir": "tts/g50",
        "python_path": "",
        "tts_config_path": "",
        "timeout_seconds": 60,
    }

    result = parse_tauri_settings_payload(payload, expected_nonce="nonce")

    assert result.tts.provider == "gpt-sovits"
    assert result.tts.work_dir == "tts/g50"
    assert result.tts.tts_config_path == ""


def test_tauri_settings_result_parser_normalizes_font_sizes() -> None:
    from app.ui.tauri_settings import parse_tauri_settings_payload
    from app.ui.theme import theme_to_mapping

    payload = _tauri_settings_result_payload(theme_to_mapping(DEFAULT_THEME_SETTINGS))
    ui = payload["system_basic"]["ui"]  # type: ignore[index]
    ui.update(  # type: ignore[union-attr]
        {
            "speech_font_size": "invalid",
            "name_font_size": 999,
            "input_font_size": -1,
            "button_font_size": None,
        }
    )

    result = parse_tauri_settings_payload(payload, expected_nonce="nonce")

    assert result.system_basic.speech_font_size == 19
    assert result.system_basic.name_font_size == 20
    assert result.system_basic.input_font_size == 12
    assert result.system_basic.button_font_size == 15


def test_tauri_settings_result_parser_reads_plugin_enabled_overrides() -> None:
    from app.ui.tauri_settings import parse_tauri_settings_payload
    from app.ui.theme import theme_to_mapping

    payload = _tauri_settings_result_payload(theme_to_mapping(DEFAULT_THEME_SETTINGS))
    payload["plugins"] = {
        "enabled_by_id": {"demo": False, "required": True},
        "settings_by_id": {"demo": {"main": {"enabled": True}}},
    }

    result = parse_tauri_settings_payload(payload, expected_nonce="nonce")

    assert result.plugins.enabled_by_id == {"demo": False, "required": True}
    assert result.plugins.settings_by_id == {"demo": {"main": {"enabled": True}}}


def test_tauri_settings_result_parser_recovers_empty_profile_models_from_slots() -> None:
    from app.ui.tauri_settings import parse_tauri_settings_payload
    from app.ui.theme import theme_to_mapping

    payload = _tauri_settings_result_payload(theme_to_mapping(DEFAULT_THEME_SETTINGS))
    payload["api"]["profiles"][0]["models"] = []  # type: ignore[index]

    result = parse_tauri_settings_payload(payload, expected_nonce="nonce")

    assert result.api.profiles[0].models == ("test-model",)
    assert result.api.settings.model == "test-model"


def test_tauri_settings_result_parser_reads_theme_changed_flag() -> None:
    from app.ui.tauri_settings import parse_tauri_settings_payload
    from app.ui.theme import theme_to_mapping

    payload = _tauri_settings_result_payload(theme_to_mapping(DEFAULT_THEME_SETTINGS))
    payload["theme_changed"] = False

    result = parse_tauri_settings_payload(payload, expected_nonce="nonce")

    assert result.theme_changed is False


def test_tauri_settings_result_parser_rejects_missing_system_basic() -> None:
    from app.ui.tauri_settings import parse_tauri_settings_payload

    payload = {
        "version": 3,
        "nonce": "nonce",
        "screen_awareness": {
            "enabled": True,
            "screen_context_enabled": True,
            "check_interval_minutes": 5,
            "cooldown_minutes": 8,
            "screen_context_batch_limit": 4,
        },
        "mcp": {"windows_enabled": True},
        "runtime_loop": {
            "max_agent_steps_per_turn": 2,
            "max_tool_calls_per_step": 2,
            "max_tool_calls_per_turn": 2,
        },
    }

    with pytest.raises(ValueError, match="系统基础配置"):
        parse_tauri_settings_payload(payload, expected_nonce="nonce")


def test_tauri_settings_result_parser_rejects_stale_protocol() -> None:
    from app.ui.tauri_settings import parse_tauri_settings_payload
    from app.ui.theme import theme_to_mapping

    payload = _tauri_settings_result_payload(theme_to_mapping(DEFAULT_THEME_SETTINGS))
    payload["version"] = 2

    with pytest.raises(ValueError, match="协议不匹配"):
        parse_tauri_settings_payload(payload, expected_nonce="nonce")


def test_tauri_settings_request_includes_theme_colors() -> None:
    from app.ui.tauri_settings import build_tauri_settings_request

    request = build_tauri_settings_request(
        ScreenAwarenessSettings(),
        mcp_settings=MCPRuntimeSettings(windows_enabled=False),
        runtime_loop_settings=RuntimeLoopSettings(),
        theme_settings=ThemeSettings(
            primary_color="#123456",
            panel_background_color="#abcdef",
            border_color="#654321",
        ),
        nonce="nonce",
    )

    assert request["theme"]["primary_color"] == "#123456"
    assert request["theme"]["panel_background_color"] == "#abcdef"
    assert request["theme"]["border_color"] == "#654321"
    assert request["theme"]["visual_effect_mode"] == "gaussian_blur"
    assert request["theme_defaults"]["primary_color"] == DEFAULT_THEME_SETTINGS.primary_color
    assert {"id": "primary_color", "label": "主题色"} in request["theme_fields"]
    assert {"id": "solid", "label": "纯色块"} in request["visual_effect_modes"]


def test_tauri_settings_request_preserves_empty_api_for_onboarding() -> None:
    from app.config.models import ModelSelectionSettings
    from app.llm.api_client import ApiSettings
    from app.ui.tauri_settings import build_tauri_settings_request

    request = build_tauri_settings_request(
        ScreenAwarenessSettings(),
        api_settings=ApiSettings("", "", ""),
        api_profiles=[],
        model_selection=ModelSelectionSettings(),
        onboarding=True,
        nonce="nonce",
    )

    assert request["onboarding"] is True
    assert request["api"]["profiles"] == []
    assert request["api"]["model_selection"]["slots"]["chat"] == {
        "profile_id": "",
        "model": "",
    }


def test_tauri_settings_frontend_has_two_step_onboarding() -> None:
    root = Path(__file__).resolve().parents[2] / "tools" / "settings-tauri" / "frontend"
    html = (root / "index.html").read_text(encoding="utf-8")
    script = (root / "settings.js").read_text(encoding="utf-8")
    styles = (root / "styles.css").read_text(encoding="utf-8")

    assert 'id="onboardingHead"' in html
    assert 'id="onboardingCharacterStep"' in html
    assert 'id="onboardingProviderStep"' in html
    assert 'id="onboardingBackButton"' in html
    assert 'body.classList.toggle("is-onboarding"' in script
    assert 'fields.saveButton.textContent = "完成并启动 Sakura"' in script
    assert 'showOnboardingStep("providers")' in script
    assert 'base_url: "通常以 /v1 结尾"' in script
    assert 'api_key: "通常以 sk- 开头"' in script
    assert "provider-url-hint" not in script
    assert "provider-url-hint" not in styles
    assert "body.is-onboarding .nav-card" in styles


def test_tauri_settings_request_includes_screen_resolution_estimates(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    import app.ui.tauri_settings as tauri_settings

    monkeypatch.setattr(tauri_settings, "_screen_estimate_size", lambda _widget: (3200, 2000))

    request = tauri_settings.build_tauri_settings_request(
        ScreenAwarenessSettings(screen_context_resolution="1080p"),
        nonce="nonce",
    )

    assert request["screen_awareness"]["screen_context_resolution"] == "1080p"
    assert request["screen_resolution_estimates"]["fullscreen"]["width"] == 3200
    assert request["screen_resolution_estimates"]["1080p"]["width"] == 1728
    assert request["screen_resolution_estimates"]["1080p"]["height"] == 1080


def test_tauri_settings_request_includes_font_sizes_and_layout_limits() -> None:
    from app.ui.tauri_settings import build_tauri_settings_request

    request = build_tauri_settings_request(
        ScreenAwarenessSettings(),
        speech_font_size=22,
        name_font_size=18,
        input_font_size=17,
        button_font_size=16,
        nonce="nonce",
    )

    assert request["version"] == 3
    assert request["system_basic"]["ui"] == {
        "subtitle_typing_interval_ms": 35,
        "reply_segment_pause_ms": 100,
        "speech_font_size": 22,
        "name_font_size": 18,
        "input_font_size": 17,
        "button_font_size": 16,
    }
    assert request["limits"]["speech_font_size"] == [10, 24]
    assert request["limits"]["name_font_size"] == [10, 20]
    assert request["limits"]["input_font_size"] == [12, 20]
    assert request["limits"]["button_font_size"] == [12, 20]
    assert request["limits"]["input_bar_offset"] == [0, 200]


def test_tauri_settings_request_includes_tts_provider_defaults(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    import app.ui.tauri_settings as tauri_settings
    from app.ui.tauri_settings import build_tauri_settings_request

    root = _ui_runtime_root("tauri_request_tts_defaults")
    gpt_work_dir = root / "tts" / "g50"
    genie_work_dir = root / "tts" / "cpu"

    def fake_default_provider_bundle_work_dir(
        provider: str,
        base_dir: Path,
        *,
        gpus: object = None,
    ) -> Path | None:
        assert base_dir == root
        assert gpus == []
        return {
            "gpt-sovits": gpt_work_dir,
            "genie-tts": genie_work_dir,
        }.get(provider)

    def fake_default_provider_bundle_notice(
        provider: str,
        base_dir: Path,
        *,
        gpus: object = None,
    ) -> str:
        assert base_dir == root
        assert gpus == []
        return "下载错了" if provider == "gpt-sovits" else ""

    monkeypatch.setattr(tauri_settings, "list_nvidia_gpus", lambda: [])
    monkeypatch.setattr(
        tauri_settings,
        "default_provider_bundle_work_dir",
        fake_default_provider_bundle_work_dir,
    )
    monkeypatch.setattr(
        tauri_settings,
        "default_provider_bundle_notice",
        fake_default_provider_bundle_notice,
    )

    request = build_tauri_settings_request(
        ScreenAwarenessSettings(),
        base_dir=root,
        nonce="nonce",
    )

    defaults = request["tts"]["provider_defaults"]
    provider_options = request["tts"]["providers"]
    assert "none" not in [option["id"] for option in provider_options]
    assert "关闭" not in [option["label"] for option in provider_options]
    assert defaults["gpt-sovits"]["api_url"] == "http://127.0.0.1:9880/tts"
    assert defaults["gpt-sovits"]["work_dir"] == str(gpt_work_dir)
    assert defaults["gpt-sovits"]["python_path"] == str(gpt_work_dir / "runtime" / "python.exe")
    assert defaults["gpt-sovits"]["notice"] == "下载错了"
    assert defaults["genie-tts"]["api_url"] == "http://127.0.0.1:9881/"
    assert defaults["genie-tts"]["work_dir"] == str(genie_work_dir)
    assert defaults["genie-tts"]["notice"] == ""
    assert defaults["custom-gpt-sovits"]["work_dir"] == ""
    assert defaults["custom-gpt-sovits"]["notice"] == ""


def test_tauri_settings_request_uses_real_tts_provider_when_disabled() -> None:
    from app.ui.tauri_settings import build_tauri_settings_request

    request = build_tauri_settings_request(
        ScreenAwarenessSettings(),
        tts_settings=replace(_minimal_tts_settings(), enabled=False, provider="none"),
        nonce="nonce",
    )

    assert request["tts"]["enabled"] is False
    assert request["tts"]["provider"] == "gpt-sovits"
    assert "none" not in [option["id"] for option in request["tts"]["providers"]]


def test_tauri_settings_request_includes_platform_desktop_mcp_metadata(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    import app.ui.tauri_settings as tauri_settings_module
    from app.agent.mcp import DesktopMCP
    from app.ui.tauri_settings import build_tauri_settings_request

    monkeypatch.setattr(
        tauri_settings_module,
        "resolve_desktop_mcp",
        lambda: DesktopMCP(server_name="macos", label="macOS MCP"),
    )

    request = build_tauri_settings_request(
        ScreenAwarenessSettings(),
        mcp_settings=MCPRuntimeSettings(windows_enabled=True),
        nonce="nonce",
    )

    assert request["mcp"]["windows_enabled"] is True
    assert request["mcp"]["desktop"] == {
        "supported": True,
        "label": "macOS MCP",
        "experimental_text": "实验性功能，供想要尝鲜的用户使用；可能不稳定，请谨慎开启",
    }


def test_tauri_settings_request_preserves_desktop_mcp_preference_when_unsupported(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    import app.ui.tauri_settings as tauri_settings_module
    from app.ui.tauri_settings import build_tauri_settings_request

    monkeypatch.setattr(tauri_settings_module, "resolve_desktop_mcp", lambda: None)

    request = build_tauri_settings_request(
        ScreenAwarenessSettings(),
        mcp_settings=MCPRuntimeSettings(windows_enabled=True),
        nonce="nonce",
    )

    assert request["mcp"]["windows_enabled"] is True
    assert request["mcp"]["desktop"]["supported"] is False


def test_tauri_settings_request_includes_plugins_and_memory_admin_metadata() -> None:
    from app.ui.tauri_settings import build_tauri_settings_request
    from app.plugins.models import PluginSettingsContribution, PluginSettingsField

    root = _ui_runtime_root("tauri_request_plugins")
    _write_settings_plugin_manifest(root, "demo", name="Demo 插件", enabled=False)

    request = build_tauri_settings_request(
        ScreenAwarenessSettings(),
        base_dir=root,
        plugin_settings_contributions=[
            PluginSettingsContribution(
                plugin_id="demo",
                section_id="demo_settings",
                title="Demo 设置",
                fields=(PluginSettingsField("enabled", "启用", "boolean", default=True),),
                load=lambda: {"enabled": False},
            )
        ],
        nonce="nonce",
    )

    plugin = request["plugins"]["items"][0]
    assert plugin["id"] == "demo"
    assert plugin["name"] == "Demo 插件"
    assert plugin["enabled"] is False
    assert plugin["permissions"] == ["plugin_settings"]
    assert plugin["entry"] == "plugin:DemoPlugin"
    assert request["plugins"]["permission_labels"]["plugin_settings"]["label"] == "插件设置"
    assert plugin["settings"][0]["section_id"] == "demo_settings"
    assert plugin["settings"][0]["values"]["enabled"] is False
    assert {"id": "core_profile", "label": "常驻档案"} in request["memory"]["layers"]
    assert request["memory"]["defaults"]["layer"] == "semantic"
    assert request["memory"]["defaults"]["source"] == "manual"


def test_apply_tauri_plugin_settings_skips_unchanged_values() -> None:
    from app.plugins.models import PluginSettingsContribution, PluginSettingsField
    from app.ui.tauri_settings import apply_tauri_plugin_settings

    saved: list[dict[str, object]] = []
    contribution = PluginSettingsContribution(
        plugin_id="demo",
        section_id="demo_settings",
        title="Demo 设置",
        fields=(PluginSettingsField("enabled", "启用", "boolean", default=True),),
        load=lambda: {"enabled": True},
        save=lambda values: saved.append(dict(values)),
    )

    assert apply_tauri_plugin_settings(
        [contribution],
        {"demo": {"demo_settings": {"enabled": True}}},
    ) is False
    assert saved == []

    assert apply_tauri_plugin_settings(
        [contribution],
        {"demo": {"demo_settings": {"enabled": False}}},
    ) is True
    assert saved == [{"enabled": False}]


def test_tauri_plugin_settings_message_does_not_claim_restart(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    import app.ui.pet_window as pet_window_module
    from app.ui.pet_window import PetWindow
    from app.ui.tauri_settings import TauriPluginResult

    class SettingsServiceStub:
        def __getattr__(self, name: str):  # type: ignore[no-untyped-def]
            if name.startswith("save_"):
                return lambda *_args, **_kwargs: None
            raise AttributeError(name)

    class ApiClientStub:
        settings = ApiSettings("https://api.example.com/v1", "test-key", "test-model")

    messages: list[str] = []
    monkeypatch.setattr(pet_window_module, "apply_tauri_plugin_settings", lambda *_args: True)
    monkeypatch.setattr(
        pet_window_module,
        "show_themed_information",
        lambda _parent, _title, message: messages.append(message),
    )
    window = _minimal_settings_window(
        PetWindow,
        SettingsServiceStub(),
        ApiClientStub(),
        object(),
    )
    result = replace(
        _build_tauri_settings_result(),
        plugins=TauriPluginResult(settings_by_id={"demo": {"main": {"enabled": False}}}),
    )

    assert window._apply_tauri_settings_result(result, final=True) is True

    assert messages == ["插件设置已保存并即时生效。"]


def test_tauri_settings_mcp_change_message_mentions_restart(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    import app.ui.pet_window as pet_window_module
    from app.ui.pet_window import PetWindow

    class SettingsServiceStub:
        def __getattr__(self, name: str):  # type: ignore[no-untyped-def]
            if name.startswith("save_"):
                return lambda *_args, **_kwargs: None
            raise AttributeError(name)

    class ApiClientStub:
        settings = ApiSettings("https://api.example.com/v1", "test-key", "test-model")

    messages: list[str] = []
    monkeypatch.setattr(
        pet_window_module,
        "show_themed_information",
        lambda _parent, _title, message: messages.append(message),
    )
    window = _minimal_settings_window(
        PetWindow,
        SettingsServiceStub(),
        ApiClientStub(),
        object(),
    )
    result = replace(
        _build_tauri_settings_result(),
        mcp=MCPRuntimeSettings(windows_enabled=True),
    )

    assert window._apply_tauri_settings_result(result, final=True) is True

    assert messages == ["桌面控制 MCP 开关需要重启 Sakura 后才会生效。"]


def test_tauri_settings_request_includes_per_character_theme() -> None:
    from types import SimpleNamespace

    from app.ui.tauri_settings import build_tauri_settings_request
    from app.ui.theme import ThemeSettings

    profile = SimpleNamespace(
        id="sakura",
        display_name="Sakura",
        voice=None,
        theme_settings=ThemeSettings(primary_color="#abcdef"),
        theme_source="package",
    )
    registry = SimpleNamespace(profiles={"sakura": profile})
    request = build_tauri_settings_request(
        ScreenAwarenessSettings(),
        character_registry=registry,
        current_character=profile,
        character_theme_overrides={"sakura": ThemeSettings(primary_color="#112233")},
        nonce="nonce",
    )
    characters = request["character"]["characters"]
    assert len(characters) == 1
    assert characters[0]["theme"]["primary_color"] == "#112233"
    assert characters[0]["default_theme"]["primary_color"] == "#abcdef"
    # 每个角色都带齐全部配色字段，供切换/恢复默认时跟随换色。
    assert len(characters[0]["theme"]) == len(request["theme_fields"])
    assert len(characters[0]["default_theme"]) == len(request["theme_fields"])


def test_tauri_settings_frontend_uses_character_theme_for_reset() -> None:
    source = Path("tools/settings-tauri/frontend/settings.js").read_text(encoding="utf-8")

    assert "function selectedCharacterThemeDefaults()" in source
    assert "function selectedCharacterTheme()" in source
    assert "themeAiButton: document.getElementById(\"themeAiButton\")" in source
    assert "ttsTestButton: document.getElementById(\"ttsTestButton\")" in source
    assert "theme-color-popover" in source
    assert "hostCall(\"theme.generate_ai\"" in source
    assert "hostCall(\"theme.pick_screen_color\"" in source
    assert "hostCall(\"tts.test\"" in source
    assert "type = \"color\"" not in source
    assert "current.hide" not in source
    assert "theme_changed: themeChanged" in source
    assert "setThemeValues(request.theme);" in source
    assert "runThemeTransition(update)" in source
    assert "setThemeValues(selectedCharacterTheme(), { updateVisualEffect: false, animateTheme: true });" in source
    assert (
        "setThemeValues(selectedCharacterThemeDefaults(), { updateVisualEffect: false, animateTheme: true });"
        in source
    )


def test_tauri_settings_frontend_disables_dependent_controls() -> None:
    source = Path("tools/settings-tauri/frontend/settings.js").read_text(encoding="utf-8")
    styles = Path("tools/settings-tauri/frontend/styles.css").read_text(encoding="utf-8")

    assert "function setControlDisabled" in source
    assert "function syncDesktopMcpControl" in source
    assert "syncDesktopMcpControl(request.mcp);" in source
    assert "`${desktop.label} 桌面控制`" in source
    assert "修改后需重启 Sakura" in source
    assert "function syncBackchannelState" in source
    assert "fields.backchannelEnabled.addEventListener(\"change\", syncBackchannelState)" in source
    assert "setControlDisabled(fields.backchannelMode, !enabled);" in source
    assert "setControlDisabled(fields.backchannelDelay, !enabled);" in source
    assert "setControlDisabled(fields.backchannelProbability, !enabled);" in source
    assert "setControlDisabled(fields.backchannelTtsEnabled, !enabled || !ttsAvailable);" in source
    assert "setControlDisabled(profileSelect, inherited, { row: false });" in source
    assert ".model-slot-row.is-inherited .slot-controls .custom-select" in styles
    assert "overflow-x: hidden;" in styles


def test_tauri_settings_uses_dot_checkbox_style() -> None:
    styles = Path("tools/settings-tauri/frontend/styles.css").read_text(encoding="utf-8")

    assert 'input[type="checkbox"] {' in styles
    assert "appearance: none;" in styles
    assert 'input[type="checkbox"]::before' in styles
    assert 'input[type="checkbox"]:checked::before' in styles
    assert "accent-color:" not in styles


def test_qt_checkboxes_use_dot_but_menu_items_keep_checkmark() -> None:
    from app.ui.theme import build_runtime_log_window_stylesheet

    settings_styles = build_settings_dialog_stylesheet(DEFAULT_THEME_SETTINGS)
    log_styles = build_runtime_log_window_stylesheet(DEFAULT_THEME_SETTINGS)
    menu_styles = build_pet_window_stylesheet(DEFAULT_THEME_SETTINGS)

    assert "selection-dot.svg" in settings_styles
    assert "selection-dot.svg" in log_styles
    assert "menu-check.svg" in menu_styles


def test_tauri_settings_round_trips_hidden_log_profile() -> None:
    from app.ui.tauri_settings import build_tauri_settings_request

    request = build_tauri_settings_request(
        ScreenAwarenessSettings(),
        debug_log_settings=DebugLogSettings(profile="trace"),
        nonce="nonce",
    )
    source = Path("tools/settings-tauri/frontend/settings.js").read_text(encoding="utf-8")

    assert request["system_basic"]["debug_log"]["profile"] == "trace"
    assert "profile: request.system_basic.debug_log.profile" in source


def test_tauri_settings_labels_body_log_as_model_reply_only() -> None:
    index = Path("tools/settings-tauri/frontend/index.html").read_text(encoding="utf-8")

    assert "完整模型回复正文" in index
    assert "完整请求 / 回复正文" not in index


def test_tauri_settings_frontend_locks_submission_and_uses_submitted_baseline() -> None:
    source = Path("tools/settings-tauri/frontend/settings.js").read_text(encoding="utf-8")

    assert "function setSubmissionBusy(busy)" in source
    assert "document.querySelectorAll(\"input, select, textarea, button\")" in source
    assert 'await invoke("apply_settings", { settings });' in source
    assert "settingsBaseline = JSON.stringify(settings);" in source


def test_tauri_settings_theme_ai_uses_vision_slot() -> None:
    from app.config.models import ApiConfigProfile, ModelSelectionSettings, ModelSlotSelection
    from app.ui.tauri_settings import _theme_ai_api_settings

    profiles = [
        ApiConfigProfile(
            id="chat",
            alias="聊天",
            base_url="https://chat.example/v1",
            api_key="chat-key",
            models=("chat-model",),
        ),
        ApiConfigProfile(
            id="vision",
            alias="视觉",
            base_url="https://vision.example/v1",
            api_key="vision-key",
            models=("vision-model",),
        ),
    ]
    settings = _theme_ai_api_settings(
        ApiSettings("https://default.example/v1", "default-key", "default-model"),
        profiles,
        ModelSelectionSettings(
            chat=ModelSlotSelection(profile_id="chat", model="chat-model"),
            vision_chat=ModelSlotSelection(profile_id="vision", model="vision-model"),
        ),
    )

    assert settings.base_url == "https://vision.example/v1"
    assert settings.model == "vision-model"


def test_tauri_character_rpc_validates_paths() -> None:
    from app.ui.tauri_settings import dispatch_tauri_character_rpc

    root = _ui_runtime_root("tauri_char_path_validation")
    bad_suffix = root / "bad.txt"
    bad_suffix.write_text("not an archive", encoding="utf-8")

    with pytest.raises(ValueError, match="扩展名"):
        dispatch_tauri_character_rpc(
            root,
            "character.import_archive",
            {"path": str(bad_suffix)},
        )
    with pytest.raises(ValueError, match="文件不存在"):
        dispatch_tauri_character_rpc(
            root,
            "character.import_voice_archive",
            {"path": str(root / "missing.voice"), "character_id": "sakura"},
        )
    with pytest.raises(ValueError, match="导出目录不存在"):
        dispatch_tauri_character_rpc(
            root,
            "character.export_archive",
            {"path": str(root / "missing" / "sakura.char"), "character_id": "sakura", "kind": "card"},
        )


def test_tauri_settings_dispatches_studio_launch_callback() -> None:
    qtwidgets = pytest.importorskip("PySide6.QtWidgets")
    if not hasattr(qtwidgets, "QApplication"):
        pytest.skip("当前测试环境只提供了 PySide6 stub。")
    qtwidgets.QApplication.instance() or qtwidgets.QApplication([])

    from app.ui.tauri_settings import TauriSettingsProcess

    calls: list[str] = []
    process = TauriSettingsProcess(
        base_dir=Path("."),
        settings=ScreenAwarenessSettings(),
        studio_launcher=lambda character_id: calls.append(character_id or "") or True,
    )

    result = process._dispatch_rpc("studio.launch", {"character_id": "sakura"})

    assert calls == ["sakura"]
    assert result["message"] == "角色工作室已打开。"


def test_tauri_settings_dispatches_studio_launch_refreshes_characters() -> None:
    qtwidgets = pytest.importorskip("PySide6.QtWidgets")
    if not hasattr(qtwidgets, "QApplication"):
        pytest.skip("当前测试环境只提供了 PySide6 stub。")
    qtwidgets.QApplication.instance() or qtwidgets.QApplication([])

    from app.ui.tauri_settings import TauriSettingsProcess

    root = _ui_runtime_root("tauri_studio_refresh")
    character_dir = root / "characters" / "demo"
    character_dir.mkdir(parents=True)
    (character_dir / "card.md").write_text("card", encoding="utf-8")
    (character_dir / "portrait.png").write_bytes(b"png")
    (character_dir / "character.json").write_text(
        json.dumps(
            {
                "id": "demo",
                "display_name": "Demo",
                "card": "card.md",
                "portrait": {"default": "portrait.png"},
            }
        ),
        encoding="utf-8",
    )
    process = TauriSettingsProcess(
        base_dir=root,
        settings=ScreenAwarenessSettings(),
        studio_launcher=lambda _character_id: {
            "refresh_characters": True,
            "current_character_id": "",
        },
    )

    result = process._dispatch_rpc("studio.launch", {"character_id": ""})

    assert result["current_character_id"] == "demo"
    assert result["characters"][0]["id"] == "demo"
    assert result["message"] == "角色列表已刷新。"


def test_tauri_settings_dispatches_studio_launch_refreshes_empty_characters() -> None:
    qtwidgets = pytest.importorskip("PySide6.QtWidgets")
    if not hasattr(qtwidgets, "QApplication"):
        pytest.skip("当前测试环境只提供了 PySide6 stub。")
    qtwidgets.QApplication.instance() or qtwidgets.QApplication([])

    from app.ui.tauri_settings import TauriSettingsProcess

    root = _ui_runtime_root("tauri_studio_refresh_empty")
    process = TauriSettingsProcess(
        base_dir=root,
        settings=ScreenAwarenessSettings(),
        studio_launcher=lambda _character_id: {
            "refresh_characters": True,
            "current_character_id": "",
        },
    )

    result = process._dispatch_rpc("studio.launch", {"character_id": ""})

    assert result["current_character_id"] == ""
    assert result["characters"] == []
    assert result["message"] == "角色列表已刷新。"


def test_tauri_settings_dispatches_studio_launch_failure() -> None:
    qtwidgets = pytest.importorskip("PySide6.QtWidgets")
    if not hasattr(qtwidgets, "QApplication"):
        pytest.skip("当前测试环境只提供了 PySide6 stub。")
    qtwidgets.QApplication.instance() or qtwidgets.QApplication([])

    from app.ui.tauri_settings import TauriSettingsProcess

    process = TauriSettingsProcess(
        base_dir=Path("."),
        settings=ScreenAwarenessSettings(),
        studio_launcher=lambda _character_id: False,
    )

    with pytest.raises(ValueError, match="角色工作室"):
        process._dispatch_rpc("studio.launch", {"character_id": "sakura"})


def test_tauri_settings_frontend_has_single_character_editor_button() -> None:
    index = Path("tools/settings-tauri/frontend/index.html").read_text(encoding="utf-8")
    source = Path("tools/settings-tauri/frontend/settings.js").read_text(encoding="utf-8")

    assert 'id="characterEditorButton"' in index
    assert 'class="character-select-controls"' in index
    assert "characterStudioCurrentButton" not in index
    assert "characterStudioOpenButton" not in index
    assert "角色工作室</legend>" not in index
    assert 'hostCall("studio.launch", { character_id: character?.id || "" })' in source
    assert "fields.characterEditorButton.disabled = characterArchiveBusy;" in source
    assert "characterStudioCurrentButton" not in source
    assert "characterStudioOpenButton" not in source
    assert 'typeof result?.current_character_id === "string"' in source
    assert "if (applyTheme && selectedCharacter()) {" in source
    assert 'document.addEventListener("contextmenu", (event) => event.preventDefault());' in source
    studio_launch_source = source.split("async function launchCharacterStudio()", 1)[1].split(
        "function resourcesSnapshot()", 1
    )[0]
    assert "applyCharacterRpcResult(result, { dirty: true, applyTheme: true });" in studio_launch_source
    assert "} else if (result?.message) {" in studio_launch_source


def test_tauri_studio_frontend_matches_settings_language() -> None:
    index = Path("tools/studio-tauri/frontend/index.html").read_text(encoding="utf-8")
    source = Path("tools/studio-tauri/frontend/studio.js").read_text(encoding="utf-8")
    styles = Path("tools/studio-tauri/frontend/styles.css").read_text(encoding="utf-8")
    tauri_config = Path("tools/studio-tauri/src-tauri/tauri.conf.json").read_text(encoding="utf-8")

    assert "nav-card" in index
    assert "detail-card" in index
    assert "page-head" in index
    assert "settings-group" in index
    assert "角色工作室" in index
    assert "保存" in index
    assert 'id="studioCharacterSelect"' in index
    assert 'id="newCharacterButton"' in index
    assert 'id="createCharacterOverlay"' in index
    assert 'id="createCharacterForm"' in index
    assert 'role="dialog"' in index
    assert 'class="studio-character-bar"' in index
    assert index.index('class="studio-character-bar"') < index.index('class="page-head"')
    nav_labels = ["基础信息", "人设卡", "立绘", "语音模型", "参考语音", "配色"]
    nav_positions = [index.index(f'<span class="nav-item-label">{label}</span>') for label in nav_labels]
    assert nav_positions == sorted(nav_positions)
    assert 'data-page="voice-model"' in index
    assert 'data-page="reference-audio"' in index
    assert 'id="page-voice-model"' in index
    assert 'id="page-reference-audio"' in index
    assert 'id="replyToneInput"' not in index
    assert 'id="voiceEnabled"' in index
    assert 'id="gptModelPath"' in index
    assert 'id="sovitsModelPath"' in index
    assert 'id="defaultRefLang"' in index
    assert 'id="textLang"' in index
    assert 'id="referenceAudioList"' in index
    assert 'id="addReferenceAudioButton"' in index
    assert 'id="importPortraitFolderButton"' in index
    assert 'id="importReferenceAudioFolderButton"' in index
    assert 'id="discardDraftButton"' in index
    assert 'id="saveDraftButton"' in index
    assert 'id="publishButton"' in index
    assert 'id="defaultPortrait"' not in index
    assert 'data-page="library"' not in index
    assert 'id="page-library"' not in index
    assert 'id="characterSearch"' not in index
    assert 'id="refreshCharactersButton"' not in index
    assert "发布角色" in index
    assert "工作区" in source
    assert "已发布角色" in source
    assert "（草稿）" not in source
    assert "editingCharacterId" in source
    assert "confirmDiscardChanges" in source
    assert "function editorSnapshot()" in source
    assert "function validateThemeInputs()" in source
    assert "function validateExpressionInputs()" in source
    assert "function validateVoiceInputs()" in source
    assert "function renderReferenceAudios(" in source
    assert "function previewReferenceAudio(" in source
    assert "{ dirty: true }" in source
    assert "openCreateCharacterDialog" in source
    assert 'window.prompt("角色 ID' not in source
    assert 'document.addEventListener("contextmenu", (event) => event.preventDefault());' in source
    assert "hostCall(\"studio.list_characters\"" not in source
    assert "hostCall(\"studio.open_character\"" in source
    assert "hostCall(\"studio.create_character\"" in source
    assert "hostCall(\"studio.save_character\"" in source
    assert "hostCall(\"studio.import_portrait\"" in source
    assert "hostCall(\"studio.import_voice_model\"" in source
    assert "hostCall(\"studio.import_reference_audio\"" in source
    assert "hostCall(\"studio.import_portrait_folder\"" in source
    assert "hostCall(\"studio.import_reference_audio_folder\"" in source
    assert "hostCall(\"studio.save_workspace_draft\"" in source
    assert "hostCall(\"studio.discard_draft\"" in source
    assert "directory: true" in source
    assert "scheduleDraftAutosave" in source
    assert 'pathInput.readOnly = true' in source
    assert "hostCall(\"studio.load_reference_audio_preview\"" in source
    assert "hostCall(\"studio.export_archive\"" in source
    assert "include_voice: Boolean(collectDoc().voice)" in source
    assert 'class="theme-colors"' in index
    assert "themeLabels" not in source
    assert "request.theme_fields.forEach(({ id, label })" in source
    assert 'className = "theme-color-popover"' in source
    assert 'hostCall("studio.pick_screen_color")' in source
    assert "updateThemeFromRgbInputs" in source
    assert "updateThemeFromSvPointer" in source
    assert "updateThemeFromHuePointer" in source
    assert "enhanceSelect(fields.studioCharacterSelect)" in source
    assert "refreshSelect(fields.studioCharacterSelect)" in source
    assert "function saveWorkspaceDraft()" in source
    assert "function publishCharacter()" in source
    assert "character.is_installed" in source
    assert 'done.className = "primary-button"' in source
    assert 'event.key === "ArrowDown"' in source
    assert 'event.key === "Tab"' in source
    assert 'trigger.setAttribute("aria-labelledby"' in source
    assert 'menu.addEventListener("focusout"' in source
    assert "fields.studioCharacterSelect.__customSelect?.focus();" in source
    assert index.count("<svg") >= 6
    assert "--sakura-primary" in styles
    assert "--motion-medium" in styles
    assert ".settings-page.is-active" in styles
    assert ".theme-color-swatch" in styles
    assert ".theme-color-popover" in styles
    assert ".theme-sv-pad" in styles
    assert "grid-template-columns: 176px minmax(0, 1fr)" in styles
    assert ".custom-select__trigger" in styles
    assert ".studio-character-bar" in styles
    assert ".modal-overlay" in styles
    assert ".create-character-dialog" in styles
    assert ".reference-audio-row" in styles
    assert "#saveButton,\n#publishButton,\n.primary-button" in styles
    assert ".custom-select__group" in styles
    assert ".custom-select__dirty-dot" in styles
    assert "overflow-x: hidden" in styles
    assert "@media (max-width: 940px)" in styles
    assert ".studio-shell {\n    grid-template-columns: 1fr;" not in styles
    assert "media-src 'self' data: blob:" in tauri_config
    assert ".nav-card {\n    display: none;" not in styles


def test_resolve_tauri_settings_binary_uses_platform_specific_name(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    import app.ui.tauri_settings as tauri_settings_module

    root = _ui_runtime_root("tauri_settings_binary_platform")
    release_dir = root / "tools" / "settings-tauri" / "src-tauri" / "target" / "release"
    release_dir.mkdir(parents=True)
    windows_binary = release_dir / "sakura-settings.exe"
    macos_binary = release_dir / "sakura-settings"
    windows_binary.write_text("windows", encoding="utf-8")
    macos_binary.write_text("macos", encoding="utf-8")
    macos_binary.chmod(macos_binary.stat().st_mode | 0o100)

    monkeypatch.setattr(tauri_settings_module.sys, "platform", "win32")
    assert tauri_settings_module.resolve_tauri_settings_binary(root, environ={}) == windows_binary

    monkeypatch.setattr(tauri_settings_module.sys, "platform", "darwin")
    assert tauri_settings_module.resolve_tauri_settings_binary(root, environ={}) == macos_binary


def test_resolve_tauri_settings_binary_env_override_still_wins(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    import app.ui.tauri_settings as tauri_settings_module
    from app.ui.tauri_settings import TAURI_SETTINGS_BIN_ENV

    root = _ui_runtime_root("tauri_settings_binary_env")
    configured = root / "custom-settings"
    configured.write_text("custom", encoding="utf-8")
    release_dir = root / "tools" / "settings-tauri" / "src-tauri" / "target" / "release"
    release_dir.mkdir(parents=True)
    (release_dir / "sakura-settings.exe").write_text("windows", encoding="utf-8")

    monkeypatch.setattr(tauri_settings_module.sys, "platform", "win32")
    assert (
        tauri_settings_module.resolve_tauri_settings_binary(
            root,
            environ={TAURI_SETTINGS_BIN_ENV: str(configured)},
        )
        == configured
    )


def test_tauri_settings_process_schedules_bounded_focus_retries_after_start(
    monkeypatch,
) -> None:  # type: ignore[no-untyped-def]
    qtwidgets = pytest.importorskip("PySide6.QtWidgets")
    if not hasattr(qtwidgets, "QApplication"):
        pytest.skip("当前测试环境只提供了 PySide6 stub。")
    qtwidgets.QApplication.instance() or qtwidgets.QApplication([])

    import app.ui.tauri_settings as tauri_settings

    scheduled: list[tuple[int, object]] = []

    class FakeQProcess:
        def __init__(self) -> None:
            self.writes: list[bytes] = []

        def write(self, data: bytes) -> int:
            self.writes.append(bytes(data))
            return len(data)

    monkeypatch.setattr(tauri_settings.sys, "platform", "win32")
    monkeypatch.setattr(
        tauri_settings.QTimer,
        "singleShot",
        lambda delay, _receiver, callback: scheduled.append((delay, callback)),
    )
    process = tauri_settings.TauriSettingsProcess(
        base_dir=Path("."),
        settings=ScreenAwarenessSettings(),
    )
    fake = FakeQProcess()
    process._process = fake
    process._request_payload = b'{"version": 3}'

    process._handle_started()

    assert fake.writes == [b'{"version": 3}\n']
    assert [delay for delay, _callback in scheduled] == list(
        tauri_settings.SETTINGS_FOCUS_RETRY_DELAYS_MS
    )


def test_tauri_settings_process_does_not_schedule_focus_retries_off_windows(
    monkeypatch,
) -> None:  # type: ignore[no-untyped-def]
    qtwidgets = pytest.importorskip("PySide6.QtWidgets")
    if not hasattr(qtwidgets, "QApplication"):
        pytest.skip("当前测试环境只提供了 PySide6 stub。")
    qtwidgets.QApplication.instance() or qtwidgets.QApplication([])

    import app.ui.tauri_settings as tauri_settings

    scheduled: list[int] = []

    class FakeQProcess:
        def write(self, data: bytes) -> int:
            return len(data)

    monkeypatch.setattr(tauri_settings.sys, "platform", "linux")
    monkeypatch.setattr(
        tauri_settings.QTimer,
        "singleShot",
        lambda delay, _callback: scheduled.append(delay),
    )
    process = tauri_settings.TauriSettingsProcess(
        base_dir=Path("."),
        settings=ScreenAwarenessSettings(),
    )
    process._process = FakeQProcess()
    process._request_payload = b"{}"

    process._handle_started()

    assert scheduled == []


def test_tauri_settings_process_focus_uses_forced_foreground_restore(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    qtwidgets = pytest.importorskip("PySide6.QtWidgets")
    if not hasattr(qtwidgets, "QApplication"):
        pytest.skip("当前测试环境只提供了 PySide6 stub。")
    qtwidgets.QApplication.instance() or qtwidgets.QApplication([])

    import app.ui.tauri_settings as tauri_settings

    calls: list[tuple[int, bool]] = []

    class FakeQProcess:
        def processId(self) -> int:  # noqa: N802
            return 4321

    monkeypatch.setattr(tauri_settings.sys, "platform", "win32")
    monkeypatch.setattr(
        tauri_settings,
        "_restore_windows_for_pid",
        lambda pid, *, force_foreground=False: calls.append((pid, force_foreground)) or True,
    )
    process = tauri_settings.TauriSettingsProcess(
        base_dir=Path("."),
        settings=ScreenAwarenessSettings(),
    )
    process._process = FakeQProcess()

    assert process.focus_window() is True
    assert calls == [(4321, True)]


def test_tauri_settings_process_stops_focus_retries_and_ignores_stale_process(
    monkeypatch,
) -> None:  # type: ignore[no-untyped-def]
    qtwidgets = pytest.importorskip("PySide6.QtWidgets")
    if not hasattr(qtwidgets, "QApplication"):
        pytest.skip("当前测试环境只提供了 PySide6 stub。")
    qtwidgets.QApplication.instance() or qtwidgets.QApplication([])

    from app.ui.tauri_settings import TauriSettingsProcess

    process = TauriSettingsProcess(
        base_dir=Path("."),
        settings=ScreenAwarenessSettings(),
    )
    active = object()
    focus_results = iter((False, True))
    focus_calls: list[bool] = []

    def focus_window() -> bool:
        focus_calls.append(True)
        return next(focus_results)

    monkeypatch.setattr(process, "focus_window", focus_window)
    process._process = active

    process._try_startup_focus(active)
    process._try_startup_focus(active)
    process._try_startup_focus(active)

    assert focus_calls == [True, True]
    assert process._startup_focus_complete is True

    process._startup_focus_complete = False
    process._process = object()
    process._try_startup_focus(active)
    process._done = True
    process._process = active
    process._try_startup_focus(active)

    assert focus_calls == [True, True]


def test_tauri_settings_process_parses_preview_and_result_lines() -> None:
    qtwidgets = pytest.importorskip("PySide6.QtWidgets")
    if not hasattr(qtwidgets, "QApplication"):
        pytest.skip("当前测试环境只提供了 PySide6 stub。")
    qtwidgets.QApplication.instance() or qtwidgets.QApplication([])

    from app.ui.tauri_settings import (
        TAURI_LAYOUT_PREVIEW_MARKER,
        TAURI_SETTINGS_RESULT_MARKER,
        TauriSettingsProcess,
    )
    from app.ui.theme import theme_to_mapping

    process = TauriSettingsProcess(base_dir=Path("."), settings=ScreenAwarenessSettings())
    process._nonce = "nonce"

    class FakeQProcess:
        def __init__(self, chunk: bytes) -> None:
            self._chunk = chunk

        def readAllStandardOutput(self) -> bytes:
            chunk, self._chunk = self._chunk, b""
            return chunk

    received: list[object] = []
    completed: list[object] = []
    process.layout_preview.connect(received.append)
    process.completed.connect(completed.append)

    marker = TAURI_LAYOUT_PREVIEW_MARKER.encode()
    result_marker = TAURI_SETTINGS_RESULT_MARKER.encode()
    payload = _tauri_settings_result_payload(theme_to_mapping(DEFAULT_THEME_SETTINGS))
    payload_line = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    # 噪声行被忽略；预览行可以跨两次读取拼接。
    process._process = FakeQProcess(b"webview noise\n" + marker + b'{"portrait_scale_percent": 150,')
    process._handle_stdout()
    assert received == []

    process._process = FakeQProcess(
        b' "control_panel_width": 720}\n' + result_marker + payload_line[:80]
    )
    process._handle_stdout()
    assert received == [{"portrait_scale_percent": 150, "control_panel_width": 720}]
    assert completed == []

    process._process = FakeQProcess(payload_line[80:])
    process._handle_stdout(flush=True)
    assert len(completed) == 1
    assert completed[0].screen_awareness.enabled is True


def test_tauri_settings_process_routes_keep_open_result_to_applied() -> None:
    qtwidgets = pytest.importorskip("PySide6.QtWidgets")
    if not hasattr(qtwidgets, "QApplication"):
        pytest.skip("当前测试环境只提供了 PySide6 stub。")
    qtwidgets.QApplication.instance() or qtwidgets.QApplication([])

    from app.ui.tauri_settings import TAURI_SETTINGS_RESULT_MARKER, TauriSettingsProcess
    from app.ui.theme import theme_to_mapping

    class FakeQProcess:
        def __init__(self, chunk: bytes) -> None:
            self._chunk = chunk

        def readAllStandardOutput(self) -> bytes:
            chunk, self._chunk = self._chunk, b""
            return chunk

    process = TauriSettingsProcess(base_dir=Path("."), settings=ScreenAwarenessSettings())
    process._nonce = "nonce"
    applied: list[object] = []
    completed: list[object] = []
    process.applied.connect(applied.append)
    process.completed.connect(completed.append)

    payload = _tauri_settings_result_payload(theme_to_mapping(DEFAULT_THEME_SETTINGS))
    payload["keep_open"] = True
    line = TAURI_SETTINGS_RESULT_MARKER + json.dumps(payload, ensure_ascii=False) + "\n"
    process._process = FakeQProcess(line.encode("utf-8"))

    process._handle_stdout()

    assert len(applied) == 1
    assert applied[0].screen_awareness.enabled is True
    assert completed == []


def test_tauri_settings_process_apply_rpc_waits_for_host_ack() -> None:
    qtwidgets = pytest.importorskip("PySide6.QtWidgets")
    if not hasattr(qtwidgets, "QApplication"):
        pytest.skip("当前测试环境只提供了 PySide6 stub。")
    qtwidgets.QApplication.instance() or qtwidgets.QApplication([])

    from app.ui.tauri_settings import TauriSettingsProcess
    from app.ui.theme import theme_to_mapping

    class FakeQProcess:
        def __init__(self) -> None:
            self.writes: list[str] = []

        def write(self, data: bytes) -> int:
            text = data.decode("utf-8")
            self.writes.append(text)
            return len(data)

    process = TauriSettingsProcess(base_dir=Path("."), settings=ScreenAwarenessSettings())
    process._nonce = "nonce"
    process._process = FakeQProcess()
    applied: list[str] = []

    def _ack(request_id: str, result: object) -> None:
        applied.append(request_id)
        process.resolve_apply_request(request_id, ok=True)

    process.apply_requested.connect(_ack)
    payload = _tauri_settings_result_payload(theme_to_mapping(DEFAULT_THEME_SETTINGS))

    process._handle_rpc_request(
        json.dumps(
            {
                "id": "apply-1",
                "method": "settings.apply",
                "params": {"settings": payload},
            },
            ensure_ascii=False,
        )
    )

    assert applied == ["apply-1"]
    assert '"ok": true' in process._process.writes[-1]


def test_tauri_settings_apply_request_reports_save_failure(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    import app.ui.pet_window as pet_window_module
    from app.ui.pet_window import PetWindow

    critical_messages: list[str] = []

    class SettingsServiceStub:
        def save_api_settings(self, _settings):  # type: ignore[no-untyped-def]
            raise OSError("api.yaml locked")

    class ApiClientStub:
        settings = ApiSettings("https://api.example.com/v1", "test-key", "test-model")

    class ProcessStub:
        def __init__(self) -> None:
            self.responses: list[tuple[str, bool, str]] = []

        def resolve_apply_request(self, request_id: str, *, ok: bool, error: str = "") -> None:
            self.responses.append((request_id, ok, error))

    monkeypatch.setattr(
        pet_window_module,
        "show_themed_critical",
        lambda _parent, _title, message: critical_messages.append(message),
    )
    process = ProcessStub()
    window = _minimal_settings_window(
        PetWindow,
        SettingsServiceStub(),
        ApiClientStub(),
        object(),
    )
    window.tauri_settings_process = process

    window._on_tauri_settings_apply_requested("apply-1", _build_tauri_settings_result())

    assert critical_messages
    assert process.responses == [("apply-1", False, "Tauri 设置没有保存成功。")]
    assert window.tauri_settings_process is process


def test_tauri_settings_capability_allows_window_close() -> None:
    capability_path = Path("tools/settings-tauri/src-tauri/capabilities/default.json")
    capability = json.loads(capability_path.read_text(encoding="utf-8"))

    assert "core:window:allow-close" in capability["permissions"]
    assert "dialog:allow-open" in capability["permissions"]
    assert "dialog:allow-save" in capability["permissions"]


def test_tauri_settings_process_dispatches_screen_color_picker(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    qtwidgets = pytest.importorskip("PySide6.QtWidgets")
    if not hasattr(qtwidgets, "QApplication"):
        pytest.skip("当前测试环境只提供了 PySide6 stub。")
    qtwidgets.QApplication.instance() or qtwidgets.QApplication([])

    import app.ui.tauri_settings as tauri_settings
    from app.ui.tauri_settings import TauriSettingsProcess

    process = TauriSettingsProcess(base_dir=Path("."), settings=ScreenAwarenessSettings())
    monkeypatch.setattr(tauri_settings, "pick_screen_color", lambda: "#112233")

    assert process._dispatch_rpc("theme.pick_screen_color", {}) == {"color": "#112233"}

    monkeypatch.setattr(tauri_settings, "pick_screen_color", lambda: None)

    assert process._dispatch_rpc("theme.pick_screen_color", {}) == {"cancelled": True}


def test_tauri_settings_process_dispatches_memory_rpc_methods() -> None:
    qtwidgets = pytest.importorskip("PySide6.QtWidgets")
    if not hasattr(qtwidgets, "QApplication"):
        pytest.skip("当前测试环境只提供了 PySide6 stub。")
    qtwidgets.QApplication.instance() or qtwidgets.QApplication([])

    from app.ui.tauri_settings import TauriSettingsProcess

    class FakeMemoryStore:
        def __init__(self) -> None:
            self.calls: list[tuple[str, dict[str, object], bool, bool | None]] = []

        def search_memory(self, arguments, *, wait=True):  # type: ignore[no-untyped-def]
            self.calls.append(("search", dict(arguments), wait, None))
            return {"status": "loading", "memories": []}

        def create_memory(self, arguments, *, allow_sensitive=False, wait=True):  # type: ignore[no-untyped-def]
            self.calls.append(("create", dict(arguments), wait, allow_sensitive))
            return {"memory": {"id": "new", "content": arguments["content"]}, "ok": True}

        def update_memory(self, arguments, *, allow_sensitive=False, wait=True):  # type: ignore[no-untyped-def]
            self.calls.append(("update", dict(arguments), wait, allow_sensitive))
            return {"memory": {"id": arguments["id"], "content": arguments["content"]}, "ok": True}

        def forget_memory(self, arguments, *, wait=True):  # type: ignore[no-untyped-def]
            self.calls.append(("delete", dict(arguments), wait, None))
            if arguments["id"] == "bad":
                return {"status": "failed", "error": "boom", "memories": []}
            return {"memory": {"id": arguments["id"], "content": ""}}

    store = FakeMemoryStore()
    process = TauriSettingsProcess(
        base_dir=Path("."),
        settings=ScreenAwarenessSettings(),
        memory_store=store,
    )

    assert process._dispatch_rpc("memory.search", {"query": "x"})["status"] == "loading"
    assert process._dispatch_rpc("memory.upsert", {"content": "new"})["memory"]["id"] == "new"
    assert process._dispatch_rpc("memory.upsert", {"id": "m1", "content": "edit"})["memory"]["id"] == "m1"
    deleted = process._dispatch_rpc("memory.delete", {"ids": ["m1", "bad"]})

    assert deleted["deleted"] == [{"id": "m1", "content": ""}]
    assert deleted["failed"] == [{"id": "bad", "error": "boom"}]
    assert store.calls == [
        ("search", {"query": "x", "limit": 120}, False, None),
        ("create", {"content": "new"}, False, True),
        ("update", {"id": "m1", "content": "edit"}, False, True),
        ("delete", {"id": "m1"}, False, None),
        ("delete", {"id": "bad"}, False, None),
    ]


def test_tauri_settings_process_writes_memory_rpc_response_line() -> None:
    qtwidgets = pytest.importorskip("PySide6.QtWidgets")
    if not hasattr(qtwidgets, "QApplication"):
        pytest.skip("当前测试环境只提供了 PySide6 stub。")
    qtwidgets.QApplication.instance() or qtwidgets.QApplication([])

    from app.ui.tauri_settings import (
        TAURI_SETTINGS_RPC_MARKER,
        TAURI_SETTINGS_RPC_RESULT_MARKER,
        TauriSettingsProcess,
    )
    QApplication = qtwidgets.QApplication
    app = QApplication.instance() or QApplication([])

    class FakeMemoryStore:
        def search_memory(self, arguments, *, wait=True):  # type: ignore[no-untyped-def]
            return {
                "status": "ready",
                "count": 1,
                "memories": [{"id": "m1", "content": arguments["query"], "layer": "semantic"}],
            }

    class FakeQProcess:
        def __init__(self, chunk: bytes) -> None:
            self._chunk = chunk
            self.writes: list[bytes] = []

        def readAllStandardOutput(self) -> bytes:
            chunk, self._chunk = self._chunk, b""
            return chunk

        def write(self, data: bytes) -> int:
            self.writes.append(bytes(data))
            return len(data)

    request = {"id": "rpc-1", "method": "memory.search", "params": {"query": "主人"}}
    fake = FakeQProcess(
        (TAURI_SETTINGS_RPC_MARKER + json.dumps(request, ensure_ascii=False) + "\n").encode("utf-8")
    )
    process = TauriSettingsProcess(
        base_dir=Path("."),
        settings=ScreenAwarenessSettings(),
        memory_store=FakeMemoryStore(),
    )
    process._process = fake

    process._handle_stdout()

    assert _process_events_until(app, lambda: bool(fake.writes))
    line = b"".join(fake.writes).decode("utf-8").strip()
    assert line.startswith(TAURI_SETTINGS_RPC_RESULT_MARKER)
    payload = json.loads(line[len(TAURI_SETTINGS_RPC_RESULT_MARKER):])
    assert payload["id"] == "rpc-1"
    assert payload["ok"] is True
    assert payload["result"]["memories"][0]["content"] == "主人"


def test_tauri_settings_memory_rpc_runs_off_main_thread() -> None:
    qtwidgets = pytest.importorskip("PySide6.QtWidgets")
    if not hasattr(qtwidgets, "QApplication"):
        pytest.skip("当前测试环境只提供了 PySide6 stub。")
    qtwidgets.QApplication.instance() or qtwidgets.QApplication([])

    from app.ui.tauri_settings import (
        TAURI_SETTINGS_RPC_MARKER,
        TAURI_SETTINGS_RPC_RESULT_MARKER,
        TauriSettingsProcess,
    )

    class BlockingMemoryStore:
        def __init__(self) -> None:
            self.started = threading.Event()
            self.release = threading.Event()

        def search_memory(self, arguments, *, wait=True):  # type: ignore[no-untyped-def]
            self.started.set()
            assert self.release.wait(2)
            return {
                "status": "ready",
                "count": 1,
                "memories": [{"id": "m1", "content": arguments["query"], "layer": "semantic"}],
            }

    class FakeQProcess:
        def __init__(self, chunk: bytes) -> None:
            self._chunk = chunk
            self.writes: list[bytes] = []

        def readAllStandardOutput(self) -> bytes:
            chunk, self._chunk = self._chunk, b""
            return chunk

        def write(self, data: bytes) -> int:
            self.writes.append(bytes(data))
            return len(data)

    QApplication = qtwidgets.QApplication
    app = QApplication.instance() or QApplication([])
    store = BlockingMemoryStore()
    request = {"id": "rpc-1", "method": "memory.search", "params": {"query": "主人"}}
    fake = FakeQProcess(
        (TAURI_SETTINGS_RPC_MARKER + json.dumps(request, ensure_ascii=False) + "\n").encode("utf-8")
    )
    process = TauriSettingsProcess(
        base_dir=Path("."),
        settings=ScreenAwarenessSettings(),
        memory_store=store,
    )
    process._process = fake

    started_at = time.monotonic()
    process._handle_stdout()
    elapsed = time.monotonic() - started_at

    started = _process_events_until(app, lambda: store.started.is_set(), timeout_ms=3000)
    store.release.set()
    if not started:
        _process_events_until(app, lambda: not process._memory_rpcs, timeout_ms=3000)

    assert elapsed < 0.2
    assert not fake.writes
    assert started
    assert not fake.writes

    assert _process_events_until(app, lambda: bool(fake.writes), timeout_ms=3000)
    line = b"".join(fake.writes).decode("utf-8").strip()
    assert line.startswith(TAURI_SETTINGS_RPC_RESULT_MARKER)
    payload = json.loads(line[len(TAURI_SETTINGS_RPC_RESULT_MARKER):])
    assert payload["id"] == "rpc-1"
    assert payload["ok"] is True
    assert payload["result"]["memories"][0]["content"] == "主人"


def test_pet_window_retires_tts_provider_by_closing_it() -> None:
    from app.ui.pet_window import PetWindow

    calls: list[str] = []

    class ProviderStub:
        def close(self) -> None:
            calls.append("close")

    class MinimalWindow:
        _retire_tts_provider = PetWindow._retire_tts_provider
        _close_retired_tts_provider = PetWindow._close_retired_tts_provider

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
        _close_retired_tts_provider = PetWindow._close_retired_tts_provider

    window = MinimalWindow()
    window.retired_tts_providers = []
    provider = ProviderStub()

    window._retire_tts_provider(provider, keep_local_service=True)

    assert calls == ["detach", "close"]
    assert window.retired_tts_providers == [provider]


def test_pet_window_defers_closing_provider_with_inflight_warmup() -> None:
    # 防御「保存设置闪退」根因:退休的 provider 正被后台预热线程探测时,不能立即 close()
    # (主线程 close 与预热 ensure_ready 并发拆解服务进程会原生崩溃),应推迟到预热结束。
    from app.ui.pet_window import PetWindow

    calls: list[str] = []

    class ProviderStub:
        def close(self) -> None:
            calls.append("close")

    class MinimalWindow:
        _retire_tts_provider = PetWindow._retire_tts_provider
        _close_retired_tts_provider = PetWindow._close_retired_tts_provider
        _cleanup_tts_ready_warmup_worker = PetWindow._cleanup_tts_ready_warmup_worker

    window = MinimalWindow()
    window.retired_tts_providers = []
    window._tts_pending_provider_closes = []
    provider = ProviderStub()
    # 模拟该 provider 正有一个在途预热线程。
    window.tts_ready_warmup_thread = object()
    window.tts_ready_warmup_worker = object()
    window._tts_warmup_provider = provider

    window._retire_tts_provider(provider)

    # 预热在途:暂不 close,仅登记引用与待关闭项。
    assert calls == []
    assert window.retired_tts_providers == [provider]
    assert window._tts_pending_provider_closes == [(provider, False)]

    # 预热线程结束 → cleanup 槽补关被推迟的 provider。
    window._cleanup_tts_ready_warmup_worker()

    assert calls == ["close"]
    assert window._tts_warmup_provider is None
    assert window._tts_pending_provider_closes == []


def test_pet_window_retires_other_provider_immediately_during_warmup() -> None:
    # 预热线程绑定的是另一个 provider 时,退休当前 provider 不受影响,应立即 close。
    from app.ui.pet_window import PetWindow

    calls: list[str] = []

    class ProviderStub:
        def __init__(self, name: str) -> None:
            self.name = name

        def close(self) -> None:
            calls.append(f"close:{self.name}")

    class MinimalWindow:
        _retire_tts_provider = PetWindow._retire_tts_provider
        _close_retired_tts_provider = PetWindow._close_retired_tts_provider

    window = MinimalWindow()
    window.retired_tts_providers = []
    window._tts_pending_provider_closes = []
    warming = ProviderStub("warming")
    retiring = ProviderStub("retiring")
    window.tts_ready_warmup_thread = object()
    window._tts_warmup_provider = warming

    window._retire_tts_provider(retiring)

    assert calls == ["close:retiring"]
    assert window._tts_pending_provider_closes == []
    assert window.retired_tts_providers == [retiring]


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


def test_tts_provider_rebuild_only_when_config_or_character_changed() -> None:
    """配置与角色都未变时不重建 provider,避免在 TTS 探测期退休正被探测的 provider。"""
    from app.ui.pet_window import _tts_provider_needs_rebuild

    class ProviderStub:
        def __init__(self, settings: GPTSoVITSTTSSettings) -> None:
            self.settings = settings

    class OtherProviderStub:
        def __init__(self, settings: GPTSoVITSTTSSettings) -> None:
            self.settings = settings

    settings = replace(_minimal_tts_settings(), enabled=True)
    old = ProviderStub(settings)

    # 配置等价且角色未变:不重建。
    assert not _tts_provider_needs_rebuild(old, ProviderStub(settings), character_changed=False)
    # 角色变化:必须重建(新角色声线)。
    assert _tts_provider_needs_rebuild(old, ProviderStub(settings), character_changed=True)
    # TTS 配置变化:必须重建。
    assert _tts_provider_needs_rebuild(
        old,
        ProviderStub(replace(settings, api_url="http://127.0.0.1:9881/tts")),
        character_changed=False,
    )
    # provider 类型变化(如启停 TTS):必须重建。
    assert _tts_provider_needs_rebuild(old, OtherProviderStub(settings), character_changed=False)


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
        / uuid.uuid4().hex
        / name
    )
    root.mkdir(parents=True, exist_ok=True)
    return root


def _write_settings_plugin_manifest(
    root: Path,
    plugin_id: str,
    *,
    name: str,
    description: str = "用于测试插件管理页。",
    enabled: bool = True,
    priority: int = 10,
) -> None:
    plugin_dir = root / "plugins" / plugin_id
    plugin_dir.mkdir(parents=True, exist_ok=True)
    (plugin_dir / "plugin.yaml").write_text(
        f"""
api_version: 2
id: {plugin_id}
name: {name}
description: {description}
version: 1.0.0
entry: plugin:DemoPlugin
enabled: {str(enabled).lower()}
priority: {priority}
permissions:
  - plugin_settings
""".strip(),
        encoding="utf-8",
    )


def _write_fake_runtime_python(path: Path, content: str = "fake") -> None:
    path.write_text(content, encoding="utf-8")
    path.chmod(0o755)


def test_update_runtime_api_clients_wires_plugin_emitter_to_slot_clients() -> None:
    from app.config.models import (
        ApiConfigProfile,
        ModelSelectionSettings,
        ModelSlotSelection,
    )
    from app.llm.api_client import OpenAICompatibleClient
    from app.ui.pet_window import _update_runtime_api_clients

    def emit_event(_event: str, _payload: dict | None = None) -> None:
        pass

    class MemoryStoreStub:
        def reload_api_settings(self, *_args, **_kwargs):  # type: ignore[no-untyped-def]
            pass

    class MemoryCuratorStub:
        api_client = None

        def set_api_client(self, client):  # type: ignore[no-untyped-def]
            self.api_client = client

    window = type("WindowStub", (), {})()
    window.api_client = OpenAICompatibleClient(
        ApiSettings("https://base.example.com/v1", "base-key", "base-model")
    )
    window.agent_runtime = type(
        "RuntimeStub",
        (),
        {
            "api_client": window.api_client,
            "vision_api_client": None,
        },
    )()
    window.memory_store = MemoryStoreStub()
    window.memory_curator = MemoryCuratorStub()
    window._llm_event_emitter = emit_event

    profiles = [
        ApiConfigProfile("chat", "Chat", "https://chat.example.com/v1", "chat-key", ("chat-model",)),
        ApiConfigProfile(
            "vision", "Vision", "https://vision.example.com/v1", "vision-key", ("vision-model",)
        ),
        ApiConfigProfile(
            "memory", "Memory", "https://memory.example.com/v1", "memory-key", ("memory-model",)
        ),
    ]
    selection = ModelSelectionSettings(
        chat=ModelSlotSelection("chat", "chat-model"),
        vision_chat=ModelSlotSelection("vision", "vision-model"),
        memory_curation=ModelSlotSelection("memory", "memory-model"),
    )

    _update_runtime_api_clients(
        window,
        api_profiles=profiles,
        model_selection=selection,
        base_settings=window.api_client.settings,
    )

    assert window.agent_runtime.api_client._event_emit is emit_event
    assert window.agent_runtime.vision_api_client._event_emit is emit_event
    assert window.memory_curator.api_client._event_emit is emit_event


def test_registered_secondary_window_suppresses_topmost_until_hidden() -> None:
    import app.ui.pet_window as pet_window_module
    from app.ui.pet_window import PetWindow

    native_sync_events: list[bool] = []
    raise_events: list[str] = []

    class SecondaryWindowStub:
        visible = False

        def show(self) -> None:
            self.visible = True

        def isMinimized(self) -> bool:  # noqa: N802 - 匹配 Qt 接口名
            return False

        def windowState(self):  # noqa: N802 - 匹配 Qt 接口名
            return pet_window_module.Qt.WindowState.WindowNoState

        def setWindowState(self, _state) -> None:  # noqa: N802 - 匹配 Qt 接口名
            pass

        def raise_(self) -> None:  # noqa: N802 - 匹配 Qt 接口名
            pass

        def activateWindow(self) -> None:  # noqa: N802 - 匹配 Qt 接口名
            pass

    class Host:
        _present_registered_secondary_window = PetWindow._present_registered_secondary_window
        _register_secondary_window = PetWindow._register_secondary_window
        _sync_secondary_window_state = PetWindow._sync_secondary_window_state
        _is_secondary_window_visible = PetWindow._is_secondary_window_visible
        _set_secondary_windows_topmost_suppressed = PetWindow._set_secondary_windows_topmost_suppressed

        always_on_top_enabled = True

        def __init__(self) -> None:
            self._registered_secondary_windows = set()
            self._secondary_windows_suppress_topmost = False

        def _sync_native_topmost_state(self) -> None:
            native_sync_events.append(self._secondary_windows_suppress_topmost)

        def isVisible(self) -> bool:
            return True

        def raise_(self) -> None:
            raise_events.append("raise")

    host = Host()
    window = SecondaryWindowStub()

    host._register_secondary_window(window)  # type: ignore[arg-type]
    host._present_registered_secondary_window(window)  # type: ignore[arg-type]

    assert native_sync_events == [True]

    window.visible = False
    host._sync_secondary_window_state()

    assert native_sync_events == [True, False]
    assert raise_events == ["raise"]


def test_manual_screenshot_overlay_suppresses_topmost_until_destroyed(
    pet_window,
    monkeypatch,
    qtbot,
) -> None:  # type: ignore[no-untyped-def]
    from PySide6.QtCore import QRect
    from PySide6.QtGui import QPixmap

    native_sync_events: list[bool] = []
    desktop = QPixmap(80, 60)
    desktop.fill()

    pet_window.always_on_top_enabled = True
    pet_window._secondary_windows_suppress_topmost = False
    monkeypatch.setattr(
        pet_window,
        "_capture_virtual_desktop_pixmap",
        lambda: (desktop, QRect(0, 0, 80, 60)),
    )
    monkeypatch.setattr(
        pet_window,
        "_sync_native_topmost_state",
        lambda: native_sync_events.append(pet_window._secondary_windows_suppress_topmost),
    )
    monkeypatch.setattr(pet_window, "raise_", lambda: None)

    pet_window._show_manual_screenshot_overlay()

    overlay = pet_window.manual_screenshot_overlay
    assert overlay is not None
    assert overlay in pet_window._registered_secondary_windows
    assert pet_window._secondary_windows_suppress_topmost is True
    assert native_sync_events == [True]

    overlay.close()
    qtbot.waitUntil(lambda: pet_window.manual_screenshot_overlay is None)

    assert overlay not in pet_window._registered_secondary_windows
    assert pet_window._secondary_windows_suppress_topmost is False
    assert native_sync_events[0] is True
    assert native_sync_events[-1] is False
    assert not any(native_sync_events[1:])


def test_pet_window_syncs_topmost_while_tauri_studio_is_active() -> None:
    from app.ui.pet_window import PetWindow

    native_sync_events: list[bool] = []

    class Host:
        _sync_secondary_window_state = PetWindow._sync_secondary_window_state
        _is_secondary_window_visible = PetWindow._is_secondary_window_visible
        _set_secondary_windows_topmost_suppressed = (
            PetWindow._set_secondary_windows_topmost_suppressed
        )

        def __init__(self) -> None:
            self._registered_secondary_windows = set()
            self._secondary_windows_suppress_topmost = False
            self.tauri_settings_process = None
            self.tauri_studio_process = object()

        def _sync_native_topmost_state(self) -> None:
            native_sync_events.append(self._secondary_windows_suppress_topmost)

    Host()._sync_secondary_window_state()

    assert native_sync_events == [True]


def test_pet_window_syncs_topmost_for_all_registered_secondary_windows(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    import app.ui.pet_window as pet_window_module
    from app.ui.pet_window import PetWindow

    calls: list[tuple[str, str, bool | None]] = []

    class SecondaryWindowStub:
        def __init__(self, name: str, visible: bool = True) -> None:
            self.name = name
            self.visible = visible

        def isVisible(self) -> bool:  # noqa: N802 - 匹配 Qt 接口名
            return self.visible

    class Host:
        _sync_secondary_windows_topmost = PetWindow._sync_secondary_windows_topmost
        _is_secondary_window_visible = PetWindow._is_secondary_window_visible

        always_on_top_enabled = True

        def __init__(self) -> None:
            self.first = SecondaryWindowStub("first")
            self.second = SecondaryWindowStub("second")
            self.hidden = SecondaryWindowStub("hidden", visible=False)
            self._registered_secondary_windows = [self.first, self.second, self.hidden]

    monkeypatch.setattr(
        pet_window_module,
        "_configure_secondary_window",
        lambda window, *, keep_on_top: calls.append(("configure", window.name, keep_on_top)),
    )
    monkeypatch.setattr(
        pet_window_module,
        "_present_secondary_window",
        lambda window: calls.append(("present", window.name, None)),
    )

    Host()._sync_secondary_windows_topmost()

    assert calls == [
        ("configure", "first", True),
        ("present", "first", None),
        ("configure", "second", True),
        ("present", "second", None),
    ]


def test_secondary_window_quiesce_leaves_input_bar_polling_enabled() -> None:
    from app.ui.pet_window import PetWindow

    input_polling_events: list[bool] = []
    bubble_polling_events: list[bool] = []

    class ControllerStub:
        def __init__(self, events: list[bool]) -> None:
            self._events = events

        def set_polling_enabled(self, enabled: bool) -> None:
            self._events.append(enabled)

    class Host:
        _set_secondary_windows_background_quiesced = (
            PetWindow._set_secondary_windows_background_quiesced
        )

        def __init__(self) -> None:
            self._secondary_windows_background_quiesced = False
            self.input_bar_animator = ControllerStub(input_polling_events)
            self.bubble_auto_hide = ControllerStub(bubble_polling_events)

    host = Host()

    host._set_secondary_windows_background_quiesced(True)
    host._set_secondary_windows_background_quiesced(False)

    assert input_polling_events == []
    assert bubble_polling_events == [False, True]


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


def test_main_requires_initial_setup_until_character_and_chat_provider_exist() -> None:
    import main as sakura_main

    root = _ui_runtime_root("initial_setup_required")
    assert sakura_main._initial_setup_required(root)

    character_dir = root / "characters" / "demo"
    character_dir.mkdir(parents=True)
    (character_dir / "character.json").write_text("{}", encoding="utf-8")
    assert sakura_main._initial_setup_required(root)

    api_config = root / "data" / "config" / "api.yaml"
    api_config.parent.mkdir(parents=True)
    api_config.write_text(
        """
llm:
  base_url: https://api.example.com/v1
  api_key: key
  model: model
api_profiles:
  - id: default
    alias: 默认
    base_url: https://api.example.com/v1
    api_key: key
    models:
      - name: model
model_slots:
  chat:
    profile_id: default
    model: model
""".lstrip(),
        encoding="utf-8",
    )

    assert not sakura_main._initial_setup_required(root)


def test_main_checks_initial_setup_before_building_app_context() -> None:
    source = (Path(__file__).resolve().parents[2] / "main.py").read_text(encoding="utf-8")
    startup = source[source.index("migration_report =") : source.index("character_issues =")]

    assert "initial_setup = _initial_setup_required(BASE_DIR)" in startup
    assert startup.index("initial_setup = _initial_setup_required(BASE_DIR)") < startup.index(
        "build_initial_app_context(BASE_DIR)"
    )
    assert "RuntimeError" in startup


def test_main_first_run_studio_waits_for_close_and_requests_refresh(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    import main as sakura_main

    root = _ui_runtime_root("first_run_studio")
    calls: list[tuple[str, object]] = []
    studio_processes: list[object] = []

    class EventLoopStub:
        def exec(self) -> None:
            calls.append(("loop_exec", None))
            studio_processes[0].closed.emit()  # type: ignore[attr-defined]

        def quit(self) -> None:
            calls.append(("loop_quit", None))

    class TauriStudioProcessStub:
        def __init__(
            self,
            base_dir: Path,
            *,
            initial_character_id: str = "",
            parent=None,
        ) -> None:  # type: ignore[no-untyped-def]
            calls.append(("init", (base_dir, initial_character_id, parent)))
            self.closed = _SignalStub()
            self.failed = _SignalStub()
            studio_processes.append(self)

        def start(self) -> bool:
            calls.append(("start", None))
            return True

        def shutdown(self) -> None:
            calls.append(("shutdown", None))

    monkeypatch.setattr(sakura_main, "QEventLoop", EventLoopStub)
    monkeypatch.setattr(sakura_main, "TauriStudioProcess", TauriStudioProcessStub)
    monkeypatch.setattr(
        sakura_main,
        "resolve_tauri_studio_binary",
        lambda _base_dir: Path("sakura-studio"),
    )

    result = sakura_main._open_first_run_studio(root, "sakura")

    assert result == {
        "refresh_characters": True,
        "current_character_id": "sakura",
    }
    assert calls == [
        ("init", (root, "sakura", None)),
        ("start", None),
        ("loop_exec", None),
        ("loop_quit", None),
        ("shutdown", None),
    ]


def test_main_first_run_tauri_save_persists_full_layout(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    import main as sakura_main
    from app.config.models import ModelSelectionSettings

    root = _ui_runtime_root("first_run_tauri_layout")
    saved_system_values: dict[str, dict[str, object]] = {}
    studio_launchers: list[object] = []
    process_kwargs: list[dict[str, object]] = []
    app_context = object()
    tts_settings = _minimal_tts_settings()
    result = _build_tauri_settings_result(
        portrait_scale_percent=155,
        control_panel_width=730,
        bubble_height=210,
        control_panel_vertical_offset=35,
        input_bar_offset=18,
    )

    class EventLoopStub:
        def exec(self) -> None:
            pass

        def quit(self) -> None:
            pass

    class SettingsServiceStub:
        def __init__(self, *, base_dir: Path) -> None:
            assert base_dir == root

        def load_api_settings(self):  # type: ignore[no-untyped-def]
            return result.api.settings

        def load_api_profiles(self):  # type: ignore[no-untyped-def]
            return result.api.profiles

        def load_model_selection(self):  # type: ignore[no-untyped-def]
            return ModelSelectionSettings()

        def load_tts_settings(self, **_kwargs):  # type: ignore[no-untyped-def]
            return tts_settings

        def load_startup_settings(self):  # type: ignore[no-untyped-def]
            return StartupSettings()

        def load_current_character_id(self, _registry):  # type: ignore[no-untyped-def]
            return "sakura"

        def load_screen_awareness_settings(self):  # type: ignore[no-untyped-def]
            return result.screen_awareness

        def load_mcp_runtime_settings(self):  # type: ignore[no-untyped-def]
            return result.mcp

        def load_runtime_loop_settings(self):  # type: ignore[no-untyped-def]
            return result.runtime_loop

        def load_debug_log_settings(self):  # type: ignore[no-untyped-def]
            return result.system_basic.debug_log

        def load_theme_settings(self):  # type: ignore[no-untyped-def]
            return DEFAULT_THEME_SETTINGS

        def load_system_values(self, section: str) -> dict[str, int]:
            assert section == "ui"
            return {
                "portrait_scale_percent": 125,
                "control_panel_width": 620,
                "bubble_height": 180,
                "control_panel_vertical_offset": 20,
                "input_bar_offset": 12,
                "subtitle_typing_interval_ms": 48,
                "reply_segment_pause_ms": 260,
            }

        def save_api_settings(self, _settings) -> None:  # type: ignore[no-untyped-def]
            pass

        def save_api_profiles(self, _profiles) -> None:  # type: ignore[no-untyped-def]
            pass

        def save_model_selection(self, _selection) -> None:  # type: ignore[no-untyped-def]
            pass

        def save_tts_settings(self, _settings) -> None:  # type: ignore[no-untyped-def]
            pass

        def save_current_character_id(self, _registry, character_id: str) -> None:  # type: ignore[no-untyped-def]
            assert character_id == "sakura"

        def save_screen_awareness_settings(self, _settings) -> None:  # type: ignore[no-untyped-def]
            pass

        def save_mcp_runtime_settings(self, _settings) -> None:  # type: ignore[no-untyped-def]
            pass

        def save_runtime_loop_settings(self, _settings) -> None:  # type: ignore[no-untyped-def]
            pass

        def save_debug_log_settings(self, _settings) -> None:  # type: ignore[no-untyped-def]
            pass

        def save_theme_settings(self, _settings) -> None:  # type: ignore[no-untyped-def]
            pass

        def save_system_values(self, section: str, values: dict[str, object]) -> None:
            saved_system_values[section] = dict(values)

    class CharacterRegistryStub:
        def __init__(self, base_dir: Path) -> None:
            assert base_dir == root

        def get(self, character_id: str):  # type: ignore[no-untyped-def]
            assert character_id == "sakura"
            return type("CharacterProfileStub", (), {"id": "sakura"})()

    class TauriSettingsProcessStub:
        def __init__(self, *_args, **kwargs) -> None:  # type: ignore[no-untyped-def]
            self.completed = _SignalStub()
            self.apply_requested = _SignalStub()
            self.cancelled = _SignalStub()
            self.failed = _SignalStub()
            self.shutdown_called = False
            studio_launchers.append(kwargs.get("studio_launcher"))
            process_kwargs.append(kwargs)

        def start(self) -> bool:
            self.completed.emit(result)
            return True

        def shutdown(self) -> None:
            self.shutdown_called = True

        def resolve_apply_request(self, _request_id: str, *, ok: bool, error: str = "") -> None:
            assert ok is True
            assert error == ""

    monkeypatch.setattr(sakura_main, "QEventLoop", EventLoopStub)
    monkeypatch.setattr(sakura_main, "AppSettingsService", SettingsServiceStub)
    monkeypatch.setattr(sakura_main, "CharacterRegistry", CharacterRegistryStub)
    monkeypatch.setattr(sakura_main, "_character_packages_missing", lambda _base_dir: False)
    monkeypatch.setattr(sakura_main, "TauriSettingsProcess", TauriSettingsProcessStub)
    monkeypatch.setattr(
        sakura_main,
        "resolve_tauri_settings_binary",
        lambda _base_dir: Path("sakura-settings"),
    )
    monkeypatch.setattr(
        sakura_main,
        "tts_settings_from_tauri_result",
        lambda *_args, **_kwargs: tts_settings,
    )
    monkeypatch.setattr(sakura_main, "build_initial_app_context", lambda _base_dir: app_context)

    assert sakura_main._open_first_run_settings(root) is app_context
    assert len(studio_launchers) == 1
    assert callable(studio_launchers[0])
    assert process_kwargs[0]["onboarding"] is True
    assert isinstance(process_kwargs[0]["character_registry"], CharacterRegistryStub)
    assert process_kwargs[0]["current_character"].id == "sakura"
    assert process_kwargs[0]["portrait_scale_percent"] == 125
    assert process_kwargs[0]["control_panel_width"] == 620
    assert process_kwargs[0]["bubble_height"] == 180
    assert process_kwargs[0]["control_panel_vertical_offset"] == 20
    assert process_kwargs[0]["input_bar_offset"] == 12
    assert process_kwargs[0]["subtitle_typing_interval_ms"] == 48
    assert process_kwargs[0]["reply_segment_pause_ms"] == 260
    assert saved_system_values["ui"] == {
        "portrait_scale_percent": 155,
        "control_panel_width": 730,
        "bubble_height": 210,
        "control_panel_vertical_offset": 35,
        "input_bar_offset": 18,
        "subtitle_typing_interval_ms": result.system_basic.subtitle_typing_interval_ms,
        "reply_segment_pause_ms": result.system_basic.reply_segment_pause_ms,
    }


def test_main_first_run_missing_tauri_binary_fails_without_starting_process(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    import main as sakura_main

    root = _ui_runtime_root("first_run_missing_tauri_binary")
    started_process = False

    class SettingsServiceStub:
        def __init__(self, *, base_dir: Path) -> None:
            assert base_dir == root

    class TauriSettingsProcessStub:
        def __init__(self, *_args, **_kwargs) -> None:
            nonlocal started_process
            started_process = True

    monkeypatch.setattr(sakura_main, "AppSettingsService", SettingsServiceStub)
    monkeypatch.setattr(sakura_main, "TauriSettingsProcess", TauriSettingsProcessStub)
    monkeypatch.setattr(sakura_main, "resolve_tauri_settings_binary", lambda _base_dir: None)

    with pytest.raises(RuntimeError, match="未找到设置程序"):
        sakura_main._open_first_run_settings(root)
    assert started_process is False


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


def test_character_theme_override_saves_or_deletes_by_default_colors() -> None:
    from app.config.character_loader import (
        THEME_SOURCE_PACKAGE,
        CharacterProfile,
    )
    from app.ui.pet_window import _save_character_theme_override

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
    saved: list[tuple[str, ThemeSettings]] = []
    deleted: list[str] = []

    class SettingsServiceStub:
        def save_character_theme_override(self, character_id, settings):  # type: ignore[no-untyped-def]
            saved.append((character_id, settings.normalized()))

        def delete_character_theme_override(self, character_id):  # type: ignore[no-untyped-def]
            deleted.append(character_id)

    service = SettingsServiceStub()

    _save_character_theme_override(service, profile, ThemeSettings(primary_color="#112233"))
    _save_character_theme_override(service, profile, DEFAULT_THEME_SETTINGS)

    assert saved == [("demo", ThemeSettings(primary_color="#112233").normalized())]
    assert deleted == ["demo"]


def test_screen_awareness_batches_screenshots_until_cooldown(
    pet_window,
    monkeypatch,
) -> None:  # type: ignore[no-untyped-def]
    import app.ui.pet_window as pet_window_module

    current_time = {"value": 0.0}
    captures: list[str] = []
    events = []
    history = []
    window = _configure_screen_awareness_window(
        pet_window,
        screen_context_enabled=True,
        check_interval_minutes=1,
        cooldown_minutes=2,
    )
    window._run_event_worker = events.append
    window._record_history = lambda *args: history.append(args)

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


def test_screen_context_cache_log_uses_summary_without_image_payload(
    pet_window,
    monkeypatch,
) -> None:  # type: ignore[no-untyped-def]
    import app.ui.pet_window as pet_window_module

    logs: list[tuple[str, str, dict[str, object] | None]] = []
    monkeypatch.setattr(
        pet_window_module,
        "log_event",
        lambda channel, message, payload=None, **_kwargs: logs.append((channel, message, payload)),
    )
    data_url = "data:image/jpeg;base64,abc123"
    observation = ScreenObservation(
        data_url=data_url,
        width=800,
        height=600,
        captured_at="2026-05-30T12:01:00+08:00",
        screen_name="DISPLAY1",
    )
    window = _configure_screen_awareness_window(
        pet_window,
        screen_context_enabled=True,
        check_interval_minutes=1,
        cooldown_minutes=2,
        screen_context_batch_limit=2,
    )
    window._finish_screen_awareness_context({"captured_at_monotonic": 1.0}, observation)

    payloads = [
        payload
        for channel, message, payload in logs
        if channel == "ScreenAwareness" and message == "主动屏幕上下文已缓存"
    ]
    assert len(payloads) == 1
    for payload in payloads:
        assert payload is not None
        assert payload["screen"] == "DISPLAY1 800x600"
        assert payload["screen_name"] == "DISPLAY1"
        assert payload["resolution"] == "800x600"
        assert payload["batch"] == "1/2"
        assert payload["batch_count"] == 1
        assert payload["batch_limit"] == 2
        assert payload["dropped_count"] == 0
        assert payload["image_chars"] == len(data_url)
        assert "image" not in payload
        assert "data:image" not in str(payload)


def test_screen_awareness_capture_defaults_to_fullscreen_resolution(
    pet_window,
    monkeypatch,
) -> None:  # type: ignore[no-untyped-def]
    import app.ui.pet_window as pet_window_module

    contexts: list[dict[str, object]] = []
    window = _configure_screen_awareness_window(
        pet_window,
        screen_context_enabled=True,
        check_interval_minutes=1,
        cooldown_minutes=2,
    )
    monkeypatch.setattr(pet_window_module, "capture_screen_image", lambda _window: object())
    window._start_screen_observation_encode = lambda _captured, context: (
        contexts.append(context) or True
    )

    window._capture_screen_awareness_context(60)

    assert contexts[0]["screen_context_resolution"] == "fullscreen"
    assert contexts[0]["preserve_original_resolution"] is True
    assert contexts[0]["detail"] == "high"


def test_screen_awareness_capture_uses_selected_resolution(
    pet_window,
    monkeypatch,
) -> None:  # type: ignore[no-untyped-def]
    import app.ui.pet_window as pet_window_module

    contexts: list[dict[str, object]] = []
    window = _configure_screen_awareness_window(
        pet_window,
        screen_context_enabled=True,
        check_interval_minutes=1,
        cooldown_minutes=2,
        screen_context_resolution="720p",
    )
    monkeypatch.setattr(pet_window_module, "capture_screen_image", lambda _window: object())
    window._start_screen_observation_encode = lambda _captured, context: (
        contexts.append(context) or True
    )

    window._capture_screen_awareness_context(60)

    assert contexts[0]["screen_context_resolution"] == "720p"
    assert contexts[0]["preserve_original_resolution"] is False
    assert contexts[0]["detail"] == "high"


def test_screen_observation_encode_worker_resizes_to_selected_resolution() -> None:
    qtgui = pytest.importorskip("PySide6.QtGui")
    if not hasattr(qtgui, "QImage"):
        pytest.skip("当前测试环境只提供了 PySide6 stub。")

    from app.agent.screen_observation import CapturedScreenImage
    from app.ui.pet_window import ScreenObservationEncodeWorker

    image = qtgui.QImage(1600, 1000, qtgui.QImage.Format.Format_RGB32)
    image.fill(0xFFFFFFFF)
    captured = CapturedScreenImage(
        image=image,
        captured_at="2026-07-12T12:00:00+08:00",
        screen_name="DISPLAY1",
    )
    worker = ScreenObservationEncodeWorker(
        captured,
        {"screen_context_resolution": "720p"},
    )
    observations: list[ScreenObservation] = []
    worker.finished.connect(lambda _context, observation: observations.append(observation))

    worker.run()

    assert len(observations) == 1
    assert (observations[0].width, observations[0].height) == (1152, 720)


def test_screen_awareness_event_includes_recent_conversation(pet_window) -> None:  # type: ignore[no-untyped-def]
    from app.ui.pet_window import SCREEN_AWARENESS_RECENT_CONVERSATION_SUMMARY_HINT

    window = _configure_screen_awareness_window(
        pet_window,
        screen_context_enabled=True,
        check_interval_minutes=1,
        cooldown_minutes=2,
    )
    window.messages = [
        {"role": "system", "content": SCREEN_AWARENESS_CONTEXT_HISTORY_MARKER},
        {"role": "user", "content": "访问 GitHub 看看 Sakura 内容"},
        {"role": "assistant", "content": "我打开看看。"},
        {"role": "assistant", "content": "稍微休息一下吧。"},
    ]

    event = window._build_screen_awareness_event(300.0)

    assert event.payload["recent_conversation"] == [
        {"role": "user", "content": "访问 GitHub 看看 Sakura 内容"},
        {"role": "assistant", "content": "我打开看看。"},
        {"role": "assistant", "content": "稍微休息一下吧。"},
    ]
    assert event.payload["recent_conversation_summary_hint"] == (
        SCREEN_AWARENESS_RECENT_CONVERSATION_SUMMARY_HINT
    )
    assert SCREEN_AWARENESS_CONTEXT_HISTORY_MARKER not in str(
        event.payload["recent_conversation"]
    )


def test_screen_awareness_visual_job_uses_recent_conversation_as_focus() -> None:
    from app.ui.pet_window import _build_screen_awareness_visual_observation_jobs

    event = AgentEvent(
        type="screen_awareness_check",
        payload={
            "recent_conversation": [
                {"role": "user", "content": "第一条太旧"},
                {"role": "assistant", "content": "我在看设置页。"},
                {"role": "user", "content": "帮我看看模型配置哪里不对"},
            ],
            "screen_contexts": [
                {
                    "data_url": "data:image/jpeg;base64,abc",
                    "width": 800,
                    "height": 600,
                    "captured_at": "2026-06-01T08:20:19+08:00",
                    "screen_name": "DISPLAY1",
                }
            ],
        },
    )

    jobs = _build_screen_awareness_visual_observation_jobs(event)

    assert len(jobs) == 1
    assert "最近对话" in jobs[0].user_text
    assert "帮我看看模型配置哪里不对" in jobs[0].user_text


def test_screen_awareness_event_reads_recent_conversation_from_history_store(
    pet_window,
) -> None:  # type: ignore[no-untyped-def]
    from app.storage.chat_history import ChatHistoryStore
    from app.ui.pet_window import SCREEN_AWARENESS_RECENT_CONVERSATION_SUMMARY_HINT

    window = _configure_screen_awareness_window(
        pet_window,
        screen_context_enabled=True,
        check_interval_minutes=1,
        cooldown_minutes=2,
    )
    history_path = Path(window.base_dir) / "data" / "history" / "recent.jsonl"
    store = ChatHistoryStore(history_path)
    store.append("system", SCREEN_AWARENESS_CONTEXT_HISTORY_MARKER)
    store.append("user", "刚才已经提醒过我喝水了")
    store.append("assistant", "水を飲んでって言ったばかりだよ。", "我刚提醒过你喝水。")
    window.history_store = store
    window.subtitle_language = "zh"
    window.messages = []

    event = window._build_screen_awareness_event(300.0)

    assert event.payload["recent_conversation"] == [
        {"role": "user", "content": "刚才已经提醒过我喝水了"},
        {"role": "assistant", "content": "我刚提醒过你喝水。"},
    ]
    assert event.payload["recent_conversation_summary_hint"] == (
        SCREEN_AWARENESS_RECENT_CONVERSATION_SUMMARY_HINT
    )
    assert SCREEN_AWARENESS_CONTEXT_HISTORY_MARKER not in str(
        event.payload["recent_conversation"]
    )


def test_screen_awareness_recent_conversation_limits_count_and_content(pet_window) -> None:  # type: ignore[no-untyped-def]
    from app.ui.pet_window import SCREEN_AWARENESS_RECENT_CONVERSATION_CONTENT_LIMIT

    window = _configure_screen_awareness_window(
        pet_window,
        screen_context_enabled=True,
        check_interval_minutes=1,
        cooldown_minutes=2,
    )
    window.messages = [
        {"role": "user", "content": f"第 {index} 条"}
        for index in range(13)
    ]
    window.messages.append({"role": "assistant", "content": "很长" * 500})

    event = window._build_screen_awareness_event(300.0)
    recent_conversation = event.payload["recent_conversation"]

    assert len(recent_conversation) == 12
    assert recent_conversation[0] == {"role": "user", "content": "第 2 条"}
    assert recent_conversation[-1]["role"] == "assistant"
    assert len(recent_conversation[-1]["content"]) == (
        SCREEN_AWARENESS_RECENT_CONVERSATION_CONTENT_LIMIT
    )
    assert recent_conversation[-1]["content"].endswith("…")


def test_screen_awareness_capture_interval_allows_timer_jitter(pet_window) -> None:  # type: ignore[no-untyped-def]
    window = _configure_screen_awareness_window(
        pet_window,
        screen_context_enabled=True,
        check_interval_minutes=1,
        cooldown_minutes=10,
    )
    window.last_user_activity_at = 0.0

    assert not window._should_capture_screen_awareness_context(58.9)
    assert window._should_capture_screen_awareness_context(59.2)

    window.last_screen_awareness_context_at = 60.0
    assert not window._should_capture_screen_awareness_context(118.9)
    assert window._should_capture_screen_awareness_context(119.2)


def test_screen_awareness_keeps_recent_screenshot_batch(
    pet_window,
    monkeypatch,
) -> None:  # type: ignore[no-untyped-def]
    import app.ui.pet_window as pet_window_module

    captures = []
    window = _configure_screen_awareness_window(
        pet_window,
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
        window._finish_screen_awareness_context(context, observations[-1]) or True
    )

    for index in range(8):
        window._capture_screen_awareness_context(float(index * 60))

    assert len(window.screen_awareness_contexts) == 6
    assert window.screen_awareness_context_dropped_count == 2
    assert [context["data_url"] for context in window.screen_awareness_contexts] == [
        "data:image/jpeg;base64,3",
        "data:image/jpeg;base64,4",
        "data:image/jpeg;base64,5",
        "data:image/jpeg;base64,6",
        "data:image/jpeg;base64,7",
        "data:image/jpeg;base64,8",
    ]


def test_screen_awareness_uses_configured_screenshot_batch_limit(
    pet_window,
    monkeypatch,
) -> None:  # type: ignore[no-untyped-def]
    import app.ui.pet_window as pet_window_module

    captures = []
    window = _configure_screen_awareness_window(
        pet_window,
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
        window._finish_screen_awareness_context(context, observations[-1]) or True
    )

    for index in range(5):
        window._capture_screen_awareness_context(float(index * 60))

    assert len(window.screen_awareness_contexts) == 3
    assert window.screen_awareness_context_dropped_count == 2
    assert [context["data_url"] for context in window.screen_awareness_contexts] == [
        "data:image/jpeg;base64,3",
        "data:image/jpeg;base64,4",
        "data:image/jpeg;base64,5",
    ]


def test_screen_awareness_disabled_does_not_capture_or_send(
    pet_window,
    monkeypatch,
) -> None:  # type: ignore[no-untyped-def]
    import app.ui.pet_window as pet_window_module

    current_time = {"value": 600.0}
    events = []
    window = _configure_screen_awareness_window(
        pet_window,
        screen_context_enabled=False,
        check_interval_minutes=1,
        cooldown_minutes=1,
    )
    window._run_event_worker = events.append

    def fail_capture(_window):  # type: ignore[no-untyped-def]
        raise AssertionError("关闭主动屏幕获取时不应该截图")

    monkeypatch.setattr(pet_window_module.time, "perf_counter", lambda: current_time["value"])
    monkeypatch.setattr(pet_window_module, "capture_screen_image", fail_capture)

    window._check_screen_awareness()

    assert events == []
    assert window.screen_awareness_contexts == []


def test_screen_awareness_redirects_limited_night_health_reminders(
    pet_window,
    monkeypatch,
) -> None:  # type: ignore[no-untyped-def]
    import app.ui.pet_window as pet_window_module
    monkeypatch.setattr(pet_window_module, "_screen_awareness_night_key", lambda: "2026-06-12")
    window = pet_window
    event = AgentEvent(
        type="screen_awareness_check",
        payload={
            "visual_contexts": [
                {
                    "summary": "用户正在查看代码编辑器和 Git 客户端。",
                    "visible_texts": ["event_result_received", "GitHub Desktop"],
                    "notable_elements": ["代码窗口", "提交列表"],
                }
            ],
        },
    )
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
    assert second.reply.segments
    assert "用户正在查看代码编辑器和 Git 客户端" in second.reply.translation
    assert "event_result_received" in second.reply.translation
    assert "休息" not in second.reply.translation


def test_user_activity_keeps_pending_screen_awareness_screenshot_batch(
    pet_window,
    monkeypatch,
) -> None:  # type: ignore[no-untyped-def]
    import app.ui.pet_window as pet_window_module

    window = _configure_screen_awareness_window(
        pet_window,
        screen_context_enabled=True,
        check_interval_minutes=1,
        cooldown_minutes=10,
    )
    window.screen_awareness_contexts = [{"data_url": "data:image/jpeg;base64,old"}]
    window.screen_awareness_context_batch_started_at = 60
    window.last_screen_awareness_context_at = 60
    window.screen_awareness_context_dropped_count = 2
    monkeypatch.setattr(pet_window_module.time, "perf_counter", lambda: 300.0)

    window._mark_user_activity()

    assert window.last_user_activity_at == 300.0
    assert window.screen_awareness_contexts == [{"data_url": "data:image/jpeg;base64,old"}]
    assert window.screen_awareness_context_batch_started_at == 60
    assert window.last_screen_awareness_context_at == 60
    assert window.screen_awareness_context_dropped_count == 2


def test_send_message_clears_pending_screen_awareness_screenshot_batch(
    pet_window,
    monkeypatch,
) -> None:  # type: ignore[no-untyped-def]
    window = _configure_screen_awareness_window(
        pet_window,
        screen_context_enabled=True,
        check_interval_minutes=1,
        cooldown_minutes=10,
    )
    requests, _history = _configure_manual_screenshot_window(
        window,
        monkeypatch,
        "发送这条",
    )
    window.pending_manual_screen_observation = None
    window.screen_awareness_contexts = [{"data_url": "data:image/jpeg;base64,old"}]
    window.screen_awareness_context_batch_started_at = 60
    window.last_screen_awareness_context_at = 60
    window.screen_awareness_context_dropped_count = 2

    window.send_message("test")

    assert len(requests) == 1
    assert window.screen_awareness_contexts == []
    assert window.screen_awareness_context_batch_started_at is None
    assert window.last_screen_awareness_context_at is None
    assert window.screen_awareness_context_dropped_count == 0


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


def test_manual_screenshot_empty_input_sends_default_text(
    pet_window,
    monkeypatch,
) -> None:  # type: ignore[no-untyped-def]
    requests, history = _configure_manual_screenshot_window(
        pet_window,
        monkeypatch,
        "",
    )

    pet_window.send_message("test")

    assert len(requests) == 1
    content = requests[0][-1]["content"]
    assert isinstance(content, list)
    assert content[0]["text"].startswith("请根据我框选的截图继续对话。")
    assert content[1]["image_url"]["url"] == "data:image/jpeg;base64,manual"
    assert pet_window.pending_manual_screen_observation is None
    assert history
    assert "data:image/jpeg;base64" not in history[0][1]


def test_manual_screenshot_text_input_records_marker_without_image_data(
    pet_window,
    monkeypatch,
) -> None:  # type: ignore[no-untyped-def]
    requests, history = _configure_manual_screenshot_window(
        pet_window,
        monkeypatch,
        "帮我看这里",
    )

    pet_window.send_message("test")

    assert len(requests) == 1
    content = requests[0][-1]["content"]
    assert isinstance(content, list)
    assert content[0]["text"].startswith("帮我看这里")
    assert content[1]["image_url"]["url"] == "data:image/jpeg;base64,manual"
    assert pet_window.messages[-1]["content"].startswith("帮我看这里")
    assert "已附加手动框选截图" in pet_window.messages[-1]["content"]
    assert "visual_id=vis_" in pet_window.messages[-1]["content"]
    assert pet_window.pending_visual_observation_jobs[0].source == "manual_screenshot"
    assert "data:image/jpeg;base64" not in pet_window.messages[-1]["content"]
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


def test_set_busy_uses_reply_waiting_property_as_previous_state(
    pet_window,
    qtbot,
) -> None:  # type: ignore[no-untyped-def]
    pet_window.activateWindow()
    pet_window.input_bar_animator.set_force_visible(True)
    pet_window.input_edit.setProperty("replyWaiting", True)
    pet_window.input_edit.setText("")
    pet_window.input_edit.setFocus()
    qtbot.waitUntil(pet_window.input_edit.hasFocus)
    assert pet_window.input_edit.hasFocus()

    pet_window._set_busy(False)

    assert pet_window.input_edit.property("replyWaiting") is False
    assert not pet_window.input_edit.hasFocus()
    assert not hasattr(pet_window, "reply_waiting_ui_active")


def test_set_busy_does_not_change_pet_ui_state(pet_window) -> None:
    from app.ui.state import PetUiState

    pet_window.ui_state.begin_speaking()
    pet_window._set_busy(False)

    assert pet_window.ui_state.state is PetUiState.SPEAKING


def test_set_busy_keeps_focus_when_waiting_ends_with_next_input(
    pet_window,
    qtbot,
) -> None:  # type: ignore[no-untyped-def]
    pet_window.activateWindow()
    pet_window.input_bar_animator.set_force_visible(True)
    pet_window.input_edit.setProperty("replyWaiting", True)
    pet_window.input_edit.setText("下一句")
    pet_window.input_edit.setFocus()
    qtbot.waitUntil(pet_window.input_edit.hasFocus)

    pet_window._set_busy(False)

    assert pet_window.input_edit.hasFocus()
    assert pet_window.input_edit.property("replyWaiting") is False


def test_set_busy_releases_focus_for_whitespace_only_input(
    pet_window,
    qtbot,
) -> None:  # type: ignore[no-untyped-def]
    pet_window.activateWindow()
    pet_window.input_bar_animator.set_force_visible(True)
    pet_window.input_edit.setProperty("replyWaiting", True)
    pet_window.input_edit.setText("   ")
    pet_window.input_edit.setFocus()
    qtbot.waitUntil(pet_window.input_edit.hasFocus)

    pet_window._set_busy(False)

    assert not pet_window.input_edit.hasFocus()
    assert pet_window.input_edit.property("replyWaiting") is False


def test_set_busy_preserves_startup_placeholder_and_common_side_effects(
    startup_pet_window,
    monkeypatch,
) -> None:  # type: ignore[no-untyped-def]
    from app.ui.pet_window import STARTUP_INITIALIZING_TEXT

    stages = []
    history_button_updates = []
    monkeypatch.setattr(
        startup_pet_window,
        "_log_interaction_stage",
        lambda stage, payload=None: stages.append((stage, payload)),
    )
    monkeypatch.setattr(
        startup_pet_window,
        "_update_reply_history_buttons",
        lambda: history_button_updates.append(True),
    )

    startup_pet_window._set_busy(True)

    assert startup_pet_window.input_edit.placeholderText() == STARTUP_INITIALIZING_TEXT
    assert startup_pet_window.send_button.text() == "初始化"
    assert startup_pet_window.input_edit.property("replyWaiting") is not True
    assert stages == [("set_busy", {"busy": True})]
    assert history_button_updates == [True]


@pytest.mark.parametrize(
    "busy_state",
    ("worker", "encoding", "pending_chat", "pending_event", "idle"),
)
def test_pet_click_reads_derived_worker_busy_state(
    pet_window,
    qtbot,
    busy_state: str,
) -> None:  # type: ignore[no-untyped-def]
    pet_window.worker_thread = None
    pet_window.screen_observation_followup_in_progress = False
    pet_window.pending_screen_observation_messages = None
    pet_window.pending_screen_observation_event = None
    if busy_state == "worker":
        pet_window.worker_thread = object()
    elif busy_state == "encoding":
        pet_window.screen_observation_followup_in_progress = True
    elif busy_state == "pending_chat":
        pet_window.pending_screen_observation_messages = [{"role": "user", "content": "x"}]
    elif busy_state == "pending_event":
        pet_window.pending_screen_observation_event = AgentEvent(
            type="screen_awareness_check",
            payload={},
        )

    pet_window.activateWindow()
    pet_window.input_edit.clearFocus()
    assert not pet_window.input_edit.hasFocus()
    pet_window._handle_pet_click()

    if busy_state == "idle":
        qtbot.waitUntil(pet_window.input_edit.hasFocus)
        assert pet_window.input_edit.hasFocus()
    else:
        assert not pet_window.input_edit.hasFocus()
    pet_window.worker_thread = None
    pet_window.screen_observation_followup_in_progress = False
    pet_window.pending_screen_observation_messages = None
    pet_window.pending_screen_observation_event = None


def test_input_bar_not_pinned_just_because_reply_is_waiting(pet_window) -> None:
    pet_window.always_on_top_enabled = True
    pet_window.input_edit.setText("")
    pet_window.input_edit.clearFocus()
    pet_window.input_edit.setProperty("replyWaiting", True)
    pet_window.pending_tool_action = None

    assert not pet_window._input_bar_pinned()


def test_input_bar_pinned_ignores_visible_secondary_window() -> None:
    from app.ui.pet_window import PetWindow

    class MinimalInputBarWindow:
        _input_bar_pinned = PetWindow._input_bar_pinned
        _input_bar_foreground_allowed = lambda _self: True
        settings_dialog = object()
        history_window = None
        runtime_log_window = None

        def _is_secondary_window_visible(self, _window) -> bool:  # type: ignore[no-untyped-def]
            return True

    window = MinimalInputBarWindow()
    window.input_edit = _DummyEditableInput("未发送文本")
    window.pending_tool_action = None

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
        / uuid.uuid4().hex
        / "chat_history_segments"
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
        / uuid.uuid4().hex
        / "chat_history_legacy"
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
        _sync_secondary_windows_topmost = PetWindow._sync_secondary_windows_topmost

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


def test_pet_window_uses_pointer_sized_hwnd_when_disabling_windows_topmost(
    monkeypatch,
) -> None:  # type: ignore[no-untyped-def]
    import ctypes
    from types import SimpleNamespace

    import app.ui.pet_window as pet_window_module
    from app.ui.pet_window import PetWindow

    calls: list[tuple[int, int]] = []

    class FakeUser32:
        def SetWindowPos(self, hwnd, insert_after, *_args) -> int:  # noqa: N802, ANN001
            calls.append((int(hwnd.value), int(insert_after.value)))
            return 1

    monkeypatch.setattr(pet_window_module.sys, "platform", "win32")
    monkeypatch.setattr(
        ctypes,
        "windll",
        SimpleNamespace(user32=FakeUser32()),
        raising=False,
    )

    class MinimalWindow:
        _sync_native_topmost_state = PetWindow._sync_native_topmost_state
        _topmost_sync_windows = PetWindow._topmost_sync_windows
        _effective_topmost = PetWindow._effective_topmost

        always_on_top_enabled = True
        _secondary_windows_suppress_topmost = True

        def isVisible(self) -> bool:
            return True

        def winId(self) -> int:
            return 123

        def _stack_renderer_overlay_below(self) -> None:
            pass

    MinimalWindow()._sync_native_topmost_state()

    assert calls == [(123, ctypes.c_void_p(-2).value)]


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


def test_macos_topmost_suppression_uses_normal_window_level() -> None:
    from app.ui.pet_window import _macos_window_level

    assert _macos_window_level(True) == 8
    assert _macos_window_level(False) == 0


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


def _configure_manual_screenshot_window(
    pet_window,
    monkeypatch,
    text: str,
):  # type: ignore[no-untyped-def]
    requests = []
    history = []
    pet_window.input_edit.setText(text)
    pet_window.pending_manual_screen_observation = ScreenObservation(
        data_url="data:image/jpeg;base64,manual",
        width=320,
        height=180,
        captured_at="2026-05-31T12:00:00+08:00",
        screen_name="manual-selection",
    )
    pet_window.messages = []
    pet_window.active_interaction_id = ""
    monkeypatch.setattr(pet_window, "_mark_user_activity", lambda: None)
    monkeypatch.setattr(
        pet_window,
        "_begin_interaction",
        lambda source: setattr(pet_window, "active_interaction_id", source),
    )
    monkeypatch.setattr(pet_window, "_log_interaction_stage", lambda *args, **kwargs: None)
    monkeypatch.setattr(pet_window, "_record_history", lambda *args: history.append(args))
    monkeypatch.setattr(pet_window, "_show_waiting_reply_placeholder", lambda: None)
    monkeypatch.setattr(pet_window, "_start_chat_worker", requests.append)
    monkeypatch.setattr(pet_window, "_update_manual_screenshot_button", lambda: None)
    monkeypatch.setattr(pet_window, "_collapse_auto_fit_bubble_height", lambda: None)
    return requests, history


def _configure_screen_awareness_window(
    pet_window,
    *,
    screen_context_enabled: bool,
    check_interval_minutes: int,
    cooldown_minutes: int,
    screen_context_batch_limit: int = 6,
    screen_context_resolution: str = "fullscreen",
):  # type: ignore[no-untyped-def]
    pet_window.screen_awareness_settings = ScreenAwarenessSettings(
        enabled=screen_context_enabled,
        screen_context_enabled=screen_context_enabled,
        check_interval_minutes=check_interval_minutes,
        cooldown_minutes=cooldown_minutes,
        screen_context_batch_limit=screen_context_batch_limit,
        screen_context_resolution=screen_context_resolution,
    )
    pet_window.worker_thread = None
    pet_window.active_event = None
    pet_window.pending_tool_action = None
    pet_window.pending_screen_observation_messages = None
    pet_window.pending_screen_observation_event = None
    pet_window.screen_observation_followup_in_progress = False
    pet_window.screen_observation_encode_thread = None
    pet_window.active_interaction_id = ""
    pet_window.last_user_activity_at = 0.0
    pet_window.last_screen_awareness_at = None
    pet_window.last_screen_awareness_context_at = None
    pet_window.screen_awareness_context_batch_started_at = None
    pet_window.screen_awareness_contexts = []
    pet_window.screen_awareness_context_dropped_count = 0
    return pet_window


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


def _minimal_settings_window(pet_window_cls, settings_service, api_client, memory_store):  # type: ignore[no-untyped-def]
    import app.ui.pet_window as pet_window_module
    from app.config.models import ModelSelectionSettings

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

    class AgentRuntimeStub:
        def __init__(self) -> None:
            self.runtime_loop_settings = RuntimeLoopSettings()

        def set_runtime_loop_settings(self, settings):  # type: ignore[no-untyped-def]
            self.runtime_loop_settings = settings

    class MinimalSettingsWindow:
        show_settings = pet_window_cls.show_settings
        _try_show_tauri_settings = pet_window_cls._try_show_tauri_settings
        _on_tauri_settings_completed = pet_window_cls._on_tauri_settings_completed
        _on_tauri_settings_applied = pet_window_cls._on_tauri_settings_applied
        _on_tauri_settings_apply_requested = pet_window_cls._on_tauri_settings_apply_requested
        _apply_tauri_settings_result = pet_window_cls._apply_tauri_settings_result
        _on_tauri_settings_cancelled = pet_window_cls._on_tauri_settings_cancelled
        _on_tauri_settings_failed = pet_window_cls._on_tauri_settings_failed
        _on_tauri_settings_layout_preview = pet_window_cls._on_tauri_settings_layout_preview
        _restore_tauri_layout_preview = pet_window_cls._restore_tauri_layout_preview
        _restore_tauri_font_preview = pet_window_cls._restore_tauri_font_preview
        _restore_tauri_settings_preview = pet_window_cls._restore_tauri_settings_preview
        _release_tauri_preview_force_state = pet_window_cls._release_tauri_preview_force_state
        _apply_fonts_values = pet_window_cls._apply_fonts_values
        _abort_tauri_settings_apply = pet_window_cls._abort_tauri_settings_apply
        _close_unused_tauri_tts_provider = pet_window_cls._close_unused_tauri_tts_provider
        _close_tauri_settings_process_for_shutdown = (
            pet_window_cls._close_tauri_settings_process_for_shutdown
        )
        _preview_layout = pet_window_cls._preview_layout
        _preview_fonts = pet_window_cls._preview_fonts
        _prepare_secondary_window = pet_window_cls._prepare_secondary_window
        _present_registered_secondary_window = pet_window_cls._present_registered_secondary_window
        _release_secondary_window = pet_window_cls._release_secondary_window
        _register_secondary_window = pet_window_cls._register_secondary_window
        _unregister_secondary_window = pet_window_cls._unregister_secondary_window
        _sync_secondary_window_state = pet_window_cls._sync_secondary_window_state
        _is_secondary_window_visible = pet_window_cls._is_secondary_window_visible
        _set_secondary_windows_topmost_suppressed = (
            pet_window_cls._set_secondary_windows_topmost_suppressed
        )
        _set_settings_window_topmost_suppressed = (
            pet_window_cls._set_settings_window_topmost_suppressed
        )
        _retire_tts_provider = pet_window_cls._retire_tts_provider
        _close_retired_tts_provider = pet_window_cls._close_retired_tts_provider
        _apply_subtitle_display_speed = pet_window_cls._apply_subtitle_display_speed
        _apply_launch_at_login_settings = pet_window_cls._apply_launch_at_login_settings
        _apply_bubble_settings = pet_window_cls._apply_bubble_settings
        _tts_settings_from_tauri_result = pet_window_cls._tts_settings_from_tauri_result

        def _apply_theme_settings(self, theme_settings):  # type: ignore[no-untyped-def]
            self.theme_settings = theme_settings.normalized()
            self.applied_theme_settings = self.theme_settings

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
            raise_on_persist_error: bool = False,
        ) -> None:
            self.portrait_scale_percent = portrait_scale_percent
            self.control_panel_width = control_panel_width
            self.bubble_height = bubble_height
            self.control_panel_vertical_offset = vertical_offset
            self.input_bar_offset = input_bar_offset
            self.layout_persisted = persist
            if getattr(self, "raise_layout_persist_error", False) and raise_on_persist_error:
                raise OSError("layout.yaml locked")

        def _sync_screen_awareness_timer(self) -> None:
            pass

        def _apply_character(self, profile):  # type: ignore[no-untyped-def]
            self.character_profile = profile

        def _apply_fonts(self) -> None:
            pass

        def setStyleSheet(self, stylesheet: str) -> None:
            pass

        def _save_system_config_values(self, section, values):  # type: ignore[no-untyped-def]
            self.settings_service.save_system_values(section, values)

    if not hasattr(settings_service, "load_api_profiles"):
        settings_service.load_api_profiles = lambda: []  # type: ignore[attr-defined]
    if not hasattr(settings_service, "load_model_selection"):
        settings_service.load_model_selection = ModelSelectionSettings  # type: ignore[attr-defined]

    window = MinimalSettingsWindow()
    window.settings_service = settings_service
    window.api_client = api_client
    window.base_dir = Path(".")
    window.character_registry = CharacterRegistryStub()
    window.character_profile = CharacterProfileStub()
    window.tauri_settings_process = None
    window.screen_awareness_settings = ScreenAwarenessSettings(screen_context_enabled=True)
    window.mcp_settings = MCPRuntimeSettings(windows_enabled=False)
    window.debug_log_settings = DebugLogSettings()
    window.startup_settings = StartupSettings()
    window.theme_settings = DEFAULT_THEME_SETTINGS
    window.memory_store = memory_store
    window.agent_runtime = AgentRuntimeStub()
    window.plugin_manager = PluginManagerStub()
    window.portrait_scale_percent = 100
    window.control_panel_width = 640
    window.bubble_height = 128
    window.control_panel_vertical_offset = 0
    window.input_bar_offset = 0
    window.speech_font_size = 19
    window.name_font_size = 13
    window.input_font_size = 15
    window.button_font_size = 15
    window._tauri_original_layout = None
    window._tauri_original_font_sizes = None
    window.subtitle_typing_interval_ms = 35
    window.reply_segment_pause_ms = 100
    window.retired_tts_providers = []
    window.tts_provider = object()
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


def test_subtitle_ignores_late_finished_callback_from_previous_segment(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    import app.ui.subtitle_controller as subtitle_module
    from app.ui.subtitle_controller import SubtitleController
    from app.voice import VoicePlaybackController
    from app.voice.tts import TTSPreparedAudio

    class DummyLabel:
        def __init__(self) -> None:
            self.text = ""

        def clear(self) -> None:
            self.text = ""

        def setText(self, text: str) -> None:
            self.text = text

    class DuplicateFinishTTS:
        def __init__(self) -> None:
            self.first_on_finished = None
            self.second_on_finished = None

        def speak(self, _text, _tone, on_finished=None, on_started=None):  # type: ignore[no-untyped-def]
            self.first_on_finished = on_finished
            if on_started is not None:
                on_started()

        def prepare(self, text, tone):  # type: ignore[no-untyped-def]
            return TTSPreparedAudio(text=text, tone=tone)

        def speak_prepared(self, _handle, on_started=None, on_finished=None):  # type: ignore[no-untyped-def]
            if on_started is not None:
                on_started()
            self.second_on_finished = on_finished

        def discard_prepared(self, _handle):  # type: ignore[no-untyped-def]
            pass

    _qt_app_or_skip()
    timers = []
    monkeypatch.setattr(
        subtitle_module.QTimer,
        "singleShot",
        staticmethod(lambda delay, callback: timers.append((delay, callback))),
    )
    ended = []
    tts = DuplicateFinishTTS()
    controller = SubtitleController(
        DummyLabel(),  # type: ignore[arg-type]
        VoicePlaybackController(tts, lambda *_args, **_kwargs: None),
        "zh",
        lambda *_args, **_kwargs: None,
        lambda _segment: None,
        lambda: ended.append("reply_completed"),
        lambda: True,
    )
    first = ChatSegment("一つ目。", "中性", "第一段。")
    second = ChatSegment("二つ目。", "中性", "第二段。")

    controller.show_segments([first, second])
    controller.speech_index = len(controller.speech_text)
    controller._mark_segment_speech_done(
        controller.current_segment_sequence_id,  # type: ignore[arg-type]
        controller.current_segment_token,
    )

    assert tts.first_on_finished is not None
    tts.first_on_finished()
    assert timers[0][0] == controller.segment_pause_ms
    timers[0][1]()
    assert controller.current_segment == second
    assert not controller.current_segment_tts_done

    tts.first_on_finished()
    assert controller.current_segment == second
    assert not controller.current_segment_tts_done

    controller.speech_index = len(controller.speech_text)
    controller._mark_segment_speech_done(
        controller.current_segment_sequence_id,  # type: ignore[arg-type]
        controller.current_segment_token,
    )
    assert tts.second_on_finished is not None
    tts.second_on_finished()

    assert ended == ["reply_completed"]


def test_send_message_injects_runtime_event_context_before_user_message(
    pet_window,
    monkeypatch,
) -> None:  # type: ignore[no-untyped-def]
    from app.agent.runtime_events import PET_REOPENED, RuntimeEvent, RuntimeEventQueue

    requests, history = _configure_manual_screenshot_window(
        pet_window,
        monkeypatch,
        "继续刚才的话题",
    )
    pet_window.pending_manual_screen_observation = None
    pet_window.runtime_event_queue = RuntimeEventQueue()
    pet_window.runtime_event_queue.push(
        RuntimeEvent(PET_REOPENED, metadata={"hidden_duration": 300})
    )

    pet_window.send_message("test")

    assert len(requests) == 1
    request = requests[0]
    # 事件上下文应作为 system 消息插在历史与当前用户消息之间
    assert request[0]["role"] == "system"
    assert "重新打开" in request[0]["content"]
    assert request[-1] == {"role": "user", "content": "继续刚才的话题"}
    # 只进 request_messages：不污染 self.messages
    assert pet_window.messages == [{"role": "user", "content": "继续刚才的话题"}]
    # 不污染聊天历史
    assert history == [("user", "继续刚才的话题")]
    # 队列已被一次性消费
    assert len(pet_window.runtime_event_queue) == 0


def _qt_app_or_skip():  # type: ignore[no-untyped-def]
    """统一获取/创建 QApplication；stub 环境下跳过。"""
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
        _cursor_in_window = True
        always_on_top_enabled = True

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
        _cursor_in_window = True
        always_on_top_enabled = True
        settings_dialog = object()
        history_window = None
        runtime_log_window = None

        def _is_secondary_window_visible(self, _window) -> bool:  # type: ignore[no-untyped-def]
            return True

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
        _cursor_in_window = True
        always_on_top_enabled = False

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
        _cursor_in_window = True
        always_on_top_enabled = False

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
        pending_tool_action = None

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


def test_input_bar_animator_force_visible_release_refreshes_hover() -> None:
    _qt_app_or_skip()
    from PySide6.QtWidgets import QGraphicsOpacityEffect, QWidget
    from app.ui.input_bar_animator import InputBarAnimator

    bar = QWidget()
    card = QWidget()
    effect = QGraphicsOpacityEffect(card)
    hover = {"value": True}
    animator = InputBarAnimator(
        bar,
        card,
        effect,
        lambda: False,
        lambda: hover["value"],
    )

    animator._started = True
    animator._hover = False
    animator._shown = False

    animator.set_force_visible(True)
    assert animator._shown is True

    # 释放强制显示时刷新真实 hover，避免旧缓存把刚唤出的输入栏立刻收回。
    animator.set_force_visible(False)
    assert animator._shown is True

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


def test_resolve_effective_theme_uses_package_theme_and_user_level_fields() -> None:
    # 角色包主题只贡献配色；visual_effect_mode 和 ai_enabled 是用户级偏好，必须沿用已保存值。
    from app.config.character_loader import THEME_SOURCE_PACKAGE
    from app.ui.theme import resolve_effective_theme

    user = ThemeSettings(visual_effect_mode="macos_visual_effect", primary_color="#aa11bb", ai_enabled=True)
    package_theme = ThemeSettings(primary_color="#123456")
    profile = _make_character_profile(package_theme, THEME_SOURCE_PACKAGE)

    merged = resolve_effective_theme(profile, None, user)

    assert merged.visual_effect_mode == "macos_visual_effect"
    assert merged.ai_enabled is True
    assert merged.primary_color == "#123456"


def test_resolve_effective_theme_compat_default_ignores_saved_global_colors() -> None:
    from app.config.character_loader import THEME_SOURCE_COMPAT_DEFAULT
    from app.ui.theme import resolve_effective_theme

    user = ThemeSettings(visual_effect_mode="windows_acrylic", primary_color="#aa11bb")
    profile = _make_character_profile(ThemeSettings(), THEME_SOURCE_COMPAT_DEFAULT)

    merged = resolve_effective_theme(profile, None, user)

    assert merged.visual_effect_mode == "windows_acrylic"
    assert merged.primary_color == DEFAULT_THEME_SETTINGS.primary_color


def test_resolve_effective_theme_override_wins_over_package_theme() -> None:
    from app.config.character_loader import THEME_SOURCE_PACKAGE
    from app.ui.theme import resolve_effective_theme

    user = ThemeSettings(visual_effect_mode="solid", primary_color="#aa11bb")
    profile = _make_character_profile(ThemeSettings(primary_color="#123456"), THEME_SOURCE_PACKAGE)
    override = ThemeSettings(primary_color="#abcdef")

    merged = resolve_effective_theme(profile, override, user)

    assert merged.visual_effect_mode == "solid"
    assert merged.primary_color == "#abcdef"


def test_resolve_effective_theme_without_profile() -> None:
    from app.ui.theme import resolve_effective_theme

    user = ThemeSettings(visual_effect_mode="solid", primary_color="#aa11bb")

    merged = resolve_effective_theme(None, None, user)

    assert merged.visual_effect_mode == "solid"
    assert merged.primary_color == DEFAULT_THEME_SETTINGS.primary_color


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
