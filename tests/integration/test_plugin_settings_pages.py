from pathlib import Path

import pytest

from app.core_host.plugin_application import PluginApplicationHost
from app.core_host.plugin_settings import PluginSettingsBoundary
from app.plugin_sdk.sakura_tools import ToolRegistry
from app.storage.runtime_roots import RuntimeRoots


def test_real_plugins_register_pages_share_regions_and_keep_private_settings(tmp_path: Path):
    roots = RuntimeRoots(tmp_path / "distribution", tmp_path / "user")
    roots.user_root.mkdir()
    for owner in ("fixture.page", "fixture.guest"):
        root = roots.distribution_root / "plugins/builtin" / owner
        root.mkdir(parents=True)
        (root / "plugin.yaml").write_text(f"api: 4\nid: {owner}\nname: 测试插件\nentry: plugin:Plugin\nprovides: []\nrequires: [sakura.host.settings]\n", encoding="utf-8")
        (root / "plugin.py").write_text('''
class Plugin:
    def setup(self, context):
        settings = context.get("sakura.host.settings")
        self.values = {"value": "initial"}
        settings.register_page({"pageId": "schedule", "title": "日程", "group": "behavior", "icon": "calendar", "regions": ["extensions"]})
        for section in ("functional", "private", "shared", "forbidden"):
            settings.register({"sectionId": section, "title": section, "presentation": {"component": "form"},
                "fields": [{"key": "value", "label": "值", "type": "string", "default": ""}]},
                load=lambda: self.values, save=lambda values: self.values.update(values))
        settings.place("functional", page_id=context.plugin_id + ":schedule")
        settings.place("shared", page_id="fixture.page:schedule", region="extensions")
        settings.place("forbidden", page_id="fixture.page:schedule", region="internal")
''', encoding="utf-8")
    app = PluginApplicationHost(roots, "settings-pages", ToolRegistry())
    try:
        app.start()
        assert app.wait_until_loaded(timeout=5)
        boundary = PluginSettingsBoundary("settings-pages", "a" * 32, roots, application_provider=lambda: app)
        snapshot = boundary.snapshot()
        plugins = {p["pluginId"]: p for p in snapshot["plugins"]}
        assert all(p["state"] == "active" for p in plugins.values()), snapshot
        assert {p["pages"][0]["pageId"] for p in plugins.values()} == {"fixture.page:schedule", "fixture.guest:schedule"}
        sections = {s["sectionId"]: s for s in plugins["fixture.guest"]["sections"]}
        assert sections["private"]["placement"] is None
        assert sections["shared"]["placement"]["available"] is True
        assert sections["forbidden"]["placement"]["available"] is False
        boundary.save({"pluginId": "fixture.guest", "sectionId": "functional", "values": {"value": "changed"}})
        refreshed = next(p for p in boundary.snapshot()["plugins"] if p["pluginId"] == "fixture.guest")
        assert refreshed["sections"][0]["values"]["value"] == "changed"
        app.reload_plugin("fixture.guest")
        reloaded = next(p for p in boundary.snapshot()["plugins"] if p["pluginId"] == "fixture.guest")
        assert reloaded["pages"][0]["pageId"] == "fixture.guest:schedule"
        assert reloaded["sections"][0]["instanceId"] != refreshed["sections"][0]["instanceId"]
        owner = plugins["fixture.page"]
        boundary.set_enabled(boundary.snapshot()["revision"], owner["installId"], False)
        remaining = {p["pluginId"]: p for p in boundary.snapshot()["plugins"]}
        assert not remaining["fixture.page"].get("pages")
        shared = next(s for s in remaining["fixture.guest"]["sections"] if s["sectionId"] == "shared")
        assert shared["placement"]["available"] is False
    finally:
        app.close()


def test_page_ownership_and_placement_conflicts_are_rejected():
    from app.core_host.plugin_host_services import _SettingsHostService, HostServiceError
    from app.plugins.host_services import HOST_CALLER
    settings = _SettingsHostService(lambda *_: {})
    token = HOST_CALLER.set("owner")
    try:
        with pytest.raises(HostServiceError, match="SETTINGS_OWNER_INVALID"):
            settings.call("register", ["victim", {"kind": "page", "pageId": "x", "title": "X", "group": "ai"}, {}])
        page = settings.call("register", ["owner", {"kind": "page", "pageId": "x", "title": "X", "group": "ai"}, {}])
        with pytest.raises(HostServiceError, match="SETTINGS_UI_CONFLICT"):
            settings.call("register", ["owner", {"kind": "page", "pageId": "x", "title": "X", "group": "ai"}, {}])
    finally:
        HOST_CALLER.reset(token)
    token = HOST_CALLER.set("other")
    try:
        with pytest.raises(HostServiceError, match="SETTINGS_OWNER_INVALID"):
            settings.call("unregister", [page["registrationId"]])
    finally:
        HOST_CALLER.reset(token)
