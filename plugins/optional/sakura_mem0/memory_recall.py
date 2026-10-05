from __future__ import annotations

from dataclasses import dataclass
from time import monotonic
from typing import Any, Protocol

try:
    from .support import log_event
    from .domain_types import ContextFragment, ContextRequest
except ImportError:
    from support import log_event
    from domain_types import ContextFragment, ContextRequest


DEFAULT_MEMORY_RECALL_LIMIT = 5
DEFAULT_MEMORY_RECALL_CANDIDATES = 10
# all-MiniLM 这类轻量嵌入模型的余弦相似度天然偏低（实测相关命中也常在 0.3~0.45），
# 0.5 会把所有候选都过滤掉、令自动召回形同虚设。用 0.3 作为去噪下限，配合 top-k=5
# 与按分排序，既挡住明显无关项，又能让最相关的少量记忆进入上下文。
DEFAULT_MEMORY_RELEVANCE_THRESHOLD = 0.3
MAX_MEMORY_QUERY_CHARS = 4000


class MemoryLike(Protocol):
    def search_memory(
        self,
        arguments: dict[str, Any],
        *,
        wait: bool = False,
    ) -> dict[str, Any]: ...


@dataclass(frozen=True)
class MemoryRecallResult:
    fragments: tuple[ContextFragment, ...] = ()
    status: str = "ready"
    query: str = ""


class MemoryRecallService:
    """基于本轮上下文选择少量相关长期记忆。"""

    def __init__(
        self,
        memory: MemoryLike,
        *,
        limit: int = DEFAULT_MEMORY_RECALL_LIMIT,
        threshold: float = DEFAULT_MEMORY_RELEVANCE_THRESHOLD,
    ) -> None:
        self.memory = memory
        self.limit = max(1, limit)
        self.threshold = threshold

    def recall(self, request: ContextRequest) -> MemoryRecallResult:
        started_at = monotonic()
        query = _build_memory_query(request)
        if not query:
            _log_recall_finished(started_at, status="skipped", candidates=0, selected=0)
            return MemoryRecallResult(query="")
        try:
            response = self.memory.search_memory(
                {"query": query, "limit": DEFAULT_MEMORY_RECALL_CANDIDATES},
                wait=False,
            )
        except Exception as exc:  # noqa: BLE001 - 记忆故障不得阻断普通聊天
            log_event(
                "Memory",
                "查找相关记忆时出错了",
                {
                    "elapsed_ms": int((monotonic() - started_at) * 1000),
                    "error_type": type(exc).__name__,
                    "diagnostic": str(exc),
                },
                event="memory.recall.failed",
                severity="warning",
                verbosity=0,
            )
            return MemoryRecallResult(status="failed", query=query)
        status = response["status"]
        memories = response["memories"]
        if status != "ready":
            log_event(
                "Memory",
                "记忆服务还未就绪，这次未查找记忆",
                {
                    "status": status,
                    "candidates": 0,
                    "selected": 0,
                    "elapsed_ms": int((monotonic() - started_at) * 1000),
                    "reason_code": "MEMORY_NOT_READY",
                },
                event="memory.recall.unavailable",
                severity="warning",
                verbosity=0,
            )
            return MemoryRecallResult(status=status, query=query)
        selected = _select_memories(
            memories,
            self.threshold,
            self.limit,
            excluded_created_in_turn_id=request.current_turn_id,
        )
        fragments: list[ContextFragment] = []
        for memory in selected:
            source = memory["source"].lower()
            fragments.append(ContextFragment(
                fragment_id=f"memory.{memory['id']}",
                source="memory",
                content=f"与本轮相关的长期记忆：{memory['content']}",
                trust="trusted" if source == "explicit" else "untrusted",
                priority=80 if source == "explicit" else 70,
                freshness=memory["updatedAt"],
                token_budget=512,
                sensitivity="private",
                cache_scope="turn",
                metadata={
                    "memory_id": memory["id"],
                    "score": memory["score"],
                    "source": source,
                },
            ))
        _log_recall_finished(
            started_at,
            status="ready",
            candidates=len(memories),
            selected=len(fragments),
        )
        return MemoryRecallResult(fragments=tuple(fragments), status="ready", query=query)


def _log_recall_finished(
    started_at: float,
    *,
    status: str,
    candidates: int,
    selected: int,
) -> None:
    log_event(
        "Memory",
        "这次未查找记忆" if status == "skipped" else f"找到 {selected} 条相关记忆" if selected else "没有找到相关记忆",
        {
            "status": status,
            "candidates": candidates,
            "selected": selected,
            "elapsed_ms": int((monotonic() - started_at) * 1000),
        },
        event="memory.recall.finished",
        severity="debug" if status == "skipped" else "info",
        verbosity=1,
    )


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
    memories: list[dict[str, Any]],
    threshold: float,
    limit: int,
    *,
    excluded_created_in_turn_id: str = "",
) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    seen: set[str] = set()
    for memory in memories:
        dedupe_key = " ".join(memory["content"].lower().split())
        if dedupe_key in seen:
            continue
        score = memory["score"]
        if score is not None and score < threshold:
            continue
        if excluded_created_in_turn_id and memory.get("createdInTurnId") == excluded_created_in_turn_id:
            continue
        selected.append(memory)
        seen.add(dedupe_key)
    selected.sort(
        key=lambda item: (
            item["score"] is None,
            -(item["score"] if item["score"] is not None else -1.0),
            item["source"].lower() != "explicit",
            item["updatedAt"],
        )
    )
    return selected[:limit]
