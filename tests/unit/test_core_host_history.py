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


def test_history_images_survive_reopen_and_respect_entry_and_character_ownership(tmp_path):
    from types import SimpleNamespace
    from app.storage.history_images import HistoryImages

    store = TimelineStore(StoragePaths(tmp_path).timeline_database())
    store.initialize()
    images = HistoryImages(store.path)
    saved = images.save([SimpleNamespace(data_url="data:image/png;base64,aW1hZ2U=", width=10, height=20)])
    store.append(NewTimelineEntry("picture", "turn-picture", "sakura", TimelineKind.OBSERVATION,
        "manual_screen", NOW, {"text": "图片", "visual": {"imageCount": 1, "images": saved}}))
    current = ["sakura"]
    boundary = _boundary(tmp_path, current)
    page = _request({"expectedCharacterId": "sakura", "limit": 50})
    assert boundary.handle(page)["payload"]["entries"][0]["payload"]["images"] == [
        {**saved[0], "thumbnail": False, "preview": False}]
    request = {**_request({"expectedCharacterId": "sakura", "entryId": "picture", "imageId": saved[0]["id"], "variant": "original"}),
               "name": "ui.history.image"}
    from pathlib import Path
    assert Path(boundary.handle(request)["payload"]["path"]).read_bytes() == b"image"
    wrong = {**request, "payload": {**request["payload"], "entryId": "another-entry"}}
    assert boundary.handle(wrong)["error"]["code"] == "HISTORY_IMAGE_NOT_FOUND"
    current[0] = "other"
    assert boundary.handle(request)["error"]["code"] == "HISTORY_CHARACTER_MISMATCH"
    wrong = {**request, "payload": {**request["payload"], "expectedCharacterId": "other"}}
    assert boundary.handle(wrong)["error"]["code"] == "HISTORY_IMAGE_NOT_FOUND"
    current[0] = "sakura"
    images.path(saved[0]).unlink()
    assert "images" not in boundary.handle(page)["payload"]["entries"][0]["payload"]
    assert boundary.handle(request)["error"]["code"] == "HISTORY_IMAGE_NOT_FOUND"


def test_large_image_preserves_original_and_generates_bounded_previews(tmp_path):
    import base64
    from io import BytesIO
    from types import SimpleNamespace
    from PIL import Image
    from app.storage.history_images import HistoryImages, THUMBNAIL_EDGE, PREVIEW_EDGE

    buffer = BytesIO()
    with Image.new("RGB", (6000, 4000), "#9bbacd") as source:
        source.save(buffer, "JPEG")
    data = buffer.getvalue()
    images = HistoryImages(tmp_path / "timeline.sqlite3")
    saved = images.save([SimpleNamespace(data_url="data:image/jpeg;base64," + base64.b64encode(data).decode(),
                                       width=0, height=0)])[0]
    assert (saved["width"], saved["height"]) == (6000, 4000)
    assert images.path(saved).read_bytes() == data
    for variant, bound in (("thumbnail", THUMBNAIL_EDGE), ("preview", PREVIEW_EDGE)):
        with Image.open(images.path(saved, variant)) as resized:
            assert max(resized.size) == bound
            assert abs(resized.width / resized.height - 1.5) < 0.01
        assert images.path(saved, variant).stat().st_size < len(data)
    images.remove([saved])
    assert list(images.root.iterdir()) == []


def test_incomplete_image_batch_is_removed_and_imported_paths_are_rejected(tmp_path):
    from types import SimpleNamespace
    from app.storage.history_images import HistoryImages
    from app.storage.timeline import TimelineDataError

    images = HistoryImages(tmp_path / "timeline.sqlite3")
    with pytest.raises(ValueError):
        images.save([SimpleNamespace(data_url=url, width=0, height=0) for url in (
            "data:image/webp;base64,aW1hZ2U=", "data:image/png;base64,!invalid!")])
    assert list(images.root.iterdir()) == []
    store = TimelineStore(tmp_path / "timeline.sqlite3")
    store.initialize()
    with pytest.raises(TimelineDataError, match="TIMELINE_IMAGE_INVALID"):
        store.append(NewTimelineEntry("picture", "turn", "sakura", TimelineKind.OBSERVATION,
            "manual_screen", NOW, {"text": "图片", "visual": {"images": [
                {"id": "../private", "mediaType": "image/png", "width": 1, "height": 1}]}}))


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
    store.append(NewTimelineEntry("image", "image", "sakura", TimelineKind.OBSERVATION,
                                  "manual_screen", NOW, {"text": "你分享了一张图片。"}))
    boundary = _boundary(tmp_path, ["sakura"])
    for query, expected in [
        ("赏花", ["human", "assistant"]), ("桜", ["assistant"]), ("春", ["human", "activity"]),
        ("SAKURA", ["human"]), ("%_", ["human"]), ("🌸", ["human"]),
        ("metadata-only", []), ("private-id", []), ("' OR 1=1 --", []),
        ("分享", []),
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
