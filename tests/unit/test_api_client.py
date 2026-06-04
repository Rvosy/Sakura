from __future__ import annotations

from typing import Any

from app.llm.api_client import (
    ApiRequestError,
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


def test_build_chat_payload_adds_json_keyword_for_json_object_response() -> None:
    payload = _build_chat_completion_payload(
        model="gpt-compatible",
        system_prompt="只返回对象，不要解释。",
        messages=[{"role": "user", "content": "提取字段"}],
        temperature=0.8,
        chat_params={"response_format": {"type": "json_object"}},
    )

    assert "json" in payload["messages"][0]["content"].lower()


def test_build_chat_payload_keeps_existing_json_keyword() -> None:
    payload = _build_chat_completion_payload(
        model="gpt-compatible",
        system_prompt="Return a JSON object only.",
        messages=[{"role": "user", "content": "提取字段"}],
        temperature=0.8,
        chat_params={"response_format": {"type": "json_object"}},
    )

    assert payload["messages"][0]["content"] == "Return a JSON object only."


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


def test_complete_raw_retries_without_temperature_when_provider_rejects(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    calls: list[dict[str, Any]] = []
    client = OpenAICompatibleClient(
        ApiSettings(
            base_url="https://api.example.com/v1",
            api_key="key",
            model="compatible-model",
        )
    )

    def fake_post(payload: dict[str, Any]) -> dict[str, Any]:
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

    def fake_post(payload: dict[str, Any]) -> dict[str, Any]:
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

    def fake_post(payload: dict[str, Any]) -> dict[str, Any]:
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

    def fake_post(payload: dict[str, Any]) -> dict[str, Any]:
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

    def fake_post(payload: dict[str, Any]) -> dict[str, Any]:
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


def test_complete_with_tools_can_request_structured_json(monkeypatch) -> None:  # type: ignore[no-untyped-def]
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
        return {"choices": [{"message": {"role": "assistant", "content": '{"segments":[]}'}}]}

    monkeypatch.setattr(client, "_post_chat_completions", fake_post)

    client.complete_with_tools(
        "system",
        [{"role": "user", "content": "hello"}],
        structured_response=True,
    )

    assert captured["response_format"] == {"type": "json_object"}


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


def test_parse_chat_reply_swaps_chinese_ja_with_japanese_zh() -> None:
    reply = parse_chat_reply(
        '{"segments":[{"ja":"原因是 Mermaid 语法。","zh":"原因はマーメイドの構文だよ。","tone":"中性"}]}'
    )

    assert reply.segments[0].text == "原因はマーメイドの構文だよ。"
    assert reply.segments[0].translation == "原因是 Mermaid 语法。"


def test_parse_chat_reply_replaces_chinese_ja_with_safe_japanese() -> None:
    reply = parse_chat_reply(
        '{"segments":[{"ja":"原因是 Mermaid 语法。","zh":"原因是 Mermaid 语法。","tone":"中性"}]}'
    )

    assert "原因是" not in reply.segments[0].text
    assert reply.segments[0].translation == "原因是 Mermaid 语法。"
