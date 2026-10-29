"""验证登录凭据、订阅请求及设置任务的实际行为。"""
from __future__ import annotations

import asyncio
import base64
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
import threading
import time
from types import SimpleNamespace
from urllib.parse import parse_qs

import httpx
import pytest

from app.plugins.sakura_plugin_sdk import PluginContext
from plugins.optional.sakura_codex.oauth import Account, CLIENT_ID, credentials
from plugins.optional.sakura_codex.profiles import CodexProfiles
from plugins.optional.sakura_codex.plugin import CodexPlugin
from plugins.optional.sakura_codex.transport import execute
from sakura_cancellation import OperationCancelled
from sakura_model import ModelError
from sakura_model_provider import Job
from sakura_openai_responses import build_request, response_result
from sakura_structured_output import require_complete_response


PROFILE_ID = "chatgpt"


@pytest.mark.parametrize("refused", [False, True])
def test_responses_preserves_incomplete_and_refusal_for_consumers(refused):
    response = {"status": "completed" if refused else "incomplete", "output": [{
        "type": "message", "content": [{"type": "refusal", "refusal": "请求被拒绝"} if refused else {
            "type": "output_text", "text": '{"operations":[]}',
        }],
    }]}
    if not refused:
        response["incomplete_details"] = {"reason": "max_output_tokens"}
    with pytest.raises(ModelError) as caught:
        require_complete_response(response_result(response))
    assert caught.value.code == ("MODEL_OUTPUT_REFUSED" if refused else "MODEL_OUTPUT_TRUNCATED")


def token(account="account-a", *, expires=None, marker="first"):
    payload = {"exp": expires or time.time() + 3600, "marker": marker,
               "https://api.openai.com/auth": {"chatgpt_account_id": account, "chatgpt_plan_type": "plus"},
               "https://api.openai.com/profile": {"email": "person@example.com"}}
    return "header." + base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=") + ".signature"


def tokens(**kwargs):
    return {"access_token": token(**kwargs), "refresh_token": "private-refresh", "expires_in": 3600}


def mock_http(monkeypatch, handler):
    original = httpx.AsyncClient
    class Client(original):
        def __init__(self, **kwargs):
            kwargs.pop("proxy", None)
            super().__init__(**kwargs, transport=httpx.MockTransport(handler))
    monkeypatch.setattr(httpx, "AsyncClient", Client)


def account_at(tmp_path, **kwargs):
    account = Account(tmp_path / "auth.json")
    account._save(credentials(tokens(**kwargs)))
    return account


def settings(account):
    return {"profileId": "fixture", "account": account.snapshot(), "model": "account-model", "timeout_seconds": 2}


def request():
    return {"messages": [{"role": "system", "content": "保留角色指令"}, {"role": "user", "content": "你好"}],
            "parameters": {"temperature": .4, "top_p": .9, "max_tokens": 100}, "responseFormat": {"type": "json_object"}, "stream": True}


def stream_response(*events):
    return httpx.Response(200, headers={"content-type": "text/event-stream"},
                          text="".join("data: " + json.dumps(event) + "\n\n" for event in events))


def completed(output=None):
    return {"type": "response.completed", "response": {"id": "resp-1", "status": "completed", "output": output or [
        {"type": "message", "id": "msg-1", "role": "assistant", "content": [{"type": "output_text", "text": "你好"}]}],
        "usage": {"input_tokens": 20, "output_tokens": 5, "total_tokens": 25, "input_tokens_details": {"cached_tokens": 10}}}}


def test_device_login_persists_own_account_without_exposing_credentials(tmp_path, monkeypatch):
    calls, published = [], []
    def handler(req):
        calls.append(req)
        if req.url.path.endswith("/usercode"):
            assert json.loads(req.content) == {"client_id": CLIENT_ID}
            return httpx.Response(200, json={"device_auth_id": "private-device", "user_code": "ABCD-EFGH", "interval": "1"})
        if req.url.path.endswith("/deviceauth/token"):
            assert json.loads(req.content) == {"device_auth_id": "private-device", "user_code": "ABCD-EFGH"}
            return httpx.Response(200, json={"authorization_code": "private-code", "code_verifier": "private-verifier"})
        data = parse_qs(req.content.decode())
        assert data["code_verifier"] == ["private-verifier"]
        assert data["redirect_uri"] == ["https://auth.openai.com/deviceauth/callback"]
        return httpx.Response(200, json=tokens())
    mock_http(monkeypatch, handler)
    account = Account(tmp_path / "auth.json")
    public = account.login(None, published.append)
    assert public == {"loggedIn": True, "email": "person@example.com", "plan": "plus"}
    assert published[0]["userCode"] == "ABCD-EFGH"
    assert "private-device" not in repr(published)
    assert Account(account.path).snapshot()["tokens"]["refresh_token"] == "private-refresh"
    assert {req.url.host for req in calls} == {"auth.openai.com"}


