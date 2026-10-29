from __future__ import annotations

import json
import re
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    from . import model_assets
    from .lexical import index_text, match_query, tokens
    from .legacy import read_legacy, normalize_existing_history_database
    from .model_assets import (DEFAULT_EMBEDDING_MODEL, DEFAULT_EMBEDDING_DIMS,
        DEFAULT_EMBEDDING_MODEL_CACHE_NAME, MemoryModelImportError, MemoryModelTaskCancelled,
        _embedding_model_cached, _embedding_model_snapshot, _validate_fastembed_snapshot_artifacts,
        download_embedding_model)
    from .support import StoragePaths, log_event
    from .vector_index import VectorIndex
except ImportError:
    import model_assets
    from lexical import index_text, match_query, tokens
    from legacy import read_legacy, normalize_existing_history_database
    from model_assets import (DEFAULT_EMBEDDING_MODEL, DEFAULT_EMBEDDING_DIMS,
        DEFAULT_EMBEDDING_MODEL_CACHE_NAME, MemoryModelImportError, MemoryModelTaskCancelled,
        _embedding_model_cached, _embedding_model_snapshot, _validate_fastembed_snapshot_artifacts,
        download_embedding_model)
    from support import StoragePaths, log_event
    from vector_index import VectorIndex

DEFAULT_MEMORY_SCOPE = "sakura"
DEFAULT_MEMORY_LIMIT = 20
MEMORY_LAYER_CORE_PROFILE = "core_profile"
MEMORY_LAYER_SEMANTIC = "semantic"
MEMORY_LAYER_EPISODIC = "episodic"
MEMORY_LAYER_PROCEDURAL = "procedural"
MEMORY_LAYER_SESSION = "session"
DEFAULT_MEMORY_LAYER = MEMORY_LAYER_SEMANTIC
MEMORY_LAYERS = ("core_profile", "semantic", "episodic", "procedural", "session")
MEMORY_LAYER_LABELS = dict(zip(MEMORY_LAYERS, ("常驻档案", "长期事实", "事件总结", "协作规则", "当前任务")))
DEFAULT_MEMORY_IMPORTANCE = 0.5
DEFAULT_MEMORY_CONFIDENCE = 0.75
DEFAULT_MEMORY_SOURCE = "manual"
DENSE_THRESHOLD = 0.48
DENSE_SCORE_WINDOW = 0.06
KNOWLEDGE_FIELDS = ("subject", "subject_kind", "predicate", "target", "target_kind")
ENTITY_KINDS = ("person", "project", "place", "thing", "event", "topic")


def memory_text(record: dict) -> str:
    """检索和聊天共用实体、属性或关系的完整表达。"""
    subject = record.get("subject", "")
    predicate = record.get("predicate", "")
    target = record.get("target", "")
    heading = " · ".join(value for value in (subject, predicate, target) if value)
    return f"{heading}：{record['content']}" if heading else record["content"]


class MemoryRuntimeUnavailableError(RuntimeError):
    pass


def append_memory_initialization_diagnostic(base_dir=None, **fields):
    log_event("Memory", "记忆存储状态", fields, event="memory.initialization", severity="debug")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _time(value: str) -> float:
    return datetime.fromisoformat(value).timestamp()


@dataclass(frozen=True)
class MemoryRecord:
    """管理、整理和数据交换共用的记忆记录。"""

    id: str
    content: str
    layer: str = DEFAULT_MEMORY_LAYER
    category: str = ""
    importance: float = DEFAULT_MEMORY_IMPORTANCE
    confidence: float = DEFAULT_MEMORY_CONFIDENCE
    source: str = DEFAULT_MEMORY_SOURCE
    scope: str = DEFAULT_MEMORY_SCOPE
    created_at: str = ""
    updated_at: str = ""
    last_accessed_at: str = ""
    score: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        metadata = dict(self.metadata)
        for key in (
            "layer",
            "category",
            "importance",
            "confidence",
            "source",
            "scope",
            "created_at",
            "updated_at",
            "last_accessed_at",
        ):
            metadata[key] = getattr(self, key)
        return {
            "id": self.id,
            "content": self.content,
            "memory": self.content,
            "layer": self.layer,
            "category": self.category,
            "importance": self.importance,
            "confidence": self.confidence,
            "source": self.source,
            "scope": self.scope,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "last_accessed_at": self.last_accessed_at,
            "score": self.score,
            "metadata": metadata,
        }


