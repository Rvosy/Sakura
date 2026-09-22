from __future__ import annotations

import json
import shutil
import socket
import sys
from pathlib import Path

import pytest

from app.plugins import bundled_migrations as migration
from app.plugins.bundled_migrations import PAYLOAD_PATH, migrate_bundled_plugins
from app.plugins.dependencies import PluginDependencyRoots
from app.plugins.installer import LocalPluginInstaller
from app.plugins.inventory import PluginDesiredStateStore, PluginInventory
from app.storage.paths import StoragePaths
from app.storage.runtime_roots import RuntimeRoots

SOURCE = Path(__file__).resolve().parents[2] / "plugins/optional/sakura_mobile"
PLUGIN = "sakura_mobile"


@pytest.fixture(autouse=True)
def offline_mobile_only(monkeypatch):
    monkeypatch.setattr(migration, "MIGRATIONS", {PLUGIN: PLUGIN})
    def forbidden(*args, **kwargs):
        pytest.fail("Startup migration must not access the network or install dependencies")
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(PluginDependencyRoots, "install", forbidden)
    monkeypatch.setattr(PluginDependencyRoots, "_uv_command", forbidden)


def roots_for(tmp_path, *, old=True):
    roots = RuntimeRoots(tmp_path / "distribution", tmp_path / "user")
    shutil.copytree(SOURCE, roots.distribution_root / "plugins/builtin" / PLUGIN)
    shutil.copytree(SOURCE, roots.distribution_root / PAYLOAD_PATH / "plugins" / PLUGIN)
    if old:
        (roots.user_root / "config").mkdir(parents=True)
    return roots


def installed(roots):
    return roots.user_root / "plugins/user" / PLUGIN


def marker(roots):
    return roots.user_root / "config/plugin-migrations.json"


def mark_120_completed(roots):
    # 1.2.0 can record completion before dependency/entry damage is discovered.
    marker(roots).write_text(json.dumps({PLUGIN: "completed"}))


def add_dependencies(code, dependencies):
    (code / "requirements.txt").write_text("migration-probe==1.0\n")
    (code / "plugin.py").write_text("import migration_probe\nclass SakuraMobilePlugin: pass\n")
    dependencies.mkdir(parents=True, exist_ok=True)
    (dependencies / "migration_probe.py").write_text("value = 1\n")
    (dependencies / ".sakura-dependencies.json").write_text(json.dumps({
        "schemaVersion": 1, "kind": "requirements.txt", "python": f"{sys.version_info.major}.{sys.version_info.minor}"}))


@pytest.mark.parametrize("enabled", [None, True, False])
@pytest.mark.parametrize("stale_builtin", [False, True])
def test_old_user_keeps_settings_and_enabled_state(tmp_path, enabled, stale_builtin):
    roots = roots_for(tmp_path)
    if enabled is not None:
        PluginDesiredStateStore(roots.user_root).set(PLUGIN, enabled)
    if not stale_builtin:
        shutil.rmtree(roots.distribution_root / "plugins/builtin")
    config = roots.user_root / "data/plugins/sakura_mobile/config.json"
    config.parent.mkdir(parents=True)
    original = '{"port": 8888, "token": "test-token", "enabled": true}'
    config.write_text(original)
    assert migrate_bundled_plugins(roots) == {}
    records = PluginInventory(roots).scan().records
    assert len(records) == 1 and records[0].source == "user"
    assert records[0].desired_enabled is (True if enabled is None else enabled)
    assert config.read_text() == original
    assert json.loads(marker(roots).read_text()) == {
        PLUGIN: "completed", migration.REVALIDATED_KEY: "completed",
    }
    LocalPluginInstaller(roots).uninstall(records[0].install_id)
    assert migrate_bundled_plugins(roots) == {}
    assert not PluginInventory(roots).scan().records


@pytest.mark.parametrize("desktop_seeded", [False, True])
def test_new_user_does_not_install_migration_payload(tmp_path, desktop_seeded):
    roots = roots_for(tmp_path, old=False)
    if desktop_seeded:
        marker(roots).parent.mkdir(parents=True)
        seed = SOURCE.parents[2] / "desktop/src-tauri/src/new_user_plugin_migrations.json"
        shutil.copy2(seed, marker(roots))
    assert migrate_bundled_plugins(roots) == {}
    assert migrate_bundled_plugins(roots) == {}
    assert not PluginInventory(roots).scan().records


