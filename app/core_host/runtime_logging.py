"""Bounded Runtime v2 logging bridge owned by the Python Core process."""

from __future__ import annotations

import json
import logging
import os
import re
import sys
import threading
import traceback
from collections import Counter, deque
from collections.abc import Mapping
from dataclasses import dataclass
from time import monotonic
from typing import Any, BinaryIO

from app.core.runtime_log import (
    LogEvent,
    register_external_sink,
    submit_external_log_event,
    unregister_external_sink,
)


CORE_BRIDGE_PREFIX = b"SAKURA_RUNTIME_LOG_V1\t"
TELEMETRY_BRIDGE_PREFIX = b"SAKURA_TELEMETRY_V1\t"
CORE_BRIDGE_QUEUE_CAPACITY = 256
CORE_BRIDGE_MAX_LINE_BYTES = 4 * 1024
TELEMETRY_BRIDGE_MAX_LINE_BYTES = 8 * 1024 + len(TELEMETRY_BRIDGE_PREFIX) + 1
CORE_BRIDGE_CLOSE_TIMEOUT_SECONDS = 0.5

_ACTIVE_BRIDGE_LOCK = threading.Lock()
_ACTIVE_BRIDGE: RuntimeLoggingBridge | None = None

_PRIORITY_SEVERITIES = frozenset({"warning", "error"})
_TOKEN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")
_CODE_RE = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")
_FORBIDDEN_KEY_MARKERS = (
    "authorization",
    "cookie",
    "credential",
    "api_key",
    "apikey",
    "token",
    "secret",
    "password",
    "prompt",
    "body",
    "content",
    "input",
    "output",
    "payload",
    "arguments",
    "query",
    "memory",
    "translation",
    "path",
    "message",
    "error",
    "reason",
)
_SAFE_ATTRIBUTE_KEYS = frozenset(
    {
        "action",
        "attempt",
        "bytes",
        "candidates",
        "category",
        "cause_type",
        "child_pid",
        "code",
        "command",
        "component",
        "count",
        "counts",
        "context_window_source",
        "context_window_tokens",
        "current_required_tokens",
        "deadline_ms",
        "detail_stage",
        "dependency",
        "diagnostic",
        "dropped_bytes",
        "dropped_count",
        "dropped_records",
        "dynamic_context_estimated_tokens",
        "elapsed_ms",
        "eof",
        "error_type",
        "failed",
        "failure_id",
        "eligible_turns",
        "created",
        "updated",
        "archived",
        "ignored",
        "filtered",
        "final_reply_elapsed_ms",
        "forced",
        "generation",
        "history_messages",
        "host_state",
        "input_target",
        "items",
        "listed",
        "lines",
        "name",
        "memories",
        "memory_estimated_tokens",
        "model",
        "model_call",
        "model_cached",
        "operation",
        "outcome",
        "output_reserve",
        "parse_status",
        "prompt_tokens",
        "completion_tokens",
        "total_tokens",
        "provider",
        "recording_id",
        "playback_id",
        "port",
        "progress",
        "duration_ms",
        "http_status",
        "retry_count",
        "provider_error_code",
        "provider_error_type",
        "purpose",
        "request_estimated_tokens",
        "required_context_tokens",
        "required_tokens",
        "retryable",
        "safety_margin",
        "segment_count",
        "saved_sections",
        "selection_saved",
        "tool_call_count",
        "tool_count",
        "tool_schema_estimated_tokens",
        "tool_schema_tokens",
        "estimated_tokens",
        "text_chars",
        "width",
        "height",
        "process_alive",
        "read_failed",
        "record_bytes",
        "record_truncated",
        "request",
        "reason_code",
        "registered",
        "revision",
        "reply_chars",
        "resolution",
        "risk",
        "selected",
        "segment_index",
        "server_id",
        "segments",
        "step_index",
        "stage",
        "static_prompt_tokens",
        "status",
        "exception_site",
        "tool_name",
        "trigger_turns",
        "tree_empty",
        "truncated",
        "truncated_records",
        "turn_elapsed_ms",
        "wait",
    }
)
_CORRELATION_KEYS = {
    "interaction_id": "operation_id",
    "operation_id": "operation_id",
    "request_id": "request_id",
    "action_id": "action_id",
    "trace_id": "trace_id",
}
_FORWARDED_RECORD_KEYS = frozenset(
    {
        "severity",
        "verbosity",
        "channel",
        "event",
        "message",
        "request_id",
        "operation_id",
        "action_id",
        "trace_id",
        "attributes",
    }
)
_FORWARDED_VERBOSITY = {
    "trace": 5,
    "debug": 3,
    "info": 1,
    "warn": 1,
    "error": 1,
}
_BODY_FREE_METRIC_KEYS = frozenset(
    {
        "completion_tokens",
        "context_window_tokens",
        "current_required_tokens",
        "dynamic_context_estimated_tokens",
        "estimated_tokens",
        "history_messages",
        "input_target",
        "memory_estimated_tokens",
        "output_reserve",
        "prompt_tokens",
        "request_estimated_tokens",
        "required_context_tokens",
        "required_tokens",
        "safety_margin",
        "static_prompt_tokens",
        "tool_schema_estimated_tokens",
        "tool_schema_tokens",
        "transport",
        "total_tokens",
    }
)
_SAFE_DIAGNOSTIC_KEYS = frozenset(
    {"diagnostic", "error_type", "provider_error_code", "provider_error_type", "reason_code"}
)
_CORE_CHANNELS = frozenset(
    {
        "agent",
        "api",
        "app",
        "chat",
        "config",
        "context",
        "core",
        "memory",
        "mcp",
        "plugin",
        "python.logging",
        "storage",
        "screen",
        "reply",
        "tool",
        "tts",
        "ui",
    }
)
_FIXED_MESSAGES = {
    "agent.turn.started": "Assistant turn started",
    "agent.turn.finished": "Assistant turn finished",
    "chat.request.received": "Chat request received",
    "chat.request.completed": "Chat request completed",
    "chat.request.cancelled": "Chat request cancelled",
    "chat.request.failed": "Chat request failed",
    "memory.recall.started": "Memory recall started",
    "memory.recall.finished": "Memory recall finished",
    "memory.recall.failed": "Memory recall failed",
    "memory.recall.unavailable": "Memory recall was not run because Memory is unavailable",
    "context.prompt.prepared": "Model context prepared",
    "context.dependencies.ready": "Prompt dependency ready",
    "context.dependencies.degraded": "Prompt dependency unavailable; chat continues degraded",
    "api.request.started": "Model request started",
    "api.request.finished": "Model request finished",
    "api.request.failed": "Model request failed",
    "api.response.received": "Model response received",
    "reply.processing.finished": "Model response processed",
    "reply.processing.repair_started": "Model response repair started",
    "reply.processing.failed": "Model response processing failed",
    "reply.display.completed": "Reply displayed",
    "reply.display.failed": "Reply display failed",
    "tool.execution.started": "Tool execution started",
    "tool.execution.waiting_confirmation": "Tool execution is waiting for confirmation",
    "tool.execution.finished": "Tool execution finished",
    "tool.execution.failed": "Tool execution failed",
    "screen.capture.started": "Screen capture started",
    "screen.capture.attached": "Screen capture attached",
    "screen.capture.cancelled": "Screen capture cancelled",
    "screen.capture.failed": "Screen capture failed",
    "tts.service.started": "TTS service started",
    "tts.service.waiting_ready": "TTS service process is waiting for readiness",
    "tts.service.ready": "TTS service ready",
    "tts.service.failed": "TTS service failed",
    "tts.process.cleanup.started": "TTS stale process cleanup started",
    "tts.process.cleanup.finished": "TTS stale process cleanup finished",
    "tts.process.cleanup.failed": "TTS stale process cleanup failed",
    "tts.settings.saved": "TTS settings saved",
    "tts.settings.partial": "TTS settings were partially saved",
    "tts.synthesis.started": "TTS synthesis started",
    "tts.synthesis.ready": "TTS synthesis ready",
    "tts.synthesis.finished": "TTS synthesis finished",
    "tts.synthesis.failed": "TTS synthesis failed",
    "tts.synthesis.cancelled": "TTS synthesis cancelled",
    "tts.recording.committed": "TTS recording committed",
    "tts.recording.failed": "TTS recording failed",
    "tts.playback.started": "TTS playback started",
    "tts.playback.finished": "TTS playback finished",
    "tts.playback.stopped": "TTS playback stopped",
    "tts.playback.failed": "TTS playback failed",
    "tts.request.started": "TTS synthesis started",
    "tts.request.finished": "TTS synthesis finished",
    "tts.request.failed": "TTS synthesis failed",
    "tts.service.http": "TTS service request completed",
    "tts.service.warning": "TTS service warning",
    "tts.service.stderr": "TTS service error",
    "tts.service.probe.started": "TTS service probe started",
    "tts.service.probe.failed": "TTS service probe not ready",
    "tts.service.synthesis.started": "TTS service synthesis started",
    "tts.service.text.received": "TTS service received synthesis text",
    "tts.service.info": "TTS service status updated",
    "tts.service.warmup_queued": "TTS service warmup queued",
    "tts.service.warmup_skipped": "TTS service warmup skipped",
    "tts.service.warmup_failed": "TTS service warmup failed",
    "tts.endpoint.ready": "TTS endpoint ready",
    "tts.weights.loading": "TTS weights loading",
    "tts.weights.ready": "TTS weights ready",
    "tts.weights.failed": "TTS weights failed",
    "mcp.server.ready": "MCP server ready",
    "mcp.ready": "MCP tools ready",
    "mcp.config.disabled": "MCP is disabled",
    "mcp.server.connecting": "MCP server connection started",
    "mcp.server.failed": "MCP server connection failed and was skipped",
    "mcp.tool.skipped": "MCP tool was skipped",
    "mcp.config.failed": "MCP configuration failed and was skipped",
    "mcp.tool.failed": "MCP tool invocation failed",
    "mcp.close.failed": "MCP connection close failed",
    "mcp.close.timeout": "MCP connection cleanup timed out",
    "plugin.loaded": "Plugin loaded",
    "settings.provider_model.slot_save_failed": "Plugin model slot save failed",
    "settings.provider_model.slot_save_reconciled": "Plugin model slot save reconciled",
    "startup.window_services.created": "Window services created",
    "startup.background_services.created": "Background services created",
    "startup.background_services.injected": "Background services injected",
    "core.process.started": "Core process logging started",
    "core.process.stopping": "Core process logging is stopping",
    "core.error.unhandled": "Unhandled Core error",
    "core.log.records_dropped": "Core log records were dropped",
    "memory.initialization.stage": "Memory initialization stage updated",
    "memory.curation.started": "Background memory curation started",
    "memory.curation.finished": "Background memory curation finished",
    "memory.curation.failed": "Background memory curation failed and will retry",
    "memory.curation.triggered": "Background memory curation triggered",
    "memory.curation.request_fuse_opened": "Background memory curation request fuse opened",
    "python.logging.info": "Python application log event",
    "python.logging.warning": "Python application warning",
    "python.logging.error": "Python application error",
}


