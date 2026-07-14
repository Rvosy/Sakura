from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from app.agent.memory import MemoryStore
from app.llm.prompts.types import ContextFragment, ContextRequest


DEFAULT_MEMORY_RECALL_LIMIT = 5
DEFAULT_MEMORY_RECALL_CANDIDATES = 10
DEFAULT_LOCAL_RECALL_CANDIDATES = 300
# all-MiniLM 这类轻量嵌入模型的余弦相似度天然偏低（实测相关命中也常在 0.3~0.45），
# 0.5 会把所有候选都过滤掉、令自动召回形同虚设。用 0.3 作为去噪下限，配合 top-k=5
# 与按分排序，既挡住明显无关项，又能让最相关的少量记忆进入上下文。
DEFAULT_MEMORY_RELEVANCE_THRESHOLD = 0.3
MAX_MEMORY_QUERY_CHARS = 4000
_RECALL_INTENT_MARKERS = (
    "还记得",
    "记得",
    "记得我",
    "之前",
    "上次",
    "以前",
    "回忆",
    "聊过",
    "提过",
    "说过",
    "去过",
    "我的名字",
    "我的偏好",
    "我是谁",
    "do you remember",
    "remember when",
    "last time",
    "previously",
    "we talked about",
    "i told you",
    "覚えて",
    "前回",
    "以前",
)
_RECALL_QUERY_FILLERS = tuple(
    sorted(
        (
            "do you remember",
            "remember when",
            "last time",
            "previously",
            "we talked about",
            "i told you",
            "还记得",
            "记得",
            "记得我",
            "之前",
            "上次",
            "以前",
            "回忆",
            "聊过",
            "提过",
            "说过",
            "去过",
            "覚えて",
            "前回",
            "你还",
            "你知道",
            "你",
            "我们",
            "我的",
            "那个",
            "那次",
            "这件事",
            "事情",
            "说的",
            "聊的",
            "提到的",
            "是什么",
            "吗",
            "呢",
            "啊",
            "the",
            "my",
            "me",
            "about",
        ),
        key=len,
        reverse=True,
    )
)
_ENTITY_TOKEN_RE = re.compile(r"[A-Za-z0-9_./:\-]{2,}|[\u3040-\u30ff]{2,}")
_CHINESE_RUN_RE = re.compile(r"[\u4e00-\u9fff]+")
_ENTITY_TOKEN_STOP_WORDS = {
    "知道",
    "记得",
    "回忆",
    "之前",
    "上次",
    "以前",
    "事情",
    "项目",
}


@dataclass(frozen=True)
class MemoryRecallResult:
    fragments: tuple[ContextFragment, ...] = ()
    status: str = "ready"
    query: str = ""


class MemoryRecallService:
    """基于本轮上下文选择少量相关长期记忆。"""

    def __init__(
        self,
        memory: MemoryStore,
        *,
        limit: int = DEFAULT_MEMORY_RECALL_LIMIT,
        threshold: float = DEFAULT_MEMORY_RELEVANCE_THRESHOLD,
    ) -> None:
        self.memory = memory
        self.limit = max(1, limit)
        self.threshold = threshold

    def recall(self, request: ContextRequest) -> MemoryRecallResult:
        query = _build_memory_query(request)
        if not query:
            return MemoryRecallResult(query="")
        local_memories = _local_exact_recall_memories(
            self.memory,
            request.current_input,
        )
        if local_memories:
            local_result = _build_recall_result(
                local_memories,
                threshold=self.threshold,
                limit=self.limit,
                query=query,
            )
            if local_result.fragments:
                return local_result
        try:
            response = self.memory.search_memory(
                {"query": query, "limit": DEFAULT_MEMORY_RECALL_CANDIDATES},
                wait=False,
            )
        except Exception:  # noqa: BLE001 - 记忆故障不得阻断普通聊天
            return MemoryRecallResult(status="failed", query=query)
        status = str(response.get("status", "ready"))
        memories = response.get("memories", [])
        if status != "ready" and not memories:
            return MemoryRecallResult(status=status, query=query)
        if not isinstance(memories, list):
            return MemoryRecallResult(status="failed", query=query)

        return _build_recall_result(
            memories,
            threshold=self.threshold,
            limit=self.limit,
            query=query,
        )


def _build_memory_query(request: ContextRequest) -> str:
    current_input = request.current_input.strip()
    if _is_explicit_recall_query(current_input):
        return current_input[:MAX_MEMORY_QUERY_CHARS].rstrip()
    parts: list[str] = []
    if current_input:
        parts.append(current_input)
    recent_user = [
        message.content.strip()
        for message in request.recent_messages
        if message.role == "user" and message.content.strip()
    ]
    parts.extend(recent_user[-2:])
    parts.extend(summary.strip() for summary in request.visual_summaries if summary.strip())
    unique = list(dict.fromkeys(parts))
    query = "\n".join(unique).strip()
    return query[:MAX_MEMORY_QUERY_CHARS].rstrip()


