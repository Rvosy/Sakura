from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path

import pytest

from app.core_host.plugin_host_services import HostServiceError, _TimelineHostService
from app.storage.timeline import (
    NewTimelineEntry,
    TimelineDataError,
    TimelineKind,
    TimelineStore,
    import_legacy_histories,
)


NOW = "2026-08-26T12:00:00+08:00"


def _entry(kind: TimelineKind, payload: dict[str, object], *, character_id: str = "sakura") -> NewTimelineEntry:
    return NewTimelineEntry(
        entry_id=f"entry-{kind.value}",
        turn_id="turn-1",
        character_id=character_id,
        kind=kind,
        origin="chat",
        created_at=NOW,
        payload=payload,
    )


def test_typed_entries_round_trip_and_are_character_scoped(tmp_path: Path) -> None:
    store = TimelineStore(tmp_path / "timeline.sqlite3")
    import_legacy_histories(store, tmp_path / "missing-history", [])
    entries = [
        _entry(TimelineKind.HUMAN, {"text": "hello"}),
        NewTimelineEntry(
            entry_id="entry-assistant",
            turn_id="turn-1",
            character_id="sakura",
            kind=TimelineKind.ASSISTANT,
            origin="chat",
            created_at=NOW,
            payload={
                "segments": [
                    {
                        "text": "ただいま",
                        "translation": "我回来了",
                        "tone": "中性",
                        "portrait": "neutral",
                        "suppressTts": False,
                    },
                    {
                        "text": "うん",
                        "translation": "嗯",
                        "tone": "开心",
                        "portrait": "smile",
                        "suppressTts": True,
                    },
                ]
            },
        ),
        NewTimelineEntry(
            entry_id="entry-observation",
            turn_id="turn-2",
            character_id="sakura",
            kind=TimelineKind.OBSERVATION,
            origin="manual_screen",
            created_at=NOW,
            payload={
                "text": "screen description",
                "visual": {
                    "imageCount": 1,
                    "visualId": "vis-1",
                    "capturedAt": NOW,
                },
            },
        ),
        NewTimelineEntry(
            entry_id="entry-system",
            turn_id="turn-3",
            character_id="other",
            kind=TimelineKind.SYSTEM,
            origin="host",
            created_at=NOW,
            payload={"text": "confirmed", "eventType": "relationship"},
        ),
    ]

    store.append_many(entries)

    sakura = store.read_all("sakura")
    assert [entry.kind for entry in sakura] == [
        TimelineKind.HUMAN,
        TimelineKind.ASSISTANT,
        TimelineKind.OBSERVATION,
    ]
    assert len(sakura[1].payload["segments"]) == 2
    assert store.read_all("other")[0].kind is TimelineKind.SYSTEM


@pytest.mark.parametrize(
    ("entry", "code"),
    [
        (_entry(TimelineKind.HUMAN, {"text": "", "extra": 1}), "TIMELINE_PAYLOAD_SHAPE_INVALID"),
        (_entry(TimelineKind.ASSISTANT, {"segments": []}), "TIMELINE_SEGMENTS_INVALID"),
        (
            NewTimelineEntry(
                entry_id="unsafe",
                turn_id="turn",
                character_id="sakura",
                kind=TimelineKind.OBSERVATION,
                origin="manual_screen",
                created_at=NOW,
                payload={"text": "safe", "visual": {"resourceToken": "secret"}},
            ),
            "TIMELINE_VISUAL_INVALID",
        ),
        (
            _entry(
                TimelineKind.ASSISTANT,
                {
                    "segments": [
                        {
                            "text": "reply",
                            "translation": "",
                            "tone": "",
                            "portrait": "C:\\temp\\portrait.png",
                            "suppressTts": False,
                        }
                    ]
                },
            ),
            "TIMELINE_SEGMENT_UNSAFE",
        ),
        (
            NewTimelineEntry(
                entry_id="nan",
                turn_id="turn",
                character_id="sakura",
                kind=TimelineKind.OBSERVATION,
                origin="manual_screen",
                created_at=NOW,
                payload={"text": "safe", "visual": {"imageCount": float("nan")}},
            ),
            "TIMELINE_VISUAL_INVALID",
        ),
    ],
)
def test_invalid_or_unsafe_payload_is_rejected(
    tmp_path: Path,
    entry: NewTimelineEntry,
    code: str,
) -> None:
    with pytest.raises(TimelineDataError, match=code):
        TimelineStore(tmp_path / "timeline.sqlite3").append(entry)


