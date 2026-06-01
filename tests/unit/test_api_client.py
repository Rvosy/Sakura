from __future__ import annotations

from typing import Any

from app.llm.api_client import (
    ApiSettings,
    OpenAICompatibleClient,
    _build_segmented_reply_instruction,
    _build_chat_completion_payload,
    _filter_supported_chat_params,
)
from app.llm.chat_reply import parse_chat_reply


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

    def fake_post(payload: dict[str, Any]) -> dict[str, Any]:
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


def test_complete_with_tools_sends_tools_and_parses_tool_calls(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    captured: dict[str, Any] = {}
    client = OpenAICompatibleClient(
        ApiSettings(
            base_url="https://api.example.com/v1",
            api_key="key",
            model="model",
        )
    )

    def fake_post(payload: dict[str, Any]) -> dict[str, Any]:
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


def test_segmented_reply_instruction_requests_portrait_field() -> None:
    instruction = _build_segmented_reply_instruction(
        ["中性", "请求"],
        ["站立待机", "伸手命令"],
    )

    assert '"portrait":"站立待机"' in instruction
    assert "portrait 只能从这些类别中选择：站立待机、伸手命令" in instruction


def test_parse_chat_reply_keeps_segment_portrait() -> None:
    reply = parse_chat_reply(
        '{"segments":[{"ja":"うん。","zh":"嗯。","tone":"中性","portrait":"站立待机"}]}'
    )

    assert reply.segments[0].portrait == "站立待机"
