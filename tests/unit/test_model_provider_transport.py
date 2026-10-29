from __future__ import annotations

import asyncio
import json
import threading
import traceback
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest

from plugins.builtin.sakura_model_openai_compatible.transport import execute
from sakura_cancellation import OperationCancelled
from sakura_model import ModelError
from sakura_model_provider import Job, ModelProvider
from app.plugins.sakura_plugin_sdk import PluginContext


SETTINGS = {"base_url": "https://fixture.invalid/v1", "api_key": "opaque-secret", "model": "fixture", "timeout_seconds": 5}
REQUEST = {"messages": [{"role": "user", "content": "Hello"}], "parameters": {"temperature": .8}, "responseFormat": {"type": "json_object"}}


def mock_http(monkeypatch, handler):
    class Client(httpx.AsyncClient):
        def __init__(self, **kwargs):
            kwargs.pop("proxy", None)
            super().__init__(**kwargs, transport=httpx.MockTransport(handler))
    monkeypatch.setattr(httpx, "AsyncClient", Client)


def run(settings=SETTINGS, request=REQUEST, **kwargs):
    return execute(settings, request, cancel_checker=kwargs.pop("cancel_checker", None), progress=kwargs.pop("progress", lambda _event: None), **kwargs)


def test_generation_forwards_sdk_parameters_to_the_provider(monkeypatch):
    received = []
    def handler(request):
        received.append(json.loads(request.content))
        return httpx.Response(200, json={"choices": [{"message": {"content": "OK"}}]})
    mock_http(monkeypatch, handler)
    result = run(request={"messages": REQUEST["messages"], "parameters": {"reasoning_effort": "high", "parallel_tool_calls": False}})
    assert received[0]["reasoning_effort"] == "high"
    assert received[0]["parallel_tool_calls"] is False
    assert result["message"]["content"] == "OK"


@pytest.fixture
def job_provider():
    from types import SimpleNamespace
    provider = ModelProvider()
    context = SimpleNamespace(caller_id="consumer", caller_scope="scope", effect=lambda _: None, on=lambda *a: None,
        provide=lambda *a, **k: None, get=lambda _: SimpleNamespace(register_provider=lambda *a, **k: None),
        exception_diagnostics=PluginContext.exception_diagnostics)
    profiles = SimpleNamespace(set_service=lambda _: None, register_settings=lambda: None, catalog=lambda: [],
                               resolve=lambda *a: {})
    provider.setup(context, profiles=profiles, service_key="fixture", label="Fixture")
    yield provider
    provider.close()


def test_concurrent_requests_each_finish_and_release_their_result(job_provider):
    ready = threading.Event()
    def execute(_settings, request, **kwargs):
        ready.wait()
        return {"message": {"content": request["text"]}}
    job_provider.execute = execute
    identities = [f"request-{number}" for number in range(140)]
    try:
        for identity in identities:
            job_provider.begin({"operationId": identity, "profileId": "profile", "modelId": "model", "request": {"text": identity}})
        ready.set()
        for identity in identities:
            while job_provider.poll(identity)["state"] == "running":
                pass
            assert job_provider.result(identity)["response"]["message"]["content"] == identity
            assert job_provider.release(identity)["released"]
    finally:
        ready.set()


def test_many_cancelled_starts_leave_the_consumer_usable(job_provider):
    job_provider.execute = lambda _settings, request, **kwargs: {"message": {"content": request["text"]}}
    for number in range(4100):
        job_provider.cancel(f"cancelled-{number}")
    with pytest.raises(OperationCancelled):
        job_provider.begin({"operationId": "cancelled-0", "profileId": "profile", "modelId": "model", "request": {}})
    job_provider.begin({"operationId": "new-request", "profileId": "profile", "modelId": "model", "request": {"text": "OK"}})
    while job_provider.poll("new-request")["state"] == "running":
        pass
    assert job_provider.result("new-request")["response"]["message"]["content"] == "OK"
    assert job_provider.release("new-request")["released"]


