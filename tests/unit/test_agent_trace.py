from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.agent.trace import (
    AgentTraceRecorder,
    MessageProvenance,
    PromptTraceMetadata,
    traced_message,
)
from app.agent.actions import AgentAction, AgentResult
from app.core.chat_pipeline import ChatPipeline
from app.llm.chat_reply import parse_chat_reply
from app.llm.prompts.runtime import estimate_prompt_tokens
from app.llm.prompts.types import (
    ContextFragment,
    ContextFragmentDecision,
    ContextRequest,
    ContextSnapshot,
    ContextTurnDecision,
    PromptInspection,
    PromptSectionInspection,
)


FIXED_NOW = datetime(2026, 8, 12, 15, 56, 45, tzinfo=timezone(timedelta(hours=8)))


class CapturingTraceRecorder(AgentTraceRecorder):
    """Keep the pre-render document contract observable without a production sidecar."""

    def __init__(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        self.committed_documents: list[dict[str, object]] = []
        super().__init__(*args, **kwargs)

    def _commit_documents(self, documents):  # type: ignore[no-untyped-def]
        self.committed_documents.extend(json.loads(json.dumps(documents, ensure_ascii=False)))
        super()._commit_documents(documents)


def _documents(recorder: CapturingTraceRecorder) -> list[dict[str, object]]:
    return recorder.committed_documents


def _inspection() -> PromptInspection:
    return PromptInspection(
        recipe_name="agent",
        sections=(
            PromptSectionInspection(
                section_id="persona.character",
                source="character",
                trust="trusted",
                sensitivity="private",
                cache_scope="static",
                chars=6,
                estimated_tokens=6,
                included=True,
            ),
            PromptSectionInspection(
                section_id="reply.protocol",
                source="host",
                trust="trusted",
                sensitivity="public",
                cache_scope="static",
                chars=8,
                estimated_tokens=8,
                included=True,
            ),
        ),
        total_chars=14,
        estimated_tokens=14,
    )


def _snapshot() -> ContextSnapshot:
    selected = (
        ContextFragmentDecision(
            ContextFragment(
                "runtime.time",
                "runtime",
                "当前本地时间：2026-08-12 15:56",
                metadata={},
            ),
            18,
            True,
        ),
        ContextFragmentDecision(
            ContextFragment(
                "m-123",
                "plugin:sakura.memory.mem0",
                "与本轮相关的长期记忆。",
                metadata={"score": 0.82, "source": "semantic"},
            ),
            96,
            True,
        ),
    )
    dropped = (
        ContextFragmentDecision(
            ContextFragment("m-old", "plugin:sakura.memory.mem0", "未发送记忆"),
            20,
            False,
            drop_reason="budget_exhausted",
        ),
    )
    return ContextSnapshot(
        request=ContextRequest(current_input="当前用户输入"),
        selected=selected,
        dropped=dropped,
        estimated_tokens=194,
        token_budget=4096,
        selected_turns=(ContextTurnDecision("turn-new", 80, True),),
        dropped_turns=(
            ContextTurnDecision("turn-old", 90, False, drop_reason="budget_exhausted"),
        ),
        context_window_tokens=131_072,
        window_source="user",
        estimator="conservative",
        input_target=98_304,
        output_reserve=4_096,
        safety_margin=6_554,
        required_tokens=2_000,
    )


def _payload() -> dict[str, object]:
    return {
        "model": "example-model",
        "messages": [
            {"role": "system", "content": "固定人格和回复协议"},
            {"role": "user", "content": "上一轮用户输入"},
            {"role": "assistant", "content": "上一轮模型回复"},
            {"role": "user", "content": "当前用户输入"},
            {"role": "system", "content": "动态上下文"},
        ],
        "tools": [
            {
                "type": "function",
                "function": {
                    "name": "memory_search",
                    "description": "检索记忆",
                    "parameters": {"type": "object", "properties": {}},
                },
            }
        ],
        "temperature": 0.8,
        "tool_choice": "auto",
        "response_format": {"type": "json_object"},
    }


def _record_pair(
    recorder: AgentTraceRecorder,
    operation_id: str,
    *,
    content: str = '{"segments":[{"ja":"こんばんは。","zh":"晚上好。"}]}',
) -> None:
    snapshot = _snapshot()
    runtime_items = (
        {"runtime": {"id": "runtime.time", "content": ["当前本地时间"], "estimated_tokens": 18}},
        {
            "plugin": {
                "id": "m-123",
                "score": 0.82,
                "source": "semantic",
                "content": ["与本轮相关的长期记忆。"],
                "estimated_tokens": 96,
            }
        },
    )
    with recorder.operation(operation_id, finalize_external=True):
        call = recorder.start_model_call(
            model="example-model",
            payload=_payload(),
            prompt_provenance=(
                MessageProvenance("system_prompt"),
                MessageProvenance("history"),
                MessageProvenance("history"),
                MessageProvenance("user_input"),
                MessageProvenance("runtime_context", runtime_items=runtime_items),
            ),
            metadata=PromptTraceMetadata(
                purpose="agent_step",
                inspection=_inspection(),
                snapshot=snapshot,
            ),
        )
        recorder.record_model_reply(
            call,
            raw_message={"role": "assistant", "content": content},
            usage={"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120},
        )


def test_request_uses_payload_order_and_hides_static_system_body(tmp_path: Path) -> None:
    recorder = CapturingTraceRecorder(tmp_path, now=lambda: FIXED_NOW)
    _record_pair(recorder, "op-order")
    request, reply = _documents(recorder)

    assert request["type"] == "request"
    assert request["time"] == "2026-08-12T15:56:45+08:00"
    prompt = request["prompt"]
    assert [next(iter(part)) for part in prompt] == [
        "system_prompt",
        "history",
        "user_input",
        "runtime_context",
    ]
    assert prompt[1]["history"]["messages"] == 2
    assert prompt[1]["history"]["items"] == [
        {"role": "user", "content": "上一轮用户输入"},
        {"role": "assistant", "content": "上一轮模型回复"},
    ]
    assert prompt[2]["user_input"]["content"] == ["当前用户输入"]
    assert "固定人格" not in json.dumps(prompt[0], ensure_ascii=False)
    assert prompt[0]["system_prompt"]["sections"] == [
        {"id": "persona.character", "chars": 6},
        {"id": "reply.protocol", "chars": 8},
    ]
    assert prompt[3]["runtime_context"]["items"][1]["plugin"]["id"] == "m-123"
    assert request["tools"]["count"] == 1
    expected_budget = {
        "context_window_tokens": 131_072,
        "window_source": "user",
        "estimator": "conservative",
        "input_target": 98_304,
        "output_reserve": 4_096,
        "safety_margin": 6_554,
        "required_tokens": 2_000,
        "history_candidate_turns": 2,
        "history_selected_turns": 1,
        "context_selected_tokens": 194,
    }
    assert all(request["summary"][key] == value for key, value in expected_budget.items())
    assert request["dropped_turns"] == [
        {
            "turn_id": "turn-old",
            "estimated_tokens": 90,
            "reason": "budget_exhausted",
        }
    ]
    tool = _payload()["tools"][0]
    encoded_tool = json.dumps(tool, ensure_ascii=False, separators=(",", ":"))
    assert request["tools"]["definitions"] == [
        {
            "name": "memory_search",
            "schema_chars": len(encoded_tool),
            "estimated_tokens": estimate_prompt_tokens(encoded_tool),
        }
    ]
    assert request["parameters"]["response_format"] == {"type": "json_object"}
    assert request["summary"]["history_messages"] == 2
    assert "memories" not in request["summary"]
    assert "memory_estimated_tokens" not in request["summary"]
    assert request["dropped_context"][0]["id"] == "m-old"
    assert reply["model_output"]["segments"][0]["ja"] == "こんばんは。"
    assert isinstance(reply["model_output"]["segments"], list)


def test_pretty_document_stream_has_no_heading_and_two_blank_lines(tmp_path: Path) -> None:
    recorder = CapturingTraceRecorder(tmp_path, now=lambda: FIXED_NOW)
    _record_pair(recorder, "op-format")
    text = recorder.path.read_text(encoding="utf-8")
    assert text.startswith("=" * 60 + "\n[Agent Trace] 模型请求")
    assert "\n\n" + "=" * 60 + "\n[Agent Trace] 模型回复" in text
    assert "提示词 1/4［系统提示词］" in text
    assert "上下文汇总" in text
    assert "模型上下文窗口" in text and "131072 tokens" in text
    assert "窗口来源" in text and "用户" in text
    assert "输入目标" in text and "98304 tokens" in text
    assert "输出预留" in text and "4096 tokens" in text
    assert "候选历史 Turn" in text
    assert "选中历史 Turn" in text
    assert "未选中的历史 Turn" in text
    assert "Turn 1: turn-old" in text
    assert "原因" in text and "超出上下文预算" in text
    assert "模型输出" in text
    assert "回复片段 1:" in text
    assert "日文" in text
    assert "中文" in text
    assert "こんばんは" in text
    assert "\\u3053" not in text
    assert '"segments":' not in text
    assert "{\n" not in text
    assert "\n}" not in text


def test_visible_trace_uses_chinese_hierarchy_without_json_syntax(tmp_path: Path) -> None:
    recorder = CapturingTraceRecorder(tmp_path, now=lambda: FIXED_NOW)
    with recorder.operation("op-human", finalize_external=True):
        call = recorder.start_model_call(
            model="m",
            payload={
                "model": "m",
                "messages": [{"role": "user", "content": "请检查结构化输出"}],
                "response_format": {"type": "json_object"},
            },
            prompt_provenance=(MessageProvenance("user_input"),),
        )
        recorder.record_model_reply(
            call,
            raw_message={
                "content": json.dumps(
                    {
                        "segments": [
                            {
                                "ja": "確認します。",
                                "zh": "我来检查。",
                                "tone": "中性",
                                "portrait": None,
                            }
                        ],
                        "visual_observation": {"summary": "画面正常", "visible": True},
                        "unknown_model_field": [1, False, None],
                    },
                    ensure_ascii=False,
                )
            },
            usage={"prompt_tokens": 12, "completion_tokens": 8, "total_tokens": 20},
        )

    text = recorder.path.read_text(encoding="utf-8")
    assert "[Agent Trace] 模型请求" in text
    assert "[Agent Trace] 模型回复" in text
    assert "提示词 1/1［当前用户输入］" in text
    assert "回复片段 1:" in text
    assert "视觉观察:" in text
    assert "unknown_model_field:" in text
    assert "visible      : 是" in text
    assert any(
        line.strip().startswith("立绘") and line.endswith(": 无")
        for line in text.splitlines()
    )
    assert "2. 否" in text
    assert "3. 无" in text
    assert "提示词 tokens" in text
    assert any(
        line.strip().startswith("解析状态") and line.endswith(": 有效")
        for line in text.splitlines()
    )
    for json_fragment in ('"segments":', '"unknown_model_field":', "{\n", "\n}", "[\n", "\n]"):
        assert json_fragment not in text


def test_reply_shapes_and_effective_change_rules(tmp_path: Path) -> None:
    recorder = CapturingTraceRecorder(tmp_path, now=lambda: FIXED_NOW)
    with recorder.operation("op-replies", finalize_external=True):
        for content in ("普通文本回复", '{"segments": [}'):
            call = recorder.start_model_call(
                model="m",
                payload={"model": "m", "messages": [{"role": "user", "content": "问"}]},
                prompt_provenance=(MessageProvenance("user_input"),),
            )
            recorder.record_model_reply(call, raw_message={"content": content})
            if content.startswith("{"):
                recorder.mark_repair_requested(call, "invalid_json")
                recorder.record_effective_reply(
                    call,
                    {"segments": [{"ja": "修復", "zh": "修复", "tone": "中性", "portrait": "站立待机"}]},
                    ["reply_repair"],
                )
    replies = [item for item in _documents(recorder) if item["type"] == "reply"]
    assert replies[0]["raw_text"] == ["普通文本回复"]
    assert replies[0]["processing"]["parse_status"] == "text"
    assert "effective_reply" not in replies[0]
    assert replies[1]["processing"]["parse_status"] == "invalid_json"
    assert replies[1]["processing"]["repair_requested"] is True
    assert replies[1]["effective_reply"]["segments"][0]["zh"] == "修复"
    assert replies[1]["changes"] == ["reply_repair"]


def test_trace_recognizes_fenced_json_before_business_parse(tmp_path: Path) -> None:
    recorder = CapturingTraceRecorder(tmp_path, now=lambda: FIXED_NOW)
    with recorder.operation("op-fenced", finalize_external=True):
        call = recorder.start_model_call(
            model="m",
            payload={"model": "m", "messages": [{"role": "user", "content": "问"}]},
            prompt_provenance=(MessageProvenance("user_input"),),
        )
        recorder.record_model_reply(
            call,
            raw_message={
                "content": '```json\n{"segments":[{"ja":"うん。","zh":"嗯。"}]}\n```'
            },
        )
    reply = [item for item in _documents(recorder) if item["type"] == "reply"][0]
    assert reply["processing"]["raw_json_status"] == "valid"
    assert reply["processing"]["business_parse_status"] == "valid"
    assert reply["processing"]["fence_extracted"] is True
    assert reply["processing"]["repair_requested"] is False


def test_credentials_and_binary_bodies_never_reach_trace(tmp_path: Path) -> None:
    recorder = CapturingTraceRecorder(tmp_path, now=lambda: FIXED_NOW)
    recorder.add_secret("sk-private-known-value")
    payload = {
        "model": "m",
        "messages": [
            {
                "role": "user",
                "content": "Authorization: Bearer abc Cookie=session Password=hunter2 "
                "token=visible https://alice:secret@example.com/path sk-private-known-value",
            },
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "图片"},
                    {"type": "image_url", "image_url": {"url": "data:image/png;base64,aGVsbG8="}},
                ],
            },
        ],
        "tools": [{"authorization": "private", "password": "private"}],
    }
    with recorder.operation("op-private", finalize_external=True):
        call = recorder.start_model_call(
            model="m",
            payload=payload,
            prompt_provenance=(MessageProvenance("history"), MessageProvenance("user_input")),
        )
        recorder.record_model_reply(
            call,
            raw_message={"content": '{"token":"private","ok":"普通正文"}'},
        )
    text = recorder.path.read_text(encoding="utf-8")
    for secret in (
        "abc",
        "session",
        "hunter2",
        "visible",
        "alice:secret",
        "sk-private-known-value",
        "aGVsbG8=",
    ):
        assert secret not in text
    assert "普通正文" in text
    request = _documents(recorder)[0]
    binary = request["prompt"][1]["user_input"]["content"][1]["image_url"]["url"]
    assert binary["type"] == "binary"
    assert binary["bytes"] == 5


