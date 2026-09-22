from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import app.storage.timeline as timeline_module
from app.storage.timeline import NewTimelineEntry, TimelineDataError, TimelineKind, TimelineStore
from sakura_assistant.llm.prompts.runtime import ContextPolicy
from sakura_context import (
    ContextFragment, ContextHistorySource, ContextRequest, ContextTurn, ContextTurnDecision,
)


NOW = datetime(2026, 9, 18, 12, tzinfo=timezone.utc)
CUTOFFS = {"observation_since": NOW - timedelta(hours=2), "proactive_since": NOW - timedelta(hours=1)}


def entry(index: str, turn: str, *, kind: TimelineKind = TimelineKind.HUMAN,
          origin: str = "chat", created_at: datetime = NOW, text: str = "hello") -> NewTimelineEntry:
    payload = {"text": text}
    if kind is TimelineKind.ASSISTANT:
        payload = {"segments": [{"text": text, "translation": "", "tone": "neutral",
                                 "portrait": "", "suppressTts": False}]}
    return NewTimelineEntry(index, turn, "sakura", kind, origin, created_at.isoformat(), payload)


def test_pages_preserve_interleaved_turns_and_share_an_append_stable_snapshot(tmp_path: Path) -> None:
    store = TimelineStore(tmp_path / "timeline.sqlite3")
    store.initialize()
    store.append_many([
        entry("a1", "a"), entry("b1", "b"),
        entry("a2", "a", kind=TimelineKind.ASSISTANT), entry("c1", "c"),
        entry("b2", "b", kind=TimelineKind.ASSISTANT),
    ])
    first = store.read_turn_page("sakura", category="conversation", limit=1, **CUTOFFS)
    assert [[row.entry_id for row in turn] for turn in first.turns] == [["c1"]]
    store.append_many([entry("d1", "d"), entry("c2", "c", kind=TimelineKind.ASSISTANT)])
    second = store.read_turn_page("sakura", category="conversation", limit=1,
                                 before_cursor=first.next_cursor, snapshot_cursor=first.snapshot_cursor, **CUTOFFS)
    third = store.read_turn_page("sakura", category="conversation", limit=1,
                                before_cursor=second.next_cursor, snapshot_cursor=first.snapshot_cursor, **CUTOFFS)
    assert [[row.entry_id for row in turn] for turn in second.turns] == [["b1", "b2"]]
    assert [[row.entry_id for row in turn] for turn in third.turns] == [["a1", "a2"]]
    assert third.next_cursor is None
    frozen = store.read_turn_page("sakura", category="conversation", snapshot_cursor=first.snapshot_cursor, **CUTOFFS)
    assert [row.entry_id for turn in frozen.turns for row in turn] == ["c1", "b1", "b2", "a1", "a2"]
    assert second.snapshot_cursor == third.snapshot_cursor == first.snapshot_cursor


def test_categories_filter_expired_facts_before_decoding_and_keep_whole_turns(tmp_path: Path, monkeypatch) -> None:
    store = TimelineStore(tmp_path / "timeline.sqlite3")
    store.initialize()
    store.append_many([
        entry("human", "chat", created_at=NOW - timedelta(days=90)),
        entry("chat-observation", "chat", kind=TimelineKind.OBSERVATION, origin="scheduled_screen"),
        entry("expired", "expired", kind=TimelineKind.OBSERVATION, origin="scheduled_screen", created_at=NOW - timedelta(hours=3)),
        entry("recent", "observation", kind=TimelineKind.OBSERVATION, origin="scheduled_screen"),
        entry("reply", "observation", kind=TimelineKind.ASSISTANT, origin="proactive"),
        entry("proactive", "proactive", kind=TimelineKind.ASSISTANT, origin="proactive"),
    ])
    decoded = []
    original = timeline_module._entry_from_row
    def decode(row):
        decoded.append(row[1])
        return original(row)
    monkeypatch.setattr(timeline_module, "_entry_from_row", decode)
    chat = store.read_turn_page("sakura", category="conversation", **CUTOFFS)
    observations = store.read_turn_page("sakura", category="observation", snapshot_cursor=chat.snapshot_cursor, **CUTOFFS)
    proactive = store.read_turn_page("sakura", category="proactive", snapshot_cursor=chat.snapshot_cursor, **CUTOFFS)
    assert [[row.entry_id for row in turn] for turn in chat.turns] == [["human", "chat-observation"]]
    assert [[row.entry_id for row in turn] for turn in observations.turns] == [["recent", "reply"]]
    assert [[row.entry_id for row in turn] for turn in proactive.turns] == [["proactive"], ["recent", "reply"]]
    assert "expired" not in decoded


