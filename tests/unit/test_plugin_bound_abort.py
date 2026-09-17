from __future__ import annotations

import threading
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import psutil
import pytest

from app.core_host.plugin_runtime_application import PluginRuntimeApplication
from app.plugins.inventory import PluginInventory
from app.plugins.runtime_v4 import PluginRuntimeError, PluginRuntimeManager, _ServiceBinding
from app.storage.runtime_roots import RuntimeRoots


def _manager(tmp_path: Path, *, dependent=False):
    distribution, user = tmp_path / "distribution", tmp_path / "user"
    user.mkdir()
    plugins = [("fixture.provider", "sakura.assistant", ())]
    if dependent:
        plugins.append(("fixture.consumer", "fixture.consumer.service", ("sakura.assistant",)))
    for plugin_id, service_key, requires in plugins:
        root = distribution / "plugins" / "builtin" / plugin_id
        root.mkdir(parents=True)
        (root / "plugin.yaml").write_text(
            f"api: 4\nid: {plugin_id}\nname: {plugin_id}\nversion: 1.0.0\n"
            f"entry: plugin:Plugin\nprovides: [{service_key}]\nrequires: [{', '.join(requires)}]\n",
            encoding="utf-8",
        )
        (root / "plugin.py").write_text(
            "import os\nclass Service:\n    def pid(self): return os.getpid()\n"
            "class Plugin:\n    def setup(self, context):\n"
            f"        context.provide('{service_key}', Service(), exports=('pid',))\n",
            encoding="utf-8",
        )
    roots = RuntimeRoots(distribution, user)
    return PluginRuntimeManager(roots, "bound-abort", PluginInventory(roots).scan().runtime_specs)


def test_bound_abort_preserves_replacement_and_stops_exact_provider_and_dependents(tmp_path):
    manager = _manager(tmp_path, dependent=True)
    try:
        manager.start()
        old = manager.service_identity("sakura.assistant")
        manager.reload_plugin(old["providerId"])
        current = manager.service_identity("sakura.assistant")
        assert old != current
        provider_pid = manager.call_service("sakura.assistant", "pid")
        consumer_pid = manager.call_service("fixture.consumer.service", "pid")
        assert not manager.abort_bound_service("sakura.assistant", old, reason="ASSISTANT_CALL_UNCERTAIN")
        assert manager.call_service("sakura.assistant", "pid") == provider_pid
        assert manager.call_service("fixture.consumer.service", "pid") == consumer_pid
        assert manager.abort_bound_service("sakura.assistant", current, reason="ASSISTANT_CALL_UNCERTAIN")
        for service in ("sakura.assistant", "fixture.consumer.service"):
            with pytest.raises(PluginRuntimeError, match="SERVICE_MISSING"):
                manager.service_identity(service)
        assert not psutil.pid_exists(provider_pid)
        assert not psutil.pid_exists(consumer_pid)
        reasons = {row["pluginId"]: row["reasonCode"] for row in manager.snapshot()["plugins"]}
        assert reasons == {"fixture.provider": "ASSISTANT_CALL_UNCERTAIN", "fixture.consumer": "DEPENDENCY_FAILED"}
    finally:
        manager.close()


def test_bound_abort_joins_existing_cleanup_before_releasing_readable_artifact(tmp_path):
    manager = _manager(tmp_path)
    entered, finish, complete, joined = (threading.Event() for _ in range(4))
    artifact = tmp_path / "input.json"
    artifact.write_text('{"input":"still needed"}', encoding="utf-8")

    class Process:
        scope_id = "old"

        def close(self, *, deadline):
            entered.set()
            assert finish.wait(5)
            assert artifact.read_text(encoding="utf-8") == '{"input":"still needed"}'
            complete.set()

        def wait_for_cleanup(self):
            joined.set()
            assert complete.wait(5)

    process = Process()
    record = manager._records["fixture.provider"]
    record.process, record.state = process, "active"
    manager._activation_order.append("fixture.provider")
    manager._services["sakura.assistant"] = _ServiceBinding("fixture.provider", frozenset(), process)
    manager.install_host_service("sakura.host.fixture", SimpleNamespace(
        revoke_scope=lambda plugin_id: artifact.unlink(missing_ok=True)), exports=())
    errors = []

    def capture(call):
        try:
            call()
        except BaseException as error:
            errors.append(error)

    closing = threading.Thread(target=lambda: capture(manager.close))
    aborting = threading.Thread(target=lambda: capture(lambda: manager.abort_bound_service(
        "sakura.assistant", {"providerId": "fixture.provider", "scopeId": "old"}, reason="UNCERTAIN")))
    closing.start()
    try:
        assert entered.wait(3)
        aborting.start()
        assert joined.wait(3)
        assert artifact.exists()
    finally:
        finish.set()
        closing.join(5)
        if aborting.ident is not None:
            aborting.join(5)
    assert not closing.is_alive() and not aborting.is_alive()
    assert not errors
    assert not artifact.exists()


