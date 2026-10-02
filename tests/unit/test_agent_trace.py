from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sakura_assistant.agent.trace import (
    AgentTraceRecorder,
    MessageProvenance,
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


def _record_pair(
    recorder: AgentTraceRecorder,
    operation_id: str,
    *,
    content: str = '{"segments":[{"ja":"こんばんは。","zh":"晚上好。"}]}',
) -> None:
    with recorder.operation(operation_id, finalize_external=True):
        call = recorder.start_model_call(
            model="example-model",
            payload={"messages": [
                {"role": "system", "content": "固定人格和回复协议"},
                {"role": "user", "content": "上一轮用户输入"},
                {"role": "user", "content": "当前用户输入"},
            ]},
            prompt_provenance=(
                MessageProvenance("system_prompt"),
                MessageProvenance("history"),
                MessageProvenance("user_input"),
            ),
        )
        recorder.record_model_reply(call, raw_message={"role": "assistant", "content": content})


def test_trace_preserves_conversation_order_without_repeating_static_prompt(tmp_path: Path) -> None:
    recorder = AgentTraceRecorder(tmp_path, now=lambda: FIXED_NOW)
    _record_pair(recorder, "op-order")
    text = recorder.path.read_text(encoding="utf-8")
    assert text.index("上一轮用户输入") < text.index("当前用户输入") < text.index("晚上好。")
    assert "固定人格和回复协议" not in text


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
        "binary_input": b"private-binary-body",
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
        "private-binary-body",
    ):
        assert secret not in text
    assert "普通正文" in text
    request = _documents(recorder)[0]
    binary = request["prompt"][1]["user_input"]["content"][1]["image_url"]["url"]
    assert binary["type"] == "binary"
    assert binary == {"type": "binary", "mime": "image/png", "bytes": 5}
    assert request["parameters"]["binary_input"] == {"type": "binary", "bytes": 19}
    assert "sha256" not in json.dumps(_documents(recorder))


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


def test_large_trace_value_preserves_both_ends_without_filling_the_log(tmp_path: Path) -> None:
    recorder = AgentTraceRecorder(tmp_path, now=lambda: FIXED_NOW)
    long_value = "消息开头" + "甲" * 600_000 + "中间原文应省略" + "甲" * 600_000 + "消息末尾"
    with recorder.operation("op-long", finalize_external=True):
        call = recorder.start_model_call(
            model="m",
            payload={
                "model": "m",
                "messages": [
                    {"role": "user", "content": long_value},
                ],
            },
            prompt_provenance=(MessageProvenance("user_input"),),
        )
        recorder.record_model_reply(call, raw_message={"content": "ok"})
    text = recorder.path.read_text(encoding="utf-8")
    assert "消息开头" in text and "消息末尾" in text
    assert "中间原文应省略" not in text
    assert recorder.path.stat().st_size < len(long_value.encode("utf-8"))


def test_write_failures_do_not_affect_model_boundary(tmp_path: Path) -> None:
    blocked = tmp_path / "blocked"
    blocked.write_text("not a directory", encoding="utf-8")
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
