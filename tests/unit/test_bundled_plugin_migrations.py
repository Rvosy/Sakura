from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from app.plugins.bundled_migrations import migrate_bundled_plugins
from app.plugins.installer import LocalPluginInstaller, PluginInstallError
from app.plugins.inventory import PluginDesiredStateStore, PluginInventory
from app.storage.runtime_roots import RuntimeRoots

@pytest.fixture(autouse=True)
def mobile_only(monkeypatch):
    from app.plugins import bundled_migrations
    monkeypatch.setattr(bundled_migrations, "MIGRATIONS", {"sakura_mobile": "sakura_mobile"})
    from tools.release.package_optional_plugin import build
    monkeypatch.setattr(bundled_migrations, "download_migration_package", lambda plugin_id, output: build(SOURCE, output))


SOURCE = Path(__file__).resolve().parents[2] / "plugins/optional/sakura_mobile"


def roots_for(tmp_path, *, old=True):
    roots = RuntimeRoots(tmp_path / "distribution", tmp_path / "user")
    shutil.copytree(SOURCE, roots.distribution_root / "plugins/builtin/sakura_mobile")
    if old:
        (roots.user_root / "config").mkdir(parents=True)
    return roots


@pytest.mark.parametrize("enabled", [None, True, False])
@pytest.mark.parametrize("stale_builtin", [False, True])
def test_old_user_keeps_settings_and_enabled_state(tmp_path, enabled, stale_builtin):
    roots = roots_for(tmp_path)
    if enabled is not None:
        PluginDesiredStateStore(roots.user_root).set("sakura_mobile", enabled)
    if not stale_builtin:
        shutil.rmtree(roots.distribution_root / "plugins/builtin/sakura_mobile")
    config = roots.user_root / "data/plugins/sakura_mobile/config.json"
    config.parent.mkdir(parents=True)
    original = '{"port": 8888, "token": "test-token", "enabled": true}'
    config.write_text(original)
    migrate_bundled_plugins(roots)
    records = PluginInventory(roots).scan().records
    assert len(records) == 1 and records[0].source == "user"
    assert records[0].desired_enabled is (True if enabled is None else enabled)
    assert config.read_text() == original
    assert json.loads((roots.user_root / "config/plugin-migrations.json").read_text()) == {"sakura_mobile": "completed"}
    # A restart after explicit uninstall must not restore either copy.
    LocalPluginInstaller(roots).uninstall(records[0].install_id)
    migrate_bundled_plugins(roots)
    assert not PluginInventory(roots).scan().records


@pytest.mark.parametrize("desktop_seeded", [False, True])
def test_new_user_does_not_install_migration_payload(tmp_path, desktop_seeded):
    roots = roots_for(tmp_path, old=False)
    if desktop_seeded:
        config = roots.user_root / "config"
        config.mkdir(parents=True)
        seed = SOURCE.parents[2] / "desktop/src-tauri/src/new_user_plugin_migrations.json"
        shutil.copy2(seed, config / "plugin-migrations.json")
    migrate_bundled_plugins(roots)
    migrate_bundled_plugins(roots)
    assert not PluginInventory(roots).scan().records


def test_new_application_classifies_user_before_model_configuration_is_written(tmp_path, monkeypatch):
    from app.core_host.plugin_application import PluginApplicationHost
    from app.plugin_sdk.sakura_tools import ToolRegistry
    from app.plugins import bundled_migrations

    roots = roots_for(tmp_path, old=False)

    def unexpected_restore(*args, **kwargs):
        pytest.fail("A new user must not restore retired plugins")

    monkeypatch.setattr(bundled_migrations, "ensure_external_plugin", unexpected_restore)
    application = PluginApplicationHost(roots, "new-user", ToolRegistry())
    try:
        assert (roots.user_root / "config/model_slots.json").is_file()
        state = json.loads((roots.user_root / "config/plugin-migrations.json").read_text())
        assert state == {"sakura_mobile": "not_applicable"}
        assert not application.inventory().records
    finally:
        application.close()


