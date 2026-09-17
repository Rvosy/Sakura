from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import httpx
from openai import APIConnectionError, APIStatusError

from app.config.app_version import read_app_version
from sakura_assistant.agent.trace import AgentTraceRecorder
from sakura_assistant.llm.api_client import (
    MAX_COMPATIBILITY_ATTEMPTS,
    ApiRequestError,
    ApiSettings,
    OpenAICompatibleClient,
    _build_chat_completion_payload,
    _filter_supported_chat_params,
)
from sakura_assistant_contract import ChatReply, ChatSegment
from sakura_assistant.llm.chat_reply import parse_chat_reply, sanitize_reply_tones
from sakura_assistant.llm.prompts.runtime import ContextPolicy, PromptRuntime
from sakura_context import ContextFragment, ContextRequest, PromptRecipe


REPO_ROOT = Path(__file__).resolve().parents[2]


def mock_http(monkeypatch, handler):
    class MockClient(httpx.AsyncClient):
        def __init__(self, **kwargs):
            kwargs.pop("proxy", None)
            super().__init__(**kwargs, transport=httpx.MockTransport(handler))
    monkeypatch.setattr("httpx.AsyncClient", MockClient)


def test_sanitize_reply_tones_normalizes_out_of_set_tone() -> None:
    allowed = ["中性", "不满", "害羞", "请求", "惊讶"]
    reply = ChatReply(
        [
            ChatSegment("hi", "en", "你好", "站立待机"),
            ChatSegment("おはよ", "害羞", "早", "害羞"),
            ChatSegment("x", "坚定", "", ""),
        ]
    )

    out = sanitize_reply_tones(reply, allowed)

    assert [segment.tone for segment in out.segments] == ["中性", "害羞", "中性"]
    # 仅改 tone，文本/译文/立绘保持不变
    assert out.segments[0].text == "hi"
    assert out.segments[0].translation == "你好"
    assert out.segments[0].portrait == "站立待机"


def test_chat_param_filter_keeps_supported_values() -> None:
    filtered = _filter_supported_chat_params(
        {
            "temperature": 0.2,
            "max_tokens": 32,
            "max_completion_tokens": 64,
            "unsupported_internal_flag": True,
            "top_p": None,
        }
    )

    assert filtered == {
        "temperature": 0.2,
        "max_completion_tokens": 64,
    }


def test_build_chat_payload_drops_unsupported_params() -> None:
    payload = _build_chat_completion_payload(
        model="gpt-compatible",
        system_prompt=" system ",
        messages=[{"role": "user", "content": "hi"}],
        temperature=0.8,
        chat_params={"presence_penalty": 0.1, "bad": "ignored"},
    )

    assert payload["model"] == "gpt-compatible"
    assert payload["temperature"] == 0.8
    assert payload["presence_penalty"] == 0.1
    assert "bad" not in payload
    assert payload["messages"][0] == {"role": "system", "content": "system"}


