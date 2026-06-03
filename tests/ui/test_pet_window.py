from __future__ import annotations

import json
import os
import threading
import time
from datetime import datetime
from pathlib import Path
import uuid

import pytest

from app.agent.mcp import MCPRuntimeSettings
from app.config.settings_service import DebugLogSettings
from app.llm.api_client import ApiSettings
from app.llm.chat_reply import ChatSegment
from app.ui.portrait_utils import portrait_kind_key, should_crossfade_portrait
from app.agent.proactive_care import ProactiveCareSettings
from app.agent.screen_observation import ScreenObservation
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
    host.always_on_top_enabled = False
    host._toggle_chinese_subtitles = lambda _checked: None
    host._toggle_free_access = lambda _checked: None
    host._toggle_always_on_top = lambda _checked: None
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
    assert "保持置顶" in checkable_texts
    assert len(checkable_texts) == 3

    menu.deleteLater()
    host.deleteLater()
    app.processEvents()


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

    from app.core.bootstrap import build_initial_app_context
    from app.ui.pet_window import PetWindow, STARTUP_INITIALIZING_TEXT

    QApplication = qtwidgets.QApplication
    app = QApplication.instance() or QApplication([])
    root = _build_runtime_root_with_character(qtgui.QPixmap, qtcore.Qt)
    context = build_initial_app_context(root)
    monkeypatch.setattr(PetWindow, "_maybe_start_memory_backfill", lambda _self: None)
    window = PetWindow(context)

    assert window.startup_initializing
    assert window.speech_label.text() == STARTUP_INITIALIZING_TEXT
    assert not window.input_edit.isEnabled()
    assert not window.send_button.isEnabled()
    assert not window.screenshot_button.isEnabled()

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

    from app.core.bootstrap import DeferredStartupServices, build_initial_app_context
    from app.core.extensions import ExtensionRegistry
    from app.core.plugin_manager import SakuraPluginManager
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
    context = build_initial_app_context(root)
    monkeypatch.setattr(PetWindow, "_maybe_start_memory_backfill", lambda _self: None)
    window = PetWindow(context)
    tts_provider = WarmableTTSProvider()
    services = DeferredStartupServices(
        tts_provider=tts_provider,
        tool_registry=context.tool_registry,
        extension_registry=ExtensionRegistry(),
        plugin_manager=SakuraPluginManager(base_dir=root),
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


def test_settings_dialog_marks_windows_mcp_as_unavailable() -> None:
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
        mcp_settings=MCPRuntimeSettings(windows_enabled=True),
    )

    labels = [label.text() for label in dialog.findChildren(qtwidgets.QLabel)]

    assert not dialog.windows_mcp_enabled_check.isChecked()
    assert not dialog.windows_mcp_enabled_check.isEnabled()
    assert any("待测试，未开放" in text for text in labels)
    assert any("重启 Sakura" in text for text in labels)

    dialog.windows_mcp_enabled_check.setChecked(True)
    dialog.accept()

    assert dialog.result_mcp_settings == MCPRuntimeSettings(windows_enabled=False)
    dialog.deleteLater()
    app.processEvents()


