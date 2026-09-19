"""An invalid optional screen config must not remove the plugin management UI."""

from pathlib import Path
import os
import shutil

import pytest

from app.core_host.plugin_application import PluginApplicationHost
from app.core_host.plugin_settings import PluginSettingsBoundary
from app.plugin_sdk.sakura_tools import ToolRegistry
from app.storage.paths import StoragePaths
from app.storage.runtime_roots import RuntimeRoots


def _roots(tmp_path):
    roots = RuntimeRoots(tmp_path / "distribution", tmp_path / "user")
    repo = Path(__file__).resolve().parents[2]
    shutil.copytree(repo / "plugins/builtin/sakura_screen_awareness",
        roots.distribution_root / "plugins/builtin/sakura_screen_awareness",
        ignore=shutil.ignore_patterns("__pycache__"))
    healthy = roots.distribution_root / "plugins/builtin/healthy"
    healthy.mkdir()
    (healthy / "plugin.yaml").write_text("api: 4\nid: fixture.healthy\nname: Healthy\nversion: 1.0.0\nentry: plugin:Plugin\nenabled: true\nprovides: []\nrequires: []\n", encoding="utf-8")
    (healthy / "plugin.py").write_text("class Plugin:\n    def setup(self, context):\n        pass\n", encoding="utf-8")
    return roots


@pytest.mark.parametrize("contents", [b'{"enabled":', b'[]'], ids=["malformed-json", "invalid-object"])
def test_damaged_screen_config_fails_only_that_plugin_and_preserves_management(tmp_path, contents):
    roots = _roots(tmp_path)
    target = StoragePaths(roots.user_root).plugin_data_for("sakura.screen_awareness") / "config.json"
    target.parent.mkdir(parents=True)
    target.write_bytes(contents)
    application = PluginApplicationHost(roots, "damaged-screen", ToolRegistry())
    try:
        application.start()
        assert application.wait_until_loaded(timeout=5)
        boundary = PluginSettingsBoundary("damaged-screen", "a" * 32, roots,
            application_provider=lambda: application)
        snapshot = boundary.snapshot()
        screen = next(row for row in snapshot["plugins"] if row["pluginId"] == "sakura.screen_awareness")
        assert screen["state"] == "failed"
        assert screen["reasonCode"] == "PLUGIN_CONFIG_INVALID"
        assert screen["installId"]
        assert any(row["pluginId"] == "fixture.healthy" and row["state"] == "active" for row in snapshot["plugins"])
        assert target.read_bytes() == contents
    finally:
        application.close()


@pytest.mark.parametrize("stage", ["read", "mkdir", "write", "replace", "replace-and-cleanup"])
def test_screen_migration_io_failure_preserves_config_and_management_can_disable_and_recover(tmp_path, monkeypatch, stage):
    roots = _roots(tmp_path)
    target = StoragePaths(roots.user_root).plugin_data_for("sakura.screen_awareness") / "config.json"
    target.parent.mkdir(parents=True)
    original = b'{"kept": "original"}'
    target.write_bytes(original)
    system = roots.user_root / "config/system_config.yaml"
    system.parent.mkdir(parents=True)
    system.write_text("config_version: 1\nscreen_awareness:\n  enabled: false\n", encoding="utf-8")
    original_system = system.read_bytes()
    active, attempted, logs = [True], [], []
    mkdir, write, replace, unlink = Path.mkdir, Path.write_text, os.replace, Path.unlink
    read = Path.read_text

    def fail(operation):
        if active[0] and operation == stage.split("-and-")[0]:
            attempted.append(operation)
            raise PermissionError(f"screen {operation} denied")

    def mkdir_screen(path, *args, **kwargs):
        if path == target.parent:
            fail("mkdir")
        return mkdir(path, *args, **kwargs)

    def read_screen(path, *args, **kwargs):
        if path == target:
            fail("read")
        return read(path, *args, **kwargs)

    def write_screen(path, *args, **kwargs):
        if path.parent == target.parent and path.name.startswith(".config-"):
            fail("write")
        return write(path, *args, **kwargs)

    def replace_screen(source, destination):
        if Path(destination) == target:
            fail("replace")
        return replace(source, destination)

    def unlink_screen(path, *args, **kwargs):
        if active[0] and stage == "replace-and-cleanup" and path.parent == target.parent and path.name.startswith(".config-"):
            raise PermissionError("screen cleanup denied")
        return unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "mkdir", mkdir_screen)
    monkeypatch.setattr(Path, "read_text", read_screen)
    monkeypatch.setattr(Path, "write_text", write_screen)
    monkeypatch.setattr(os, "replace", replace_screen)
    monkeypatch.setattr(Path, "unlink", unlink_screen)
    monkeypatch.setattr("app.core.runtime_log.log_message", lambda *args, **kwargs: logs.append(kwargs))
    application = PluginApplicationHost(roots, "screen-migration", ToolRegistry())
    try:
        application.start()
        boundary = PluginSettingsBoundary("screen-migration", "a" * 32, roots,
            application_provider=lambda: application)
        snapshot = boundary.snapshot()
        rows = {row["pluginId"]: row for row in snapshot["plugins"]}
        assert rows["fixture.healthy"]["state"] == "active"
        screen = rows["sakura.screen_awareness"]
        assert screen["state"] == "failed"
        assert screen["reasonCode"] == "SCREEN_SETTINGS_MIGRATION_FAILED"
        assert target.read_bytes() == original
        assert system.read_bytes() == original_system
        failure = next(log["fields"] for log in logs if log.get("fields", {}).get("reason_code") == "SCREEN_SETTINGS_MIGRATION_FAILED")
        assert f"screen {stage.split('-and-')[0]} denied" in failure["diagnostic"]
        if stage == "replace-and-cleanup":
            assert "screen cleanup denied" in failure["recovery_diagnostic"]
        disabled = boundary.set_enabled(snapshot["revision"], screen["installId"], False)
        assert disabled["applicationState"] == "applied"
        assert attempted == [stage.split("-and-")[0]], "disable must not retry migration"
        healthy = rows["fixture.healthy"]
        assert boundary.set_enabled(boundary.snapshot()["revision"], healthy["installId"], False)["applicationState"] == "applied"
        assert boundary.set_enabled(boundary.snapshot()["revision"], healthy["installId"], True)["applicationState"] == "applied"
        active[0] = False
        restored = boundary.set_enabled(boundary.snapshot()["revision"], screen["installId"], True)
        assert restored["applicationState"] == "applied"
        restored_screen = next(row for row in restored["plugins"] if row["pluginId"] == "sakura.screen_awareness")
        assert restored_screen["state"] == "active"
        assert restored_screen["sections"][0]["values"]["enabled"] is False
        assert system.read_bytes() == original_system
    finally:
        active[0] = False
        application.close()
