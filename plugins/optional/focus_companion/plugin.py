from __future__ import annotations

import math
import re
import threading
import time
from collections import OrderedDict
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any


SERVICE_KEY = "focus_companion.executor"
MAX_RECENT_OPERATIONS = 32
MAX_DURATION_SECONDS = 120 * 60
HELP_TEXT = "发送“专注 10 秒 整理桌面”或“专注 1 分钟 阅读”开始计时。计时期间可点击停止。"
_COMMAND = re.compile(r"专注\s*([0-9]{1,4})\s*(秒|分钟)\s*(.*)", re.DOTALL)


@dataclass(frozen=True)
class _Request:
    operation_id: str
    generation_id: str
    turn_id: str
    character_id: str
    text: str


@dataclass
class _Operation:
    request: _Request
    duration: int = 0
    task: str = ""
    state: str = "running"
    sequence: int = 0
    progress: str = ""
    result_text: str | None = None
    outcome: str | None = None
    stop: threading.Event = field(default_factory=threading.Event)
    thread: threading.Thread | None = None


class FocusCompanionPlugin:
    """An offline executor using the same public entry point as dialogue plugins."""

    def setup(self, context: Any) -> None:
        service = FocusCompanionExecutor(context)
        context.effect(service.close)
        context.provide(SERVICE_KEY, service, exports=("describe", "begin", "read", "cancel"))
        context.get("sakura.host.executors").register(
            {"serviceKey": SERVICE_KEY, "displayName": "专注陪伴"}
        )


class FocusCompanionExecutor:
    def __init__(self, context: Any) -> None:
        self._context = context
        self._lock = threading.Lock()
        self._closed = False
        self._operations: OrderedDict[str, _Operation] = OrderedDict()

    def describe(self) -> dict[str, object]:
        with self._lock:
            return {"schemaVersion": 1, "ready": not self._closed, "inputs": ["text"]}

    def begin(self, request: Mapping[str, Any]) -> dict[str, object]:
        self._require_host()
        info = _parse_request(request)
        with self._lock:
            if self._closed:
                raise RuntimeError("FOCUS_COMPANION_CLOSED")
            existing = self._operations.get(info.operation_id)
            if existing is not None:
                if existing.request != info:
                    raise ValueError("FOCUS_OPERATION_CONFLICT")
                return self._snapshot_locked(existing)
            for operation in self._operations.values():
                self._refresh_locked(operation)
                if operation.state in {"running", "cancelling"}:
                    raise RuntimeError("FOCUS_COMPANION_BUSY")
            while len(self._operations) >= MAX_RECENT_OPERATIONS:
                self._operations.popitem(last=False)
            operation = _Operation(info)
            self._operations[info.operation_id] = operation
            parsed = _parse_command(info.text)
            if isinstance(parsed, str):
                operation.state = "completed"
                operation.result_text = parsed
                return self._snapshot_locked(operation)
            operation.duration, operation.task = parsed
            operation.progress = _progress(operation.task, operation.duration)
            operation.thread = threading.Thread(
                target=self._work,
                args=(operation,),
                name="focus-companion-timer",
                daemon=True,
            )
            try:
                operation.thread.start()
            except RuntimeError:
                operation.outcome = "failed"
                operation.state = "failed"
                raise
            return self._snapshot_locked(operation)

    def read(self, request: Mapping[str, Any]) -> dict[str, object]:
        self._require_host()
        operation_id = _operation_id(request)
        with self._lock:
            return self._snapshot_locked(self._find_locked(operation_id))

    def cancel(self, request: Mapping[str, Any]) -> dict[str, object]:
        self._require_host()
        operation_id = _operation_id(request)
        with self._lock:
            operation = self._find_locked(operation_id)
            self._refresh_locked(operation)
            if operation.state in {"running", "cancelling"}:
                operation.stop.set()
                if operation.state != "cancelling":
                    operation.state = "cancelling"
                    operation.progress = "正在停止计时…"
                    operation.sequence += 1
            return self._snapshot_locked(operation)

    def close(self) -> None:
        with self._lock:
            self._closed = True
            operations = tuple(self._operations.values())
            for operation in operations:
                self._refresh_locked(operation)
                if operation.state in {"running", "cancelling"}:
                    operation.stop.set()
                    operation.state = "cancelling"
            threads = [operation.thread for operation in operations if operation.thread is not None]
        for thread in threads:
            if thread.ident is not None:
                thread.join()
        with self._lock:
            for operation in operations:
                self._refresh_locked(operation)

    def _require_host(self) -> None:
        if self._context.caller_id != "sakura.core":
            raise PermissionError("FOCUS_EXECUTOR_CALLER_FORBIDDEN")

    def _find_locked(self, operation_id: str) -> _Operation:
        try:
            return self._operations[operation_id]
        except KeyError as error:
            raise ValueError("FOCUS_OPERATION_NOT_FOUND") from error

    @staticmethod
    def _refresh_locked(operation: _Operation) -> None:
        # Terminal state is visible only after the worker has actually exited.
        if operation.thread is None or operation.thread.is_alive() or operation.outcome is None:
            return
        if operation.state in {"running", "cancelling"}:
            operation.state = "cancelled" if operation.stop.is_set() else operation.outcome
            operation.sequence += 1
            operation.progress = ""

    def _snapshot_locked(self, operation: _Operation) -> dict[str, object]:
        self._refresh_locked(operation)
        value: dict[str, object] = {
            "operationId": operation.request.operation_id,
            "state": operation.state,
            "sequence": operation.sequence,
            "progress": operation.progress,
        }
        if operation.state == "completed":
            value["reply"] = {"segments": [{"text": operation.result_text or "", "tone": "中性"}]}
        return value

    def _work(self, operation: _Operation) -> None:
        try:
            self._run_timer(operation)
        except Exception:
            with self._lock:
                operation.outcome = "failed"

    def _run_timer(self, operation: _Operation) -> None:
        # Caller identity was checked at begin; this worker uses captured values.
        deadline = time.monotonic() + operation.duration
        while not operation.stop.is_set():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                with self._lock:
                    operation.result_text = f"{operation.task}的 {operation.duration} 秒专注计时结束了。休息一下，再继续吧。"
                    operation.outcome = "completed"
                return
            with self._lock:
                progress = _progress(operation.task, math.ceil(remaining))
                if operation.state == "running" and operation.progress != progress:
                    operation.progress = progress
                    operation.sequence += 1
            if operation.stop.wait(min(1.0, remaining)):
                break
        with self._lock:
            operation.outcome = "cancelled"


