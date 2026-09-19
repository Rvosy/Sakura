"""An invalid optional screen config must not remove the plugin management UI."""

from pathlib import Path
import shutil

import pytest

from app.core_host.plugin_application import PluginApplicationHost
from app.core_host.plugin_settings import PluginSettingsBoundary
from app.plugin_sdk.sakura_tools import ToolRegistry
from app.storage.paths import StoragePaths
from app.storage.runtime_roots import RuntimeRoots


@pytest.mark.parametrize("contents", [b'{"enabled":', b'[]'], ids=["malformed-json", "invalid-object"])
def test_damaged_screen_config_fails_only_that_plugin_and_preserves_management(tmp_path, contents):
    roots = RuntimeRoots(tmp_path / "distribution", tmp_path / "user")
    repo = Path(__file__).resolve().parents[2]
    shutil.copytree(repo / "plugins/builtin/sakura_screen_awareness",
        roots.distribution_root / "plugins/builtin/sakura_screen_awareness",
        ignore=shutil.ignore_patterns("__pycache__"))
    healthy = roots.distribution_root / "plugins/builtin/healthy"
    healthy.mkdir()
    (healthy / "plugin.yaml").write_text("api: 4\nid: fixture.healthy\nname: Healthy\nversion: 1.0.0\nentry: plugin:Plugin\nenabled: true\nprovides: []\nrequires: []\n", encoding="utf-8")
    (healthy / "plugin.py").write_text("class Plugin:\n    def setup(self, context):\n        pass\n", encoding="utf-8")
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
