from pathlib import Path

import pytest

from app.plugin_sdk.sakura_tools import ToolRegistry
from app.core_host.plugin_application import PluginApplicationHost
from app.core_host.plugin_host_services import _SettingsHostService
from app.core_host.plugin_settings import PluginSettingsBoundary, PluginSettingsError
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
        assert host.wait_until_loaded(timeout=5)
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


def test_action_value_warning_survives_refresh_without_filling_absent_fields() -> None:
    result = {"values": {"choice": [], "private": "ignored"}}

    def callback(_handle, method, *_args):
        return result if method == "settings.action" else {"choice": "a", "healthy": "loaded"}

    settings = _SettingsHostService(callback)
    handle = "cb_" + "b" * 32
    settings.call("register", ["fixture", {
        "sectionId": "general", "title": "设置",
        "fields": [
            {"key": "choice", "label": "Choice", "type": "select", "default": "a", "options": [{"label": "A", "value": "a"}]},
            {"key": "healthy", "label": "Healthy", "type": "readonly", "default": "default"},
        ],
        "actions": [{"actionId": "refresh", "label": "刷新"}],
    }, {"load": handle, "save": handle, "actions": {"refresh": handle}}])

    assert settings.action("fixture", "general", "refresh", {}) == (True, {"values": {"choice": "a"}})
    assert settings.sections_for_plugin("fixture")[0]["reasonCode"] == "SETTINGS_VALUE_INVALID"

    result["values"] = {"choice": "a"}
    assert settings.action("fixture", "general", "refresh", {}) == (True, result)
    assert settings.sections_for_plugin("fixture")[0]["reasonCode"] == "READY"


@pytest.mark.parametrize("invalid_descriptor,invalid_value,reason", [
    (True, False, "SETTINGS_DESCRIPTOR_INVALID"),
    (False, True, "SETTINGS_VALUE_INVALID"),
    (True, True, "SETTINGS_DESCRIPTOR_INVALID"),
])
def test_action_projects_full_plugin_state_without_repeating_the_action(
    tmp_path: Path, invalid_descriptor: bool, invalid_value: bool, reason: str,
) -> None:
    roots = RuntimeRoots(tmp_path / "distribution", tmp_path / "user")
    roots.user_root.mkdir()
    plugin_root = roots.distribution_root / "plugins/builtin/action"
    plugin_root.mkdir(parents=True)
    (plugin_root / "plugin.yaml").write_text(
        "api: 4\nid: fixture.action\nentry: plugin:Plugin\n"
        "provides: []\nrequires: [sakura.host.settings]\n",
        encoding="utf-8",
    )
    (plugin_root / "plugin.py").write_text(
        f"INVALID_DESCRIPTOR = {invalid_descriptor!r}\nINVALID_VALUE = {invalid_value!r}\n" + '''
class Plugin:
    def setup(self, context):
        self.calls = 0
        self.values = {"healthy": "initial", "state": "ready"}
        fields = [
            {"key": "healthy", "label": "Healthy", "type": "string", "default": "default"},
            {"key": "state", "label": "State", "type": "select", "default": "ready",
             "options": [{"label": "Ready", "value": "ready"}]},
            {"key": "calls", "label": "Calls", "type": "readonly", "default": "0"},
        ]
        if INVALID_DESCRIPTOR:
            fields.append({"key": "unsupported", "label": "Unsupported", "type": "future-widget"})
        def load():
            return {**self.values, "calls": str(self.calls)}
        def refresh(values):
            self.calls += 1
            self.values = {"healthy": "refreshed", "state": [] if INVALID_VALUE else "ready",
                           "unsupported": "plugin-private-state"}
            return {"values": load(), "message": "已刷新"}
        context.get("sakura.host.settings").register(
            {"sectionId": "general", "title": "设置", "fields": fields,
             "actions": [{"actionId": "refresh", "label": "刷新"}]},
            load=load, save=lambda values: self.values.update(values),
            actions={"refresh": refresh},
        )
''', encoding="utf-8")
    host = PluginApplicationHost(roots, "settings-action-contract", ToolRegistry())
    try:
        host.start()
        assert host.wait_until_loaded(timeout=5)
        boundary = PluginSettingsBoundary("settings-action-contract", "a" * 32, roots, application_provider=lambda: host)
        request = {"pluginId": "fixture.action", "sectionId": "general", "actionId": "refresh", "values": {}}
        for change, expected in [
            ({"actionId": "unknown"}, "SETTINGS_ACTION_INVALID"),
            ({"values": {"unsupported": "write"}}, "SETTINGS_VALUES_INVALID"),
        ]:
            with pytest.raises(PluginSettingsError) as rejected:
                boundary.action({**request, **change})
            assert rejected.value.code == expected
        with pytest.raises(PluginSettingsError) as rejected:
            boundary.save({"pluginId": "fixture.action", "sectionId": "general", "values": {"unsupported": "write"}})
        assert rejected.value.code == "SETTINGS_VALUES_INVALID"

        result = boundary.action(request)

        assert result == {"values": {"healthy": "refreshed", "state": "ready", "calls": "1"}, "message": "已刷新"}
        section = boundary.snapshot()["plugins"][0]["sections"][0]
        assert section["values"] == result["values"]
        assert section["reasonCode"] == reason
        assert "unsupported" not in {field["key"] for field in section["fields"]}
    finally:
        host.close()


def test_conditional_settings_project_hide_and_isolate_invalid_flags() -> None:
    settings = _SettingsHostService(lambda *_args: {})
    handle = "cb_" + "c" * 32
    settings.call("register", ["fixture", {
        "sectionId": "general", "title": "设置",
        "fields": [
            {"key": "mode", "label": "Mode", "type": "string", "default": "ready"},
            {"key": "conditional", "label": "Conditional", "type": "string",
             "enabledWhen": {"field": "mode", "equals": "ready", "hide": True}},
            {"key": "invalid", "label": "Invalid", "type": "string",
             "enabledWhen": {"field": "mode", "equals": "ready", "hide": "yes"}},
        ],
    }, {"load": handle, "save": handle, "actions": {}}])
    section = settings.sections_for_plugin("fixture")[0]
    assert [field["key"] for field in section["fields"]] == ["mode", "conditional"]
    assert section["fields"][1]["enabledWhen"] == {"field": "mode", "equals": "ready", "hide": True}
    assert section["reasonCode"] == "SETTINGS_DESCRIPTOR_INVALID"
