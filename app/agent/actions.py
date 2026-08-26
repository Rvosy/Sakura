from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.llm.chat_reply import ChatReply


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


@dataclass(frozen=True)
class AgentResult:
    """Agent Runtime 的统一输出，供 UI 根据回复和动作分别处理。"""

    reply: ChatReply
    actions: list[AgentAction] = field(default_factory=list)
    _debug: dict[str, Any] | None = field(default=None)
    visual_observation: dict[str, Any] | None = None
