from __future__ import annotations

from pathlib import Path
from datetime import datetime, timedelta
import sqlite3
import threading
from types import SimpleNamespace

import pytest

from app.core_host.real_chat import (
    RealChatBoundary,
    assemble_recent_turns,
)
from app.llm.chat_reply import ChatReply, ChatSegment
from app.storage.paths import StoragePaths
from app.storage.timeline import (
    NewTimelineEntry,
    TimelineKind,
    TimelineStore,
    import_legacy_histories,
)


GENERATION_ID = "00000000-0000-4000-8000-000000004007"
GENERATION_CREDENTIAL = "40" * 16
NOW = "2026-08-26T12:00:00+08:00"


def _request(operation_id: str, message: str = "hello") -> dict[str, object]:
    return {
        "id": operation_id,
        "kind": "request",
        "name": "chat.send",
        "generationId": GENERATION_ID,
        "generationCredential": GENERATION_CREDENTIAL,
        "payload": {"message": message, "operationId": operation_id},
    }


def _boundary(
    tmp_path: Path,
    reply: ChatReply,
    *,
    authorizer=None,
) -> tuple[RealChatBoundary, TimelineStore, list[dict[str, object]]]:
    events: list[dict[str, object]] = []

    class Pipeline:
        def run_user_message(self, _messages, **_kwargs):  # type: ignore[no-untyped-def]
            return SimpleNamespace(reply=reply, actions=[])

    session = SimpleNamespace(
        character=SimpleNamespace(id="sakura", display_name="Sakura"),
        runtime=SimpleNamespace(finish_trace_operation=lambda *_args, **_kwargs: True),
        pipeline=Pipeline(),
        tool_actions=None,
        memory_boundary=None,
    )
    store = TimelineStore(tmp_path / "timeline.sqlite3")
    import_legacy_histories(store, tmp_path / "missing-history", [])
    boundary = RealChatBoundary(
        GENERATION_ID,
        GENERATION_CREDENTIAL,
        tmp_path,
        session_provider=lambda: session,
        timeline_store=store,
        segment_authorizer=authorizer,
        event_publisher=events.append,
    )
    return boundary, store, events


def test_generation_is_one_assistant_entry_and_all_segments_share_authorization_id(
    tmp_path: Path,
) -> None:
    authorized: list[dict[str, object]] = []
    reply = ChatReply(
        [
            ChatSegment("first", "第一", "中性", "neutral"),
            ChatSegment("second", "第二", "开心", "smile", suppress_tts=True),
        ]
    )
    boundary, store, events = _boundary(
        tmp_path,
        reply,
        authorizer=lambda **values: authorized.append(values),
    )
    request = _request("multi-segment")

    boundary.reserve_send(request)
    boundary.handle_send(request)

    entries = store.read_all("sakura")
    assert [entry.kind for entry in entries] == [TimelineKind.HUMAN, TimelineKind.ASSISTANT]
    assert entries[0].turn_id == entries[1].turn_id
    assert entries[1].payload["segments"] == events[-1]["payload"]["reply"]["segments"]
    assert len(authorized) == 2
    assert {item["history_entry_id"] for item in authorized} == {entries[1].entry_id}
    boundary.close()


def test_empty_noop_reply_does_not_create_assistant_history(tmp_path: Path) -> None:
    boundary, store, _events = _boundary(tmp_path, ChatReply([ChatSegment("")]))
    request = _request("noop")

    boundary.reserve_send(request)
    boundary.handle_send(request)

    assert [entry.kind for entry in store.read_all("sakura")] == [TimelineKind.HUMAN]
    boundary.close()