def test_pagination_rejects_foreign_or_missing_snapshot(tmp_path: Path) -> None:
    store = TimelineStore(tmp_path / "timeline.sqlite3")
    store.initialize()
    store.append_many([entry("a", "a"), entry("b", "b")])
    page = store.read_turn_page("sakura", category="conversation", limit=1, **CUTOFFS)
    with pytest.raises(TimelineDataError, match="TIMELINE_CURSOR_INVALID"):
        store.read_turn_page("sakura", category="conversation", before_cursor=page.next_cursor, **CUTOFFS)
    with pytest.raises(TimelineDataError, match="TIMELINE_CURSOR_INVALID"):
        store.read_turn_page("other", category="conversation", snapshot_cursor=page.snapshot_cursor, **CUTOFFS)


def test_budget_stops_fetching_old_payloads_without_a_history_count_limit(tmp_path: Path, monkeypatch) -> None:
    store = TimelineStore(tmp_path / "timeline.sqlite3")
    store.initialize()
    store.append_many([entry(str(index), str(index), text="x" * 2_048) for index in range(2_000)])
    decoded = []
    original = timeline_module._entry_from_row
    def decode(row):
        decoded.append(row[1])
        return original(row)
    monkeypatch.setattr(timeline_module, "_entry_from_row", decode)

    def conversations():
        before = snapshot = None
        while True:
            page = store.read_turn_page("sakura", category="conversation", limit=16,
                                        before_cursor=before, snapshot_cursor=snapshot, **CUTOFFS)
            snapshot = page.snapshot_cursor
            for turn in page.turns:
                yield ContextTurn(turn[0].turn_id, 100, sequence=turn[0].seq)
            if page.next_cursor is None:
                break
            before = page.next_cursor

    result = ContextPolicy(total_budget=1_000).select(
        ContextRequest(), (), history_source=ContextHistorySource(conversations=conversations()),
    )
    assert [turn.turn_id for turn in result.selected_turns] == [str(index) for index in range(1_990, 2_000)]
    assert len(decoded) == 16
    # Unread turns have not been inspected and must not receive invented drops.
    assert not result.dropped_turns


@pytest.mark.parametrize("budget", [0, 1, 49, 50, 99, 400, 580, 1_000, 100_000])
def test_lazy_selection_matches_full_history_priorities_and_skips_oversized_turns(budget: int) -> None:
    turns = [
        ContextTurn("old-small", 5, sequence=1),
        ContextTurn("old-large", 10_000, sequence=2),
        ContextTurn("old-observation", 40, "observation", sequence=3),
        *[ContextTurn(f"chat-{index}", 50, sequence=4 + index) for index in range(10)],
        ContextTurn("new-observation", 120, "observation", sequence=14),
    ]
    fragments = [ContextFragment("test", "test", "optional", token_budget=25)]
    policy = ContextPolicy(total_budget=budget)
    eager = policy.select(ContextRequest(), fragments, history_turns=turns)
    lazy = policy.select(ContextRequest(), fragments, history_source=ContextHistorySource(
        conversations=(turn for turn in reversed(turns) if turn.category == "conversation"),
        observations=(turn for turn in reversed(turns) if turn.category == "observation"),
    ))
    assert lazy.selected_turns == eager.selected_turns
    assert lazy.selected == eager.selected
    assert lazy.estimated_tokens == eager.estimated_tokens


def test_lazy_projection_diagnostics_are_collected_after_reading() -> None:
    drops = []
    def source():
        drops.append(ContextTurnDecision("broken", 0, False, "corrupt_or_empty"))
        yield ContextTurn("valid", 10, sequence=5)
    result = ContextPolicy(total_budget=10).select(
        ContextRequest(), (), history_source=ContextHistorySource(conversations=source()), projected_drops=drops,
    )
    assert result.dropped_turns == tuple(drops)


def test_minimum_cost_stops_without_loading_an_impossible_next_turn() -> None:
    def source():
        yield ContextTurn("latest", 97, sequence=1)
        raise AssertionError("no valid turn fits the remaining three tokens")
    result = ContextPolicy(total_budget=100).select(
        ContextRequest(), (), history_source=ContextHistorySource(conversations=source(), minimum_turn_tokens=6),
    )
    assert [turn.turn_id for turn in result.selected_turns] == ["latest"]
