"""Pick one current-character line whose retained speech is missing."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from app.storage.timeline import TimelineKind
from app.voice.recording_store import VoiceRecordingStore


IDLE_FILL_PAGE_LIMIT = 40
IDLE_FILL_HEADROOM_BYTES = 2 * 1024 * 1024
IDLE_FILL_BACKOFF_SECONDS = 120.0


@dataclass(frozen=True)
class MissingSpeech:
    history_entry_id: str
    segment_index: int
    text: str
    tone: str
    portrait: str


@dataclass
class IdleFillCursor:
    character_id: str = ""
    latest: str = ""
    before: str | None = None


def next_missing_speech(
    timeline: Any,
    store: VoiceRecordingStore,
    character_id: str,
    cursor: IdleFillCursor,
) -> MissingSpeech | None:
    """Return the newest missing speakable line, then walk older pages.

    A later call continues after a page that was already complete. A new
    timeline head starts again from the newest page.
    """

    latest = timeline.latest_cursor(character_id)
    if cursor.character_id != character_id or cursor.latest != latest:
        cursor.character_id = character_id
        cursor.latest = latest
        cursor.before = None
    page, next_before, has_more, _total = timeline.read_page_before(
        character_id,
        IDLE_FILL_PAGE_LIMIT,
        before_cursor=cursor.before,
    )
    for entry in reversed(page):
        if getattr(entry, "kind", None) is not TimelineKind.ASSISTANT:
            continue
        segments = getattr(entry, "payload", {}).get("segments") or []
        for index, segment in enumerate(segments):
            speech = _speakable(segment)
            if speech is None:
                continue
            if store.for_segment(character_id, entry.entry_id, index) is not None:
                continue
            return MissingSpeech(entry.entry_id, index, *speech)
    cursor.before = next_before if has_more else None
    return None


def _speakable(segment: object) -> tuple[str, str, str] | None:
    if not isinstance(segment, Mapping):
        return None
    text = segment.get("text", "")
    if not isinstance(text, str) or not text.strip() or segment.get("suppressTts") is True:
        return None
    tone = segment.get("tone", "")
    portrait = segment.get("portrait", "")
    if not isinstance(tone, str):
        tone = ""
    if not isinstance(portrait, str):
        portrait = ""
    return text, tone, portrait
