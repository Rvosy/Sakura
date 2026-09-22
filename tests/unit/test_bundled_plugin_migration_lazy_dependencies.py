from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import pytest

from app.plugins import bundled_migrations as migration
from app.plugins.dependencies import PluginDependencyRoots
from app.plugins.inventory import PluginDesiredStateStore
from app.storage.paths import StoragePaths
from app.storage.runtime_roots import RuntimeRoots


PLUGIN = "sakura.memory.mem0"
ENTRY = "plugin:SakuraMem0Plugin"
SOURCE = Path(__file__).resolve().parents[2] / "plugins/optional/sakura_mem0"


@pytest.mark.parametrize("payload_available", [False, True])
def test_mem0_partial_lazy_dependencies_are_repaired_or_left_pending(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, payload_available: bool,
):
    monkeypatch.setattr(migration, "MIGRATIONS", {PLUGIN: "sakura_mem0"})

    def no_install(*args, **kwargs):
        pytest.fail("Migration must use the offline dependency payload")

    monkeypatch.setattr(PluginDependencyRoots, "install", no_install)
    roots = RuntimeRoots(tmp_path / "distribution", tmp_path / "user")
    paths = StoragePaths(roots.user_root)
    code = paths.user_plugins_dir / PLUGIN
    shutil.copytree(SOURCE, code)
    dependency = paths.plugin_dependency_root_for(PLUGIN)
    dependency.mkdir(parents=True)
    marker = json.dumps({
        "schemaVersion": 1, "kind": "requirements.txt",
        "python": f"{sys.version_info.major}.{sys.version_info.minor}",
    })
    (dependency / ".sakura-dependencies.json").write_text(marker)
    (dependency / "retained_package.py").write_text("# a partial environment is not empty\n")
    PluginDesiredStateStore(roots.user_root).set(PLUGIN, False)
    state_path = roots.user_root / "config" / migration.STATE_NAME
    state_path.write_text(json.dumps({PLUGIN: "completed"}))
    private_data = paths.plugin_data_for(PLUGIN) / "config.json"
    private_data.parent.mkdir(parents=True)
    private_data.write_text('{"keep": true}')

    # The real Mem0 entry loads successfully without its delayed dependencies.
    # This is why checking the marker and entry alone used to mark it complete.
    dependencies = PluginDependencyRoots(roots.user_root)
    dependencies._validate_entry(PLUGIN, code, dependency, ENTRY)
    if payload_available:
        payload = roots.distribution_root / migration.PAYLOAD_PATH
        shutil.copytree(SOURCE, payload / "plugins/sakura_mem0")
        payload_dependencies = payload / "dependencies" / PLUGIN
        payload_dependencies.mkdir(parents=True)
        (payload_dependencies / ".sakura-dependencies.json").write_text(marker)
        # Small importable modules isolate the migration/runner contract from
        # package downloads. Release smoke uses the actual resolved packages.
        for module in migration.SOURCES[PLUGIN]["runtimeImports"]:
            (payload_dependencies / f"{module}.py").write_text("# offline dependency fixture\n")

    failures = migration.migrate_bundled_plugins(roots)
    state = json.loads(state_path.read_text())
    if payload_available:
        assert failures == {}
        assert state[PLUGIN] == "completed"
        dependencies._validate_entry(
            PLUGIN, code, dependency, ENTRY,
            runtime_imports=migration.SOURCES[PLUGIN]["runtimeImports"],
        )
        backups = roots.user_root / "plugins/migration-backups"
        assert list(backups.glob(f"*/dependencies/{PLUGIN}/retained_package.py"))
    else:
        assert failures == {PLUGIN: "PLUGIN_MIGRATION_FAILED"}
        assert state[PLUGIN] == "repairing"
        assert (dependency / "retained_package.py").exists()
    assert private_data.read_text() == '{"keep": true}'
    assert PluginDesiredStateStore(roots.user_root).read()[PLUGIN] is False


@pytest.mark.parametrize("payload_available", [False, True])
def test_spine_missing_renderer_is_repaired_or_left_pending(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, payload_available: bool,
):
    plugin_id = "sakura.visual.spine"
    source = SOURCE.parent / "sakura_spine"
    monkeypatch.setattr(migration, "MIGRATIONS", {plugin_id: "sakura_spine"})
    roots = RuntimeRoots(tmp_path / "distribution", tmp_path / "user")
    code = StoragePaths(roots.user_root).user_plugins_dir / plugin_id
    shutil.copytree(source, code)
    (code / "renderer.mjs").unlink()
    PluginDesiredStateStore(roots.user_root).set(plugin_id, False)
    state_path = roots.user_root / "config" / migration.STATE_NAME
    state_path.write_text(json.dumps({plugin_id: "completed"}))
    if payload_available:
        shutil.copytree(source, roots.distribution_root / migration.PAYLOAD_PATH / "plugins/sakura_spine")

    failures = migration.migrate_bundled_plugins(roots)
    state = json.loads(state_path.read_text())
    if payload_available:
        assert failures == {}
        assert state[plugin_id] == "completed"
        assert (code / "renderer.mjs").read_bytes() == (source / "renderer.mjs").read_bytes()
    else:
        assert failures == {plugin_id: "PLUGIN_MIGRATION_FAILED"}
        assert state[plugin_id] == "repairing"
        assert not (code / "renderer.mjs").exists()
    assert PluginDesiredStateStore(roots.user_root).read()[plugin_id] is False