@dataclass(frozen=True)
class _QueuedLine:
    line: bytes
    severity: str
    source: str


class _AppLoggingHandler(logging.Handler):
    def __init__(self, bridge: RuntimeLoggingBridge) -> None:
        super().__init__(level=logging.NOTSET)
        self._bridge = bridge

    def emit(self, record: logging.LogRecord) -> None:
        if record.name != "app" and not record.name.startswith("app."):
            return
        severity = _severity_from_logging_level(record.levelno)
        event = f"python.logging.{severity}"
        attributes: dict[str, object] = {
            "category": _safe_category(record.name),
        }
        if record.exc_info and isinstance(record.exc_info[0], type):
            attributes["code"] = "PYTHON_EXCEPTION"
            attributes["category"] = _safe_category(record.exc_info[0].__name__)
        self._bridge.emit_fixed(
            severity=severity,
            channel="python.logging",
            event=event,
            attributes=attributes,
        )


class RuntimeLoggingBridge:
    """Non-blocking producer queue with one stderr writer thread."""

    def __init__(
        self,
        stream: BinaryIO,
        *,
        capacity: int = CORE_BRIDGE_QUEUE_CAPACITY,
        start_worker: bool = True,
    ) -> None:
        self._stream = stream
        self._capacity = max(1, int(capacity))
        self._pending: deque[_QueuedLine] = deque()
        self._dropped: Counter[str] = Counter()
        self._condition = threading.Condition()
        self._stopping = False
        self._failed = False
        self._closed = False
        self._shutdown_deadline: float | None = None
        self._handler: _AppLoggingHandler | None = None
        self._app_logger: logging.Logger | None = None
        self._previous_logger_handlers: list[logging.Handler] = []
        self._previous_logger_level = logging.NOTSET
        self._previous_logger_propagate = True
        self._sink = self.submit
        self._worker = threading.Thread(
            target=self._run,
            name="sakura-core-runtime-log",
            daemon=True,
        )
        if start_worker:
            try:
                self._worker.start()
            except RuntimeError:
                self._failed = True

    @property
    def failed(self) -> bool:
        with self._condition:
            return self._failed

    def install(self) -> None:
        global _ACTIVE_BRIDGE
        register_external_sink(self._sink)
        logger = logging.getLogger("app")
        handler = _AppLoggingHandler(self)
        self._previous_logger_level = logger.level
        self._previous_logger_propagate = logger.propagate
        self._previous_logger_handlers = [*logger.handlers]
        for existing in self._previous_logger_handlers:
            logger.removeHandler(existing)
        logger.setLevel(logging.DEBUG)
        logger.propagate = False
        logger.addHandler(handler)
        self._app_logger = logger
        self._handler = handler
        with _ACTIVE_BRIDGE_LOCK:
            _ACTIVE_BRIDGE = self

    def submit(self, record: LogEvent) -> bool:
        wire = _wire_record_from_log_event(record)
        return self._enqueue_wire(wire, source="event")

    def emit_fixed(
        self,
        *,
        severity: str,
        channel: str,
        event: str,
        attributes: Mapping[str, object] | None = None,
        request_id: str | None = None,
        operation_id: str | None = None,
        action_id: str | None = None,
        trace_id: str | None = None,
    ) -> bool:
        severity = _normalize_severity(severity)
        wire: dict[str, object] = {
            "severity": severity,
            "verbosity": _verbosity_for_severity(severity),
            "channel": _safe_token(channel, 64) or "core.runtime",
            "event": _safe_token(event, 96) or "core.runtime.event",
            "message": _fixed_message(event),
        }
        for key, value in (
            ("request_id", request_id),
            ("operation_id", operation_id),
            ("action_id", action_id),
            ("trace_id", trace_id),
        ):
            safe = _safe_id(value)
            if safe is not None:
                wire[key] = safe
        safe_attributes = _safe_attributes(attributes)
        if safe_attributes:
            wire["attributes"] = safe_attributes
        return self._enqueue_wire(wire, source="fixed")

    def emit_unhandled(self, code: str, error: BaseException) -> bool:
        declared = str(getattr(error, "code", ""))
        if not _CODE_RE.fullmatch(declared):
            prefix = str(error).partition(":")[0].strip()
            declared = prefix if _CODE_RE.fullmatch(prefix) else ""
        stable_detail = declared if _CODE_RE.fullmatch(declared) else type(error).__name__
        diagnostic = {
            "TRANSPORT_WRITE_FAILED": "Core 协议写入通道意外关闭",
            "WRITER_QUEUE_CLOSED": "Core 协议写入队列已关闭",
            "GENERATION_CREDENTIAL_MISMATCH": "Core generation 凭据握手失败",
            "SHUTDOWN_DURING_INITIALIZE": "Assistant 后台初始化未在退出期限内结束",
        }.get(stable_detail, f"Core 进程边界异常：{_safe_category(type(error).__name__)}")
        logged = self.emit_fixed(
            severity="error",
            channel="core.process",
            event="core.error.unhandled",
            attributes={
                "code": code if _CODE_RE.fullmatch(code) else "CORE_UNHANDLED_ERROR",
                "category": _safe_category(type(error).__name__),
                "error_type": stable_detail,
                "diagnostic": diagnostic,
            },
        )
        telemetry_code = code if _CODE_RE.fullmatch(code) else "CORE_UNHANDLED_ERROR"
        self._enqueue_telemetry(
            "error",
            {
                "schema": 1,
                "component": "core",
                "event": "core.error.unhandled",
                "code": telemetry_code,
                "operationId": None,
                "exceptionType": _safe_token(type(error).__name__, 128),
                "stack": _safe_stack(error),
            },
            severity="error",
        )
        return logged

    def close(self, timeout: float = CORE_BRIDGE_CLOSE_TIMEOUT_SECONDS) -> bool:
        global _ACTIVE_BRIDGE
        timeout = min(max(0.0, float(timeout)), CORE_BRIDGE_CLOSE_TIMEOUT_SECONDS)
        if self._app_logger is not None and self._handler is not None:
            self._app_logger.removeHandler(self._handler)
            self._app_logger.setLevel(self._previous_logger_level)
            self._app_logger.propagate = self._previous_logger_propagate
            for previous in self._previous_logger_handlers:
                self._app_logger.addHandler(previous)
            self._previous_logger_handlers = []
            self._handler = None
            self._app_logger = None
        unregister_external_sink(self._sink)
        with _ACTIVE_BRIDGE_LOCK:
            if _ACTIVE_BRIDGE is self:
                _ACTIVE_BRIDGE = None
        with self._condition:
            if not self._closed:
                self._closed = True
                self._stopping = True
                self._shutdown_deadline = monotonic() + timeout
                self._condition.notify_all()
        if self._worker.ident is not None:
            self._worker.join(timeout=timeout)
            return not self._worker.is_alive()
        return True

    def _enqueue_wire(self, wire: Mapping[str, object], *, source: str) -> bool:
        severity = _normalize_severity(str(wire.get("severity", "info")))
        line = _encode_wire_record(wire)
        if line is None:
            with self._condition:
                self._note_dropped(source, severity)
            return False
        return self._enqueue_line(line, severity=severity, source=source)

    def _enqueue_telemetry(
        self,
        kind: str,
        payload: Mapping[str, object],
        *,
        severity: str = "info",
    ) -> bool:
        line = _encode_telemetry_record(kind, payload)
        if line is None:
            with self._condition:
                self._note_dropped("telemetry", severity)
            return False
        return self._enqueue_line(line, severity=severity, source="telemetry")

    def _enqueue_line(self, line: bytes, *, severity: str, source: str) -> bool:
        queued = _QueuedLine(line=line, severity=severity, source=source)
        with self._condition:
            if self._stopping or self._failed:
                return False
            if self._dropped and len(self._pending) + 2 <= self._capacity:
                self._pending.append(self._drop_summary())
            if len(self._pending) >= self._capacity:
                if severity in _PRIORITY_SEVERITIES:
                    low_priority = next(
                        (
                            index
                            for index, pending in enumerate(self._pending)
                            if pending.severity not in _PRIORITY_SEVERITIES
                        ),
                        None,
                    )
                    if low_priority is None:
                        self._note_dropped(source, severity)
                        return False
                    evicted = self._pending[low_priority]
                    del self._pending[low_priority]
                    self._note_dropped(evicted.source, evicted.severity)
                else:
                    self._note_dropped(source, severity)
                    return False
            self._pending.append(queued)
            self._condition.notify()
            return True

    def _note_dropped(self, source: str, severity: str) -> None:
        self._dropped[f"{_safe_category(source)}.{severity}"] += 1

    def _drop_summary(self) -> _QueuedLine:
        total = sum(self._dropped.values())
        counts = dict(self._dropped)
        self._dropped.clear()
        wire = {
            "severity": "warning",
            "verbosity": "warn",
            "channel": "core.runtime",
            "event": "core.log.records_dropped",
            "message": _fixed_message("core.log.records_dropped"),
            "attributes": {"dropped_count": total, "counts": counts},
        }
        line = _encode_wire_record(wire)
        assert line is not None
        return _QueuedLine(line=line, severity="warning", source="bridge")

    def _run(self) -> None:
        while True:
            with self._condition:
                while not self._pending and not self._dropped and not self._stopping:
                    self._condition.wait()
                if self._shutdown_deadline is not None and monotonic() >= self._shutdown_deadline:
                    self._pending.clear()
                    self._dropped.clear()
                    return
                if self._pending:
                    queued = self._pending.popleft()
                elif self._dropped:
                    queued = self._drop_summary()
                elif self._stopping:
                    return
                else:
                    continue
            try:
                self._stream.write(queued.line)
                self._stream.flush()
            except (BrokenPipeError, OSError, ValueError):
                with self._condition:
                    self._failed = True
                    self._pending.clear()
                    self._dropped.clear()
                    self._condition.notify_all()
                return


