from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.core_host.history import HistoryBoundary
from app.storage.paths import StoragePaths
from app.storage.timeline import NewTimelineEntry, TimelineKind, TimelineStore


GENERATION = "generation-history"
CREDENTIAL = "credential-history"
NOW = "2026-08-29T12:00:00+08:00"


def _request(payload: dict[str, object]) -> dict[str, object]:
    return {
        "protocolMajor": 2,
        "protocolMinor": 2,
        "kind": "request",
        "generationId": GENERATION,
        "generationCredential": CREDENTIAL,
        "id": "history-request",
        "name": "ui.history.page",
        "payload": payload,
    }


def _boundary(tmp_path, current: list[str]) -> HistoryBoundary:  # type: ignore[no-untyped-def]
    return HistoryBoundary(
        GENERATION,
        CREDENTIAL,
        tmp_path,
        character_provider=lambda: current[0],
    )


def test_history_boundary_returns_only_current_character_typed_entries(tmp_path) -> None:  # type: ignore[no-untyped-def]
    store = TimelineStore(StoragePaths(tmp_path).timeline_database())
    store.initialize()
    store.append_many(
        [
            NewTimelineEntry(
                entry_id="entry-user",
                turn_id="turn-user",
                character_id="sakura",
                kind=TimelineKind.HUMAN,
                origin="chat",
                created_at=NOW,
                payload={"text": "你好"},
            ),
            NewTimelineEntry(
                entry_id="entry-other",
                turn_id="turn-other",
                character_id="other",
                kind=TimelineKind.SYSTEM,
                origin="host",
                created_at=NOW,
                payload={"text": "private other character fact"},
            ),
            NewTimelineEntry(
                entry_id="entry-observation",
                turn_id="turn-observation",
                character_id="sakura",
                kind=TimelineKind.OBSERVATION,
                origin="manual_screen",
                created_at=NOW,
                payload={
                    "text": "safe screen summary",
                    "visual": {
                        "imageCount": 1,
                        "visualId": "vis-private",
                        "capturedAt": NOW,
                    },
                },
            ),
        ]
    )
    boundary = _boundary(tmp_path, ["sakura"])

    result = boundary.handle(
        _request(
            {
                "expectedCharacterId": "sakura",
                "beforeCursor": None,
                "limit": 50,
            }
        )
    )

    assert result["ok"] is True
    assert result["payload"] == {
        "schemaVersion": 1,
        "coreGenerationId": GENERATION,
        "characterId": "sakura",
        "totalCount": 2,
        "entries": [
            {
                "entryId": "entry-user",
                "turnId": "turn-user",
                "kind": "human",
                "origin": "chat",
                "createdAt": NOW,
                "payload": {"text": "你好"},
            },
            {
                "entryId": "entry-observation",
                "turnId": "turn-observation",
                "kind": "observation",
                "origin": "manual_screen",
                "createdAt": NOW,
                "payload": {"text": "safe screen summary", "visual": {"imageCount": 1}},
            },
        ],
        "beforeCursor": None,
        "afterCursor": None,
    }


def test_history_boundary_rejects_character_changes_and_invalid_shapes(tmp_path) -> None:  # type: ignore[no-untyped-def]
    store = TimelineStore(StoragePaths(tmp_path).timeline_database())
    store.initialize()
    current = ["sakura"]
    boundary = _boundary(tmp_path, current)
    current[0] = "other"

    mismatch = boundary.handle(
        _request(
            {
                "expectedCharacterId": "sakura",
                "beforeCursor": None,
                "limit": 50,
            }
        )
    )
    invalid = boundary.handle(
        _request(
            {
                "expectedCharacterId": "other",
                "beforeCursor": None,
                "limit": 51,
            }
        )
    )

    assert mismatch["ok"] is False
    assert mismatch["error"]["code"] == "HISTORY_CHARACTER_MISMATCH"
    assert invalid["ok"] is False
    assert invalid["error"]["code"] == "INVALID_REQUEST"


def test_history_preserves_observation_and_system_presentation_fields_without_visual_identity(
    tmp_path,
) -> None:  # type: ignore[no-untyped-def]
    store = TimelineStore(StoragePaths(tmp_path).timeline_database())
    store.initialize()
    store.append_many(
        [
            NewTimelineEntry(
                entry_id="screen-observation",
                turn_id="screen-turn",
                character_id="sakura",
                kind=TimelineKind.OBSERVATION,
                origin="host",
                created_at=NOW,
                payload={
                    "text": "原始观察内容",
                    "sourcePluginId": "sakura.screen_awareness",
                    "sourcePluginName": "屏幕观察",
                    "notificationKind": "update",
                    "visual": {"imageCount": 3, "visualId": "vis-private", "capturedAt": NOW},
                },
            ),
            NewTimelineEntry(
                entry_id="startup-event",
                turn_id="startup-turn",
                character_id="sakura",
                kind=TimelineKind.SYSTEM,
                origin="host",
                created_at=NOW,
                payload={"text": "原始启动事件内容", "eventType": "app.started"},
            ),
        ]
    )
    result = _boundary(tmp_path, ["sakura"]).handle(
        _request({"expectedCharacterId": "sakura", "beforeCursor": None, "limit": 50})
    )

    assert result["ok"] is True
    assert [entry["payload"] for entry in result["payload"]["entries"]] == [
        {
            "text": "原始观察内容",
            "sourcePluginId": "sakura.screen_awareness",
            "sourcePluginName": "屏幕观察",
            "notificationKind": "update",
            "visual": {"imageCount": 3},
        },
        {"text": "原始启动事件内容", "eventType": "app.started"},
    ]


