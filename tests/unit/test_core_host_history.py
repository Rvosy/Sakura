from __future__ import annotations

from types import SimpleNamespace

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
        session_provider=lambda: SimpleNamespace(character=SimpleNamespace(id=current[0])),
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
                "payload": {"text": "safe screen summary"},
            },
        ],
        "beforeCursor": None,
        "hasMore": False,
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
