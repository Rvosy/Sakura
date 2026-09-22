"""Enabled updates exercise real Runner processes and file rollback in isolated roots."""
import shutil
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
from app.storage.paths import StoragePaths
from app.storage.runtime_roots import RuntimeRoots


def package(path, plugin_id, version="1.0.0", *, requires=(), fail=False, reject_new_dependency=False):
    dependency = f'self.provider = context.get("{requires[0]}")' if requires else ""
    setup = 'raise RuntimeError("new setup failed")' if fail else dependency
    if reject_new_dependency:
        setup += '\n        assert self.provider.ping() == "1.0.0", "incompatible provider"'
    body = f'''import os
class Plugin:
    def setup(self, context):
        {setup or 'pass'}
        context.provide("{plugin_id}", self, exports=("ping", "pid"))
    def ping(self):
        return {"self.provider.ping()" if requires else repr(version)}
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

        def begin(stager, identity):
            # Windows must release loaded modules before the installer moves their directories.
            assert not psutil.pid_exists(old_pid)
            assert not psutil.pid_exists(consumer_pid)
            if outcome == "move_failure":
                raise OSError("cannot move old code")
            return original_begin(stager, identity)

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
