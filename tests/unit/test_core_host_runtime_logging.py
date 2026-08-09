from __future__ import annotations

import io
import json
import logging

from app.agent import memory as memory_module
from app.core.interaction import get_interaction_id, interaction_context
from app.core.runtime_log import log_event, suppress_runtime_logs
from app.core_host.router import _request_interaction_context
from app.core_host.runtime_logging import (
    CORE_BRIDGE_MAX_LINE_BYTES,
    CORE_BRIDGE_PREFIX,
    RuntimeLoggingBridge,
    install_runtime_logging,
)


PRIVATE_CHAT = "WP4L01 private chat body must never persist"
PRIVATE_TOOL_ARGUMENT = "WP4L01 tool argument must never persist"
PRIVATE_SECRET = "sk-WP4L01-PRIVATE-CREDENTIAL"


def _records(stream: io.BytesIO) -> list[dict[str, object]]:
    records = []
    for line in stream.getvalue().splitlines():
        assert line.startswith(CORE_BRIDGE_PREFIX)
        records.append(json.loads(line.removeprefix(CORE_BRIDGE_PREFIX)))
    return records


def test_core_bridge_forwards_suppressed_log_events_without_legacy_outputs(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    stream = io.BytesIO()
    bridge = install_runtime_logging(stream)
    monkeypatch.setattr(
        "app.core.runtime_log._write_file_log",
        lambda _record: (_ for _ in ()).throw(AssertionError("Legacy file writer was used")),
    )
    monkeypatch.setattr(
        "app.core.runtime_log.console_log_enabled",
        lambda: (_ for _ in ()).throw(AssertionError("Legacy debug settings were read")),
    )
    try:
        with interaction_context("operation-wp-4l-01"), suppress_runtime_logs():
            log_event(
                "AgentRuntime",
                "开始处理用户消息",
                {
                    "content": PRIVATE_CHAT,
                    "arguments": {"query": PRIVATE_TOOL_ARGUMENT},
                    "api_key": PRIVATE_SECRET,
                    "tool_name": "memory.search",
                    "elapsed_ms": 12,
                },
            )
    finally:
        bridge.close()

    records = _records(stream)
    event = next(record for record in records if record["event"] == "agent.turn.started")
    assert event["operation_id"] == "operation-wp-4l-01"
    assert event["message"] == "Assistant turn started"
    assert event["attributes"] == {"tool_name": "memory.search", "elapsed_ms": 12}
    serialized = stream.getvalue().decode("utf-8")
    assert PRIVATE_CHAT not in serialized
    assert PRIVATE_TOOL_ARGUMENT not in serialized
    assert PRIVATE_SECRET not in serialized


def test_router_chat_operation_context_is_scoped_and_content_derived_events_are_removed() -> None:
    stream = io.BytesIO()
    bridge = install_runtime_logging(stream)
    try:
        assert get_interaction_id() == ""
        with _request_interaction_context({"name": "chat.send", "id": "operation-router-1"}):
            assert get_interaction_id() == "operation-router-1"
            log_event("PrivateChannel", PRIVATE_CHAT, {"status": "completed"})
        assert get_interaction_id() == ""
    finally:
        bridge.close()

    serialized = stream.getvalue().decode("utf-8")
    assert PRIVATE_CHAT not in serialized
    record = _records(stream)[0]
    assert record["channel"] == "core.runtime"
    assert record["event"] == "core.runtime.event"
    assert record["operation_id"] == "operation-router-1"


def test_app_logging_handler_never_formats_message_or_traceback() -> None:
    stream = io.BytesIO()
    bridge = install_runtime_logging(stream)
    logger = logging.getLogger("app.core.private_boundary")
    try:
        try:
            raise RuntimeError(PRIVATE_CHAT)
        except RuntimeError:
            logger.exception("%s %s", PRIVATE_CHAT, PRIVATE_SECRET)
    finally:
        bridge.close()

    serialized = stream.getvalue().decode("utf-8")
    assert PRIVATE_CHAT not in serialized
    assert PRIVATE_SECRET not in serialized
    event = next(record for record in _records(stream) if record["event"] == "python.logging.error")
    assert event["message"] == "Python application error"
    assert event["attributes"] == {
        "category": "RuntimeError",
        "code": "PYTHON_EXCEPTION",
    }


def test_bridge_queue_evicts_low_priority_and_aggregates_drops() -> None:
    stream = io.BytesIO()
    bridge = RuntimeLoggingBridge(stream, capacity=2, start_worker=False)
    assert bridge.emit_fixed(severity="info", channel="test", event="test.info.1")
    assert bridge.emit_fixed(severity="debug", channel="test", event="test.info.2")
    assert bridge.emit_fixed(severity="error", channel="test", event="test.error")
    assert len(bridge._pending) == 2  # noqa: SLF001 - deterministic queue contract test
    assert any(item.severity == "error" for item in bridge._pending)  # noqa: SLF001
    assert sum(bridge._dropped.values()) == 1  # noqa: SLF001
    summary = json.loads(  # noqa: SLF001
        bridge._drop_summary().line.strip().removeprefix(CORE_BRIDGE_PREFIX)
    )
    assert summary["attributes"] == {
        "dropped_count": 1,
        "counts": {"fixed.info": 1},
    }
    assert bridge.close()


def test_bridge_records_are_bounded_and_unknown_attributes_are_removed() -> None:
    stream = io.BytesIO()
    bridge = install_runtime_logging(stream)
    try:
        bridge.emit_fixed(
            severity="warning",
            channel="core.test",
            event="core.test.bounded",
            attributes={
                "status": "x" * 10_000,
                "arbitrary": PRIVATE_CHAT,
                "body": PRIVATE_TOOL_ARGUMENT,
                "record_bytes": 10_000,
            },
            operation_id="operation-1",
        )
    finally:
        bridge.close()
    lines = stream.getvalue().splitlines(keepends=True)
    assert lines
    assert all(len(line) <= CORE_BRIDGE_MAX_LINE_BYTES for line in lines)
    serialized = stream.getvalue().decode("utf-8")
    assert PRIVATE_CHAT not in serialized
    assert PRIVATE_TOOL_ARGUMENT not in serialized
    record = _records(stream)[0]
    assert record["attributes"] == {"record_bytes": 10_000}


class _BrokenStream:
    def write(self, _data: bytes) -> int:
        raise BrokenPipeError

    def flush(self) -> None:
        return None


def test_broken_stderr_is_isolated_from_producers() -> None:
    bridge = RuntimeLoggingBridge(_BrokenStream())
    assert bridge.emit_fixed(severity="error", channel="core.test", event="core.test.failure")
    bridge.close()
    assert bridge.failed


def test_runtime_v2_memory_diagnostic_preserves_existing_legacy_file(tmp_path) -> None:  # type: ignore[no-untyped-def]
    legacy_path = tmp_path / "data/logs/memory-initialization.jsonl"
    legacy_path.parent.mkdir(parents=True)
    legacy_contents = "LEGACY MEMORY DIAGNOSTIC\n"
    legacy_path.write_text(legacy_contents, encoding="utf-8")
    stream = io.BytesIO()
    bridge = install_runtime_logging(stream)
    try:
        memory_module.append_memory_initialization_diagnostic(
            tmp_path,
            component="memory_store",
            event="embedding_load_failed",
            stage="embedding_load",
            outcome="failed",
            category="dependency_unavailable",
            error_type="ImportError",
            elapsed_ms=17,
            model_cached=False,
            child_pid=42,
            process_alive=False,
        )
    finally:
        bridge.close()

    assert legacy_path.read_text(encoding="utf-8") == legacy_contents
    event = next(
        record
        for record in _records(stream)
        if record["event"] == "memory.initialization.stage"
    )
    assert event["message"] == "Memory initialization stage updated"
    assert event["attributes"] == {
        "component": "memory_store",
        "detail_stage": "embedding_load_failed",
        "stage": "embedding_load",
        "outcome": "failed",
        "category": "dependency_unavailable",
        "error_type": "ImportError",
        "elapsed_ms": 17,
        "model_cached": False,
        "child_pid": 42,
        "process_alive": False,
    }
