from __future__ import annotations

import json
from pathlib import Path

from app.storage.chat_history import ChatHistoryStore


def test_entry_id_is_optional_and_round_trips(tmp_path: Path) -> None:
    path = tmp_path / "history.jsonl"
    store = ChatHistoryStore(path)
    store.append("user", "legacy compatible")
    store.append("assistant", "reply", entry_id="entry-0001")

    raw = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert "entry_id" not in raw[0]
    assert raw[1]["entry_id"] == "entry-0001"
    loaded = store.load()
    assert loaded[0].entry_id == ""
    assert loaded[1].entry_id == "entry-0001"


def test_unknown_or_invalid_entry_id_remains_legacy_compatible(tmp_path: Path) -> None:
    path = tmp_path / "history.jsonl"
    path.write_text(
        '{"created_at":"2026-01-01T00:00:00+00:00","role":"assistant",'
        '"content":"ok","entry_id":42,"future":"kept-on-disk"}\n',
        encoding="utf-8",
    )

    entry = ChatHistoryStore(path).load()[0]
    assert entry.content == "ok"
    assert entry.entry_id == ""
