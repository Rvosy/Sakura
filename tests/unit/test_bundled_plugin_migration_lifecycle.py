from __future__ import annotations

import json
import shutil
import socket
from pathlib import Path

import pytest
import yaml

from app.plugins import bundled_migrations as migration
from app.plugins.dependencies import PluginDependencyRoots
from app.plugins.inventory import PluginDesiredStateStore, PluginInventory
from app.storage.runtime_roots import RuntimeRoots


OPTIONAL = Path(__file__).resolve().parents[2] / "plugins/optional"
MOBILE = "sakura_mobile"
SPINE = "sakura.visual.spine"

# v1.1.2:plugins/builtin/sakura_spine/plugin.yaml. Keep the historical manifest
# in the fixture so this regression does not silently track the payload version.
SPINE_112_MANIFEST = """api: 4
id: sakura.visual.spine
name: Spine
author: Sakura
description: Spine 3.6 角色的表情、动画和编辑。
version: 0.2.5
entry: plugin:SpinePlugin
enabled: true
priority: 40
provides:
  - sakura.visual.spine
requires:
  - sakura.host.character
  - sakura.host.logging
visuals:
  - type: spine.json@1
    service: sakura.visual.spine
    contract: 1
    renderer: renderer.mjs
    editor: editor.mjs
presentation:
  kind: provider
  category: model
  icon: person-standing
"""


