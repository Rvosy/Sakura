from __future__ import annotations

import json
import threading
from types import SimpleNamespace

import pytest

from sakura_assistant import diagnostics
from sakura_assistant import service as service_module
from sakura_assistant.service import AssistantPlugin, Operation, RemoteTools
from sakura_cancellation import OperationCancelled
from sakura_model import ApiConfigError


class ArtifactFailure(RuntimeError):
    code = "ARTIFACT_NOT_FOUND"


def provider(*, release=None):
    plugin = AssistantPlugin()
    plugin.changed = threading.Condition()
    plugin.operations = {}
    plugin.closed = False
    plugin.context = SimpleNamespace(get=lambda _key: SimpleNamespace(
        release_received=release or (lambda _artifact_id: True),
    ))
    return plugin


@pytest.mark.parametrize("saved,expected,parameters", [
    ({"temperature": 3, "top_p": False, "max_tokens": -1},
     {"temperature": None, "top_p": None, "max_tokens": None}, {"temperature": 0.8}),
    ({"temperature": "0.8", "top_p": float("nan"), "max_tokens": 2.5},
     {"temperature": None, "top_p": None, "max_tokens": None}, {"temperature": 0.8}),
    ({"temperature": 0, "top_p": 0, "max_tokens": 2048},
     {"temperature": 0, "top_p": 0, "max_tokens": 2048},
     {"temperature": 0, "top_p": 0, "max_tokens": 2048}),
])
def test_prior_generation_handoff_remains_usable_without_rewriting_configuration(tmp_path, monkeypatch, saved, expected, parameters):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"generation": saved, "custom": "keep"}), encoding="utf-8")
    before = path.read_bytes()
    sections, requests = {}, []
    def update(values):
        current = json.loads(path.read_text(encoding="utf-8"))
        path.write_text(json.dumps({**current, **values}), encoding="utf-8")
    def register(descriptor, **callbacks):
        sections[descriptor["sectionId"]] = callbacks
    artifacts = SimpleNamespace(
        allocate=lambda _descriptor: {"artifactId": "output", "path": str(tmp_path / "output.json")},
        commit=lambda _identity: {"artifactId": "output"}, release_received=lambda _identity: None)
    services = {
        "sakura.host.settings": SimpleNamespace(register=register, place=lambda *_a, **_k: None),
        "sakura.host.logging": None,
        "sakura.host.storage": SimpleNamespace(resolve=lambda *_args: str(tmp_path / "logs")),
        "sakura.host.context": SimpleNamespace(catalog=lambda: []),
        "sakura.host.tools": SimpleNamespace(catalog=lambda: []),
        "sakura.host.artifacts": artifacts,
    }
    context = SimpleNamespace(config=SimpleNamespace(
        get=lambda: json.loads(path.read_text(encoding="utf-8")), update=update),
        get=services.__getitem__, effect=lambda _callback: None, provide=lambda *_a, **_k: None)
    class Model:
        description = {"contextWindowTokens": 32768}
        def __init__(self, _context, reference, **_kwargs): self.reference = reference
        def close(self): pass
        def complete(self, request, **_kwargs):
            requests.append(request)
            return {"message": {"role": "assistant", "content": '{"segments":[{"text":"已收到","tone":"中性"}]}'}}
    monkeypatch.setattr(service_module, "ModelClient", Model)
    monkeypatch.setattr(diagnostics, "_logger", None)
    plugin = AssistantPlugin()
    plugin.setup(context)
    settings = sections["generation"]
    assert settings["load"]() == expected
    for invalid in (3, float("nan")):
        with pytest.raises(ApiConfigError):
            settings["save"]({"temperature": invalid})
    reference = {"serviceKey": "fixture.model", "profileId": "fixture", "modelId": "model"}
    request = {"session": {"modelSlots": {"chat": reference}, "modelBindings": {"chat": {"providerId": "fixture", "scopeId": "scope"}},
        "character": {"id": "fixture", "displayName": "Fixture", "systemPrompt": "Fixture", "replyTones": ["中性"]},
        "loopSettings": {}}, "event": {"type": "update_available", "payload": {"version": "2.0"}}}
    monkeypatch.setattr(plugin, "_read_input", lambda _operation: request)
    operation = Operation("prior-generation", {})
    plugin.operations[operation.operation_id] = operation
    plugin._run(operation)
    assert operation.error is None
    assert len(requests) == 1
    assert requests[0]["parameters"] == parameters
    assert path.read_bytes() == before
    plugin.release(operation.operation_id, "completed")
    assert settings["save"]({"temperature": 1, "top_p": 0.5, "max_tokens": 32}) == {"applicationState": "restart_required"}
    assert settings["load"]() == {"temperature": 1, "top_p": 0.5, "max_tokens": 32}
    assert plugin.generation == expected
    assert json.loads(path.read_text(encoding="utf-8"))["custom"] == "keep"
    plugin.close()


