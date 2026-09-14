from pathlib import Path

from app.agent.tools import ToolRegistry
from app.core_host.plugin_application import PluginApplicationHost
from app.core_host.plugin_host_services import _SettingsHostService
from app.core_host.plugin_settings import PluginSettingsBoundary
from app.storage.runtime_roots import RuntimeRoots


def test_invalid_controls_do_not_stop_plugins_or_hide_valid_settings(tmp_path: Path) -> None:
    roots = RuntimeRoots(tmp_path / "distribution", tmp_path / "user")
    roots.user_root.mkdir()
    for plugin_id in ("fixture.mixed", "fixture.healthy"):
        plugin_root = roots.distribution_root / "plugins/builtin" / plugin_id
        plugin_root.mkdir(parents=True)
        (plugin_root / "plugin.yaml").write_text(
            f"api: 4\nid: {plugin_id}\nname: {'中' * 120}\nversion: 1.0.0\n"
            "entry: plugin:Plugin\nprovides: []\nrequires: [sakura.host.settings]\n",
            encoding="utf-8",
        )
        (plugin_root / "plugin.py").write_text('''
class Plugin:
    def setup(self, context):
        self.values = {"mode": "default", "dependent": "keep"}
        fields = [{"key": "mode", "label": "设置", "type": "string", "default": "default", "futureHint": True}]
        actions = []
        if context.plugin_id == "fixture.mixed":
            fields.extend([
                {"key": "unsupported", "label": "Bad", "type": "future-widget"},
                {"key": "dependent", "label": "Dependent", "type": "string", "default": "keep",
                 "enabledWhen": {"field": "unsupported", "equals": "ready"}},
            ])
            actions.append({"actionId": "invalid", "label": "Invalid", "danger": True})
        context.get("sakura.host.settings").register(
            {"sectionId": "general", "title": "设置", "fields": fields, "actions": actions, "futureDisplay": True},
            load=lambda: self.values,
            save=lambda values: self.values.update(values),
            actions={"invalid": lambda values: {}} if actions else {},
        )
''', encoding="utf-8")
    host = PluginApplicationHost(roots, "settings-contract", ToolRegistry())
    try:
        host.start()
        assert host.application.wait_until_loaded(timeout=5)
        boundary = PluginSettingsBoundary("settings-contract", "a" * 32, roots, application_provider=lambda: host)
        plugins = {item["pluginId"]: item for item in boundary.snapshot()["plugins"]}
        assert all(item["state"] == "active" for item in plugins.values()), plugins
        assert plugins["fixture.mixed"]["name"] == "中" * 120
        section = plugins["fixture.mixed"]["sections"][0]
        assert [field["key"] for field in section["fields"]] == ["mode", "dependent"]
        assert section["fields"][1]["readonly"] is True
        assert section["actions"] == []
        assert section["reasonCode"] == "SETTINGS_DESCRIPTOR_INVALID"
        assert "futureHint" not in section["fields"][0]
        assert "futureDisplay" not in section
        assert "entry" not in plugins["fixture.mixed"]
        assert plugins["fixture.healthy"]["sections"][0]["reasonCode"] == "READY"
        boundary.save({"pluginId": "fixture.mixed", "sectionId": "general", "values": {"mode": "changed"}})
        changed = next(item for item in boundary.snapshot()["plugins"] if item["pluginId"] == "fixture.mixed")
        assert changed["sections"][0]["values"]["mode"] == "changed"
        assert changed["sections"][0]["reasonCode"] == "SETTINGS_DESCRIPTOR_INVALID"
    finally:
        host.close()


def test_invalid_loaded_value_falls_back_only_for_its_control() -> None:
    settings = _SettingsHostService(lambda *_args: {"choice": [], "status": {"state": [], "label": "坏状态", "message": ""}, "healthy": "current"})
    handle = "cb_" + "a" * 32
    settings.call("register", ["fixture", {
        "sectionId": "general", "title": "设置",
        "fields": [
            {"key": "choice", "label": "Choice", "type": "select", "default": "a", "options": [{"label": "A", "value": "a"}]},
            {"key": "status", "label": "Status", "type": "status", "default": None},
            {"key": "healthy", "label": "Healthy", "type": "readonly", "default": "default"},
        ],
    }, {"load": handle, "save": handle, "actions": {}}])
    section = settings.sections_for_plugin("fixture")[0]
    assert section["values"] == {"choice": "a", "status": None, "healthy": "current"}
    assert section["reasonCode"] == "SETTINGS_VALUE_INVALID"