def _operation_id(request: Mapping[str, Any]) -> str:
    if not isinstance(request, Mapping):
        raise ValueError("FOCUS_REQUEST_INVALID")
    value = request.get("operationId")
    if not isinstance(value, str) or not value or len(value) > 200:
        raise ValueError("FOCUS_REQUEST_INVALID")
    return value


def _parse_request(request: Mapping[str, Any]) -> _Request:
    operation_id = _operation_id(request)
    if request.get("schemaVersion") != 1 or isinstance(request.get("schemaVersion"), bool):
        raise ValueError("FOCUS_REQUEST_INVALID")
    identifiers = [request.get(key) for key in ("generationId", "turnId", "characterId")]
    if any(not isinstance(value, str) or not value or len(value) > 200 for value in identifiers):
        raise ValueError("FOCUS_REQUEST_INVALID")
    content = request.get("input")
    if not isinstance(content, Mapping) or not isinstance(content.get("text"), str):
        raise ValueError("FOCUS_REQUEST_INVALID")
    return _Request(operation_id, *identifiers, content["text"])


def _parse_command(text: str) -> tuple[int, str] | str:
    match = _COMMAND.fullmatch(text.strip())
    if match is None:
        return HELP_TEXT
    amount, unit, task = match.groups()
    duration = int(amount) * (60 if unit == "分钟" else 1)
    if not 1 <= duration <= MAX_DURATION_SECONDS:
        return "计时长度支持 1 秒至 120 分钟。" + HELP_TEXT
    task = task.strip() or "这件事"
    if len(task) > 200:
        return "请把专注事项缩短到 200 字以内。"
    return duration, task


def _progress(task: str, remaining: int) -> str:
    return f"正在专注：{task} · 剩余 {remaining} 秒"