def test_existing_external_by_id_is_not_overwritten_and_others_do_not_skip_migration(tmp_path):
    roots = roots_for(tmp_path)
    other = roots.user_root / "plugins/user/other"
    other.mkdir(parents=True)
    (other / "plugin.yaml").write_text("api: 4\nid: other\nentry: plugin:Plugin\nversion: 1.0\n")
    migrate_bundled_plugins(roots)
    assert any(r.plugin_id == "sakura_mobile" for r in PluginInventory(roots).scan().records)
    state = roots.user_root / "config/plugin-migrations.json"
    state.unlink()
    installed = roots.user_root / "plugins/user/sakura_mobile"
    renamed = installed.with_name("custom-mobile-name")
    installed.rename(renamed)
    (renamed / "plugin.py").write_text("# user's external version")
    PluginDesiredStateStore(roots.user_root).set("sakura_mobile", False)
    migrate_bundled_plugins(roots)
    assert (renamed / "plugin.py").read_text() == "# user's external version"
    assert not installed.exists()
    assert PluginDesiredStateStore(roots.user_root).read()["sakura_mobile"] is False


def test_failed_install_remains_retryable_without_changing_old_state(tmp_path, monkeypatch):
    roots = roots_for(tmp_path)
    PluginDesiredStateStore(roots.user_root).set("sakura_mobile", True)
    before = (roots.user_root / "config/plugins.yaml").read_bytes()
    original = LocalPluginInstaller._replace_path
    def fail(*args):
        raise OSError("disk unavailable")
    monkeypatch.setattr(LocalPluginInstaller, "_replace_path", fail)
    with pytest.raises(RuntimeError):
        migrate_bundled_plugins(roots)
    assert not (roots.user_root / "config/plugin-migrations.json").exists()
    assert (roots.user_root / "config/plugins.yaml").read_bytes() == before
    assert not PluginInventory(roots).scan().records
    monkeypatch.setattr(LocalPluginInstaller, "_replace_path", staticmethod(original))
    migrate_bundled_plugins(roots)
    assert PluginInventory(roots).scan().records[0].desired_enabled


def test_missing_payload_does_not_silently_complete(tmp_path, monkeypatch):
    roots = roots_for(tmp_path)
    shutil.rmtree(roots.distribution_root / "plugins/builtin")
    from app.plugins import bundled_migrations
    def fail(*args):
        raise OSError("network unavailable")
    monkeypatch.setattr(bundled_migrations, "download_migration_package", fail)
    with pytest.raises(RuntimeError):
        migrate_bundled_plugins(roots)
    assert not (roots.user_root / "config/plugin-migrations.json").exists()


@pytest.mark.parametrize("development", [False, True])
def test_cache_only_old_directory_uses_complete_payload(tmp_path, monkeypatch, development):
    from app.plugins import bundled_migrations
    from tools.release.package_optional_plugin import build

    roots = roots_for(tmp_path)
    old = roots.distribution_root / "plugins/builtin/sakura_mobile"
    shutil.rmtree(old)
    (old / "__pycache__").mkdir(parents=True)
    (old / "__pycache__/plugin.cpython-313.pyc").write_bytes(b"old cache")
    downloaded = []

    def download(plugin_id, output):
        downloaded.append(plugin_id)
        build(SOURCE, output)

    monkeypatch.setattr(bundled_migrations, "download_migration_package", download)
    if development:
        (roots.distribution_root / "app/core_host").mkdir(parents=True)
        shutil.copytree(SOURCE, roots.distribution_root / "plugins/optional/sakura_mobile")
    migrate_bundled_plugins(roots)
    records = PluginInventory(roots).scan().records
    assert len(records) == 1
    assert records[0].plugin_id == "sakura_mobile" and records[0].source == "user"
    assert downloaded == ([] if development else ["sakura_mobile"])


