"""voice_input 插件基础加载测试。"""

from __future__ import annotations

from pathlib import Path

from app.agent.tools import ToolRegistry
from app.plugins.discovery import PluginDiscovery
from app.plugins.manager import PluginManager


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
