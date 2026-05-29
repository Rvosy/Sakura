from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.chat_reply import ChatReply


@dataclass(frozen=True)
class AgentAction:
    """Agent 决策出的外部动作。"""

    type: str
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class MemoryUpdate:
    """候选长期记忆更新，用于 UI 或调用方决定是否提示用户。"""

    id: str
    category: str
    content: str
    reason: str = ""


@dataclass(frozen=True)
class AgentEvent:
    """运行时主动事件，例如提醒到期。"""

    type: str
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class AgentResult:
    """Agent Runtime 的统一输出，供 UI 根据回复、动作和记忆更新分别处理。"""

    reply: ChatReply
    actions: list[AgentAction] = field(default_factory=list)
    memory_updates: list[MemoryUpdate] = field(default_factory=list)