@pytest.mark.parametrize("response_bytes", [700 * 1024, 1])
def test_history_date_jump_starts_at_local_midnight_and_pages_both_ways(tmp_path, monkeypatch, response_bytes) -> None:
    monkeypatch.setattr("app.core_host.history._HISTORY_RESPONSE_BYTES", response_bytes)
    store = TimelineStore(StoragePaths(tmp_path).timeline_database())
    store.initialize()
    # 时间格式和写入顺序不同，仍按本地日期与原始时间定位。
    records = [
        ("next-day", "sakura", datetime(2026, 10, 9)),
        ("day-end", "sakura", datetime(2026, 10, 8, 23, 59, 59)),
        ("previous-day", "sakura", datetime(2026, 10, 7, 23, 59, 59)),
        ("day-start", "sakura", datetime(2026, 10, 8)),
        ("other-character", "other", datetime(2026, 10, 8, 23, 59, 59)),
        ("same-time", "sakura", datetime(2026, 10, 8, 23, 59, 59)),
    ]
    store.append_many([
        NewTimelineEntry(entry_id, entry_id, character, TimelineKind.HUMAN, "chat",
                         created.astimezone(timezone.utc).isoformat(), {"text": entry_id})
        for entry_id, character, created in records
    ])
    boundary = _boundary(tmp_path, ["sakura"])
    request = {"expectedCharacterId": "sakura", "beforeCursor": None, "limit": 2}

    result = boundary.handle(_request({**request, "fromDate": "2026-10-08"}))
    assert result["ok"] is True
    page = result["payload"]
    assert page["entries"][0]["entryId"] == "day-start"
    assert page["totalCount"] == 5
    assert page["beforeCursor"] is not None
    assert page["afterCursor"] is not None

    earlier = boundary.handle(_request({**request, "beforeCursor": page["beforeCursor"]}))["payload"]
    assert [entry["entryId"] for entry in earlier["entries"]] == ["previous-day"]
    assert earlier["beforeCursor"] is None

    forward = [entry["entryId"] for entry in page["entries"]]
    while page["afterCursor"]:
        page = boundary.handle(_request({**request, "afterCursor": page["afterCursor"]}))["payload"]
        forward.extend(entry["entryId"] for entry in page["entries"])
    assert forward == ["day-start", "day-end", "same-time", "next-day"]

    empty = boundary.handle(_request({**request, "fromDate": "2026-10-10"}))["payload"]
    assert empty["entries"] == []
    assert empty["beforeCursor"] is None
    assert empty["afterCursor"] is None

    latest = boundary.handle(_request(request))["payload"]
    assert latest["entries"][-1]["entryId"] == "next-day"


@pytest.mark.parametrize("value", ["2026-02-30", "invalid", 123])
def test_history_date_jump_reports_invalid_date(tmp_path, value) -> None:
    result = _boundary(tmp_path, ["sakura"]).handle(_request({
        "expectedCharacterId": "sakura", "beforeCursor": None, "limit": 50, "fromDate": value,
    }))
    assert result["ok"] is False
    assert result["error"]["code"] == "INVALID_REQUEST"


def test_history_calendar_lists_local_days_without_other_characters_or_duplicate_dates(tmp_path) -> None:
    store = TimelineStore(StoragePaths(tmp_path).timeline_database())
    store.initialize()
    records = [
        ("start", "sakura", datetime(2026, 10, 8)),
        ("end", "sakura", datetime(2026, 10, 8, 23, 59, 59)),
        ("later", "sakura", datetime(2026, 10, 20, 12)),
        ("outside", "sakura", datetime(2026, 9, 30, 23, 59, 59)),
        ("other", "other", datetime(2026, 10, 9, 12)),
    ]
    store.append_many([
        NewTimelineEntry(entry_id, entry_id, character, TimelineKind.HUMAN, "chat",
                         created.astimezone(timezone.utc).isoformat(), {"text": entry_id})
        for entry_id, character, created in records
    ])
    current = ["sakura"]
    boundary = _boundary(tmp_path, current)
    request = {**_request({
        "expectedGenerationId": GENERATION, "expectedCharacterId": "sakura",
        "startDate": "2026-10-01", "endDate": "2026-10-31",
    }), "name": "ui.history.dates"}

    result = boundary.handle(request)
    assert result["ok"] is True
    assert result["payload"]["dates"] == ["2026-10-08", "2026-10-20"]

    request["payload"].update(startDate="2026-11-01", endDate="2026-11-30")
    assert boundary.handle(request)["payload"]["dates"] == []
    current[0] = "other"
    assert boundary.handle(request)["error"]["code"] == "HISTORY_CHARACTER_MISMATCH"


