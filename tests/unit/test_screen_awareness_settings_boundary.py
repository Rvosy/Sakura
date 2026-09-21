import json

import pytest

from app.core_host.screen_host import migrate_legacy_screen_settings
from app.storage.paths import StoragePaths
from plugins.builtin.sakura_screen_awareness.settings import ScreenAwarenessSettings


@pytest.mark.parametrize("legacy", ["enabled: false", "screen_context_enabled: false"])
def test_legacy_disabled_configuration_is_imported_without_rewriting_yaml(tmp_path, legacy):
    config = tmp_path / "config/system_config.yaml"
    config.parent.mkdir()
    original = f"config_version: 1\nscreen_awareness:\n  {legacy}\n  check_interval_minutes: 7\n  screen_context_resolution: 720p\n"
    config.write_text(original, encoding="utf-8")
    migrate_legacy_screen_settings(tmp_path)
    target = StoragePaths(tmp_path).plugin_data_for("sakura.screen_awareness") / "config.json"
    values = json.loads(target.read_text(encoding="utf-8"))
    assert ScreenAwarenessSettings.parse(values).values() == {
        "enabled": False, "checkIntervalMinutes": 7, "cooldownMinutes": 10,
        "batchLimit": 6, "resolution": "720p",
    }
    values["enabled"] = True
    target.write_text(json.dumps(values), encoding="utf-8")
    migrate_legacy_screen_settings(tmp_path)
    assert json.loads(target.read_text(encoding="utf-8"))["enabled"] is True
    assert config.read_text(encoding="utf-8") == original