def test_cancel_waiting_on_assistant_commit_is_rejected_after_completion_claim(
    tmp_path: Path,
) -> None:
    append_started = threading.Event()
    release_append = threading.Event()

    class BlockingTimeline(TimelineStore):
        def append(self, entry):  # type: ignore[no-untyped-def]
            if entry.kind is TimelineKind.ASSISTANT:
                append_started.set()
                assert release_append.wait(2)
            return super().append(entry)

    events: list[dict[str, object]] = []

    class Pipeline:
        def run_user_message(self, _messages, **_kwargs):  # type: ignore[no-untyped-def]
            return SimpleNamespace(reply=ChatReply([ChatSegment("reply")]), actions=[])

    session = SimpleNamespace(
        character=SimpleNamespace(id="sakura", display_name="Sakura"),
        runtime=SimpleNamespace(finish_trace_operation=lambda *_args, **_kwargs: True),
        pipeline=Pipeline(),
        tool_actions=None,
        memory_boundary=None,
    )
    store = BlockingTimeline(tmp_path / "timeline.sqlite3")
    import_legacy_histories(store, tmp_path / "missing-history", [])
    boundary = RealChatBoundary(
        GENERATION_ID,
        GENERATION_CREDENTIAL,
        tmp_path,
        session_provider=lambda: session,
        timeline_store=store,
        event_publisher=events.append,
    )
    request = _request("commit-race")
    boundary.reserve_send(request)
    chat_thread = threading.Thread(target=boundary.handle_send, args=(request,))
    chat_thread.start()
    assert append_started.wait(2)

    cancel_result: list[dict[str, object]] = []
    cancel_thread = threading.Thread(
        target=lambda: cancel_result.append(
            boundary.handle_cancel(
                {
                    **_request("cancel-race"),
                    "name": "chat.cancel",
                    "payload": {"operationId": "commit-race"},
                }
            )
        )
    )
    cancel_thread.start()
    release_append.set()
    chat_thread.join(2)
    cancel_thread.join(2)

    assert cancel_result[0]["payload"]["accepted"] is False
    assert events[-1]["name"] == "chat.completed"
    assert [entry.kind for entry in store.read_all("sakura")] == [
        TimelineKind.HUMAN,
        TimelineKind.ASSISTANT,
    ]
    boundary.close()


def test_projection_keeps_manual_observation_as_host_fact_and_drops_observation_only_turn(
    tmp_path: Path,
) -> None:
    store = TimelineStore(tmp_path / "timeline.sqlite3")
    import_legacy_histories(store, tmp_path / "missing-history", [])
    store.append_many(
        [
            _entry("human", "manual", TimelineKind.HUMAN, {"text": "look"}),
            _entry(
                "observation",
                "manual",
                TimelineKind.OBSERVATION,
                {"text": "manual screen", "visual": {"imageCount": 1}},
                origin="manual_screen",
            ),
            _entry(
                "assistant",
                "manual",
                TimelineKind.ASSISTANT,
                {"segments": [_segment("seen")]},
            ),
            _entry(
                "scheduled-observation",
                "scheduled",
                TimelineKind.OBSERVATION,
                {"text": "scheduled screen", "visual": {"imageCount": 2}},
                origin="scheduled_screen",
            ),
            _entry(
                "scheduled-assistant",
                "scheduled",
                TimelineKind.ASSISTANT,
                {"segments": [_segment("proactive reply")]},
                origin="proactive",
            ),
            _entry(
                "unanswered-human",
                "unanswered",
                TimelineKind.HUMAN,
                {"text": "still waiting"},
            ),
        ]
    )

    projection = assemble_recent_turns(store.read_all("sakura"))
    assert [
        dict(message)
        for turn in projection.turns
        for message in turn.messages
    ] == [
        {"role": "user", "content": "look"},
        {"role": "system", "content": "[Host fact] manual screen"},
        {"role": "assistant", "content": "seen"},
        {"role": "user", "content": "still waiting"},
    ]
    assert [turn.turn_id for turn in projection.turns] == ["manual", "unanswered"]
    assert projection.dropped == (("scheduled", "observation_only"),)


