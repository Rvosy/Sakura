"""读取当前安装的旧记忆。原文件保留，迁移只向新的 SQLite 写入。"""

from __future__ import annotations

import json
import sqlite3
import tempfile
from contextlib import closing
from pathlib import Path
from typing import Any


def _legacy_points(directory: Path):
    """旧 Qdrant 配置字段随版本变化；迁移只读 SDK 持久化的记录。"""
    storage = directory / "qdrant/collection/sakura_memories/storage.sqlite"
    if not storage.exists():
        return
    from qdrant_client.local.persistence import CollectionPersistence

    with tempfile.TemporaryDirectory(prefix="sakura-memory-") as temporary:
        with closing(sqlite3.connect(storage.as_uri() + "?mode=ro", uri=True)) as source:
            with closing(sqlite3.connect(Path(temporary) / "storage.sqlite")) as target:
                source.backup(target)
        persistence = CollectionPersistence(temporary)
        try:
            yield from persistence.load()
        finally:
            persistence.close()


def normalize_existing_history_database(database: Path) -> None:
    """旧导入流程需要的列按需补齐，保留额外列和历史记录。"""
    columns = {
        "id": "TEXT", "memory_id": "TEXT", "old_memory": "TEXT", "new_memory": "TEXT",
        "event": "TEXT", "created_at": "TEXT", "updated_at": "TEXT",
        "is_deleted": "INTEGER DEFAULT 0", "actor_id": "TEXT", "role": "TEXT",
    }
    with closing(sqlite3.connect(database)) as db, db:
        db.execute("CREATE TABLE IF NOT EXISTS history (id TEXT PRIMARY KEY)")
        existing = {row[1] for row in db.execute("PRAGMA table_info(history)")}
        for name, definition in columns.items():
            if name not in existing:
                db.execute(f"ALTER TABLE history ADD COLUMN {name} {definition}")


def read_legacy(directory: Path) -> list[dict[str, Any]]:
    records: dict[tuple[str, str], dict[str, Any]] = {}
    for point in _legacy_points(directory):
        payload = dict(point.payload or {})
        scope = str(payload.get("scope") or payload.get("user_id") or "sakura")
        record = {**payload, "id": str(point.id), "scope": scope,
                  "content": payload.get("data") or payload.get("memory") or "",
                  "metadata": payload}
        if record["content"]:
            records[(scope, str(point.id))] = record
    indexed_keys = set(records)
    history = directory / "mem0_history.db"
    if history.exists():
        with closing(sqlite3.connect(f"{history.as_uri()}?mode=ro", uri=True)) as db:
            db.row_factory = sqlite3.Row
            columns = {row[1] for row in db.execute("PRAGMA table_info(history)")}
            if {"memory_id", "new_memory", "event"} <= columns:
                rows = [dict(row) for row in db.execute("SELECT * FROM history ORDER BY rowid")]
                for row in rows:
                    memory_id = str(row["memory_id"] or "")
                    deleted = str(row["event"]).upper() == "DELETE" or bool(int(row.get("is_deleted") or 0))
                    # history 没有角色字段时，不能把孤立文本猜成当前角色的数据。
                    scope = row.get("user_id") or row.get("scope") or row.get("actor_id")
                    matching = [key for key in records if key[1] == memory_id and (not scope or key[0] == scope)]
                    if not matching and (scope or deleted):
                        matching = [(str(scope or ""), memory_id)]
                    for key in matching:
                        text = row["new_memory"] or row.get("old_memory")
                        if key not in records and (text or deleted):
                            records[key] = {
                                "id": memory_id, "scope": key[0], "content": text or "",
                                "created_at": row.get("created_at") or "",
                                "updated_at": row.get("updated_at") or row.get("created_at") or "",
                                "source": "legacy",
                            }
                        if key in records:
                            records[key]["deleted"] = deleted
                            if key not in indexed_keys:
                                if text:
                                    records[key]["content"] = text
                                records[key]["updated_at"] = row.get("updated_at") or row.get("created_at") or ""
    profiles = directory / "core_profiles.json"
    if profiles.exists():
        for scope, raw in json.loads(profiles.read_text(encoding="utf-8")).items():
            if isinstance(raw, str):
                raw = {"content": raw}
            metadata = raw.get("metadata") or {}
            record = {**metadata, **raw, "scope": scope, "layer": "core_profile",
                      "id": raw.get("id") or f"core_profile:{scope}",
                      "content": raw.get("content") or raw.get("memory") or "",
                      "source": metadata.get("source") or raw.get("source") or "legacy"}
            if record["content"]:
                records[(scope, record["id"])] = record
    return list(records.values())
