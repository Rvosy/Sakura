from __future__ import annotations

import re
import sqlite3
import threading
from collections.abc import Callable, Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4


SETTINGS_SECTION_ID = "fact_memory"
COLLECTION_ID = "facts"
TOOL_NAME = "fact_memory_search"
MAX_CONTENT_LENGTH = 1000
MAX_KEYWORDS_LENGTH = 200
MAX_RECALL_ITEMS = 5
MAX_RECALL_CHARS = 2000
_KEYWORD_SEPARATOR = re.compile(r"[,，、;；\r\n]+")
_CHINESE_RUN = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]+")
_ENGLISH_WORD = re.compile(r"[a-z0-9]+")


class FactMemoryPlugin:
    def setup(self, context: Any) -> None:
        contributions = context.get("sakura.host.context")
        describe = getattr(contributions, "describe", None)
        capabilities = describe() if callable(describe) else None
        if (
            not isinstance(capabilities, Mapping)
            or capabilities.get("schemaVersion") != 2
            or "turn" not in capabilities.get("scopes", [])
            or "skip" not in capabilities.get("failurePolicies", [])
        ):
            raise RuntimeError("FACT_MEMORY_HOST_UNSUPPORTED: 请更新 Sakura 后启用便签记忆。")
        runtime = FactMemoryRuntime(
            Path(context.data_path("facts.sqlite3")),
            context.get("sakura.host.character").current,
        )
        context.effect(runtime.close)
        context.get("sakura.host.settings").register(
            {"sectionId": SETTINGS_SECTION_ID, "title": "便签记忆", "fields": []}
        )
        context.get("sakura.host.settings.collection-v0").register(
            SETTINGS_SECTION_ID,
            {
                "collectionId": COLLECTION_ID,
                "title": "当前角色的便签",
                "columns": [
                    {"key": "content", "label": "内容", "type": "string", "maxLength": MAX_CONTENT_LENGTH},
                    {"key": "keywords", "label": "关键词", "type": "string", "maxLength": MAX_KEYWORDS_LENGTH},
                    {"key": "updatedAt", "label": "更新时间", "type": "datetime"},
                ],
                "fields": [
                    {
                        "key": "content", "label": "内容", "type": "string",
                        "default": "", "required": True, "maxLength": MAX_CONTENT_LENGTH,
                    },
                    {
                        "key": "keywords", "label": "关键词", "type": "string",
                        "default": "", "maxLength": MAX_KEYWORDS_LENGTH,
                        "description": "用逗号、顿号、分号或换行分隔；留空时按便签内容匹配。",
                    },
                ],
                "filters": [],
                "searchable": True,
                "pageSize": 25,
                "deleteConfirmation": "确定删除这条便签吗？此操作不能撤销。",
            },
            query=runtime.query,
            create=runtime.create,
            update=runtime.update,
            delete=runtime.delete,
        )
        contributions.register(
            {
                "providerId": context.plugin_id,
                "description": "从当前角色的手写便签中按关键词或文字匹配本轮相关内容。",
                "order": 60,
                "scope": "turn",
                "failurePolicy": "skip",
            },
            runtime.context,
        )
        context.get("sakura.host.tools").register(
            {
                "name": TOOL_NAME,
                "description": (
                    "只读搜索当前角色便签的正文或关键词；需要核对个人偏好、约定或项目事实时使用。"
                    "query 填要查找的词或短语；仅做字面匹配，未命中不表示用户没有相关偏好。"
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "minLength": 1, "maxLength": 200},
                        "limit": {"type": "integer", "minimum": 1, "maximum": MAX_RECALL_ITEMS},
                    },
                    "required": ["query"],
                    "additionalProperties": False,
                },
                "group": "plugin",
                "risk": "low",
            },
            runtime.search_tool,
        )