def install_runtime_logging(stream: BinaryIO | None = None) -> RuntimeLoggingBridge:
    bridge = RuntimeLoggingBridge(stream if stream is not None else _stderr_buffer())
    bridge.install()
    return bridge


def submit_telemetry_model_call(candidate: Mapping[str, object]) -> bool:
    """Submit one body-free, fixed-schema model metric to the active bridge."""

    if not _valid_model_call_candidate(candidate):
        return False
    with _ACTIVE_BRIDGE_LOCK:
        bridge = _ACTIVE_BRIDGE
    return bridge._enqueue_telemetry("modelCall", candidate) if bridge is not None else False


def forward_runtime_log_record(value: Mapping[str, object]) -> bool:
    """Forward a validated worker record only to the active Core log bridge."""

    if not isinstance(value, Mapping) or not set(value).issubset(_FORWARDED_RECORD_KEYS):
        return False
    try:
        encoded = json.dumps(
            dict(value),
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError):
        return False
    if len(CORE_BRIDGE_PREFIX) + len(encoded) + 1 > CORE_BRIDGE_MAX_LINE_BYTES:
        return False

    severity_value = value.get("severity")
    verbosity_value = value.get("verbosity")
    channel = _safe_token(value.get("channel"), 64)
    event = _safe_token(value.get("event"), 96)
    message = value.get("message")
    if (
        not isinstance(severity_value, str)
        or severity_value not in {"trace", "debug", "info", "warning", "error"}
        or not isinstance(verbosity_value, str)
        or verbosity_value not in _FORWARDED_VERBOSITY
        or channel is None
        or event is None
        or not isinstance(message, str)
        or len(message.encode("utf-8")) > 192
    ):
        return False

    raw_attributes = value.get("attributes")
    if raw_attributes is not None and not isinstance(raw_attributes, Mapping):
        return False
    attributes: dict[str, object] = _safe_attributes(raw_attributes)
    for key in ("request_id", "operation_id", "action_id"):
        safe = _safe_id(value.get(key))
        if value.get(key) is not None and safe is None:
            return False
        if safe is not None:
            attributes[key] = safe
    trace_id = _safe_id(value.get("trace_id"))
    if value.get("trace_id") is not None and trace_id is None:
        return False

    known_event = event in _FIXED_MESSAGES
    record = LogEvent(
        timestamp="",
        severity=str(severity_value) if known_event else "trace",
        verbosity=_FORWARDED_VERBOSITY[verbosity_value] if known_event else 5,
        channel=channel,
        event=event,
        message=_fixed_message(event),
        trace_id=trace_id or "",
        attributes=attributes or None,
        event_is_fixed=known_event,
    )
    return submit_external_log_event(record)


