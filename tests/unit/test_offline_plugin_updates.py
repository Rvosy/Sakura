from __future__ import annotations

import json
import os
import shutil
import socket
import sys

import pytest
import yaml

from app.plugins import offline_updates as updates
from app.plugins.dependencies import PluginDependencyRoots
from app.plugins.inventory import PluginDesiredStateStore, PluginInventory
from app.storage.paths import StoragePaths
from app.storage.runtime_roots import RuntimeRoots


PLUGIN = "test.offline"


def _files(root):
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob("*") if p.is_file()}


def _transaction(code):
    return next(code.parent.glob(".update-*/transaction.json")).parent


@pytest.fixture
def installation(tmp_path, monkeypatch):
    roots = RuntimeRoots(tmp_path / "distribution", tmp_path / "user")
    roots.distribution_root.mkdir()
    (roots.distribution_root / "VERSION").write_text("1.3.2")
    code = roots.user_root / "plugins/user/custom-directory"
    code.mkdir(parents=True)
    manifest = {"api": 4, "id": PLUGIN, "name": "Offline", "version": "0.1.0",
                "entry": "plugin:Plugin", "enabled": True, "provides": [], "requires": []}
    (code / "plugin.yaml").write_text(yaml.safe_dump(manifest))
    (code / "plugin.py").write_text("class Plugin: pass\n")
    (code / "requirements.txt").write_text("# No external requirements in this fixture\n")
    monkeypatch.setattr(updates, "SOURCES", {PLUGIN: {"directory": "test"}})
    payload = roots.distribution_root / updates.PAYLOAD_PATH
    new_code = payload / "plugins/test"
    shutil.copytree(code, new_code)
    manifest.update(version="0.2.0", enabled=False, min_app_version="1.3.2")
    (new_code / "plugin.yaml").write_text(yaml.safe_dump(manifest))
    (new_code / "plugin.py").write_text("class Plugin: upgraded = True\n")
    dependencies = StoragePaths(roots.user_root).plugin_dependency_root_for(PLUGIN)
    dependencies.mkdir(parents=True)
    (dependencies / "old.txt").write_text("old environment")
    new_dependencies = payload / "dependencies" / PLUGIN
    new_dependencies.mkdir(parents=True)
    (new_dependencies / ".sakura-dependencies.json").write_text(json.dumps(
        {"schemaVersion": 1, "python": f"{sys.version_info.major}.{sys.version_info.minor}"}))
    (new_dependencies / "new.txt").write_text("new environment")
    def no_network(*args, **kwargs):
        pytest.fail("offline updates must not download or resolve dependencies")
    monkeypatch.setattr(socket, "create_connection", no_network)
    monkeypatch.setattr(PluginDependencyRoots, "_uv_command", no_network)
    return roots, code, dependencies, new_code


@pytest.mark.parametrize("enabled", [True, False])
def test_updates_only_code_and_dependencies_preserving_user_state(installation, enabled):
    roots, code, dependencies, replacement = installation
    PluginDesiredStateStore(roots.user_root).set(PLUGIN, enabled)
    settings = roots.user_root / "data/plugins" / PLUGIN / "config.json"
    settings.parent.mkdir(parents=True)
    settings.write_text('{"model":"my-model","microphone":"existing-device"}')
    saved_settings = settings.read_bytes()
    desired = (roots.user_root / "config/plugins.yaml").read_bytes()
    before = PluginInventory(roots).scan().records[0]
    assert updates.update_installed_plugins(roots) == {}
    record = PluginInventory(roots).scan().records[0]
    assert (record.version, record.source, record.install_id, record.desired_enabled) == (
        "0.2.0", "user", before.install_id, enabled)
    assert record.can_uninstall
    assert (code / "plugin.py").read_bytes() == (replacement / "plugin.py").read_bytes()
    assert not (dependencies / "old.txt").exists()
    assert (dependencies / "new.txt").read_text() == "new environment"
    assert settings.read_bytes() == saved_settings
    assert (roots.user_root / "config/plugins.yaml").read_bytes() == desired


def test_manifest_default_does_not_change_existing_enabled_choice(installation):
    roots, _, _, _ = installation
    assert updates.update_installed_plugins(roots) == {}
    assert PluginInventory(roots).scan().records[0].desired_enabled


