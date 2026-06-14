from __future__ import annotations

import importlib
from pathlib import Path
import uuid

import pytest

from app.agent.tools import ToolRegistry
from app.plugins.manager import PluginManager


def test_legacy_sdk_modules_remain_importable() -> None:
    for module_name in (
        "sdk",
        "sdk.plugin",
        "sdk.register",
        "sdk.tool_registry",
        "sdk.types",
    ):
        assert importlib.import_module(module_name) is not None


def test_removed_internal_public_module_is_not_importable() -> None:
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("app.agent.tool_registry")


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


def test_plugin_using_legacy_sdk_api_loads() -> None:
    base = _runtime_root("old_sdk_plugin")
    plugin_dir = base / "plugins" / "old_sdk_plugin"
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

    results = PluginManager(base).load_all(ToolRegistry())

    assert len(results) == 1
    assert results[0].loaded
    assert results[0].error is None


def test_legacy_sdk_global_tool_and_three_arg_initialize_load() -> None:
    base = _runtime_root("legacy_tool_plugin")
    plugin_dir = base / "plugins" / "legacy_tool_plugin"
    plugin_dir.mkdir(parents=True)
    (plugin_dir / "plugin.yaml").write_text(
        """
api_version: 1
id: legacy_tool_plugin
name: Legacy Tool Plugin
entry: plugin:LegacyToolPlugin
enabled: true
""".lstrip(),
        encoding="utf-8",
    )
    (plugin_dir / "plugin.py").write_text(
        """
from sdk.plugin import PluginBase
from sdk.tool_registry import tool

@tool(name="legacy_add", description="add")
def add(a: int, b: int):
    return {"result": a + b}

class LegacyToolPlugin(PluginBase):
    plugin_id = "legacy_tool_plugin"

    def initialize(self, register, plugin_root, host):
        self.plugin_root = plugin_root
        self.host = host
""".lstrip(),
        encoding="utf-8",
    )

    registry = ToolRegistry()
    results = PluginManager(base).load_all(registry)

    assert len(results) == 1
    assert results[0].loaded
    assert registry.execute("legacy_add", {"a": 2, "b": 3}).content == {"result": 5}


def _runtime_root(name: str) -> Path:
    root = (
        Path(__file__).resolve().parents[2]
        / "__pycache__"
        / "test_runtime"
        / "public_api_cleanup"
        / name
        / uuid.uuid4().hex
    )
    root.mkdir(parents=True, exist_ok=True)
    return root