@pytest.mark.parametrize(
    "unsafe_path",
    [
        "C:\\temp\\portrait.png",
        "C:portrait.png",
        "\\temp\\portrait.png",
        "/tmp/portrait.png",
        "data:image/png;base64,AA",
    ],
)
def test_portrait_rejects_portable_paths_and_data_urls(tmp_path: Path, unsafe_path: str) -> None:
    entry = _entry(
        TimelineKind.ASSISTANT,
        {
            "segments": [
                {
                    "text": "reply",
                    "translation": "",
                    "tone": "",
                    "portrait": unsafe_path,
                    "suppressTts": False,
                }
            ]
        },
    )
    with pytest.raises(TimelineDataError, match="TIMELINE_SEGMENT_UNSAFE"):
        TimelineStore(tmp_path / "timeline.sqlite3").append(entry)


@pytest.mark.parametrize(
    "unsafe_id",
    [
        "C:\\Users\\me\\shot.png",
        "C:shot.png",
        "\\temp\\shot.png",
        "/tmp/shot.png",
        "data:token",
        " data:token",
    ],
)
def test_visual_id_rejects_portable_paths_and_data_urls(tmp_path: Path, unsafe_id: str) -> None:
    entry = NewTimelineEntry(
        entry_id="unsafe-visual",
        turn_id="turn",
        character_id="sakura",
        kind=TimelineKind.OBSERVATION,
        origin="manual_screen",
        created_at=NOW,
        payload={"text": "screen", "visual": {"visualId": unsafe_id}},
    )
    with pytest.raises(TimelineDataError, match="TIMELINE_VISUAL_INVALID"):
        TimelineStore(tmp_path / "timeline.sqlite3").append(entry)


@pytest.mark.parametrize("created_at", [42, "2026-01-01T00:00:00+00:00" + "0" * 100])
def test_created_at_must_be_a_bounded_string(tmp_path: Path, created_at: object) -> None:
    entry = NewTimelineEntry(
        entry_id="bad-time",
        turn_id="turn",
        character_id="sakura",
        kind=TimelineKind.HUMAN,
        origin="chat",
        created_at=created_at,  # type: ignore[arg-type]
        payload={"text": "hello"},
    )
    with pytest.raises(TimelineDataError, match="TIMELINE_CREATED_AT_INVALID"):
        TimelineStore(tmp_path / "timeline.sqlite3").append(entry)


def test_turn_id_cannot_cross_characters_in_batch_or_existing_rows(tmp_path: Path) -> None:
    store = TimelineStore(tmp_path / "timeline.sqlite3")
    import_legacy_histories(store, tmp_path / "missing-history", [])
    first = _entry(TimelineKind.HUMAN, {"text": "one"})
    other = NewTimelineEntry(
        entry_id="entry-other",
        turn_id=first.turn_id,
        character_id="other",
        kind=TimelineKind.HUMAN,
        origin="chat",
        created_at=NOW,
        payload={"text": "two"},
    )

    with pytest.raises(TimelineDataError, match="TIMELINE_TURN_CHARACTER_MISMATCH"):
        store.append_many([first, other])
    assert store.read_all("sakura") == []

    store.append(first)
    with pytest.raises(TimelineDataError, match="TIMELINE_TURN_CHARACTER_MISMATCH"):
        store.append(other)
    assert store.read_all("other") == []