def test_new_application_classifies_user_before_model_configuration_is_written(tmp_path, monkeypatch):
    from app.core_host.plugin_application import PluginApplicationHost
    from app.plugin_sdk.sakura_tools import ToolRegistry
    roots = roots_for(tmp_path, old=False)
    def unexpected_restore(*args, **kwargs):
        pytest.fail("A new user must not restore retired plugins")
    monkeypatch.setattr(migration, "ensure_external_plugin", unexpected_restore)
    application = PluginApplicationHost(roots, "new-user", ToolRegistry())
    try:
        assert (roots.user_root / "config/model_slots.json").is_file()
        assert json.loads(marker(roots).read_text()) == {PLUGIN: "not_applicable"}
        assert not application.inventory().records
    finally:
        application.close()


def test_existing_external_by_id_and_newer_version_are_not_overwritten(tmp_path):
    roots = roots_for(tmp_path)
    other = roots.user_root / "plugins/user/custom-mobile-name"
    shutil.copytree(SOURCE, other)
    manifest = other / "plugin.yaml"
    manifest.write_text(manifest.read_text().replace("version: 1.0.0", "version: 9.0.0"))
    (other / "plugin.py").write_text("# user's external version")
    PluginDesiredStateStore(roots.user_root).set(PLUGIN, False)
    for _ in range(2):
        assert migrate_bundled_plugins(roots) == {}
        assert (other / "plugin.py").read_text() == "# user's external version"
        assert not installed(roots).exists()
        assert PluginDesiredStateStore(roots.user_root).read()[PLUGIN] is False


def test_local_source_wins_over_payload(tmp_path):
    roots = roots_for(tmp_path)
    old = roots.distribution_root / "plugins/builtin" / PLUGIN
    (old / "local-notes.txt").write_text("old distribution copy")
    assert migrate_bundled_plugins(roots) == {}
    assert (installed(roots) / "local-notes.txt").read_text() == "old distribution copy"


def test_failed_publish_is_retryable_and_does_not_change_enabled_state(tmp_path, monkeypatch):
    roots = roots_for(tmp_path)
    PluginDesiredStateStore(roots.user_root).set(PLUGIN, False)
    before = (roots.user_root / "config/plugins.yaml").read_bytes()
    replace = migration.os.replace
    def fail(source, target):
        if Path(target) == installed(roots):
            raise OSError("disk unavailable")
        return replace(source, target)
    monkeypatch.setattr(migration.os, "replace", fail)
    assert migrate_bundled_plugins(roots) == {PLUGIN: "PLUGIN_MIGRATION_FAILED"}
    assert json.loads(marker(roots).read_text())[PLUGIN] == "repairing"
    assert (roots.user_root / "config/plugins.yaml").read_bytes() == before
    assert not installed(roots).exists()
    monkeypatch.setattr(migration.os, "replace", replace)
    assert migrate_bundled_plugins(roots) == {}
    assert not PluginInventory(roots).scan().records[0].desired_enabled


def test_missing_source_is_reported_without_completing(tmp_path):
    roots = roots_for(tmp_path)
    shutil.rmtree(roots.distribution_root)
    events = []
    assert migrate_bundled_plugins(roots, progress=events.append) == {PLUGIN: "PLUGIN_MIGRATION_SOURCE_MISSING"}
    assert json.loads(marker(roots).read_text())[PLUGIN] == "repairing"
    assert events[-1]["state"] == "failed"


@pytest.mark.parametrize("development", [False, True])
def test_cache_only_old_directory_uses_complete_local_payload(tmp_path, development):
    roots = roots_for(tmp_path)
    old = roots.distribution_root / "plugins/builtin" / PLUGIN
    shutil.rmtree(old)
    (old / "__pycache__").mkdir(parents=True)
    if development:
        shutil.rmtree(roots.distribution_root / PAYLOAD_PATH)
        (roots.distribution_root / "app/core_host").mkdir(parents=True)
        shutil.copytree(SOURCE, roots.distribution_root / "plugins/optional" / PLUGIN)
    assert migrate_bundled_plugins(roots) == {}
    assert PluginInventory(roots).scan().records[0].source == "user"


@pytest.mark.parametrize("text", ['{"sakura_mobile":', '[]', '{"sakura_mobile": "unknown"}'])
def test_corrupt_marker_is_preserved_without_blocking_core(tmp_path, text):
    roots = roots_for(tmp_path)
    marker(roots).write_text(text)
    assert migrate_bundled_plugins(roots)[PLUGIN] == "PLUGIN_MIGRATION_STATE_INVALID"
    assert marker(roots).read_text() == text
    assert not installed(roots).exists()