def test_history_search_matches_text_and_translation_without_metadata_or_other_characters(tmp_path) -> None:
    store = TimelineStore(StoragePaths(tmp_path).timeline_database())
    store.initialize()
    records = [
        ("human", "sakura", TimelineKind.HUMAN, {"text": "春天去赏花，预算 100%_，带上 Sakura 🌸"}),
        ("assistant", "sakura", TimelineKind.ASSISTANT, {"segments": [
            {"text": "桜を見に行こう", "translation": "一起去赏花吧", "tone": "metadata-only",
             "portrait": "neutral", "suppressTts": False}]}),
        ("activity", "sakura", TimelineKind.SYSTEM, {"text": "一局游戏", "activity": {
            "sourcePluginId": "metadata-only", "recordId": "private-id", "details": "复盘：春天的棋局", "actionLabel": "打开棋谱"}}),
        ("hidden", "sakura", TimelineKind.SYSTEM, {"text": "不显示的春天", "eventType": "app.reconnected"}),
        ("other", "other", TimelineKind.HUMAN, {"text": "春天去赏花"}),
    ]
    store.append_many([NewTimelineEntry(entry_id, entry_id, character, kind, "chat", NOW, payload)
                       for entry_id, character, kind, payload in records])
    boundary = _boundary(tmp_path, ["sakura"])
    for query, expected in [
        ("赏花", ["human", "assistant"]), ("桜", ["assistant"]), ("春", ["human", "activity"]),
        ("SAKURA", ["human"]), ("%_", ["human"]), ("🌸", ["human"]),
        ("metadata-only", []), ("private-id", []), ("' OR 1=1 --", []),
    ]:
        response = boundary.handle(_request({"expectedCharacterId": "sakura", "limit": 50, "query": query}))
        assert response["ok"] is True, response
        page = response["payload"]
        assert [entry["entryId"] for entry in page["entries"]] == expected
        assert page["totalCount"] == len(expected)


def test_history_search_pages_and_opens_the_exact_record_with_unfiltered_context(tmp_path) -> None:
    store = TimelineStore(StoragePaths(tmp_path).timeline_database())
    store.initialize()
    # 同一时间多条记录，命中项之间穿插不匹配的上下文。
    store.append_many([
        NewTimelineEntry(f"entry-{i}", f"entry-{i}", "sakura", TimelineKind.HUMAN, "chat", NOW,
                         {"text": f"{'命中' if i % 2 else '上下文'} {i}"})
        for i in range(8)
    ])
    store.append(NewTimelineEntry("other", "other", "other", TimelineKind.HUMAN, "chat", NOW, {"text": "命中"}))
    boundary = _boundary(tmp_path, ["sakura"])
    request = {"expectedCharacterId": "sakura", "limit": 2}
    page = boundary.handle(_request({**request, "query": "命中"}))["payload"]
    assert [entry["entryId"] for entry in page["entries"]] == ["entry-5", "entry-7"]
    earlier = boundary.handle(_request({**request, "query": "命中", "beforeCursor": page["beforeCursor"]}))["payload"]
    assert [entry["entryId"] for entry in earlier["entries"]] == ["entry-1", "entry-3"]
    assert earlier["beforeCursor"] is None
    assert earlier["totalCount"] == 4

    context = boundary.handle(_request({**request, "fromEntryId": "entry-3"}))["payload"]
    assert [entry["entryId"] for entry in context["entries"]] == ["entry-3", "entry-4"]
    assert context["totalCount"] == 8
    before = boundary.handle(_request({**request, "beforeCursor": context["beforeCursor"]}))["payload"]
    after = boundary.handle(_request({**request, "afterCursor": context["afterCursor"]}))["payload"]
    assert [entry["entryId"] for entry in before["entries"]] == ["entry-1", "entry-2"]
    assert [entry["entryId"] for entry in after["entries"]] == ["entry-5", "entry-6"]
    inaccessible = boundary.handle(_request({**request, "fromEntryId": "other"}))
    assert inaccessible["ok"] is False
    assert inaccessible["error"]["code"] == "TIMELINE_ENTRY_NOT_FOUND"