def test_projection_exposes_only_recent_proactive_utterances_as_short_term_context(
    tmp_path: Path,
) -> None:
    store = TimelineStore(tmp_path / "timeline.sqlite3")
    import_legacy_histories(store, tmp_path / "missing-history", [])
    recent = datetime.now().astimezone().isoformat(timespec="seconds")
    expired = (datetime.now().astimezone() - timedelta(hours=2)).isoformat(
        timespec="seconds"
    )
    store.append_many(
        [
            _entry(
                "expired-observation",
                "expired",
                TimelineKind.OBSERVATION,
                {"text": "old screen", "visual": {"imageCount": 1}},
                origin="scheduled_screen",
            ),
            NewTimelineEntry(
                entry_id="expired-assistant",
                turn_id="expired",
                character_id="sakura",
                kind=TimelineKind.ASSISTANT,
                origin="proactive",
                created_at=expired,
                payload={"segments": [_segment("旧提醒")]},
            ),
            _entry(
                "recent-observation",
                "recent",
                TimelineKind.OBSERVATION,
                {"text": "new screen", "visual": {"imageCount": 1}},
                origin="scheduled_screen",
            ),
            NewTimelineEntry(
                entry_id="recent-assistant",
                turn_id="recent",
                character_id="sakura",
                kind=TimelineKind.ASSISTANT,
                origin="proactive",
                created_at=recent,
                payload={"segments": [_segment("刚才已经提醒过午饭了")]},
            ),
        ]
    )

    projection = assemble_recent_turns(store.read_all("sakura"))

    assert [turn.turn_id for turn in projection.recent_proactive] == ["recent"]
    assert projection.recent_proactive[0].messages[0]["content"] == "刚才已经提醒过午饭了"


def test_projection_keeps_complete_legacy_turn_reconstructed_across_error_record(
    tmp_path: Path,
) -> None:
    history_dir = tmp_path / "chat_history"
    history_dir.mkdir()
    (history_dir / "sakura.jsonl").write_text(
        '{"created_at":"2026-01-01T00:00:00+00:00","role":"user","content":"hello"}\n'
        '{"created_at":"2026-01-01T00:00:01+00:00","role":"assistant","content":"first","entry_id":"one"}\n'
        '{"created_at":"2026-01-01T00:00:02+00:00","role":"error","content":"old failure"}\n'
        '{"created_at":"2026-01-01T00:00:03+00:00","role":"assistant","content":"second","entry_id":"two"}\n',
        encoding="utf-8",
    )
    store = TimelineStore(tmp_path / "timeline.sqlite3")
    import_legacy_histories(store, history_dir, ["sakura"])

    projection = assemble_recent_turns(store.read_all("sakura"))

    assert projection.dropped == ()
    assert len(projection.turns) == 1
    assert projection.turns[0].messages == (
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "first\nsecond"},
    )


@pytest.mark.parametrize(
    "order",
    [
        ("assistant", "human"),
        ("human", "assistant", "observation"),
    ],
)
def test_projection_drops_turns_with_corrupt_entry_order(
    tmp_path: Path,
    order: tuple[str, ...],
) -> None:
    store = TimelineStore(tmp_path / "timeline.sqlite3")
    import_legacy_histories(store, tmp_path / "missing-history", [])
    entries = [
        _entry(
            f"entry-{index}",
            "corrupt",
            TimelineKind(kind),
            (
                {"segments": [_segment("reply")]}
                if kind == "assistant"
                else {"text": kind}
            ),
        )
        for index, kind in enumerate(order)
    ]
    store.append_many(entries)

    projection = assemble_recent_turns(store.read_all("sakura"))

    assert projection.turns == ()
    assert projection.dropped == (("corrupt", "corrupt_or_empty"),)