def test_trace_flush_failure_still_releases_output_and_frees_the_operation(monkeypatch):
    released = []
    recorded = []
    plugin = provider(release=released.append)
    original = OSError("trace directory is not writable")
    def finish(*_args, **_kwargs):
        raise original
    operation = Operation("trace-failure", {}, state="completed", output={"artifactId": "output"},
                          trace=SimpleNamespace(finish_operation=finish))
    plugin.operations[operation.operation_id] = operation
    monkeypatch.setattr(diagnostics, "log_event", lambda *args, **kwargs: recorded.append((args, kwargs)))

    assert plugin.release(operation.operation_id, "completed") == {"released": True}
    assert released == ["output"]
    assert not plugin.operations
    assert recorded[0][0][2]["stage"] == "trace"
    assert "trace directory is not writable" in recorded[0][0][2]["error_message"]


def test_disposal_is_idempotent_when_the_receiver_already_released_the_artifact(monkeypatch):
    def gone(_artifact_id):
        raise ArtifactFailure("already gone")
    recorded = []
    plugin = provider(release=gone)
    operation = Operation("already-released", {}, state="completed", output={"artifactId": "output"})
    plugin.operations[operation.operation_id] = operation
    monkeypatch.setattr(diagnostics, "log_event", lambda *args, **kwargs: recorded.append(args))
    assert plugin.release(operation.operation_id, "completed") == {"released": True}
    plugin._dispose(operation)
    assert not plugin.operations
    assert not recorded


def test_disposal_never_removes_another_operation_with_the_same_identifier():
    plugin = provider()
    stale = Operation("operation", {}, state="completed")
    replacement = Operation("operation", {})
    plugin.operations[replacement.operation_id] = replacement
    plugin._dispose(stale)
    assert plugin.operations[replacement.operation_id] is replacement


def test_worker_start_failure_does_not_leave_the_assistant_busy(monkeypatch):
    plugin = provider()
    original = RuntimeError("thread creation failed")
    with monkeypatch.context() as patch:
        def fail_start(_worker):
            raise original
        patch.setattr(threading.Thread, "start", fail_start)
        with pytest.raises(RuntimeError) as caught:
            plugin.begin({"operationId": "start-failure", "input": {"artifactId": "input"}})
        assert caught.value is original
        assert not plugin.operations

    def complete(operation):
        with plugin.changed:
            operation.state = "completed"
            plugin.changed.notify_all()
    monkeypatch.setattr(plugin, "_run", complete)
    plugin.begin({"operationId": "next-turn", "input": {"artifactId": "next"}})
    operation = plugin.operations["next-turn"]
    operation.worker.join(2)
    assert not operation.worker.is_alive()
    assert plugin.release(operation.operation_id, "completed") == {"released": True}


