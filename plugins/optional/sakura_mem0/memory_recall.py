from __future__ import annotations

from dataclasses import dataclass
from time import monotonic

try:
    from .support import log_event
    from .domain_types import ContextFragment, ContextRequest
    from .memory import memory_text
except ImportError:
    from support import log_event
    from domain_types import ContextFragment, ContextRequest
    from memory import memory_text


@dataclass(frozen=True)
class MemoryRecallResult:
    fragments: tuple[ContextFragment, ...] = ()
    status: str = "ready"
    query: str = ""


class MemoryRecallService:
    def __init__(self, memory, *, limit: int = 5) -> None:
        self.memory = memory
        self.limit = limit

    def recall(self, request: ContextRequest) -> MemoryRecallResult:
        started = monotonic()
        query = _build_memory_query(request)
        try:
            profiles = self.memory.profile_memories()
            profiles.sort(key=lambda item: (not item.get("pinned"), -item.get("importance", 0.5)))
            selected = []
            used = 0
            for item in profiles:
                if item.get("createdInTurnId") == request.current_turn_id and request.current_turn_id:
                    continue
                if used + len(item["content"]) > 4000:
                    continue
                selected.append((item, "档案与约定"))
                used += len(item["content"])
            profile_ids = {item["id"] for item, _ in selected}
            response = self.memory.search_memory({
                "query": query, "limit": self.limit + len(profile_ids), "automatic": True,
                "excluded_turn_id": request.current_turn_id,
            }, wait=False) if query else {"memories": [], "status": "ready"}
            recalled = [item for item in response["memories"] if item["id"] not in profile_ids][:self.limit]
            selected.extend((item, "相关记忆") for item in recalled)
            fragments = []
            for memory, label in selected:
                evidence = memory.get("evidence", [])
                quotes = "；".join(dict.fromkeys(source["quote"] for source in evidence if source["role"] == "user"))
                content = f"{label}（发生时间：{memory.get('occurredAt', '')}，状态：{memory.get('state', 'active')}）：{memory_text(memory)}"
                if quotes and quotes not in memory["content"]:
                    content += f"\n用户原话依据：{quotes}"
                fragments.append(ContextFragment(
                    fragment_id=f"memory.{memory['id']}", source="memory", content=content,
                    priority=85 if label == "档案与约定" else 65,
                    freshness=memory["updatedAt"], token_budget=1000, cache_scope="turn",
                    metadata={"memory_id": memory["id"], "source": memory["source"],
                              "revision": memory.get("revision"), "retrieval": memory.get("retrieval", {}),
                              "evidence_entry_ids": [source["entry_id"] for source in evidence],
                              "selection": "profile" if label == "档案与约定" else "retrieval"},
                ))
            log_event("Memory", "本轮记忆检索完成", {
                "profiles": len(profile_ids), "recalled": len(recalled),
                "elapsed_ms": int((monotonic() - started) * 1000),
            }, event="memory.recall.finished", verbosity=1)
            return MemoryRecallResult(tuple(fragments), response["status"], query)
        except Exception as exc:
            log_event("Memory", "本轮记忆检索失败", {"error": str(exc)},
                      event="memory.recall.failed", severity="warning")
            return MemoryRecallResult(status="failed", query=query)


def _build_memory_query(request: ContextRequest) -> str:
    query = request.current_input.strip()
    # 只为明确的承接问句补一条用户上下文，避免上一话题污染新问题。
    if query.startswith(("那件事", "那个", "后来呢", "然后呢", "继续上次", "刚才说的")):
        previous = [message.content.strip() for message in request.recent_messages
                    if message.role == "user" and message.content.strip() != query]
        if previous:
            query = previous[-1] + "\n" + query
    if request.source == "event":
        query = "\n".join([query, *request.visual_summaries]).strip()
    return query[:4000]