def test_known_credentials_are_removed_from_dynamic_context(tmp_path: Path) -> None:
    recorder = CapturingTraceRecorder(tmp_path, now=lambda: FIXED_NOW)
    recorder.add_secret("sk-private-memory-value")
    runtime_items = (
        {
            "memory": {
                "id": "m-private",
                "content": ["记忆里的 sk-private-memory-value 不得落盘"],
                "estimated_tokens": 12,
            }
        },
    )
    with recorder.operation("op-private-context", finalize_external=True):
        call = recorder.start_model_call(
            model="m",
            payload={
                "model": "m",
                "messages": [{"role": "system", "content": "system\n动态上下文"}],
            },
            prompt_provenance=(
                MessageProvenance("system_prompt", runtime_items=runtime_items),
            ),
        )
        recorder.record_model_reply(call, raw_message={"content": "ok"})

    text = recorder.path.read_text(encoding="utf-8")
    assert "sk-private-memory-value" not in text
    assert "[REDACTED]" in text


def test_long_free_text_is_wrapped_and_one_mib_value_is_truncated(tmp_path: Path) -> None:
    recorder = CapturingTraceRecorder(tmp_path, now=lambda: FIXED_NOW)
    long_value = "甲" * (1024 * 1024 + 1)
    with recorder.operation("op-long", finalize_external=True):
        call = recorder.start_model_call(
            model="m",
            payload={
                "model": "m",
                "messages": [
                    {"role": "user", "content": "这是一段需要分行显示的文本" * 20},
                    {"role": "user", "content": long_value},
                ],
            },
            prompt_provenance=(MessageProvenance("history"), MessageProvenance("user_input")),
        )
        recorder.record_model_reply(call, raw_message={"content": "ok"})
    request = _documents(recorder)[0]
    assert len(request["prompt"][0]["history"]["items"][0]["content"]) > 1
    truncated = request["prompt"][1]["user_input"]["content"]
    assert truncated["truncated"] is True
    assert truncated["bytes"] > 1024 * 1024
    assert truncated["head"] and truncated["tail"]