@pytest.mark.parametrize("state", [None, "completed", "repairing"])
def test_partial_dependency_root_is_repaired_offline(tmp_path, state):
    roots = roots_for(tmp_path)
    shutil.rmtree(roots.distribution_root / "plugins/builtin")
    payload = roots.distribution_root / PAYLOAD_PATH
    add_dependencies(payload / "plugins" / PLUGIN, payload / "dependencies" / PLUGIN)
    dependency = StoragePaths(roots.user_root).plugin_dependency_root_for(PLUGIN)
    dependency.mkdir(parents=True)
    (dependency / "partial.txt").write_text("preserve failed environment")
    if state:
        shutil.copytree(payload / "plugins" / PLUGIN, installed(roots))
        marker(roots).write_text(json.dumps({PLUGIN: state}))
    assert migrate_bundled_plugins(roots) == {}
    assert (dependency / "migration_probe.py").is_file()
    backups = list((roots.user_root / "plugins/migration-backups").glob("*/dependencies/sakura_mobile/partial.txt"))
    assert len(backups) == 1
    assert backups[0].read_text() == "preserve failed environment"
    assert json.loads(marker(roots).read_text())[PLUGIN] == "completed"


def test_interrupted_dependency_publish_is_reused(tmp_path):
    roots = roots_for(tmp_path)
    shutil.rmtree(roots.distribution_root / "plugins/builtin")
    payload = roots.distribution_root / PAYLOAD_PATH
    dependency = StoragePaths(roots.user_root).plugin_dependency_root_for(PLUGIN)
    add_dependencies(payload / "plugins" / PLUGIN, dependency)
    (dependency / "owned.txt").write_text("already published")
    marker(roots).write_text(json.dumps({PLUGIN: "repairing"}))
    assert migrate_bundled_plugins(roots) == {}
    assert (dependency / "owned.txt").read_text() == "already published"


@pytest.mark.parametrize("fails", [False, True])
def test_retired_api_repair_prepares_before_moving_original(tmp_path, monkeypatch, fails):
    roots = roots_for(tmp_path)
    assert migrate_bundled_plugins(roots) == {}
    mark_120_completed(roots)
    manifest = installed(roots) / "plugin.yaml"
    manifest.write_text(manifest.read_text() + "\nrequires: [sakura.host.model_slots]\n")
    original = manifest.read_bytes()
    (installed(roots) / "local-notes.txt").write_text("preserve local edits")
    if fails:
        def fail(*args, **kwargs):
            assert manifest.read_bytes() == original
            raise OSError("disk full while preparing")
        monkeypatch.setattr(migration.shutil, "copytree", fail)
    failures = migrate_bundled_plugins(roots)
    if fails:
        assert failures == {PLUGIN: "PLUGIN_MIGRATION_FAILED"}
        assert manifest.read_bytes() == original
    else:
        assert failures == {}
        assert "sakura.host.model_slots" not in PluginInventory(roots).scan().records[0].requires
        backups = list((roots.user_root / "plugins/migration-backups").glob("*/sakura_mobile"))
        assert len(backups) == 1
        assert (backups[0] / "plugin.yaml").read_bytes() == original
        assert (backups[0] / "local-notes.txt").read_text() == "preserve local edits"
        events = []
        assert migrate_bundled_plugins(roots, progress=events.append) == {}
        assert all(event["state"] != "failed" for event in events)


def test_120_interrupted_repair_recovers_backup_without_distribution(tmp_path):
    roots = roots_for(tmp_path)
    assert migrate_bundled_plugins(roots) == {}
    backup = roots.user_root / "plugins/migration-backups/interrupted/sakura_mobile"
    backup.parent.mkdir(parents=True)
    installed(roots).rename(backup)
    (backup / "local-notes.txt").write_text("preserve backup")
    shutil.rmtree(roots.distribution_root)
    marker(roots).write_text(json.dumps({PLUGIN: "repairing"}))
    assert migrate_bundled_plugins(roots) == {}
    assert (installed(roots) / "local-notes.txt").read_text() == "preserve backup"
    assert (backup / "plugin.yaml").is_file()


def test_one_failed_plugin_does_not_skip_other_migrations(tmp_path, monkeypatch):
    roots = roots_for(tmp_path)
    monkeypatch.setattr(migration, "MIGRATIONS", {"missing.plugin": "missing", PLUGIN: PLUGIN})
    events = []
    assert migrate_bundled_plugins(roots, progress=events.append) == {"missing.plugin": "PLUGIN_MIGRATION_SOURCE_MISSING"}
    assert (installed(roots) / "plugin.yaml").is_file()
    assert events[-1] == {"state": "failed", "completed": 1, "total": 2, "pluginId": "missing.plugin"}


