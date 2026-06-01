from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
import uuid

import pytest

from app.agent.mcp import MCPRuntimeSettings
from app.llm.api_client import ApiSettings
from app.llm.chat_reply import ChatSegment
from app.portrait_utils import portrait_kind_key, should_crossfade_portrait
from app.proactive_care import ProactiveCareSettings
from app.screen_observation import ScreenObservation
from app.voice.tts import GPTSoVITSTTSSettings
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
    host._toggle_chinese_subtitles = lambda _checked: None
    host._toggle_free_access = lambda _checked: None
    host.show_history = lambda: None
    host.show_settings = lambda: None

    menu = PetWindow._build_menu(host)  # type: ignore[arg-type]
    actions = [action for action in menu.actions() if not action.isSeparator()]
    texts = [action.text() for action in actions]
    checkable_texts = [action.text() for action in actions if action.isCheckable()]

    assert texts[0] == "隐藏至托盘"
    assert "启用模型视觉" not in texts
    assert "允许自主看屏幕" not in texts
    assert "自由访问权限" not in texts
    assert "显示中文字幕" in checkable_texts
    assert "完整访问权限" in checkable_texts
    assert len(checkable_texts) == 2

    menu.deleteLater()
    host.deleteLater()
    app.processEvents()


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
    assert next_button.text() == "▼"
    assert next_button.toolTip() == "下一条历史消息"

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
        / "__pycache__"
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


def test_settings_dialog_exposes_windows_mcp_restart_setting() -> None:
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
        proactive_care_settings=ProactiveCareSettings(screen_context_enabled=True),
        mcp_settings=MCPRuntimeSettings(windows_enabled=False),
    )

    labels = [label.text() for label in dialog.findChildren(qtwidgets.QLabel)]

    assert not dialog.windows_mcp_enabled_check.isChecked()
    assert any("重启 Sakura" in text for text in labels)

    dialog.windows_mcp_enabled_check.setChecked(True)
    dialog.accept()

    assert dialog.result_mcp_settings == MCPRuntimeSettings(windows_enabled=True)
    dialog.deleteLater()
    app.processEvents()


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

    def fake_capture(_window):  # type: ignore[no-untyped-def]
        index = len(captures) + 1
        data_url = f"data:image/jpeg;base64,{index}"
        captures.append(data_url)
        return ScreenObservation(
            data_url=data_url,
            width=800,
            height=600,
            captured_at=f"2026-05-30T12:0{index}:00+08:00",
            screen_name="DISPLAY1",
        )

    monkeypatch.setattr(pet_window_module.time, "perf_counter", lambda: current_time["value"])
    monkeypatch.setattr(pet_window_module, "capture_screen_observation", fake_capture)

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


