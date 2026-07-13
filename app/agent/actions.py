from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any
import uuid

from app.llm.chat_reply import ChatReply


class ApprovalScope(str, Enum):
    """用户对待确认工具动作授予的执行范围。"""

    ONCE = "once"
    PROCESS = "process"


@dataclass(frozen=True)
class ToolConfirmationDetails:
    """工具提供给确认界面的结构化说明。"""

    summary: str = ""
    working_directory: str = ""
    risk_level: str = "normal"
    allowed_scopes: tuple[ApprovalScope, ...] = (ApprovalScope.ONCE,)

    def normalized(self) -> "ToolConfirmationDetails":
        risk_level = self.risk_level.strip().lower()
        if risk_level not in {"low", "normal", "medium", "high"}:
            risk_level = "medium"
        scopes = tuple(dict.fromkeys(self.allowed_scopes))
        if ApprovalScope.ONCE not in scopes:
            scopes = (ApprovalScope.ONCE, *scopes)
        return ToolConfirmationDetails(
            summary=self.summary.strip(),
            working_directory=self.working_directory.strip(),
            risk_level=risk_level,
            allowed_scopes=scopes,
        )


@dataclass(frozen=True)
class AgentAction:
    """Agent 决策出的外部动作。"""

    type: str
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class AgentEvent:
    """运行时主动事件，例如提醒到期。"""

    type: str
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class AgentProgress:
    """Agent 运行中的中间回复，用于前台展示工具调用进度。"""

    reply: ChatReply
    stage: str = "tool_planning"
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, init=False)
class PendingToolAction:
    """等待用户确认后才执行的工具动作。"""

    id: str
    tool_name: str
    arguments: dict[str, Any]
    reason: str
    created_at: str
    tool_call_id: str = ""
    continuation_messages: list[dict[str, Any]] = field(default_factory=list)
    summary: str = ""
    working_directory: str = ""
    risk_level: str = "normal"
    allowed_approval_scopes: tuple[ApprovalScope, ...] = (ApprovalScope.ONCE,)

    def __init__(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        reason: str,
        *,
        id: str = "",
        created_at: str = "",
        tool_call_id: str = "",
        continuation_messages: list[dict[str, Any]] | None = None,
        summary: str = "",
        working_directory: str = "",
        risk_level: str = "normal",
        allowed_approval_scopes: tuple[ApprovalScope, ...] | list[ApprovalScope | str] | None = None,
    ) -> None:
        object.__setattr__(self, "id", id.strip() or uuid.uuid4().hex[:8])
        object.__setattr__(self, "tool_name", tool_name)
        object.__setattr__(self, "arguments", dict(arguments))
        object.__setattr__(self, "reason", reason)
        object.__setattr__(
            self,
            "created_at",
            created_at.strip() or datetime.now().astimezone().isoformat(timespec="seconds"),
        )
        object.__setattr__(self, "tool_call_id", tool_call_id.strip())
        object.__setattr__(
            self,
            "continuation_messages",
            [dict(message) for message in (continuation_messages or []) if isinstance(message, dict)],
        )
        details = ToolConfirmationDetails(
            summary=summary,
            working_directory=working_directory,
            risk_level=risk_level,
            allowed_scopes=_normalize_approval_scopes(allowed_approval_scopes),
        ).normalized()
        object.__setattr__(self, "summary", details.summary)
        object.__setattr__(self, "working_directory", details.working_directory)
        object.__setattr__(self, "risk_level", details.risk_level)
        object.__setattr__(self, "allowed_approval_scopes", details.allowed_scopes)

    @classmethod
    def create(
        cls,
        tool_name: str,
        arguments: dict[str, Any],
        reason: str = "",
        tool_call_id: str = "",
        confirmation_details: ToolConfirmationDetails | None = None,
    ) -> "PendingToolAction":
        details = (confirmation_details or ToolConfirmationDetails()).normalized()
        return cls(
            tool_name=tool_name,
            arguments=dict(arguments),
            reason=reason,
            id=uuid.uuid4().hex[:8],
            created_at=datetime.now().astimezone().isoformat(timespec="seconds"),
            tool_call_id=tool_call_id.strip(),
            summary=details.summary,
            working_directory=details.working_directory,
            risk_level=details.risk_level,
            allowed_approval_scopes=details.allowed_scopes,
        )

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "PendingToolAction":
        action_id = data.get("id")
        tool_name = data.get("tool_name")
        arguments = data.get("arguments", {})
        reason = data.get("reason", "")
        created_at = data.get("created_at")
        tool_call_id = data.get("tool_call_id", "")
        if not isinstance(action_id, str) or not action_id.strip():
            raise ValueError("待确认动作缺少 id。")
        if not isinstance(tool_name, str) or not tool_name.strip():
            raise ValueError("待确认动作缺少工具名。")
        if not isinstance(arguments, dict):
            raise ValueError("待确认动作参数必须是 JSON object。")
        if not isinstance(reason, str):
            reason = ""
        if not isinstance(created_at, str) or not created_at.strip():
            created_at = datetime.now().astimezone().isoformat(timespec="seconds")
        if not isinstance(tool_call_id, str):
            tool_call_id = ""
        continuation_messages = data.get("continuation_messages", [])
        if not isinstance(continuation_messages, list):
            continuation_messages = []
        return cls(
            id=action_id.strip(),
            tool_name=tool_name.strip(),
            arguments=dict(arguments),
            reason=reason.strip(),
            created_at=created_at.strip(),
            tool_call_id=tool_call_id.strip(),
            continuation_messages=[
                dict(message)
                for message in continuation_messages
                if isinstance(message, dict)
            ],
            summary=str(data.get("summary") or ""),
            working_directory=str(data.get("working_directory") or ""),
            risk_level=str(data.get("risk_level") or "normal"),
            allowed_approval_scopes=data.get("allowed_approval_scopes"),
        )

    def with_continuation_messages(
        self,
        continuation_messages: list[dict[str, Any]],
    ) -> "PendingToolAction":
        """附带确认后继续推理所需的轻量对话上下文。"""
        return PendingToolAction(
            id=self.id,
            tool_name=self.tool_name,
            arguments=dict(self.arguments),
            reason=self.reason,
            created_at=self.created_at,
            tool_call_id=self.tool_call_id,
            continuation_messages=[dict(message) for message in continuation_messages],
            summary=self.summary,
            working_directory=self.working_directory,
            risk_level=self.risk_level,
            allowed_approval_scopes=self.allowed_approval_scopes,
        )

    def allows_scope(self, scope: ApprovalScope) -> bool:
        return scope in self.allowed_approval_scopes

    def to_dict(self, *, include_context: bool = False) -> dict[str, Any]:
        data = {
            "id": self.id,
            "tool_name": self.tool_name,
            "arguments": self.arguments,
            "reason": self.reason,
            "created_at": self.created_at,
            "tool_call_id": self.tool_call_id,
            "summary": self.summary,
            "working_directory": self.working_directory,
            "risk_level": self.risk_level,
            "allowed_approval_scopes": [scope.value for scope in self.allowed_approval_scopes],
        }
        if include_context and self.continuation_messages:
            data["continuation_messages"] = self.continuation_messages
        return data


def _normalize_approval_scopes(
    values: tuple[ApprovalScope, ...] | list[ApprovalScope | str] | None,
) -> tuple[ApprovalScope, ...]:
    if values is None:
        return (ApprovalScope.ONCE,)
    scopes: list[ApprovalScope] = []
    for value in values:
        try:
            scope = value if isinstance(value, ApprovalScope) else ApprovalScope(str(value))
        except ValueError:
            continue
        if scope not in scopes:
            scopes.append(scope)
    return tuple(scopes) or (ApprovalScope.ONCE,)


@dataclass(frozen=True)
class AgentResult:
    """Agent Runtime 的统一输出，供 UI 根据回复和动作分别处理。"""

    reply: ChatReply
    actions: list[AgentAction] = field(default_factory=list)
    _debug: dict[str, Any] | None = field(default=None)
    visual_observation: dict[str, Any] | None = None