def test_legacy_worker_uses_installed_plugin_helpers_without_bundled_code(tmp_path, monkeypatch):
    import sys
    from app.legacy_import import plugin_support
    roots = RuntimeRoots(tmp_path / "distribution", tmp_path / "user")
    plugin = roots.user_root / "plugins/user/external-memory"
    plugin.mkdir(parents=True)
    (plugin / "plugin.yaml").write_text("api: 4\nid: sakura.memory.mem0\nversion: 1.0.0\nentry: plugin:Plugin\n")
    (plugin / "plugin.py").write_text("class Plugin: pass\n")
    (plugin / "__init__.py").write_text("")
    (plugin / "memory.py").write_text("source = 'installed helper'\n")
    monkeypatch.setattr(plugin_support, "_roots", roots)
    # This module name is owned only by the isolated legacy worker.
    monkeypatch.delitem(sys.modules, "sakura_legacy_sakura_mem0", raising=False)
    monkeypatch.delitem(sys.modules, "sakura_legacy_sakura_mem0.memory", raising=False)
    from app.plugins import bundled_migrations
    monkeypatch.setattr(bundled_migrations, "MIGRATIONS", {"sakura.memory.mem0": "sakura_mem0"})
    try:
        assert plugin_support.migration_module("sakura_mem0.memory").source == "installed helper"
    finally:
        sys.modules.pop("sakura_legacy_sakura_mem0.memory", None)
        sys.modules.pop("sakura_legacy_sakura_mem0", None)


def test_completed_plugin_missing_entry_is_repaired(tmp_path):
    roots = roots_for(tmp_path)
    assert migrate_bundled_plugins(roots) == {}
    mark_120_completed(roots)
    (installed(roots) / "plugin.py").unlink()
    assert migrate_bundled_plugins(roots) == {}
    assert (installed(roots) / "plugin.py").read_bytes() == (SOURCE / "plugin.py").read_bytes()


@pytest.mark.parametrize("damage", ["entry", "dependencies"])
def test_existing_partial_copy_without_migration_record_is_repaired(tmp_path, damage):
    roots = roots_for(tmp_path)
    shutil.rmtree(roots.distribution_root / "plugins/builtin")
    payload = roots.distribution_root / PAYLOAD_PATH
    payload_code = payload / "plugins" / PLUGIN
    if damage == "dependencies":
        add_dependencies(payload_code, payload / "dependencies" / PLUGIN)
    shutil.copytree(payload_code, installed(roots))
    if damage == "entry":
        (installed(roots) / "plugin.py").unlink()
    else:
        dependency = StoragePaths(roots.user_root).plugin_dependency_root_for(PLUGIN)
        shutil.copytree(payload / "dependencies" / PLUGIN, dependency)
        (dependency / "migration_probe.py").unlink()
    PluginDesiredStateStore(roots.user_root).set(PLUGIN, False)
    before = (roots.user_root / "config/plugins.yaml").read_bytes()
    # 1.2.0 publishes code before writing its migration record. Retained user
    # files can therefore outlive that record during an interrupted upgrade.
    assert not marker(roots).exists()

    assert migrate_bundled_plugins(roots) == {}
    assert (installed(roots) / "plugin.py").read_bytes() == (payload_code / "plugin.py").read_bytes()
    dependencies = PluginDependencyRoots(roots.user_root)
    dependency = dependencies.verified_root(PLUGIN, installed(roots))
    dependencies._validate_entry(PLUGIN, installed(roots), dependency, "plugin:SakuraMobilePlugin")
    assert (roots.user_root / "config/plugins.yaml").read_bytes() == before


@pytest.mark.parametrize("remaining_module", [False, True])
def test_completed_partial_dependencies_with_valid_marker_are_repaired(tmp_path, remaining_module):
    roots = roots_for(tmp_path)
    shutil.rmtree(roots.distribution_root / "plugins/builtin")
    payload = roots.distribution_root / PAYLOAD_PATH
    add_dependencies(payload / "plugins" / PLUGIN, payload / "dependencies" / PLUGIN)
    assert migrate_bundled_plugins(roots) == {}
    mark_120_completed(roots)
    dependency = StoragePaths(roots.user_root).plugin_dependency_root_for(PLUGIN)
    (dependency / "migration_probe.py").unlink()
    if remaining_module:
        (dependency / "another_module.py").write_text("# retained module")
    assert migrate_bundled_plugins(roots) == {}
    assert (dependency / "migration_probe.py").is_file()


