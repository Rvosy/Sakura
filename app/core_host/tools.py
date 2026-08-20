"""Generation-scoped built-in Tools and Action ID confirmation for Runtime v2."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal

from app.agent.actions import AgentResult, PendingToolAction
from app.agent.builtin_tools import get_current_time
from app.agent.tools import Tool, ToolRegistry
from app.core.cancellation import OperationCancelled


ACTION_TTL_SECONDS = 60.0
ACTION_ID_LENGTH = 32


class ToolActionError(ValueError):
    def __init__(self, code: str, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable

    def public_error(self) -> dict[str, object]:
        return {
            "code": self.code,
            "message": self.message,
            "retryable": self.retryable,
            "details": {},
        }


@dataclass
class _PendingDecision:
    action: PendingToolAction
    operation_id: str
    expires_at: float
    decision: Literal["confirm", "reject"] | None = None


class ToolActionCoordinator:
    """Store immutable actions and arbitrate one-shot decisions for one generation."""

    def __init__(
        self,
        generation_id: str,
        *,
        ttl_seconds: float = ACTION_TTL_SECONDS,
        clock: Callable[[], float] = time.monotonic,
        tool_lookup: Callable[[str], object | None] | None = None,
    ) -> None:
        if not generation_id.strip():
            raise ValueError("tool generation identity must not be empty")
        if ttl_seconds <= 0:
            raise ValueError("tool action TTL must be positive")
        self._generation_id = generation_id
        self._ttl_seconds = float(ttl_seconds)
        self._clock = clock
        self._tool_lookup = tool_lookup
        self._changed = threading.Condition(threading.Lock())
        self._pending: dict[str, _PendingDecision] = {}
        self._closed = False

    def await_decision(
        self,
        action: PendingToolAction,
        *,
        operation_id: str,
        publish: Callable[[dict[str, object]], None],
        cancel_checker: Callable[[], None],
    ) -> Literal["confirm", "reject", "expired"]:
        self._validate_action(action)
        expires_at = self._clock() + self._ttl_seconds
        item = _PendingDecision(action, operation_id, expires_at)
        with self._changed:
            if self._closed:
                raise OperationCancelled()
            if action.id in self._pending:
                raise ToolActionError("DUPLICATE_ACTION_ID", "工具确认标识重复。")
            if self._pending:
                raise ToolActionError("TOOL_CONFIRMATION_BUSY", "已有工具操作正在等待确认。")
            self._pending[action.id] = item
        try:
            publish(self.public_confirmation(action, expires_at=expires_at))
            while True:
                cancel_checker()
                with self._changed:
                    if self._closed:
                        raise OperationCancelled()
                    current = self._pending.get(action.id)
                    if current is not item:
                        raise ToolActionError("ACTION_INVALIDATED", "工具确认已失效。")
                    if item.decision is not None:
                        return item.decision
                    remaining = expires_at - self._clock()
                    if remaining <= 0:
                        return "expired"
                    self._changed.wait(timeout=min(0.05, remaining))
        finally:
            with self._changed:
                if self._pending.get(action.id) is item:
                    self._pending.pop(action.id, None)
                self._changed.notify_all()

    def decide(self, action_id: object, *, confirm: bool) -> dict[str, object]:
        normalized = self._required_action_id(action_id)
        with self._changed:
            if self._closed:
                return {"accepted": False, "actionId": normalized, "code": "GENERATION_INVALIDATED"}
            item = self._pending.get(normalized)
            if item is None or item.decision is not None:
                return {"accepted": False, "actionId": normalized, "code": "ACTION_NOT_PENDING"}
            if self._clock() >= item.expires_at:
                return {"accepted": False, "actionId": normalized, "code": "ACTION_EXPIRED"}
            item.decision = "confirm" if confirm else "reject"
            self._changed.notify_all()
            return {"accepted": True, "actionId": normalized, "code": "ACCEPTED"}

    def close(self) -> None:
        with self._changed:
            self._closed = True
            self._changed.notify_all()

    def pending_count(self) -> int:
        with self._changed:
            return len(self._pending)

    def public_confirmation(
        self,
        action: PendingToolAction,
        *,
        expires_at: float,
    ) -> dict[str, object]:
        tool = self._tool_lookup(action.tool_name) if self._tool_lookup is not None else None
        if getattr(tool, "group", "") == "mcp":
            risk = "destructive" if getattr(tool, "risk", "") == "high" else "write"
            title = "执行 MCP 工具"
        elif getattr(tool, "source", "") == "plugin":
            risk = "destructive" if getattr(tool, "risk", "") == "high" else "write"
            title = "执行插件工具"
        else:
            risk = "write"
            title = "执行工具"
        summary = _confirmation_summary(action)
        remaining = max(0.0, expires_at - time.monotonic())
        expires = datetime.now(timezone.utc).timestamp() + remaining
        return {
            "actionId": action.id,
            "title": title,
            "summary": summary,
            "risk": risk,
            "expiresAt": datetime.fromtimestamp(expires, timezone.utc).isoformat().replace("+00:00", "Z"),
        }

    @staticmethod
    def _required_action_id(value: object) -> str:
        if (
            not isinstance(value, str)
            or len(value) != ACTION_ID_LENGTH
            or any(character not in "0123456789abcdef" for character in value)
        ):
            raise ToolActionError("ACTION_ID_INVALID", "工具确认标识无效。")
        return value

    def _validate_action(self, action: PendingToolAction) -> None:
        self._required_action_id(action.id)
        tool = self._tool_lookup(action.tool_name) if self._tool_lookup is not None else None
        allowed = bool(tool is not None and getattr(tool, "requires_confirmation", False))
        if not allowed:
            raise ToolActionError("ACTION_TOOL_INVALID", "工具不允许请求确认。")


def create_runtime_v2_tool_registry(
    *,
    confirm_writes: bool = False,
) -> ToolRegistry:
    """Build the Core-owned registry; plugins add their own domain tools."""

    registry = ToolRegistry(
        [
            Tool(
                name="get_current_time",
                description="获取当前本机时间和时区。",
                parameters={"type": "object", "properties": {}, "required": []},
                handler=lambda _arguments: get_current_time(),
            ),
        ]
    )
    registry.set_free_access_enabled(not confirm_writes)
    return registry


def pending_actions_from_result(result: AgentResult) -> tuple[PendingToolAction, ...]:
    pending: list[PendingToolAction] = []
    for emitted in result.actions:
        if emitted.type != "pending_action":
            continue
        try:
            pending.append(PendingToolAction.from_dict(emitted.payload))
        except ValueError as error:
            raise ToolActionError("PENDING_ACTION_INVALID", "工具确认请求无效。") from error
    return tuple(pending)


def _confirmation_summary(action: PendingToolAction) -> str:
    # Native confirmation receives only the opaque action identity. Arguments
    # remain in Core even while this dormant assistant-stage path is retained.
    return f"执行外部工具 {action.tool_name[:96]}"


def public_tool_error(error: BaseException) -> dict[str, object]:
    if isinstance(error, ToolActionError):
        return error.public_error()
    return {
        "code": "TOOL_EXECUTION_FAILED",
        "message": "工具执行失败。",
        "retryable": False,
        "details": {},
    }


__all__ = [
    "ACTION_TTL_SECONDS",
    "ToolActionCoordinator",
    "ToolActionError",
    "create_runtime_v2_tool_registry",
    "pending_actions_from_result",
    "public_tool_error",
]
