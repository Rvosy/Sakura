from __future__ import annotations

import base64
import hashlib
import json
import secrets
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, Iterator, Mapping, Sequence


MAX_ID_CHARS = 128
MAX_ORIGIN_CHARS = 64
MAX_TEXT_CHARS = 64 * 1024
MAX_SEGMENTS = 64
MAX_PAYLOAD_BYTES = 256 * 1024
MAX_TIMELINE_READ = 500
_CURSOR_VERSION = 1

ALLOWED_ORIGINS = {
    "chat",
    "manual_screen",
    "scheduled_screen",
    "proactive",
    "host",
}


class TimelineDataError(ValueError):
    pass


class TimelineKind(StrEnum):
    HUMAN = "human"
    ASSISTANT = "assistant"
    OBSERVATION = "observation"
    SYSTEM = "system"


@dataclass(frozen=True)
class TimelineEntry:
    seq: int
    entry_id: str
    turn_id: str
    character_id: str
    kind: TimelineKind
    origin: str
    created_at: str
    payload: dict[str, Any]


@dataclass(frozen=True)
class NewTimelineEntry:
    entry_id: str
    turn_id: str
    character_id: str
    kind: TimelineKind
    origin: str
    created_at: str
    payload: Mapping[str, Any]


class TimelineStore:
    """Small Host-owned store for committed interaction facts."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    def initialize(self) -> None:
        """Create a fresh Runtime v2 Timeline or validate the existing one."""

        if self.path.exists():
            self.assert_activated()
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with self._connect() as connection:
                _create_schema(connection)
                if int(connection.execute("PRAGMA application_id").fetchone()[0]) == 0:
                    connection.execute(
                        f"PRAGMA application_id = {secrets.randbelow(0x7FFFFFFE) + 1}"
                    )
                connection.execute("PRAGMA user_version = 1")
        except sqlite3.DatabaseError as exc:
            raise TimelineDataError("TIMELINE_DATABASE_INVALID") from exc

    def append(self, entry: NewTimelineEntry) -> TimelineEntry:
        return self.append_many([entry])[0]

    def append_many(self, entries: Sequence[NewTimelineEntry]) -> list[TimelineEntry]:
        if not entries:
            return []
        encoded = [_validated_row(entry) for entry in entries]
        if not self.path.is_file():
            raise TimelineDataError("TIMELINE_NOT_ACTIVATED")
        try:
            with self._connect_existing() as connection:
                _assert_activated_connection(connection)
                _assert_turn_ownership(connection, entries)
                rows: list[TimelineEntry] = []
                for entry, payload_json in encoded:
                    cursor = connection.execute(
                        """
                        INSERT INTO timeline_entries (
                            entry_id, turn_id, character_id, kind, origin,
                            created_at, payload_json
                        ) VALUES (?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            entry.entry_id,
                            entry.turn_id,
                            entry.character_id,
                            entry.kind.value,
                            entry.origin,
                            entry.created_at,
                            payload_json,
                        ),
                    )
                    rows.append(
                        TimelineEntry(
                            seq=int(cursor.lastrowid),
                            entry_id=entry.entry_id,
                            turn_id=entry.turn_id,
                            character_id=entry.character_id,
                            kind=entry.kind,
                            origin=entry.origin,
                            created_at=entry.created_at,
                            payload=dict(entry.payload),
                        )
                    )
                return rows
        except sqlite3.IntegrityError as exc:
            raise TimelineDataError("TIMELINE_ENTRY_CONFLICT") from exc
        except sqlite3.DatabaseError as exc:
            raise TimelineDataError("TIMELINE_DATABASE_INVALID") from exc

    def read_all(self, character_id: str) -> list[TimelineEntry]:
        _bounded_text("character_id", character_id, MAX_ID_CHARS)
        if not self.path.is_file():
            raise TimelineDataError("TIMELINE_NOT_ACTIVATED")
        try:
            with self._connect_existing() as connection:
                _assert_activated_connection(connection)
                rows = connection.execute(
                    """
                    SELECT seq, entry_id, turn_id, character_id, kind, origin,
                           created_at, payload_json
                    FROM timeline_entries
                    WHERE character_id = ?
                    ORDER BY seq
                    """,
                    (character_id,),
                ).fetchall()
        except sqlite3.DatabaseError as exc:
            raise TimelineDataError("TIMELINE_DATABASE_INVALID") from exc
        return [_entry_from_row(row) for row in rows]

    def read_context_candidates(
        self,
        character_id: str,
        *,
        observation_since: datetime,
        proactive_since: datetime,
    ) -> list[TimelineEntry]:
        """Read only turns that can participate in the next chat context.

        Human turns remain unbounded so the adaptive context policy can use large
        provider windows. Scheduled observations and assistant-only proactive
        turns are limited at the database boundary, which avoids decoding every
        expired screenshot observation on each request.
        """

        _bounded_text("character_id", character_id, MAX_ID_CHARS)
        observation_since_text = _aware_iso_datetime(
            "observation_since", observation_since
        )
        proactive_since_text = _aware_iso_datetime(
            "proactive_since",
            proactive_since,
        )
        if not self.path.is_file():
            raise TimelineDataError("TIMELINE_NOT_ACTIVATED")
        try:
            with self._connect_existing() as connection:
                _assert_activated_connection(connection)
                rows = connection.execute(
                    """
                    WITH eligible_turns(turn_id) AS (
                        SELECT turn_id
                        FROM timeline_entries
                        WHERE character_id = ? AND kind = 'human'
                        UNION
                        SELECT turn_id
                        FROM timeline_entries
                        WHERE character_id = ?
                          AND kind = 'observation'
                          AND origin = 'scheduled_screen'
                          AND julianday(created_at) >= julianday(?)
                        UNION
                        SELECT turn_id
                        FROM timeline_entries
                        WHERE character_id = ?
                          AND kind = 'assistant'
                          AND origin = 'proactive'
                          AND julianday(created_at) >= julianday(?)
                    )
                    SELECT entry.seq, entry.entry_id, entry.turn_id,
                           entry.character_id, entry.kind, entry.origin,
                           entry.created_at, entry.payload_json
                    FROM timeline_entries AS entry
                    JOIN eligible_turns USING (turn_id)
                    WHERE entry.character_id = ?
                    ORDER BY entry.seq
                    """,
                    (
                        character_id,
                        character_id,
                        observation_since_text,
                        character_id,
                        proactive_since_text,
                        character_id,
                    ),
                ).fetchall()
        except sqlite3.DatabaseError as exc:
            raise TimelineDataError("TIMELINE_DATABASE_INVALID") from exc
        return [_entry_from_row(row) for row in rows]

    def latest_cursor(self, character_id: str) -> str:
        _bounded_text("character_id", character_id, MAX_ID_CHARS)
        if not self.path.is_file():
            raise TimelineDataError("TIMELINE_NOT_ACTIVATED")
        try:
            with self._connect_existing() as connection:
                lineage = _assert_activated_connection(connection)
                row = connection.execute(
                    """
                    SELECT seq, entry_id
                    FROM timeline_entries
                    WHERE character_id = ?
                    ORDER BY seq DESC
                    LIMIT 1
                    """,
                    (character_id,),
                ).fetchone()
        except sqlite3.DatabaseError as exc:
            raise TimelineDataError("TIMELINE_DATABASE_INVALID") from exc
        if row is None:
            return _encode_cursor(character_id, lineage, 0, "")
        return _encode_cursor(character_id, lineage, int(row[0]), str(row[1]))

    def read_recent(
        self,
        character_id: str,
        limit: int,
        *,
        max_bytes: int | None = None,
    ) -> tuple[list[TimelineEntry], str]:
        _bounded_text("character_id", character_id, MAX_ID_CHARS)
        _validated_limit(limit)
        if not self.path.is_file():
            raise TimelineDataError("TIMELINE_NOT_ACTIVATED")
        try:
            with self._connect_existing() as connection:
                lineage = _assert_activated_connection(connection)
                rows = connection.execute(
                    """
                    SELECT seq, entry_id, turn_id, character_id, kind, origin,
                           created_at, payload_json
                    FROM timeline_entries
                    WHERE character_id = ?
                    ORDER BY seq DESC
                    LIMIT ?
                    """,
                    (character_id, limit),
                )
                entries_desc, _truncated = _bounded_entries(rows, limit, max_bytes=max_bytes)
        except sqlite3.DatabaseError as exc:
            raise TimelineDataError("TIMELINE_DATABASE_INVALID") from exc
        entries = list(reversed(entries_desc))
        if not entries:
            return [], _encode_cursor(character_id, lineage, 0, "")
        latest = entries[-1]
        return entries, _encode_cursor(character_id, lineage, latest.seq, latest.entry_id)

    def read_page_before(
        self,
        character_id: str,
        limit: int,
        *,
        before_cursor: str | None = None,
        max_bytes: int | None = None,
    ) -> tuple[list[TimelineEntry], str | None, bool, int]:
        """Read one newest-first window and return it in chronological order.

        ``before_cursor`` is an opaque anchor for the oldest entry already visible to
        the caller.  The returned cursor points at the oldest entry in this page and
        is present only when an earlier page exists.
        """

        _bounded_text("character_id", character_id, MAX_ID_CHARS)
        _validated_limit(limit)
        if before_cursor is not None and not isinstance(before_cursor, str):
            raise TimelineDataError("TIMELINE_CURSOR_INVALID")
        if not self.path.is_file():
            raise TimelineDataError("TIMELINE_NOT_ACTIVATED")
        try:
            with self._connect_existing() as connection:
                lineage = _assert_activated_connection(connection)
                before_seq: int | None = None
                if before_cursor is not None:
                    before_seq, entry_id = _decode_cursor(
                        before_cursor,
                        character_id,
                        lineage,
                    )
                    if before_seq <= 0:
                        raise TimelineDataError("TIMELINE_CURSOR_INVALID")
                    row = connection.execute(
                        "SELECT entry_id, character_id FROM timeline_entries WHERE seq = ?",
                        (before_seq,),
                    ).fetchone()
                    if row is None or row[0] != entry_id or row[1] != character_id:
                        raise TimelineDataError("TIMELINE_CURSOR_INVALID")

                total = int(
                    connection.execute(
                        "SELECT COUNT(*) FROM timeline_entries WHERE character_id = ?",
                        (character_id,),
                    ).fetchone()[0]
                )
                if before_seq is None:
                    rows = connection.execute(
                        """
                        SELECT seq, entry_id, turn_id, character_id, kind, origin,
                               created_at, payload_json
                        FROM timeline_entries
                        WHERE character_id = ?
                        ORDER BY seq DESC
                        LIMIT ?
                        """,
                        (character_id, limit + 1),
                    )
                else:
                    rows = connection.execute(
                        """
                        SELECT seq, entry_id, turn_id, character_id, kind, origin,
                               created_at, payload_json
                        FROM timeline_entries
                        WHERE character_id = ? AND seq < ?
                        ORDER BY seq DESC
                        LIMIT ?
                        """,
                        (character_id, before_seq, limit + 1),
                    )
                entries_desc, has_more = _bounded_entries(
                    rows,
                    limit,
                    max_bytes=max_bytes,
                )
        except sqlite3.DatabaseError as exc:
            raise TimelineDataError("TIMELINE_DATABASE_INVALID") from exc

        entries = list(reversed(entries_desc))
        if not entries:
            return [], None, False, total
        oldest = entries[0]
        next_before_cursor = (
            _encode_cursor(character_id, lineage, oldest.seq, oldest.entry_id)
            if has_more
            else None
        )
        return entries, next_before_cursor, has_more, total

    def read_since(
        self,
        character_id: str,
        cursor: str,
        limit: int,
        *,
        max_bytes: int | None = None,
    ) -> tuple[list[TimelineEntry], str, bool]:
        _bounded_text("character_id", character_id, MAX_ID_CHARS)
        _validated_limit(limit)
        if not self.path.is_file():
            raise TimelineDataError("TIMELINE_NOT_ACTIVATED")
        try:
            with self._connect_existing() as connection:
                lineage = _assert_activated_connection(connection)
                seq, entry_id = _decode_cursor(cursor, character_id, lineage)
                if seq:
                    row = connection.execute(
                        "SELECT entry_id, character_id FROM timeline_entries WHERE seq = ?",
                        (seq,),
                    ).fetchone()
                    if row is None or row[0] != entry_id or row[1] != character_id:
                        raise TimelineDataError("TIMELINE_CURSOR_INVALID")
                rows = connection.execute(
                    """
                    SELECT seq, entry_id, turn_id, character_id, kind, origin,
                           created_at, payload_json
                    FROM timeline_entries
                    WHERE character_id = ? AND seq > ?
                    ORDER BY seq
                    LIMIT ?
                    """,
                    (character_id, seq, limit + 1),
                )
                entries, has_more = _bounded_entries(rows, limit, max_bytes=max_bytes)
        except sqlite3.DatabaseError as exc:
            raise TimelineDataError("TIMELINE_DATABASE_INVALID") from exc
        if not entries:
            return [], cursor, False
        latest = entries[-1]
        next_cursor = _encode_cursor(character_id, lineage, latest.seq, latest.entry_id)
        return entries, next_cursor, has_more

    def assert_activated(self) -> None:
        if not self.path.is_file():
            raise TimelineDataError("TIMELINE_NOT_ACTIVATED")
        try:
            with self._connect_existing() as connection:
                _assert_activated_connection(connection)
        except sqlite3.DatabaseError as exc:
            raise TimelineDataError("TIMELINE_DATABASE_INVALID") from exc

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path)
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    @contextmanager
    def _connect_existing(self) -> Iterator[sqlite3.Connection]:
        resolved = str(self.path.resolve())
        if resolved.startswith("\\\\?\\UNC\\"):
            resolved = "\\\\" + resolved[8:]
        elif resolved.startswith("\\\\?\\"):
            resolved = resolved[4:]
        connection = sqlite3.connect(f"{Path(resolved).as_uri()}?mode=rw", uri=True)
        try:
            with connection:
                yield connection
        finally:
            connection.close()


