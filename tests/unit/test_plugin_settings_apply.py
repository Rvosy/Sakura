"""Settings reload owns chat admission for the entire plugin RPC lifecycle."""

import threading
from types import SimpleNamespace

import pytest

from app.core_host.plugin_runtime_application import PluginRuntimeApplication
from app.core_host.real_chat import RealChatBoundary, RealChatRejection
from app.plugins.runtime_v4 import PluginRuntimeError


def _application(tmp_path):
    session = SimpleNamespace(character=SimpleNamespace(id="character"))
    boundary = RealChatBoundary("generation", "credential", tmp_path,
        session_provider=lambda: session, timeline_store=object())
    application = object.__new__(PluginRuntimeApplication)
    application._chat_boundary = boundary
    return application, boundary


def _request():
    return {"id": "new-turn", "kind": "request", "name": "chat.send",
            "generationId": "generation", "generationCredential": "credential",
            "payload": {"message": "hello", "operationId": "new-turn"}}


@pytest.mark.parametrize("fails", [False, True], ids=["applied", "reload-failed"])
def test_settings_reload_rejects_new_chat_without_holding_rpc_lock(tmp_path, fails):
    application, boundary = _application(tmp_path)
    states = []

    def reload(_plugin_id):
        # Plugins may call Host services during setup; those calls must not
        # deadlock on the same admission lock while reload awaits their RPC.
        worker = threading.Thread(target=lambda: states.append(boundary.current_host_state()))
        worker.start()
        worker.join(2)
        assert not worker.is_alive()
        assert states[0]["idle"] is False
        with pytest.raises(RealChatRejection) as caught:
            boundary.reserve_send(_request())
        assert caught.value.code == "RUNTIME_UPDATE_BUSY"
        if fails:
            raise RuntimeError("reload failed")
        return {"plugins": [{"pluginId": "provider", "state": "active", "reasonCode": "READY"}]}

    application.reload_plugin = reload
    try:
        if fails:
            with pytest.raises(RuntimeError, match="reload failed"):
                application._apply_settings_result("provider", {"applicationState": "restart_required"})
        else:
            assert application._apply_settings_result("provider", {"applicationState": "restart_required"})["applicationState"] == "applied"
        boundary.reserve_send(_request())
        boundary.abandon_send(_request())
    finally:
        boundary.close()


def test_settings_reload_cannot_start_during_accepted_chat(tmp_path):
    application, boundary = _application(tmp_path)
    reloads = []
    application.reload_plugin = lambda identity: reloads.append(identity)
    try:
        boundary.reserve_send(_request())
        with pytest.raises(PluginRuntimeError) as caught:
            application._apply_settings_result("provider", {"applicationState": "restart_required"})
        assert caught.value.code == "PLUGIN_RELOAD_BUSY"
        assert reloads == []
        boundary.abandon_send(_request())
    finally:
        boundary.close()