def test_legacy_import_preserves_order_fields_archives_and_is_idempotent(tmp_path: Path) -> None:
    history_dir = tmp_path / "chat_history"
    history_dir.mkdir()
    archive = history_dir / "sakura.jsonl.20260101.archive"
    current = history_dir / "sakura.jsonl"
    archive.write_text(
        json.dumps({"created_at": "2026-01-01T00:00:00+00:00", "role": "user", "content": "old"}) + "\n",
        encoding="utf-8",
    )
    current.write_text(
        json.dumps(
            {
                "created_at": "2026-01-01T00:00:01+00:00",
                "role": "assistant",
                "content": "reply",
                "translation": "回复",
                "tone": "中性",
                "portrait": "neutral",
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    before = {path: path.read_bytes() for path in (archive, current)}
    legacy_root = tmp_path / "chat_history.jsonl.migrated"
    corrupt_backup = history_dir / "sakura.jsonl.corrupt-old.bak"
    legacy_root.write_bytes(b"legacy-root\n")
    corrupt_backup.write_bytes(b"corrupt-backup\n")
    preserved = {path: path.read_bytes() for path in (legacy_root, corrupt_backup)}
    store = TimelineStore(tmp_path / "timeline.sqlite3")

    assert import_legacy_histories(store, history_dir, ["sakura"]) == 2
    store.assert_activated()
    first = store.read_all("sakura")
    assert import_legacy_histories(store, history_dir, ["sakura"]) == 2
    second = store.read_all("sakura")

    assert first == second
    assert [entry.kind for entry in first] == [TimelineKind.HUMAN, TimelineKind.ASSISTANT]
    assert first[0].turn_id == first[1].turn_id
    assert first[1].payload["segments"] == [
        {
            "text": "reply",
            "translation": "回复",
            "tone": "中性",
            "portrait": "neutral",
            "suppressTts": False,
        }
    ]
    assert {path: path.read_bytes() for path in (archive, current)} == before
    assert {path: path.read_bytes() for path in preserved} == preserved


def test_legacy_import_accepts_structurally_valid_empty_records(tmp_path: Path) -> None:
    history_dir = tmp_path / "chat_history"
    history_dir.mkdir()
    (history_dir / "sakura.jsonl").write_text(
        '{"created_at":"2026-01-01T00:00:00+00:00","role":"user","content":""}\n'
        '{"created_at":"2026-01-01T00:00:01+00:00","role":"assistant","content":""}\n',
        encoding="utf-8",
    )

    store = TimelineStore(tmp_path / "timeline.sqlite3")
    assert import_legacy_histories(store, history_dir, ["sakura"]) == 2
    assert [entry.payload for entry in store.read_all("sakura")] == [
        {"text": ""},
        {
            "segments": [
                {
                    "text": "",
                    "translation": "",
                    "tone": "",
                    "portrait": "",
                    "suppressTts": False,
                }
            ]
        },
    ]


def test_legacy_import_skips_errors_and_combines_consecutive_assistant_segments(
    tmp_path: Path,
) -> None:
    history_dir = tmp_path / "chat_history"
    history_dir.mkdir()
    source = history_dir / "sakura.jsonl"
    records = [
        {"created_at": "2026-01-01T00:00:01+00:00", "role": "user", "content": "hello"},
        {
            "created_at": "2026-01-01T00:00:02+00:00",
            "role": "assistant",
            "content": "first",
            "translation": "一",
            "tone": "neutral",
            "portrait": "one",
            "entry_id": "legacy-generation-one",
        },
        {"created_at": "2026-01-01T00:00:02+00:00", "role": "error", "content": "old failure"},
        {
            "created_at": "2026-01-01T00:00:03+00:00",
            "role": "assistant",
            "content": "second",
            "translation": "二",
            "tone": "happy",
            "portrait": "two",
            "entry_id": "legacy-generation-two",
        },
    ]
    source.write_text(
        "".join(json.dumps(record, ensure_ascii=False) + "\n" for record in records),
        encoding="utf-8",
    )
    before = source.read_bytes()
    store = TimelineStore(tmp_path / "timeline.sqlite3")

    assert import_legacy_histories(store, history_dir, ["sakura"]) == 2
    first = store.read_all("sakura")
    assert import_legacy_histories(store, history_dir, ["sakura"]) == 2
    assert store.read_all("sakura") == first
    assert source.read_bytes() == before
    assert [entry.kind for entry in first] == [TimelineKind.HUMAN, TimelineKind.ASSISTANT]
    assert len(first) == 2
    assert first[0].turn_id == first[1].turn_id
    assert first[1].payload["segments"] == [
        {
            "text": "first",
            "translation": "一",
            "tone": "neutral",
            "portrait": "one",
            "suppressTts": False,
        },
        {
            "text": "second",
            "translation": "二",
            "tone": "happy",
            "portrait": "two",
            "suppressTts": False,
        },
    ]


def test_legacy_assistant_group_over_segment_limit_fails_without_splitting(
    tmp_path: Path,
) -> None:
    history_dir = tmp_path / "chat_history"
    history_dir.mkdir()
    source = history_dir / "sakura.jsonl"
    records = [
        {"created_at": "2026-01-01T00:00:00+00:00", "role": "user", "content": "hello"},
        *(
            {
                "created_at": "2026-01-01T00:00:01+00:00",
                "role": "assistant",
                "content": f"segment-{index}",
            }
            for index in range(65)
        ),
    ]
    source.write_text(
        "".join(json.dumps(record) + "\n" for record in records),
        encoding="utf-8",
    )
    before = source.read_bytes()
    store = TimelineStore(tmp_path / "timeline.sqlite3")

    with pytest.raises(TimelineDataError, match="TIMELINE_SEGMENTS_INVALID"):
        import_legacy_histories(store, history_dir, ["sakura"])

    assert source.read_bytes() == before
    with pytest.raises(TimelineDataError, match="TIMELINE_NOT_ACTIVATED"):
        store.read_all("sakura")


def test_legacy_system_entry_is_imported_without_becoming_human(tmp_path: Path) -> None:
    history_dir = tmp_path / "chat_history"
    history_dir.mkdir()
    (history_dir / "sakura.jsonl").write_text(
        '{"created_at":"2026-01-01T00:00:00+00:00","role":"system","content":"host fact"}\n'
        '{"created_at":"2026-01-01T00:00:01+00:00","role":"assistant","content":"reply"}\n',
        encoding="utf-8",
    )

    store = TimelineStore(tmp_path / "timeline.sqlite3")
    import_legacy_histories(store, history_dir, ["sakura"])
    entries = store.read_all("sakura")

    assert [entry.kind for entry in entries] == [TimelineKind.SYSTEM, TimelineKind.ASSISTANT]
    assert entries[0].turn_id == entries[1].turn_id
    assert entries[0].payload == {"text": "host fact"}


def test_legacy_import_rejects_character_filename_collision_and_unclaimed_sources(tmp_path: Path) -> None:
    history_dir = tmp_path / "chat_history"
    history_dir.mkdir()
    (history_dir / "same_name.jsonl").write_text("", encoding="utf-8")

    store = TimelineStore(tmp_path / "timeline.sqlite3")
    with pytest.raises(TimelineDataError, match="LEGACY_HISTORY_CHARACTER_COLLISION"):
        import_legacy_histories(store, history_dir, ["same/name", "same?name"])
    with pytest.raises(TimelineDataError, match="LEGACY_HISTORY_CHARACTER_UNKNOWN"):
        import_legacy_histories(store, history_dir, ["sakura"])


def test_archive_without_current_file_must_also_be_claimed(tmp_path: Path) -> None:
    history_dir = tmp_path / "chat_history"
    history_dir.mkdir()
    (history_dir / "retired.jsonl.20260101.archive").write_text("", encoding="utf-8")

    with pytest.raises(TimelineDataError, match="LEGACY_HISTORY_CHARACTER_UNKNOWN"):
        import_legacy_histories(
            TimelineStore(tmp_path / "timeline.sqlite3"),
            history_dir,
            ["sakura"],
        )


def test_discovered_source_uses_actual_case_and_rejects_directory(tmp_path: Path) -> None:
    history_dir = tmp_path / "chat_history"
    history_dir.mkdir()
    actual = history_dir / "Sakura.jsonl"
    actual.write_text(
        '{"created_at":"2026-01-01T00:00:00+00:00","role":"user","content":"ok"}\n',
        encoding="utf-8",
    )
    store = TimelineStore(tmp_path / "timeline.sqlite3")
    assert import_legacy_histories(store, history_dir, ["sakura"]) == 1
    assert store.read_all("sakura")[0].payload == {"text": "ok"}

    directory_history = tmp_path / "directory-history"
    directory_history.mkdir()
    (directory_history / "sakura.jsonl").mkdir()
    with pytest.raises(TimelineDataError, match="HISTORY_PATH_UNSAFE"):
        import_legacy_histories(
            TimelineStore(tmp_path / "directory.sqlite3"),
            directory_history,
            ["sakura"],
        )


def test_invalid_optional_legacy_display_field_blocks_import(tmp_path: Path) -> None:
    history_dir = tmp_path / "chat_history"
    history_dir.mkdir()
    source = history_dir / "sakura.jsonl"
    source.write_text(
        '{"created_at":"2026-01-01T00:00:00+00:00","role":"assistant",'
        '"content":"reply","translation":42}\n',
        encoding="utf-8",
    )
    before = source.read_bytes()

    with pytest.raises(TimelineDataError, match="HISTORY_DATA_INVALID"):
        import_legacy_histories(
            TimelineStore(tmp_path / "timeline.sqlite3"),
            history_dir,
            ["sakura"],
        )
    assert source.read_bytes() == before


def test_corrupt_legacy_source_rolls_back_every_character_and_preserves_bytes(tmp_path: Path) -> None:
    history_dir = tmp_path / "chat_history"
    history_dir.mkdir()
    good = history_dir / "sakura.jsonl"
    bad = history_dir / "other.jsonl"
    good.write_text(
        '{"created_at":"2026-01-01T00:00:00+00:00","role":"user","content":"ok"}\n',
        encoding="utf-8",
    )
    bad.write_bytes(b'{"created_at":"broken"')
    before = {path: path.read_bytes() for path in (good, bad)}
    store = TimelineStore(tmp_path / "timeline.sqlite3")

    with pytest.raises(ValueError, match="HISTORY_DATA_INVALID"):
        import_legacy_histories(store, history_dir, ["sakura", "other"])

    with pytest.raises(TimelineDataError, match="TIMELINE_NOT_ACTIVATED"):
        store.read_all("sakura")
    assert {path: path.read_bytes() for path in (good, bad)} == before


def test_import_validation_failure_does_not_keep_partial_rows(tmp_path: Path) -> None:
    history_dir = tmp_path / "chat_history"
    history_dir.mkdir()
    (history_dir / "sakura.jsonl").write_text(
        '{"created_at":"2026-01-01T00:00:00+00:00","role":"tool","content":"bad"}\n',
        encoding="utf-8",
    )
    store = TimelineStore(tmp_path / "timeline.sqlite3")

    with pytest.raises(TimelineDataError, match="LEGACY_HISTORY_ROLE_INVALID"):
        import_legacy_histories(store, history_dir, ["sakura"])

    if store.path.exists():
        with sqlite3.connect(store.path) as connection:
            table = connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='timeline_entries'"
            ).fetchone()
            if table:
                assert connection.execute("SELECT count(*) FROM timeline_entries").fetchone()[0] == 0


def test_post_insert_verification_failure_rolls_back_new_rows(tmp_path: Path) -> None:
    history_dir = tmp_path / "chat_history"
    history_dir.mkdir()
    (history_dir / "sakura.jsonl").write_text(
        '{"created_at":"2026-01-01T00:00:00+00:00","role":"user","content":"source"}\n',
        encoding="utf-8",
    )
    store = TimelineStore(tmp_path / "timeline.sqlite3")
    import_legacy_histories(store, tmp_path / "missing-history", [])
    store.append(
        NewTimelineEntry(
            entry_id="unexpected-legacy-row",
            turn_id="unexpected-turn",
            character_id="sakura",
            kind=TimelineKind.HUMAN,
            origin="legacy_chat",
            created_at=NOW,
            payload={"text": "unexpected"},
        )
    )

    with pytest.raises(TimelineDataError, match="LEGACY_IMPORT_VERIFY_FAILED"):
        import_legacy_histories(store, history_dir, ["sakura"])

    remaining = store.read_all("sakura")
    assert [entry.entry_id for entry in remaining] == ["unexpected-legacy-row"]


def test_schema_has_one_business_table_and_two_indexes(tmp_path: Path) -> None:
    store = TimelineStore(tmp_path / "timeline.sqlite3")
    import_legacy_histories(store, tmp_path / "missing-history", [])
    store.append(_entry(TimelineKind.HUMAN, {"text": "hello"}))

    with sqlite3.connect(store.path) as connection:
        tables = {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
            if not row[0].startswith("sqlite_")
        }
        indexes = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='index' AND name LIKE 'timeline_%'"
            )
        }
    assert tables == {"timeline_entries"}
    assert indexes == {"timeline_character_seq", "timeline_character_turn_seq"}


