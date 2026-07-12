from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterator

from app.storage.atomic import atomic_write_text


CHAT_HISTORY_SEGMENT_BYTES = 32 * 1024 * 1024


@dataclass(frozen=True)
class ChatHistoryEntry:
    created_at: str
    role: str
    content: str
    translation: str = ""
    tone: str = ""
    portrait: str = ""

    def display_content(self, subtitle_language: str) -> str:
        if self.role == "assistant" and subtitle_language == "zh" and self.translation.strip():
            return self.translation.strip()
        return self.content


class ChatHistoryStore:
    """按 JSONL 保存聊天历史，避免单条坏记录影响整体读取。"""

    def __init__(self, path: Path, assistant_name: str = "桜") -> None:
        self.path = path
        self.assistant_name = assistant_name

    def append(
        self,
        role: str,
        content: str,
        translation: str = "",
        tone: str = "",
        portrait: str = "",
        _debug: dict | None = None,
    ) -> None:
        entry = {
            "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "role": role,
            "content": content,
        }
        if translation.strip():
            entry["translation"] = translation.strip()
        if tone.strip():
            entry["tone"] = tone.strip()
        if portrait.strip():
            entry["portrait"] = portrait.strip()
        if _debug is not None:
            entry["_debug"] = _debug
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._rotate_if_needed()
        with self.path.open("a", encoding="utf-8") as file:
            file.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def load(self) -> list[ChatHistoryEntry]:
        return list(self.iter_entries())

    def iter_entries(self) -> Iterator[ChatHistoryEntry]:
        self._repair_truncated_tail()
        for segment in self._segments():
            try:
                with segment.open("rb") as handle:
                    for raw_line in handle:
                        entry = _entry_from_bytes(raw_line)
                        if entry is not None:
                            yield entry
            except OSError:
                continue

    def load_recent(self, limit: int) -> list[ChatHistoryEntry]:
        if limit <= 0:
            return []
        self._repair_truncated_tail()
        entries: list[ChatHistoryEntry] = []
        for segment in reversed(self._segments()):
            for raw_line in reversed(_read_lines_binary(segment)):
                entry = _entry_from_bytes(raw_line)
                if entry is not None:
                    entries.append(entry)
                    if len(entries) >= limit:
                        return list(reversed(entries))
        return list(reversed(entries))

    def total_count(self) -> int:
        return sum(1 for _entry in self.iter_entries())

    def clear(self) -> None:
        for segment in self._archive_segments():
            segment.unlink(missing_ok=True)
        atomic_write_text(self.path, "", encoding="utf-8", backup=False)

    def _segments(self) -> list[Path]:
        segments = self._archive_segments()
        if self.path.is_file():
            segments.append(self.path)
        return segments

    def _archive_segments(self) -> list[Path]:
        return sorted(self.path.parent.glob(f"{self.path.name}.*.archive"))

    def _rotate_if_needed(self) -> None:
        try:
            size = self.path.stat().st_size
        except OSError:
            return
        if size < CHAT_HISTORY_SEGMENT_BYTES:
            return
        stamp = datetime.now().astimezone().strftime("%Y%m%dT%H%M%S%f")
        archive = self.path.with_name(f"{self.path.name}.{stamp}.archive")
        self.path.replace(archive)

    def _repair_truncated_tail(self) -> None:
        if not self.path.is_file():
            return
        try:
            with self.path.open("rb") as handle:
                data = handle.read()
        except OSError:
            return
        if not data or data.endswith(b"\n"):
            return
        tail_start = data.rfind(b"\n") + 1
        tail = data[tail_start:]
        try:
            tail.decode("utf-8")
            json.loads(tail.decode("utf-8"))
            return
        except (UnicodeDecodeError, json.JSONDecodeError):
            pass
        stamp = datetime.now().astimezone().strftime("%Y%m%dT%H%M%S%f")
        backup = self.path.with_name(f"{self.path.name}.corrupt-{stamp}.bak")
        try:
            shutil.copy2(self.path, backup)
            with self.path.open("r+b") as handle:
                handle.truncate(tail_start)
        except OSError:
            return


def _entry_from_bytes(raw_line: bytes) -> ChatHistoryEntry | None:
    try:
        line = raw_line.decode("utf-8").strip()
    except UnicodeDecodeError:
        return None
    if not line:
        return None
    try:
        data = json.loads(line)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    created_at = data.get("created_at")
    role = data.get("role")
    content = data.get("content")
    if not all(isinstance(value, str) for value in (created_at, role, content)):
        return None
    translation = data.get("translation", "")
    tone = data.get("tone", "")
    portrait = data.get("portrait", "")
    return ChatHistoryEntry(
        created_at=created_at,
        role=role,
        content=content,
        translation=translation if isinstance(translation, str) else "",
        tone=tone if isinstance(tone, str) else "",
        portrait=portrait if isinstance(portrait, str) else "",
    )


def _read_lines_binary(path: Path, block_size: int = 64 * 1024) -> list[bytes]:
    try:
        with path.open("rb") as handle:
            handle.seek(0, 2)
            position = handle.tell()
            buffer = b""
            lines: list[bytes] = []
            while position > 0:
                size = min(block_size, position)
                position -= size
                handle.seek(position)
                buffer = handle.read(size) + buffer
                parts = buffer.splitlines(keepends=True)
                if position > 0 and parts:
                    buffer = parts.pop(0)
                else:
                    buffer = b""
                lines[:0] = parts
            if buffer:
                lines.insert(0, buffer)
            return lines
    except OSError:
        return []
