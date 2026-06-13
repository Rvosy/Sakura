from __future__ import annotations

import importlib
from pathlib import Path

import pytest

from app.agent.tools import ToolRegistry
from app.plugins.manager import PluginManager


def test_removed_public_compat_modules_are_not_importable() -> None:
    for module_name in (
        "app.agent.tool_registry",
        "sdk.plugin",
        "sdk.register",
        "sdk.tool_registry",
    ):
        with pytest.raises(ModuleNotFoundError):
            importlib.import_module(module_name)


def test_old_reexport_symbols_are_not_available_from_former_modules() -> None:
    qtwidgets = pytest.importorskip("PySide6.QtWidgets")
    del qtwidgets

    tts_module = importlib.import_module("app.voice.tts")
    for name in (
        "GPTSoVITSTTSSettings",
        "TTS_PROVIDER_GPT_SOVITS",
        "TTS_PLAYBACK_BACKEND_AUDIO_SINK",
        "ToneReference",
    ):
        assert not hasattr(tts_module, name)

    settings_dialog = importlib.import_module("app.ui.settings_dialog")
    for name in (
        "ApiConnectionTestWorker",
        "TTSTestWorker",
        "ModelComboBox",
    ):
        assert not hasattr(settings_dialog, name)

    runtime = importlib.import_module("app.agent.runtime")
    for name in (
        "_should_prefer_browser_page_tools",
        "_filter_openai_tools_for_browser_routing",
        "_build_browser_page_mode_rule",
    ):
        assert not hasattr(runtime, name)


def test_plugin_using_removed_sdk_api_fails_clearly(tmp_path: Path) -> None:
    plugin_dir = tmp_path / "plugins" / "old_sdk_plugin"
    plugin_dir.mkdir(parents=True)
    (plugin_dir / "plugin.yaml").write_text(
        """
api_version: 1
id: old_sdk_plugin
name: Old SDK Plugin
entry: plugin:OldSdkPlugin
enabled: true
permissions:
  - tool
""".lstrip(),
        encoding="utf-8",
    )
    (plugin_dir / "plugin.py").write_text(
        """
from sdk.plugin import PluginBase

class OldSdkPlugin(PluginBase):
    plugin_id = "old_sdk_plugin"
""".lstrip(),
        encoding="utf-8",
    )

    results = PluginManager(tmp_path).load_all(ToolRegistry())

    assert len(results) == 1
    assert results[0].error is not None
    assert "sdk" in results[0].error