def test_activated_store_does_not_recreate_database_deleted_during_runtime(tmp_path: Path) -> None:
    store = TimelineStore(tmp_path / "timeline.sqlite3")
    import_legacy_histories(store, tmp_path / "missing-history", [])
    store.append(_entry(TimelineKind.HUMAN, {"text": "hello"}))
    store.path.unlink()

    with pytest.raises(TimelineDataError, match="TIMELINE_NOT_ACTIVATED"):
        store.read_all("sakura")
    with pytest.raises(TimelineDataError, match="TIMELINE_NOT_ACTIVATED"):
        store.append(_entry(TimelineKind.HUMAN, {"text": "again"}))
    assert not store.path.exists()


def test_activated_store_rejects_database_replaced_with_empty_sqlite(tmp_path: Path) -> None:
    store = TimelineStore(tmp_path / "timeline.sqlite3")
    import_legacy_histories(store, tmp_path / "missing-history", [])
    store.append(_entry(TimelineKind.HUMAN, {"text": "hello"}))
    store.path.unlink()
    with sqlite3.connect(store.path):
        pass

    with pytest.raises(TimelineDataError, match="TIMELINE_NOT_ACTIVATED"):
        store.read_all("sakura")
    with pytest.raises(TimelineDataError, match="TIMELINE_NOT_ACTIVATED"):
        store.append(_entry(TimelineKind.HUMAN, {"text": "again"}))
    with sqlite3.connect(store.path) as connection:
        assert connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='timeline_entries'"
        ).fetchone() is None


