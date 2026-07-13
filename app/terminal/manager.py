from __future__ import annotations

import re
import threading
from pathlib import Path
from typing import Any

from app.agent.actions import ApprovalScope
from app.core.runtime_log import log_event
from app.terminal.models import (
    TerminalReadResult,
    TerminalSpawnResult,
    TerminalState,
    TerminalTransport,
)
from app.terminal.risk import DefaultTerminalRiskClassifier, TerminalRiskClassifier
from app.terminal.settings import TerminalSettings


TERMINAL_COLUMNS = 120
TERMINAL_ROWS = 30
TERMINAL_DEFAULT_YIELD_MS = 1000
TERMINAL_MIN_YIELD_MS = 250
TERMINAL_MAX_YIELD_MS = 10_000
TERMINAL_DEFAULT_TIMEOUT_MS = 120_000
TERMINAL_MAX_TIMEOUT_MS = 30 * 60 * 1000
TERMINAL_MODEL_READ_MAX_BYTES = 16 * 1024
TERMINAL_MODEL_TEXT_MAX_CHARS = 6000
TERMINAL_MAX_ARGS = 128
TERMINAL_MAX_ARG_CHARS = 4096
TERMINAL_MAX_COMMAND_CHARS = 16 * 1024

_ANSI_ESCAPE_RE = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")
_SECRET_ASSIGNMENT_RE = re.compile(
    r"(?i)\b(api[_-]?key|access[_-]?token|token|password|passwd|secret)\b(\s*[:=]\s*)([^\s]+)"
)
_BEARER_RE = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+")


class TerminalError(RuntimeError):
    pass


class TerminalDisabledError(TerminalError):
    pass


class TerminalBusyError(TerminalError):
    pass


