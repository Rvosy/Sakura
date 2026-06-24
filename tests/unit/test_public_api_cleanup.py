from __future__ import annotations

import importlib
from pathlib import Path
import uuid

import pytest

from app.agent.tools import ToolRegistry
from app.plugins.manager import PluginManager


def test_legacy_sdk_package_is_removed() -> None:
    # 旧 SDK 兼容层已在首个正式发布前移除，整个 sdk.* 不应再可导入。
    for module_name in (
        "sdk",
        "sdk.plugin",
        "sdk.register",
        "sdk.tool_registry",
        "sdk.types",
    ):
        with pytest.raises(ModuleNotFoundError):
            importlib.import_module(module_name)


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


def test_plugin_importing_removed_sdk_fails_gracefully() -> None:
    # 针对已删除 sdk.* 编写的旧插件，应加载失败但不拖垮宿主，且其余插件不受影响。
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
    assert not results[0].loaded
    assert results[0].error is not None


def _runtime_root(name: str) -> Path:
    root = (
        Path(__file__).resolve().parents[2]
        / "temp"
        / "test_runtime"
        / uuid.uuid4().hex
        / "public_api_cleanup"
        / name
    )
    root.mkdir(parents=True, exist_ok=True)
    return root