def test_settings_dialog_exposes_tts_bundle_controls() -> None:
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

    assert any("TTS 工作目录" in text for text in labels)
    assert any("TTS 提供器" in text for text in labels)
    assert dialog.tts_bundle_download_button.text() == "一键下载 TTS 整合包"
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
            self.downloaded_work_dir = root / "data" / "tts_bundles" / "installed" / "gpt_sovits_v2pro"

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
    assert dialog.tts_work_dir_edit.text().endswith("gpt_sovits_v2pro")
    monkeypatch.setattr(
        dialog,
        "_start_tts_settings_test",
        lambda _settings, accept_values: dialog._complete_accept(accept_values),
    )

    dialog.accept()

    assert dialog.result_tts_settings is not None
    assert dialog.result_tts_settings.work_dir == root / "data" / "tts_bundles" / "installed" / "gpt_sovits_v2pro"
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
            self.downloaded_work_dir = root / "data" / "tts_bundles" / "installed" / "genie_tts_server"
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
    assert dialog.tts_work_dir_edit.text().endswith("genie_tts_server")
    monkeypatch.setattr(
        dialog,
        "_start_tts_settings_test",
        lambda _settings, accept_values: dialog._complete_accept(accept_values),
    )

    dialog.accept()

    assert dialog.result_tts_settings is not None
    assert dialog.result_tts_settings.provider == "genie-tts"
    assert dialog.result_tts_settings.work_dir == root / "data" / "tts_bundles" / "installed" / "genie_tts_server"
    assert dialog.result_tts_settings.onnx_model_dir == root / "data" / "tts_bundles" / "onnx" / "sakura"
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


