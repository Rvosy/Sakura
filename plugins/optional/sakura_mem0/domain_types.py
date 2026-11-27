from __future__ import annotations

from dataclasses import dataclass


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
    "PromptTraceMetadata",
]
