from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.agent.trace import (
    AgentTraceRecorder,
    PromptTraceMetadata,
    TRACE_PROVENANCE_KEY,
    traced_message,
)
from app.llm.api_client import ApiRequestError, ApiSettings, OpenAICompatibleClient
from app.llm.prompts.types import (
    ContextFragment,
    ContextFragmentDecision,
    ContextRequest,
    ContextSnapshot,
    PromptInspection,
    PromptSectionInspection,
)


def _documents(path: Path) -> list[dict[str, Any]]:
    text = path.read_text(encoding="utf-8")
    decoder = json.JSONDecoder()
    documents = []
    index = 0
    while index < len(text):
        while index < len(text) and text[index].isspace():
            index += 1
        if index >= len(text):
            break
        document, index = decoder.raw_decode(text, index)
        documents.append(document)
    return documents


class CapturingClient(OpenAICompatibleClient):
    def __init__(self, recorder: AgentTraceRecorder, *, fail_response_format_once: bool = False) -> None:
        super().__init__(
            ApiSettings("https://provider.example/v1", "sk-private-fixture", "trace-model"),
            agent_trace_recorder=recorder,
        )
        self.payloads: list[dict[str, Any]] = []
        self.fail_response_format_once = fail_response_format_once

    def _post_chat_completions(
        self,
        payload: dict[str, Any],
        *,
        cancel_checker=None,
    ) -> dict[str, Any]:
        del cancel_checker
        captured = json.loads(json.dumps(payload, ensure_ascii=False))
        self.payloads.append(captured)
        assert TRACE_PROVENANCE_KEY not in json.dumps(captured, ensure_ascii=False)
        if self.fail_response_format_once and len(self.payloads) == 1:
            raise ApiRequestError("response_format unsupported")
        return {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": json.dumps(
                            {
                                "segments": [
                                    {
                                        "ja": "こんばんは。",
                                        "zh": "晚上好。",
                                        "tone": "中性",
                                        "portrait": "站立待机",
                                    }
                                ],
                                "visual_observation": {"summary": "画面摘要"},
                            },
                            ensure_ascii=False,
                        ),
                    }
                }
            ],
            "usage": {"prompt_tokens": 51, "completion_tokens": 19, "total_tokens": 70},
        }


def _metadata(purpose: str = "agent_step") -> PromptTraceMetadata:
    snapshot = ContextSnapshot(
        request=ContextRequest(current_input="当前问题"),
        selected=(
            ContextFragmentDecision(
                ContextFragment("runtime.time", "runtime", "当前本地时间：15:56"),
                12,
                True,
            ),
            ContextFragmentDecision(
                ContextFragment(
                    "m-42",
                    "memory",
                    "用户偏好简洁回复。",
                    metadata={"score": 0.91, "source": "semantic"},
                ),
                14,
                True,
            ),
        ),
        estimated_tokens=26,
        token_budget=4096,
    )
    inspection = PromptInspection(
        recipe_name="agent",
        sections=(
            PromptSectionInspection(
                "persona.character",
                "character",
                "trusted",
                "private",
                "static",
                4,
                4,
                True,
            ),
            PromptSectionInspection(
                "reply.protocol",
                "host",
                "trusted",
                "public",
                "static",
                4,
                4,
                True,
            ),
        ),
        total_chars=8,
        estimated_tokens=8,
    )
    return PromptTraceMetadata(purpose=purpose, inspection=inspection, snapshot=snapshot)


def _base_messages() -> list[dict[str, Any]]:
    return [
        traced_message({"role": "user", "content": "历史问题"}, "history"),
        traced_message({"role": "assistant", "content": "历史回答"}, "history"),
        traced_message({"role": "user", "content": "当前问题"}, "user_input"),
    ]