def _build_recall_result(
    memories: list[Any],
    *,
    threshold: float,
    limit: int,
    query: str,
) -> MemoryRecallResult:
    selected = _select_memories(memories, threshold, limit)
    fragments = tuple(
        ContextFragment(
            fragment_id=f"memory.{memory['id'] or index}",
            source="memory",
            content=f"与本轮相关的长期记忆：{memory['content']}",
            trust="trusted" if memory["source"] == "explicit" else "untrusted",
            priority=80 if memory["source"] == "explicit" else 70,
            freshness=memory["updated_at"],
            token_budget=512,
            sensitivity="private",
            cache_scope="turn",
        )
        for index, memory in enumerate(selected)
    )
    return MemoryRecallResult(fragments=fragments, status="ready", query=query)


def _local_exact_recall_memories(memory: MemoryStore, query: str) -> list[dict[str, Any]]:
    entity_tokens = _recall_entity_tokens(query)
    if not entity_tokens:
        return []

    is_ready = getattr(memory, "is_ready", None)
    if callable(is_ready):
        try:
            if not is_ready():
                return []
        except Exception:  # noqa: BLE001 - 状态探测失败时保留语义召回路径
            return []

    list_memories = getattr(memory, "list_memories", None)
    if not callable(list_memories):
        return []
    try:
        memories = list_memories(limit=DEFAULT_LOCAL_RECALL_CANDIDATES)
    except Exception:  # noqa: BLE001 - 本地兜底失败不得阻断语义召回
        return []
    if not isinstance(memories, list):
        return []

    matched: list[tuple[float, int, dict[str, Any]]] = []
    for index, raw in enumerate(memories):
        if not isinstance(raw, dict):
            continue
        content = str(raw.get("content") or raw.get("memory") or "").strip()
        if not content:
            continue
        lowered = content.casefold()
        hits = [token for token in entity_tokens if token in lowered]
        if not hits:
            continue
        lexical_score = 1.0 + min(0.75, sum(min(len(token), 8) for token in hits) / 24)
        existing_score = _optional_score(raw.get("score"))
        candidate = dict(raw)
        candidate["score"] = max(existing_score or 0.0, lexical_score)
        matched.append((candidate["score"], index, candidate))

    matched.sort(key=lambda item: (-item[0], item[1]))
    return [item[2] for item in matched]


def _is_explicit_recall_query(query: str) -> bool:
    lowered = query.casefold()
    return bool(lowered) and any(marker in lowered for marker in _RECALL_INTENT_MARKERS)


def _recall_entity_tokens(query: str) -> set[str]:
    if not _is_explicit_recall_query(query):
        return set()
    cleaned = query.casefold()
    for filler in _RECALL_QUERY_FILLERS:
        cleaned = cleaned.replace(filler, " ")

    tokens = {
        token.casefold()
        for token in _ENTITY_TOKEN_RE.findall(cleaned)
        if token.casefold() not in _ENTITY_TOKEN_STOP_WORDS
    }
    for run in _CHINESE_RUN_RE.findall(cleaned):
        if run in _ENTITY_TOKEN_STOP_WORDS:
            continue
        if 2 <= len(run) <= 12:
            tokens.add(run)
        if len(run) > 2:
            tokens.update(
                run[index : index + 2]
                for index in range(len(run) - 1)
                if run[index : index + 2] not in _ENTITY_TOKEN_STOP_WORDS
            )
    return {token for token in tokens if len(token) >= 2}


def _select_memories(
    memories: list[Any],
    threshold: float,
    limit: int,
) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    now = datetime.now().astimezone()
    for raw in memories:
        if not isinstance(raw, dict):
            continue
        content = str(raw.get("content") or raw.get("memory") or "").strip()
        if not content:
            continue
        dedupe_key = " ".join(content.lower().split())
        if dedupe_key in seen or _is_expired(raw.get("expires_at"), now):
            continue
        score = _optional_score(raw.get("score"))
        if score is not None and score < threshold:
            continue
        metadata = raw.get("metadata") if isinstance(raw.get("metadata"), dict) else {}
        source = str(raw.get("source") or metadata.get("source") or "inferred").strip().lower()
        updated_at = str(raw.get("updated_at") or metadata.get("updated_at") or "").strip()
        normalized.append(
            {
                "id": str(raw.get("id") or raw.get("memory_id") or "").strip(),
                "content": content,
                "score": score,
                "source": source,
                "updated_at": updated_at,
            }
        )
        seen.add(dedupe_key)
    normalized.sort(
        key=lambda item: (
            item["score"] is None,
            -(item["score"] if item["score"] is not None else -1.0),
            item["source"] != "explicit",
            item["updated_at"],
        )
    )
    return normalized[:limit]


def _optional_score(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _is_expired(value: Any, now: datetime) -> bool:
    if not isinstance(value, str) or not value.strip():
        return False
    try:
        expires_at = datetime.fromisoformat(value.strip())
    except ValueError:
        return False
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=now.tzinfo)
    return expires_at <= now
