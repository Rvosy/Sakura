from __future__ import annotations

import base64
import json
import re
import sqlite3
import uuid
from collections import defaultdict, deque
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator, TextIO

from app.storage.timeline import (
    MAX_SEGMENTS,
    NewTimelineEntry,
    TimelineDataError,
    TimelineKind,
    TimelineStore,
)

from .errors import LegacyImportError


_MANUAL_MARKER = re.compile(
    r"\[Sakura 已附加手动框选截图(?:，视觉记录\s+visual_id=([^\]\s]+))?\]"
)
_SCHEDULED_MARKER = re.compile(
    r"\[(?:Sakura 已自主观察屏幕|已抓取屏幕上下文)(?:，视觉记录\s+visual_id=([^\]\s]+))?\]"
)
_SAFE_RESOURCE = re.compile(r"^[^/\\:\x00-\x1f]*$")


@dataclass(frozen=True)
class HistoryImportStats:
    source_records: int
    timeline_entries: int
    errors_quarantined: int
    per_character_records: dict[str, int]
    cutoff_entry_ids: dict[str, str]


@dataclass(frozen=True)
class _SourceRecord:
    path: Path
    relative: str
    line: int
    ordinal: int
    identity: str
    timestamp: str
    raw: bytes
    value: dict[str, Any]


@dataclass(frozen=True)
class HistoryIssue:
    code: str
    relative: str
    line: int
    raw: bytes


class _HistoryQuarantineWriter:
    def __init__(self, staged_root: Path, import_id: str) -> None:
        self.target = (
            staged_root
            / "data"
            / "legacy-imports"
            / import_id
            / "quarantine"
            / "history-records.jsonl"
        )
        self.handle: TextIO | None = None
        self.count = 0

    def append(self, issue: HistoryIssue) -> None:
        if self.handle is None:
            self.target.parent.mkdir(parents=True, exist_ok=True)
            self.handle = self.target.open("w", encoding="utf-8", newline="\n")
        self.handle.write(
            json.dumps(
                {
                    "code": issue.code,
                    "relativePath": issue.relative,
                    "line": issue.line,
                    "rawBase64": base64.b64encode(issue.raw).decode("ascii"),
                },
                ensure_ascii=True,
                separators=(",", ":"),
            )
            + "\n"
        )
        self.count += 1

    def close(self) -> None:
        if self.handle is not None:
            self.handle.close()
            self.handle = None


class _TimelineWriter:
    def __init__(self, store: TimelineStore) -> None:
        self.store = store
        self.count = 0

    def append(self, entry: NewTimelineEntry, *, source: _SourceRecord) -> None:
        try:
            self.store.append(entry)
        except TimelineDataError as exc:
            raise LegacyImportError(
                str(exc), "staging", source.relative, source.line
            ) from exc
        self.count += 1


