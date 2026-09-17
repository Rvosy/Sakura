from pathlib import Path

import pytest

from app.core_host.plugin_application import PluginApplicationHost
from app.core_host.plugin_settings import PluginSettingsBoundary, PluginSettingsError
from app.plugin_sdk.sakura_tools import ToolRegistry
from app.plugins.runtime_v4 import PluginRuntimeError
from app.storage.runtime_roots import RuntimeRoots


def _write_plugin(root: Path, plugin_id: str) -> None:
    directory = root / "plugins" / "user" / plugin_id
    directory.mkdir(parents=True)
    (directory / "plugin.yaml").write_text(
        f"api: 4\nid: {plugin_id}\nname: Fixture\nversion: 1.0.0\nentry: plugin:Plugin\n",
        encoding="utf-8",
    )
    (directory / "plugin.py").write_text("class Plugin: pass\n", encoding="utf-8")


def test_visual_and_public_queries_share_inventory_until_an_explicit_refresh(tmp_path, monkeypatch):
    _write_plugin(tmp_path, "fixture.first")
    roots = RuntimeRoots(tmp_path, tmp_path)
    application = PluginApplicationHost(roots, "inventory-generation", ToolRegistry())
    try:
        _write_plugin(tmp_path, "fixture.second")
        scans = []
        scan = application._inventory.scan

        def counted_scan():
            scans.append(True)
            return scan()

        monkeypatch.setattr(application._inventory, "scan", counted_scan)
        application.visuals.catalog()
        application.visuals.candidates("fixture.visual@1")
        before = application.public_snapshot()
        assert {item["pluginId"] for item in before["plugins"]} == {"fixture.first"}
        assert scans == []

        settings = PluginSettingsBoundary(
            "inventory-generation", "credential", roots,
            application_provider=lambda: application,
        )
        refreshed = settings.snapshot()
        assert {item["pluginId"] for item in refreshed["plugins"]} == {
            "fixture.first", "fixture.second",
        }
        assert len(scans) == 1
        application.visuals.catalog()
        assert application.public_snapshot()["revision"] == refreshed["revision"]
        assert len(scans) == 1
    finally:
        application.close()


@pytest.mark.parametrize("operation", ["install", "uninstall"])
def test_failed_code_change_refreshes_inventory_after_rollback(tmp_path, monkeypatch, operation):
    user = tmp_path / "user"
    _write_plugin(user, "fixture.first")
    roots = RuntimeRoots(user, user)
    application = PluginApplicationHost(roots, "rollback-generation", ToolRegistry())
    settings = PluginSettingsBoundary(
        "rollback-generation", "credential", roots,
        application_provider=lambda: application,
    )

    def reject(_value):
        raise PluginRuntimeError("PLUGIN_LIFECYCLE_FAILED")

    monkeypatch.setattr(application._manager, f"{operation}_plugin", reject)
    try:
        snapshot = settings.snapshot()
        with pytest.raises(PluginSettingsError, match="未能应用"):
            if operation == "install":
                _write_plugin(tmp_path / "source", "fixture.second")
                settings.install(
                    snapshot["revision"], "folder",
                    str(tmp_path / "source/plugins/user/fixture.second"),
                )
            else:
                settings.uninstall(snapshot["revision"], snapshot["plugins"][0]["installId"])
        assert {item["pluginId"] for item in application.public_snapshot()["plugins"]} == {
            "fixture.first",
        }
    finally:
        application.close()