def test_expired_device_code_stops_login_without_replacing_account(tmp_path, monkeypatch):
    account = account_at(tmp_path)
    previous = account.path.read_bytes()
    mock_http(monkeypatch, lambda _: httpx.Response(200, json={
        "device_auth_id": "device", "user_code": "code", "interval": 1, "expires_in": 0}))
    with pytest.raises(ModelError, match="登录代码已过期"):
        account.login(None, lambda _: None)
    assert account.path.read_bytes() == previous


def test_cancelled_login_keeps_previous_account(tmp_path, monkeypatch):
    account = account_at(tmp_path)
    previous = account.path.read_bytes()
    entered, cancel, closed = threading.Event(), threading.Event(), threading.Event()
    async def handler(req):
        if req.url.path.endswith("/usercode"):
            return httpx.Response(200, json={"device_auth_id": "device", "user_code": "code", "interval": 1})
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            closed.set()
    mock_http(monkeypatch, handler)
    def check():
        if cancel.is_set():
            raise OperationCancelled()
    with ThreadPoolExecutor() as executor:
        future = executor.submit(account.login, check, lambda _: None)
        assert entered.wait(2)
        cancel.set()
        with pytest.raises(OperationCancelled):
            future.result(2)
    assert closed.is_set()
    assert account.path.read_bytes() == previous


@pytest.mark.parametrize("marker", ["first", "renewed"])
def test_concurrent_expired_requests_share_refresh_and_keep_rotated_token(tmp_path, monkeypatch, marker):
    account = account_at(tmp_path)
    account.value["expires_at"] = time.time() - 1
    snapshot = account.snapshot()
    calls = []
    def handler(req):
        calls.append(req)
        refreshed = tokens(marker=marker)
        if marker == "first":
            refreshed["access_token"] = snapshot["tokens"]["access_token"]
        return httpx.Response(200, json={**refreshed, "refresh_token": "rotated-refresh"})
    mock_http(monkeypatch, handler)
    with ThreadPoolExecutor(max_workers=2) as executor:
        values = list(executor.map(lambda _: account.authorize(snapshot, None), range(2)))
    assert values[0]["access_token"] == values[1]["access_token"]
    assert len(calls) == 1
    assert Account(account.path).snapshot()["tokens"]["refresh_token"] == "rotated-refresh"


def test_logout_prevents_late_refresh_from_restoring_account(tmp_path, monkeypatch):
    account = account_at(tmp_path)
    snapshot = account.snapshot()
    entered, release = threading.Event(), threading.Event()
    async def handler(req):
        entered.set()
        while not release.is_set():
            await asyncio.sleep(.01)
        return httpx.Response(200, json=tokens(marker="renewed"))
    mock_http(monkeypatch, handler)
    with ThreadPoolExecutor() as executor:
        future = executor.submit(account.authorize, snapshot, None, force=True)
        assert entered.wait(2)
        account.logout(); release.set()
        with pytest.raises(OperationCancelled):
            future.result(2)
    assert not account.path.exists()
    assert not account.public()["loggedIn"]


