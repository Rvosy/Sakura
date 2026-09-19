from types import SimpleNamespace

import pytest

import sakura_model_client as module
from sakura_model import ModelClient, ModelError
from sakura_cancellation import OperationCancelled


REF = {"serviceKey": "fixture.model", "profileId": "p", "modelId": "m"}


class Service:
    identity = {"providerId": "provider", "scopeId": "scope"}

    def __init__(self, action):
        self.action = action
        self.calls = []

    def invoke(self, method, *args, timeout_seconds=None):
        self.calls.append((method, args, timeout_seconds))
        if method == "describe":
            return {"contextWindowTokens": 32768}
        return self.action(method, args)


def client(service, **kwargs):
    context = SimpleNamespace(bind=lambda _key: service, get=lambda _key: None)
    return ModelClient(context, REF, **kwargs)


def test_unknown_ack_releases_the_same_id_without_replay():
    def action(method, _args):
        if method == "begin":
            raise TimeoutError("lost ack")
        return {"released": False}
    service = Service(action)
    with pytest.raises(TimeoutError, match="lost ack"):
        client(service).complete({"messages": []})
    assert [call[0] for call in service.calls] == ["describe", "begin", "release"]
    identity = service.calls[1][1][0]["operationId"]
    assert service.calls[2][1] == (identity,)
    assert 0 < service.calls[2][2] <= 2


def test_unknown_release_is_retained_for_close_without_restarting_the_cleanup_budget(monkeypatch):
    now = [0.]
    monkeypatch.setattr(module.time, "monotonic", lambda: now[0])
    def action(method, _args):
        if method == "begin":
            raise TimeoutError("lost ack")
        if method == "release" and now[0] == 0:
            now[0] += 3
            raise TimeoutError("release lost")
        return {"released": True}
    service = Service(action)
    value = client(service)
    with pytest.raises(TimeoutError, match="lost ack"):
        value.complete({"messages": []})
    assert [call[0] for call in service.calls] == ["describe", "begin", "release"]
    assert value._operations == {service.calls[1][1][0]["operationId"]}
    value.close()
    assert [call[0] for call in service.calls][-1] == "release"
    assert value._operations == set()
    assert service.calls[-1][1] == service.calls[-2][1]


def test_generation_has_no_total_cleanup_deadline_and_preserves_batched_progress(monkeypatch):
    now, polls = [0.], [0]
    monkeypatch.setattr(module.time, "monotonic", lambda: now[0])
    def action(method, args):
        if method == "begin":
            return {"operationId": args[0]["operationId"]}
        if method == "poll":
            polls[0] += 1
            now[0] += 1000
            assert args[1] == polls[0] - 1
            return {"state": "running" if polls[0] < 3 else "completed", "sequence": polls[0],
                    "progress": [{"type": "text_delta", "text": str(polls[0])}]}
        if method == "result":
            return {"response": {"message": {"content": "123"}}}
        return {}
    service, progress = Service(action), []
    assert client(service).complete({"messages": []}, progress_callback=progress.append)["message"]["content"] == "123"
    assert [event["text"] for event in progress] == ["1", "2", "3"]
    assert [call[0] for call in service.calls][-1] == "release"


def test_consumer_cancellation_exception_is_preserved():
    class ConsumerCancelled(Exception):
        pass
    def check():
        raise ConsumerCancelled()
    service = Service(lambda *_args: {})
    with pytest.raises(ConsumerCancelled):
        client(service).complete({"messages": []}, cancel_checker=check)
    assert "begin" not in [call[0] for call in service.calls]


def test_stale_session_rejects_a_replacement_before_describe():
    service = Service(lambda *_args: {})
    with pytest.raises(ModelError, match="模型服务已重新加载") as caught:
        client(service, expected_identity={"providerId": "provider", "scopeId": "retired"})
    assert caught.value.code == "SERVICE_BINDING_EXPIRED"
    assert service.calls == []


