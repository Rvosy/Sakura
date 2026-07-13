from __future__ import annotations

import base64
import json
import os
import secrets
import sys
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from PySide6.QtCore import QObject, QProcess, QProcessEnvironment, QThread, Qt, Signal, Slot

from app.core.runtime_log import log_event
from app.terminal.models import (
    TerminalReadResult,
    TerminalSpawnResult,
    TerminalState,
)


TERMINAL_PROTOCOL_VERSION = 1
TERMINAL_REQUEST_MARKER = "@@SAKURA_TERMINAL_REQUEST@@"
TERMINAL_RESULT_MARKER = "@@SAKURA_TERMINAL_RESULT@@"
TERMINAL_READY_MARKER = "@@SAKURA_TERMINAL_READY@@"
TERMINAL_RING_BYTES = 1024 * 1024
TERMINAL_REQUEST_TIMEOUT_SECONDS = 15.0
TAURI_TERMINAL_BIN_ENV = "SAKURA_TAURI_TERMINAL_BIN"


class TauriTerminalError(RuntimeError):
    pass


@dataclass
class _PendingRequest:
    event: threading.Event
    result: dict[str, Any] | None = None
    error: str = ""


def resolve_tauri_terminal_binary(
    base_dir: Path,
    *,
    environ: dict[str, str] | None = None,
    platform: str | None = None,
) -> Path | None:
    environment = os.environ if environ is None else environ
    current_platform = sys.platform if platform is None else platform
    override = environment.get(TAURI_TERMINAL_BIN_ENV, "").strip()
    if override:
        candidate = Path(override).expanduser()
        return candidate.resolve() if _usable_binary(candidate, current_platform) else None

    binary_name = "sakura-terminal.exe" if current_platform == "win32" else "sakura-terminal"
    root = Path(base_dir).resolve()
    candidates = (
        root / "tools" / "terminal-tauri" / "src-tauri" / "target" / "release" / binary_name,
        root / "tools" / "terminal-tauri" / "src-tauri" / "target" / "debug" / binary_name,
        root / binary_name,
    )
    return next(
        (candidate for candidate in candidates if _usable_binary(candidate, current_platform)),
        None,
    )


def _usable_binary(path: Path, platform: str) -> bool:
    return path.is_file() and (platform == "win32" or os.access(path, os.X_OK))


