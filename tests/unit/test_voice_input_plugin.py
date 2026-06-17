"""voice_input 插件基础加载测试。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from app.agent.tools import ToolRegistry
from app.plugins.discovery import PluginDiscovery
from app.plugins.manager import PluginManager
import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def test_voice_input_manifest_discovered() -> None:
    specs = PluginDiscovery(PROJECT_ROOT).discover()
    voice = [spec for spec in specs if spec.plugin_id == "voice_input"]

    assert voice
    assert "chat_ui" in voice[0].permissions
    assert "settings_panel" in voice[0].permissions
    assert "audio_input" in voice[0].permissions
    assert "model_download" in voice[0].permissions


def test_voice_input_plugin_loads_without_optional_runtime_dependencies() -> None:
    manager = PluginManager(PROJECT_ROOT)

    results = manager.load_all(ToolRegistry())

    by_id = {result.spec.plugin_id: result for result in results}
    assert by_id["voice_input"].loaded, by_id["voice_input"].error
    assert any(widget.widget_id == "voice_input_button" for widget in manager.chat_ui_widgets)
    assert any(panel.section_id == "voice_input_settings" for panel in manager.settings_panels)
    manager.shutdown_all()


def test_voice_input_controller_reports_missing_model(tmp_path: Path) -> None:
    from plugins.voice_input.controller import VoiceInputController, VoiceInputControllerCallbacks

    errors: list[str] = []
    context = _FakeContext(tmp_path, {})
    controller = VoiceInputController(
        context,
        callbacks=VoiceInputControllerCallbacks(error=errors.append),
    )

    controller.start_recording()

    assert errors
    assert "缺少 ASR 模型" in errors[0]


def test_voice_input_chat_button_builds() -> None:
    qtwidgets = pytest.importorskip("PySide6.QtWidgets")
    if not all(hasattr(qtwidgets, name) for name in ("QApplication", "QWidget", "QToolButton")):
        pytest.skip("当前测试环境只提供了 PySide6 stub。")
    from plugins.voice_input.ui import VoiceInputButton

    QApplication = qtwidgets.QApplication
    QWidget = qtwidgets.QWidget
    QToolButton = qtwidgets.QToolButton
    app = QApplication.instance() or QApplication([])
    parent = QWidget()
    context = _FakeContext(PROJECT_ROOT / "__pycache__" / "voice_input_button", {})

    button = VoiceInputButton(context, parent)

    assert isinstance(button, QToolButton)
    assert button.objectName() == "voiceInputButton"
    assert button.text() == "麦"
    button.shutdown()
    parent.deleteLater()
    app.processEvents()


def test_voice_input_settings_panel_saves_config(tmp_path: Path) -> None:
    qtwidgets = pytest.importorskip("PySide6.QtWidgets")
    if not all(hasattr(qtwidgets, name) for name in ("QApplication", "QWidget")):
        pytest.skip("当前测试环境只提供了 PySide6 stub。")
    from plugins.voice_input.settings_panel import VoiceInputSettingsPanel

    QApplication = qtwidgets.QApplication
    app = QApplication.instance() or QApplication([])
    context = _FakeContext(tmp_path, {})
    panel = VoiceInputSettingsPanel(context)

    panel.max_record_spin.setValue(12)
    app.processEvents()

    assert context.saved_config["max_record_seconds"] == 12
    assert "模型" in panel.status_label.text()
    panel.shutdown()
    panel.deleteLater()
    app.processEvents()


class _FakeInputService:
    def __init__(self) -> None:
        self.texts: list[str] = []

    def set_input_text(self, text: str) -> None:
        self.texts.append(text)


class _FakeServices:
    def __init__(self) -> None:
        self.input = _FakeInputService()


class _FakeContext:
    def __init__(self, root: Path, config: dict[str, Any]) -> None:
        self.base_dir = root
        self.plugin_root = root / "plugins" / "voice_input"
        self.data_dir = root / "data" / "plugins" / "voice_input"
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self._config = dict(config)
        self.saved_config: dict[str, Any] = {}
        self.services = _FakeServices()

    def get_config(self) -> dict[str, Any]:
        return dict(self._config)

    def save_config(self, config: dict[str, Any]) -> None:
        self.saved_config = dict(config)
        self._config = dict(config)