@pytest.mark.parametrize("image", [
    {"type": "image", "dataUrl": "data:image/png;base64,aGVsbG8="},
    {"type": "input_image", "image_url": "data:image/png;base64,aGVsbG8="},
])
def test_subscription_stream_preserves_images_tools_reasoning_and_usage(tmp_path, monkeypatch, image):
    account = account_at(tmp_path)
    calls, progress = [], []
    output = [{"type": "reasoning", "id": "reason-1", "summary": [], "encrypted_content": "opaque-reasoning"},
              {"type": "message", "id": "msg-1", "role": "assistant", "phase": "commentary", "status": "completed",
               "content": [{"type": "output_text", "text": "先查询", "annotations": []}]},
              {"type": "function_call", "id": "fc-1", "call_id": "call-1", "name": "lookup", "arguments": '{"q":"天气"}'}]
    def handler(req):
        calls.append(req)
        return stream_response(completed(output))
    mock_http(monkeypatch, handler)
    value = request()
    value["messages"][1]["content"] = [{"type": "text", "text": "看看图片"}, image]
    value["tools"] = [{"name": "lookup", "description": "查询", "parameters": {"type": "object", "properties": {"q": {"type": "string"}}}}]
    before = deepcopy(value)
    result = execute(account, settings(account), value, cancel_checker=None, progress=progress.append)
    body = json.loads(calls[0].content)
    assert calls[0].url == "https://chatgpt.com/backend-api/codex/responses"
    assert calls[0].headers["chatgpt-account-id"] == "account-a"
    assert calls[0].headers["authorization"] == "Bearer " + account.value["access_token"]
    assert body["instructions"] == "保留角色指令"
    assert body["input"][0]["content"][1]["image_url"] == "data:image/png;base64,aGVsbG8="
    assert body["tools"][0]["name"] == "lookup" and body["tools"][0]["strict"] is False
    assert not ({"temperature", "top_p", "max_output_tokens", "text"} & body.keys())
    assert body["store"] is False and body["stream"] is True
    assert value == before
    assert result["usage"]["prompt_tokens_details"]["cached_tokens"] == 10
    assert result["finishReason"] == "tool_calls"
    next_request = build_request("account-model", {"messages": [result["message"], {"role": "tool", "toolCallId": "call-1", "content": "晴天"}]})
    assert next_request["input"][0]["encrypted_content"] == "opaque-reasoning"
    assert next_request["input"][1]["phase"] == "commentary"
    assert next_request["input"][2]["call_id"] == next_request["input"][3]["call_id"] == "call-1"
    assert next_request["input"][2]["arguments"] == '{"q":"天气"}'


@pytest.mark.parametrize("expires_at", [None, "future"])
def test_current_access_token_can_be_used_without_a_refresh_token(tmp_path, monkeypatch, expires_at):
    account = account_at(tmp_path)
    account.value.pop("refresh_token")
    account.value["expires_at"] = time.time() + 20 if expires_at else None
    def handler(req):
        assert req.url.path.endswith("/responses")
        return stream_response(completed())
    mock_http(monkeypatch, handler)
    result = execute(account, settings(account), request(), cancel_checker=None, progress=lambda _: None)
    assert result["message"]["content"] == "你好"


@pytest.mark.parametrize("status,expected", [(401, 2), (403, 1), (429, 1), (500, 1)])
def test_only_rejected_authentication_refreshes_once(tmp_path, monkeypatch, status, expected):
    account = account_at(tmp_path)
    sent, refreshed = [], []
    def handler(req):
        if req.url.path == "/oauth/token":
            refreshed.append(req)
            return httpx.Response(200, json=tokens(marker="renewed"))
        sent.append(req)
        if status == 401 and len(sent) == 2:
            return stream_response(completed())
        return httpx.Response(status, json={"error": {"message": "denied " + account.value["access_token"]}})
    mock_http(monkeypatch, handler)
    if status == 401:
        assert execute(account, settings(account), request(), cancel_checker=None, progress=lambda _: None)["message"]["content"] == "你好"
    else:
        plugin = CodexPlugin()
        plugin.context = SimpleNamespace(exception_diagnostics=PluginContext.exception_diagnostics)
        plugin.profiles = SimpleNamespace(account_for=lambda _: account)
        plugin.changed = threading.Condition()
        job = Job("request", ("consumer", "scope"), settings(account), {"request": request()})
        plugin._run(job)
        assert job.state == "failed"
        assert account.value["access_token"] not in repr(job.failure)
        assert "denied" in repr(job.failure)
    assert len(sent) == expected
    assert len(refreshed) == (status == 401)


@pytest.mark.parametrize("events", [[], [{"type": "response.output_text.delta", "delta": "partial"}],
    [{"type": "response.failed", "response": {"status": "failed", "error": {"code": "server_error", "message": "upstream failed"}}}]])
def test_truncated_or_failed_stream_never_becomes_success(tmp_path, monkeypatch, events):
    account = account_at(tmp_path)
    mock_http(monkeypatch, lambda _: stream_response(*events))
    with pytest.raises(ModelError):
        execute(account, settings(account), request(), cancel_checker=None, progress=lambda _: None)


