from __future__ import annotations

import io
import json
import logging

from app.core.interaction import get_interaction_id, interaction_context
from app.core.runtime_log import (
    RUNTIME_LOG_EXTERNAL_ONLY_KEY,
    diagnostic_attributes,
    log_event,
    suppress_runtime_logs,
)
from app.core_host.router import _request_interaction_context
from app.core_host.runtime_logging import (
    CORE_BRIDGE_MAX_LINE_BYTES,
    CORE_BRIDGE_PREFIX,
    RuntimeLoggingBridge,
    forward_runtime_log_record,
    install_runtime_logging,
)


PRIVATE_CHAT = "WP4L01 private chat body must never persist"
PRIVATE_TOOL_ARGUMENT = "WP4L01 tool argument must never persist"
PRIVATE_SECRET = "sk-WP4L01-PRIVATE-CREDENTIAL"


def _records(stream: io.BytesIO) -> list[dict[str, object]]:
    records = []
    for line in stream.getvalue().splitlines():
        if line.startswith(CORE_BRIDGE_PREFIX):
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


def test_external_only_mode_drops_when_bridge_is_absent(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv(RUNTIME_LOG_EXTERNAL_ONLY_KEY, "1")
    monkeypatch.setattr(
        "app.core.runtime_log._write_file_log",
        lambda _record: (_ for _ in ()).throw(AssertionError("Legacy file fallback")),
    )

    log_event("TTS", "发送 GPT-SoVITS 请求", {"text_chars": 4})


def test_core_bridge_maps_mcp_business_events_and_keeps_stable_reason_code() -> None:
    stream = io.BytesIO()
    bridge = install_runtime_logging(stream)
    try:
        with suppress_runtime_logs():
            log_event(
                "MCP",
                "连接或读取工具失败，已跳过",
                {
                    "server_id": "server-a1b2c3d4",
                    "reason_code": "CONNECT_TIMEOUT",
                    "error": PRIVATE_CHAT,
                },
            )
    finally:
        bridge.close()

    records = _records(stream)
    assert records == [
        {
            "severity": "error",
            "verbosity": "error",
            "channel": "mcp",
            "event": "mcp.server.failed",
            "message": "MCP server connection failed and was skipped",
            "attributes": {
                "server_id": "server-a1b2c3d4",
                "reason_code": "CONNECT_TIMEOUT",
            },
        }
    ]
    assert PRIVATE_CHAT not in stream.getvalue().decode("utf-8")


def test_core_bridge_keeps_mcp_tool_registration_counts() -> None:
    stream = io.BytesIO()
    bridge = install_runtime_logging(stream)
    try:
        with suppress_runtime_logs():
            log_event(
                "MCP",
                "服务器工具注册完成",
                {
                    "server_id": "server-a1b2c3d4",
                    "listed": 7,
                    "filtered": 2,
                    "registered": 5,
                },
            )
    finally:
        bridge.close()

    record = _records(stream)[0]
    assert record["event"] == "mcp.server.ready"
    assert record["attributes"] == {
        "server_id": "server-a1b2c3d4",
        "listed": 7,
        "filtered": 2,
        "registered": 5,
    }


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


def test_exception_diagnostics_add_safe_root_cause_location_and_failure_id() -> None:
    try:
        try:
            raise OSError(PRIVATE_CHAT)
        except OSError as cause:
            raise RuntimeError(PRIVATE_SECRET) from cause
    except RuntimeError as error:
        attributes = diagnostic_attributes(
            error,
            reason_code="FIXTURE_FAILED",
            stage="fixture",
        )

    assert attributes["error_type"] == "RuntimeError"
    assert attributes["cause_type"] == "OSError"
    assert ":test_exception_diagnostics_add_safe_root_cause_location_and_failure_id:" in attributes["exception_site"]
    assert len(attributes["failure_id"]) == 10
    assert attributes["failure_id"].isalnum()
    assert PRIVATE_CHAT not in str(attributes["exception_site"])
    assert PRIVATE_SECRET not in str(attributes["failure_id"])

    stream = io.BytesIO()
    bridge = install_runtime_logging(stream)
    try:
        log_event(
            "Chat",
            "对话请求失败",
            attributes,
            event="chat.request.failed",
            severity="error",
        )
    finally:
        bridge.close()
    forwarded = _records(stream)[0]["attributes"]
    assert forwarded["cause_type"] == "OSError"
    assert forwarded["exception_site"] == attributes["exception_site"]
    assert forwarded["failure_id"] == attributes["failure_id"]
    assert PRIVATE_CHAT not in stream.getvalue().decode("utf-8")
    assert PRIVATE_SECRET not in stream.getvalue().decode("utf-8")


def test_unhandled_transport_error_uses_only_stable_safe_diagnostic() -> None:
    from app.core_host.server import WriterError

    stream = io.BytesIO()
    bridge = install_runtime_logging(stream)
    try:
        bridge.emit_unhandled(
            "CORE_HOST_TRANSPORT_ERROR",
            WriterError("TRANSPORT_WRITE_FAILED", PRIVATE_CHAT),
        )
    finally:
        bridge.close()

    event = _records(stream)[0]
    assert event["attributes"] == {
        "code": "CORE_HOST_TRANSPORT_ERROR",
        "category": "WriterError",
        "error_type": "TRANSPORT_WRITE_FAILED",
        "diagnostic": "Core 协议写入通道意外关闭",
    }
    assert PRIVATE_CHAT not in stream.getvalue().decode("utf-8")


def test_unhandled_transport_error_can_recover_only_a_stable_code_prefix() -> None:
    stream = io.BytesIO()
    bridge = install_runtime_logging(stream)
    try:
        bridge.emit_unhandled(
            "CORE_HOST_TRANSPORT_ERROR",
            RuntimeError(f"SHUTDOWN_DURING_INITIALIZE: {PRIVATE_CHAT}"),
        )
    finally:
        bridge.close()

    event = _records(stream)[0]
    assert event["attributes"]["error_type"] == "SHUTDOWN_DURING_INITIALIZE"
    assert event["attributes"]["diagnostic"] == "Assistant 后台初始化未在退出期限内结束"
    assert PRIVATE_CHAT not in stream.getvalue().decode("utf-8")


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



def test_business_event_keeps_correlation_and_body_free_prompt_metrics() -> None:
    stream = io.BytesIO()
    bridge = install_runtime_logging(stream)
    try:
        with interaction_context("chat-1234567890"):
            log_event(
                "Context",
                "模型上下文已构建",
                {
                    "trace_id": "17",
                    "model_call": 2,
                    "purpose": "agent_step",
                    "model": "example-model",
                    "history_messages": 8,
                    "memories": 3,
                    "tool_count": 18,
                    "estimated_tokens": 11684,
                    "content": PRIVATE_CHAT,
                },
                event="context.prompt.prepared",
                verbosity=1,
            )
    finally:
        bridge.close()

    event = _records(stream)[0]
    assert event["severity"] == "info"
    assert event["channel"] == "context"
    assert event["event"] == "context.prompt.prepared"
    assert event["operation_id"] == "chat-1234567890"
    assert event["trace_id"] == "17"
    assert event["attributes"] == {
        "model_call": 2,
        "purpose": "agent_step",
        "model": "example-model",
        "history_messages": 8,
        "memories": 3,
        "tool_count": 18,
        "estimated_tokens": 11684,
    }
    assert PRIVATE_CHAT not in stream.getvalue().decode("utf-8")


def test_prompt_dependency_degradation_keeps_safe_root_cause() -> None:
    stream = io.BytesIO()
    bridge = RuntimeLoggingBridge(stream, start_worker=False)
    bridge.install()
    try:
        with interaction_context("chat-dependency-1"):
            log_event(
                "Context",
                "Prompt 依赖未就绪，继续降级对话",
                {
                    "dependency": "memory",
                    "status": "degraded",
                    "reason_code": "PROCESS_EXITED",
                    "stage": "process_exit",
                    "category": "process_exited",
                    "error_type": "ChildProcessExit",
                    "elapsed_ms": 5021,
                    "message": PRIVATE_CHAT,
                },
            )
    finally:
        bridge.close()
        while bridge._pending:  # noqa: SLF001 - deterministic bridge projection
            stream.write(bridge._pending.popleft().line)  # noqa: SLF001

    event = _records(stream)[0]
    assert event["event"] == "context.dependencies.degraded"
    assert event["operation_id"] == "chat-dependency-1"
    assert event["attributes"] == {
        "dependency": "memory",
        "status": "degraded",
        "reason_code": "PROCESS_EXITED",
        "stage": "process_exit",
        "category": "process_exited",
        "error_type": "ChildProcessExit",
        "elapsed_ms": 5021,
    }
    assert PRIVATE_CHAT not in stream.getvalue().decode("utf-8")


def test_unknown_info_event_is_not_promoted_to_user_visible_info() -> None:
    stream = io.BytesIO()
    bridge = install_runtime_logging(stream)
    try:
        log_event("MCP", "内部握手细节", event="mcp.internal.handshake")
    finally:
        bridge.close()

    event = _records(stream)[0]
    assert event["event"] == "core.runtime.event"
    assert event["severity"] in {"debug", "trace"}
    assert event["message"] == "Core internal diagnostic"


def test_tts_business_event_keeps_text_size_without_text() -> None:
    stream = io.BytesIO()
    bridge = install_runtime_logging(stream)
    try:
        with interaction_context("chat-tts-1"):
            log_event(
                "TTS",
                "发送 GPT-SoVITS 请求",
                {"text": PRIVATE_CHAT, "provider": "gpt_sovits"},
            )
    finally:
        bridge.close()

    event = _records(stream)[0]
    assert event["event"] == "tts.request.started"
    assert event["operation_id"] == "chat-tts-1"
    assert event["attributes"] == {
        "provider": "gpt_sovits",
        "text_chars": len(PRIVATE_CHAT),
    }
    assert PRIVATE_CHAT not in stream.getvalue().decode("utf-8")


def test_forwarded_worker_record_uses_only_active_sink_and_reapplies_safety(
    monkeypatch,
) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(
        "app.core.runtime_log._write_file_log",
        lambda _record: (_ for _ in ()).throw(AssertionError("Legacy file fallback")),
    )
    forwarded = {
        "severity": "info",
        "verbosity": "info",
        "channel": "tts",
        "event": "tts.request.started",
        "message": "untrusted worker message",
        "operation_id": "chat-worker-1",
        "attributes": {
            "provider": "gpt_sovits",
            "text_chars": 41,
            "attempt": 1,
            "text": PRIVATE_CHAT,
            "authorization": PRIVATE_SECRET,
            "api_url": "http://127.0.0.1:9880/tts",
            "weights_path": r"D:\private\voice.pth",
        },
    }

    assert forward_runtime_log_record(forwarded) is False

    stream = io.BytesIO()
    bridge = install_runtime_logging(stream)
    try:
        assert forward_runtime_log_record(forwarded) is True
        assert forward_runtime_log_record(
            {
                **forwarded,
                "severity": "error",
                "verbosity": "error",
                "event": "tts.private.unknown",
            }
        )
        assert not forward_runtime_log_record({**forwarded, "unexpected": True})
        assert not forward_runtime_log_record({**forwarded, "severity": []})
        assert not forward_runtime_log_record(
            {**forwarded, "attributes": {"diagnostic": "x" * 4096}}
        )
    finally:
        bridge.close()

    records = _records(stream)
    event = records[0]
    assert event["event"] == "tts.request.started"
    assert event["operation_id"] == "chat-worker-1"
    assert event["message"] == "TTS synthesis started"
    assert event["attributes"] == {
        "provider": "gpt_sovits",
        "text_chars": 41,
        "attempt": 1,
    }
    persisted = stream.getvalue().decode("utf-8")
    assert records[1]["event"] == "core.runtime.event"
    assert records[1]["severity"] in {"debug", "trace"}
    assert "127.0.0.1" not in persisted
    assert "voice.pth" not in persisted
    assert PRIVATE_CHAT not in persisted
    assert PRIVATE_SECRET not in persisted


def test_api_failure_keeps_bounded_diagnostic_but_redacts_credentials() -> None:
    stream = io.BytesIO()
    bridge = install_runtime_logging(stream)
    try:
        log_event(
            "API",
            "HTTP 请求失败",
            {
                "status": 401,
                "provider_error_type": "authentication_error",
                "provider_error_code": "invalid_api_key",
                "diagnostic": (
                    "Invalid authentication credentials; Authorization: Bearer "
                    f"{PRIVATE_SECRET}; token=visible"
                ),
                "content": PRIVATE_CHAT,
            },
            event="api.request.failed",
            severity="warning",
            verbosity=0,
        )
    finally:
        bridge.close()

    serialized = stream.getvalue().decode("utf-8")
    event = _records(stream)[0]
    assert event["attributes"]["provider_error_type"] == "authentication_error"
    assert event["attributes"]["provider_error_code"] == "invalid_api_key"
    assert "Invalid authentication credentials" in event["attributes"]["diagnostic"]
    assert "[REDACTED]" in event["attributes"]["diagnostic"]
    assert PRIVATE_SECRET not in serialized
    assert "token=visible" not in serialized
    assert PRIVATE_CHAT not in serialized