def test_settings_dialog_enabled_tts_saves_after_successful_test(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qtwidgets = pytest.importorskip("PySide6.QtWidgets")
    if not hasattr(qtwidgets, "QApplication"):
        pytest.skip("当前测试环境只提供了 PySide6 stub。")

    from app.ui.settings_dialog import SettingsDialog

    QApplication = qtwidgets.QApplication
    app = QApplication.instance() or QApplication([])
    root = _ui_runtime_root("tts_save_success")
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
    dialog.tts_enabled_check.setChecked(True)
    calls: list[str] = []

    def fake_start_tts_test(settings, accept_values):  # type: ignore[no-untyped-def]
        calls.append(settings.api_url)
        dialog._complete_accept(accept_values)

    monkeypatch.setattr(dialog, "_start_tts_settings_test", fake_start_tts_test)

    dialog.accept()

    assert calls == ["http://127.0.0.1:9880/tts"]
    assert dialog.result_tts_settings is not None
    assert dialog.result_tts_settings.enabled
    dialog.deleteLater()
    app.processEvents()


def test_settings_dialog_tts_test_failure_disables_tts_and_saves(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qtwidgets = pytest.importorskip("PySide6.QtWidgets")
    if not hasattr(qtwidgets, "QApplication"):
        pytest.skip("当前测试环境只提供了 PySide6 stub。")

    import app.ui.settings_dialog as settings_dialog_module
    from app.ui.settings_dialog import SettingsDialog

    QApplication = qtwidgets.QApplication
    app = QApplication.instance() or QApplication([])
    root = _ui_runtime_root("tts_save_failure")
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
    dialog.tts_enabled_check.setChecked(True)
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
    assert not dialog.tts_enabled_check.isChecked()
    assert dialog.result_tts_settings is not None
    assert not dialog.result_tts_settings.enabled
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
    monkeypatch.setattr(settings_dialog_module.QMessageBox, "information", lambda *_args, **_kwargs: None)

    dialog._import_character_archive()

    assert dialog.character_combo.currentData() == "nanami"
    assert dialog._selected_character_profile().display_name == "Nanami"

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
    monkeypatch.setattr(settings_dialog_module.QMessageBox, "information", lambda *_args, **_kwargs: None)

    assert dialog.character_combo.currentText() == "尚未导入角色"
    assert not dialog.character_combo.isEnabled()
    assert not dialog.character_empty_label.isHidden()
    assert not dialog.character_export_button.isEnabled()

    dialog._import_character_archive()

    assert dialog.character_combo.isEnabled()
    assert dialog.character_combo.currentData() == "nanami"
    assert dialog._selected_character_profile().display_name == "Nanami"
    assert dialog.character_export_button.isEnabled()

    dialog.deleteLater()
    app.processEvents()


def test_settings_dialog_exports_character_archive_in_background(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qtwidgets = pytest.importorskip("PySide6.QtWidgets")
    if not hasattr(qtwidgets, "QApplication"):
        pytest.skip("当前测试环境只提供了 PySide6 stub。")

    import app.ui.settings_dialog as settings_dialog_module
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

    def fake_export_character_archive(export_profile, export_path):  # type: ignore[no-untyped-def]
        exported["profile"] = export_profile
        exported["path"] = export_path
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
        settings_dialog_module,
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


def test_settings_dialog_loads_memory_on_open_in_background() -> None:
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

    assert dialog.memory_status_label.text() == "正在读取长期记忆..."
    assert _process_events_until(app, lambda: memory_store.list_calls == 1)
    assert _process_events_until(app, lambda: dialog._memory_list_thread is None)
    assert dialog.memory_status_label.text() == "已加载 1 条记忆"
    assert dialog.memory_table.item(0, 1).text() == "主人喜欢精简的管理界面"
    dialog.deleteLater()
    app.processEvents()


def test_settings_dialog_shows_memory_dependency_download_hint() -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qtwidgets = pytest.importorskip("PySide6.QtWidgets")
    if not hasattr(qtwidgets, "QApplication"):
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
    assert dialog.memory_status_label.text() == expected
    assert dialog.memory_table.item(0, 1).text() == expected
    assert _process_events_until(app, lambda: memory_store.list_calls == 1)
    assert _process_events_until(app, lambda: dialog._memory_list_thread is None)
    assert dialog.memory_status_label.text() == "已加载 0 条记忆"
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

        def save_system_values(self, *_args):  # type: ignore[no-untyped-def]
            pass

    class ApiClientStub:
        settings = api_settings

        def update_settings(self, _settings):  # type: ignore[no-untyped-def]
            calls["update_api"] += 1

    class MemoryStoreStub:
        def reload_api_settings(self, *_args, **_kwargs):  # type: ignore[no-untyped-def]
            calls["reload_memory"] += 1

    class DialogStub:
        def __init__(self, *_args, **_kwargs) -> None:
            self.result_api_settings = api_settings
            self.result_tts_settings = tts_settings
            self.result_character_id = "sakura"
            self.result_proactive_care_settings = ProactiveCareSettings(screen_context_enabled=True)
            self.result_mcp_settings = MCPRuntimeSettings(windows_enabled=False)
            self.result_debug_log_settings = DebugLogSettings()
            self.result_portrait_scale_percent = 100

        def exec(self):  # type: ignore[no-untyped-def]
            return pet_window_module.QDialog.DialogCode.Accepted

    window = _minimal_settings_window(
        PetWindow,
        SettingsServiceStub(),
        ApiClientStub(),
        MemoryStoreStub(),
    )
    monkeypatch.setattr(pet_window_module, "SettingsDialog", DialogStub)
    monkeypatch.setattr(pet_window_module.QMessageBox, "information", lambda *_args, **_kwargs: None)

    window.show_settings()

    assert calls == {"save_api": 0, "update_api": 0, "reload_memory": 0}


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

    class DialogStub:
        def __init__(self, *_args, **_kwargs) -> None:
            self.result_api_settings = api_settings
            self.result_tts_settings = tts_settings
            self.result_character_id = "sakura"
            self.result_proactive_care_settings = ProactiveCareSettings(screen_context_enabled=True)
            self.result_mcp_settings = MCPRuntimeSettings(windows_enabled=False)
            self.result_debug_log_settings = DebugLogSettings()
            self.result_portrait_scale_percent = 100
            self.result_subtitle_typing_interval_ms = 80
            self.result_reply_segment_pause_ms = 900

        def exec(self):  # type: ignore[no-untyped-def]
            return pet_window_module.QDialog.DialogCode.Accepted

    window = _minimal_settings_window(
        PetWindow,
        SettingsServiceStub(),
        ApiClientStub(),
        MemoryStoreStub(),
    )
    monkeypatch.setattr(pet_window_module, "SettingsDialog", DialogStub)
    monkeypatch.setattr(pet_window_module.QMessageBox, "information", lambda *_args, **_kwargs: None)

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

    monkeypatch.setattr(pet_window_module.QMessageBox, "warning", lambda _parent, title, text: warnings.append((title, text)))
    monkeypatch.setattr(pet_window_module.QMessageBox, "information", lambda *_args, **_kwargs: None)

    window._handle_memory_curation_finished(
        MemoryCurationResult(ignored=3, processed_entries=3, returned=0)
    )

    assert window.history_store.clear_calls == 0
    assert window.memory_curation_state.cleared is False
    assert warnings == [("整理失败", "记忆整理没有写入任何结果，已保留聊天历史。请检查日志后再重试。")]


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

    class DialogStub:
        def __init__(self, *_args, **_kwargs) -> None:
            self.result_api_settings = new_settings
            self.result_tts_settings = tts_settings
            self.result_character_id = "sakura"
            self.result_proactive_care_settings = ProactiveCareSettings(screen_context_enabled=True)
            self.result_mcp_settings = MCPRuntimeSettings(windows_enabled=False)
            self.result_debug_log_settings = DebugLogSettings()
            self.result_portrait_scale_percent = 100

        def exec(self):  # type: ignore[no-untyped-def]
            return pet_window_module.QDialog.DialogCode.Accepted

    messages: list[str] = []
    window = _minimal_settings_window(
        PetWindow,
        SettingsServiceStub(),
        ApiClientStub(),
        MemoryStoreStub(),
    )
    monkeypatch.setattr(pet_window_module, "SettingsDialog", DialogStub)
    monkeypatch.setattr(
        pet_window_module.QMessageBox,
        "information",
        lambda *_args, **_kwargs: messages.append(str(_args[2])),
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

        def save_system_values(self, *_args):  # type: ignore[no-untyped-def]
            pass

    class ApiClientStub:
        settings = api_settings

        def update_settings(self, _settings):  # type: ignore[no-untyped-def]
            pass

    class MemoryStoreStub:
        def reload_api_settings(self, *_args, **_kwargs):  # type: ignore[no-untyped-def]
            pass

    class DialogStub:
        def __init__(self, *_args, **_kwargs) -> None:
            self.character_registry = imported_registry
            self.result_api_settings = api_settings
            self.result_tts_settings = tts_settings
            self.result_character_id = "imported"
            self.result_proactive_care_settings = ProactiveCareSettings(screen_context_enabled=True)
            self.result_mcp_settings = MCPRuntimeSettings(windows_enabled=False)
            self.result_debug_log_settings = DebugLogSettings()
            self.result_portrait_scale_percent = 100

        def exec(self):  # type: ignore[no-untyped-def]
            return pet_window_module.QDialog.DialogCode.Accepted

    window = _minimal_settings_window(
        PetWindow,
        SettingsServiceStub(),
        ApiClientStub(),
        MemoryStoreStub(),
    )
    monkeypatch.setattr(pet_window_module, "SettingsDialog", DialogStub)
    monkeypatch.setattr(pet_window_module.QMessageBox, "information", lambda *_args, **_kwargs: None)

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
        self.display_speeds: list[tuple[int, int]] = []

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

    def set_display_speed(self, typing_interval_ms: int, segment_pause_ms: int) -> None:
        self.display_speeds.append((typing_interval_ms, segment_pause_ms))


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

    assert window.messages[-1] == {"role": "assistant", "content": "調べるね。"}
    assert history[-1] == ("assistant", "調べるね。", "我查一下。", "中性", "")


def test_progress_reply_records_segments_as_separate_history_entries() -> None:
    from app.agent import AgentProgress
    from app.llm.chat_reply import parse_chat_reply
    from app.ui.pet_window import PetWindow

    class MinimalProgressWindow:
        _handle_progress_reply = PetWindow._handle_progress_reply
        _record_assistant_reply_history = PetWindow._record_assistant_reply_history

    window = MinimalProgressWindow()
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

    assert window.messages[-1] == {"role": "assistant", "content": "一つ目。\n二つ目。"}
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
    from app.ui.pet_window import PetWindow

    class MinimalConsumeWindow:
        _consume_agent_result = PetWindow._consume_agent_result

    window = MinimalConsumeWindow()
    segment = ChatSegment("時間だよ。水を飲んで。", "请求", "到时间了，喝水。", "伸手命令")
    shown_segments = []
    applied_results = []
    history = []
    window.messages = []
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

        def _sync_native_topmost_state(self) -> None:
            self.sync_count += 1

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

        def _sync_native_topmost_state(self) -> None:
            self.sync_count += 1

    window = MinimalWindow()

    window._apply_window_flags()

    assert window.show_count == 0
    assert window.sync_count == 0


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


def _build_settings_dialog_character(root: Path, character_id: str, display_name: str):
    from app.config.character_loader import CharacterRegistry

    character_dir = root / "characters" / character_id
    character_dir.mkdir(parents=True, exist_ok=True)
    (character_dir / "voice" / "models").mkdir(parents=True, exist_ok=True)
    (character_dir / "voice" / "refs" / "tone_refs").mkdir(parents=True, exist_ok=True)
    (character_dir / "card.md").write_text("system prompt", encoding="utf-8")
    (character_dir / "portrait.png").write_bytes(b"portrait")
    (character_dir / "voice" / "models" / "gpt.ckpt").write_bytes(b"gpt")
    (character_dir / "voice" / "models" / "sovits.pth").write_bytes(b"sovits")
    (character_dir / "voice" / "refs" / "tone_refs" / "neutral.wav").write_bytes(b"wav")
    (character_dir / "voice" / "refs" / "ref.txt").write_text(
        "voice/refs/tone_refs/neutral.wav|JA|テスト|中性\n",
        encoding="utf-8",
    )
    (character_dir / "character.json").write_text(
        json.dumps(
            {
                "id": character_id,
                "display_name": display_name,
                "initial_message": "hello",
                "card": "card.md",
                "portrait": {
                    "default": "portrait.png",
                },
                "voice": {
                    "gpt_model": "voice/models/gpt.ckpt",
                    "sovits_model": "voice/models/sovits.pth",
                    "tone_refs": "voice/refs/ref.txt",
                    "ref_lang": "ja",
                    "text_lang": "ja",
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return CharacterRegistry(root).get(character_id)


def _settings_dialog_character_kwargs(root: Path) -> dict[str, object]:
    from app.config.character_loader import CharacterRegistry

    profile = _build_settings_dialog_character(root, "sakura", "Sakura")
    return {
        "character_registry": CharacterRegistry(root),
        "current_character": profile,
    }


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


def test_tts_test_worker_closes_provider_after_success(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    pytest.importorskip("PySide6.QtCore")
    import app.ui.settings_dialog as settings_dialog

    closed: list[bool] = []

    class FakeProvider:
        def __init__(self, settings: GPTSoVITSTTSSettings) -> None:
            self.settings = settings

        def ensure_ready(self) -> tuple[bool, str]:
            return True, "ok"

        def close(self) -> None:
            closed.append(True)

    monkeypatch.setattr(settings_dialog, "GPTSoVITSTTSProvider", FakeProvider)

    worker = settings_dialog.TTSTestWorker(_minimal_tts_settings())
    worker.run()

    assert closed == [True]


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
        _retire_tts_provider = pet_window_cls._retire_tts_provider
        _apply_subtitle_display_speed = pet_window_cls._apply_subtitle_display_speed

        def _create_tts_provider_from_settings(self, _settings):  # type: ignore[no-untyped-def]
            return object()

        def _apply_portrait_scale_percent(self, percent: int) -> None:
            self.portrait_scale_percent = percent

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
    window.memory_store = memory_store
    window.plugin_manager = PluginManagerStub()
    window.portrait_scale_percent = 100
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