class TauriTerminalProcess(QObject):
    """把 worker 的同步工具调用安全地桥接到 UI 线程拥有的 QProcess。"""

    request_queued = Signal(str, str, object)
    show_queued = Signal()
    shutdown_queued = Signal(int)
    host_failed = Signal(str)

    def __init__(
        self,
        *,
        base_dir: Path,
        on_failure: Callable[[str], None] | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self.base_dir = Path(base_dir).resolve()
        self._nonce = secrets.token_urlsafe(24)
        self._process: QProcess | None = None
        self._stdout_buffer = ""
        self._ready = False
        self._queued_ids: list[str] = []
        self._request_payloads: dict[str, tuple[str, dict[str, Any]]] = {}
        self._pending_lock = threading.Lock()
        self._pending: dict[str, _PendingRequest] = {}
        self._closing = False

        self.request_queued.connect(self._dispatch_request, Qt.ConnectionType.QueuedConnection)
        self.show_queued.connect(self._show_on_ui, Qt.ConnectionType.QueuedConnection)
        self.shutdown_queued.connect(self._shutdown_on_ui, Qt.ConnectionType.QueuedConnection)
        if on_failure is not None:
            self.host_failed.connect(on_failure)

    @property
    def binary_available(self) -> bool:
        return resolve_tauri_terminal_binary(self.base_dir) is not None

    def spawn(
        self,
        *,
        command: tuple[str, ...],
        cwd: str,
        columns: int,
        rows: int,
        yield_time_ms: int,
        timeout_ms: int,
    ) -> TerminalSpawnResult:
        payload = self._call(
            "spawn",
            {
                "command": list(command),
                "cwd": cwd,
                "columns": columns,
                "rows": rows,
                "yield_time_ms": yield_time_ms,
                "timeout_ms": timeout_ms,
            },
            timeout_seconds=max(TERMINAL_REQUEST_TIMEOUT_SECONDS, yield_time_ms / 1000 + 10),
        )
        result = _terminal_result(payload)
        return TerminalSpawnResult(**result)

    def read(self, *, session_id: str, cursor: int, max_bytes: int) -> TerminalReadResult:
        payload = self._call(
            "read",
            {"session_id": session_id, "cursor": cursor, "max_bytes": max_bytes},
        )
        return TerminalReadResult(**_terminal_result(payload))

    def write(self, *, session_id: str, data: bytes) -> TerminalReadResult:
        payload = self._call(
            "write",
            {"session_id": session_id, "data_b64": base64.b64encode(data).decode("ascii")},
        )
        return TerminalReadResult(**_terminal_result(payload))

    def stop(self, *, session_id: str) -> TerminalReadResult:
        payload = self._call("stop", {"session_id": session_id})
        return TerminalReadResult(**_terminal_result(payload))

    def show(self) -> None:
        self.show_queued.emit()

    def shutdown(self, timeout_ms: int) -> None:
        if QThread.currentThread() is self.thread():
            self._shutdown_on_ui(timeout_ms)
        else:
            self.shutdown_queued.emit(timeout_ms)

    def _call(
        self,
        method: str,
        params: dict[str, Any],
        *,
        timeout_seconds: float = TERMINAL_REQUEST_TIMEOUT_SECONDS,
    ) -> dict[str, Any]:
        if QThread.currentThread() is self.thread():
            raise TauriTerminalError("终端工具不能阻塞 Qt UI 线程。")
        request_id = uuid.uuid4().hex
        pending = _PendingRequest(event=threading.Event())
        with self._pending_lock:
            if self._closing:
                raise TauriTerminalError("终端宿主正在关闭。")
            self._pending[request_id] = pending
        self.request_queued.emit(request_id, method, params)
        if not pending.event.wait(timeout_seconds):
            with self._pending_lock:
                self._pending.pop(request_id, None)
            raise TauriTerminalError("终端宿主请求超时。")
        if pending.error:
            raise TauriTerminalError(pending.error)
        if not isinstance(pending.result, dict):
            raise TauriTerminalError("终端宿主返回了无效结果。")
        return pending.result

    @Slot(str, str, object)
    def _dispatch_request(self, request_id: str, method: str, params: object) -> None:
        if self._closing:
            self._resolve_pending(request_id, error="终端宿主正在关闭。")
            return
        if not isinstance(params, dict):
            self._resolve_pending(request_id, error="终端请求参数无效。")
            return
        self._request_payloads[request_id] = (method, params)
        if not self._ensure_process():
            self._request_payloads.pop(request_id, None)
            self._resolve_pending(
                request_id,
                error=(
                    "终端程序（sakura-terminal）未找到或启动失败。"
                    "请构建 tools/terminal-tauri，或设置 SAKURA_TAURI_TERMINAL_BIN。"
                ),
            )
            return
        if self._ready:
            self._write_request(request_id)
        else:
            self._queued_ids.append(request_id)

    def _ensure_process(self) -> bool:
        process = self._process
        if process is not None and process.state() != QProcess.ProcessState.NotRunning:
            return True
        binary = resolve_tauri_terminal_binary(self.base_dir)
        if binary is None:
            return False
        process = QProcess(self)
        process.setProgram(str(binary))
        process.setWorkingDirectory(str(self.base_dir))
        process.setProcessEnvironment(QProcessEnvironment.systemEnvironment())
        process.started.connect(self._handle_started)
        process.readyReadStandardOutput.connect(self._handle_stdout)
        process.readyReadStandardError.connect(self._handle_stderr)
        process.finished.connect(self._handle_finished)
        process.errorOccurred.connect(self._handle_error)
        self._process = process
        self._ready = False
        self._stdout_buffer = ""
        process.start()
        return True

    @Slot()
    def _handle_started(self) -> None:
        process = self._process
        if process is None:
            return
        init = {
            "version": TERMINAL_PROTOCOL_VERSION,
            "nonce": self._nonce,
            "limits": {"ring_bytes": TERMINAL_RING_BYTES},
        }
        process.write((json.dumps(init, ensure_ascii=True) + "\n").encode("utf-8"))

    @Slot()
    def _handle_stdout(self) -> None:
        process = self._process
        if process is None:
            return
        self._stdout_buffer += bytes(process.readAllStandardOutput()).decode("utf-8", errors="replace")
        while "\n" in self._stdout_buffer:
            line, self._stdout_buffer = self._stdout_buffer.split("\n", 1)
            if line.startswith(TERMINAL_READY_MARKER):
                self._ready = True
                queued, self._queued_ids = self._queued_ids, []
                for request_id in queued:
                    self._write_request(request_id)
                continue
            if not line.startswith(TERMINAL_RESULT_MARKER):
                continue
            try:
                payload = json.loads(line[len(TERMINAL_RESULT_MARKER) :])
                request_id = str(payload.get("id") or "")
                if payload.get("ok") is True:
                    self._resolve_pending(request_id, result=payload.get("result"))
                else:
                    self._resolve_pending(
                        request_id,
                        error=str(payload.get("error") or "终端宿主执行失败。"),
                    )
            except (TypeError, ValueError, json.JSONDecodeError):
                continue

    @Slot()
    def _handle_stderr(self) -> None:
        process = self._process
        if process is None:
            return
        text = bytes(process.readAllStandardError()).decode("utf-8", errors="replace").strip()
        if text:
            log_event("Terminal", "终端宿主诊断", {"message": text[-500:]})

    def _write_request(self, request_id: str) -> None:
        process = self._process
        request = self._request_payloads.pop(request_id, None)
        if process is None or request is None:
            self._resolve_pending(request_id, error="终端宿主请求已失效。")
            return
        method, params = request
        payload = {
            "id": request_id,
            "version": TERMINAL_PROTOCOL_VERSION,
            "nonce": self._nonce,
            "method": method,
            "params": params,
        }
        line = TERMINAL_REQUEST_MARKER + json.dumps(payload, ensure_ascii=True) + "\n"
        if process.write(line.encode("utf-8")) < 0:
            self._resolve_pending(request_id, error="无法写入终端宿主。")

    @Slot()
    def _show_on_ui(self) -> None:
        if not self._ensure_process():
            self.host_failed.emit(
                "终端程序（sakura-terminal）未找到，请先构建终端组件。"
            )
            return
        request_id = uuid.uuid4().hex
        self._request_payloads[request_id] = ("show", {})
        if self._ready:
            self._write_request(request_id)
        else:
            self._queued_ids.append(request_id)

    @Slot(int)
    def _shutdown_on_ui(self, timeout_ms: int) -> None:
        self._closing = True
        process = self._process
        if process is not None and process.state() != QProcess.ProcessState.NotRunning:
            deadline = time.monotonic() + max(0, timeout_ms) / 1000

            def remaining_ms() -> int:
                return max(0, int((deadline - time.monotonic()) * 1000))

            payload = {
                "id": uuid.uuid4().hex,
                "version": TERMINAL_PROTOCOL_VERSION,
                "nonce": self._nonce,
                "method": "shutdown",
                "params": {},
            }
            line = TERMINAL_REQUEST_MARKER + json.dumps(payload, ensure_ascii=True) + "\n"
            process.write(line.encode("utf-8"))
            process.closeWriteChannel()
            if not process.waitForFinished(remaining_ms()):
                process.terminate()
                if not process.waitForFinished(remaining_ms()):
                    process.kill()
                    process.waitForFinished(remaining_ms())
        self._fail_all("终端宿主已关闭。", notify=False)
        self._process = None
        self._ready = False
        self._closing = False

    @Slot(int, QProcess.ExitStatus)
    def _handle_finished(self, exit_code: int, _status: QProcess.ExitStatus) -> None:
        if self._closing:
            return
        reason = f"终端宿主已退出（exit_code={exit_code}）。"
        self._fail_all(reason)
        self._process = None
        self._ready = False

    @Slot(QProcess.ProcessError)
    def _handle_error(self, _error: QProcess.ProcessError) -> None:
        process = self._process
        reason = process.errorString() if process is not None else "终端宿主启动失败。"
        self._fail_all(reason)

    def _resolve_pending(
        self,
        request_id: str,
        *,
        result: object = None,
        error: str = "",
    ) -> None:
        if not request_id:
            return
        with self._pending_lock:
            pending = self._pending.pop(request_id, None)
        if pending is None:
            return
        if isinstance(result, dict):
            pending.result = result
        pending.error = error
        pending.event.set()

    def _fail_all(self, reason: str, *, notify: bool = True) -> None:
        with self._pending_lock:
            pending = list(self._pending.values())
            self._pending.clear()
        self._queued_ids.clear()
        self._request_payloads.clear()
        for request in pending:
            request.error = reason
            request.event.set()
        if notify:
            self.host_failed.emit(reason)


def _terminal_result(payload: dict[str, Any]) -> dict[str, Any]:
    try:
        state = TerminalState(str(payload["state"]))
        session_id = str(payload["session_id"])
        output = base64.b64decode(str(payload.get("output_b64") or ""), validate=True)
        cursor = int(payload.get("cursor", 0))
        exit_code_raw = payload.get("exit_code")
        exit_code = int(exit_code_raw) if exit_code_raw is not None else None
    except (KeyError, TypeError, ValueError) as exc:
        raise TauriTerminalError("终端宿主返回了无效会话结果。") from exc
    if not session_id:
        raise TauriTerminalError("终端宿主返回了空会话 ID。")
    return {
        "session_id": session_id,
        "state": state,
        "output": output,
        "cursor": cursor,
        "exit_code": exit_code,
        "truncated": bool(payload.get("truncated", False)),
    }