def test_compact_request_keeps_large_history_readable_and_tool_costs_actionable(
    tmp_path: Path,
) -> None:
    recorder = CapturingTraceRecorder(tmp_path, now=lambda: FIXED_NOW)
    history = [
        {"role": "user" if index % 2 == 0 else "assistant", "content": f"历史消息 {index}"}
        for index in range(23)
    ]
    tools = [
        {
            "type": "function",
            "function": {
                "name": f"tool_{index}",
                "description": "固定说明" * 10,
                "parameters": {
                    "type": "object",
                    "properties": {"query": {"type": "string"}},
                },
            },
        }
        for index in range(18)
    ]
    with recorder.operation("op-compact", finalize_external=True):
        call = recorder.start_model_call(
            model="m",
            payload={
                "model": "m",
                "messages": [
                    {"role": "system", "content": "system"},
                    *history,
                    {"role": "user", "content": "当前输入"},
                ],
                "tools": tools,
            },
            prompt_provenance=(
                MessageProvenance("system_prompt"),
                *(MessageProvenance("history") for _ in history),
                MessageProvenance("user_input"),
            ),
        )
        recorder.record_model_reply(call, raw_message={"content": "ok"})

    request = _documents(recorder)[0]
    history_block = request["prompt"][1]["history"]
    assert history_block["messages"] == 23
    assert [item["role"] for item in history_block["items"]] == [
        message["role"] for message in history
    ]
    assert [item["content"] for item in history_block["items"]] == [
        message["content"] for message in history
    ]
    assert [item["name"] for item in request["tools"]["definitions"]] == [
        f"tool_{index}" for index in range(18)
    ]
    assert "description" not in json.dumps(request["tools"], ensure_ascii=False)
    pretty_request = recorder.path.read_text(encoding="utf-8").split("\n\n", 1)[0]
    assert pretty_request.count("\n") + 1 < 200
    assert "  工具 1: tool_0" in pretty_request
    assert pretty_request.index("上下文汇总") < pretty_request.index("提示词 1/")