def _create_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS timeline_entries (
            seq          INTEGER PRIMARY KEY AUTOINCREMENT,
            entry_id     TEXT NOT NULL UNIQUE,
            turn_id      TEXT NOT NULL,
            character_id TEXT NOT NULL,
            kind         TEXT NOT NULL CHECK (kind IN ('human', 'assistant', 'observation', 'system')),
            origin       TEXT NOT NULL,
            created_at   TEXT NOT NULL,
            payload_json TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS timeline_character_seq
            ON timeline_entries(character_id, seq);
        CREATE INDEX IF NOT EXISTS timeline_character_turn_seq
            ON timeline_entries(character_id, turn_id, seq);
        """
    )


def _database_lineage(connection: sqlite3.Connection) -> int:
    lineage = int(connection.execute("PRAGMA application_id").fetchone()[0])
    if lineage <= 0:
        raise TimelineDataError("TIMELINE_NOT_ACTIVATED")
    return lineage


def _assert_activated_connection(connection: sqlite3.Connection) -> int:
    if connection.execute("PRAGMA user_version").fetchone()[0] != 1:
        raise TimelineDataError("TIMELINE_NOT_ACTIVATED")
    lineage = _database_lineage(connection)
    connection.execute("SELECT 1 FROM timeline_entries LIMIT 1").fetchone()
    return lineage


def _validated_limit(value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= MAX_TIMELINE_READ:
        raise TimelineDataError("TIMELINE_LIMIT_INVALID")
    return value


def _aware_iso_datetime(name: str, value: datetime) -> str:
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() is None
    ):
        raise TimelineDataError(f"TIMELINE_{name.upper()}_INVALID")
    return value.isoformat(timespec="seconds")


def _bounded_entries(
    rows: Iterable[Sequence[Any]],
    limit: int,
    *,
    max_bytes: int | None,
) -> tuple[list[TimelineEntry], bool]:
    entries: list[TimelineEntry] = []
    used = 0
    for row in rows:
        if len(entries) >= limit:
            return entries, True
        entry = _entry_from_row(row)
        size = _timeline_transfer_bytes(entry)
        if entries and max_bytes is not None and used + size > max_bytes:
            return entries, True
        entries.append(entry)
        used += size
    return entries, False


def _timeline_transfer_bytes(entry: TimelineEntry) -> int:
    return len(
        json.dumps(
            {
                "entryId": entry.entry_id,
                "turnId": entry.turn_id,
                "characterId": entry.character_id,
                "kind": entry.kind.value,
                "origin": entry.origin,
                "createdAt": entry.created_at,
                "payload": entry.payload,
            },
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
    ) + 32


def _encode_cursor(character_id: str, lineage: int, seq: int, entry_id: str) -> str:
    payload = json.dumps(
        [_CURSOR_VERSION, character_id, lineage, seq, entry_id],
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    checksum = hashlib.sha256(payload).digest()[:8]
    return base64.urlsafe_b64encode(payload + checksum).decode("ascii").rstrip("=")


def _decode_cursor(cursor: str, character_id: str, lineage: int) -> tuple[int, str]:
    if not isinstance(cursor, str) or not cursor or len(cursor) > 512:
        raise TimelineDataError("TIMELINE_CURSOR_INVALID")
    try:
        padding = "=" * (-len(cursor) % 4)
        encoded = base64.b64decode(cursor + padding, altchars=b"-_", validate=True)
        payload, checksum = encoded[:-8], encoded[-8:]
        if len(checksum) != 8 or not secrets.compare_digest(
            checksum, hashlib.sha256(payload).digest()[:8]
        ):
            raise ValueError
        decoded = json.loads(payload.decode("utf-8"))
    except (UnicodeError, ValueError, json.JSONDecodeError) as exc:
        raise TimelineDataError("TIMELINE_CURSOR_INVALID") from exc
    if (
        not isinstance(decoded, list)
        or len(decoded) != 5
        or decoded[0] != _CURSOR_VERSION
        or decoded[1] != character_id
        or decoded[2] != lineage
        or isinstance(decoded[3], bool)
        or not isinstance(decoded[3], int)
        or decoded[3] < 0
        or not isinstance(decoded[4], str)
        or (decoded[3] == 0) != (decoded[4] == "")
    ):
        raise TimelineDataError("TIMELINE_CURSOR_INVALID")
    return decoded[3], decoded[4]


def _validated_row(entry: NewTimelineEntry) -> tuple[NewTimelineEntry, str]:
    _bounded_text("entry_id", entry.entry_id, MAX_ID_CHARS)
    _bounded_text("turn_id", entry.turn_id, MAX_ID_CHARS)
    _bounded_text("character_id", entry.character_id, MAX_ID_CHARS)
    _bounded_text("origin", entry.origin, MAX_ORIGIN_CHARS)
    if entry.origin not in ALLOWED_ORIGINS:
        raise TimelineDataError("TIMELINE_ORIGIN_INVALID")
    created_at = _bounded_text("created_at", entry.created_at, MAX_ORIGIN_CHARS)
    try:
        created = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
    except ValueError as exc:
        raise TimelineDataError("TIMELINE_CREATED_AT_INVALID") from exc
    if created.tzinfo is None or created.utcoffset() is None:
        raise TimelineDataError("TIMELINE_CREATED_AT_INVALID")
    payload = dict(entry.payload)
    _validate_payload(entry.kind, payload)
    try:
        payload_json = json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise TimelineDataError("TIMELINE_PAYLOAD_INVALID") from exc
    if len(payload_json.encode("utf-8")) > MAX_PAYLOAD_BYTES:
        raise TimelineDataError("TIMELINE_PAYLOAD_TOO_LARGE")
    return entry, payload_json


def _validate_payload(kind: TimelineKind, payload: dict[str, Any]) -> None:
    if kind is TimelineKind.HUMAN:
        _exact_keys(payload, {"text"})
        _bounded_text("text", payload.get("text"), MAX_TEXT_CHARS, allow_empty=True)
        return
    if kind is TimelineKind.ASSISTANT:
        _exact_keys(payload, {"segments"})
        segments = payload.get("segments")
        if not isinstance(segments, list) or not 1 <= len(segments) <= MAX_SEGMENTS:
            raise TimelineDataError("TIMELINE_SEGMENTS_INVALID")
        for segment in segments:
            if not isinstance(segment, dict):
                raise TimelineDataError("TIMELINE_SEGMENT_INVALID")
            _exact_keys(
                segment,
                {"text", "translation", "tone", "portrait", "suppressTts"},
            )
            for key in ("text", "translation", "tone", "portrait"):
                _bounded_text(key, segment.get(key), MAX_TEXT_CHARS, allow_empty=True)
            portrait = segment["portrait"]
            if _is_unsafe_resource_string(portrait):
                raise TimelineDataError("TIMELINE_SEGMENT_UNSAFE")
            if not isinstance(segment.get("suppressTts"), bool):
                raise TimelineDataError("TIMELINE_SEGMENT_INVALID")
        return
    if kind is TimelineKind.OBSERVATION:
        allowed = {"text", "visual"}
        if not set(payload) <= allowed or "text" not in payload:
            raise TimelineDataError("TIMELINE_PAYLOAD_SHAPE_INVALID")
        _bounded_text("text", payload.get("text"), MAX_TEXT_CHARS, allow_empty=True)
        visual = payload.get("visual")
        if visual is not None:
            _validate_visual_metadata(visual)
        return
    if kind is TimelineKind.SYSTEM:
        allowed = {"text", "eventType"}
        if not set(payload) <= allowed or "text" not in payload:
            raise TimelineDataError("TIMELINE_PAYLOAD_SHAPE_INVALID")
        _bounded_text("text", payload.get("text"), MAX_TEXT_CHARS, allow_empty=True)
        if "eventType" in payload:
            _bounded_text("eventType", payload["eventType"], MAX_ORIGIN_CHARS)
        return
    raise TimelineDataError("TIMELINE_KIND_INVALID")


def _validate_visual_metadata(value: Any) -> None:
    if not isinstance(value, dict) or not set(value) <= {
        "visualId",
        "imageCount",
        "capturedAt",
        "analysisStatus",
        "confidence",
        "sensitiveRedacted",
    }:
        raise TimelineDataError("TIMELINE_VISUAL_INVALID")
    if "visualId" in value:
        visual_id = _bounded_text("visualId", value["visualId"], MAX_ID_CHARS)
        if _is_unsafe_resource_string(visual_id) or "/" in visual_id or "\\" in visual_id:
            raise TimelineDataError("TIMELINE_VISUAL_INVALID")
    if "imageCount" in value:
        count = value["imageCount"]
        if isinstance(count, bool) or not isinstance(count, int) or not 1 <= count <= 64:
            raise TimelineDataError("TIMELINE_VISUAL_INVALID")
    if "capturedAt" in value:
        captured_at = _bounded_text("capturedAt", value["capturedAt"], MAX_ORIGIN_CHARS)
        try:
            parsed = datetime.fromisoformat(captured_at.replace("Z", "+00:00"))
        except ValueError as exc:
            raise TimelineDataError("TIMELINE_VISUAL_INVALID") from exc
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise TimelineDataError("TIMELINE_VISUAL_INVALID")
    if "analysisStatus" in value and value["analysisStatus"] != "succeeded":
        raise TimelineDataError("TIMELINE_VISUAL_INVALID")
    if "confidence" in value:
        confidence = value["confidence"]
        if (
            isinstance(confidence, bool)
            or not isinstance(confidence, (int, float))
            or not 0 <= float(confidence) <= 1
        ):
            raise TimelineDataError("TIMELINE_VISUAL_INVALID")
    if "sensitiveRedacted" in value and not isinstance(value["sensitiveRedacted"], bool):
        raise TimelineDataError("TIMELINE_VISUAL_INVALID")


def _is_unsafe_resource_string(value: str) -> bool:
    cleaned = value.strip()
    windows_path = PureWindowsPath(cleaned)
    return (
        cleaned.lower().startswith("data:")
        or PurePosixPath(cleaned).is_absolute()
        or bool(windows_path.drive)
        or bool(windows_path.root)
    )


def _assert_turn_ownership(
    connection: sqlite3.Connection,
    entries: Sequence[NewTimelineEntry],
) -> None:
    owners: dict[str, str] = {}
    for entry in entries:
        previous = owners.setdefault(entry.turn_id, entry.character_id)
        if previous != entry.character_id:
            raise TimelineDataError("TIMELINE_TURN_CHARACTER_MISMATCH")
    for turn_id, character_id in owners.items():
        existing = connection.execute(
            "SELECT DISTINCT character_id FROM timeline_entries WHERE turn_id = ?",
            (turn_id,),
        ).fetchall()
        if any(row[0] != character_id for row in existing):
            raise TimelineDataError("TIMELINE_TURN_CHARACTER_MISMATCH")


def _entry_from_row(row: sqlite3.Row | tuple[Any, ...]) -> TimelineEntry:
    seq, entry_id, turn_id, character_id, kind, origin, created_at, payload_json = row
    try:
        parsed_kind = TimelineKind(kind)
        payload = json.loads(payload_json)
        if not isinstance(payload, dict):
            raise TypeError
        entry = NewTimelineEntry(
            entry_id=entry_id,
            turn_id=turn_id,
            character_id=character_id,
            kind=parsed_kind,
            origin=origin,
            created_at=created_at,
            payload=payload,
        )
        _validated_row(entry)
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise TimelineDataError("TIMELINE_ROW_INVALID") from exc
    return TimelineEntry(seq, entry_id, turn_id, character_id, parsed_kind, origin, created_at, payload)


def _exact_keys(value: Mapping[str, Any], expected: set[str]) -> None:
    if set(value) != expected:
        raise TimelineDataError("TIMELINE_PAYLOAD_SHAPE_INVALID")


def _bounded_text(
    name: str,
    value: Any,
    maximum: int,
    *,
    allow_empty: bool = False,
) -> str:
    if not isinstance(value, str):
        raise TimelineDataError(f"TIMELINE_{name.upper()}_INVALID")
    if (not allow_empty and not value.strip()) or len(value) > maximum:
        raise TimelineDataError(f"TIMELINE_{name.upper()}_INVALID")
    return value
