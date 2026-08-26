from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, Iterable, Iterator, Mapping, Sequence

from app.storage.chat_history import ChatHistoryEntry, ChatHistoryStore
from app.storage.paths import sanitize_file_stem


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
    "legacy_chat",
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


def import_legacy_histories(
    store: TimelineStore,
    history_dir: Path,
    character_ids: Iterable[str],
) -> int:
    """Strictly import current JSONL files and their archive segments once.

    Every source is validated before the SQLite transaction begins. Existing
    files are only read, and deterministic IDs make a repeated import a no-op.
    """

    claimed: dict[str, tuple[str, Path]] = {}
    for character_id in dict.fromkeys(character_ids):
        _bounded_text("character_id", character_id, MAX_ID_CHARS)
        path = Path(history_dir) / f"{sanitize_file_stem(character_id)}.jsonl"
        source_key = path.name.casefold()
        previous = claimed.get(source_key)
        if previous is not None and previous[0] != character_id:
            raise TimelineDataError("LEGACY_HISTORY_CHARACTER_COLLISION")
        claimed[source_key] = (character_id, path)

    discovered = _legacy_sources(Path(history_dir))
    unclaimed = set(discovered) - set(claimed)
    if unclaimed:
        raise TimelineDataError("LEGACY_HISTORY_CHARACTER_UNKNOWN")

    prepared: list[NewTimelineEntry] = []
    expected: dict[str, list[NewTimelineEntry]] = {}
    for source_key, (character_id, _claimed_path) in claimed.items():
        path = discovered.get(source_key)
        if path is None:
            continue
        source_entries = _load_legacy_entries(path)
        converted = _legacy_timeline_entries(character_id, source_entries)
        expected[character_id] = converted
        prepared.extend(converted)

    encoded = [_validated_row(entry) for entry in prepared]
    store.path.parent.mkdir(parents=True, exist_ok=True)
    with store._connect() as connection:
        _create_schema(connection)
        _assert_turn_ownership(connection, prepared)
        for entry, payload_json in encoded:
            connection.execute(
                """
                INSERT INTO timeline_entries (
                    entry_id, turn_id, character_id, kind, origin,
                    created_at, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(entry_id) DO NOTHING
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
        _verify_legacy_import(connection, expected)
        if int(connection.execute("PRAGMA application_id").fetchone()[0]) == 0:
            connection.execute(
                f"PRAGMA application_id = {secrets.randbelow(0x7FFFFFFE) + 1}"
            )
        connection.execute("PRAGMA user_version = 1")
    return len(prepared)


def _legacy_sources(history_dir: Path) -> dict[str, Path]:
    if not history_dir.is_dir():
        return {}
    sources: dict[str, Path] = {}
    try:
        children = list(history_dir.iterdir())
    except OSError as exc:
        raise TimelineDataError("LEGACY_HISTORY_READ_FAILED") from exc
    for child in children:
        lowered = child.name.casefold()
        if lowered.endswith(".jsonl"):
            base_name = child.name
        elif ".jsonl." in lowered and lowered.endswith(".archive"):
            marker = lowered.index(".jsonl.") + len(".jsonl")
            base_name = child.name[:marker]
        else:
            continue
        key = base_name.casefold()
        base_path = history_dir / base_name
        previous = sources.get(key)
        if previous is not None and previous.name != base_name:
            raise TimelineDataError("LEGACY_HISTORY_SOURCE_COLLISION")
        sources[key] = base_path
    return sources


def discover_legacy_character_ids(history_dir: Path) -> list[str]:
    return sorted(
        (path.name[: -len(".jsonl")] for path in _legacy_sources(Path(history_dir)).values()),
        key=str.casefold,
    )


def _load_legacy_entries(path: Path) -> list[ChatHistoryEntry]:
    if os.path.lexists(path) and (
        path.is_symlink()
        or getattr(path, "is_junction", lambda: False)()
        or not path.is_file()
    ):
        raise TimelineDataError("HISTORY_PATH_UNSAFE")
    legacy = ChatHistoryStore(path)
    legacy.assert_compatible_append()
    segments = sorted(path.parent.glob(f"{path.name}.*.archive"))
    if path.is_file():
        segments.append(path)
    entries: list[ChatHistoryEntry] = []
    for segment in segments:
        try:
            lines = segment.read_bytes().splitlines()
        except OSError as exc:
            raise TimelineDataError("HISTORY_READ_FAILED") from exc
        for raw_line in lines:
            try:
                data = json.loads(raw_line.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise TimelineDataError("HISTORY_DATA_INVALID") from exc
            if not isinstance(data, dict):
                raise TimelineDataError("HISTORY_DATA_INVALID")
            required = (data.get("created_at"), data.get("role"), data.get("content"))
            optional = (data.get("translation", ""), data.get("tone", ""), data.get("portrait", ""))
            if not all(isinstance(value, str) for value in (*required, *optional)):
                raise TimelineDataError("HISTORY_DATA_INVALID")
            entries.append(
                ChatHistoryEntry(
                    created_at=required[0],
                    role=required[1],
                    content=required[2],
                    translation=optional[0],
                    tone=optional[1],
                    portrait=optional[2],
                    entry_id=data.get("entry_id", "") if isinstance(data.get("entry_id", ""), str) else "",
                )
            )
    return entries


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


def _is_unsafe_resource_string(value: str) -> bool:
    cleaned = value.strip()
    windows_path = PureWindowsPath(cleaned)
    return (
        cleaned.lower().startswith("data:")
        or PurePosixPath(cleaned).is_absolute()
        or bool(windows_path.drive)
        or bool(windows_path.root)
    )


def _legacy_timeline_entries(
    character_id: str,
    entries: Sequence[ChatHistoryEntry],
) -> list[NewTimelineEntry]:
    result: list[NewTimelineEntry] = []
    current_turn = ""
    semantic_entries: list[tuple[int, ChatHistoryEntry]] = []
    for source_index, entry in enumerate(entries):
        if entry.role == "error":
            # Legacy chat history persisted provider/runtime failures as a
            # display-only role. They are not interaction facts and have no
            # typed Timeline equivalent.
            continue
        if entry.role not in {"user", "assistant", "system"}:
            raise TimelineDataError("LEGACY_HISTORY_ROLE_INVALID")
        semantic_entries.append((source_index, entry))

    position = 0
    while position < len(semantic_entries):
        source_index, entry = semantic_entries[position]
        if entry.role in {"user", "system"} or not current_turn:
            current_turn = _legacy_id(character_id, source_index, entry, "turn")

        if entry.role == "assistant":
            first_index = source_index
            first_entry = entry
            segments: list[dict[str, Any]] = []
            while (
                position < len(semantic_entries)
                and semantic_entries[position][1].role == "assistant"
            ):
                assistant = semantic_entries[position][1]
                segments.append(
                    {
                        "text": assistant.content,
                        "translation": assistant.translation,
                        "tone": assistant.tone,
                        "portrait": assistant.portrait,
                        "suppressTts": False,
                    }
                )
                position += 1
            result.append(
                NewTimelineEntry(
                    entry_id=_legacy_id(character_id, first_index, first_entry, "entry"),
                    turn_id=current_turn,
                    character_id=character_id,
                    kind=TimelineKind.ASSISTANT,
                    origin="legacy_chat",
                    created_at=first_entry.created_at,
                    payload={"segments": segments},
                )
            )
            continue

        kind = TimelineKind.HUMAN if entry.role == "user" else TimelineKind.SYSTEM
        result.append(
            NewTimelineEntry(
                entry_id=_legacy_id(character_id, source_index, entry, "entry"),
                turn_id=current_turn,
                character_id=character_id,
                kind=kind,
                origin="legacy_chat",
                created_at=entry.created_at,
                payload={"text": entry.content},
            )
        )
        position += 1
    return result


def _legacy_id(character_id: str, index: int, entry: ChatHistoryEntry, purpose: str) -> str:
    identity = json.dumps(
        [
            purpose,
            character_id,
            index,
            entry.created_at,
            entry.role,
            entry.content,
            entry.translation,
            entry.tone,
            entry.portrait,
        ],
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"legacy-{purpose}-{hashlib.sha256(identity).hexdigest()}"


def _verify_legacy_import(
    connection: sqlite3.Connection,
    expected: Mapping[str, Sequence[NewTimelineEntry]],
) -> None:
    for character_id, wanted in expected.items():
        rows = connection.execute(
            """
            SELECT seq, entry_id, turn_id, character_id, kind, origin,
                   created_at, payload_json
            FROM timeline_entries
            WHERE character_id = ? AND origin = 'legacy_chat'
            ORDER BY seq
            """,
            (character_id,),
        ).fetchall()
        actual = [_entry_from_row(row) for row in rows]
        expected_rows = [
            (
                entry.entry_id,
                entry.turn_id,
                entry.character_id,
                entry.kind,
                entry.origin,
                entry.created_at,
                dict(entry.payload),
            )
            for entry in wanted
        ]
        actual_rows = [
            (
                entry.entry_id,
                entry.turn_id,
                entry.character_id,
                entry.kind,
                entry.origin,
                entry.created_at,
                entry.payload,
            )
            for entry in actual
        ]
        if actual_rows != expected_rows:
            raise TimelineDataError("LEGACY_IMPORT_VERIFY_FAILED")


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