@pytest.mark.parametrize("scenario", ["equal", "newer", "modified", "extra", "git"])
def test_overwrites_every_installed_official_copy(installation, scenario):
    roots, code, dependencies, replacement = installation
    if scenario in {"equal", "newer"}:
        manifest = yaml.safe_load((code / "plugin.yaml").read_text())
        manifest["version"] = "0.2.0" if scenario == "equal" else "9.0.0"
        (code / "plugin.yaml").write_text(yaml.safe_dump(manifest))
    elif scenario == "modified":
        (code / "plugin.py").write_text("class Plugin: custom = True\n")
    elif scenario == "extra":
        (code / ".gitignore").write_text("cache/")
    else:
        (code / ".git").mkdir()
    assert updates.update_installed_plugins(roots) == {}
    assert _files(code) == _files(replacement)
    assert not (code / ".git").exists()
    assert (dependencies / "new.txt").exists()


def test_copy_failure_leaves_old_code_and_dependencies_usable(installation, monkeypatch):
    roots, code, dependencies, _ = installation
    before = _files(code)
    copy = shutil.copytree
    def fail_dependencies(source, target, **kwargs):
        if str(target).endswith("new-dependencies"):
            raise PermissionError("dependency copy denied")
        return copy(source, target, **kwargs)
    monkeypatch.setattr(updates.shutil, "copytree", fail_dependencies)
    failures = updates.update_installed_plugins(roots)
    assert "dependency copy denied" in str(failures[PLUGIN]["diagnostics"])
    assert _files(code) == before
    assert (dependencies / "old.txt").exists()
    assert PluginInventory(roots, migration_failures=failures).scan().records[0].runtime_eligible


def test_publish_failure_restores_both_old_directories(installation, monkeypatch):
    roots, code, dependencies, _ = installation
    before = _files(code)
    replace = os.replace
    def fail_new_code(source, target):
        if str(source).endswith("new-code"):
            raise OSError("code publication denied")
        return replace(source, target)
    monkeypatch.setattr(updates.os, "replace", fail_new_code)
    failures = updates.update_installed_plugins(roots)
    assert "code publication denied" in str(failures[PLUGIN]["diagnostics"])
    assert _files(code) == before
    assert (dependencies / "old.txt").read_text() == "old environment"
    assert not (dependencies / "new.txt").exists()


def test_interrupted_publication_recovers_before_plugin_discovery(installation, monkeypatch):
    roots, code, dependencies, _ = installation
    before = _files(code)
    replace = os.replace
    def crash_before_code(source, target):
        if str(source).endswith("new-code"):
            raise SystemExit("power loss")
        return replace(source, target)
    with monkeypatch.context() as crash:
        crash.setattr(updates.os, "replace", crash_before_code)
        with pytest.raises(SystemExit):
            updates.update_installed_plugins(roots)
    assert not code.exists()
    # The payload is now unavailable: recovery still restores both directories,
    # and the failed update must not erase the user's previous installation.
    shutil.rmtree(roots.distribution_root / updates.PAYLOAD_PATH)
    failures = updates.update_installed_plugins(roots)
    assert failures == {}
    assert _files(code) == before
    assert (dependencies / "old.txt").read_text() == "old environment"


def test_offline_replacement_does_not_import_code_or_reinstall_uninstalled_plugin(installation, monkeypatch):
    roots, code, _, _ = installation
    assert updates.update_installed_plugins(roots) == {}
    def no_import(*args, **kwargs):
        pytest.fail("offline updates copy the tested payload without running plugin code")
    monkeypatch.setattr(PluginDependencyRoots, "_validate_entry", no_import)
    assert updates.update_installed_plugins(roots) == {}
    shutil.rmtree(code)
    assert updates.update_installed_plugins(roots) == {}
    assert not code.exists()


def _interrupt_publication(roots, monkeypatch):
    replace = os.replace
    def crash_before_code(source, target):
        if str(source).endswith("new-code"):
            raise SystemExit("power loss")
        return replace(source, target)
    with monkeypatch.context() as crash:
        crash.setattr(updates.os, "replace", crash_before_code)
        with pytest.raises(SystemExit):
            updates.update_installed_plugins(roots)


def test_missing_update_catalog_does_not_prevent_recovery(installation, monkeypatch):
    roots, code, dependencies, _ = installation
    before = _files(code)
    _interrupt_publication(roots, monkeypatch)
    assert not code.exists()
    shutil.rmtree(roots.distribution_root / updates.PAYLOAD_PATH)
    assert updates.update_installed_plugins(roots) == {}
    assert _files(code) == before
    assert (dependencies / "old.txt").read_text() == "old environment"
    assert not (dependencies / "new.txt").exists()


