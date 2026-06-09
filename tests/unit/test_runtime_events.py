"""运行时事件系统（app/agent/runtime_events.py）单元测试。"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from app.agent.runtime_events import (
    APP_CLOSED,
    APP_STARTED,
    PET_HIDDEN,
    PET_REOPENED,
    RuntimeEvent,
    RuntimeEventLog,
    RuntimeEventQueue,
    build_runtime_event_context_message,
)


def _iso(seconds_ago: float = 0.0) -> str:
    return (
        datetime.now().astimezone() - timedelta(seconds=seconds_ago)
    ).isoformat(timespec="seconds")


# ---- RuntimeEvent 序列化 ----

def test_runtime_event_to_dict_and_from_dict_roundtrip() -> None:
    event = RuntimeEvent(
        event_type=PET_REOPENED,
        timestamp="2026-06-08T12:00:00+08:00",
        source="tray",
        metadata={"hidden_duration": 300},
        priority=1,
    )
    restored = RuntimeEvent.from_dict(event.to_dict())
    assert restored == event


def test_runtime_event_from_dict_rejects_invalid() -> None:
    assert RuntimeEvent.from_dict({}) is None
    assert RuntimeEvent.from_dict({"event_type": "  "}) is None
    assert RuntimeEvent.from_dict("not a dict") is None  # type: ignore[arg-type]


# ---- RuntimeEventQueue 去重 / 合并 / 容量 / 优先级 ----

def test_queue_drain_returns_and_clears() -> None:
    queue = RuntimeEventQueue()
    queue.push(RuntimeEvent(APP_STARTED))
    assert len(queue) == 1
    drained = queue.drain()
    assert [e.event_type for e in drained] == [APP_STARTED]
    assert len(queue) == 0
    assert queue.drain() == []


def test_queue_merges_consecutive_same_type_with_count() -> None:
    queue = RuntimeEventQueue()
    for _ in range(10):
        queue.push(RuntimeEvent("pet.clicked"))
    drained = queue.drain()
    assert len(drained) == 1
    assert drained[0].event_type == "pet.clicked"
    assert drained[0].metadata["count"] == 10


def test_queue_folds_hidden_when_reopened_pushed() -> None:
    queue = RuntimeEventQueue()
    queue.push(RuntimeEvent(PET_HIDDEN))
    queue.push(RuntimeEvent(PET_REOPENED, metadata={"hidden_duration": 5}))
    drained = queue.drain()
    assert [e.event_type for e in drained] == [PET_REOPENED]


def test_queue_caps_to_max_size_dropping_oldest() -> None:
    queue = RuntimeEventQueue(max_size=3)
    for index in range(5):
        # 不同类型避免被合并，便于验证容量丢弃。
        queue.push(RuntimeEvent(f"evt.{index}"))
    drained = queue.drain()
    assert [e.event_type for e in drained] == ["evt.2", "evt.3", "evt.4"]


def test_queue_drain_orders_by_priority_then_time() -> None:
    queue = RuntimeEventQueue()
    queue.push(RuntimeEvent("low", timestamp=_iso(seconds_ago=10), priority=0))
    queue.push(RuntimeEvent("high", timestamp=_iso(seconds_ago=0), priority=5))
    drained = queue.drain()
    assert [e.event_type for e in drained] == ["high", "low"]


# ---- 上下文消息构造 ----

def test_build_context_message_empty_returns_none() -> None:
    assert build_runtime_event_context_message([]) is None


def test_build_context_message_describes_events_and_warns_against_recital() -> None:
    message = build_runtime_event_context_message(
        [RuntimeEvent(PET_REOPENED, metadata={"hidden_duration": 300})]
    )
    assert message is not None
    assert message["role"] == "system"
    content = message["content"]
    assert "重新打开" in content
    assert "5 分钟" in content  # 300 秒 → 5 分钟
    assert "不要逐字复述" in content


# ---- RuntimeEventLog 落盘与跨会话衔接 ----

def test_event_log_append_and_read_recent_roundtrip(tmp_path) -> None:  # type: ignore[no-untyped-def]
    log = RuntimeEventLog(tmp_path / "runtime_events" / "sakura.jsonl")
    log.append(RuntimeEvent(APP_STARTED))
    log.append(RuntimeEvent(PET_HIDDEN, source="tray"))
    recent = log.read_recent()
    assert [e.event_type for e in recent] == [APP_STARTED, PET_HIDDEN]
    assert recent[1].source == "tray"


def test_event_log_read_recent_missing_file_returns_empty(tmp_path) -> None:  # type: ignore[no-untyped-def]
    log = RuntimeEventLog(tmp_path / "missing.jsonl")
    assert log.read_recent() == []


def test_load_startup_carryover_synthesizes_from_last_close(tmp_path) -> None:  # type: ignore[no-untyped-def]
    log = RuntimeEventLog(tmp_path / "events.jsonl")
    log.append(
        RuntimeEvent(
            APP_CLOSED,
            timestamp=_iso(seconds_ago=120),
            metadata={"interrupted_reply": True},
        )
    )
    carryover = log.load_startup_carryover()
    assert carryover is not None
    assert carryover["previous_reply_interrupted"] is True
    assert carryover["away_seconds"] >= 110  # 约 120 秒，留余量
    assert "previous_close_at" in carryover


def test_load_startup_carryover_without_close_returns_none(tmp_path) -> None:  # type: ignore[no-untyped-def]
    log = RuntimeEventLog(tmp_path / "events.jsonl")
    log.append(RuntimeEvent(PET_HIDDEN))
    assert log.load_startup_carryover() is None