def test_catalog_comes_from_account_and_does_not_infer_image_support(tmp_path, monkeypatch):
    account = account_at(tmp_path)
    mock_http(monkeypatch, lambda req: httpx.Response(200, json={"models": [
        {"slug": "visible", "display_name": "Visible", "context_window": 200000, "input_modalities": ["text", "image"]},
        {"slug": "unknown"}, {"slug": "hidden", "visibility": "hide"}]}))
    result = execute(account, settings(account), {}, cancel_checker=None, progress=lambda _: None, operation="list_models")
    assert result["models"] == [{"modelId": "visible", "label": "Visible", "contextWindowTokens": 200000, "inputModalities": ["text", "image"]},
                                {"modelId": "unknown", "label": "unknown"}]


@pytest.mark.parametrize("data, expected", [
    ({"rate_limit": {"primary_window": {"used_percent": 25, "limit_window_seconds": 18000, "reset_at": 1800000000},
                     "secondary_window": {"used_percent": 0, "limit_window_seconds": 604800, "reset_at": 1800100000}},
      "additional_rate_limits": [{"limit_name": "额外模型", "rate_limit": {
          "primary_window": {"used_percent": 100, "limit_window_seconds": 3600, "reset_at": 1800000001}}}]},
     [{"label": "", "usedPercent": 25, "windowSeconds": 18000, "resetsAt": 1800000000},
      {"label": "", "usedPercent": 0, "windowSeconds": 604800, "resetsAt": 1800100000},
      {"label": "额外模型", "usedPercent": 100, "windowSeconds": 3600, "resetsAt": 1800000001}]),
    ({"rate_limit": None}, []),
    ({"rate_limit": {"primary_window": {"limit_window_seconds": 18000}}},
     [{"label": "", "usedPercent": None, "windowSeconds": 18000, "resetsAt": None}]),
])
def test_usage_queries_the_selected_account_and_preserves_available_windows(tmp_path, monkeypatch, data, expected):
    account = account_at(tmp_path)
    def handler(req):
        assert req.method == "GET"
        assert req.url == "https://chatgpt.com/backend-api/wham/usage"
        assert req.headers["chatgpt-account-id"] == "account-a"
        assert req.headers["authorization"] == "Bearer " + account.value["access_token"]
        return httpx.Response(200, json=data)
    mock_http(monkeypatch, handler)
    before = time.time()
    result = execute(account, settings(account), {}, cancel_checker=None, progress=lambda _: None, operation="usage")
    assert result["usage"]["windows"] == expected
    assert before <= result["usage"]["updatedAt"] <= time.time()


def test_http_success_with_catalog_error_preserves_upstream_reason(tmp_path, monkeypatch):
    account = account_at(tmp_path)
    mock_http(monkeypatch, lambda _: httpx.Response(200, json={"error": {"message": "catalog denied " + account.value["access_token"]}}))
    with pytest.raises(ModelError, match="catalog denied") as caught:
        execute(account, settings(account), {}, cancel_checker=None, progress=lambda _: None, operation="list_models")
    assert account.value["access_token"] not in str(caught.value)


@pytest.mark.parametrize("event_type", ["response.completed", "response.done"])
def test_terminal_events_return_authoritative_text(tmp_path, monkeypatch, event_type):
    account = account_at(tmp_path)
    event = {**completed(), "type": event_type}
    mock_http(monkeypatch, lambda _: stream_response({"type": "response.output_text.delta", "delta": "你好"}, event))
    progress = []
    result = execute(account, settings(account), request(), cancel_checker=None, progress=progress.append)
    assert result["message"]["content"] == "你好"
    assert "".join(value["text"] for value in progress) == "你好"


def test_cancel_interrupts_waiting_response_and_closes_stream(tmp_path, monkeypatch):
    account = account_at(tmp_path)
    entered, cancelled, closed = threading.Event(), threading.Event(), threading.Event()
    class Stream(httpx.AsyncByteStream):
        async def __aiter__(self):
            entered.set()
            await asyncio.Event().wait()
            yield b""
        async def aclose(self):
            closed.set()
    mock_http(monkeypatch, lambda _: httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=Stream()))
    def check():
        if cancelled.is_set():
            raise OperationCancelled()
    with ThreadPoolExecutor() as executor:
        future = executor.submit(execute, account, settings(account), request(), cancel_checker=check, progress=lambda _: None)
        assert entered.wait(2)
        cancelled.set()
        with pytest.raises(OperationCancelled):
            future.result(2)
    assert closed.is_set()