def test_write_failures_do_not_affect_model_boundary(tmp_path: Path) -> None:
    blocked = tmp_path / "blocked"
    (blocked / "data").mkdir(parents=True)
    (blocked / "data" / "logs").write_text("not a directory", encoding="utf-8")
    recorder = AgentTraceRecorder(blocked)
    assert recorder.start_model_call(
        model="m",
        payload={"messages": []},
        prompt_provenance=(),
    ) is None
    assert recorder.finish_operation("missing") is True


def test_legacy_disabled_setting_does_not_disable_trace(tmp_path: Path) -> None:
    config = tmp_path / "data" / "config" / "system_config.yaml"
    config.parent.mkdir(parents=True)
    config.write_text("agent_trace:\n  enabled: false\n", encoding="utf-8")

    recorder = AgentTraceRecorder(tmp_path)
    call = recorder.start_model_call(
        model="m",
        payload={"messages": []},
        prompt_provenance=(),
    )

    assert call is not None
    assert recorder.finish_operation(call.operation_id) is True
    assert recorder.path.exists()


def test_crash_staging_recovers_as_interrupted(tmp_path: Path) -> None:
    first = CapturingTraceRecorder(tmp_path, now=lambda: FIXED_NOW)
    call = first.start_model_call(
        model="m",
        payload={"model": "m", "messages": [{"role": "user", "content": "未完成"}]},
        prompt_provenance=(MessageProvenance("user_input"),),
    )
    assert call is not None
    assert list(first.staging_dir.glob("*.stage"))

    recovered = CapturingTraceRecorder(tmp_path, now=lambda: FIXED_NOW)
    documents = _documents(recovered)
    assert documents[0]["status"] == "interrupted"
    assert not list(recovered.staging_dir.glob("*.stage"))