def looks_like_sensitive_memory(content: str) -> bool:
    """粗粒度识别不应自动进入长期记忆的敏感凭据和身份信息。"""

    text = content.strip()
    regexes = (
        r"(?i)(?:password|passwd|api[_ ]?key|secret|token|密码|密钥)\s*(?:是|为|[:=：])\s*\S{6,}",
        r"\bsk-[A-Za-z0-9_-]{16,}\b",
        r"\b[A-Za-z0-9_=-]{32,}\.[A-Za-z0-9_=-]{16,}\.[A-Za-z0-9_=-]{16,}\b",
        r"\b\d{15}(\d{2}[0-9Xx])?\b",
        r"\b(?:\d[ -]*?){13,19}\b",
    )
    return any(re.search(pattern, text) for pattern in regexes)


class MemoryStore:
    """实体、属性和关系保存当前记忆，检索索引从这些记录生成。"""

    def __init__(
        self, base_dir: Path | None = None, scope_id: str = DEFAULT_MEMORY_SCOPE,
        *, memory_dir: Path | None = None, memory_cache_dir: Path | None = None,
    ) -> None:
        self.base_dir = Path(base_dir or ".").resolve()
        paths = StoragePaths(self.base_dir)
        self.scope_id = scope_id
        self.memory_dir = Path(memory_dir or paths.memory_dir).resolve()
        self.memory_cache_dir = Path(memory_cache_dir or paths.memory_cache_dir).resolve()
        self.memory_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._transaction_depth = 0
        self._closed = False
        self._db = sqlite3.connect(self.memory_dir / "memory.sqlite3", check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.executescript("""
            CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts USING fts5(
                scope UNINDEXED, id UNINDEXED, words, tokenize='unicode61'
            );
            CREATE TABLE IF NOT EXISTS revisions (
                scope TEXT NOT NULL, id TEXT NOT NULL, revision INTEGER NOT NULL,
                document TEXT NOT NULL, PRIMARY KEY (scope, id, revision)
            );
            CREATE TABLE IF NOT EXISTS evidence (
                scope TEXT NOT NULL, memory_id TEXT NOT NULL, entry_id TEXT NOT NULL,
                document TEXT NOT NULL, PRIMARY KEY (scope, memory_id, entry_id)
            );
            CREATE TABLE IF NOT EXISTS processed_entries (
                scope TEXT NOT NULL, entry_id TEXT NOT NULL, PRIMARY KEY (scope, entry_id)
            );
            CREATE TABLE IF NOT EXISTS state (
                scope TEXT NOT NULL, key TEXT NOT NULL, value TEXT NOT NULL, PRIMARY KEY (scope, key)
            );
        """)
        self._index = VectorIndex(self)
        try:
            self._initialize_knowledge()
            self._migrate()
        except Exception:
            self._db.close()
            raise

    def _initialize_knowledge(self) -> None:
        # 三张业务表保存当前事实；只读视图供检索、证据和数据交换共用。
        with self.transaction():
            self._db.execute("""CREATE TABLE IF NOT EXISTS entities (
                scope TEXT NOT NULL, id TEXT NOT NULL, name TEXT NOT NULL, kind TEXT NOT NULL,
                PRIMARY KEY(scope,id), UNIQUE(scope,name,kind)
            )""")
            columns = """scope TEXT NOT NULL, id TEXT NOT NULL, document TEXT NOT NULL,
                content TEXT NOT NULL, layer TEXT NOT NULL, revision INTEGER NOT NULL,
                deleted INTEGER NOT NULL DEFAULT 0, point_id TEXT NOT NULL,
                vector_revision INTEGER NOT NULL DEFAULT 0, entity_id TEXT NOT NULL,
                predicate TEXT NOT NULL"""
            self._db.execute(f"CREATE TABLE IF NOT EXISTS entity_attrs ({columns}, PRIMARY KEY(scope,id))")
            self._db.execute(f"""CREATE TABLE IF NOT EXISTS entity_relations (
                {columns}, target_id TEXT NOT NULL, PRIMARY KEY(scope,id))""")
            old = self._db.execute("SELECT type FROM sqlite_master WHERE name='memories'").fetchone()
            if old and old["type"] == "table":
                for row in self._db.execute("SELECT * FROM memories").fetchall():
                    record = json.loads(row["document"])
                    record.update(scope=row["scope"], id=row["id"], revision=row["revision"], deleted=bool(row["deleted"]))
                    self._write_knowledge(record, point_id=row["point_id"])
                self._db.execute("DROP TABLE memories")
            self._db.execute("""CREATE VIEW IF NOT EXISTS memories AS
                SELECT a.*, '' AS target_id, e.name AS subject, e.kind AS subject_kind,
                       '' AS target, '' AS target_kind
                FROM entity_attrs a JOIN entities e ON e.scope=a.scope AND e.id=a.entity_id
                UNION ALL
                SELECT r.*, e.name AS subject, e.kind AS subject_kind, t.name AS target, t.kind AS target_kind
                FROM entity_relations r JOIN entities e ON e.scope=r.scope AND e.id=r.entity_id
                JOIN entities t ON t.scope=r.scope AND t.id=r.target_id
            """)
            if old and old["type"] == "table":
                self._db.execute("DELETE FROM memory_fts")
                for row in self._db.execute("SELECT * FROM memories WHERE deleted=0").fetchall():
                    self._db.execute("INSERT INTO memory_fts VALUES (?,?,?)",
                                     (row["scope"], row["id"], index_text(memory_text(dict(row)))))

    def _entity(self, scope: str, name: str, kind: str) -> str:
        row = self._db.execute("SELECT id FROM entities WHERE scope=? AND name=? AND kind=?",
                               (scope, name, kind)).fetchone()
        if row:
            return row["id"]
        entity_id = str(uuid.uuid4())
        self._db.execute("INSERT INTO entities VALUES (?,?,?,?)", (scope, entity_id, name, kind))
        return entity_id

    def _write_knowledge(self, record: dict, *, point_id: str) -> None:
        layer = record["layer"]
        values = {**record.get("metadata", {}), **record}
        defaults = {"core_profile": ("用户", "person"), "procedural": ("用户", "person"),
                    "episodic": ("共同经历", "event"), "session": ("当前事项", "topic")}
        subject, kind = defaults.get(layer, ("未分类", "topic"))
        record.update({
            "subject": str(values.get("subject") or subject).strip(),
            "subject_kind": str(values.get("subject_kind") or kind),
            "predicate": str(values.get("predicate") or values.get("category") or MEMORY_LAYER_LABELS[layer]).strip(),
            "target": str(values.get("target") or "").strip(),
            "target_kind": str(values.get("target_kind") or "person") if values.get("target") else "",
        })
        for key in ("subject_kind", "target_kind"):
            if record[key] and record[key] not in ENTITY_KINDS:
                raise ValueError("实体类型无效。")
        record["metadata"].update({key: record[key] for key in KNOWLEDGE_FIELDS})
        scope, memory_id = record["scope"], record["id"]
        entity_id = self._entity(scope, record["subject"], record["subject_kind"])
        table = "entity_relations" if record["target"] else "entity_attrs"
        other = "entity_attrs" if record["target"] else "entity_relations"
        self._db.execute(f"DELETE FROM {other} WHERE scope=? AND id=?", (scope, memory_id))
        fields = [scope, memory_id, json.dumps(record, ensure_ascii=False), record["content"], layer,
                  record["revision"], int(record["deleted"]), point_id, 0, entity_id, record["predicate"]]
        if record["target"]:
            fields.append(self._entity(scope, record["target"], record["target_kind"]))
        self._db.execute(f"INSERT OR REPLACE INTO {table} VALUES ({','.join('?' for _ in fields)})", fields)

    @contextmanager
    def transaction(self):
        with self._lock:
            if self._closed:
                raise MemoryRuntimeUnavailableError("记忆存储已关闭。")
            self._transaction_depth += 1
            try:
                if self._transaction_depth == 1:
                    with self._db:
                        yield self._db
                else:
                    yield self._db
            finally:
                self._transaction_depth -= 1

    def _migrate(self) -> None:
        if self.get_state("legacy_migrated", scope=""):
            return
        records = read_legacy(self.memory_dir)
        with self.transaction():
            for raw in records:
                self._save(raw, scope=str(raw.get("scope", DEFAULT_MEMORY_SCOPE)), preserve=True)
            for path in (self.memory_dir / "curation_state").glob("*.json"):
                self._set_state("curation", json.loads(path.read_text(encoding="utf-8")), scope=path.stem)
            self._set_state("legacy_migrated", True, scope="")
        if records:
            log_event("Memory", "旧记忆已迁移，原文件保留",
                      {"count": len(records)}, event="memory.migration.completed")

    def _record(self, row) -> dict[str, Any] | None:
        if row is None:
            return None
        record = json.loads(row["document"])
        record["revision"] = row["revision"]
        record["deleted"] = bool(row["deleted"])
        record.update({key: row[key] for key in KNOWLEDGE_FIELDS})
        record["evidence"] = [
            json.loads(item["document"]) for item in self._db.execute(
                "SELECT document FROM evidence WHERE scope=? AND memory_id=?", (row["scope"], row["id"]),
            )
        ]
        record["metadata"].update({key: record[key] for key in
                                   ("revision", "deleted", "evidence", "state", "pinned", "occurred_at", *KNOWLEDGE_FIELDS)})
        return record

    def get(self, memory_id: str, *, include_deleted: bool = False) -> dict | None:
        with self.transaction():
            row = self._db.execute("SELECT * FROM memories WHERE scope=? AND id=?",
                                   (self.scope_id, memory_id)).fetchone()
            if row is not None and (include_deleted or not row["deleted"]):
                return self._record(row)
            return None

    def _save(self, raw: dict, *, scope: str | None = None, preserve: bool = False) -> dict:
        scope = self.scope_id if scope is None else scope
        memory_id = str(raw.get("id") or uuid.uuid4())
        previous_row = self._db.execute("SELECT * FROM memories WHERE scope=? AND id=?", (scope, memory_id)).fetchone()
        previous = self._record(previous_row) or {}
        if previous_row:
            self._db.execute("INSERT OR IGNORE INTO revisions VALUES (?,?,?,?)",
                             (scope, memory_id, previous_row["revision"], json.dumps(previous, ensure_ascii=False)))
        metadata = {**previous.get("metadata", {}), **raw.get("metadata", {})}
        values = {**previous, **metadata, **raw}
        now = _now_iso()
        record = MemoryRecord(
            id=memory_id, content=str(values.get("content") or values.get("memory") or ""),
            scope=scope, layer=values.get("layer", DEFAULT_MEMORY_LAYER),
            category=values.get("category", ""), source=values.get("source", "legacy" if preserve else "manual"),
            importance=float(values.get("importance", DEFAULT_MEMORY_IMPORTANCE)),
            confidence=float(values.get("confidence", DEFAULT_MEMORY_CONFIDENCE)),
            created_at=values.get("created_at") or now,
            updated_at=(values.get("updated_at") or now) if preserve else now,
            last_accessed_at=values.get("last_accessed_at", ""), metadata=metadata,
        ).to_dict()
        revision = (previous_row["revision"] if previous_row else 0) + 1
        record.update({
            "revision": revision, "deleted": bool(values.get("deleted", False)),
            "state": values.get("state", "active"), "pinned": bool(values.get("pinned", False)),
            "occurred_at": values.get("occurred_at") or values.get("created_at") or now,
            "evidence": values.get("evidence", []),
        })
        for key in ("source_entry_ids", "source_turn_id", "created_in_turn_id", "evidence_kind"):
            if key in values:
                record["metadata"][key] = values[key]
        for key in ("revision", "deleted", "state", "pinned", "occurred_at", "evidence"):
            record["metadata"][key] = record[key]
        for key in KNOWLEDGE_FIELDS:
            if key in values:
                record[key] = values[key]
        self._write_knowledge(record, point_id=previous_row["point_id"] if previous_row else str(uuid.uuid4()))
        self._db.execute("DELETE FROM memory_fts WHERE scope=? AND id=?", (scope, memory_id))
        if not record["deleted"]:
            self._db.execute("INSERT INTO memory_fts VALUES (?,?,?)",
                             (scope, memory_id, index_text(memory_text(record))))
        if "evidence" in raw:
            self._db.execute("DELETE FROM evidence WHERE scope=? AND memory_id=?", (scope, memory_id))
        for evidence in record["evidence"]:
            self._db.execute("INSERT OR REPLACE INTO evidence VALUES (?,?,?,?)",
                             (scope, memory_id, evidence["entry_id"], json.dumps(evidence, ensure_ascii=False)))
        return self._record(self._db.execute("SELECT * FROM memories WHERE scope=? AND id=?",
                                            (scope, memory_id)).fetchone())

    def list_memories(self, *, limit: int | None = DEFAULT_MEMORY_LIMIT, include_deleted: bool = False) -> list[dict]:
        with self.transaction():
            sql = "SELECT * FROM memories WHERE scope=?" + ("" if include_deleted else " AND deleted=0")
            sql += " ORDER BY json_extract(document,'$.updated_at') DESC, id"
            args = [self.scope_id]
            if limit is not None:
                sql += " LIMIT ?"
                args.append(limit)
            return [self._record(row) for row in self._db.execute(sql, args).fetchall()]

    def export_records(self) -> list[dict]:
        fields = {"id", "content", "layer", "category", "importance", "confidence", "source", "scope",
                  "created_at", "updated_at", "last_accessed_at", "metadata"}
        return [{key: value for key, value in item.items() if key in fields}
                for item in self.list_memories(limit=None, include_deleted=True)]

    def restore_records(self, records: list[dict]) -> None:
        with self.transaction():
            for item in records:
                legacy_deleted = self._db.execute(
                    "SELECT 1 FROM memories WHERE scope='' AND id=? AND deleted=1", (item["id"],),
                ).fetchone()
                if not legacy_deleted and self.get(item["id"], include_deleted=True) is None:
                    self._save(item, preserve=True)
        self._index.schedule()

    def import_records(self, records: list[dict], *, overwrite: bool = False) -> None:
        """目录迁移保留角色；相同身份默认保留本地内容和删除标记。"""
        with self.transaction():
            for item in sorted(records, key=lambda row: not (row.get("scope") == "" and row.get("deleted"))):
                scope = item.get("scope", self.scope_id)
                old = self._db.execute("SELECT 1 FROM memories WHERE scope=? AND id=?",
                                       (scope, item["id"])).fetchone()
                legacy_deleted = self._db.execute(
                    "SELECT 1 FROM memories WHERE scope='' AND id=? AND deleted=1", (item["id"],),
                ).fetchone()
                if not legacy_deleted and (old is None or overwrite):
                    self._save(item, scope=scope, preserve=True)
        self._index.schedule()

    def all_records(self) -> list[dict]:
        with self.transaction():
            return [self._record(row) for row in self._db.execute("SELECT * FROM memories").fetchall()]

    def remap_scopes(self, mapping: dict[str, str], *, keep_conflicts: bool = False) -> int:
        """角色别名重新关联时保留记录 ID、证据和修改历史。"""
        count = 0
        with self.transaction():
            for source, target in mapping.items():
                for row in self._db.execute("SELECT * FROM memories WHERE scope=?", (source,)).fetchall():
                    old_id = row["id"]
                    new_id = f"core_profile:{target}" if old_id == f"core_profile:{source}" else old_id
                    if self._db.execute("SELECT 1 FROM memories WHERE scope=? AND id=?", (target, new_id)).fetchone():
                        if keep_conflicts:
                            continue
                        raise ValueError("角色映射后有重复记忆，请先处理冲突。")
                    record = self._record(row)
                    record.update(scope=target, id=new_id)
                    record["metadata"]["scope"] = target
                    self._write_knowledge(record, point_id=row["point_id"])
                    for table in ("entity_attrs", "entity_relations"):
                        self._db.execute(f"DELETE FROM {table} WHERE scope=? AND id=?", (source, old_id))
                    self._db.execute("UPDATE memory_fts SET scope=?,id=? WHERE scope=? AND id=?",
                                     (target, new_id, source, old_id))
                    self._db.execute("UPDATE evidence SET scope=?,memory_id=? WHERE scope=? AND memory_id=?",
                                     (target, new_id, source, old_id))
                    self._db.execute(
                        "UPDATE revisions SET scope=?,id=?,document=json_set(document,'$.scope',?,'$.id',?,'$.metadata.scope',?) "
                        "WHERE scope=? AND id=?", (target, new_id, target, new_id, target, source, old_id),
                    )
                    count += 1
                self._db.execute("INSERT OR IGNORE INTO processed_entries SELECT ?,entry_id FROM processed_entries WHERE scope=?",
                                 (target, source))
                self._db.execute("DELETE FROM processed_entries WHERE scope=?", (source,))
                self._db.execute("DELETE FROM state WHERE scope=? AND key IN ('curation','backfill')", (source,))
        return count

    def create_memory(self, arguments: dict, *, wait: bool = True, allow_sensitive: bool = False) -> dict:
        content = str(arguments.get("content") or "").strip()
        if not content:
            raise ValueError("记忆内容不能为空。")
        if not allow_sensitive and looks_like_sensitive_memory(content):
            raise ValueError("不能保存密码、密钥等敏感凭据。")
        with self.transaction():
            record = self._save({**arguments, "content": content})
        self._index.schedule()
        return {"status": "ready", "memory": record}

    remember_memory = create_memory

    def update_memory(self, arguments: dict, *, wait: bool = True, allow_sensitive: bool = False) -> dict:
        with self.transaction():
            previous = self.get(arguments["id"])
            if previous is None:
                raise ValueError("记忆不存在或不属于当前角色。")
            if "revision" in arguments and arguments["revision"] != previous["revision"]:
                raise ValueError("记忆已被修改，请重新读取后保存。")
            content = str(arguments.get("content", previous["content"])).strip()
            if not content:
                raise ValueError("记忆内容不能为空。")
            if not allow_sensitive and looks_like_sensitive_memory(content):
                raise ValueError("不能保存密码、密钥等敏感凭据。")
            record = self._save({**arguments, "content": content})
        self._index.schedule()
        return {"status": "ready", "memory": record}

    def forget_memory(self, arguments: dict, *, wait: bool = True) -> dict:
        with self.transaction():
            previous = self.get(arguments["id"])
            if previous is None:
                return {"status": "ready", "already_missing": True}
            self._save({"id": arguments["id"], "deleted": True,
                        "evidence": previous["evidence"] + arguments.get("evidence", [])})
        self._index.schedule()
        return {"status": "ready", "already_missing": False, "memory": previous, "forgotten": previous}

    delete_memory = forget_memory

    def duplicate_groups(self) -> list[list[dict]]:
        groups: dict[tuple, list[dict]] = {}
        for item in self.list_memories(limit=None):
            if item["pinned"] or item["source"] == "manual":
                continue
            key = (item["layer"], item["state"], *(item[key] for key in KNOWLEDGE_FIELDS), item["content"].strip())
            groups.setdefault(key, []).append(item)
        return [items for items in groups.values() if len(items) > 1]

    def merge_duplicates(self) -> dict:
        merged = 0
        with self.transaction():
            for items in self.duplicate_groups():
                items.sort(key=lambda item: (item["created_at"], item["id"]))
                kept = items[0]
                evidence = {source["entry_id"]: source for item in items for source in item["evidence"]}
                source_ids = list(dict.fromkeys(source for item in items
                                               for source in item["metadata"].get("source_entry_ids", [])))
                self._save({"id": kept["id"], "evidence": list(evidence.values()),
                            "source_entry_ids": source_ids,
                            "evidence_kind": "human" if any(source.get("role") == "user" for source in evidence.values())
                            else kept["metadata"].get("evidence_kind", "")})
                for duplicate in items[1:]:
                    self._save({"id": duplicate["id"], "deleted": True,
                                "metadata": {"merged_into": kept["id"]}})
                    merged += 1
        if merged:
            self._index.schedule()
        log_event("Memory", "重复记忆合并完成", {"scope": self.scope_id, "merged": merged},
                  event="memory.maintenance.merged")
        return {"merged": merged}

    def profile_memories(self) -> list[dict]:
        return [item for item in self.list_memories(limit=None)
                if item["state"] == "active" and (
                    item["layer"] == "core_profile" and item["subject"] == "用户" or
                    item["layer"] == "procedural" and
                    (item["pinned"] or item["source"] in {"manual", "user_tool"} or
                     item["metadata"].get("evidence_kind") == "human"))]

    def core_profile(self) -> dict | None:
        return next((item for item in self.profile_memories() if item["layer"] == "core_profile"), None)

    def set_core_profile(self, content: str, metadata: dict | None = None) -> dict:
        return self.create_memory({"id": f"core_profile:{self.scope_id}", "content": content,
                                   "layer": "core_profile", "metadata": metadata or {}})["memory"]

    def delete_core_profile(self) -> dict | None:
        previous = self.core_profile()
        if previous:
            self.forget_memory({"id": previous["id"]})
        return previous

    def search_memory(self, arguments: dict, *, wait: bool = False) -> dict:
        query = str(arguments.get("query") or "").strip()
        limit = int(arguments.get("limit", DEFAULT_MEMORY_LIMIT))
        automatic = bool(arguments.get("automatic", False))
        layer = arguments.get("layer")
        match, words = match_query(query)
        structured = {key: arguments[key] for key in ("subject", "predicate", "target") if arguments.get(key)}
        if automatic and not words and not structured:
            return {"status": "ready", "query": query, "memories": [], "count": 0}
        candidates: dict[str, dict] = {}
        with self.transaction():
            if structured and not query:
                where = " AND ".join(f"{key}=?" for key in structured)
                rows = self._db.execute(f"SELECT * FROM memories WHERE scope=? AND deleted=0 AND {where}",
                                        [self.scope_id, *structured.values()]).fetchall()
                for row in rows:
                    candidates[row["id"]] = {"memory": self._record(row), "rrf": 1, "matched_terms": [], "structured": True}
            if match:
                rows = self._db.execute(
                    "SELECT id,bm25(memory_fts) AS rank FROM memory_fts WHERE memory_fts MATCH ? AND scope=? "
                    "ORDER BY rank LIMIT ?", (match, self.scope_id, max(20, limit)),
                ).fetchall()
                for rank, row in enumerate(rows, 1):
                    memory = self.get(row["id"])
                    if memory:
                        overlap = words & set(tokens(memory_text(memory)))
                        candidates[memory["id"]] = {"memory": memory, "rrf": 1 / (60 + rank),
                                                   "lexical_rank": rank, "matched_terms": sorted(overlap)}
        dense_results = self._index.search(query, self.scope_id, max(20, limit)) if query else []
        for rank, dense in enumerate(dense_results, 1):
            memory = self.get(dense["id"])
            if memory is None or memory["revision"] != dense["revision"]:
                continue
            candidate = candidates.setdefault(memory["id"], {"memory": memory, "rrf": 0, "matched_terms": []})
            candidate.update({"dense_score": dense["score"], "dense_rank": rank})
            candidate["rrf"] += 1 / (60 + rank)
        result = []
        best_dense = max((item.get("dense_score", 0) for item in candidates.values()), default=0)
        for candidate in sorted(candidates.values(), key=lambda item: item["rrf"], reverse=True):
            memory = candidate["memory"]
            if layer and memory["layer"] != layer:
                continue
            if any(arguments.get(key) and memory[key] != arguments[key] for key in ("subject", "predicate", "target")):
                continue
            if automatic and (memory["state"] == "cancelled" or
                              memory["layer"] == "session" and memory["state"] != "active"):
                continue
            if arguments.get("excluded_turn_id") and memory["metadata"].get("created_in_turn_id") == arguments["excluded_turn_id"]:
                continue
            matched = candidate["matched_terms"]
            coverage = len(matched) / max(1, len(words))
            lexical_relevant = bool(matched) and coverage >= 0.5
            dense_relevant = candidate.get("dense_score", 0) >= max(DENSE_THRESHOLD, best_dense - DENSE_SCORE_WINDOW)
            if automatic and not lexical_relevant and not dense_relevant and not candidate.get("structured"):
                continue
            details = {key: value for key, value in candidate.items() if key != "memory"}
            memory["score"] = candidate["rrf"]
            memory["retrieval"] = {**details, "lexical_coverage": coverage,
                                   "reason": "entity" if candidate.get("structured") else "keyword+semantic" if lexical_relevant and dense_relevant else
                                   "keyword" if lexical_relevant else "semantic",
                                   "index_state": self._index.status, "model": DEFAULT_EMBEDDING_MODEL}
            result.append(memory)
        return {"status": "ready", "query": query, "memories": result[:limit], "count": len(result[:limit])}

    def get_state(self, key: str, *, scope: str | None = None, default: Any = "") -> Any:
        with self.transaction():
            row = self._db.execute("SELECT value FROM state WHERE scope=? AND key=?",
                                   (self.scope_id if scope is None else scope, key)).fetchone()
            return json.loads(row["value"]) if row else default

    def _set_state(self, key: str, value: Any, *, scope: str | None = None) -> None:
        self._db.execute("INSERT OR REPLACE INTO state VALUES (?,?,?)",
                         (self.scope_id if scope is None else scope, key, json.dumps(value, ensure_ascii=False)))

    def set_state(self, key: str, value: Any) -> None:
        with self.transaction():
            self._set_state(key, value)

    def unprocessed_entries(self, entries: list) -> list:
        with self.transaction():
            processed = {row[0] for row in self._db.execute(
                "SELECT entry_id FROM processed_entries WHERE scope=?", (self.scope_id,))}
            deleted_sources = {row[0] for row in self._db.execute(
                "SELECT entry_id FROM evidence JOIN memories ON evidence.scope=memories.scope "
                "AND evidence.memory_id=memories.id WHERE memories.scope=? AND deleted=1", (self.scope_id,))}
        return [entry for entry in entries if entry.entry_id not in processed | deleted_sources]

    def commit_curation(self, operations: list[dict], entries: list, *, cursor: str = "",
                        state_key: str = "curation", cancel_checker=None) -> dict:
        counts = {"created": 0, "updated": 0, "archived": 0, "ignored": 0}
        with self.transaction():
            if cancel_checker:
                cancel_checker()
            for operation in operations:
                if all(self._db.execute(
                    "SELECT 1 FROM processed_entries WHERE scope=? AND entry_id=?",
                    (self.scope_id, source["entry_id"])).fetchone() for source in operation["evidence"]):
                    counts["ignored"] += 1
                    continue
                memory_id = operation.get("id")
                previous = self.get(memory_id, include_deleted=True) if memory_id else None
                if memory_id and (previous is None or previous["deleted"] or previous["pinned"] or
                                  previous["revision"] != operation["expected_revision"]):
                    counts["ignored"] += 1
                    continue
                if previous and (previous["source"] == "manual" or previous["evidence"]) and (
                                  _time(operation["occurred_at"]) < _time(
                                      previous["updated_at"] if previous["source"] == "manual"
                                      else previous["occurred_at"])):
                    counts["ignored"] += 1
                    continue
                self._save(operation)
                counts["updated" if previous else "created"] += 1
            self._db.executemany("INSERT OR IGNORE INTO processed_entries VALUES (?,?)",
                                 [(self.scope_id, entry.entry_id) for entry in entries])
            if cursor:
                state = self.get_state(state_key, default={})
                state.update({"curation_cursor": cursor, "pending_turns": 0})
                self._set_state(state_key, state)
        self._index.schedule()
        return counts

    def index_rows(self, *, all_rows: bool = False, limit: int = 32) -> list[dict]:
        with self.transaction():
            sql = "SELECT scope,id,content,subject,predicate,target,revision,deleted,point_id FROM memories"
            if not all_rows:
                sql += " WHERE vector_revision != revision LIMIT ?"
            return [{**dict(row), "content": memory_text(dict(row))}
                    for row in self._db.execute(sql, () if all_rows else (limit,))]

    def mark_indexed(self, rows: list[dict]) -> None:
        with self.transaction():
            for table in ("entity_attrs", "entity_relations"):
                self._db.executemany(f"UPDATE {table} SET vector_revision=? WHERE scope=? AND id=? AND revision=?",
                                     [(row["revision"], row["scope"], row["id"], row["revision"]) for row in rows])

    def activate_index(self, collection: str, rows: list[dict]) -> None:
        with self.transaction():
            for table in ("entity_attrs", "entity_relations"):
                self._db.execute(f"UPDATE {table} SET vector_revision=0")
            self.mark_indexed(rows)
            self._set_state("vector_collection", collection, scope="")

    def rebuild_index(self) -> None:
        self._index.schedule(rebuild=True)

    def index_status(self) -> dict:
        return self._index.snapshot()

    def preload(self, *, wait: bool = False) -> None:
        self._index.schedule()

    def needs_embedding_model_download(self) -> bool:
        return not _embedding_model_cached(DEFAULT_EMBEDDING_MODEL, cache_dir=self.memory_cache_dir)

    def embedding_model_endpoint(self) -> str:
        return model_assets.embedding_endpoint()

    def download_embedding_model(self, **kwargs):
        result = download_embedding_model(cache_dir=self.memory_cache_dir, **kwargs)
        self._index.schedule(rebuild=True)
        return result

    def is_ready(self) -> bool:
        return not self._closed

    def close(self) -> None:
        if self._closed:
            return
        self._index.close()
        with self._lock:
            self._closed = True
            self._db.close()