class Config:
    def __init__(self):
        self.value = {"models": [{"modelId": "saved", "label": "saved"}], "timeout_seconds": 120, "future": "keep"}
    def get(self):
        return deepcopy(self.value)
    def replace(self, values):
        self.value = deepcopy(values)
        return self.callback(self.get())
    def on_change(self, callback):
        self.callback = callback
    def update(self, patch):
        self.value.update(deepcopy(patch))
        return self.callback(self.get())


def settings_profiles(tmp_path):
    config = Config()
    context = SimpleNamespace(config=config, data_path=lambda relative: tmp_path / relative, effect=lambda _: None,
        get=lambda _: SimpleNamespace(info=lambda *a, **k: None, warning=lambda *a, **k: None),
        exception_diagnostics=PluginContext.exception_diagnostics)
    profiles = CodexProfiles(context)
    return profiles, config


def test_each_add_owns_its_account_models_and_survives_other_profile_removal(tmp_path, monkeypatch):
    profiles, config = settings_profiles(tmp_path)
    first = profiles.add({"operationRequest": {"createdAt": 100}})["values"]["profiles"][-1]
    second = profiles.add({"operationRequest": {"createdAt": 200}})["values"]["profiles"][-1]
    first_id, second_id = first["profileId"], second["profileId"]
    assert first_id != second_id
    assert first["label"] != second["label"]
    for identity in (first_id, second_id):
        account = profiles.account_for(identity)
        monkeypatch.setattr(account, "login", lambda *_, account=account, identity=identity: account._save(credentials(tokens(account=identity))))
    profiles.set_service(SimpleNamespace(execute=lambda settings, *a, **k: {"models": [{"modelId": settings["profileId"]}]}))
    try:
        for identity in (first_id, second_id):
            profiles.start({"operationRequest": {"profileId": identity, "requestId": identity, "operation": "login"}})
        for identity in (first_id, second_id):
            profiles.operations[identity].worker.join(2)
            assert profiles.operations[identity].result["state"] == "completed"
            assert profiles.resolve(identity, identity)["account"]["tokens"]["account_id"] == identity
        profiles.rename({"operationRequest": {"profileId": first_id, "label": "工作账号"}})
        second_credentials = profiles.account_for(second_id).path.read_bytes()
        profiles.logout({"operationRequest": {"profileId": first_id}})
        assert profiles.account_for(second_id).path.read_bytes() == second_credentials
        profiles.remove({"operationRequest": {"profileId": first_id}})
        profiles.close()
        restored = CodexProfiles(profiles.context)
        try:
            assert restored.resolve(second_id, second_id)["account"]["tokens"]["account_id"] == second_id
            assert restored.profile(second_id)["createdAt"] == 200
            with pytest.raises(ModelError):
                restored.resolve(first_id, first_id)
        finally:
            restored.close()
    finally:
        profiles.close()


def test_existing_single_account_keeps_identity_credentials_and_model_selection(tmp_path):
    account = account_at(tmp_path)
    original_credentials = account.path.read_bytes()
    profiles, config = settings_profiles(tmp_path)
    try:
        resolved = profiles.resolve("chatgpt", "saved")
        assert resolved["account"]["tokens"]["account_id"] == "account-a"
        assert config.get()["future"] == "keep"
        profiles.add({})
        assert account.path.read_bytes() == original_credentials
        assert profiles.catalog()[0]["profileId"] == "chatgpt"
        profiles.remove({"operationRequest": {"profileId": "chatgpt"}})
        profiles.close()
        restored = CodexProfiles(profiles.context)
        try:
            assert len(restored.catalog()) == 1
            assert not restored.load()["profiles"][0]["account"]["loggedIn"]
        finally:
            restored.close()
    finally:
        profiles.close()


