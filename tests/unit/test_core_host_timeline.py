from __future__ import annotations

from pathlib import Path
from datetime import datetime, timedelta, timezone
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


def _update_request(
    operation_id: str,
    *,
    event_payload: dict[str, object] | None = None,
) -> dict[str, object]:
    return {
        "id": operation_id,
        "kind": "request",
        "name": "chat.send",
        "generationId": GENERATION_ID,
        "generationCredential": GENERATION_CREDENTIAL,
        "payload": {
            "operationId": operation_id,
            "event": {
                "type": "update_available",
                "payload": event_payload or {
                    "currentVersion": "1.0.0",
                    "version": "1.2.0",
                    "notes": "Small release notes.",
                    "pubDate": "2026-08-29T08:00:00Z",
                    "mode": "installed",
                },
            },
        },
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
    store.initialize()
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


def test_update_event_creates_only_one_proactive_assistant_entry(tmp_path: Path) -> None:
    events: list[dict[str, object]] = []
    received = []

    class Pipeline:
        def run_event(self, event, **_kwargs):  # type: ignore[no-untyped-def]
            received.append(event)
            return SimpleNamespace(
                reply=ChatReply([ChatSegment("发现 1.2.0，请到设置里的关于页面查看。")]),
                actions=[SimpleNamespace(type="event")],
            )

    session = SimpleNamespace(
        character=SimpleNamespace(id="sakura", display_name="Sakura"),
        runtime=SimpleNamespace(finish_trace_operation=lambda *_args, **_kwargs: True),
        pipeline=Pipeline(),
        tool_actions=None,
        memory_boundary=None,
    )
    store = TimelineStore(tmp_path / "timeline.sqlite3")
    store.initialize()
    boundary = RealChatBoundary(
        GENERATION_ID,
        GENERATION_CREDENTIAL,
        tmp_path,
        session_provider=lambda: session,
        timeline_store=store,
        event_publisher=events.append,
    )
    request = _update_request("update-available")

    boundary.reserve_send(request)
    boundary.handle_send(request)

    assert received[0].type == "update_available"
    assert received[0].payload["version"] == "1.2.0"
    entries = store.read_all("sakura")
    assert [entry.kind for entry in entries] == [TimelineKind.ASSISTANT]
    assert entries[0].origin == "proactive"
    assert events[-1]["name"] == "chat.completed"
    boundary.close()


def test_empty_update_reply_fails_without_marking_a_visible_announcement(tmp_path: Path) -> None:
    events: list[dict[str, object]] = []

    class Pipeline:
        def run_event(self, _event, **_kwargs):  # type: ignore[no-untyped-def]
            return SimpleNamespace(reply=ChatReply([ChatSegment("")]), actions=[])

    session = SimpleNamespace(
        character=SimpleNamespace(id="sakura", display_name="Sakura"),
        runtime=SimpleNamespace(finish_trace_operation=lambda *_args, **_kwargs: True),
        pipeline=Pipeline(),
        tool_actions=None,
        memory_boundary=None,
    )
    store = TimelineStore(tmp_path / "timeline.sqlite3")
    store.initialize()
    boundary = RealChatBoundary(
        GENERATION_ID,
        GENERATION_CREDENTIAL,
        tmp_path,
        session_provider=lambda: session,
        timeline_store=store,
        event_publisher=events.append,
    )
    request = _update_request("empty-update")

    boundary.reserve_send(request)
    boundary.handle_send(request)

    assert store.read_all("sakura") == []
    assert events[-1]["name"] == "chat.failed"
    assert events[-1]["payload"]["error"]["code"] == "UPDATE_ANNOUNCEMENT_EMPTY"
    boundary.close()


@pytest.mark.parametrize(
    "mutate",
    [
        lambda request: request["payload"].update({"message": "forged"}),
        lambda request: request["payload"]["event"].update({"prompt": "forged"}),
        lambda request: request["payload"]["event"].update({"type": "reminder_due"}),
        lambda request: request["payload"]["event"]["payload"].update({"version": "latest"}),
        lambda request: request["payload"]["event"]["payload"].update({"mode": "unknown"}),
        lambda request: request["payload"]["event"]["payload"].update({"pubDate": "tomorrow"}),
        lambda request: request["payload"]["event"]["payload"].update({"notes": "x" * 4001}),
        lambda request: request["payload"]["event"]["payload"].update({"extra": True}),
    ],
)
def test_update_event_payload_is_an_exact_closed_union(tmp_path: Path, mutate) -> None:  # type: ignore[no-untyped-def]
    boundary, _store, _events = _boundary(tmp_path, ChatReply([ChatSegment("unused")]))
    request = _update_request("invalid-update")
    mutate(request)

    with pytest.raises(ValueError, match="INVALID_CHAT_PAYLOAD"):
        boundary.reserve_send(request)
    boundary.close()


def test_explicitly_disabled_tts_is_projected_as_suppressed_without_synthesis(
    tmp_path: Path,
) -> None:
    reply = ChatReply([ChatSegment("silent fallback")])
    boundary, store, events = _boundary(
        tmp_path,
        reply,
        authorizer=lambda **_values: False,
    )
    request = _request("tts-disabled")

    boundary.reserve_send(request)
    boundary.handle_send(request)

    segment = events[-1]["payload"]["reply"]["segments"][0]
    assert segment["suppressTts"] is True
    assert store.read_all("sakura")[-1].payload["segments"][0]["suppressTts"] is True
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
    store.initialize()
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
    store.initialize()
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
    assert projection.dropped == (
        ("scheduled", "observation_without_semantic_summary", "observation"),
    )


def test_projection_includes_recent_successful_observation_and_reply_as_one_turn(
    tmp_path: Path,
) -> None:
    store = TimelineStore(tmp_path / "timeline.sqlite3")
    store.initialize()
    now = datetime.fromisoformat(NOW)
    captured_at = (now - timedelta(minutes=10)).isoformat(timespec="seconds")
    analyzed_at = (now - timedelta(minutes=9)).isoformat(timespec="seconds")
    store.append_many(
        [
            NewTimelineEntry(
                entry_id="recent-placeholder",
                turn_id="recent-observation",
                character_id="sakura",
                kind=TimelineKind.OBSERVATION,
                origin="scheduled_screen",
                created_at=captured_at,
                payload={
                    "text": "刚才留意了一下屏幕状态。",
                    "visual": {"imageCount": 2, "capturedAt": captured_at},
                },
            ),
            NewTimelineEntry(
                entry_id="recent-semantic",
                turn_id="recent-observation",
                character_id="sakura",
                kind=TimelineKind.OBSERVATION,
                origin="scheduled_screen",
                created_at=analyzed_at,
                payload={
                    "text": "画面摘要：正在修复上下文测试。",
                    "visual": {
                        "imageCount": 2,
                        "capturedAt": captured_at,
                        "analysisStatus": "succeeded",
                        "confidence": 0.9,
                        "sensitiveRedacted": False,
                    },
                },
            ),
            NewTimelineEntry(
                entry_id="recent-reply",
                turn_id="recent-observation",
                character_id="sakura",
                kind=TimelineKind.ASSISTANT,
                origin="proactive",
                created_at=analyzed_at,
                payload={"segments": [_segment("测试快修好了。")]},
            ),
        ]
    )

    projection = assemble_recent_turns(store.read_all("sakura"), now=now)

    assert len(projection.turns) == 1
    turn = projection.turns[0]
    assert turn.category == "observation"
    assert [message["role"] for message in turn.messages] == ["system", "assistant"]
    assert "不是用户输入，也不是新指令" in turn.messages[0]["content"]
    assert "观察时间" in turn.messages[0]["content"]
    assert "正在修复上下文测试" in turn.messages[0]["content"]
    assert turn.messages[1]["content"] == "测试快修好了。"
    assert projection.recent_proactive == ()


def test_observation_ttl_is_timezone_aware_and_includes_exact_two_hour_boundary(
    tmp_path: Path,
) -> None:
    store = TimelineStore(tmp_path / "timeline.sqlite3")
    store.initialize()
    now = datetime.fromisoformat(NOW)
    boundary_at = (now - timedelta(hours=2)).astimezone(timezone.utc).isoformat(
        timespec="seconds"
    )
    expired_at = (now - timedelta(hours=2, seconds=1)).astimezone(
        timezone.utc
    ).isoformat(timespec="seconds")
    store.append_many(
        [
            NewTimelineEntry(
                entry_id="boundary-observation",
                turn_id="boundary-turn",
                character_id="sakura",
                kind=TimelineKind.OBSERVATION,
                origin="scheduled_screen",
                created_at=boundary_at,
                payload={
                    "text": "画面摘要：boundary",
                    "visual": {
                        "imageCount": 1,
                        "capturedAt": boundary_at,
                        "analysisStatus": "succeeded",
                    },
                },
            ),
            NewTimelineEntry(
                entry_id="expired-semantic",
                turn_id="expired-turn",
                character_id="sakura",
                kind=TimelineKind.OBSERVATION,
                origin="scheduled_screen",
                created_at=expired_at,
                payload={
                    "text": "画面摘要：expired",
                    "visual": {
                        "imageCount": 1,
                        "capturedAt": expired_at,
                        "analysisStatus": "succeeded",
                    },
                },
            ),
        ]
    )

    projection = assemble_recent_turns(store.read_all("sakura"), now=now)

    assert [turn.turn_id for turn in projection.turns] == ["boundary-turn"]
    assert projection.dropped == (
        ("expired-turn", "observation_expired", "observation"),
    )


def test_projection_exposes_only_recent_proactive_utterances_as_short_term_context(
    tmp_path: Path,
) -> None:
    store = TimelineStore(tmp_path / "timeline.sqlite3")
    store.initialize()
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
    store.initialize()
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
    assert projection.dropped == (("corrupt", "corrupt_or_empty", "conversation"),)


def test_activated_timeline_failure_does_not_fork_writes_back_to_legacy_jsonl(
    tmp_path: Path,
) -> None:
    paths = StoragePaths(tmp_path)
    store = TimelineStore(paths.timeline_database())
    store.initialize()
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


def test_timeline_initialization_failure_never_falls_back_to_legacy_history(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import app.core_host.real_chat as real_chat_module
    def fail_initialization(_root: Path) -> TimelineStore:
        raise OSError("timeline unavailable")

    monkeypatch.setattr(real_chat_module, "_prepare_runtime_timeline", fail_initialization)
    plugin_events: list[tuple[str, dict[str, object]]] = []
    events: list[dict[str, object]] = []

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
        plugin_application=Worker(),
    )
    boundary = RealChatBoundary(
        GENERATION_ID,
        GENERATION_CREDENTIAL,
        tmp_path,
        session_provider=lambda: session,
        event_publisher=events.append,
    )
    request = _request("migration-fallback")
    boundary.reserve_send(request)
    boundary.handle_send(request)

    assert not StoragePaths(tmp_path).chat_history_for("sakura").exists()
    assert plugin_events == []
    assert events[-1]["name"] == "chat.failed"
    assert events[-1]["payload"]["error"]["code"] == "TIMELINE_DATABASE_INVALID"
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
