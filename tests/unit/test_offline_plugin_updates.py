from __future__ import annotations

import json
import os
import shutil
import socket
import sys
import zipfile

import pytest
import yaml

from app.plugins import offline_updates as updates
from app.plugins.dependencies import PluginDependencyRoots
from app.plugins.inventory import PluginDesiredStateStore, PluginInventory
from app.storage.paths import StoragePaths
from app.storage.runtime_roots import RuntimeRoots


PLUGIN = "test.offline"


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
    baseline = roots.distribution_root / updates.BASELINES
    baseline.mkdir(parents=True)
    with zipfile.ZipFile(baseline / "original.zip", "w") as archive:
        for path in code.iterdir():
            archive.write(path, path.name)
    source = {"directory": "test", "baselines": [{"file": "original.zip"}],
              "minimumCompatibleVersion": "0.2.0"}
    (baseline / "sources.json").write_text(json.dumps({PLUGIN: source}))
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


@pytest.mark.parametrize("scenario", ["uninstalled", "equal", "newer", "modified", "extra", "git"])
def test_skips_missing_newer_and_custom_installations(installation, scenario):
    roots, code, dependencies, _ = installation
    if scenario == "uninstalled":
        shutil.rmtree(code)
    elif scenario in {"equal", "newer"}:
        manifest = yaml.safe_load((code / "plugin.yaml").read_text())
        manifest["version"] = "0.2.0" if scenario == "equal" else "1.0.0"
        (code / "plugin.yaml").write_text(yaml.safe_dump(manifest))
    elif scenario == "modified":
        (code / "plugin.py").write_text("class Plugin: custom = True\n")
    elif scenario == "extra":
        (code / "notes.txt").write_text("keep local notes")
    else:
        (code / ".git").mkdir()
    before = updates._files(code) if code.exists() else None
    failures = updates.update_installed_plugins(roots)
    assert (updates._files(code) if code.exists() else None) == before
    assert (dependencies / "old.txt").exists()
    assert bool(failures) == (scenario in {"modified", "extra", "git"})
    if failures:
        record = PluginInventory(roots, migration_failures=failures).scan().records[0]
        assert record.reason_code == "PLUGIN_UPDATE_REQUIRED"
        assert not record.runtime_eligible
        assert record.desired_enabled
        assert record.can_uninstall


def test_dependency_or_entry_failure_leaves_old_plugin_usable(installation):
    roots, code, dependencies, replacement = installation
    (replacement / "plugin.py").write_text("raise RuntimeError('broken new entry')\n")
    before = updates._files(code)
    failures = updates.update_installed_plugins(roots)
    assert "broken new entry" in str(failures[PLUGIN]["diagnostics"])
    assert updates._files(code) == before
    assert (dependencies / "old.txt").exists()
    (replacement / "plugin.py").write_text("class Plugin: repaired = True\n")
    assert updates.update_installed_plugins(roots) == {}
    assert yaml.safe_load((code / "plugin.yaml").read_text())["version"] == "0.2.0"


def test_publish_failure_restores_both_old_directories(installation, monkeypatch):
    roots, code, dependencies, _ = installation
    before = updates._files(code)
    replace = os.replace
    def fail_new_code(source, target):
        if str(source).endswith("new-code"):
            raise OSError("code publication denied")
        return replace(source, target)
    monkeypatch.setattr(updates.os, "replace", fail_new_code)
    failures = updates.update_installed_plugins(roots)
    assert "code publication denied" in str(failures[PLUGIN]["diagnostics"])
    assert updates._files(code) == before
    assert (dependencies / "old.txt").read_text() == "old environment"
    assert not (dependencies / "new.txt").exists()


def test_interrupted_publication_recovers_before_plugin_discovery(installation, monkeypatch):
    roots, code, dependencies, _ = installation
    before = updates._files(code)
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
    assert PLUGIN in failures
    assert updates._files(code) == before
    assert (dependencies / "old.txt").read_text() == "old environment"


def test_completed_update_and_later_uninstall_are_not_repeated(installation, monkeypatch):
    roots, code, _, _ = installation
    assert updates.update_installed_plugins(roots) == {}
    def no_import(*args, **kwargs):
        pytest.fail("same version must not prepare or import again")
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
    before = updates._files(code)
    _interrupt_publication(roots, monkeypatch)
    assert not code.exists()
    shutil.rmtree(roots.distribution_root / updates.BASELINES)
    shutil.rmtree(roots.distribution_root / updates.PAYLOAD_PATH)
    assert updates.update_installed_plugins(roots) == {}
    assert updates._files(code) == before
    assert (dependencies / "old.txt").read_text() == "old environment"
    assert not (dependencies / "new.txt").exists()


def test_application_recovers_before_running_any_bundled_migration(installation, monkeypatch):
    from app.plugins import bundled_migrations
    from app.core_host.plugin_runtime_application import PluginRuntimeApplication
    from app.plugin_sdk.sakura_tools import ToolRegistry

    roots, code, dependencies, _ = installation
    before = updates._files(code)
    _interrupt_publication(roots, monkeypatch)
    shutil.rmtree(roots.distribution_root / updates.BASELINES)
    inspected = []
    def migrate(restored_roots, **kwargs):
        assert restored_roots == roots
        assert updates._files(code) == before
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
    transaction = code.parent / (".update-" + PLUGIN)
    journal = transaction / "transaction.json"
    state = json.loads(journal.read_text())
    state["directory"] = "../outside"
    journal.write_text(json.dumps(state))
    outside = code.parent.parent / "outside"
    outside.mkdir()
    (outside / "keep.txt").write_text("original")
    with pytest.raises(RuntimeError, match="PLUGIN_UPDATE_RECOVERY_FAILED"):
        updates.recover_installed_plugins(roots)
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
    transaction = code.parent / (".update-" + PLUGIN)
    assert (transaction / "transaction.json").is_file()
    shutil.rmtree(roots.distribution_root / updates.BASELINES)
    updates.recover_installed_plugins(roots)
    assert not transaction.exists()
    assert (code / "plugin.py").read_bytes() == (replacement / "plugin.py").read_bytes()
    assert (dependencies / "new.txt").exists()
