from pathlib import Path

import pytest
import yaml

from app.core_host.plugin_settings import PluginSettingsBoundary, PluginSettingsError


@pytest.fixture
def frontend(tmp_path):
    plugin = tmp_path / "plugins/builtin/arbitrary_name"
    plugin.mkdir(parents=True)
    (plugin / "plugin.py").write_text("class Plugin: pass\n")
    (plugin / "playback.mjs").write_text("export const playback = 'plugin-owned';\n")
    manifest = {"api": 4, "id": "example.voice", "name": "Example", "version": "0.1.0",
                "entry": "plugin:Plugin", "enabled": False, "provides": ["example.playback"],
                "requires": [], "frontendModules": {"playback": "playback.mjs"}}
    (plugin / "plugin.yaml").write_text(yaml.safe_dump(manifest))
    return PluginSettingsBoundary("g", "c", tmp_path), plugin, manifest


def test_installed_plugin_owns_module_even_when_service_is_disabled(frontend):
    boundary, plugin, _ = frontend
    result = boundary.frontend_module("example.playback", "playback")
    assert result == {"pluginId": "example.voice", "source": (plugin / "playback.mjs").read_text()}
    (plugin / "plugin.yaml").unlink()
    with pytest.raises(PluginSettingsError, match="不可用"):
        boundary.frontend_module("example.playback", "playback")


@pytest.mark.parametrize("path", ["../outside.mjs", "/tmp/outside.mjs", "plugin.py", None])
def test_plugin_module_cannot_read_undeclared_or_outside_files(frontend, path):
    boundary, plugin, manifest = frontend
    manifest["frontendModules"]["playback"] = path
    (plugin / "plugin.yaml").write_text(yaml.safe_dump(manifest))
    with pytest.raises(PluginSettingsError) as caught:
        boundary.frontend_module("example.playback", "playback")
    assert caught.value.code == "PLUGIN_FRONTEND_INVALID"


def test_module_symlink_cannot_expose_other_files(frontend):
    boundary, plugin, _ = frontend
    outside = plugin.parent / "outside.mjs"
    outside.write_text("private")
    (plugin / "playback.mjs").unlink()
    (plugin / "playback.mjs").symlink_to(outside)
    with pytest.raises(PluginSettingsError) as caught:
        boundary.frontend_module("example.playback", "playback")
    assert caught.value.code == "PLUGIN_FRONTEND_INVALID"


def test_active_service_owner_wins_over_an_installed_disabled_hub(frontend):
    from app.core_host.plugin_runtime_application import PluginRuntimeApplication
    from app.plugin_sdk.sakura_tools import ToolRegistry
    from app.plugins.inventory import PluginInventory
    from app.storage.runtime_roots import RuntimeRoots

    _, plugin, manifest = frontend
    distribution = plugin.parent.parent.parent
    roots = RuntimeRoots(distribution, distribution / "user")
    replacement = plugin.parent / "replacement"
    replacement.mkdir()
    manifest = {**manifest, "id": "replacement.voice", "enabled": True}
    (replacement / "plugin.yaml").write_text(yaml.safe_dump(manifest))
    (replacement / "plugin.py").write_text('''class Plugin:
    def setup(self, context):
        context.provide("example.playback", self, exports=("status",))
    def status(self): return {"ready": True}
''')
    (replacement / "playback.mjs").write_text("export const owner = 'replacement';")
    application = PluginRuntimeApplication(roots, "frontend-owner", ToolRegistry(),
        PluginInventory(roots).scan().runtime_specs)
    boundary = PluginSettingsBoundary("frontend-owner", "credential", roots,
        application_provider=lambda: application)
    try:
        application.start()
        result = boundary.frontend_module("example.playback", "playback")
        assert result["pluginId"] == "replacement.voice"
        assert result["source"] == (replacement / "playback.mjs").read_text()
        application.set_plugin_enabled("replacement.voice", False)
        with pytest.raises(PluginSettingsError, match="不可用"):
            boundary.frontend_module("example.playback", "playback")
    finally:
        application.close()