@pytest.mark.parametrize("payload", [b'{"messages":[]}', b"{"])
def test_request_cleanup_failure_keeps_generation_or_original_parse_error(job_provider, tmp_path, payload):
    from types import SimpleNamespace
    path = tmp_path / "request.json"
    path.write_bytes(payload)
    logs = []
    def release_received(*args, **kwargs):
        raise TimeoutError("request cleanup unavailable")
    job_provider.artifacts = SimpleNamespace(resolve=lambda _: {
        "path": str(path), "delivery": {"senderId": "consumer", "senderScope": "scope", "operationId": "job"}},
        release_received=release_received)
    job_provider.context.get = lambda _: SimpleNamespace(warning=lambda message, **kwargs: logs.append((message, kwargs)))
    job_provider.execute = lambda *a, **k: {"message": {"content": "OK"}}
    job = Job("job", ("consumer", "scope"), {}, {"requestArtifact": {"artifactId": "request"}})
    job_provider._run(job)
    if payload == b"{":
        assert job.state == "failed"
        assert "JSONDecodeError" in str(job.failure)
    else:
        assert job.state == "completed"
        assert job.response["response"]["message"]["content"] == "OK"
    assert "request cleanup unavailable" in str(logs)


@pytest.mark.parametrize("operation", ["list_models", "test_connection", "generate"])
def test_keyless_network_endpoint_uses_the_server_authentication_policy(monkeypatch, operation):
    requests = []
    def handler(request):
        requests.append(request)
        return httpx.Response(200, json={"data": [{"id": "fixture"}]} if operation == "list_models"
                              else {"choices": [{"message": {"content": "OK"}}]})
    mock_http(monkeypatch, handler)
    settings = {**SETTINGS, "base_url": "http://192.168.1.20:8000/v1", "api_key": ""}
    result = run(settings, operation=operation)
    assert len(requests) == 1
    assert "Authorization" not in requests[0].headers
    assert result["models"] == ["fixture"] if operation == "list_models" else result["message"]["content"] == "OK"


def test_async_model_worker_preserves_cause_before_releasing_credentials(monkeypatch):
    from types import SimpleNamespace
    from plugins.builtin.sakura_model_openai_compatible import plugin, transport
    def fail(*args, **kwargs):
        raise OSError('connection reset at C:\\runtime\\model opaque-secret')
    monkeypatch.setattr(transport, 'execute', fail)
    worker = plugin.ModelPlugin()
    worker.context = SimpleNamespace(exception_diagnostics=PluginContext.exception_diagnostics)
    worker.changed = threading.Condition()
    worker._read_request = lambda job: REQUEST
    job = Job('failure', ('owner', 'scope'), dict(SETTINGS), {})
    worker._run(job)
    assert job.state == 'failed'
    assert not job.settings
    assert 'connection reset at C:\\runtime\\model' in job.failure['message']
    assert 'OSError' in job.failure['diagnostics']['exception_stack']
    assert 'opaque-secret' not in json.dumps(job.failure)


@pytest.mark.parametrize("temperature_error", [
    "temperature is not supported",
    "invalid temperature: only 1 is allowed for this model",
    "temperature does not support 0.8 with this model. Only the default (1) value is supported.",
])
def test_parameter_fallback_is_bounded_and_does_not_mutate_request(monkeypatch, temperature_error):
    calls = []
    def handler(request):
        body = json.loads(request.content)
        calls.append(body)
        for parameter in ("response_format", "temperature"):
            if parameter in body:
                message = temperature_error if parameter == "temperature" else parameter + " is not supported"
                return httpx.Response(400, json={"error": {"message": message}})
        return httpx.Response(200, json={"choices": [{"message": {"content": "OK"}}]})
    mock_http(monkeypatch, handler)
    result = run()
    assert result["message"]["content"] == "OK"
    assert result["diagnostics"]["attemptCount"] == 3
    assert len(calls) == 3
    assert "temperature" in REQUEST["parameters"] and REQUEST["responseFormat"]