def test_all_retired_plugins_migrate_offline_before_core_inventory(tmp_path, monkeypatch):
    import sys
    from app.plugins import bundled_migrations
    from app.core_host.plugin_runtime_application import PluginRuntimeApplication
    from app.plugin_sdk.sakura_tools import ToolRegistry
    migrations = {key: value["directory"] for key, value in bundled_migrations.SOURCES.items()}
    monkeypatch.setattr(bundled_migrations, "MIGRATIONS", migrations)
    def network_forbidden(*args):
        raise AssertionError("local migration must not download")
    monkeypatch.setattr(bundled_migrations, "download_migration_package", network_forbidden)
    roots = RuntimeRoots(tmp_path / "distribution", tmp_path / "user")
    (roots.user_root / "config").mkdir(parents=True)
    for plugin_id, directory in migrations.items():
        source = roots.distribution_root / "plugins/builtin" / directory
        source.mkdir(parents=True)
        (source / "plugin.yaml").write_text(f"api: 4\nid: {plugin_id}\nversion: 1.0.0\nentry: plugin:Plugin\n")
        (source / "plugin.py").write_text("import migration_probe\nclass Plugin:\n    def setup(self, context): pass\n")
        (source / "requirements.txt").write_text("migration-probe==1.0\n")
        dependency = roots.distribution_root / "plugins/dependencies" / plugin_id
        dependency.mkdir(parents=True)
        (dependency / "migration_probe.py").write_text("value = 1\n")
        (dependency / ".sakura-dependencies.json").write_text(json.dumps({"schemaVersion": 1, "kind": "requirements.txt", "python": f"{sys.version_info.major}.{sys.version_info.minor}"}))
    PluginDesiredStateStore(roots.user_root).set("sakura.tts.genie", False)
    application = PluginRuntimeApplication(roots, "migration-startup", ToolRegistry([]))
    try:
        records = application.inventory().records
        assert {r.plugin_id for r in records} == set(migrations)
        assert all(r.source == "user" for r in records)
        assert all(r.desired_enabled == (r.plugin_id != "sakura.tts.genie") for r in records)
        assert all((roots.user_root / "data/plugin-runtime/dependencies" / key / "migration_probe.py").is_file() for key in migrations)
    finally:
        application.close()


def test_corrupt_marker_does_not_overwrite_user_plugins(tmp_path):
    roots = roots_for(tmp_path)
    marker = roots.user_root / "config/plugin-migrations.json"
    marker.write_text('{"sakura_mobile": "unknown"}')
    with pytest.raises(ValueError, match="PLUGIN_MIGRATION_STATE_INVALID"):
        migrate_bundled_plugins(roots)
    assert not (roots.user_root / "plugins/user/sakura_mobile").exists()


def test_interrupted_dependency_publish_is_reused_without_overwrite(tmp_path, monkeypatch):
    import sys
    from app.plugins import bundled_migrations
    from app.plugins.dependencies import PluginDependencyRoots
    from app.storage.paths import StoragePaths
    from tools.release.package_optional_plugin import build
    roots = roots_for(tmp_path)
    payload = tmp_path / "payload"
    shutil.copytree(SOURCE, payload)
    (payload / "requirements.txt").write_text("migration-probe==1.0\n")
    shutil.rmtree(roots.distribution_root / "plugins/builtin")
    dependency = StoragePaths(roots.user_root).plugin_dependency_root_for("sakura_mobile")
    dependency.mkdir(parents=True)
    (dependency / "owned.py").write_text("# already published environment")
    (dependency / ".sakura-dependencies.json").write_text(json.dumps({"schemaVersion": 1, "kind": "requirements.txt", "python": f"{sys.version_info.major}.{sys.version_info.minor}"}))
    def no_reinstall(*args, **kwargs):
        raise AssertionError("must reuse the already published dependencies")
    monkeypatch.setattr(PluginDependencyRoots, "install", no_reinstall)
    monkeypatch.setattr(bundled_migrations, "download_migration_package", lambda plugin_id, output: build(payload, output))
    migrate_bundled_plugins(roots)
    assert (dependency / "owned.py").read_text() == "# already published environment"
    assert PluginInventory(roots).scan().records[0].desired_enabled


def test_new_desktop_seed_covers_all_retired_plugins(tmp_path, monkeypatch):
    from app.plugins import bundled_migrations
    monkeypatch.setattr(bundled_migrations, "MIGRATIONS", {key: value["directory"] for key, value in bundled_migrations.SOURCES.items()})
    roots = roots_for(tmp_path)
    seed = SOURCE.parents[2] / "desktop/src-tauri/src/new_user_plugin_migrations.json"
    shutil.copy2(seed, roots.user_root / "config/plugin-migrations.json")
    def no_install(*args, **kwargs):
        raise AssertionError("a new user must not restore any retired plugin")
    monkeypatch.setattr(bundled_migrations, "ensure_external_plugin", no_install)
    migrate_bundled_plugins(roots)
    assert not PluginInventory(roots).scan().records


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