def test_login_automatically_saves_all_models_and_keeps_other_configuration(tmp_path, monkeypatch):
    profiles, config = settings_profiles(tmp_path)
    monkeypatch.setattr(profiles.account_for(PROFILE_ID), "login", lambda *_: profiles.account_for(PROFILE_ID)._save(credentials(tokens())))
    calls = []
    def execute_probe(settings, request, **kwargs):
        calls.append(deepcopy(settings))
        return {"models": [{"modelId": "first", "label": "First", "contextWindowTokens": 200000},
                           {"modelId": "second", "label": "Second", "inputModalities": ["text", "image"]}]}
    profiles.set_service(SimpleNamespace(execute=execute_probe))
    try:
        config.update({"timeout_seconds": 23.5})
        profiles.start({"operationRequest": {"profileId": PROFILE_ID, "requestId": "login", "operation": "login"}})
        profiles.operations[PROFILE_ID].worker.join(2)
        result = profiles.status({})["values"]["profiles"][0]
        assert result["operationResult"]["state"] == "completed"
        assert result["account"]["loggedIn"]
        assert [m["modelId"] for m in result["models"]] == ["first", "second"]
        assert config.get()["profiles"][0]["models"] == result["models"] == profiles.catalog()[0]["models"]
        assert calls[0]["timeout_seconds"] == 23.5
        assert config.get()["future"] == "keep"
        assert profiles.resolve(PROFILE_ID, "second")["timeout_seconds"] == 23.5
        assert profiles.describe(PROFILE_ID, "first")["contextWindowTokens"] == 200000
        assert profiles.describe(PROFILE_ID, "second")["inputModalities"] == ["text", "image"]
        assert "private-refresh" not in repr(profiles.load())
        profiles.logout({"operationRequest": {"profileId": PROFILE_ID}})
        assert profiles.catalog()[0]["models"] == result["models"]
        with pytest.raises(ModelError, match="请先登录"):
            profiles.resolve(PROFILE_ID, "second")
    finally:
        profiles.close()


def test_failed_sync_keeps_login_and_previous_models_for_retry(tmp_path, monkeypatch):
    profiles, config = settings_profiles(tmp_path)
    monkeypatch.setattr(profiles.account_for(PROFILE_ID), "login", lambda *_: profiles.account_for(PROFILE_ID)._save(credentials(tokens())))
    def fail(*args, **kwargs):
        raise ModelError("CATALOG_UNAVAILABLE", "目录暂不可用")
    profiles.set_service(SimpleNamespace(execute=fail))
    before = config.get()
    try:
        profiles.start({"operationRequest": {"profileId": PROFILE_ID, "requestId": "login", "operation": "login"}})
        profiles.operations[PROFILE_ID].worker.join(2)
        assert profiles.status({})["values"]["profiles"][0]["operationResult"]["state"] == "failed"
        assert profiles.account_for(PROFILE_ID).public()["loggedIn"]
        assert config.get() == before
        profiles.service.execute = lambda *a, **k: {"models": [{"modelId": "recovered"}]}
        profiles.start({"operationRequest": {"profileId": PROFILE_ID, "requestId": "retry", "operation": "sync_models"}})
        profiles.operations[PROFILE_ID].worker.join(2)
        assert profiles.status({})["values"]["profiles"][0]["operationResult"]["state"] == "completed"
        assert profiles.catalog()[0]["models"][0]["modelId"] == "recovered"
    finally:
        profiles.close()


@pytest.mark.parametrize("exit_action", ["cancel", "logout"])
@pytest.mark.parametrize("operation", ["sync_models", "usage", "test_connection"])
def test_cancelled_account_request_does_not_publish_late_data(tmp_path, exit_action, operation):
    profiles, config = settings_profiles(tmp_path)
    profiles.account_for(PROFILE_ID)._save(credentials(tokens()))
    entered, release = threading.Event(), threading.Event()
    def execute_probe(*args, **kwargs):
        entered.set()
        assert release.wait(2)
        return {"models": [{"modelId": "late"}], "usage": {"windows": [], "updatedAt": 1}, "message": {"content": "late reply"}}
    profiles.set_service(SimpleNamespace(execute=execute_probe))
    before = config.get()
    try:
        profiles.start({"operationRequest": {"profileId": PROFILE_ID, "requestId": "sync", "operation": operation, "modelId": "saved"}})
        assert entered.wait(2)
        getattr(profiles, exit_action)({"operationRequest": {"profileId": PROFILE_ID, "requestId": "sync"}})
        release.set()
        profiles.operations[PROFILE_ID].worker.join(2)
        assert profiles.status({})["values"]["profiles"][0]["operationResult"]["state"] == "cancelled"
        assert config.get() == before
        assert profiles.status({})["values"]["profiles"][0]["usage"] is None
        assert profiles.account_for(PROFILE_ID).public()["loggedIn"] == (exit_action == "cancel")
    finally:
        release.set()
        profiles.close()