@pytest.mark.parametrize("with_tools", [False, True])
def test_schema_falls_back_only_after_explicit_provider_rejection(monkeypatch, with_tools):
    calls = []
    def handler(request):
        body = json.loads(request.content)
        calls.append(body)
        format = body.get("response_format", {}).get("type")
        if format in {"json_schema", "json_object"}:
            return httpx.Response(400, json={"error": {"message": f"response_format {format} is not supported"}})
        if "temperature" in body:
            return httpx.Response(400, json={"error": {"message": "temperature is not supported"}})
        return httpx.Response(200, json={"choices": [{"message": {"content": "OK"}}]})
    mock_http(monkeypatch, handler)
    request = {**REQUEST, "responseFormat": {"type": "json_schema", "json_schema": {
        "name": "reply", "strict": True, "schema": {"type": "object", "properties": {}, "additionalProperties": False},
    }}}
    if with_tools:
        request["tools"] = [{"name": "search", "parameters": {"type": "object", "properties": {}}}]
    result = run(request=request)
    formats = [body.get("response_format", {}).get("type") for body in calls]
    assert formats == (["json_schema", None, None] if with_tools else ["json_schema", "json_object", None, None])
    assert all(body["messages"] == request["messages"] for body in calls)
    assert result["message"]["content"] == "OK"
    assert result["diagnostics"]["responseFormat"] == "text"
    assert request["responseFormat"]["type"] == "json_schema"


@pytest.mark.parametrize("status,message", [(401, "response_format unsupported"), (429, "try later"), (503, "unavailable"), (400, "temperature must be between 0 and 2"), (400, "Invalid schema for response_format: additionalProperties is not allowed")])
def test_http_failure_is_not_replayed_and_credentials_stay_private(monkeypatch, status, message):
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(status, json={"error": {"message": message + " opaque-secret"}})
    mock_http(monkeypatch, handler)
    with pytest.raises(ModelError) as caught:
        run()
    assert len(calls) == 1
    assert caught.value.status_code == status
    assert "opaque-secret" not in str(caught.value)
    from sakura_provider_errors import provider_exception_diagnostics
    assert caught.value.__cause__ is not None
    details = provider_exception_diagnostics(caught.value)
    assert "opaque-secret" not in json.dumps(details)
    assert type(caught.value.__cause__).__name__ in details["exception_chain"]
    evidence = PluginContext.exception_diagnostics(caught.value, secrets=(SETTINGS["api_key"],))
    assert message in evidence["diagnostic"]
    assert "opaque-secret" not in json.dumps(evidence)
    assert "openai" in evidence["exception_stack"]


@pytest.mark.parametrize("operation", ["generate", "test_connection", "list_models"])
@pytest.mark.parametrize("error", [
    {"message": "quota exhausted opaque-secret", "code": "insufficient_quota", "param": "model", "request_id": "req-quota", "request": "private-request"},
    "quota exhausted opaque-secret",
])
def test_success_status_with_provider_error_preserves_safe_reason(monkeypatch, operation, error):
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(200, json={"error": error, "messages": ["private-conversation"]})
    mock_http(monkeypatch, handler)
    with pytest.raises(ModelError) as caught:
        run(operation=operation)
    failure = caught.value
    assert failure.code == "MODEL_REQUEST_FAILED"
    assert "quota exhausted" in str(failure)
    if isinstance(error, dict):
        assert "insufficient_quota" in str(failure)
        assert "param: model" in str(failure)
        assert "request_id: req-quota" in str(failure)
    assert failure.diagnostics == {"attemptCount": 1, "httpStatus": 200, "faultDomain": "provider", "stage": "response"}
    observed = "".join(traceback.format_exception(failure)) + repr(failure.diagnostics)
    assert all(value not in observed for value in ("opaque-secret", "private-request", "private-conversation"))
    assert len(calls) == 1