def import_history(
    source_root: Path,
    staged_root: Path,
    *,
    character_ids: tuple[str, ...],
    processed_counts: dict[str, int] | None = None,
    import_id: str = "history-import",
    identity_root: Path | None = None,
    identities: dict[tuple[str, str], str] | None = None,
) -> HistoryImportStats:
    history_root = source_root / "data" / "chat_history"
    timeline = TimelineStore(staged_root / "data" / "chat_history" / "timeline.sqlite3")
    timeline.initialize()
    try:
        ids = _HistoryIdentities(identity_root or staged_root, identities)
    except (sqlite3.Error, TimelineDataError) as exc:
        raise LegacyImportError("LEGACY_DATA_TARGET_TIMELINE_INVALID", "inspect") from exc
    writer = _TimelineWriter(timeline)
    visual = _load_visual_records(source_root / "data" / "visual_observations")
    source_records = 0
    quarantine = _HistoryQuarantineWriter(staged_root, import_id)
    per_character: dict[str, int] = {}
    cutoffs: dict[str, str] = {}
    try:
        for scope, paths in _history_groups(history_root, character_ids):
            scan = {"ordinal": 0}
            records = _iter_records(
                paths,
                source_root,
                scope=scope,
                scan=scan,
                quarantine=quarantine,
            )
            processed = max(0, int((processed_counts or {}).get(scope, 0)))
            current_turn = ""
            current_has_human = False
            current_scheduled = False
            assistant_buffer: list[_SourceRecord] = []

            def flush_assistant() -> None:
                nonlocal assistant_buffer
                if not assistant_buffer:
                    return
                # Keep only the current Runtime v2-sized reply chunk in memory.
                chunk = assistant_buffer
                first, last = chunk[0], chunk[-1]
                turn_id = current_turn or ids.get("turn", first)
                entry_id = ids.get("assistant", first)
                segments = [_segment(record, quarantine) for record in chunk]
                writer.append(
                    NewTimelineEntry(
                        entry_id=entry_id,
                        turn_id=turn_id,
                        character_id=scope,
                        kind=TimelineKind.ASSISTANT,
                        origin="proactive" if current_scheduled and not current_has_human else "chat",
                        created_at=first.timestamp,
                        payload={"segments": segments},
                    ),
                    source=first,
                )
                if last.ordinal <= processed:
                    cutoffs[scope] = entry_id
                assistant_buffer = []

            for record in records:
                role = record.value["role"]
                if role == "assistant":
                    assistant_buffer.append(record)
                    if len(assistant_buffer) == MAX_SEGMENTS:
                        flush_assistant()
                    continue
                flush_assistant()
                if role == "error":
                    quarantine.append(
                        HistoryIssue(
                            "LEGACY_HISTORY_ERROR_RECORD",
                            record.relative,
                            record.line,
                            record.raw,
                        )
                    )
                    continue
                content = str(record.value["content"])
                if role == "user":
                    current_turn = ids.get("turn", record)
                    current_has_human = True
                    current_scheduled = False
                    visual_id, cleaned = _strip_marker(content, _MANUAL_MARKER)
                    writer.append(
                        NewTimelineEntry(
                            entry_id=ids.get("human", record),
                            turn_id=current_turn,
                            character_id=scope,
                            kind=TimelineKind.HUMAN,
                            origin="chat",
                            created_at=record.timestamp,
                            payload={"text": cleaned},
                        ),
                        source=record,
                    )
                    if record.ordinal <= processed:
                        cutoffs[scope] = ids.get("human", record)
                    if cleaned != content:
                        observation_id = ids.get("observation", record)
                        writer.append(
                            _observation_entry(
                                record,
                                scope=scope,
                                turn_id=current_turn,
                                entry_id=observation_id,
                                origin="manual_screen",
                                visual_id=visual_id,
                                visual=visual,
                            ),
                            source=record,
                        )
                        if record.ordinal <= processed:
                            cutoffs[scope] = observation_id
                    continue
                if role == "system":
                    scheduled_id, cleaned = _strip_marker(content, _SCHEDULED_MARKER)
                    if cleaned != content:
                        current_turn = ids.get("turn", record)
                        current_has_human = False
                        current_scheduled = True
                        entry_id = ids.get("observation", record)
                        writer.append(
                            _observation_entry(
                                record,
                                scope=scope,
                                turn_id=current_turn,
                                entry_id=entry_id,
                                origin="scheduled_screen",
                                visual_id=scheduled_id,
                                visual=visual,
                            ),
                            source=record,
                        )
                    else:
                        current_turn = current_turn or ids.get("turn", record)
                        entry_id = ids.get("system", record)
                        writer.append(
                            NewTimelineEntry(
                                entry_id=entry_id,
                                turn_id=current_turn,
                                character_id=scope,
                                kind=TimelineKind.SYSTEM,
                                origin="host",
                                created_at=record.timestamp,
                                payload={"text": content, "eventType": "legacy_system"},
                            ),
                            source=record,
                        )
                    if record.ordinal <= processed:
                        cutoffs[scope] = entry_id
                    continue
                quarantine.append(
                    HistoryIssue(
                        "LEGACY_HISTORY_ROLE_UNSUPPORTED",
                        record.relative,
                        record.line,
                        record.raw,
                    )
                )
            flush_assistant()
            group_records = scan["ordinal"]
            source_records += group_records
            per_character[scope] = group_records

        with closing(sqlite3.connect(timeline.path)) as connection:
            write_history_identities(connection, ids.values)
            connection.commit()
        timeline.assert_activated()
    finally:
        quarantine.close()
    return HistoryImportStats(
        source_records=source_records,
        timeline_entries=writer.count,
        errors_quarantined=quarantine.count,
        per_character_records=per_character,
        cutoff_entry_ids=cutoffs,
    )