def _stderr_buffer() -> BinaryIO:
    stderr = sys.__stderr__
    buffer = getattr(stderr, "buffer", None)
    if buffer is not None:
        try:
            return _FileDescriptorBinaryStream(buffer.fileno())
        except (AttributeError, OSError, ValueError):
            pass
    return _NullBinaryStream()


class _FileDescriptorBinaryStream:
    """Write bridge frames without holding CPython's buffered-stderr lock."""

    def __init__(self, descriptor: int) -> None:
        self._descriptor = int(descriptor)

    def write(self, data: bytes) -> int:
        view = memoryview(data)
        written = 0
        while written < len(view):
            count = os.write(self._descriptor, view[written:])
            if count <= 0:
                raise OSError("stderr descriptor accepted no bytes")
            written += count
        return written

    def flush(self) -> None:
        return None


class _NullBinaryStream:
    def write(self, data: bytes) -> int:
        return len(data)

    def flush(self) -> None:
        return None


def _wire_record_from_log_event(record: LogEvent) -> dict[str, object]:
    severity = _normalize_severity(record.severity)
    known_event = record.event in _FIXED_MESSAGES
    if severity == "info":
        if not known_event or record.verbosity >= 5:
            severity = "trace"
        elif record.verbosity >= 3:
            severity = "debug"
    event = (
        _safe_token(record.event, 96)
        if record.event_is_fixed and known_event
        else "core.runtime.event"
    )
    wire: dict[str, object] = {
        "severity": severity,
        "verbosity": _verbosity_for_severity(severity),
        "channel": _safe_core_channel(record.channel),
        "event": event or "core.runtime.event",
        "message": _fixed_message(record.event),
    }
    attributes = record.attributes if isinstance(record.attributes, Mapping) else {}
    correlations: dict[str, str] = {}
    for source_key, target_key in _CORRELATION_KEYS.items():
        value = record.trace_id if source_key == "trace_id" else attributes.get(source_key)
        safe = _safe_id(value)
        if safe is not None:
            correlations[target_key] = safe
    wire.update(correlations)
    safe_attributes = _safe_attributes(attributes)
    if safe_attributes:
        wire["attributes"] = safe_attributes
    return wire