@pytest.mark.parametrize("data,field,detail", [
    ({"output": "private-conversation"}, "choices", "缺少"),
    ({"choices": []}, "choices", "空数组"),
    ({"choices": None}, "choices", "NoneType"),
    ({"choices": [{"message": "private-conversation"}]}, "choices[0].message", "str"),
    ({"choices": [{"message": {"tool_calls": [None]}}]}, "choices[0].message.tool_calls[0]", "NoneType"),
    ({"choices": [{"message": {"tool_calls": [{"id": "call", "function": {"name": "lookup"}}]}}]},
     "choices[0].message.tool_calls[0].function.arguments", "缺少"),
    ([], "$", "list"),
])
def test_malformed_completion_identifies_field_without_echoing_values(monkeypatch, data, field, detail):
    mock_http(monkeypatch, lambda _request: httpx.Response(200, json=data))
    with pytest.raises(ModelError) as caught:
        run()
    failure = caught.value
    assert failure.code == "MODEL_RESPONSE_INVALID"
    assert field in str(failure) and detail in str(failure)
    assert failure.diagnostics == {"attemptCount": 1, "httpStatus": 200, "faultDomain": "protocol", "stage": "decode", "validation_field": field}
    assert "private-conversation" not in "".join(traceback.format_exception(failure))


@pytest.mark.parametrize("operation", ["generate", "list_models"])
def test_non_json_response_keeps_decode_location_and_content_type_without_body(monkeypatch, operation):
    mock_http(monkeypatch, lambda _request: httpx.Response(200, text="<html>opaque-secret private-conversation</html>", headers={"Content-Type": "text/html"}))
    with pytest.raises(ModelError) as caught:
        run(operation=operation)
    failure = caught.value
    assert failure.code == "MODEL_RESPONSE_INVALID"
    assert "JSON" in str(failure) and "line 1 column 1" in str(failure)
    assert "text/html" in str(failure)
    assert failure.diagnostics["httpStatus"] == 200
    assert failure.diagnostics["stage"] == "decode"
    observed = "".join(traceback.format_exception(failure))
    assert "opaque-secret" not in observed and "private-conversation" not in observed


@pytest.mark.parametrize("path", ["", "/v1", "/v1beta", "/v1/openai", "/v1beta/openai/"])
@pytest.mark.parametrize("operation", ["list_models", "test_connection"])
def test_google_endpoint_probes_share_provider_transport(monkeypatch, path, operation):
    requests = []
    def handler(request):
        requests.append(request)
        return httpx.Response(200, json={"data": [{"id": "z"}, {"id": "a"}, {"id": "z"}]} if operation == "list_models" else {"choices": [{"message": {"content": "OK"}}]})
    mock_http(monkeypatch, handler)
    result = run({**SETTINGS, "base_url": "https://generativelanguage.googleapis.com" + path}, {}, operation=operation)
    assert result["models"] == ["a", "z"] if operation == "list_models" else result["message"]["content"] == "OK"
    endpoint = "models" if operation == "list_models" else "chat/completions"
    assert str(requests[0].url) == "https://generativelanguage.googleapis.com/v1beta/openai/" + endpoint


@pytest.mark.parametrize("data,expected", [({"data": [None, 3, {"id": "  "}, {"id": " b "}, {"id": "a"}, {"id": "a"}]}, ["a", "b"]),
                                        ({"data": "bad"}, None), ({}, None)])
def test_model_list_ignores_invalid_entries_but_rejects_invalid_envelopes(monkeypatch, data, expected):
    mock_http(monkeypatch, lambda _request: httpx.Response(200, json=data))
    if expected is None:
        with pytest.raises(ModelError) as caught:
            run(operation="list_models")
        assert caught.value.code == "MODEL_RESPONSE_INVALID"
    else:
        assert run(operation="list_models")["models"] == expected


