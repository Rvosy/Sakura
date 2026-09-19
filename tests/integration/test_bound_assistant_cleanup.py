from unittest.mock import Mock

import pytest

from app.core_host import assistant_adapter
from app.plugins.runtime_v4 import PluginRuntimeError
from tests.integration.test_context_plugin_chat import chat


@pytest.mark.parametrize("failure", ["descriptor", "timeline_read", "timeline_write", "artifact", "begin_rejected"])
def test_failure_before_begin_keeps_healthy_assistant_and_next_turn_usable(chat, monkeypatch, failure):
    application = chat.application
    identity = application.service_identity("sakura.assistant")
    process = application._manager._records[identity["providerId"]].process
    pid = process.pid
    calls = []
    call = application.call_bound_service
    def observe(service, owner, method, *args, **kwargs):
        calls.append(method)
        return call(service, owner, method, *args, **kwargs)
    monkeypatch.setattr(application, "call_bound_service", observe)
    abort = Mock(wraps=application.abort_bound_service)
    release = Mock(wraps=chat.session.assistant.release)
    monkeypatch.setattr(application, "abort_bound_service", abort)
    monkeypatch.setattr(chat.session.assistant, "release", release)

    def unavailable(*_args, **_kwargs):
        raise OSError("fixture storage unavailable")

    with monkeypatch.context() as failing:
        if failure == "descriptor":
            failing.setattr(chat.session, "descriptor", unavailable)
        elif failure == "timeline_read":
            failing.setattr(chat.timeline, "latest_cursor", unavailable)
        elif failure == "timeline_write":
            failing.setattr(chat.timeline, "append_many", unavailable)
        elif failure == "artifact":
            failing.setattr(application, "create_assistant_input", unavailable)
        else:
            def reject(service, owner, method, *args, **kwargs):
                calls.append(method)
                assert method == "begin"
                raise PluginRuntimeError("ASSISTANT_BUSY")
            failing.setattr(application, "call_bound_service", reject)
        assert chat.send("before-begin", "first attempt")["name"] == "chat.failed"

    assert calls == (["begin"] if failure == "begin_rejected" else [])
    if failure in {"descriptor", "timeline_read", "timeline_write"}:
        release.assert_not_called()
    abort.assert_not_called()
    assert application.service_identity("sakura.assistant") == identity
    assert process.pid == pid
    assert not application._assistant_inputs
    assert chat.requests == []

    assert chat.send("recovered", "storage has recovered")["name"] == "chat.completed"
    assert application.service_identity("sakura.assistant") == identity
    assert process.pid == pid
    assert calls.count("release") == 1
    assert len(chat.requests) == 1


@pytest.mark.parametrize("reloaded", [False, True])
def test_stuck_release_deadline_aborts_only_its_real_process_scope(chat, monkeypatch, reloaded):
    application = chat.application
    old_identity = application.service_identity("sakura.assistant")
    old_process = application._manager._records[old_identity["providerId"]].process
    call = application.call_bound_service
    now = [0.0]
    cleaning = [False]
    polls = []
    monkeypatch.setattr(assistant_adapter, "monotonic", lambda: now[0])
    def stuck_release(service, identity, method, *args, timeout=None):
        if method == "release":
            cleaning[0] = True
            return {"released": False}
        if method == "poll" and cleaning[0]:
            polls.append(timeout)
            if reloaded and len(polls) == 1:
                application._manager.reload_plugin(old_identity["providerId"])
            now[0] += 0.4
            return {"state": "running", "sequence": 0, "progress": []}
        return call(service, identity, method, *args, timeout=timeout)
    monkeypatch.setattr(application, "call_bound_service", stuck_release)
    assert chat.send("stuck-release", "finish the reply")["name"] == "chat.completed"
    assert polls and all(0 < timeout <= 2 for timeout in polls)
    assert not application._assistant_inputs
    assert old_process.pid is None
    if reloaded:
        current = application.service_identity("sakura.assistant")
        assert current != old_identity
        record = application._manager._records[current["providerId"]]
        assert record.state == "active"
        assert record.process.pid is not None
    else:
        with pytest.raises(PluginRuntimeError, match="SERVICE_MISSING"):
            application.service_identity("sakura.assistant")
    assert chat.boundary.snapshot_fields("ready", None)["activeInteractionSummary"] is None