def test_activated_timeline_failure_does_not_fork_writes_back_to_legacy_jsonl(
    tmp_path: Path,
) -> None:
    paths = StoragePaths(tmp_path)
    store = TimelineStore(paths.timeline_database())
    import_legacy_histories(store, tmp_path / "missing-history", [])
    with sqlite3.connect(store.path) as connection:
        connection.execute("DROP TABLE timeline_entries")
    legacy = paths.chat_history_for("sakura")
    legacy.parent.mkdir(parents=True, exist_ok=True)
    legacy.write_text("", encoding="utf-8")
    pipeline_called = False

    class Pipeline:
        def run_user_message(self, _messages, **_kwargs):  # type: ignore[no-untyped-def]
            nonlocal pipeline_called
            pipeline_called = True
            return SimpleNamespace(reply=ChatReply([ChatSegment("reply")]), actions=[])

    session = SimpleNamespace(
        character=SimpleNamespace(id="sakura", display_name="Sakura"),
        runtime=SimpleNamespace(finish_trace_operation=lambda *_args, **_kwargs: True),
        pipeline=Pipeline(),
        tool_actions=None,
        memory_boundary=None,
    )
    events: list[dict[str, object]] = []
    boundary = RealChatBoundary(
        GENERATION_ID,
        GENERATION_CREDENTIAL,
        tmp_path,
        session_provider=lambda: session,
        event_publisher=events.append,
    )
    request = _request("activated-invalid")
    boundary.reserve_send(request)
    boundary.handle_send(request)

    assert pipeline_called is False
    assert legacy.read_bytes() == b""
    assert events[-1]["name"] == "chat.failed"
    assert events[-1]["payload"]["error"]["code"] == "TIMELINE_DATABASE_INVALID"
    boundary.close()


def test_initial_migration_failure_keeps_legacy_write_and_memory_event(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import app.core_host.real_chat as real_chat_module
    from app.storage.chat_history import ChatHistoryStore

    def fail_migration(_root: Path) -> TimelineStore:
        raise OSError("migration unavailable")

    monkeypatch.setattr(real_chat_module, "_prepare_runtime_timeline", fail_migration)
    runtime_events: list[tuple[str, str, dict[str, object]]] = []
    monkeypatch.setattr(
        "app.core.runtime_log.log_event",
        lambda _channel, _message, attributes, **kwargs: runtime_events.append(
            (str(kwargs.get("event")), str(kwargs.get("severity")), dict(attributes))
        ),
    )
    plugin_events: list[tuple[str, dict[str, object]]] = []

    class Worker:
        def emit_event(self, name, payload):  # type: ignore[no-untyped-def]
            plugin_events.append((name, payload))

    class Pipeline:
        def run_user_message(self, _messages, **_kwargs):  # type: ignore[no-untyped-def]
            return SimpleNamespace(reply=ChatReply([ChatSegment("reply")]), actions=[])

    session = SimpleNamespace(
        character=SimpleNamespace(id="sakura", display_name="Sakura"),
        runtime=SimpleNamespace(finish_trace_operation=lambda *_args, **_kwargs: True),
        pipeline=Pipeline(),
        tool_actions=None,
        memory_boundary=None,
        plugin_worker=Worker(),
    )
    boundary = RealChatBoundary(
        GENERATION_ID,
        GENERATION_CREDENTIAL,
        tmp_path,
        session_provider=lambda: session,
    )
    request = _request("migration-fallback")
    boundary.reserve_send(request)
    boundary.handle_send(request)

    history = ChatHistoryStore(StoragePaths(tmp_path).chat_history_for("sakura")).load()
    assert [entry.role for entry in history] == ["user", "assistant"]
    assert plugin_events[-1] == (
        "sakura.host.chat.completed",
        {"characterId": "sakura", "legacyHistory": True},
    )
    assert runtime_events == [
        (
            "timeline.migration.failed",
            "warning",
            {
                "reason_code": "TIMELINE_MIGRATION_IO_FAILED",
                "category": "io_error",
            },
        )
    ]
    boundary.close()


def _entry(
    entry_id: str,
    turn_id: str,
    kind: TimelineKind,
    payload: dict[str, object],
    *,
    origin: str = "chat",
) -> NewTimelineEntry:
    return NewTimelineEntry(
        entry_id=entry_id,
        turn_id=turn_id,
        character_id="sakura",
        kind=kind,
        origin=origin,
        created_at=NOW,
        payload=payload,
    )


def _segment(text: str) -> dict[str, object]:
    return {
        "text": text,
        "translation": "",
        "tone": "中性",
        "portrait": "neutral",
        "suppressTts": False,
    }
