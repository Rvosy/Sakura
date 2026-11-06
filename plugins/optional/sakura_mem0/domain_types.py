from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Mapping


@dataclass(frozen=True)
class ContextMessage:
    role: str
    content: str


@dataclass(frozen=True)
class ContextRequest:
    current_input: str = ""
    character_id: str = ""
    character_name: str = ""
    current_turn_id: str = ""
    source_entry_ids: tuple[str, ...] = ()
    human_entry_id: str = ""
    observation_entry_ids: tuple[str, ...] = ()
    source: Literal["chat", "event"] = "chat"
    mode: Literal["normal", "screen_awareness"] = "normal"
    event_type: str = ""
    step_index: int = 0
    remaining_steps: int = 0
    recent_messages: tuple[ContextMessage, ...] = ()
    available_tools: tuple[str, ...] = ()
    visual_summaries: tuple[str, ...] = ()
    screen_context_available: bool = False
    current_time: str = ""


@dataclass(frozen=True)
class ContextFragment:
    fragment_id: str
    source: str
    content: str
    trust: Literal["trusted", "untrusted"] = "untrusted"
    priority: int = 50
    freshness: str = ""
    token_budget: int = 512
    sensitivity: Literal["public", "private", "sensitive"] = "private"
    cache_scope: Literal["turn", "step"] = "turn"
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ChatHistoryEntry:
    created_at: str
    role: str
    content: str
    translation: str = ""
    tone: str = ""
    portrait: str = ""
    entry_id: str = ""
    turn_id: str = ""
    origin: str = ""
    evidence_ready: bool = False


@dataclass(frozen=True)
class PromptTraceMetadata:
    purpose: str
    curation_turn_ids: tuple[str, ...] = ()
    curation_evidence_kinds: tuple[str, ...] = ()


__all__ = [
    "ChatHistoryEntry",
    "ContextFragment",
    "ContextMessage",
    "ContextRequest",
    "PromptTraceMetadata",
]