@pytest.mark.parametrize("failure,expected", [
    ("commit", ["release"]),
    ("deliver", ["release_delivered", "release_received"]),
    ("begin", ["release_delivered"]),
])
def test_request_artifact_cleanup_matches_ownership_and_shares_the_rpc_budget(tmp_path, monkeypatch, failure, expected):
    now, cleanup = [0.], []
    monkeypatch.setattr(module.time, "monotonic", lambda: now[0])
    class Artifacts:
        def allocate(self, _descriptor):
            return {"artifactId": "request", "path": str(tmp_path / "request.json")}
        def commit(self, _identity):
            if failure == "commit":
                raise TimeoutError("commit ack lost")
            return {}
        def deliver(self, _identity, _receiver, _operation):
            if failure == "deliver":
                raise TimeoutError("delivery ack lost")
            return {"artifactId": "request"}
        def __getattr__(self, method):
            def release(*_args, timeout_seconds):
                cleanup.append((method, timeout_seconds))
                now[0] += .2
                return {"released": True}
            return release
    def action(method, _args):
        if method == "begin":
            raise TimeoutError("begin ack lost")
        now[0] += .2
        return {}
    service = Service(action)
    value = ModelClient(SimpleNamespace(bind=lambda _key: service, get=lambda _key: Artifacts()), REF)
    with pytest.raises(TimeoutError):
        value.complete({"messages": [{"role": "user", "content": "x" * 40000}]})
    assert [method for method, _timeout in cleanup] == expected
    deadlines = [timeout for _method, timeout in cleanup]
    assert all(0 < timeout <= 2 for timeout in deadlines)
    assert deadlines == sorted(deadlines, reverse=True)
    if failure == "begin":
        assert deadlines[0] < service.calls[-1][2] <= 2


def test_bound_sdk_deadline_and_artifact_deadlines_reach_the_rpc_payload(tmp_path):
    from app.plugins.sakura_plugin_sdk import PluginContext
    calls = []
    def remote_request(name, payload):
        calls.append((name, payload))
        return {"providerId": "provider", "scopeId": "scope"} if name == "service.bind" else {}
    def remote_call(_service, method, _args):
        if method == "allocate":
            return {"artifactId": "allocated", "path": str(tmp_path / "allocated")}
        return {}
    context = PluginContext("consumer", tmp_path, tmp_path, remote_call, remote_request)
    bound = context.bind("fixture.model")
    bound.invoke("cancel", "operation", timeout_seconds=.35)
    assert calls[-1] == ("service.call", {"serviceKey": "fixture.model", "method": "cancel", "args": ["operation"],
                                        "binding": {"providerId": "provider", "scopeId": "scope"}, "timeoutSeconds": .35})
    artifacts = context.get("sakura.host.artifacts")
    artifacts.allocate({"mediaType": "application/json", "suffix": ".json"})
    artifacts.release("allocated", timeout_seconds=.3)
    artifacts.release_received("received", timeout_seconds=.2)
    artifacts.release_delivered("delivered", "operation", timeout_seconds=.1)
    assert [payload["timeoutSeconds"] for name, payload in calls[-3:]] == [.3, .2, .1]
    assert [payload["method"] for name, payload in calls[-3:]] == ["release", "release_received", "release_delivered"]


def test_close_reclaims_artifact_cleanup_skipped_by_an_unknown_release(tmp_path, monkeypatch):
    now, released, attempts = [0.], [], [0]
    monkeypatch.setattr(module.time, "monotonic", lambda: now[0])
    class Artifacts:
        def allocate(self, _descriptor):
            return {"artifactId": "input", "path": str(tmp_path / "request.json")}
        def commit(self, _identity):
            return {}
        def deliver(self, _identity, _receiver, _operation):
            return {"artifactId": "input"}
        def release_delivered(self, identity, operation, *, timeout_seconds):
            released.append((identity, operation, timeout_seconds))
            return {"released": True}
    def action(method, _args):
        if method == "begin":
            raise TimeoutError("begin ack lost")
        attempts[0] += 1
        if attempts[0] == 1:
            now[0] += 3
            raise TimeoutError("release ack lost")
        return {"released": False}
    service = Service(action)
    value = ModelClient(SimpleNamespace(bind=lambda _key: service, get=lambda _key: Artifacts()), REF)
    with pytest.raises(TimeoutError, match="begin ack lost"):
        value.complete({"messages": [{"role": "user", "content": "x" * 40000}]})
    assert released == []
    assert value._operations
    value.close()
    assert released == [("input", service.calls[1][1][0]["operationId"], 2)]
    assert not value._operations
    assert not value._artifact_cleanups
