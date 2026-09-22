"""Enabled updates exercise real Runner processes and file rollback in isolated roots."""
import json
import shutil
import sys
import zipfile
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import psutil
import pytest

from app.core_host.plugin_application import PluginApplicationHost
from app.core_host.plugin_settings import PluginSettingsBoundary, PluginSettingsError
from app.plugin_sdk.sakura_tools import ToolRegistry
from app.plugins.installer import LocalPluginInstaller
from app.plugins.inventory import PluginDesiredStateStore
from app.plugins.dependencies import PluginDependencyRoots
from app.storage.paths import StoragePaths
from app.storage.runtime_roots import RuntimeRoots


def package(path, plugin_id, version="1.0.0", *, requires=(), fail=False, reject_new_dependency=False, lazy_dependency=False):
    dependency = f'self.provider = context.get("{requires[0]}")' if requires else ""
    setup = 'raise RuntimeError("new setup failed")' if fail else dependency
    if reject_new_dependency:
        setup += '\n        assert self.provider.ping() == "1.0.0", "incompatible provider"'
    ping = "from fixture_dependency import VALUE\n        return VALUE" if lazy_dependency else f'return {"self.provider.ping()" if requires else repr(version)}'
    body = f'''import os
class Plugin:
    def setup(self, context):
        {setup or 'pass'}
        context.provide("{plugin_id}", self, exports=("ping", "pid"))
    def ping(self):
        {ping}
    def pid(self):
        return os.getpid()
'''
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("plugin.yaml", f"api: 4\nid: {plugin_id}\nname: Fixture\nversion: {version}\nentry: plugin:Plugin\nprovides: [{plugin_id}]\nrequires: [{', '.join(requires)}]\n")
        archive.writestr("plugin.py", body)
    return path


@pytest.mark.parametrize("outcome", ["success", "setup_failure", "dependent_failure", "wrong_version", "move_failure", "busy"])
def test_enabled_update_restores_processes_and_keeps_data(tmp_path, monkeypatch, outcome):
    user = tmp_path / "user"
    roots = RuntimeRoots(tmp_path / "distribution", user)
    (roots.distribution_root / "plugins/builtin").mkdir(parents=True)
    (user / "config").mkdir(parents=True)
    shutil.copy2(Path(__file__).parents[2] / "desktop/src-tauri/src/new_user_plugin_migrations.json", user / "config/plugin-migrations.json")
    installer = LocalPluginInstaller(roots)
    for plugin_id, requires, enabled in [
        ("fixture.provider", (), True),
        ("fixture.consumer", ("fixture.provider",), True),
        ("fixture.unrelated", (), True),
        ("fixture.disabled", ("fixture.provider",), False),
    ]:
        installer.install(package(tmp_path / f"{plugin_id}.zip", plugin_id, requires=requires,
                                  reject_new_dependency=plugin_id == "fixture.consumer" and outcome == "dependent_failure"), "zip", initial_enabled=enabled)
    config = StoragePaths(user).plugins_config()
    before_config = config.read_bytes()
    data = user / "data/plugins/fixture.provider/notes.txt"
    data.parent.mkdir(parents=True)
    data.write_text("keep my data", encoding="utf-8")
    application = PluginApplicationHost(roots, "update-generation", ToolRegistry())
    boundary = PluginSettingsBoundary("update-generation", "credential", roots, application_provider=lambda: application)
    try:
        application.start()
        old_pid = application.call_service("fixture.provider", "pid")
        consumer_pid = application.call_service("fixture.consumer", "pid")
        unrelated_pid = application.call_service("fixture.unrelated", "pid")
        if outcome == "busy":
            from app.core_host.real_chat import RealChatRejection

            @contextmanager
            def busy():
                raise RealChatRejection("RUNTIME_UPDATE_BUSY", "busy")
                yield

            application._chat_boundary = SimpleNamespace(idle_runtime_update=busy)
        original_begin = LocalPluginInstaller.begin_uninstall

        def begin(stager, identity, **kwargs):
            # Windows must release loaded modules before the installer moves their directories.
            assert not psutil.pid_exists(old_pid)
            assert not psutil.pid_exists(consumer_pid)
            if outcome == "move_failure":
                raise OSError("cannot move old code")
            return original_begin(stager, identity, **kwargs)

        monkeypatch.setattr(LocalPluginInstaller, "begin_uninstall", begin)
        new = package(tmp_path / "new.zip", "fixture.provider", "1.1.0", fail=outcome == "setup_failure")
        request = {"revision": boundary.snapshot()["revision"], "sourcePath": str(new), "pluginId": "fixture.provider", "version": "2.0.0" if outcome == "wrong_version" else "1.1.0"}
        if outcome == "success":
            result = boundary.marketplace_install(request)
            assert result["managementAction"] == "updated"
        else:
            with pytest.raises(PluginSettingsError) as caught:
                boundary.marketplace_install(request)
            assert caught.value.recovery_error is None
            if outcome == "busy":
                assert caught.value.retryable
                assert application.call_service("fixture.provider", "pid") == old_pid
        expected = "1.1.0" if outcome == "success" else "1.0.0"
        assert application.call_service("fixture.provider", "ping") == expected
        assert application.call_service("fixture.consumer", "ping") == expected
        assert application.call_service("fixture.unrelated", "pid") == unrelated_pid
        snapshot = {p["pluginId"]: p for p in boundary.snapshot()["plugins"]}
        assert snapshot["fixture.provider"]["enabled"] is True
        assert snapshot["fixture.disabled"]["state"] == "disabled"
        assert snapshot["fixture.disabled"]["enabled"] is False
        assert config.read_bytes() == before_config
        assert data.read_text(encoding="utf-8") == "keep my data"
    finally:
        application._chat_boundary = None
        application.close()