def test_marker_only_root_is_repaired_even_when_entry_defers_imports(tmp_path):
    roots = roots_for(tmp_path)
    shutil.rmtree(roots.distribution_root / "plugins/builtin")
    payload = roots.distribution_root / PAYLOAD_PATH
    add_dependencies(payload / "plugins" / PLUGIN, payload / "dependencies" / PLUGIN)
    (payload / "plugins" / PLUGIN / "plugin.py").write_text("class SakuraMobilePlugin: pass\n")
    assert migrate_bundled_plugins(roots) == {}
    mark_120_completed(roots)
    dependency = StoragePaths(roots.user_root).plugin_dependency_root_for(PLUGIN)
    (dependency / "migration_probe.py").unlink()
    assert migrate_bundled_plugins(roots) == {}
    assert (dependency / "migration_probe.py").is_file()


def test_valid_empty_dependency_declaration_does_not_force_repair(tmp_path, monkeypatch):
    roots = roots_for(tmp_path)
    old = roots.distribution_root / "plugins/builtin" / PLUGIN
    (old / "requirements.txt").write_text("# no private dependencies\n")
    dependency = roots.distribution_root / "plugins/dependencies" / PLUGIN
    dependency.mkdir(parents=True)
    (dependency / ".sakura-dependencies.json").write_text(json.dumps({
        "schemaVersion": 1, "kind": "requirements.txt", "python": f"{sys.version_info.major}.{sys.version_info.minor}"}))
    assert migrate_bundled_plugins(roots) == {}
    mark_120_completed(roots)
    def no_repair(*args, **kwargs):
        pytest.fail("An empty dependency declaration must remain usable")
    monkeypatch.setattr(migration, "ensure_external_plugin", no_repair)
    assert migrate_bundled_plugins(roots) == {}


def test_broken_dependency_declaration_uses_compatible_payload(tmp_path):
    roots = roots_for(tmp_path)
    assert migrate_bundled_plugins(roots) == {}
    mark_120_completed(roots)
    (installed(roots) / "pyproject.toml").write_text("[project\n")
    assert migrate_bundled_plugins(roots) == {}
    assert not (installed(roots) / "pyproject.toml").exists()
    assert list((roots.user_root / "plugins/migration-backups").glob("*/sakura_mobile/pyproject.toml"))


def test_repair_does_not_replace_another_plugin_in_the_target_directory(tmp_path):
    roots = roots_for(tmp_path)
    target = installed(roots)
    target.mkdir(parents=True)
    (target / "plugin.yaml").write_text("api: 4\nid: another.plugin\nversion: 1.0.0\nentry: plugin:Plugin\n")
    (target / "plugin.py").write_text("class Plugin: pass\n")
    marker(roots).write_text(json.dumps({PLUGIN: "completed"}))
    before = (target / "plugin.yaml").read_bytes()
    assert migrate_bundled_plugins(roots) == {PLUGIN: "PLUGIN_MIGRATION_FAILED"}
    assert (target / "plugin.yaml").read_bytes() == before


@pytest.mark.parametrize("state", ["completed", "repairing"])
@pytest.mark.parametrize("damage", ["entry_missing", "retired_api"])
def test_user_version_is_preserved_even_when_it_cannot_run(tmp_path, state, damage):
    roots = roots_for(tmp_path)
    shutil.copytree(SOURCE, installed(roots))
    manifest = installed(roots) / "plugin.yaml"
    manifest.write_text(manifest.read_text().replace("version: 1.0.0", "version: 9.0.0"))
    if damage == "entry_missing":
        (installed(roots) / "plugin.py").unlink()
    else:
        manifest.write_text(manifest.read_text() + "\nrequires: [sakura.host.model_slots]\n")
    before = manifest.read_bytes()
    marker(roots).write_text(json.dumps({PLUGIN: state}))
    assert migrate_bundled_plugins(roots) == {}
    assert manifest.read_bytes() == before
    if damage == "entry_missing":
        assert not (installed(roots) / "plugin.py").exists()


def test_existing_copy_import_checks_are_inside_the_migration_phase(tmp_path, monkeypatch):
    roots = roots_for(tmp_path)
    assert migrate_bundled_plugins(roots) == {}
    mark_120_completed(roots)
    events = []
    validate = PluginDependencyRoots._validate_entry
    def validate_in_migration(self, *args, **kwargs):
        assert events[-1]["state"] == "running"
        return validate(self, *args, **kwargs)
    monkeypatch.setattr(PluginDependencyRoots, "_validate_entry", validate_in_migration)
    assert migrate_bundled_plugins(roots, progress=events.append) == {}
    assert events[-1]["state"] == "completed"