class TerminalManager:
    """线程安全的终端策略、会话与授权所有者。"""

    def __init__(
        self,
        settings: TerminalSettings | None = None,
        *,
        risk_classifier: TerminalRiskClassifier | None = None,
    ) -> None:
        self._lock = threading.RLock()
        self._settings = (settings or TerminalSettings()).normalized()
        self._risk_classifier = risk_classifier or DefaultTerminalRiskClassifier()
        self._transport: TerminalTransport | None = None
        self._session_id = ""
        self._session_running = False
        self._process_grants: set[str] = set()

    @property
    def settings(self) -> TerminalSettings:
        with self._lock:
            return self._settings

    @property
    def enabled(self) -> bool:
        return self.settings.enabled

    @property
    def current_session_id(self) -> str:
        with self._lock:
            return self._session_id

    @property
    def has_running_session(self) -> bool:
        with self._lock:
            return self._session_running

    def bind_transport(self, transport: TerminalTransport | None) -> None:
        with self._lock:
            self._transport = transport
            if transport is None:
                self._revoke_session_locked()

    def update_settings(self, settings: TerminalSettings) -> None:
        normalized = settings.normalized()
        with self._lock:
            if not normalized.enabled and self._session_running:
                raise TerminalBusyError("终端会话仍在运行，请先停止会话再关闭终端能力。")
            self._settings = normalized
            if not normalized.enabled:
                self._revoke_session_locked()

    def execute(self, arguments: dict[str, Any]) -> dict[str, Any]:
        command = normalize_terminal_command(arguments.get("command"))
        cwd = self._resolve_cwd(arguments.get("cwd"))
        yield_time_ms = _bounded_int(
            arguments.get("yield_time_ms"),
            TERMINAL_DEFAULT_YIELD_MS,
            TERMINAL_MIN_YIELD_MS,
            TERMINAL_MAX_YIELD_MS,
        )
        timeout_ms = _bounded_int(
            arguments.get("timeout_ms"),
            TERMINAL_DEFAULT_TIMEOUT_MS,
            TERMINAL_MIN_YIELD_MS,
            TERMINAL_MAX_TIMEOUT_MS,
        )
        with self._lock:
            transport = self._require_transport_locked()
            if self._session_running:
                raise TerminalBusyError("已有终端进程正在运行，请先读取或停止当前会话。")
            self._process_grants.clear()
        try:
            result = transport.spawn(
                command=command,
                cwd=cwd,
                columns=TERMINAL_COLUMNS,
                rows=TERMINAL_ROWS,
                yield_time_ms=yield_time_ms,
                timeout_ms=timeout_ms,
            )
        except Exception:
            self._mark_transport_failed()
            raise
        self._accept_result(result)
        transport.show()
        log_event(
            "Terminal",
            "终端进程已创建",
            {
                "session_id": result.session_id,
                "state": result.state.value,
                "output_bytes": len(result.output),
                "exit_code": result.exit_code,
            },
        )
        return self._model_result(result)

    def read(self, arguments: dict[str, Any]) -> dict[str, Any]:
        session_id = self._require_owned_session(arguments.get("session_id"))
        cursor = _bounded_int(arguments.get("cursor"), 0, 0, 2**63 - 1)
        with self._lock:
            transport = self._require_transport_locked()
        try:
            result = transport.read(
                session_id=session_id,
                cursor=cursor,
                max_bytes=TERMINAL_MODEL_READ_MAX_BYTES,
            )
        except Exception:
            self._mark_transport_failed()
            raise
        self._accept_result(result)
        return self._model_result(result)

    def write(self, arguments: dict[str, Any]) -> dict[str, Any]:
        session_id = self._require_owned_session(arguments.get("session_id"), require_running=True)
        data = str(arguments.get("data") or "")
        if bool(arguments.get("append_newline", False)):
            data += "\n"
        if not data:
            raise TerminalError("终端写入内容不能为空。")
        encoded = data.encode("utf-8")
        if len(encoded) > TERMINAL_MODEL_READ_MAX_BYTES:
            raise TerminalError("单次终端写入不能超过 16 KiB。")
        with self._lock:
            transport = self._require_transport_locked()
        try:
            result = transport.write(session_id=session_id, data=encoded)
        except Exception:
            self._mark_transport_failed()
            raise
        self._accept_result(result)
        return self._model_result(result)

    def stop_session(self, arguments: dict[str, Any]) -> dict[str, Any]:
        session_id = self._require_owned_session(arguments.get("session_id"))
        with self._lock:
            transport = self._require_transport_locked()
        try:
            result = transport.stop(session_id=session_id)
        except Exception:
            self._mark_transport_failed()
            raise
        self._accept_result(result)
        return self._model_result(result)

    def show(self) -> None:
        with self._lock:
            transport = self._require_transport_locked()
        transport.show()

    def register_exec_approval(
        self,
        scope: ApprovalScope,
        content: object,
    ) -> None:
        if scope is not ApprovalScope.PROCESS or not isinstance(content, dict):
            return
        session_id = str(content.get("session_id") or "").strip()
        with self._lock:
            if session_id and session_id == self._session_id and self._session_running:
                self._process_grants.add(session_id)

    def has_process_grant(self, session_id: object) -> bool:
        normalized = str(session_id or "").strip()
        with self._lock:
            return bool(
                normalized
                and normalized == self._session_id
                and self._session_running
                and normalized in self._process_grants
            )

    def classify(self, command: tuple[str, ...]):  # type: ignore[no-untyped-def]
        return self._risk_classifier.classify(command)

    def stop(self, timeout_ms: int = 3000) -> None:
        with self._lock:
            transport = self._transport
            self._revoke_session_locked()
        if transport is not None:
            transport.shutdown(timeout_ms)

    def _resolve_cwd(self, value: object) -> str:
        configured = self.settings.default_cwd
        raw = str(value or configured).strip()
        path = Path(raw).expanduser()
        if not path.is_absolute() or not path.is_dir():
            raise TerminalError("终端工作目录必须是存在的绝对目录。")
        return str(path.resolve())

    def _require_transport_locked(self) -> TerminalTransport:
        if not self._settings.enabled:
            raise TerminalDisabledError("终端能力未启用。")
        if self._transport is None:
            raise TerminalError("可见终端尚未初始化。")
        return self._transport

    def _require_owned_session(self, value: object, *, require_running: bool = False) -> str:
        session_id = str(value or "").strip()
        with self._lock:
            if not session_id or session_id != self._session_id:
                raise TerminalError("终端会话不存在或不属于当前 Sakura 实例。")
            if require_running and not self._session_running:
                raise TerminalError("终端进程已经结束。")
        return session_id

    def _accept_result(self, result: TerminalSpawnResult | TerminalReadResult) -> None:
        if not result.session_id.strip():
            raise TerminalError("终端宿主返回了无效会话 ID。")
        with self._lock:
            self._session_id = result.session_id
            self._session_running = result.state is TerminalState.RUNNING
            if not self._session_running:
                self._process_grants.clear()

    def _revoke_session_locked(self) -> None:
        self._process_grants.clear()
        self._session_running = False
        self._session_id = ""

    def _mark_transport_failed(self) -> None:
        with self._lock:
            self._revoke_session_locked()

    def _model_result(self, result: TerminalSpawnResult | TerminalReadResult) -> dict[str, Any]:
        return {
            "session_id": result.session_id,
            "state": result.state.value,
            "cursor": int(result.cursor),
            "output": sanitize_terminal_output(result.output),
            "exit_code": result.exit_code,
            "truncated": bool(result.truncated),
            "untrusted_output": True,
        }


def normalize_terminal_command(value: object) -> tuple[str, ...]:
    if not isinstance(value, list) or not value:
        raise TerminalError("terminal_exec.command 必须是非空 argv 数组。")
    if len(value) > TERMINAL_MAX_ARGS:
        raise TerminalError(f"终端命令参数不能超过 {TERMINAL_MAX_ARGS} 项。")
    command: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item or "\x00" in item:
            raise TerminalError("终端命令参数必须是非空字符串且不能包含 NUL。")
        if len(item) > TERMINAL_MAX_ARG_CHARS:
            raise TerminalError("单个终端命令参数过长。")
        command.append(item)
    if sum(len(item) for item in command) > TERMINAL_MAX_COMMAND_CHARS:
        raise TerminalError("终端命令总长度过长。")
    return tuple(command)


def sanitize_terminal_output(output: bytes) -> str:
    text = output[:TERMINAL_MODEL_READ_MAX_BYTES].decode("utf-8", errors="replace")
    text = _ANSI_ESCAPE_RE.sub("", text)
    text = "".join(char for char in text if char in "\n\r\t" or ord(char) >= 32)
    text = _SECRET_ASSIGNMENT_RE.sub(lambda match: f"{match.group(1)}{match.group(2)}[REDACTED]", text)
    text = _BEARER_RE.sub("Bearer [REDACTED]", text)
    return text[:TERMINAL_MODEL_TEXT_MAX_CHARS]


def _bounded_int(value: object, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value) if value is not None else default
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(maximum, parsed))