@pytest.mark.parametrize("enabled", [True, False])
@pytest.mark.parametrize("damage", ["setup_failure", "missing_manifest"])
def test_market_reinstalls_same_version_and_keeps_existing_user_state(tmp_path, monkeypatch, enabled, damage):
    plugin_id = "sakura_mobile"
    roots = RuntimeRoots(tmp_path / "distribution", tmp_path / "user")
    paths = StoragePaths(roots.user_root)
    original = LocalPluginInstaller(roots).install(package(tmp_path / "old.zip", plugin_id), "zip", initial_enabled=enabled)
    if damage == "setup_failure":
        (original.code_dir / "plugin.py").write_text('class Plugin:\n    def setup(self, context):\n        raise RuntimeError("old installation broken")\n')
    else:
        (original.code_dir / "plugin.yaml").unlink()
    monkeypatch.setattr("app.plugins.bundled_migrations.migrate_bundled_plugins", lambda *args, **kwargs: {
        plugin_id: "PLUGIN_MIGRATION_FAILED",
    })
    config = paths.plugins_config().read_bytes()
    private = paths.plugin_data_for(plugin_id) / "settings.json"
    private.parent.mkdir(parents=True)
    private.write_text('{"keep": true}')
    application = PluginApplicationHost(roots, "repair-generation", ToolRegistry())
    boundary = PluginSettingsBoundary("repair-generation", "credential", roots, application_provider=lambda: application)
    try:
        application.start()
        result = boundary.marketplace_install({"revision": boundary.snapshot()["revision"],
            "sourcePath": str(package(tmp_path / "fixed.zip", plugin_id)), "pluginId": plugin_id, "version": "1.0.0"})
        current = next(p for p in result["plugins"] if p["pluginId"] == plugin_id)
        assert current["state"] == ("active" if enabled else "disabled")
        assert current["enabled"] is enabled
        assert current["source"] == "user"
        if enabled:
            assert application.call_service(plugin_id, "ping") == "1.0.0"
        assert paths.plugins_config().read_bytes() == config
        assert private.read_text() == '{"keep": true}'
        assert len(result["plugins"]) == 1
    finally:
        application.close()


@pytest.mark.parametrize("damage", ["setup_failure", "missing_manifest"])
def test_failed_market_repair_preserves_broken_copy_and_can_be_retried(tmp_path, monkeypatch, damage):
    plugin_id = "fixture.provider"
    roots = RuntimeRoots(tmp_path / "distribution", tmp_path / "user")
    paths = StoragePaths(roots.user_root)
    original = LocalPluginInstaller(roots).install(package(tmp_path / "old.zip", plugin_id, fail=True), "zip", initial_enabled=True)
    if damage == "missing_manifest":
        (original.code_dir / "plugin.yaml").unlink()
    monkeypatch.setattr("app.plugins.bundled_migrations.migrate_bundled_plugins", lambda *args, **kwargs: {})
    before = {p.name: p.read_bytes() for p in original.code_dir.iterdir() if p.is_file()}
    config = paths.plugins_config().read_bytes()
    application = PluginApplicationHost(roots, "repair-generation", ToolRegistry())
    boundary = PluginSettingsBoundary("repair-generation", "credential", roots, application_provider=lambda: application)
    try:
        application.start()
        request = {"revision": boundary.snapshot()["revision"], "sourcePath": str(package(tmp_path / "bad.zip", plugin_id, fail=True)),
                   "pluginId": plugin_id, "version": "1.0.0"}
        with pytest.raises(PluginSettingsError) as caught:
            boundary.marketplace_install(request)
        assert caught.value.code == "PLUGIN_UPDATE_START_FAILED"
        assert caught.value.recovery_error is None
        assert {p.name: p.read_bytes() for p in original.code_dir.iterdir() if p.is_file()} == before
        assert paths.plugins_config().read_bytes() == config
        request.update(revision=boundary.snapshot()["revision"], sourcePath=str(package(tmp_path / "good.zip", plugin_id)))
        boundary.marketplace_install(request)
        assert application.call_service(plugin_id, "ping") == "1.0.0"
    finally:
        application.close()