def _history_groups(root: Path, character_ids: tuple[str, ...]) -> list[tuple[str, list[Path]]]:
    if not root.is_dir():
        return []
    bases: dict[str, list[Path]] = {}
    for path in root.iterdir():
        if not path.is_file() or ".jsonl" not in path.name:
            continue
        raw_scope = path.name.split(".jsonl", 1)[0]
        bases.setdefault(raw_scope, []).append(path)
    folded: dict[str, list[str]] = {}
    for character_id in character_ids:
        folded.setdefault(character_id.casefold(), []).append(character_id)
    result: list[tuple[str, list[Path]]] = []
    for raw_scope, paths in sorted(bases.items(), key=lambda item: item[0].casefold()):
        if raw_scope in character_ids:
            scope = raw_scope
        else:
            matches = folded.get(raw_scope.casefold(), [])
            scope = matches[0] if len(matches) == 1 else raw_scope
        archives = sorted(
            (path for path in paths if path.name.endswith(".archive")),
            key=lambda path: path.name,
        )
        active = sorted(
            (path for path in paths if path.name.endswith(".jsonl")),
            key=lambda path: path.name,
        )
        result.append((scope, [*archives, *active]))
    return result


def _iter_records(
    paths: list[Path],
    source_root: Path,
    *,
    scope: str,
    scan: dict[str, int],
    quarantine: _HistoryQuarantineWriter,
) -> Iterator[_SourceRecord]:
    occurrences: dict[tuple[str, str], int] = {}
    for path in paths:
        relative = path.relative_to(source_root).as_posix()
        try:
            handle = path.open("rb")
        except OSError as exc:
            raise LegacyImportError("LEGACY_HISTORY_UNREADABLE", "staging", relative) from exc
        with handle:
            for line_number, raw_bytes in enumerate(handle, 1):
                if not raw_bytes.strip():
                    continue
                scan["ordinal"] += 1
                try:
                    raw = raw_bytes.decode("utf-8")
                    value = json.loads(raw)
                except (UnicodeError, json.JSONDecodeError):
                    quarantine.append(
                        HistoryIssue(
                            "LEGACY_HISTORY_JSON_INVALID", relative, line_number, raw_bytes
                        )
                    )
                    continue
                if not isinstance(value, dict) or not all(
                    isinstance(value.get(name), str)
                    for name in ("created_at", "role", "content")
                ):
                    quarantine.append(
                        HistoryIssue(
                            "LEGACY_HISTORY_RECORD_INVALID", relative, line_number, raw_bytes
                        )
                    )
                    continue
                timestamp = _parse_timestamp(str(value["created_at"]))
                if not timestamp:
                    quarantine.append(
                        HistoryIssue(
                            "LEGACY_HISTORY_TIMESTAMP_INVALID",
                            relative,
                            line_number,
                            raw_bytes,
                        )
                    )
                    continue
                role = str(value["role"])
                occurrence_key = (role, timestamp)
                occurrence = occurrences.get(occurrence_key, 0) + 1
                occurrences[occurrence_key] = occurrence
                identity = json.dumps(
                    [scope, role, timestamp, occurrence], ensure_ascii=True, separators=(",", ":")
                )
                yield _SourceRecord(
                    path,
                    relative,
                    line_number,
                    scan["ordinal"],
                    identity,
                    timestamp,
                    raw_bytes,
                    value,
                )


def _parse_timestamp(value: str) -> str:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return ""
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return ""
    return parsed.isoformat(timespec="seconds")


def _segment(
    record: _SourceRecord, quarantine: _HistoryQuarantineWriter
) -> dict[str, object]:
    def text(name: str) -> str:
        value = record.value.get(name, "")
        if not isinstance(value, str):
            quarantine.append(
                HistoryIssue(
                    "LEGACY_HISTORY_SEGMENT_INVALID",
                    record.relative,
                    record.line,
                    record.raw,
                )
            )
            return ""
        return value

    portrait = text("portrait")
    if portrait and not _SAFE_RESOURCE.fullmatch(portrait):
        quarantine.append(
            HistoryIssue(
                "LEGACY_HISTORY_PORTRAIT_UNSAFE",
                record.relative,
                record.line,
                record.raw,
            )
        )
        portrait = ""
    return {
        "text": text("content"),
        "translation": text("translation"),
        "tone": text("tone"),
        "portrait": portrait,
        "suppressTts": False,
    }


def _strip_marker(content: str, pattern: re.Pattern[str]) -> tuple[str, str]:
    match = pattern.search(content)
    visual_id = match.group(1) if match and match.lastindex else ""
    return visual_id or "", pattern.sub("", content).strip()