class FactMemoryRuntime:
    def __init__(self, database_path: Path, current_character: Callable[[], Mapping[str, Any]]) -> None:
        self._current_character = current_character
        self._lock = threading.Lock()
        self._closed = False
        database_path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(database_path, check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        try:
            with self._connection:
                self._connection.execute(
                    "CREATE TABLE IF NOT EXISTS facts ("
                    "id TEXT PRIMARY KEY, character_id TEXT NOT NULL, content TEXT NOT NULL, "
                    "keywords TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)"
                )
                self._connection.execute(
                    "CREATE INDEX IF NOT EXISTS facts_character ON facts(character_id)"
                )
        except Exception:
            self._connection.close()
            raise

    def close(self) -> None:
        with self._lock:
            if not self._closed:
                self._closed = True
                self._connection.close()

    def query(self, request: Mapping[str, Any]) -> dict[str, Any]:
        if not isinstance(request, Mapping):
            raise ValueError("FACT_MEMORY_QUERY_INVALID")
        cursor, limit, search = request.get("cursor"), request.get("limit", 25), request.get("search", "")
        if (
            (cursor is not None and (not isinstance(cursor, str) or not re.fullmatch(r"[0-9]{1,10}", cursor)))
            or not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 100
            or not isinstance(search, str) or len(search) > 200
            or request.get("filters", {}) != {}
        ):
            raise ValueError("FACT_MEMORY_QUERY_INVALID")
        rows = self._rows(self._character_id())
        term = search.strip().casefold()
        if term:
            rows = [row for row in rows if term in row["content"].casefold() or term in row["keywords"].casefold()]
        offset = int(cursor or 0)
        end = offset + limit
        return {
            "items": [_collection_item(row) for row in rows[offset:end]],
            "nextCursor": str(end) if end < len(rows) else None,
            "total": len(rows),
        }

    def create(self, values: Mapping[str, Any]) -> dict[str, Any]:
        normalized = _values(values)
        character_id = self._character_id()
        item_id = uuid4().hex
        now = datetime.now(timezone.utc).isoformat()
        with self._lock:
            self._require_open()
            with self._connection:
                self._connection.execute(
                    "INSERT INTO facts VALUES (?, ?, ?, ?, ?, ?)",
                    (item_id, character_id, normalized["content"], normalized["keywords"], now, now),
                )
        return {"itemId": item_id, "values": {**normalized, "updatedAt": now}}

    def update(self, item_id: str, values: Mapping[str, Any]) -> dict[str, Any]:
        _validate_item_id(item_id)
        normalized = _values(values, partial=True)
        character_id = self._character_id()
        with self._lock:
            self._require_open()
            with self._connection:
                row = self._connection.execute(
                    "SELECT * FROM facts WHERE id = ? AND character_id = ?", (item_id, character_id)
                ).fetchone()
                if row is None:
                    raise ValueError("FACT_MEMORY_NOT_FOUND")
                updated = {"content": row["content"], "keywords": row["keywords"], **normalized}
                now = datetime.now(timezone.utc).isoformat()
                self._connection.execute(
                    "UPDATE facts SET content = ?, keywords = ?, updated_at = ? WHERE id = ? AND character_id = ?",
                    (updated["content"], updated["keywords"], now, item_id, character_id),
                )
        return {"itemId": item_id, "values": {**updated, "updatedAt": now}}

    def delete(self, item_id: str) -> dict[str, bool]:
        _validate_item_id(item_id)
        character_id = self._character_id()
        with self._lock:
            self._require_open()
            with self._connection:
                result = self._connection.execute(
                    "DELETE FROM facts WHERE id = ? AND character_id = ?", (item_id, character_id)
                )
        return {"deleted": result.rowcount > 0}

    def search_tool(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        if not isinstance(arguments, Mapping) or set(arguments) - {"query", "limit"}:
            raise ValueError("FACT_MEMORY_SEARCH_INVALID")
        query, limit = arguments.get("query"), arguments.get("limit", MAX_RECALL_ITEMS)
        if (
            not isinstance(query, str) or not query.strip() or len(query) > 200
            or not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= MAX_RECALL_ITEMS
        ):
            raise ValueError("FACT_MEMORY_SEARCH_INVALID")
        term = query.strip().casefold()
        matches = [
            row for row in self._rows(self._character_id())
            if term in row["content"].casefold() or term in row["keywords"].casefold()
        ]
        return {"facts": [
            {"id": row["id"], **_collection_item(row)["values"]}
            for row in _within_budget(matches, limit)
        ]}

    def context(self, request: Mapping[str, Any]) -> list[dict[str, Any]]:
        character_id, query = request.get("character_id"), request.get("current_input", "")
        if not isinstance(character_id, str) or not character_id or not isinstance(query, str) or not query.strip():
            return []
        if character_id != self._character_id():
            return []
        rows = self._recall(character_id, query, MAX_RECALL_ITEMS)
        if not rows:
            return []
        return [{
            "id": "facts",
            "content": "当前角色的便签记忆：\n" + "\n".join(f"- {row['content']}" for row in rows),
            "priority": 60,
            "budgetHint": 4096,
        }]

    def _recall(self, character_id: str, query: str, limit: int) -> list[sqlite3.Row]:
        folded = query.casefold()
        terms = _text_terms(folded)
        ranked: list[tuple[tuple[int, int], sqlite3.Row]] = []
        for row in self._rows(character_id):
            keywords = _keywords(row["keywords"])
            if keywords:
                score = (2, sum(keyword in folded for keyword in keywords))
            else:
                score = (1, len(terms & _text_terms(row["content"].casefold())))
            if score[1]:
                ranked.append((score, row))
        # Stable sorting retains updated_at DESC, id ASC for equal relevance.
        ranked.sort(key=lambda item: item[0], reverse=True)
        return _within_budget([row for _score, row in ranked], limit)

    def _rows(self, character_id: str) -> list[sqlite3.Row]:
        with self._lock:
            self._require_open()
            return self._connection.execute(
                "SELECT * FROM facts WHERE character_id = ? ORDER BY updated_at DESC, id ASC", (character_id,)
            ).fetchall()

    def _character_id(self) -> str:
        character_id = self._current_character().get("id")
        if not isinstance(character_id, str) or not character_id:
            raise RuntimeError("FACT_MEMORY_CHARACTER_UNAVAILABLE")
        return character_id

    def _require_open(self) -> None:
        if self._closed:
            raise RuntimeError("FACT_MEMORY_CLOSED")


def _values(values: Mapping[str, Any], *, partial: bool = False) -> dict[str, str]:
    if (
        not isinstance(values, Mapping) or not values or set(values) - {"content", "keywords"}
        or (not partial and "content" not in values)
    ):
        raise ValueError("FACT_MEMORY_VALUES_INVALID")
    result = {}
    for key, maximum in (("content", MAX_CONTENT_LENGTH), ("keywords", MAX_KEYWORDS_LENGTH)):
        if partial and key not in values:
            continue
        value = values.get(key, "")
        if not isinstance(value, str) or len(value) > maximum or "\x00" in value:
            raise ValueError("FACT_MEMORY_VALUES_INVALID")
        value = value.strip()
        if (key == "content" and not value) or (key == "keywords" and value and not _keywords(value)):
            raise ValueError("FACT_MEMORY_VALUES_INVALID")
        result[key] = value
    return result


def _validate_item_id(item_id: str) -> None:
    if not isinstance(item_id, str) or not item_id or len(item_id) > 200:
        raise ValueError("FACT_MEMORY_ITEM_INVALID")


def _collection_item(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "itemId": row["id"],
        "values": {"content": row["content"], "keywords": row["keywords"], "updatedAt": row["updated_at"]},
    }


def _within_budget(rows: list[sqlite3.Row], limit: int) -> list[sqlite3.Row]:
    selected = []
    remaining = MAX_RECALL_CHARS
    for row in rows:
        if len(row["content"]) > remaining:
            continue
        selected.append(row)
        remaining -= len(row["content"])
        if len(selected) == limit:
            break
    return selected


def _keywords(value: str) -> set[str]:
    return {term.strip().casefold() for term in _KEYWORD_SEPARATOR.split(value) if term.strip()}


def _text_terms(folded: str) -> set[str]:
    terms = set(_ENGLISH_WORD.findall(folded))
    for run in _CHINESE_RUN.findall(folded):
        terms.update(run[index:index + 2] for index in range(len(run) - 1))
    return terms