@pytest.mark.parametrize("enabled", [True, False])
@pytest.mark.parametrize("valid_marker", [True, False])
def test_market_restores_missing_migration_with_orphan_dependencies(tmp_path, monkeypatch, enabled, valid_marker):
    plugin_id = "sakura_mobile"
    roots = RuntimeRoots(tmp_path / "distribution", tmp_path / "user")
    paths = StoragePaths(roots.user_root)
    PluginDesiredStateStore(roots.user_root).set(plugin_id, enabled)
    dependency_root = paths.plugin_dependency_root_for(plugin_id)
    dependency_root.mkdir(parents=True)
    (dependency_root / "partial.txt").write_text("incomplete download")
    if valid_marker:
        (dependency_root / ".sakura-dependencies.json").write_text(json.dumps({
            "schemaVersion": 1, "kind": "requirements.txt", "python": f"{sys.version_info.major}.{sys.version_info.minor}",
        }))
    uv = tmp_path / "fake_uv.py"
    uv.write_text('import sys\nfrom pathlib import Path\ntarget = Path(sys.argv[sys.argv.index("--target") + 1])\n(target / "fixture_dependency.py").write_text("VALUE = 42\\n")\n')
    monkeypatch.setattr(PluginDependencyRoots, "_uv_command", lambda self: [sys.executable, str(uv)])
    monkeypatch.setattr("app.plugins.bundled_migrations.migrate_bundled_plugins", lambda *args, **kwargs: {
        plugin_id: "PLUGIN_MIGRATION_FAILED",
    })
    archive_path = package(tmp_path / "plugin.zip", plugin_id, lazy_dependency=True)
    with zipfile.ZipFile(archive_path, "a") as archive:
        archive.writestr("requirements.txt", "fixture-dependency==1.0\n")
    application = PluginApplicationHost(roots, "repair-generation", ToolRegistry())
    boundary = PluginSettingsBoundary("repair-generation", "credential", roots, application_provider=lambda: application)
    try:
        application.start()
        result = boundary.marketplace_install({"revision": boundary.snapshot()["revision"], "sourcePath": str(archive_path),
            "pluginId": plugin_id, "version": "1.0.0"})
        current = next(p for p in result["plugins"] if p["pluginId"] == plugin_id)
        assert current["state"] == ("active" if enabled else "disabled")
        assert current["enabled"] is enabled
        assert PluginDesiredStateStore(roots.user_root).read()[plugin_id] is enabled
        if enabled:
            assert application.call_service(plugin_id, "ping") == 42
        assert (dependency_root / "fixture_dependency.py").read_text() == "VALUE = 42\n"
        assert not (dependency_root / "partial.txt").exists()
        assert len(result["plugins"]) == 1
    finally:
        application.close()


def test_missing_migration_failed_install_releases_runtime_record_before_retry(tmp_path, monkeypatch):
    plugin_id = "sakura_mobile"
    roots = RuntimeRoots(tmp_path / "distribution", tmp_path / "user")
    paths = StoragePaths(roots.user_root)
    PluginDesiredStateStore(roots.user_root).set(plugin_id, True)
    monkeypatch.setattr("app.plugins.bundled_migrations.migrate_bundled_plugins", lambda *args, **kwargs: {
        plugin_id: "PLUGIN_MIGRATION_FAILED",
    })
    application = PluginApplicationHost(roots, "repair-generation", ToolRegistry())
    boundary = PluginSettingsBoundary("repair-generation", "credential", roots, application_provider=lambda: application)
    try:
        application.start()
        request = {"revision": boundary.snapshot()["revision"], "sourcePath": str(package(tmp_path / "bad.zip", plugin_id, fail=True)),
                   "pluginId": plugin_id, "version": "1.0.0"}
        with pytest.raises(PluginSettingsError) as caught:
            boundary.marketplace_install(request)
        assert caught.value.code == "PLUGIN_INSTALL_START_FAILED"
        assert caught.value.recovery_error is None
        assert not (paths.user_plugins_dir / plugin_id).exists()
        assert PluginDesiredStateStore(roots.user_root).read()[plugin_id] is True
        request.update(revision=boundary.snapshot()["revision"], sourcePath=str(package(tmp_path / "good.zip", plugin_id)))
        boundary.marketplace_install(request)
        assert application.call_service(plugin_id, "ping") == "1.0.0"
    finally:
        application.close()