@pytest.mark.skipif(os.name != "nt", reason="Windows extended path contract")
def test_existing_connection_accepts_windows_extended_length_path(tmp_path: Path) -> None:
    ordinary = tmp_path / "timeline.sqlite3"
    extended = Path("\\\\?\\" + str(ordinary.resolve()))
    store = TimelineStore(extended)
    import_legacy_histories(store, tmp_path / "missing-history", [])

    store.append(_entry(TimelineKind.HUMAN, {"text": "hello"}))

    assert store.read_all("sakura")[0].payload == {"text": "hello"}


def test_cursor_reads_are_character_scoped_and_paginated(tmp_path: Path) -> None:
    store = TimelineStore(tmp_path / "timeline.sqlite3")
    import_legacy_histories(store, tmp_path / "missing-history", [])
    start = store.latest_cursor("sakura")
    store.append_many(
        [
            NewTimelineEntry(
                entry_id=f"entry-{index}",
                turn_id=f"turn-{index}",
                character_id="sakura",
                kind=TimelineKind.HUMAN,
                origin="chat",
                created_at=NOW,
                payload={"text": str(index)},
            )
            for index in range(4)
        ]
    )

    first, cursor, has_more = store.read_since("sakura", start, 2)
    second, final_cursor, has_more_after = store.read_since("sakura", cursor, 2)
    recent, recent_cursor = store.read_recent("sakura", 3)

    assert [entry.entry_id for entry in first] == ["entry-0", "entry-1"]
    assert has_more is True
    assert [entry.entry_id for entry in second] == ["entry-2", "entry-3"]
    assert has_more_after is False
    assert final_cursor == store.latest_cursor("sakura") == recent_cursor
    assert [entry.entry_id for entry in recent] == ["entry-1", "entry-2", "entry-3"]


