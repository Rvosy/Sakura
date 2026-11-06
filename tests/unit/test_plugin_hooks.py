from __future__ import annotations

import threading
from copy import deepcopy
from types import SimpleNamespace

import pytest

from app.core_host.plugin_hooks import HookHostService
from app.plugin_sdk.sakura_cancellation import CancellationToken, OperationCancelled
from app.plugin_sdk.sakura_hooks import HookError
from app.plugins.host_services import HOST_CALLER, HOST_CALLER_SCOPE


@pytest.fixture
def hooks():
    callbacks = {}
    service = HookHostService(lambda handle, _shape, envelope: {"value": callbacks[handle](deepcopy(envelope["value"]))},
                              None, lambda _plugin, _scope, commit: commit())

    def register(callback, *, plugin="fixture", hook="example.transform", **descriptor):
        handle = str(len(callbacks))
        callbacks[handle] = callback
        caller = HOST_CALLER.set(plugin)
        scope = HOST_CALLER_SCOPE.set("scope-" + plugin)
        try:
            return service.call("register", [{"hook": hook, **descriptor}, handle])["registrationId"]
        finally:
            HOST_CALLER_SCOPE.reset(scope)
            HOST_CALLER.reset(caller)

    return service, register


def test_hooks_pass_edits_in_order_and_keep_callers_data_detached(hooks):
    service, register = hooks
    register(lambda event: {"patch": {"values": event["data"]["values"] + [2]}}, order=20)

    def first(event):
        event["context"]["operationId"] = "changed"
        event["data"]["values"].append(1)
        return {"patch": event["data"]}

    register(first, order=10)
    original, metadata = {"values": [0]}, {"operationId": "original"}
    assert service.run("example.transform", original, metadata) == {"values": [0, 1, 2]}
    assert original == {"values": [0]}
    assert metadata == {"operationId": "original"}


@pytest.mark.parametrize("policy", ["skip", "abort"])
def test_failed_hook_keeps_original_data_and_obeys_failure_policy(hooks, monkeypatch, policy):
    service, register = hooks
    logs = []
    monkeypatch.setattr("app.core_host.plugin_hooks.log_event", lambda *args, **kwargs: logs.append(args))

    def fail(event):
        event["data"]["values"].append("partial change")
        raise ValueError("original failure detail")

    register(fail, failurePolicy=policy)
    original = {"values": []}
    if policy == "abort":
        with pytest.raises(ValueError, match="original failure detail"):
            service.run("example.transform", original)
    else:
        assert service.run("example.transform", original) == {"values": []}
        assert "original failure detail" in str(logs)
    assert original == {"values": []}


def test_abort_prevents_later_handlers_and_retains_reason(hooks):
    service, register = hooks
    later = []
    register(lambda _event: {"abort": True, "reason": "当前操作已被插件拦截。"}, order=0)
    register(lambda _event: later.append(True))
    with pytest.raises(HookError, match="当前操作已被插件拦截") as caught:
        service.run("example.transform", {"text": "hello"})
    assert caught.value.code == "HOOK_ABORTED"
    assert later == []


def test_removed_and_cancelled_callbacks_cannot_publish_late_edits(hooks):
    service, register = hooks
    entered, finish = threading.Event(), threading.Event()
    output = []

    def delayed(_event):
        entered.set()
        assert finish.wait(2)
        return {"patch": {"text": "old result"}}

    register(delayed)
    thread = threading.Thread(target=lambda: output.append(service.run("example.transform", {"text": "original"})))
    thread.start()
    assert entered.wait(2)
    service.revoke_scope("fixture")
    register(lambda _event: {"patch": {"text": "new result"}})
    finish.set()
    thread.join(2)
    assert output == [{"text": "original"}]
    assert service.run("example.transform", {"text": "original"}) == {"text": "new result"}

    cancel = CancellationToken()
    register(lambda _event: cancel.cancel(), order=0)
    with pytest.raises(OperationCancelled):
        service.run("example.transform", {"text": "original"}, cancel_checker=cancel.throw_if_cancelled)


def test_custom_hooks_fill_optional_data_and_pass_new_fields_to_later_handlers(hooks):
    service, register = hooks
    register(lambda _event: {"patch": {"text": "hello", "language": "en"}, "details": "default greeting"})
    register(lambda event: {"patch": {"text": event["data"]["text"].upper()}}
             if event["data"]["language"] == "en" else None)
    original = {"text": None}
    assert service.run("example.transform", original) == {"text": "HELLO", "language": "en"}
    assert original == {"text": None}


def test_artifact_cleanup_failure_preserves_original_hook_error(tmp_path):
    from app.plugins.sakura_plugin_sdk import _HooksProxy

    warnings = []

    def cleanup(_identity):
        raise ConnectionError("cleanup transport closed")

    artifacts = SimpleNamespace(
        allocate=lambda _descriptor: {"artifactId": "input", "path": str(tmp_path / "input.json")},
        commit=lambda identity: {"artifactId": identity}, release_received=cleanup,
    )
    logger = SimpleNamespace(warning=lambda message, **kwargs: warnings.append((message, kwargs)))

    def remote(_service, method, _args):
        if method == "catalog":
            return [{"hook": "example.transform"}]
        raise ValueError("original hook failure")

    context = SimpleNamespace(_remote_call=remote,
                              get=lambda key: logger if key == "sakura.host.logging" else artifacts)
    with pytest.raises(ValueError, match="original hook failure"):
        _HooksProxy(context).run("example.transform", {"text": "大" * 400000})
    assert "cleanup transport closed" in str(warnings)
