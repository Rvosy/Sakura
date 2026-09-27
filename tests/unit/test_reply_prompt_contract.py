from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from plugins.builtin.sakura_portrait.plugin import PortraitService
from sakura_assistant.agent.actions import AgentEvent
from sakura_assistant.agent.runtime import AgentRuntime
from sakura_assistant.llm.api_client import AssistantModelClient, DialogueSettings
from tests.model_fixture import LocalModelClient


@pytest.mark.parametrize("route", ["agent", "image", "final", "event", "repair", "unbound"])
def test_provider_reply_contract_keeps_visual_controls_across_reply_paths(monkeypatch, route):
    # A portrait name that cannot be recovered from the TTS tone.
    key = "微笑_抬手"
    visual = {
        "resourceId": "portrait-one",
        "prompt": "选择当前立绘标签。",
        "outputSchema": {
            "type": "object",
            "properties": {"key": {"type": "string", "enum": ["__default__", key]}},
            "required": ["key"], "additionalProperties": False,
        },
    }
    control = {"version": 1, "resourceId": visual["resourceId"], "payload": {"key": key}}
    segment = {"ja": "こんにちは。", "zh": "你好。", "tone": "中性"}
    if route != "unbound":
        segment["control"] = control
    raw = json.dumps({"segments": [segment]}, ensure_ascii=False)
    captured = []
    client = AssistantModelClient(DialogueSettings(model="model"), model_client=LocalModelClient())

    def post(payload, **_kwargs):
        captured.append(payload)
        content = raw
        if route == "repair" and len(captured) == 1:
            content = json.dumps({"segments": [{**segment, "ja": "这个需要修复。"}]}, ensure_ascii=False)
        return {"choices": [{"message": {"role": "assistant", "content": content}}]}

    monkeypatch.setattr(client, "_post_chat_completions", post)
    runtime = AgentRuntime(client, "测试角色", reply_tones=["中性"])
    if route != "unbound":
        runtime.set_visual_binding(SimpleNamespace(reply_visual=visual))
    messages = [{"role": "user", "content": "你好"}]
    if route == "image":
        messages[0]["content"] = [
            {"type": "text", "text": "看这张图"},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,AA=="}},
        ]
    if route == "event":
        reply = runtime.handle_event(AgentEvent("update_available", {"version": "1.2.3"})).reply
    elif route == "final":
        reply, _ = runtime._complete_final_reply(runtime._build_final_reply_prompt(), messages)
    else:
        reply = runtime.handle_user_message(messages).reply

    assert len(captured) == (2 if route == "repair" else 1)
    for payload in captured:
        system = payload["messages"][0]["content"]
        objects = [json.loads(line) for line in system.splitlines() if line.startswith('{"')]
        if route == "unbound":
            assert all("control" not in obj.get("segments", [{}])[0] for obj in objects)
            continue
        schemas = [obj for obj in objects if obj.get("type") == "object" and "segments" in obj.get("properties", {})]
        assert len(schemas) == 1  # Including events: no second, conflicting reply format.
        item = schemas[0]["properties"]["segments"]["items"]
        assert set(item["required"]) == {"ja", "zh", "tone", "control"}
        shape = item["properties"]["control"]
        assert set(shape["required"]) == {"version", "resourceId", "payload"}
        assert shape["properties"]["version"] == {"const": 1}
        assert shape["properties"]["resourceId"] == {"const": "portrait-one"}
        assert shape["properties"]["payload"] == visual["outputSchema"]
        assert not any("segments" in obj for obj in objects)  # No incomplete example beside the schema.
    assert reply.segments[0].text == "こんにちは。"
    if route == "unbound":
        assert reply.segments[0].control is None
    else:
        assert reply.segments[0].control == control
        parsed = PortraitService(None, None).parseControl(
            {"segment": {"tone": "中性"}}, {"keys": ["__default__", key]}, control["payload"], None,
        )
        assert parsed["state"] == {"key": key}
    if route == "repair":
        instruction = captured[-1]["messages"][-2]["content"]
        # Repair instructions must defer to the system contract instead of
        # demanding a text/tone-only replacement object.
        assert "control" in instruction
        assert '{"segments"' not in instruction
