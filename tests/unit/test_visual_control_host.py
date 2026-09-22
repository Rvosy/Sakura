import threading
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from app.core_host.visual_control_host import HostVisualService
from app.core_host.visual_host import VisualBinding, VisualHost, VisualHostError
from app.plugins.host_services import HOST_CALLER, HOST_CALLER_SCOPE


@contextmanager
def caller(plugin_id="consumer", scope="scope"):
    a, b = HOST_CALLER.set(plugin_id), HOST_CALLER_SCOPE.set(scope)
    try:
        yield
    finally:
        HOST_CALLER_SCOPE.reset(b)
        HOST_CALLER.reset(a)


def fixture(parse=None):
    identity = {"providerId": "portraits", "scopeId": "provider-scope"}
    runtime = SimpleNamespace(service_identity=lambda key: identity,
        call_service=lambda *args: parse(*args) if parse else {"state": {"portrait": "smile"}})
    binding = VisualBinding(runtime, SimpleNamespace(service="portraits", renderer="renderer.js", editor=None), identity,
        {"characterId": "character", "resource": {"id": "portrait", "type": "portrait"}},
        {"prompt": "choose a portrait", "outputSchema": {"type": "object"},
         "rendererData": {"path": "private"}, "parserData": {}})
    current, idle, events, selected = [binding], [True], [], []
    host = HostVisualService(binding_provider=lambda: ("character", current[0]),
        emit_callback=lambda name, payload: events.append((name, payload)), is_idle=lambda: idle[0],
        select_callback=lambda target, resource: selected.append((target, resource)) or {"accepted": True})
    return host, current, idle, events, selected


def apply(host):
    target = host.current()["target"]
    result = host.apply({"target": target, "control": {"version": 1, "resourceId": target["resourceId"], "payload": {"portrait": "smile"}}})
    return target, result


def test_provider_parses_control_and_receipt_requires_actual_desktop_claim():
    host, _, _, events, _ = fixture()
    with caller():
        current = host.current()
        assert "rendererData" not in current and "assets" not in current
        target, result = apply(host)
        receipt = result["requestId"]
        assert result["status"] == "accepted"
        assert events[0][0] == "host.visual.apply"
        assert events[0][1]["control"]["state"] == {"portrait": "smile"}
        assert "payload" not in events[0][1]["control"]
        assert host.complete({"requestId": receipt, "target": target, "status": "displayed"}) == {"accepted": False}
        assert host.claim(receipt, target) == {"accepted": True}
        assert host.claim(receipt, target) == {"accepted": False}
        assert host.complete({"requestId": receipt, "target": target, "status": "displayed"}) == {"accepted": True}
        assert host.status(receipt)["status"] == "displayed"
    with caller(scope="new-scope"):
        with pytest.raises(VisualHostError, match="UNAUTHORIZED"):
            host.status(receipt)
    with caller():
        assert host.release(receipt) == {"released": True}
    assert events[-1] == ("host.visual.cancel", {"requestId": receipt, "target": target})


def test_busy_and_replaced_target_cannot_execute_or_switch_resources():
    host, current, idle, events, selected = fixture()
    with caller():
        idle[0] = False
        assert apply(host)[1] == {"accepted": False, "reasonCode": "VISUAL_BUSY"}
        idle[0] = True
        target, result = apply(host)
        current[0].close()
        assert host.claim(result["requestId"], target) == {"accepted": False}
        assert host.status(result["requestId"])["status"] == "cancelled"
        assert host.complete({"requestId": result["requestId"], "target": target, "status": "displayed"}) == {"accepted": False}
        assert host.select({"target": target, "resourceId": "other"})["accepted"] is False
        assert selected == []
        assert events[-1][0] == "host.visual.cancel"


def test_scope_revocation_during_provider_parse_does_not_publish_late_control():
    entered, release = threading.Event(), threading.Event()
    def parse(*args):
        entered.set()
        assert release.wait(3)
        return {"state": {"portrait": "smile"}}
    host, _, _, events, _ = fixture(parse)
    results = []
    def worker():
        with caller():
            results.append(apply(host)[1])
    thread = threading.Thread(target=worker)
    thread.start()
    assert entered.wait(3)
    host.revoke_scope("consumer")
    release.set()
    thread.join(3)
    assert not thread.is_alive()
    assert results == [{"accepted": False, "reasonCode": "VISUAL_BINDING_EXPIRED"}]
    assert events == []


def test_desktop_detach_cancels_claimed_and_displayed_controls_but_allows_next_activity():
    host, _, _, events, _ = fixture()
    with caller():
        target, first = apply(host)
        assert host.claim(first["requestId"], target)["accepted"]
        assert host.complete({"requestId": first["requestId"], "target": target, "status": "displayed"})["accepted"]
        _, pending = apply(host)
        host.invalidate_activity()
        assert host.status(first["requestId"])["status"] == "cancelled"
        assert host.status(pending["requestId"])["status"] == "cancelled"
        assert not host.claim(pending["requestId"], target)["accepted"]
        assert [name for name, _ in events].count("host.visual.cancel") == 2
        assert apply(host)[1]["accepted"]


def test_desktop_detach_during_parse_rejects_the_late_control():
    entered, release = threading.Event(), threading.Event()
    def parse(*args):
        entered.set()
        assert release.wait(3)
        return {"state": {"portrait": "smile"}}
    host, _, _, events, _ = fixture(parse)
    results = []
    def worker():
        with caller():
            results.append(apply(host)[1])
    thread = threading.Thread(target=worker)
    thread.start()
    assert entered.wait(3)
    host.invalidate_activity()
    release.set()
    thread.join(3)
    assert not thread.is_alive()
    assert not results[0]["accepted"]
    assert events == []


def test_visual_preference_commit_rejects_replaced_target_and_obsolete_caller():
    from app.core_host.plugin_runtime_application import PluginRuntimeApplication
    from app.plugins.runtime_v4 import PluginRuntimeError
    controls, bindings, _, _, _ = fixture()
    binding = bindings[0]
    app = PluginRuntimeApplication.__new__(PluginRuntimeApplication)
    app._visual_state_lock = threading.RLock()
    app._visual_character = SimpleNamespace(id="character")
    app._visual_binding = binding
    app.visuals = VisualHost(SimpleNamespace(), inventory=lambda: None)
    app.visuals.publish(0, binding)
    writes = []
    live = [True]
    def commit_scope(plugin_id, scope_id, commit):
        assert (plugin_id, scope_id) == ("consumer", "scope")
        if not live[0]:
            raise PluginRuntimeError("SERVICE_BINDING_EXPIRED")
        return commit()
    app._manager = SimpleNamespace(commit_plugin_scope=commit_scope)
    with caller():
        target = controls.current()["target"]
        app.commit_visual_selection(target, lambda: writes.append("saved"))
        live[0] = False
        with pytest.raises(PluginRuntimeError, match="SERVICE_BINDING_EXPIRED"):
            app.commit_visual_selection(target, lambda: writes.append("stale-scope"))
        live[0] = True
        # The VisualHost owner retires the binding before the app projection updates.
        app.visuals.clear()
        with pytest.raises(VisualHostError, match="VISUAL_BINDING_EXPIRED"):
            app.commit_visual_selection(target, lambda: writes.append("stale-target"))
    assert writes == ["saved"]