def test_proactive_care_event_includes_recent_conversation() -> None:
    from app.proactive_care import PROACTIVE_SCREEN_CONTEXT_HISTORY_MARKER
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
    from app.proactive_care import PROACTIVE_SCREEN_CONTEXT_HISTORY_MARKER
    from app.storage.chat_history import ChatHistoryStore
    from app.ui.pet_window import PROACTIVE_RECENT_CONVERSATION_SUMMARY_HINT

    window = _build_minimal_proactive_window(
        screen_context_enabled=True,
        check_interval_minutes=1,
        cooldown_minutes=2,
    )
    history_path = (
        Path(__file__).resolve().parents[2]
        / "__pycache__"
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

    def fake_capture(_window):  # type: ignore[no-untyped-def]
        index = len(captures) + 1
        captures.append(index)
        return ScreenObservation(
            data_url=f"data:image/jpeg;base64,{index}",
            width=800,
            height=600,
            captured_at=f"2026-05-30T12:{index:02d}:00+08:00",
            screen_name="DISPLAY1",
        )

    monkeypatch.setattr(pet_window_module, "capture_screen_observation", fake_capture)

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

    def fake_capture(_window):  # type: ignore[no-untyped-def]
        index = len(captures) + 1
        captures.append(index)
        return ScreenObservation(
            data_url=f"data:image/jpeg;base64,{index}",
            width=800,
            height=600,
            captured_at=f"2026-05-30T12:{index:02d}:00+08:00",
            screen_name="DISPLAY1",
        )

    monkeypatch.setattr(pet_window_module, "capture_screen_observation", fake_capture)

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
    monkeypatch.setattr(pet_window_module, "capture_screen_observation", fail_capture)

    window._check_proactive_care()

    assert events == []
    assert window.proactive_screen_contexts == []


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

    def text(self) -> str:
        return self._text

    def clear(self) -> None:
        self.cleared = True
        self._text = ""

    def setEnabled(self, enabled: bool) -> None:
        self.enabled = enabled


class _DummyTimer:
    def isActive(self) -> bool:
        return False


class _DummyButton:
    def __init__(self) -> None:
        self.enabled = True
        self.text = ""

    def setVisible(self, _visible: bool) -> None:
        pass

    def setEnabled(self, enabled: bool) -> None:
        self.enabled = enabled

    def setText(self, text: str) -> None:
        self.text = text


class _DummySubtitleController:
    def __init__(self) -> None:
        self.cancelled_with: list[str | None] = []
        self.active = False
        self.segments = []
        self.shown_immediately: list[str] = []
        self.subtitle_languages: list[str] = []
        self.restarted = False

    def cancel_reply_flow(self, placeholder_text: str | None = None) -> None:
        self.cancelled_with.append(placeholder_text)

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


def test_manual_screenshot_empty_input_sends_default_text() -> None:
    window, requests, history = _build_minimal_manual_screenshot_window("")

    window.send_message("test")

    assert len(requests) == 1
    content = requests[0][-1]["content"]
    assert isinstance(content, list)
    assert content[0]["text"].startswith("请根据我框选的截图继续对话。")
    assert content[1]["image_url"]["url"] == "data:image/jpeg;base64,manual"
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

    window = MinimalBusyWindow()
    window.input_edit = _DummyEditableInput("")
    window.screenshot_button = _DummyButton()
    window.send_button = _DummyButton()
    window.confirm_action_button = _DummyButton()
    window.cancel_action_button = _DummyButton()
    window._log_interaction_stage = lambda *_args, **_kwargs: None

    window._set_busy(True)
    assert not window.screenshot_button.enabled

    window._set_busy(False)
    assert window.screenshot_button.enabled


def test_progress_reply_displays_and_records_assistant_message() -> None:
    from app.agent import AgentProgress
    from app.llm.chat_reply import parse_chat_reply
    from app.ui.pet_window import PetWindow

    class MinimalProgressWindow:
        _handle_progress_reply = PetWindow._handle_progress_reply

    window = MinimalProgressWindow()
    history = []
    shown = []
    window.messages = [{"role": "user", "content": "查一下"}]
    window._log_interaction_stage = lambda *_args, **_kwargs: None
    window._record_history = lambda *args: history.append(args)
    window._record_assistant_reply_history = (
        PetWindow._record_assistant_reply_history.__get__(window, type(window))
    )
    window._show_reply_segments = lambda segments: shown.append(segments)

    window._handle_progress_reply(
        AgentProgress(
            reply=parse_chat_reply(
                '{"segments":[{"ja":"調べるね。","zh":"我查一下。","tone":"中性"}]}'
            )
        )
    )

    assert window.messages[-1] == {"role": "assistant", "content": "調べるね。"}
    assert history[-1] == ("assistant", "調べるね。", "我查一下。", "中性", "")
    assert shown and shown[0][0].translation == "我查一下。"


def test_progress_reply_records_segments_as_separate_history_entries() -> None:
    from app.agent import AgentProgress
    from app.llm.chat_reply import parse_chat_reply
    from app.ui.pet_window import PetWindow

    class MinimalProgressWindow:
        _handle_progress_reply = PetWindow._handle_progress_reply
        _record_assistant_reply_history = PetWindow._record_assistant_reply_history

    window = MinimalProgressWindow()
    history = []
    shown = []
    window.messages = [{"role": "user", "content": "查一下"}]
    window._log_interaction_stage = lambda *_args, **_kwargs: None
    window._record_history = lambda *args: history.append(args)
    window._show_reply_segments = lambda segments: shown.append(segments)

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

    assert window.messages[-1] == {"role": "assistant", "content": "一つ目。\n二つ目。"}
    assert history == [
        ("assistant", "一つ目。", "第一段。", "中性", ""),
        ("assistant", "二つ目。", "第二段。", "中性", ""),
    ]
    assert shown and len(shown[0]) == 2


def test_assistant_reply_history_records_tone_and_portrait() -> None:
    from app.llm.chat_reply import ChatReply
    from app.ui.pet_window import PetWindow

    class MinimalHistoryWindow:
        _record_assistant_reply_history = PetWindow._record_assistant_reply_history

    window = MinimalHistoryWindow()
    history = []
    window._record_history = lambda *args: history.append(args)

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
        / "__pycache__"
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
        / "__pycache__"
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


def test_screen_observation_followup_uses_last_user_message_after_progress(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from app.agent import AgentAction, AgentResult
    from app.llm.chat_reply import parse_chat_reply
    import app.ui.pet_window as pet_window_module
    from app.ui.pet_window import PetWindow

    class MinimalScreenFollowupWindow:
        _queue_screen_observation_followup = PetWindow._queue_screen_observation_followup

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
    observation = ScreenObservation(
        data_url="data:image/jpeg;base64,screen",
        width=640,
        height=360,
        captured_at="2026-05-31T12:00:00+08:00",
        screen_name="DISPLAY1",
    )
    monkeypatch.setattr(pet_window_module, "capture_screen_observation", lambda _window: observation)

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
    observation = ScreenObservation(
        data_url=f"data:image/jpeg;base64,{'a' * 50000}",
        width=640,
        height=360,
        captured_at="2026-05-31T12:00:00+08:00",
        screen_name="DISPLAY1",
    )
    monkeypatch.setattr(pet_window_module, "capture_screen_observation", lambda _window: observation)

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
    window.subtitle_controller = _DummySubtitleController()
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


def _minimal_tts_settings() -> GPTSoVITSTTSSettings:
    return GPTSoVITSTTSSettings(
        enabled=False,
        api_url="http://127.0.0.1:9880/tts",
        ref_audio_path=Path("characters/sakura/voice/refs/tone_refs/00_中性_VO01_2785.ogg"),
        ref_text_path=Path("characters/sakura/voice/refs/ref.txt"),
        ref_text="テスト",
        ref_lang="ja",
        text_lang="ja",
        timeout_seconds=1,
    )


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
    second = ChatSegment("执行前确认", "提醒", "执行前确认")

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