def _observation_entry(
    record: _SourceRecord,
    *,
    scope: str,
    turn_id: str,
    entry_id: str,
    origin: str,
    visual_id: str,
    visual: dict[str, dict[str, object]],
) -> NewTimelineEntry:
    detail = visual.get(visual_id, {}) if visual_id else {}
    summary = detail.get("summary")
    text = summary if isinstance(summary, str) and summary.strip() else "旧版本屏幕观察记录"
    metadata: dict[str, object] = {"analysisStatus": "succeeded", "sensitiveRedacted": True}
    if visual_id and re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", visual_id):
        metadata["visualId"] = visual_id
    confidence = detail.get("confidence")
    if isinstance(confidence, (int, float)) and not isinstance(confidence, bool) and 0 <= float(confidence) <= 1:
        metadata["confidence"] = float(confidence)
    return NewTimelineEntry(
        entry_id=entry_id,
        turn_id=turn_id,
        character_id=scope,
        kind=TimelineKind.OBSERVATION,
        origin=origin,
        created_at=record.timestamp,
        payload={"text": text, "visual": metadata},
    )


def _load_visual_records(root: Path) -> dict[str, dict[str, object]]:
    records: dict[str, dict[str, object]] = {}
    if not root.is_dir():
        return records
    for path in sorted(root.glob("*.jsonl")):
        try:
            handle = path.open("rb")
        except OSError:
            continue
        with handle:
            for raw in handle:
                try:
                    value = json.loads(raw.decode("utf-8"))
                except (UnicodeError, json.JSONDecodeError):
                    continue
                if isinstance(value, dict) and isinstance(value.get("id"), str):
                    records[value["id"]] = value
    return records


def read_history_identities(root: Path) -> dict[tuple[str, str], str]:
    path = root / "data/chat_history/timeline.sqlite3"
    if not path.is_file():
        return {}
    with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)) as connection:
        exists = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='legacy_history_identities'"
        ).fetchone()
        if not exists:
            return {}
        return {
            (identity, kind): item_id
            for identity, kind, item_id in connection.execute(
                "SELECT source_identity, kind, item_id FROM legacy_history_identities"
            )
        }


def write_history_identities(
    connection: sqlite3.Connection, identities: dict[tuple[str, str], str]
) -> None:
    connection.execute(
        """CREATE TABLE IF NOT EXISTS legacy_history_identities (
            source_identity TEXT NOT NULL,
            kind TEXT NOT NULL,
            item_id TEXT NOT NULL,
            PRIMARY KEY (source_identity, kind)
        )"""
    )
    connection.executemany(
        "INSERT OR REPLACE INTO legacy_history_identities VALUES (?, ?, ?)",
        ((identity, kind, item_id) for (identity, kind), item_id in identities.items()),
    )


class _HistoryIdentities:
    """Persist source identities separately from opaque Timeline IDs.

    Pre-registry imports retain their existing IDs. Their available identity is
    character/kind/timestamp plus occurrence in Timeline order, so bootstrap from
    those columns without recomputing the old digest or comparing edited content.
    """

    def __init__(
        self, root: Path, pending: dict[tuple[str, str], str] | None
    ) -> None:
        self.values = dict(pending or {})
        # Committed identities win over a preview if another import has completed.
        self.values.update(read_history_identities(root))
        self.legacy: dict[tuple[str, str, str], deque[tuple[str, str]]] = defaultdict(deque)
        self.matched: dict[tuple[str, str], tuple[str, str] | None] = {}
        path = root / "data/chat_history/timeline.sqlite3"
        if path.is_file():
            assigned = set(self.values.values())
            with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)) as connection:
                for entry_id, turn_id, scope, kind, timestamp in connection.execute(
                    "SELECT entry_id, turn_id, character_id, kind, created_at "
                    "FROM timeline_entries ORDER BY seq"
                ):
                    if entry_id not in assigned and re.fullmatch(
                        r"legacy-(?:human|assistant|system|observation)-[0-9a-f]{32}", entry_id
                    ):
                        self.legacy[(scope, kind, timestamp)].append((entry_id, turn_id))

    def _existing(self, kind: str, record: _SourceRecord) -> tuple[str, str] | None:
        key = (record.identity, kind)
        if key not in self.matched:
            scope = json.loads(record.identity)[0]
            candidates = self.legacy[(scope, kind, record.timestamp)]
            self.matched[key] = candidates.popleft() if candidates else None
        return self.matched[key]

    def get(self, kind: str, record: _SourceRecord) -> str:
        key = (record.identity, kind)
        if key not in self.values:
            entry_kind = kind
            if kind == "turn":
                role = record.value["role"]
                entry_kind = "human" if role == "user" else role
                if role == "system" and _SCHEDULED_MARKER.search(record.value["content"]):
                    entry_kind = "observation"
            existing = self._existing(entry_kind, record)
            self.values[key] = (
                existing[1 if kind == "turn" else 0]
                if existing else f"legacy-{kind}-{uuid.uuid4().hex}"
            )
        return self.values[key]
