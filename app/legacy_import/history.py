from __future__ import annotations

import hashlib
import json
import re
import base64
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

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
) -> HistoryImportStats:
    history_root = source_root / "data" / "chat_history"
    timeline = TimelineStore(staged_root / "data" / "chat_history" / "timeline.sqlite3")
    timeline.initialize()
    writer = _TimelineWriter(timeline)
    visual = _load_visual_records(source_root / "data" / "visual_observations")
    source_records = 0
    issues: list[HistoryIssue] = []
    per_character: dict[str, int] = {}
    cutoffs: dict[str, str] = {}

    for scope, paths in _history_groups(history_root, character_ids):
        records, group_records, group_issues = _read_records(
            paths, source_root, scope=scope
        )
        source_records += group_records
        issues.extend(group_issues)
        per_character[scope] = group_records
        processed = max(0, int((processed_counts or {}).get(scope, 0)))
        current_turn = ""
        current_has_human = False
        current_scheduled = False
        assistant_buffer: list[_SourceRecord] = []

        def flush_assistant() -> None:
            nonlocal assistant_buffer
            if not assistant_buffer:
                return
            # Released 0.9 builds did not enforce Runtime v2's segment cap.
            # Preserve every usable segment by splitting oversized replies.
            for offset in range(0, len(assistant_buffer), MAX_SEGMENTS):
                chunk = assistant_buffer[offset : offset + MAX_SEGMENTS]
                first, last = chunk[0], chunk[-1]
                turn_id = current_turn or _stable_id("turn", first)
                entry_id = _stable_group_id("assistant", first, last)
                segments = [_segment(record, issues) for record in chunk]
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
                continue
            flush_assistant()
            if role == "error":
                issues.append(
                    HistoryIssue(
                        "LEGACY_HISTORY_ERROR_RECORD", record.relative, record.line, record.raw
                    )
                )
                continue
            content = str(record.value["content"])
            if role == "user":
                current_turn = _stable_id("turn", record)
                current_has_human = True
                current_scheduled = False
                visual_id, cleaned = _strip_marker(content, _MANUAL_MARKER)
                writer.append(
                    NewTimelineEntry(
                        entry_id=_stable_id("human", record),
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
                    cutoffs[scope] = _stable_id("human", record)
                if cleaned != content:
                    observation_id = _stable_id("observation", record)
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
                    current_turn = _stable_id("turn", record)
                    current_has_human = False
                    current_scheduled = True
                    entry_id = _stable_id("observation", record)
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
                    current_turn = current_turn or _stable_id("turn", record)
                    entry_id = _stable_id("system", record)
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
            issues.append(
                HistoryIssue(
                    "LEGACY_HISTORY_ROLE_UNSUPPORTED",
                    record.relative,
                    record.line,
                    record.raw,
                )
            )
        flush_assistant()

    timeline.assert_activated()
    _write_history_quarantine(staged_root, import_id, issues)
    return HistoryImportStats(
        source_records=source_records,
        timeline_entries=writer.count,
        errors_quarantined=len(issues),
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


def _read_records(
    paths: list[Path], source_root: Path, *, scope: str
) -> tuple[list[_SourceRecord], int, list[HistoryIssue]]:
    ordinal = 0
    records: list[_SourceRecord] = []
    issues: list[HistoryIssue] = []
    occurrences: dict[tuple[str, str], int] = {}
    for path in paths:
        relative = path.relative_to(source_root).as_posix()
        try:
            raw_lines = path.read_bytes().splitlines(keepends=True)
        except OSError as exc:
            raise LegacyImportError("LEGACY_HISTORY_UNREADABLE", "staging", relative) from exc
        for line_number, raw_bytes in enumerate(raw_lines, 1):
            if not raw_bytes.strip():
                continue
            ordinal += 1
            try:
                raw = raw_bytes.decode("utf-8")
                value = json.loads(raw)
            except (UnicodeError, json.JSONDecodeError):
                issues.append(
                    HistoryIssue(
                        "LEGACY_HISTORY_JSON_INVALID", relative, line_number, raw_bytes
                    )
                )
                continue
            if not isinstance(value, dict) or not all(
                isinstance(value.get(name), str)
                for name in ("created_at", "role", "content")
            ):
                issues.append(
                    HistoryIssue(
                        "LEGACY_HISTORY_RECORD_INVALID", relative, line_number, raw_bytes
                    )
                )
                continue
            timestamp = _parse_timestamp(str(value["created_at"]))
            if not timestamp:
                issues.append(
                    HistoryIssue(
                        "LEGACY_HISTORY_TIMESTAMP_INVALID", relative, line_number, raw_bytes
                    )
                )
                continue
            role = str(value["role"])
            occurrence_key = (role, timestamp)
            occurrence = occurrences.get(occurrence_key, 0) + 1
            occurrences[occurrence_key] = occurrence
            identity_seed = f"{scope}\0{role}\0{timestamp}\0{occurrence}".encode()
            identity = hashlib.sha256(identity_seed).hexdigest()
            records.append(
                _SourceRecord(
                    path,
                    relative,
                    line_number,
                    ordinal,
                    identity,
                    timestamp,
                    raw_bytes,
                    value,
                )
            )
    return records, ordinal, issues


def _parse_timestamp(value: str) -> str:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return ""
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return ""
    return parsed.isoformat(timespec="seconds")


def _segment(record: _SourceRecord, issues: list[HistoryIssue]) -> dict[str, object]:
    def text(name: str) -> str:
        value = record.value.get(name, "")
        if not isinstance(value, str):
            issues.append(
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
        issues.append(
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
            lines = path.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeError):
            continue
        for raw in lines:
            try:
                value = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict) and isinstance(value.get("id"), str):
                records[value["id"]] = value
    return records


def _stable_id(kind: str, record: _SourceRecord) -> str:
    seed = f"{record.identity}\0{kind}".encode()
    return f"legacy-{kind}-{hashlib.sha256(seed).hexdigest()[:32]}"


def _stable_group_id(kind: str, first: _SourceRecord, last: _SourceRecord) -> str:
    # The first segment owns the reply identity. Appending a late segment to
    # the active JSONL must update one conflict candidate, not manufacture a
    # new reply and leave the earlier partial reply duplicated.
    del last
    seed = f"{first.identity}\0{kind}".encode()
    return f"legacy-{kind}-{hashlib.sha256(seed).hexdigest()[:32]}"


def _write_history_quarantine(
    staged_root: Path, import_id: str, issues: list[HistoryIssue]
) -> None:
    if not issues:
        return
    target = (
        staged_root
        / "data"
        / "legacy-imports"
        / import_id
        / "quarantine"
        / "history-records.jsonl"
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", encoding="utf-8", newline="\n") as handle:
        for issue in issues:
            handle.write(
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
