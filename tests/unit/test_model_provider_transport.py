from __future__ import annotations

import asyncio
import json
import threading
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest

from plugins.builtin.sakura_model_openai_compatible.transport import execute
from sakura_cancellation import OperationCancelled
from sakura_model import ModelError


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


def test_parameter_fallback_is_bounded_and_does_not_mutate_request(monkeypatch):
    calls = []
    def handler(request):
        body = json.loads(request.content)
        calls.append(body)
        for parameter in ("response_format", "temperature"):
            if parameter in body:
                return httpx.Response(400, json={"error": {"message": parameter + " is not supported"}})
        return httpx.Response(200, json={"choices": [{"message": {"content": "OK"}}]})
    mock_http(monkeypatch, handler)
    result = run()
    assert result["message"]["content"] == "OK"
    assert result["diagnostics"]["attemptCount"] == 3
    assert len(calls) == 3
    assert "temperature" in REQUEST["parameters"] and REQUEST["responseFormat"]


@pytest.mark.parametrize("status,message", [(401, "response_format unsupported"), (429, "try later"), (503, "unavailable"), (400, "temperature must be between 0 and 2")])
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
    assert caught.value.__cause__ is None


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
def test_provider_timeout_keeps_the_failure_stage_without_transferring_sdk_errors(monkeypatch, error_type, code, stage):
    def handler(request):
        raise error_type("opaque-secret", request=request)
    mock_http(monkeypatch, handler)
    with pytest.raises(ModelError) as caught:
        run()
    assert caught.value.code == code
    assert caught.value.diagnostics["stage"] == stage
    assert caught.value.diagnostics["attemptCount"] == 1
    assert caught.value.__cause__ is None


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
    from plugins.builtin.sakura_model_openai_compatible.plugin import ModelPlugin, Job
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