def test_bound_abort_cleanup_failure_retains_process_scope_and_artifact(tmp_path):
    manager = _manager(tmp_path)
    failure = PluginRuntimeError("PLUGIN_CLEANUP_FAILED")
    process = SimpleNamespace(scope_id="old", close=Mock(side_effect=failure), wait_for_cleanup=Mock(side_effect=failure))
    manager._records["fixture.provider"].process = process
    revoke = Mock()
    manager.install_host_service("sakura.host.fixture", SimpleNamespace(revoke_scope=revoke), exports=())
    with pytest.raises(PluginRuntimeError, match="PLUGIN_CLEANUP_FAILED"):
        manager.abort_bound_service("sakura.assistant", {"providerId": "fixture.provider", "scopeId": "old"}, reason="UNCERTAIN")
    assert manager._draining_processes["fixture.provider"].process is process
    revoke.assert_not_called()


def _application():
    application = PluginRuntimeApplication.__new__(PluginRuntimeApplication)
    application._manager = Mock()
    application._host_services = Mock()
    application._assistant_input_lock = threading.Lock()
    application._assistant_inputs = {
        "old-input": ({"providerId": "fixture.provider", "scopeId": "old"}, "old-token"),
        "new-input": ({"providerId": "fixture.provider", "scopeId": "new"}, "new-token"),
    }
    return application


@pytest.mark.parametrize("cleanup_fails", [False, True])
def test_application_abort_reclaims_only_confirmed_lifetime_inputs(cleanup_fails):
    application = _application()
    identity = {"providerId": "fixture.provider", "scopeId": "old"}
    if cleanup_fails:
        application._manager.abort_bound_service.side_effect = PluginRuntimeError("PLUGIN_CLEANUP_FAILED")
        with pytest.raises(PluginRuntimeError, match="PLUGIN_CLEANUP_FAILED"):
            application.abort_bound_service("sakura.assistant", identity, reason="UNCERTAIN")
        assert "old-input" in application._assistant_inputs
        application._host_services.revoke_history.assert_not_called()
    else:
        application.abort_bound_service("sakura.assistant", identity, reason="UNCERTAIN")
        assert set(application._assistant_inputs) == {"new-input"}
        application._host_services.revoke_history.assert_called_once_with("old-token")
        application._host_services.release_owned_artifact.assert_called_once_with("fixture.provider", "old-input")


@pytest.mark.parametrize("cleanup_fails", [False, True])
def test_application_close_reclaims_inputs_after_manager_cleanup(cleanup_fails):
    application = _application()
    application._closed = False
    application.visuals, application.audio_input = Mock(), Mock()
    application.unbind_session = Mock()
    application._loaded = threading.Event()

    def cleanup():
        assert set(application._assistant_inputs) == {"old-input", "new-input"}
        if cleanup_fails:
            raise PluginRuntimeError("PLUGIN_CLEANUP_FAILED")

    application._manager.close.side_effect = cleanup
    if cleanup_fails:
        with pytest.raises(PluginRuntimeError, match="PLUGIN_CLEANUP_FAILED"):
            application.close()
        assert len(application._assistant_inputs) == 2
        application._host_services.clear.assert_not_called()
    else:
        application.close()
        assert not application._assistant_inputs
        assert application._host_services.revoke_history.call_count == 2