def _safe_attributes(attributes: Mapping[str, object] | None) -> dict[str, object]:
    if not isinstance(attributes, Mapping):
        return {}
    safe: dict[str, object] = {}
    for raw_key, value in list(attributes.items())[:32]:
        key = _canonical_key(raw_key)
        if (
            key in _CORRELATION_KEYS
            or key not in _SAFE_ATTRIBUTE_KEYS
            or (
                key not in {*_SAFE_DIAGNOSTIC_KEYS, *_BODY_FREE_METRIC_KEYS}
                and any(marker in key for marker in _FORBIDDEN_KEY_MARKERS)
            )
        ):
            continue
        if value is None or isinstance(value, bool):
            safe[key] = value
        elif isinstance(value, (int, float)) and not isinstance(value, bool):
            if isinstance(value, float) and (value != value or value in (float("inf"), float("-inf"))):
                continue
            safe[key] = value
        elif isinstance(value, str):
            if key == "diagnostic":
                diagnostic = _safe_diagnostic(value)
                if diagnostic is not None:
                    safe[key] = diagnostic
            else:
                token = _safe_token(value, 128)
                if token is not None:
                    safe[key] = token
        elif isinstance(value, (list, tuple, set, frozenset)):
            safe[key] = len(value)
        elif isinstance(value, Mapping):
            safe[key] = len(value)
    return safe