def test_application_recovers_before_running_any_bundled_migration(installation, monkeypatch):
    from app.plugins import bundled_migrations
    from app.core_host.plugin_runtime_application import PluginRuntimeApplication
    from app.plugin_sdk.sakura_tools import ToolRegistry

    roots, code, dependencies, _ = installation
    before = _files(code)
    _interrupt_publication(roots, monkeypatch)
    shutil.rmtree(roots.distribution_root / updates.PAYLOAD_PATH)
    inspected = []
    def migrate(restored_roots, **kwargs):
        assert restored_roots == roots
        assert _files(code) == before
        assert (dependencies / "old.txt").exists()
        inspected.append(True)
        return {}
    monkeypatch.setattr(bundled_migrations, "migrate_bundled_plugins", migrate)
    app = PluginRuntimeApplication(roots, "recovered-startup", ToolRegistry())
    try:
        assert inspected == [True]
        assert app.inventory().records[0].version == "0.1.0"
    finally:
        app.close()


def test_recovery_rejects_a_journal_that_escapes_the_plugin_directory(installation, monkeypatch):
    roots, code, _, _ = installation
    _interrupt_publication(roots, monkeypatch)
    transaction = _transaction(code)
    journal = transaction / "transaction.json"
    state = json.loads(journal.read_text())
    state["directory"] = "../outside"
    journal.write_text(json.dumps(state))
    outside = code.parent.parent / "outside"
    outside.mkdir()
    (outside / "keep.txt").write_text("original")
    failures = updates.recover_installed_plugins(roots)
    assert failures[PLUGIN]["reasonCode"] == "PLUGIN_UPDATE_RECOVERY_FAILED"
    assert (outside / "keep.txt").read_text() == "original"
    assert (transaction / "old-code/plugin.py").exists()


def test_committed_cleanup_can_resume_after_a_crash(installation, monkeypatch):
    roots, code, dependencies, replacement = installation
    remove = shutil.rmtree
    def crash_in_cleanup(path, *args, **kwargs):
        if str(path).endswith("old-code"):
            raise SystemExit("power loss while removing backup")
        return remove(path, *args, **kwargs)
    with monkeypatch.context() as crash:
        crash.setattr(updates.shutil, "rmtree", crash_in_cleanup)
        with pytest.raises(SystemExit):
            updates.update_installed_plugins(roots)
    transaction = _transaction(code)
    assert (transaction / "transaction.json").is_file()
    updates.recover_installed_plugins(roots)
    assert not transaction.exists()
    assert (code / "plugin.py").read_bytes() == (replacement / "plugin.py").read_bytes()
    assert (dependencies / "new.txt").exists()


def test_locked_committed_backup_does_not_block_startup_or_next_update(installation, monkeypatch):
    roots, code, dependencies, replacement = installation
    remove = shutil.rmtree
    def deny_backup(path, *args, **kwargs):
        if str(path).endswith("old-code"):
            raise PermissionError("locked backup")
        return remove(path, *args, **kwargs)
    monkeypatch.setattr(updates.shutil, "rmtree", deny_backup)
    assert updates.update_installed_plugins(roots) == {}
    assert json.loads((_transaction(code) / "transaction.json").read_text())["committed"]
    assert updates.recover_installed_plugins(roots) == {}
    (replacement / "plugin.py").write_text("class Plugin: upgraded_again = True\n")
    assert updates.update_installed_plugins(roots) == {}
    assert _files(code) == _files(replacement)
    assert (dependencies / "new.txt").exists()


