from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


@dataclass(frozen=True)
class ChatHistoryEntry:
    created_at: str
    role: str
    content: str


class ChatHistoryStore:
    """按 JSONL 保存聊天历史，避免单条坏记录影响整体读取。"""

    def __init__(self, path: Path) -> None:
        self.path = path

    def append(self, role: str, content: str) -> None:
        entry = {
            "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "role": role,
            "content": content,
        }
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
            if not all(isinstance(value, str) for value in (created_at, role, content)):
                continue
            entries.append(ChatHistoryEntry(created_at=created_at, role=role, content=content))
        return entries

    def clear(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text("", encoding="utf-8")