def test_concurrent_operations_commit_as_whole_blocks(tmp_path: Path) -> None:
    recorder = CapturingTraceRecorder(tmp_path, now=lambda: FIXED_NOW)
    threads = [
        threading.Thread(target=_record_pair, args=(recorder, f"op-{index}"), kwargs={"content": f'{{"index":{index}}}'})
        for index in range(6)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    documents = _documents(recorder)
    assert len(documents) == 12
    for index in range(0, len(documents), 2):
        assert documents[index]["type"] == "request"
        assert documents[index + 1]["type"] == "reply"
        assert documents[index]["trace"] == documents[index + 1]["trace"]


def test_rotation_retention_and_whole_operation_behavior(tmp_path: Path) -> None:
    now = FIXED_NOW
    recorder = CapturingTraceRecorder(
        tmp_path,
        max_file_bytes=1,
        max_total_bytes=1024 * 1024,
        retention_days=30,
        now=lambda: now,
    )
    _record_pair(recorder, "op-first")
    _record_pair(recorder, "op-second")
    archives = list(recorder.log_dir.glob("sakura-agent-trace.*.log"))
    assert len(archives) == 1
    assert archives[0].read_text(encoding="utf-8").count("[Agent Trace]") == 2
    assert recorder.path.read_text(encoding="utf-8").count("[Agent Trace]") == 2

    old = recorder.log_dir / "sakura-agent-trace.2020-01-01.1.log"
    old.write_text("old", encoding="utf-8")
    old_time = (now - timedelta(days=31)).timestamp()
    os.utime(old, (old_time, old_time))
    _record_pair(recorder, "op-retention")
    assert not old.exists()


def test_trace_crosses_calendar_days_without_rotation(tmp_path: Path) -> None:
    now = FIXED_NOW
    recorder = CapturingTraceRecorder(
        tmp_path,
        max_file_bytes=1024 * 1024,
        now=lambda: now,
    )
    _record_pair(recorder, "op-day-one")
    first_contents = recorder.path.read_text(encoding="utf-8")

    now = FIXED_NOW + timedelta(days=3)
    _record_pair(recorder, "op-day-four")

    assert not list(recorder.log_dir.glob("sakura-agent-trace.*.log"))
    contents = recorder.path.read_text(encoding="utf-8")
    assert contents.startswith(first_contents)
    assert contents.count("[Agent Trace]") == 4


def test_rotation_limit_counts_separator_between_complete_operations(
    tmp_path: Path,
) -> None:
    recorder = CapturingTraceRecorder(tmp_path / "active", now=lambda: FIXED_NOW)
    _record_pair(recorder, "op-first")
    first_bytes = recorder.path.stat().st_size

    probe = CapturingTraceRecorder(tmp_path / "probe", now=lambda: FIXED_NOW)
    _record_pair(probe, "op-second")
    second_bytes = probe.path.stat().st_size

    recorder.max_file_bytes = first_bytes + second_bytes
    _record_pair(recorder, "op-second")

    archives = list(recorder.log_dir.glob("sakura-agent-trace.*.log"))
    assert len(archives) == 1
    assert archives[0].stat().st_size == first_bytes
    assert recorder.path.stat().st_size == second_bytes