def test_failed_usage_refresh_keeps_each_accounts_last_reading(tmp_path):
    profiles, config = settings_profiles(tmp_path)
    second = profiles.add({})["values"]["profiles"][-1]["profileId"]
    for identity in (PROFILE_ID, second):
        profiles.account_for(identity)._save(credentials(tokens(account=identity)))
    readings = {identity: {"windows": [{"usedPercent": index * 50}], "updatedAt": index + 1}
                for index, identity in enumerate((PROFILE_ID, second))}
    profiles.set_service(SimpleNamespace(execute=lambda settings, *a, **k: {"usage": readings[settings["profileId"]]}))
    before = config.get()
    try:
        for identity in (PROFILE_ID, second):
            profiles.start({"operationRequest": {"profileId": identity, "requestId": identity, "operation": "usage"}})
            profiles.operations[identity].worker.join(2)
        def fail(*args, **kwargs):
            raise ModelError("MODEL_HTTP_FAILED", "额度服务暂不可用")
        profiles.service.execute = fail
        profiles.start({"operationRequest": {"profileId": PROFILE_ID, "requestId": "refresh", "operation": "usage"}})
        profiles.operations[PROFILE_ID].worker.join(2)
        values = profiles.load()["profiles"]
        assert values[0]["operationResult"]["state"] == "failed"
        assert "额度服务暂不可用" in values[0]["operationResult"]["message"]
        assert {p["profileId"]: p["usage"] for p in values} == readings
        assert config.get() == before
        profiles.logout({"operationRequest": {"profileId": PROFILE_ID}})
        assert profiles.load()["profiles"][0]["usage"] is None
        assert profiles.load()["profiles"][1]["usage"] == readings[second]
    finally:
        profiles.close()


def test_model_test_uses_selected_model_without_changing_configuration(tmp_path):
    profiles, config = settings_profiles(tmp_path)
    profiles.account_for(PROFILE_ID)._save(credentials(tokens()))
    calls = []
    def probe(settings, request, **kwargs):
        calls.append((settings["model"], kwargs["operation"]))
        return {"message": {"content": "OK"}}
    profiles.set_service(SimpleNamespace(execute=probe))
    before = config.get()
    try:
        profiles.start({"operationRequest": {"profileId": PROFILE_ID, "requestId": "test", "operation": "test_connection", "modelId": "chosen-model"}})
        profiles.operations[PROFILE_ID].worker.join(2)
        result = profiles.load()["profiles"][0]["operationResult"]
        assert result["state"] == "completed"
        assert result["modelId"] == "chosen-model" and result["reply"] == "OK"
        assert result["elapsedMs"] >= 0
        assert calls == [("chosen-model", "test_connection")]
        assert config.get() == before
    finally:
        profiles.close()


def test_cancelled_account_operation_can_restart_before_old_worker_finishes(tmp_path):
    profiles, config = settings_profiles(tmp_path)
    profiles.account_for(PROFILE_ID)._save(credentials(tokens()))
    entered, release = threading.Event(), threading.Event()
    def slow_sync(*args, **kwargs):
        entered.set()
        assert release.wait(2)
        return {"models": [{"modelId": "late"}]}
    profiles.set_service(SimpleNamespace(execute=slow_sync))
    try:
        profiles.start({"operationRequest": {"profileId": PROFILE_ID, "requestId": "old", "operation": "sync_models"}})
        assert entered.wait(2)
        old = profiles.operations[PROFILE_ID]
        profiles.cancel({"operationRequest": {"profileId": PROFILE_ID, "requestId": "old"}})
        profiles.service.execute = lambda *a, **k: {"models": [{"modelId": "current"}]}
        profiles.start({"operationRequest": {"profileId": PROFILE_ID, "requestId": "new", "operation": "sync_models"}})
        profiles.operations[PROFILE_ID].worker.join(2)
        assert profiles.status({})["values"]["profiles"][0]["operationResult"]["state"] == "completed"
        saved = config.get()
        assert saved["profiles"][0]["models"] == [{"modelId": "current"}]
        release.set()
        old.worker.join(2)
        assert config.get() == saved
        assert profiles.status({})["values"]["profiles"][0]["operationResult"]["requestId"] == "new"
    finally:
        release.set()
        profiles.close()