def _encode_wire_record(wire: Mapping[str, object]) -> bytes | None:
    candidate = dict(wire)
    line = _json_line(candidate)
    if len(line) <= CORE_BRIDGE_MAX_LINE_BYTES:
        return line
    candidate["attributes"] = {"record_truncated": True}
    line = _json_line(candidate)
    if len(line) <= CORE_BRIDGE_MAX_LINE_BYTES:
        return line
    for key in ("request_id", "operation_id", "action_id", "trace_id"):
        candidate.pop(key, None)
    line = _json_line(candidate)
    return line if len(line) <= CORE_BRIDGE_MAX_LINE_BYTES else None


def _encode_telemetry_record(kind: str, payload: Mapping[str, object]) -> bytes | None:
    key = "error" if kind == "error" else "modelCall" if kind == "modelCall" else ""
    if not key:
        return None
    try:
        encoded = json.dumps(
            {"kind": kind, key: dict(payload)},
            ensure_ascii=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError):
        return None
    line = TELEMETRY_BRIDGE_PREFIX + encoded + b"\n"
    return line if len(line) <= TELEMETRY_BRIDGE_MAX_LINE_BYTES else None


def _safe_stack(error: BaseException) -> list[dict[str, object]]:
    frames: list[dict[str, object]] = []
    for frame in traceback.extract_tb(error.__traceback__)[-16:]:
        module = frame.filename.replace("\\", "/")
        if "/app/" in module:
            relative = "app/" + module.rsplit("/app/", 1)[1]
        else:
            relative = ""
        function = _safe_token(frame.name, 128)
        item: dict[str, object] = {}
        if function is not None:
            item["function"] = function
        if relative and ".." not in relative and len(relative) <= 240:
            item["file"] = relative
        if isinstance(frame.lineno, int) and frame.lineno > 0:
            item["line"] = frame.lineno
        if item:
            frames.append(item)
    return frames


