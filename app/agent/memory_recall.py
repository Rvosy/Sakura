from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from app.agent.memory import MemoryStore
from app.llm.prompts.types import ContextFragment, ContextRequest


DEFAULT_MEMORY_RECALL_LIMIT = 5
DEFAULT_MEMORY_RECALL_CANDIDATES = 10
# all-MiniLM 这类轻量嵌入模型的余弦相似度天然偏低（实测相关命中也常在 0.3~0.45），
# 0.5 会把所有候选都过滤掉、令自动召回形同虚设。用 0.3 作为去噪下限，配合 top-k=5
# 与按分排序，既挡住明显无关项，又能让最相关的少量记忆进入上下文。
DEFAULT_MEMORY_RELEVANCE_THRESHOLD = 0.3
MAX_MEMORY_QUERY_CHARS = 4000


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

        selected = _select_memories(memories, self.threshold, self.limit)
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


def _build_memory_query(request: ContextRequest) -> str:
    parts: list[str] = []
    if request.current_input.strip():
        parts.append(request.current_input.strip())
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