@pytest.mark.parametrize("cancel", [False, True])
def test_remote_tool_failure_stays_a_tool_result_while_cancellation_stops_the_turn(monkeypatch, cancel):
    class RemoteFailure(RuntimeError):
        code = "TOOL_REGISTRATION_EXPIRED"
        diagnostics = {"diagnostic": "captured provider scope expired", "cause_type": "ScopeExpired"}

    calls = []
    logs = []
    operation = Operation("remote-tool", {})
    def execute(*args, **kwargs):
        calls.append((args, kwargs))
        if cancel:
            operation.cancelled.set()
        raise RemoteFailure("private detail")
    remote = SimpleNamespace(catalog=lambda: [{"name": "fixture", "description": "fixture", "parameters": {},
        "group": "plugin", "risk": "low", "source": "plugin", "registrationId": "captured", "timeoutSeconds": 90}], execute=execute)
    context = SimpleNamespace(get=lambda key: remote if key == "sakura.host.tools" else object())
    registry = RemoteTools(context, operation)
    monkeypatch.setattr(diagnostics, "log_event", lambda *args, **kwargs: logs.append((args, kwargs)))
    if cancel:
        with pytest.raises(OperationCancelled):
            registry.execute("fixture", {})
        assert not logs
    else:
        result = registry.execute("fixture", {})
        assert result.success is False
        assert result.reason_code == "TOOL_REGISTRATION_EXPIRED"
        assert "private detail" not in result.error
        assert logs[0][0][2]["diagnostic"] == "captured provider scope expired"
    assert len(calls) == 1


def test_lost_begin_ack_can_be_released_without_replaying_the_worker(monkeypatch):
    plugin = provider()
    entered = threading.Event()
    finish = threading.Event()
    calls = []
    def read_input(operation):
        calls.append(operation.operation_id)
        entered.set()
        assert finish.wait(2), "test must release the worker barrier"
        operation.check()
        raise AssertionError("cancelled input must not reach model setup")
    monkeypatch.setattr(plugin, "_read_input", read_input)
    try:
        # The begin mutation reaches the provider; its acknowledgement is lost.
        plugin.begin({"operationId": "uncertain-begin", "input": {"artifactId": "input"}})
        assert entered.wait(2)
        operation = plugin.operations["uncertain-begin"]
        assert plugin.release(operation.operation_id, "cancelled") == {"released": False}
        assert operation.worker.is_alive()
        assert plugin.operations[operation.operation_id] is operation
        assert operation.cancelled.is_set()
        finish.set()
        operation.worker.join(2)
        assert not operation.worker.is_alive()
        assert isinstance(operation.error, OperationCancelled)
        assert not plugin.operations
        assert calls == ["uncertain-begin"]
    finally:
        finish.set()
        plugin.close()


def test_worker_completion_notifies_waiters_even_if_cleanup_diagnostics_fail(monkeypatch):
    plugin = provider()
    entered = threading.Event()
    finish = threading.Event()
    def read_input(operation):
        operation.trace = SimpleNamespace(finish_operation=lambda *_a, **_k: (_ for _ in ()).throw(OSError("disk failure")))
        entered.set()
        assert finish.wait(2)
        operation.check()
    monkeypatch.setattr(plugin, "_read_input", read_input)
    # Diagnostics is allowed to fail; the ownership slot and notification must
    # already be settled before that failure escapes the cleanup callback.
    monkeypatch.setattr(diagnostics, "log_event", lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("log failure")))
    operation = Operation("cleanup", {})
    plugin.operations[operation.operation_id] = operation
    observed_errors = []
    def run():
        try:
            plugin._run(operation)
        except RuntimeError as error:
            observed_errors.append(str(error))
    worker = threading.Thread(target=run)
    operation.worker = worker
    worker.start()
    try:
        assert entered.wait(2)
        assert plugin.release(operation.operation_id, "cancelled") == {"released": False}
        with plugin.changed:
            finish.set()
            assert plugin.changed.wait_for(lambda: not plugin.operations, timeout=2)
        worker.join(2)
        assert not worker.is_alive()
        assert observed_errors == ["log failure"]
    finally:
        finish.set()
        worker.join(2)
