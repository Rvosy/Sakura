from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from time import monotonic
from typing import Any, Protocol

from app.core.runtime_log import log_event
from app.llm.prompts.types import ContextFragment, ContextRequest


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
                "记忆召回失败",
                {
                    "elapsed_ms": int((monotonic() - started_at) * 1000),
                    "error_type": type(exc).__name__,
                },
                event="memory.recall.failed",
                severity="warning",
                verbosity=0,
            )
            return MemoryRecallResult(status="failed", query=query)
        status = str(response.get("status", "ready"))
        memories = response.get("memories", [])
        if status != "ready" and not memories:
            log_event(
                "Memory",
                "记忆未就绪，本轮未执行召回",
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
        if not isinstance(memories, list):
            log_event(
                "Memory",
                "记忆召回结果无效",
                {"elapsed_ms": int((monotonic() - started_at) * 1000), "code": "INVALID_RESULT"},
                event="memory.recall.failed",
                severity="warning",
                verbosity=0,
            )
            return MemoryRecallResult(status="failed", query=query)

        selected = _select_memories(
            memories,
            self.threshold,
            self.limit,
            excluded_created_in_turn_id=request.current_turn_id,
        )
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
                metadata={
                    "memory_id": memory["id"],
                    "score": memory["score"],
                    "source": memory["source"],
                },
            )
            for index, memory in enumerate(selected)
        )
        _log_recall_finished(
            started_at,
            status="ready",
            candidates=len(memories),
            selected=len(fragments),
        )
        return MemoryRecallResult(fragments=fragments, status="ready", query=query)


def create_legacy_memory_context_provider(
    memory: MemoryLike,
    *,
    provider_id: str = "sakura.legacy.memory",
):
    """Preserve Legacy Qt recall through the ordinary Context provider path."""

    from app.plugins.models import ContextProviderContribution

    recall = MemoryRecallService(memory)
    return ContextProviderContribution(
        provider_id=provider_id,
        description="Legacy Qt 当前角色的本地长期记忆。",
        build_context=lambda request: recall.recall(request).fragments,
        order=60,
    )


def _log_recall_finished(
    started_at: float,
    *,
    status: str,
    candidates: int,
    selected: int,
) -> None:
    log_event(
        "Memory",
        "记忆召回完成",
        {
            "status": status,
            "candidates": candidates,
            "selected": selected,
            "elapsed_ms": int((monotonic() - started_at) * 1000),
        },
        event="memory.recall.finished",
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
    memories: list[Any],
    threshold: float,
    limit: int,
    *,
    excluded_created_in_turn_id: str = "",
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
        created_in_turn_id = str(
            raw.get("created_in_turn_id") or metadata.get("created_in_turn_id") or ""
        ).strip()
        if excluded_created_in_turn_id and created_in_turn_id == excluded_created_in_turn_id:
            continue
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