@pytest.mark.parametrize("limit", [0, 501, True, 1.5])
def test_cursor_read_limit_is_bounded(tmp_path: Path, limit: object) -> None:
    store = TimelineStore(tmp_path / "timeline.sqlite3")
    import_legacy_histories(store, tmp_path / "missing-history", [])

    with pytest.raises(TimelineDataError, match="TIMELINE_LIMIT_INVALID"):
        store.read_recent("sakura", limit)  # type: ignore[arg-type]


def test_cursor_is_invalid_for_another_character_or_database_lineage(tmp_path: Path) -> None:
    first = TimelineStore(tmp_path / "first.sqlite3")
    second = TimelineStore(tmp_path / "second.sqlite3")
    import_legacy_histories(first, tmp_path / "missing-history", [])
    import_legacy_histories(second, tmp_path / "missing-history", [])
    first.append(_entry(TimelineKind.HUMAN, {"text": "hello"}))
    cursor = first.latest_cursor("sakura")

    with pytest.raises(TimelineDataError, match="TIMELINE_CURSOR_INVALID"):
        first.read_since("other", cursor, 10)
    with pytest.raises(TimelineDataError, match="TIMELINE_CURSOR_INVALID"):
        second.read_since("sakura", cursor, 10)

    with sqlite3.connect(first.path) as connection:
        connection.execute("DELETE FROM timeline_entries")
    with pytest.raises(TimelineDataError, match="TIMELINE_CURSOR_INVALID"):
        first.read_since("sakura", cursor, 10)


