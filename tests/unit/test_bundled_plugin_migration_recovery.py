from __future__ import annotations

import json
import socket
import subprocess
import sys
from pathlib import Path

import pytest

from app.core.runtime_log import diagnostic_attributes
from app.plugins import bundled_migrations as migration
from app.plugins.dependencies import PluginDependencyRoots
from app.plugins.inventory import PluginDesiredStateStore
from app.storage.paths import StoragePaths
from app.storage.runtime_roots import RuntimeRoots


PLUGIN = "sakura_mobile"
ENTRY = "plugin:Plugin"
OLD_CODE = "import missing_migration_helper\nclass Plugin: pass\n"
NEW_CODE = "import migration_probe\nclass Plugin: pass\n"


@pytest.fixture
def repairing_plugin(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(migration, "MIGRATIONS", {PLUGIN: PLUGIN})

    def forbidden(*args, **kwargs):
        pytest.fail("Recovery must not install dependencies or access the network")

    monkeypatch.setattr(PluginDependencyRoots, "install", forbidden)
    monkeypatch.setattr(PluginDependencyRoots, "_uv_command", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    roots = RuntimeRoots(tmp_path / "distribution", tmp_path / "user")
    paths = StoragePaths(roots.user_root)
    code = paths.user_plugins_dir / PLUGIN
    dependencies = paths.plugin_dependency_root_for(PLUGIN)
    payload = roots.distribution_root / migration.PAYLOAD_PATH
    payload_code = payload / "plugins" / PLUGIN
    payload_dependencies = payload / "dependencies" / PLUGIN
    for directory in (code, dependencies, payload_code, payload_dependencies):
        directory.mkdir(parents=True)
    manifest = f"api: 4\nid: {PLUGIN}\nversion: {migration.SOURCES[PLUGIN]['version']}\nentry: {ENTRY}\n"
    for directory in (code, payload_code):
        (directory / "plugin.yaml").write_text(manifest, encoding="utf-8")
        (directory / "requirements.txt").write_text("migration-probe==1.0\n", encoding="utf-8")
    (code / "plugin.py").write_text(OLD_CODE, encoding="utf-8")
    (payload_code / "plugin.py").write_text(NEW_CODE, encoding="utf-8")
    (dependencies / "original.txt").write_text("preserve original dependencies", encoding="utf-8")
    (payload_dependencies / "migration_probe.py").write_text("value = 1\n", encoding="utf-8")
    for directory, python in (
        (dependencies, "obsolete"),
        (payload_dependencies, f"{sys.version_info.major}.{sys.version_info.minor}"),
    ):
        (directory / ".sakura-dependencies.json").write_text(json.dumps({
            "schemaVersion": 1, "kind": "requirements.txt", "python": python,
        }), encoding="utf-8")
    PluginDesiredStateStore(roots.user_root).set(PLUGIN, False)
    state = roots.user_root / "config" / migration.STATE_NAME
    state.write_text(json.dumps({PLUGIN: "completed"}), encoding="utf-8")
    private_data = paths.plugin_data_for(PLUGIN) / "settings.json"
    private_data.parent.mkdir(parents=True)
    private_data.write_text('{"keep": true}', encoding="utf-8")
    return roots, code, dependencies, state, private_data


_EXIT_AT_PUBLICATION = """
import os
import socket
import sys
from pathlib import Path
from app.plugins import bundled_migrations as migration
from app.plugins.dependencies import PluginDependencyRoots
from app.storage.paths import StoragePaths
from app.storage.runtime_roots import RuntimeRoots

roots = RuntimeRoots(Path(sys.argv[1]), Path(sys.argv[2]))
point = sys.argv[3]
plugin = 'sakura_mobile'
migration.MIGRATIONS = {plugin: plugin}
paths = StoragePaths(roots.user_root)
code = paths.user_plugins_dir / plugin
dependencies = paths.plugin_dependency_root_for(plugin)
def forbidden(*args, **kwargs):
    raise AssertionError('Recovery attempted a network dependency installation')
PluginDependencyRoots.install = forbidden
PluginDependencyRoots._uv_command = forbidden
socket.create_connection = forbidden
replace = os.replace
def interrupt(source, destination):
    source, destination = Path(source), Path(destination)
    replace(source, destination)
    reached = (
        (point == 'dependencies' and source.name == 'dependencies' and destination == dependencies)
        or (point == 'code_backup' and source == code)
        or (point == 'code' and source.name == 'code' and destination == code)
    )
    if reached:
        os._exit(73)
os.replace = interrupt
migration.migrate_bundled_plugins(roots)
raise AssertionError('The requested interruption point was not reached')
"""


def _assert_recovered(roots, code, dependencies, state, private_data):
    assert code.joinpath("plugin.py").read_text() == NEW_CODE
    PluginDependencyRoots(roots.user_root)._validate_entry(PLUGIN, code, dependencies, ENTRY)
    assert json.loads(state.read_text()) == {
        PLUGIN: "completed", migration.REVALIDATED_KEY: "completed",
    }
    assert PluginDesiredStateStore(roots.user_root).read()[PLUGIN] is False
    assert private_data.read_text() == '{"keep": true}'


@pytest.mark.parametrize("point", ["dependencies", "code_backup", "code"])
def test_process_exit_between_publication_steps_resumes_complete_plugin(repairing_plugin, point):
    roots, code, dependencies, state, private_data = repairing_plugin
    result = subprocess.run(
        [sys.executable, "-c", _EXIT_AT_PUBLICATION,
         str(roots.distribution_root), str(roots.user_root), point],
        cwd=Path(__file__).resolve().parents[2],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 73, result.stderr
    assert json.loads(state.read_text()) == {PLUGIN: "repairing"}
    assert migration.migrate_bundled_plugins(roots) == {}
    _assert_recovered(roots, code, dependencies, state, private_data)
    backups = roots.user_root / "plugins/migration-backups"
    assert any(path.read_text() == OLD_CODE for path in backups.glob(f"*/{PLUGIN}/plugin.py"))
    assert any(path.read_text() == "preserve original dependencies"
               for path in backups.glob(f"*/dependencies/{PLUGIN}/original.txt"))


@pytest.mark.parametrize("point", ["dependencies", "code"])
def test_publication_error_restores_original_code_dependencies_and_settings(
    repairing_plugin, monkeypatch: pytest.MonkeyPatch, point: str,
):
    roots, code, dependencies, state, private_data = repairing_plugin
    old_marker = (dependencies / ".sakura-dependencies.json").read_bytes()
    replace = migration.os.replace

    def fail(source, destination):
        target = dependencies if point == "dependencies" else code
        source_name = "dependencies" if point == "dependencies" else "code"
        if Path(source).name == source_name and Path(destination) == target:
            raise OSError("publication failure")
        return replace(source, destination)

    with monkeypatch.context() as failure:
        failure.setattr(migration.os, "replace", fail)
        assert migration.migrate_bundled_plugins(roots) == {PLUGIN: "PLUGIN_MIGRATION_FAILED"}
    assert code.joinpath("plugin.py").read_text() == OLD_CODE
    assert dependencies.joinpath("original.txt").read_text() == "preserve original dependencies"
    assert (dependencies / ".sakura-dependencies.json").read_bytes() == old_marker
    assert not (dependencies / "migration_probe.py").exists()
    assert json.loads(state.read_text()) == {
        PLUGIN: "repairing", migration.REVALIDATED_KEY: "completed",
    }
    assert PluginDesiredStateStore(roots.user_root).read()[PLUGIN] is False
    assert private_data.read_text() == '{"keep": true}'
    assert migration.migrate_bundled_plugins(roots) == {}
    _assert_recovered(roots, code, dependencies, state, private_data)


def test_rollback_failure_keeps_both_errors_and_recoverable_backups(
    repairing_plugin, monkeypatch: pytest.MonkeyPatch,
):
    roots, code, dependencies, state, private_data = repairing_plugin
    replace = migration.os.replace
    rmtree = migration.shutil.rmtree
    errors = []

    def fail_publish(source, destination):
        if Path(source).name == "code" and Path(destination) == code:
            raise OSError("original code publication failure")
        return replace(source, destination)

    def fail_rollback(path, *args, **kwargs):
        if Path(path) == dependencies:
            raise OSError("dependency rollback failure")
        return rmtree(path, *args, **kwargs)

    def capture_error(error, reason, plugin_id=None, details=None):
        errors.append(diagnostic_attributes(error, reason_code=reason, stage="migration"))

    with monkeypatch.context() as failure:
        failure.setattr(migration.os, "replace", fail_publish)
        failure.setattr(migration.shutil, "rmtree", fail_rollback)
        failure.setattr(migration, "_failure", capture_error)
        assert migration.migrate_bundled_plugins(roots) == {PLUGIN: "PLUGIN_MIGRATION_FAILED"}
    details = json.dumps(errors)
    assert "original code publication failure" in details
    assert "dependency rollback failure" in details
    backups = roots.user_root / "plugins/migration-backups"
    # A failed dependency rollback does not prevent restoring the original code.
    assert (code / "plugin.py").read_text() == OLD_CODE
    assert any(path.read_text() == "preserve original dependencies"
               for path in backups.glob(f"*/dependencies/{PLUGIN}/original.txt"))
    assert migration.migrate_bundled_plugins(roots) == {}
    _assert_recovered(roots, code, dependencies, state, private_data)