def test_complete_raw_applies_param_filter(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    captured: dict[str, Any] = {}
    client = OpenAICompatibleClient(
        ApiSettings(
            base_url="https://api.example.com/v1",
            api_key="key",
            model="model",
        )
    )

    def fake_post(payload: dict[str, Any], **_kwargs: Any) -> dict[str, Any]:
        captured.update(payload)
        return {"choices": [{"message": {"content": "OK"}}]}

    monkeypatch.setattr(client, "_post_chat_completions", fake_post)

    assert client.complete_raw(
        "system",
        [{"role": "user", "content": "hello"}],
        temperature=0.1,
        unsupported_internal_flag=True,
        max_tokens=8,
    ) == "OK"

    assert captured["temperature"] == 0.1
    assert captured["max_tokens"] == 8
    assert "unsupported_internal_flag" not in captured


def test_complete_raw_does_not_log_request_body(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    events: list[tuple[str, dict[str, Any]]] = []
    client = OpenAICompatibleClient(
        ApiSettings(base_url="https://api.example.com/v1", api_key="key", model="model")
    )
    monkeypatch.setattr(
        client,
        "_post_chat_completions_with_compatibility_fallbacks",
        lambda *_args, **_kwargs: {"choices": [{"message": {"content": "OK"}}]},
    )
    monkeypatch.setattr(
        "sakura_assistant.llm.api_client.log_event",
        lambda _channel, message, attributes=None, **_kwargs: events.append((message, attributes or {})),
    )

    client.complete_raw("system prompt", [{"role": "user", "content": "full request"}])

    request = next(attributes for message, attributes in events if message == "准备发送聊天补全请求")
    assert "payload" not in request
    assert "messages" not in request


@pytest.mark.parametrize("method", ["complete_raw", "complete_with_tools"])
def test_runtime_role_fallback_preserves_unclassified_context(
    monkeypatch: pytest.MonkeyPatch, method: str,
) -> None:
    client = OpenAICompatibleClient(ApiSettings("https://example.invalid/v1", "fixture", "model"))
    snapshot = ContextPolicy().select(ContextRequest(), [
        ContextFragment(
            "rule", "plugin:fixture", "用简短的句子回答。",
            required=True,
        ),
        ContextFragment("reference", "plugin:fixture", "这是一段参考资料。"),
    ])
    runtime_context = PromptRuntime().build(PromptRecipe("fixture", []), snapshot).runtime_context
    calls: list[dict[str, Any]] = []

    def fake_post(payload: dict[str, Any], **_kwargs: Any) -> dict[str, Any]:
        calls.append(payload)
        if payload["messages"][-1]["role"] == "system":
            raise ApiRequestError("system messages must be first")
        return {"choices": [{"message": {"role": "assistant", "content": "OK"}}]}

    monkeypatch.setattr(client, "_post_chat_completions", fake_post)
    invoke = getattr(client, method)
    result = invoke("角色身份", [{"role": "user", "content": "你好"}], runtime_context=runtime_context)

    assert (result if method == "complete_raw" else result.content) == "OK"
    assert len(calls) == 2
    assert calls[0]["messages"][-1] == {"role": "system", "content": runtime_context}
    fallback = calls[1]["messages"][-1]
    assert fallback["role"] == "user"
    prefix, retained_context = fallback["content"].split("\n", 1)
    assert retained_context == runtime_context
    assert "用简短的句子回答。" in retained_context
    assert "这是一段参考资料。" in retained_context
    assert 'kind=' not in retained_context
    assert 'trust=' not in retained_context
    assert "facts" not in prefix.lower()
    assert "host" in prefix.lower()

    invoke("角色身份", [{"role": "user", "content": "继续"}], runtime_context=runtime_context)
    assert len(calls) == 3
    assert calls[-1]["messages"][-1] == fallback


def test_complete_raw_ignores_reasoning_content(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    client = OpenAICompatibleClient(
        ApiSettings(base_url="https://api.example.com/v1", api_key="key", model="model")
    )

    monkeypatch.setattr(
        client,
        "_post_chat_completions_with_compatibility_fallbacks",
        lambda *_args, **_kwargs: {
            "choices": [
                {
                    "message": {
                        "reasoning_content": '{"secret":"hidden"}',
                        "content": '{"segments":[]}',
                    }
                }
            ]
        },
    )

    assert client.complete_raw("system", [{"role": "user", "content": "hi"}]) == '{"segments":[]}'


def test_complete_raw_retries_without_temperature_when_provider_rejects(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    calls: list[dict[str, Any]] = []
    client = OpenAICompatibleClient(
        ApiSettings(
            base_url="https://api.example.com/v1",
            api_key="key",
            model="compatible-model",
        )
    )

    def fake_post(payload: dict[str, Any], **_kwargs: Any) -> dict[str, Any]:
        calls.append(dict(payload))
        if "temperature" in payload:
            raise ApiRequestError("Unsupported value: temperature only supports the default value")
        return {"choices": [{"message": {"content": "OK"}}]}

    monkeypatch.setattr(client, "_post_chat_completions", fake_post)

    assert client.complete_raw(
        "system",
        [{"role": "user", "content": "hello"}],
        temperature=0.8,
    ) == "OK"

    assert "temperature" in calls[0]
    assert "temperature" not in calls[1]


def test_complete_raw_remembers_temperature_unsupported(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    calls: list[dict[str, Any]] = []
    client = OpenAICompatibleClient(
        ApiSettings(
            base_url="https://api.example.com/v1",
            api_key="key",
            model="compatible-model",
        )
    )

    def fake_post(payload: dict[str, Any], **_kwargs: Any) -> dict[str, Any]:
        calls.append(dict(payload))
        if "temperature" in payload:
            raise ApiRequestError("temperature does not support non-default values")
        return {"choices": [{"message": {"content": "OK"}}]}

    monkeypatch.setattr(client, "_post_chat_completions", fake_post)

    client.complete_raw("system", [{"role": "user", "content": "hello"}], temperature=0.8)
    client.complete_raw("system", [{"role": "user", "content": "again"}], temperature=0.8)

    assert "temperature" in calls[0]
    assert "temperature" not in calls[1]
    assert "temperature" not in calls[2]


def test_update_settings_clears_cached_unsupported_params(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    calls: list[dict[str, Any]] = []
    client = OpenAICompatibleClient(
        ApiSettings(
            base_url="https://api.example.com/v1",
            api_key="key",
            model="old-model",
        )
    )

    def fake_post(payload: dict[str, Any], **_kwargs: Any) -> dict[str, Any]:
        calls.append(dict(payload))
        if len(calls) == 1:
            raise ApiRequestError("temperature only supports the default value")
        return {"choices": [{"message": {"content": "OK"}}]}

    monkeypatch.setattr(client, "_post_chat_completions", fake_post)

    client.complete_raw("system", [{"role": "user", "content": "hello"}], temperature=0.8)
    client.update_settings(
        ApiSettings(
            base_url="https://api.example.com/v1",
            api_key="key",
            model="new-model",
        )
    )
    client.complete_raw("system", [{"role": "user", "content": "again"}], temperature=0.8)

    assert "temperature" in calls[0]
    assert "temperature" not in calls[1]
    assert "temperature" in calls[2]


def test_complete_raw_requests_structured_json_by_default_for_chat(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    captured: dict[str, Any] = {}
    client = OpenAICompatibleClient(
        ApiSettings(
            base_url="https://api.example.com/v1",
            api_key="key",
            model="model",
        )
    )

    def fake_post(payload: dict[str, Any], **_kwargs: Any) -> dict[str, Any]:
        captured.update(payload)
        return {"choices": [{"message": {"content": '{"segments":[{"ja":"うん。","zh":"嗯。"}]}'}}]}

    monkeypatch.setattr(client, "_post_chat_completions", fake_post)

    client.chat("system", [{"role": "user", "content": "hello"}])

    assert captured["response_format"] == {"type": "json_object"}


def test_response_format_falls_back_when_provider_rejects(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    calls: list[dict[str, Any]] = []
    client = OpenAICompatibleClient(
        ApiSettings(
            base_url="https://api.example.com/v1",
            api_key="key",
            model="model",
        )
    )

    def fake_post(payload: dict[str, Any], **_kwargs: Any) -> dict[str, Any]:
        calls.append(dict(payload))
        if "response_format" in payload:
            raise ApiRequestError("unsupported response_format json_object")
        return {"choices": [{"message": {"content": "OK"}}]}

    monkeypatch.setattr(client, "_post_chat_completions", fake_post)

    assert client.complete_raw(
        "system",
        [{"role": "user", "content": "hello"}],
        response_format={"type": "json_object"},
    ) == "OK"

    assert "response_format" in calls[0]
    assert "response_format" not in calls[1]


@pytest.mark.parametrize("method", ["complete_raw", "complete_with_tools"])
def test_trailing_system_rejection_retries_and_records_compatibility(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, method: str,
) -> None:
    recorder = AgentTraceRecorder(tmp_path)
    client = OpenAICompatibleClient(
        ApiSettings("https://api.example.com/v1", "key", "model"),
        agent_trace_recorder=recorder,
    )
    payloads = []
    metrics = []

    def read_response(request):  # type: ignore[no-untyped-def]
        payload = json.loads(request.content)
        payloads.append(payload)
        if payload["messages"][-1]["role"] == "system":
            return httpx.Response(400, json={"error": {"message": "System message must be at the beginning."}})
        return httpx.Response(200, json={"choices": [{"message": {"role": "assistant", "content": "OK"}}]})

    mock_http(monkeypatch, read_response)
    monkeypatch.setattr("sakura_assistant.llm.api_client.submit_telemetry_model_call", metrics.append)

    with recorder.operation("trailing-system", finalize_external=True):
        for _ in range(2):
            reply = getattr(client, method)(
                "private system prompt", [{"role": "user", "content": "private user text"}],
                runtime_context="private runtime context",
            )
            assert (reply if method == "complete_raw" else reply.content) == "OK"

    assert [[message["role"] for message in payload["messages"]] for payload in payloads] == [
        ["system", "user", "system"], ["system", "user", "user"], ["system", "user", "user"],
    ]
    assert client.runtime_context_role == "user"
    assert [metric["outcome"] for metric in metrics] == ["failed", "success", "success"]
    assert metrics[0]["request"]["httpStatus"] == 400
    assert metrics[0]["request"]["faultDomain"] == "compatibility"
    assert metrics[0]["request"]["reasonCode"] == "MODEL_PARAMETER_UNSUPPORTED"
    assert metrics[1]["request"]["compatibilityFallback"] == "runtime_context_role"
    assert all(metric["operationId"] == "trailing-system" for metric in metrics)
    assert [metric["modelCall"] for metric in metrics] == [1, 2, 3]
    assert "private" not in json.dumps(metrics)


@pytest.mark.parametrize("method", ["complete_raw", "complete_with_tools"])
@pytest.mark.parametrize("scenario", ["no_runtime", "tail_runtime", "tool_runtime", "supported"])
def test_proactive_history_fallback_preserves_context_and_provider_defaults(
    monkeypatch: pytest.MonkeyPatch, method: str, scenario: str,
) -> None:
    from types import SimpleNamespace
    from sakura_assistant.agent.context_orchestrator import messages_for_context_snapshot
    from sakura_assistant.history import _ProjectedTurn, _TurnProjection, messages_from_turn_projection

    messages = messages_from_turn_projection(_TurnProjection(
        (_ProjectedTurn("conversation", (
            {"role": "user", "content": "previous user"},
            {"role": "assistant", "content": "previous assistant"},
        ), "conversation"),), (),
        (_ProjectedTurn("proactive", ({"role": "assistant", "content": "previous proactive reply"},), "proactive"),),
    ))
    messages.append({"role": "user", "content": "current user"})
    messages = messages_for_context_snapshot(
        messages, SimpleNamespace(selected_turns=[SimpleNamespace(turn_id="conversation")]),
    )
    if scenario == "tool_runtime":
        messages.extend([
            {"role": "assistant", "content": "", "tool_calls": [
                {"id": "call-1", "type": "function", "function": {"name": "fixture", "arguments": "{}"}},
            ]},
            {"role": "tool", "tool_call_id": "call-1", "content": "tool result"},
        ])
    original_messages = [dict(message) for message in messages]
    payloads = []

    def read_response(request):  # type: ignore[no-untyped-def]
        payload = json.loads(request.content)
        payloads.append(payload)
        if scenario != "supported" and any(message["role"] == "system" for message in payload["messages"][1:]):
            return httpx.Response(400, json={"error": {"message": "System message must be at the beginning."}})
        return httpx.Response(200, json={"choices": [{"message": {"role": "assistant", "content": "OK"}}]})

    mock_http(monkeypatch, read_response)
    client = OpenAICompatibleClient(ApiSettings("https://api.example.com/v1", "key", "model"))
    for _ in range(2):
        reply = getattr(client, method)(
            "primary system", messages, runtime_context="" if scenario == "no_runtime" else "runtime facts",
        )
        assert (reply if method == "complete_raw" else reply.content) == "OK"

    assert messages == original_messages
    assert payloads[0]["messages"][3]["role"] == "system"
    if scenario == "supported":
        assert len(payloads) == 2
        assert payloads[0] == payloads[1]
        assert client.runtime_context_role == "system"
    else:
        assert len(payloads) == 3
        assert payloads[1] == payloads[2]
        assert [message["role"] for message in payloads[1]["messages"]].count("system") == 1
        proactive = payloads[1]["messages"][3]
        assert proactive["role"] == "user"
        assert proactive["content"] == original_messages[2]["content"]
        assert "previous proactive reply" not in payloads[1]["messages"][0]["content"]
        assert client.runtime_context_role == "user"


@pytest.mark.parametrize("method", ["complete_raw", "complete_with_tools"])
@pytest.mark.parametrize(
    ("message", "runtime_context", "messages", "attempts"),
    [
        ("System message must be at the beginning.", "", [{"role": "user", "content": "hi"}], 1),
        ("Unknown model", "context", [{"role": "user", "content": "hi"}], 1),
        ("System message must be at the beginning.", "", [{"role": "system", "content": "unmarked input"}], 1),
        ("System message must be at the beginning.", "context", [{"role": "user", "content": "hi"}], 2),
        ("System message must be at the beginning.", "context", [{"role": "tool", "tool_call_id": "call-1", "content": "ok"}], 1),
    ],
)
def test_runtime_context_fallback_only_changes_an_actual_trailing_system_once(
    monkeypatch: pytest.MonkeyPatch, method: str, message: str,
    runtime_context: str, messages: list[dict[str, Any]], attempts: int,
) -> None:
    client = OpenAICompatibleClient(ApiSettings("https://api.example.com/v1", "key", "model"))
    payloads = []

    def read_response(request):  # type: ignore[no-untyped-def]
        payloads.append(json.loads(request.content))
        return httpx.Response(400, json={"error": {"message": message}})

    mock_http(monkeypatch, read_response)
    with pytest.raises(ApiRequestError):
        getattr(client, method)("system", messages, runtime_context=runtime_context)
    assert len(payloads) == attempts


def test_compatibility_fallback_attempts_are_bounded_by_shared_policy(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    calls: list[dict[str, Any]] = []
    client = OpenAICompatibleClient(
        ApiSettings(
            base_url="https://api.example.com/v1",
            api_key="key",
            model="model",
        )
    )

    def fake_post(payload: dict[str, Any], **_kwargs: Any) -> dict[str, Any]:
        calls.append(dict(payload))
        if "response_format" in payload:
            raise ApiRequestError("unsupported response_format json_object")
        if "temperature" in payload:
            raise ApiRequestError("temperature only supports the default value")
        raise ApiRequestError("still broken")

    monkeypatch.setattr(client, "_post_chat_completions", fake_post)

    try:
        client.complete_raw(
            "system",
            [{"role": "user", "content": "hello"}],
            temperature=0.8,
            response_format={"type": "json_object"},
        )
    except ApiRequestError:
        pass
    else:
        raise AssertionError("最终请求仍失败时应抛出 ApiRequestError")

    assert len(calls) == MAX_COMPATIBILITY_ATTEMPTS
    assert "response_format" in calls[0]
    assert "temperature" in calls[0]
    assert "response_format" not in calls[1]
    assert "temperature" in calls[1]
    assert "response_format" not in calls[2]
    assert "temperature" not in calls[2]


def test_complete_with_tools_sends_tools_and_parses_tool_calls(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    captured: dict[str, Any] = {}
    client = OpenAICompatibleClient(
        ApiSettings(
            base_url="https://api.example.com/v1",
            api_key="key",
            model="model",
        )
    )

    def fake_post(payload: dict[str, Any], **_kwargs: Any) -> dict[str, Any]:
        captured.update(payload)
        return {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": "",
                        "tool_calls": [
                            {
                                "id": "call_1",
                                "type": "function",
                                "function": {
                                    "name": "echo_tool",
                                    "arguments": '{"value":"ok"}',
                                },
                            }
                        ],
                    }
                }
            ]
        }

    monkeypatch.setattr(client, "_post_chat_completions", fake_post)

    turn = client.complete_with_tools(
        "system",
        [{"role": "user", "content": "hello"}],
        tools=[
            {
                "type": "function",
                "function": {
                    "name": "echo_tool",
                    "description": "Echo",
                    "parameters": {"type": "object", "properties": {}},
                },
            }
        ],
    )

    assert captured["tools"][0]["function"]["name"] == "echo_tool"
    assert captured["tool_choice"] == "auto"
    assert turn.tool_calls[0].id == "call_1"
    assert turn.tool_calls[0].name == "echo_tool"
    assert turn.tool_calls[0].arguments == {"value": "ok"}
    assert turn.message["tool_calls"][0]["id"] == "call_1"


def test_complete_with_tools_preserves_provider_tool_call_metadata_for_continuation(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    client = OpenAICompatibleClient(
        ApiSettings(base_url="https://api.example.com/v1", api_key="key", model="gemini-3")
    )

    monkeypatch.setattr(
        client,
        "_post_chat_completions",
        lambda _payload, **_kwargs: {
            "choices": [{
                "message": {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [{
                        "id": "call_1",
                        "type": "function",
                        "function": {"name": "echo_tool", "arguments": "{}"},
                        "extra_content": {"google": {"thought_signature": "opaque-signature"}},
                    }],
                }
            }]
        },
    )

    turn = client.complete_with_tools(
        "system",
        [{"role": "user", "content": "hello"}],
        tools=[{
            "type": "function",
            "function": {
                "name": "echo_tool",
                "description": "Echo",
                "parameters": {"type": "object", "properties": {}},
            },
        }],
    )

    assert turn.message["tool_calls"][0]["extra_content"] == {
        "google": {"thought_signature": "opaque-signature"}
    }


def test_complete_with_tools_parses_pseudo_tool_call_json_content(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    client = OpenAICompatibleClient(
        ApiSettings(
            base_url="https://api.example.com/v1",
            api_key="key",
            model="model",
        )
    )

    def fake_post(_payload: dict[str, Any], **_kwargs: Any) -> dict[str, Any]:
        return {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": (
                            '{"tool":"playwright_navigate",'
                            '"parameters":{"url":"https://example.com"}}'
                        ),
                    }
                }
            ]
        }

    monkeypatch.setattr(client, "_post_chat_completions", fake_post)

    turn = client.complete_with_tools(
        "system",
        [{"role": "user", "content": "open"}],
        tools=[
            {
                "type": "function",
                "function": {
                    "name": "playwright_navigate",
                    "description": "Open",
                    "parameters": {"type": "object", "properties": {}},
                },
            }
        ],
    )

    assert turn.tool_calls[0].id == "pseudo_tool_call_0"
    assert turn.tool_calls[0].name == "playwright_navigate"
    assert turn.tool_calls[0].arguments == {"url": "https://example.com"}
    assert turn.message["tool_calls"][0]["function"]["name"] == "playwright_navigate"


def test_complete_with_tools_ignores_plain_json_reply_without_tool_call(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    client = OpenAICompatibleClient(
        ApiSettings(
            base_url="https://api.example.com/v1",
            api_key="key",
            model="model",
        )
    )

    monkeypatch.setattr(
        client,
        "_post_chat_completions",
        lambda _payload, **_kwargs: {
            "choices": [{"message": {"role": "assistant", "content": '{"segments":[]}'}}]
        },
    )

    turn = client.complete_with_tools(
        "system",
        [{"role": "user", "content": "hello"}],
        structured_response=True,
    )

    assert turn.tool_calls == []
    assert "tool_calls" not in turn.message


def test_list_models_requests_models_endpoint(monkeypatch) -> None:
    requests = []
    client = OpenAICompatibleClient(ApiSettings("https://api.example.com/v1", "key", "", timeout_seconds=12))

    def respond(request):
        requests.append(request)
        return httpx.Response(200, json={"data": [{"id": "z-model"}, {"id": "a-model"}, {"id": "a-model"}]})

    mock_http(monkeypatch, respond)
    assert client.list_models() == ["a-model", "z-model"]
    request, = requests
    assert str(request.url) == "https://api.example.com/v1/models"
    assert request.method == "GET"
    assert request.headers["Authorization"] == "Bearer key"
    assert request.headers["User-Agent"] == "Sakura/dev"
    assert request.extensions["timeout"]["read"] == 12


@pytest.mark.parametrize("base_path", ["", "/v1", "/v1/", "/v1beta", "/v1/openai", "/v1beta/openai/"])
@pytest.mark.parametrize("method", ["test_connection", "list_models"])
def test_requests_normalize_google_ai_studio_base_url(monkeypatch, base_path, method) -> None:
    requests = []
    client = OpenAICompatibleClient(ApiSettings(f"https://generativelanguage.googleapis.com{base_path}", "key", "gemini-2.5-flash"))

    def respond(request):
        requests.append(request)
        data = {"data": [{"id": "gemini-2.5-flash"}]} if method == "list_models" else {"choices": [{"message": {"content": "OK"}}]}
        return httpx.Response(200, json=data)

    mock_http(monkeypatch, respond)
    assert getattr(client, method)() == (["gemini-2.5-flash"] if method == "list_models" else "OK")
    request, = requests
    path = "models" if method == "list_models" else "chat/completions"
    assert str(request.url) == f"https://generativelanguage.googleapis.com/v1beta/openai/{path}"
    if method == "test_connection":
        assert json.loads(request.content) == {"model": "gemini-2.5-flash", "messages": [{"role": "user", "content": "Reply with only OK."}]}


@pytest.mark.parametrize("status", [401, 429, 500, 502, 503, 504])
def test_http_errors_are_not_retried_and_keep_original_response(monkeypatch, status) -> None:
    api_key = "opaque-provider-secret"
    client = OpenAICompatibleClient(ApiSettings("https://api.example.com/v1", api_key, "model"))
    requests = []
    payload = {"error": {"message": "invalid key " + api_key, "code": "invalid_api_key"}}

    def respond(request):
        requests.append(request)
        return httpx.Response(status, json=payload, headers={"x-request-id": "provider-request"})

    mock_http(monkeypatch, respond)
    with pytest.raises(ApiRequestError) as caught:
        client.test_connection()
    assert len(requests) == 1
    assert f"API HTTP {status}" in str(caught.value)
    assert api_key not in str(caught.value)
    assert isinstance(caught.value.__cause__, APIStatusError)
    assert caught.value.__cause__.response.json() == payload
    assert caught.value.__cause__.request_id == "provider-request"


@pytest.mark.parametrize("failure", [httpx.ConnectError, httpx.ReadTimeout, httpx.RemoteProtocolError])
def test_transport_errors_are_not_retried_and_keep_cause(monkeypatch, failure) -> None:
    client = OpenAICompatibleClient(ApiSettings("https://api.example.com/v1", "key", "model"))
    requests = []
    original = failure("offline")

    def respond(request):
        requests.append(request)
        raise original

    mock_http(monkeypatch, respond)
    with pytest.raises(ApiRequestError) as caught:
        client.test_connection()
    assert len(requests) == 1
    assert isinstance(caught.value.__cause__, APIConnectionError)
    assert caught.value.__cause__.__cause__ is original


@pytest.mark.parametrize("body", [b'{"object":"list"}', b'not json', b'[]'])
def test_list_models_rejects_bad_response_shape(monkeypatch, body) -> None:
    mock_http(monkeypatch, lambda request: httpx.Response(200, content=body, headers={"Content-Type": "application/json"}))
    client = OpenAICompatibleClient(ApiSettings("https://api.example.com/v1", "key", ""))
    with pytest.raises(ApiRequestError):
        client.list_models()


def test_parse_chat_reply_keeps_segment_portrait() -> None:
    reply = parse_chat_reply(
        '{"segments":[{"ja":"うん。","zh":"嗯。","tone":"中性","portrait":"站立待机"}]}'
    )

    assert reply.segments[0].portrait == "站立待机"


def test_parse_chat_reply_fenced_json() -> None:
    reply = parse_chat_reply(
        '```json\n{"segments":[{"ja":"うん。","zh":"嗯。","tone":"中性"}]}\n```'
    )

    assert reply.segments[0].text == "うん。"


def test_parse_chat_reply_bad_json_does_not_echo_raw() -> None:
    reply = parse_chat_reply(
        '{"segments":[{"ja":"うん。","zh":"这里有 `""` 裸双引号","tone":"中性"}]}'
    )

    assert reply.segments[0].text != '{"segments":[{"ja":"うん。","zh":"这里有 `""` 裸双引号","tone":"中性"}]}'


def test_parse_chat_reply_suppresses_tts_for_safe_parse_failure() -> None:
    reply = parse_chat_reply('{"segments":')

    assert reply.segments[0].text
    assert reply.segments[0].suppress_tts is True


def test_model_call_runtime_events_match_final_payload_and_trace(
    monkeypatch, tmp_path: Path
) -> None:  # type: ignore[no-untyped-def]
    recorder = AgentTraceRecorder(tmp_path)
    client = OpenAICompatibleClient(
        ApiSettings("https://api.example.com/v1", "key", "example-model"),
        agent_trace_recorder=recorder,
    )
    events: list[tuple[str | None, dict[str, Any]]] = []

    def capture_log_event(_channel, _message, attributes=None, **kwargs):  # type: ignore[no-untyped-def]
        events.append((kwargs.get("event"), dict(attributes or {})))

    monkeypatch.setattr("sakura_assistant.llm.api_client.log_event", capture_log_event)
    monkeypatch.setattr(
        client,
        "_post_chat_completions",
        lambda _payload, cancel_checker=None: {
            "choices": [{"message": {"content": '{"answer":"ok"}'}}],
            "usage": {"prompt_tokens": 120, "completion_tokens": 8, "total_tokens": 128},
        },
    )

    with recorder.operation("chat-runtime-log", finalize_external=True):
        result = client.complete_raw(
            "system",
            [
                {"role": "user", "content": "old"},
                {"role": "assistant", "content": "answer"},
                {"role": "user", "content": "current"},
            ],
        )

    assert result == '{"answer":"ok"}'
    context = next(attributes for event, attributes in events if event == "context.prompt.prepared")
    started = next(attributes for event, attributes in events if event == "api.request.started")
    received = next(attributes for event, attributes in events if event == "api.response.received")
    assert context["trace_id"] == started["trace_id"] == received["trace_id"] == "1"
    assert context["model_call"] == started["model_call"] == received["model_call"] == 1
    assert context["history_messages"] == 2
    assert context["tool_count"] == 0
    assert context["estimated_tokens"] > 0
    assert received["prompt_tokens"] == 120
    assert received["completion_tokens"] == 8