def test_timeline_host_service_exposes_only_current_character_typed_entries(
    tmp_path: Path,
) -> None:
    store = TimelineStore(tmp_path / "timeline.sqlite3")
    import_legacy_histories(store, tmp_path / "missing-history", [])
    store.append(_entry(TimelineKind.HUMAN, {"text": "hello"}))
    current = ["sakura"]
    service = _TimelineHostService(store, lambda: current[0])

    latest = service.call("latest_cursor", [])
    recent = service.call("read_recent", [{"limit": 10}])

    assert recent["cursor"] == latest["cursor"]
    assert recent["entries"] == [
        {
            "entryId": "entry-human",
            "turnId": "turn-1",
            "characterId": "sakura",
            "kind": "human",
            "origin": "chat",
            "createdAt": NOW,
            "payload": {"text": "hello"},
        }
    ]
    assert "seq" not in recent["entries"][0]

    current[0] = "other"
    with pytest.raises(HostServiceError, match="TIMELINE_CURSOR_INVALID") as error:
        service.call("read_since", [{"cursor": latest["cursor"], "limit": 10}])
    assert error.value.code == "TIMELINE_CURSOR_INVALID"

    with pytest.raises(HostServiceError, match="TIMELINE_LIMIT_INVALID") as error:
        service.call("read_recent", [{"limit": 501}])
    assert error.value.code == "TIMELINE_LIMIT_INVALID"


def test_timeline_host_service_paginates_before_private_frame_limit(tmp_path: Path) -> None:
    store = TimelineStore(tmp_path / "timeline.sqlite3")
    import_legacy_histories(store, tmp_path / "missing-history", [])
    start = store.latest_cursor("sakura")
    large_text = "x" * 60_000
    store.append_many(
        [
            NewTimelineEntry(
                entry_id=f"large-{index}",
                turn_id=f"turn-{index}",
                character_id="sakura",
                kind=TimelineKind.ASSISTANT,
                origin="chat",
                created_at=NOW,
                payload={
                    "segments": [
                        {
                            "text": large_text,
                            "translation": large_text,
                            "tone": "",
                            "portrait": "",
                            "suppressTts": False,
                        }
                    ]
                },
            )
            for index in range(9)
        ]
    )
    service = _TimelineHostService(store, lambda: "sakura")
    cursor = start
    seen: list[str] = []

    while True:
        page = service.call("read_since", [{"cursor": cursor, "limit": 500}])
        assert len(json.dumps(page, ensure_ascii=False).encode("utf-8")) < 1024 * 1024
        seen.extend(entry["entryId"] for entry in page["entries"])
        cursor = page["nextCursor"]
        if not page["hasMore"]:
            break

    assert seen == [f"large-{index}" for index in range(9)]