@pytest.fixture(autouse=True)
def offline_migration(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Startup migration must stay offline")

    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(PluginDependencyRoots, "install", forbidden)
    monkeypatch.setattr(PluginDependencyRoots, "_uv_command", forbidden)


def roots_for(tmp_path, monkeypatch, plugins):
    monkeypatch.setattr(migration, "MIGRATIONS", plugins)
    roots = RuntimeRoots(tmp_path / "distribution", tmp_path / "user")
    (roots.user_root / "config").mkdir(parents=True)
    return roots


def copy_plugin(target, directory, *, historical_spine=False):
    shutil.copytree(OPTIONAL / directory, target)
    if historical_spine:
        (target / "plugin.yaml").write_text(SPINE_112_MANIFEST, encoding="utf-8")
    return target


def state_path(roots):
    return roots.user_root / "config" / migration.STATE_NAME


def forbid_completed_work(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Completed migration must not scan, import, or report progress again")

    monkeypatch.setattr(PluginInventory, "scan", forbidden)
    monkeypatch.setattr(PluginDependencyRoots, "_validate_entry", forbidden)
    monkeypatch.setattr(migration, "ensure_external_plugin", forbidden)
    return forbidden


def test_120_completed_copy_is_revalidated_once_then_belongs_to_user(tmp_path, monkeypatch):
    roots = roots_for(tmp_path, monkeypatch, {MOBILE: MOBILE})
    target = copy_plugin(roots.user_root / "plugins/user" / MOBILE, MOBILE)
    state_path(roots).write_text(json.dumps({MOBILE: "completed", "old.migration": "not_applicable"}))
    imports = []
    validate = PluginDependencyRoots._validate_entry

    def track_import(self, plugin_id, *args, **kwargs):
        imports.append(plugin_id)
        return validate(self, plugin_id, *args, **kwargs)

    monkeypatch.setattr(PluginDependencyRoots, "_validate_entry", track_import)
    events = []
    assert migration.migrate_bundled_plugins(roots, progress=events.append) == {}
    assert imports == [MOBILE]
    assert events[0]["state"] == "running"
    assert events[-1]["state"] == "completed"
    saved = state_path(roots).read_bytes()
    state = json.loads(saved)
    assert state["old.migration"] == "not_applicable"
    # This is the complete v1.2.0 parser contract, including metadata keys.
    assert isinstance(state, dict) and all(
        isinstance(key, str) and value in ("completed", "not_applicable", "repairing")
        for key, value in state.items()
    )

    forbidden = forbid_completed_work(monkeypatch)
    assert migration.migrate_bundled_plugins(roots, progress=forbidden) == {}
    # Later breakage/uninstall belongs to ordinary plugin management, not a
    # permanent migration health check that reinstalls a user's removed copy.
    (target / "plugin.py").unlink()
    assert migration.migrate_bundled_plugins(roots, progress=forbidden) == {}
    assert not (target / "plugin.py").exists()
    shutil.rmtree(target)
    assert migration.migrate_bundled_plugins(roots, progress=forbidden) == {}
    assert not target.exists()
    assert state_path(roots).read_bytes() == saved


@pytest.mark.parametrize("failure", ["entry_import", "inspection"])
def test_failed_revalidation_retries_only_the_unfinished_plugin(tmp_path, monkeypatch, failure):
    roots = roots_for(tmp_path, monkeypatch, {MOBILE: MOBILE, SPINE: "sakura_spine"})
    copy_plugin(roots.user_root / "plugins/user" / MOBILE, MOBILE)
    spine = copy_plugin(roots.user_root / "plugins/user" / SPINE, "sakura_spine")
    good_entry = (spine / "plugin.py").read_bytes()
    if failure == "entry_import":
        (spine / "plugin.py").write_text("raise RuntimeError('interrupted old installation')\n")
    state_path(roots).write_text(json.dumps({MOBILE: "completed", SPINE: "completed"}))
    with monkeypatch.context() as first_pass:
        if failure == "inspection":
            needs_repair = migration._needs_repair

            def unreadable_directory(roots, plugin_id):
                if plugin_id == SPINE:
                    raise OSError("old plugin directory is temporarily unreadable")
                return needs_repair(roots, plugin_id)

            first_pass.setattr(migration, "_needs_repair", unreadable_directory)
        assert migration.migrate_bundled_plugins(roots) == {SPINE: "PLUGIN_MIGRATION_FAILED"}
    state = json.loads(state_path(roots).read_text())
    assert state[MOBILE] == "completed"
    assert state[SPINE] == "repairing"

    # The user repairs the pending plugin; a previous success must stay final
    # even though the first pass could not complete every plugin.
    (spine / "plugin.py").write_bytes(good_entry)
    imports = []
    validate = PluginDependencyRoots._validate_entry

    def only_pending(self, plugin_id, *args, **kwargs):
        assert plugin_id == SPINE
        imports.append(plugin_id)
        return validate(self, plugin_id, *args, **kwargs)

    monkeypatch.setattr(PluginDependencyRoots, "_validate_entry", only_pending)
    events = []
    assert migration.migrate_bundled_plugins(roots, progress=events.append) == {}
    assert imports == [SPINE]
    assert [event["pluginId"] for event in events if event["state"] == "running"] == [SPINE]
    assert json.loads(state_path(roots).read_text())[SPINE] == "completed"
    forbidden = forbid_completed_work(monkeypatch)
    assert migration.migrate_bundled_plugins(roots, progress=forbidden) == {}


@pytest.mark.parametrize("overlay", [False, True])
def test_112_spine_preserves_local_version_or_uses_replacement_payload(tmp_path, monkeypatch, overlay):
    roots = roots_for(tmp_path, monkeypatch, {SPINE: "sakura_spine"})
    copy_plugin(roots.distribution_root / migration.PAYLOAD_PATH / "plugins/sakura_spine", "sakura_spine")
    if overlay:
        old = copy_plugin(roots.distribution_root / "plugins/builtin/sakura_spine", "sakura_spine",
                          historical_spine=True)
        (old / "local-notes.txt").write_text("retained 1.1.2 installation")
    PluginDesiredStateStore(roots.user_root).set(SPINE, False)
    before_desired = (roots.user_root / "config/plugins.yaml").read_bytes()

    assert migration.migrate_bundled_plugins(roots) == {}
    target = roots.user_root / "plugins/user" / SPINE
    manifest = yaml.safe_load((target / "plugin.yaml").read_text())
    assert manifest["version"] == ("0.2.5" if overlay else "0.2.7")
    if overlay:
        assert (target / "plugin.yaml").read_text() == SPINE_112_MANIFEST
        assert (target / "local-notes.txt").read_text() == "retained 1.1.2 installation"
    assert (roots.user_root / "config/plugins.yaml").read_bytes() == before_desired
    forbidden = forbid_completed_work(monkeypatch)
    assert migration.migrate_bundled_plugins(roots, progress=forbidden) == {}


def test_120_completed_historical_spine_is_repaired_despite_payload_version_difference(tmp_path, monkeypatch):
    roots = roots_for(tmp_path, monkeypatch, {SPINE: "sakura_spine"})
    old = copy_plugin(roots.distribution_root / "plugins/builtin/sakura_spine", "sakura_spine",
                      historical_spine=True)
    target = copy_plugin(roots.user_root / "plugins/user" / SPINE, "sakura_spine", historical_spine=True)
    copy_plugin(roots.distribution_root / migration.PAYLOAD_PATH / "plugins/sakura_spine", "sakura_spine")
    (target / "plugin.py").unlink()
    state_path(roots).write_text(json.dumps({SPINE: "completed"}))

    assert migration.migrate_bundled_plugins(roots) == {}
    assert (target / "plugin.py").read_bytes() == (old / "plugin.py").read_bytes()
    assert (target / "plugin.yaml").read_text() == SPINE_112_MANIFEST
    forbidden = forbid_completed_work(monkeypatch)
    assert migration.migrate_bundled_plugins(roots, progress=forbidden) == {}


def test_pre_121_explicit_uninstall_is_not_undone_by_one_time_revalidation(tmp_path, monkeypatch):
    roots = roots_for(tmp_path, monkeypatch, {MOBILE: MOBILE})
    copy_plugin(roots.distribution_root / migration.PAYLOAD_PATH / "plugins" / MOBILE, MOBILE)
    state_path(roots).write_text(json.dumps({MOBILE: "completed"}))
    # LocalPluginInstaller.uninstall removes both code and its desired-state
    # entry. Retained plugin data alone must not cause a reinstall.
    settings = roots.user_root / "data/plugins" / MOBILE / "config.json"
    settings.parent.mkdir(parents=True)
    settings.write_text('{"port": 8123}')
    before = settings.read_bytes()

    assert migration.migrate_bundled_plugins(roots) == {}
    assert not PluginInventory(roots).scan().records
    assert settings.read_bytes() == before
    forbidden = forbid_completed_work(monkeypatch)
    assert migration.migrate_bundled_plugins(roots, progress=forbidden) == {}