def test_final_provider_payload_and_trace_prompt_are_parallel_and_provenance_free(
    tmp_path: Path,
) -> None:
    recorder = AgentTraceRecorder(tmp_path)
    client = CapturingClient(recorder)
    with recorder.operation("operation-tail-system", finalize_external=True):
        turn = client.complete_with_tools(
            "固定人格\n\n回复协议",
            _base_messages(),
            tools=[
                {
                    "type": "function",
                    "function": {
                        "name": "search",
                        "parameters": {"type": "object", "properties": {}},
                    },
                }
            ],
            structured_response=True,
            runtime_context="当前本地时间：15:56\n用户偏好简洁回复。",
            trace_metadata=_metadata(),
        )

    payload = client.payloads[0]
    request, reply = _documents(recorder.path)
    assert [message["role"] for message in payload["messages"]] == [
        "system",
        "user",
        "assistant",
        "user",
        "system",
    ]
    assert [next(iter(part)) for part in request["prompt"]] == [
        "system_prompt",
        "history",
        "user_input",
        "runtime_context",
    ]
    history_items = request["prompt"][1]["history"]["items"]
    assert history_items == [
        {"role": message["role"], "content": message["content"]}
        for message in payload["messages"][1:3]
    ]
    current = request["prompt"][2]["user_input"]
    assert current["role"] == payload["messages"][3]["role"]
    assert current["content"] == [payload["messages"][3]["content"]]
    assert [item["name"] for item in request["tools"]["definitions"]] == ["search"]
    assert "parameters" not in request["tools"]["definitions"][0]
    assert request["parameters"]["temperature"] == payload["temperature"]
    assert request["parameters"]["response_format"] == payload["response_format"]
    assert turn.runtime_context_placement == "tail_system"
    assert reply["model_output"]["visual_observation"]["summary"] == "画面摘要"
    assert reply["usage"] == {"prompt_tokens": 51, "completion_tokens": 19, "total_tokens": 70}


def test_runtime_context_tail_user_and_merged_system_follow_real_payload_positions(
    tmp_path: Path,
) -> None:
    recorder = AgentTraceRecorder(tmp_path)
    user_client = CapturingClient(recorder)
    user_client._runtime_context_role = "user"
    with recorder.operation("operation-tail-user", finalize_external=True):
        turn = user_client.complete_with_tools(
            "system",
            _base_messages(),
            runtime_context="动态事实",
            trace_metadata=_metadata(),
        )
    assert turn.runtime_context_placement == "tail_user"
    first_request = _documents(recorder.path)[0]
    assert next(iter(first_request["prompt"][-1])) == "runtime_context"
    assert user_client.payloads[0]["messages"][-1]["role"] == "user"

    merged_client = CapturingClient(recorder)
    tool_messages = [
        *_base_messages(),
        traced_message(
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "call-1",
                        "type": "function",
                        "function": {"name": "search", "arguments": "{}"},
                    }
                ],
            },
            "assistant_tool_call",
        ),
        traced_message(
            {"role": "tool", "tool_call_id": "call-1", "name": "search", "content": "结果"},
            "tool_result",
        ),
    ]
    with recorder.operation("operation-merged", finalize_external=True):
        merged_turn = merged_client.complete_with_tools(
            "system",
            tool_messages,
            runtime_context="动态事实",
            trace_metadata=_metadata(),
        )
    assert merged_turn.runtime_context_placement == "merged_system"
    assert merged_client.payloads[0]["messages"][-1]["role"] == "tool"
    assert "动态事实" in merged_client.payloads[0]["messages"][0]["content"]
    merged_request = [item for item in _documents(recorder.path) if item["type"] == "request"][-1]
    assert [next(iter(part)) for part in merged_request["prompt"]][-2:] == [
        "assistant_tool_call",
        "tool_result",
    ]
    appended = merged_request["prompt"][0]["system_prompt"]["appended_runtime_context"]
    assert appended["items"][1]["memory"]["id"] == "m-42"


def test_compatibility_retry_creates_the_next_model_call_without_losing_operation_block(
    tmp_path: Path,
) -> None:
    recorder = AgentTraceRecorder(tmp_path)
    client = CapturingClient(recorder, fail_response_format_once=True)
    with recorder.operation("operation-compat", finalize_external=True):
        client.complete_with_tools(
            "system",
            _base_messages(),
            structured_response=True,
            trace_metadata=_metadata("final_reply"),
        )
    documents = _documents(recorder.path)
    assert [(item["type"], item["model_call"]) for item in documents] == [
        ("request", 1),
        ("request", 2),
        ("reply", 2),
    ]
    assert [item["purpose"] for item in documents] == ["final_reply"] * 3
    assert "response_format" in documents[0]["parameters"]
    assert "response_format" not in documents[1]["parameters"]