def _valid_model_call_candidate(candidate: Mapping[str, object]) -> bool:
    required = {
        "schema", "operationId", "modelCall", "purpose", "modelFamily", "outcome",
        "errorCode", "latencyMs", "contextWindowTokens", "contextWindowSource",
        "usage", "estimate",
    }
    if not isinstance(candidate, Mapping) or set(candidate) != required:
        return False
    if candidate.get("schema") != 1:
        return False
    operation_id = candidate.get("operationId")
    if operation_id is not None and _safe_id(operation_id) is None:
        return False
    return (
        isinstance(candidate.get("modelCall"), int)
        and not isinstance(candidate.get("modelCall"), bool)
        and candidate["modelCall"] >= 1
        and candidate.get("purpose") in {
            "agent_step", "final_reply", "reply_repair", "screen_observation",
            "proactive_reply", "background_agent", "memory_curation", "memory_curation_repair",
        }
        and candidate.get("modelFamily") in {"openai", "anthropic", "gemini", "deepseek", "custom", "unknown"}
        and candidate.get("outcome") in {"success", "failed", "cancelled"}
        and candidate.get("contextWindowSource") in {"provider", "configured", "fallback", "unknown"}
        and isinstance(candidate.get("latencyMs"), int)
        and not isinstance(candidate.get("latencyMs"), bool)
        and candidate["latencyMs"] >= 0
        and isinstance(candidate.get("contextWindowTokens"), int)
        and not isinstance(candidate.get("contextWindowTokens"), bool)
        and candidate["contextWindowTokens"] >= 0
        and (candidate.get("errorCode") is None or bool(_CODE_RE.fullmatch(str(candidate["errorCode"]))))
        and (candidate.get("outcome") != "success" or candidate.get("errorCode") is None)
        and (candidate.get("usage") is None or _valid_nonnegative_metrics(candidate["usage"], {
            "promptTokens", "completionTokens", "totalTokens", "inputTokens", "outputTokens",
            "cachedInputTokens", "reasoningTokens",
        }))
        and (candidate.get("estimate") is None or _valid_nonnegative_metrics(candidate["estimate"], {
            "requestTokens", "historyTokens", "memoryTokens", "dynamicContextTokens",
            "toolSchemaTokens", "historyMessages", "memories", "toolCount",
        }, allow_none=False))
    )