def test_cancel_reclaims_the_pending_http_task(monkeypatch):
    entered, disposed, cancelled = threading.Event(), threading.Event(), threading.Event()
    async def handler(_request):
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            disposed.set()
    mock_http(monkeypatch, handler)
    def check():
        if cancelled.is_set():
            raise OperationCancelled()
    with ThreadPoolExecutor() as executor:
        pending = executor.submit(run, cancel_checker=check)
        assert entered.wait(2)
        cancelled.set()
        with pytest.raises(OperationCancelled):
            pending.result(timeout=2)
    assert disposed.is_set()


@pytest.mark.parametrize("error_type,code,stage", [(httpx.ConnectTimeout, "MODEL_CONNECTION_TIMEOUT", "connect"),
                                                 (httpx.ReadTimeout, "MODEL_READ_TIMEOUT", "read")])
def test_provider_timeout_keeps_the_failure_stage_and_original_cause(monkeypatch, error_type, code, stage):
    def handler(request):
        raise error_type("upstream socket timed out opaque-secret", request=request)
    mock_http(monkeypatch, handler)
    with pytest.raises(ModelError) as caught:
        run()
    assert caught.value.code == code
    assert caught.value.diagnostics["stage"] == stage
    assert caught.value.diagnostics["attemptCount"] == 1
    from sakura_provider_errors import provider_exception_diagnostics
    assert caught.value.__cause__ is not None
    details = provider_exception_diagnostics(caught.value)
    assert "opaque-secret" not in json.dumps(details)
    assert type(caught.value.__cause__).__name__ in details["exception_chain"]
    assert isinstance(caught.value.__cause__.__cause__, error_type)
    evidence = PluginContext.exception_diagnostics(caught.value, secrets=(SETTINGS["api_key"],))
    assert "upstream socket timed out" in evidence["diagnostic"]
    assert error_type.__name__ in evidence["exception_chain"]
    assert "opaque-secret" not in json.dumps(evidence)


def test_native_tool_arguments_and_opaque_continuation_metadata_roundtrip(monkeypatch):
    calls = []
    tool = {"id": "call-1", "type": "function", "function": {"name": "lookup", "arguments": "{broken"}, "extra_content": {"google": {"thought_signature": "signature"}}}
    def handler(request):
        calls.append(json.loads(request.content))
        return httpx.Response(200, json={"choices": [{"message": {"content": None, "tool_calls": [tool]}}]})
    mock_http(monkeypatch, handler)
    response = run()
    assert response["message"]["toolCalls"][0]["arguments"] == "{broken"
    run(request={"messages": [response["message"]]})
    assert calls[-1]["messages"][0]["tool_calls"] == [tool]


