from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from app.storage.atomic import atomic_write_text


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
        with self.path.open("a", encoding="utf-8") as file:
            file.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def load(self) -> list[ChatHistoryEntry]:
        if not self.path.exists():
            return []

        entries: list[ChatHistoryEntry] = []
        for raw_line in self.path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(data, dict):
                continue

            created_at = data.get("created_at")
            role = data.get("role")
            content = data.get("content")
            translation = data.get("translation", "")
            tone = data.get("tone", "")
            portrait = data.get("portrait", "")
            if not all(isinstance(value, str) for value in (created_at, role, content)):
                continue
            if not isinstance(translation, str):
                translation = ""
            if not isinstance(tone, str):
                tone = ""
            if not isinstance(portrait, str):
                portrait = ""
            entries.append(
                ChatHistoryEntry(
                    created_at=created_at,
                    role=role,
                    content=content,
                    translation=translation,
                    tone=tone,
                    portrait=portrait,
                )
            )
        return entries

    def clear(self) -> None:
        atomic_write_text(self.path, "", encoding="utf-8", backup=False)