def _valid_nonnegative_metrics(value: object, keys: set[str], *, allow_none: bool = True) -> bool:
    if not isinstance(value, Mapping) or set(value) != keys:
        return False
    return all(
        (item is None and allow_none)
        or (isinstance(item, int) and not isinstance(item, bool) and item >= 0)
        for item in value.values()
    )


def _json_line(wire: Mapping[str, object]) -> bytes:
    payload = json.dumps(
        wire,
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return CORE_BRIDGE_PREFIX + payload + b"\n"


def _canonical_key(value: object) -> str:
    text = re.sub(r"[^A-Za-z0-9]+", "_", str(value)).strip("_").lower()
    aliases = {
        "deadlinems": "deadline_ms",
        "droppedbytes": "dropped_bytes",
        "droppedcount": "dropped_count",
        "droppedrecords": "dropped_records",
        "elapsedms": "elapsed_ms",
        "hoststate": "host_state",
        "recordbytes": "record_bytes",
        "recordtruncated": "record_truncated",
        "toolname": "tool_name",
        "treeempty": "tree_empty",
        "truncatedrecords": "truncated_records",
    }
    return aliases.get(text, text)


def _safe_token(value: object, maximum: int) -> str | None:
    if not isinstance(value, str) or not value or len(value.encode("utf-8")) > maximum:
        return None
    lower = value.lower()
    if lower.startswith(("sk-", "bearer")) or any(
        marker in lower
        for marker in ("credential", "password", "private_key", "secret=", "token=")
    ):
        return None
    return value if _TOKEN_RE.fullmatch(value) else None


def _safe_diagnostic(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    text = re.sub(r"[\x00-\x1f\x7f]+", " ", value)
    text = re.sub(r"(?i)\bbearer\s+[^\s,;]+", "Bearer [REDACTED]", text)
    text = re.sub(
        r"(?i)\b(api[_-]?key|authorization|cookie|password|secret|token)\s*[:=]\s*(?:bearer\s+)?[^\s,;]+",
        r"\1=[REDACTED]",
        text,
    )
    text = re.sub(r"(?i)\bsk-[A-Za-z0-9._-]{6,}", "[REDACTED]", text)
    text = re.sub(
        r"(?i)\b([a-z][a-z0-9+.-]*://)[^/@\s]+@",
        r"\1[REDACTED]@",
        text,
    )
    text = " ".join(text.split())[:320]
    return text or None


def _safe_core_channel(value: object) -> str:
    channel = _safe_token(value, 64)
    if channel in _CORE_CHANNELS or (channel is not None and channel.startswith("core.")):
        return channel
    return "core.runtime"


def _safe_id(value: object) -> str | None:
    return _safe_token(value, 128)


def _safe_category(value: object) -> str:
    token = _safe_token(str(value).replace(" ", "_"), 64)
    return token or "unknown"


def _normalize_severity(value: object) -> str:
    normalized = str(value or "info").strip().lower()
    if normalized == "warn":
        normalized = "warning"
    return normalized if normalized in {"trace", "debug", "info", "warning", "error"} else "info"


def _verbosity_for_severity(severity: str) -> str:
    return "warn" if severity == "warning" else severity


def _severity_from_logging_level(level: int) -> str:
    if level >= logging.ERROR:
        return "error"
    if level >= logging.WARNING:
        return "warning"
    if level >= logging.INFO:
        return "info"
    if level >= logging.DEBUG:
        return "debug"
    return "trace"


def _fixed_message(event: object) -> str:
    return _FIXED_MESSAGES.get(str(event), "Core internal diagnostic")


__all__ = [
    "CORE_BRIDGE_CLOSE_TIMEOUT_SECONDS",
    "CORE_BRIDGE_MAX_LINE_BYTES",
    "CORE_BRIDGE_PREFIX",
    "TELEMETRY_BRIDGE_PREFIX",
    "TELEMETRY_BRIDGE_MAX_LINE_BYTES",
    "CORE_BRIDGE_QUEUE_CAPACITY",
    "RuntimeLoggingBridge",
    "forward_runtime_log_record",
    "install_runtime_logging",
    "submit_telemetry_model_call",
]