def test_failed_recovery_isolates_plugin_without_aborting_core(installation, monkeypatch):
    from app.plugins import bundled_migrations
    from app.core_host.plugin_runtime_application import PluginRuntimeApplication
    from app.plugin_sdk.sakura_tools import ToolRegistry
    roots, code, dependencies, _ = installation
    _interrupt_publication(roots, monkeypatch)
    replace = os.replace
    def deny_restore(source, target):
        if str(source).endswith("old-code"):
            raise PermissionError("restore denied")
        return replace(source, target)
    monkeypatch.setattr(updates.os, "replace", deny_restore)
    def migrate(roots, **kwargs):
        assert PLUGIN in kwargs["excluded_plugin_ids"]
        return {}
    monkeypatch.setattr(bundled_migrations, "migrate_bundled_plugins", migrate)
    healthy = code.parent / "healthy"
    healthy.mkdir()
    (healthy / "plugin.yaml").write_text("api: 4\nid: test.healthy\nversion: 1.0.0\nentry: plugin:Plugin\n")
    (healthy / "plugin.py").write_text("class Plugin:\n    def setup(self, context): pass\n")
    app = PluginRuntimeApplication(roots, "failed-recovery", ToolRegistry())
    try:
        assert [spec.plugin_id for spec in app.inventory().runtime_specs] == ["test.healthy"]
        assert (_transaction(code) / "old-code/plugin.py").exists()
    finally:
        app.close()


@pytest.mark.parametrize(("directory", "tag"), [
    ("sakura_mem0", "v1.3.1"), ("sakura_gpt_sovits", "v1.3.0"), ("sakura_asr_sensevoice", "v1.2.1"),
])
def test_real_historical_release_sources_are_replaced(tmp_path, monkeypatch, directory, tag):
    import zipfile
    from pathlib import Path
    repo = Path(__file__).resolve().parents[2]
    roots = RuntimeRoots(tmp_path / "distribution", tmp_path / "user")
    code = roots.user_root / "plugins/user" / directory
    with zipfile.ZipFile(repo / "tests/fixtures/plugin_upgrade" / f"{directory}-{tag}.zip") as archive:
        archive.extractall(code)
    manifest = yaml.safe_load((code / "plugin.yaml").read_text())
    plugin_id = manifest["id"]
    monkeypatch.setattr(updates, "SOURCES", {plugin_id: {"directory": directory}})
    replacement = roots.distribution_root / updates.PAYLOAD_PATH / "plugins" / directory
    shutil.copytree(repo / "plugins/optional" / directory, replacement)
    dependency_source = roots.distribution_root / updates.PAYLOAD_PATH / "dependencies" / plugin_id
    dependency_source.mkdir(parents=True)
    (dependency_source / "new.txt").write_text("staged dependencies")
    PluginDesiredStateStore(roots.user_root).set(plugin_id, False)
    private = roots.user_root / "data/plugins" / plugin_id / "saved.txt"
    private.parent.mkdir(parents=True)
    private.write_text("existing model and configuration")
    assert updates.update_installed_plugins(roots) == {}
    assert (code / "plugin.py").read_bytes() == (replacement / "plugin.py").read_bytes()
    assert not PluginInventory(roots).scan().records[0].desired_enabled
    assert private.read_text() == "existing model and configuration"


def test_missing_payload_dependencies_does_not_publish_code_alone(installation):
    roots, code, dependencies, _ = installation
    before = _files(code)
    shutil.rmtree(roots.distribution_root / updates.PAYLOAD_PATH / "dependencies" / PLUGIN)
    failures = updates.update_installed_plugins(roots)
    assert failures[PLUGIN]["reasonCode"] == "PLUGIN_UPDATE_FAILED"
    assert _files(code) == before
    assert (dependencies / "old.txt").exists()


def test_rollback_cleanup_journal_cannot_erase_a_later_update(installation, monkeypatch):
    roots, code, dependencies, replacement = installation
    shutil.rmtree(dependencies)
    replace = os.replace
    remove = shutil.rmtree
    def fail_publication(source, target):
        if str(source).endswith("new-code"):
            raise PermissionError("code publication denied")
        return replace(source, target)
    def fail_cleanup(path, *args, **kwargs):
        if str(path).endswith("new-code"):
            raise PermissionError("staging cleanup denied")
        return remove(path, *args, **kwargs)
    with monkeypatch.context() as failing:
        failing.setattr(updates.os, "replace", fail_publication)
        failing.setattr(updates.shutil, "rmtree", fail_cleanup)
        assert updates.update_installed_plugins(roots)[PLUGIN]["reasonCode"] == "PLUGIN_UPDATE_FAILED"
    transaction = _transaction(code)
    assert json.loads((transaction / "transaction.json").read_text())["committed"]
    # Publish again before the leftover journal gets a chance to clean up.
    assert updates.update_installed_plugins(roots, recovery_failures={}) == {}
    assert updates.recover_installed_plugins(roots) == {}
    assert _files(code) == _files(replacement)
    assert (dependencies / "new.txt").exists()