def test_streaming_batches_large_network_deltas_without_losing_final_content(monkeypatch):
    text = "界" * 12000
    chunks = [
        {"id": "stream", "object": "chat.completion.chunk", "created": 0, "model": "fixture",
         "choices": [{"index": 0, "delta": {"role": "assistant", "content": text}, "finish_reason": None}]},
        {"id": "stream", "object": "chat.completion.chunk", "created": 0, "model": "fixture",
         "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]},
    ]
    data = "".join("data: " + json.dumps(chunk, ensure_ascii=False) + "\n\n" for chunk in chunks) + "data: [DONE]\n\n"
    mock_http(monkeypatch, lambda _request: httpx.Response(200, headers={"Content-Type": "text/event-stream"}, content=data.encode()))
    events = []
    result = run(request={"messages": [{"role": "user", "content": "hello"}], "stream": True}, progress=events.append)
    assert result["message"]["content"] == text
    assert "".join(event["text"] for event in events) == text
    assert all(len(event["text"]) <= 2048 for event in events)


@pytest.mark.parametrize("count", [100, 180])
def test_poll_drains_retained_progress_in_bounded_batches_before_terminal_state(count):
    from types import SimpleNamespace
    from plugins.builtin.sakura_model_openai_compatible.plugin import ModelPlugin
    plugin = ModelPlugin()
    plugin.context = SimpleNamespace(caller_id="consumer", caller_scope="scope")
    plugin.changed = threading.Condition()
    job = Job("job", ("consumer", "scope"), {}, {}, state="completed", sequence=count)
    for index in range(1, count + 1):
        job.progress.append((index, {"type": "text_delta", "text": str(index)}))
    plugin.jobs = {(("consumer", "scope"), "job"): job}
    sequence, received, truncated = 0, [], False
    while True:
        result = plugin.poll("job", sequence, 0)
        assert len(result["progress"]) <= 32
        received.extend(int(item["text"]) for item in result["progress"])
        sequence = result["sequence"]
        truncated |= result["truncated"]
        if result["state"] != "running":
            break
    assert received == list(range(max(1, count - 127), count + 1))
    assert truncated == (count > 128)


@pytest.mark.parametrize("kind", ["filesystem", "connection", "http"])
def test_background_failure_retains_original_reason_frames_and_redacts_credentials(monkeypatch, kind):
    from types import SimpleNamespace
    from plugins.builtin.sakura_model_openai_compatible import plugin as provider
    from plugins.builtin.sakura_model_openai_compatible import transport
    from sakura_model_client import decode_model_result

    plugin = provider.ModelPlugin()
    plugin.context = SimpleNamespace(exception_diagnostics=PluginContext.exception_diagnostics)
    plugin.changed = threading.Condition()
    job = Job("job", ("consumer", "scope"), dict(SETTINGS), {"request": REQUEST})
    if kind == "filesystem":
        def execute(*args, **kwargs):
            raise PermissionError(13, "fixture disk is locked opaque-secret", "C:/model/cache/data.json")
        monkeypatch.setattr(transport, "execute", execute)
        expected = "fixture disk is locked"
    elif kind == "connection":
        def handler(request):
            raise httpx.ConnectError("TLS certificate verification failed opaque-secret", request=request)
        mock_http(monkeypatch, handler)
        expected = "TLS certificate verification failed"
    else:
        mock_http(monkeypatch, lambda request: httpx.Response(403, json={"error": {"message": "project access denied opaque-secret", "param": "project", "request_id": "req-42"}, "choices": [{"message": {"content": "private-conversation"}}]}))
        expected = "project access denied"
    plugin._run(job)
    assert job.state == "failed" and job.settings == {} and job.descriptor == {}
    with pytest.raises(ModelError) as caught:
        decode_model_result({"failure": job.failure}, None, {}, job.operation_id)
    evidence = caught.value.diagnostics
    assert expected in str(caught.value) and expected in evidence["diagnostic"]
    assert expected in evidence["exception_stack"]
    assert evidence["exception_site"]
    assert "opaque-secret" not in json.dumps(job.failure)
    assert "private-conversation" not in json.dumps(job.failure)
    if kind == "http":
        assert "req-42" in evidence["diagnostic"]


def test_provider_diagnostic_keeps_urls_paths_newlines_and_long_error_tail():
    from sakura_provider_errors import sanitize_provider_diagnostic, public_provider_http_message
    text = "TLS failed at https://user:password@api.example/v1?token=private-token\nC:/models/voice; /opt/models/voice\n" + "x" * 600 + "\nupstream request req-42"
    result = sanitize_provider_diagnostic(text)
    assert "api.example/v1?token=[REDACTED]" in result
    assert "C:/models/voice" in result and "/opt/models/voice" in result
    assert "\nupstream request req-42" in result
    assert "user:password" not in result and "private-token" not in result
    assert "private-conversation" not in public_provider_http_message(RuntimeError('API HTTP 502: {"choices":[{"content":"private-conversation"}]}'), 502)
