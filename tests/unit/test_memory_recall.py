from __future__ import annotations

from typing import Any

from app.agent.memory_recall import MemoryRecallService, _build_memory_query
from app.llm.prompts.types import ContextMessage, ContextRequest


class FakeMemoryStore:
    def __init__(
        self,
        *,
        local_memories: list[dict[str, Any]] | None = None,
        semantic_memories: list[dict[str, Any]] | None = None,
        ready: bool = True,
    ) -> None:
        self.local_memories = list(local_memories or [])
        self.semantic_memories = list(semantic_memories or [])
        self.ready = ready
        self.list_calls: list[int] = []
        self.search_calls: list[dict[str, Any]] = []

    def is_ready(self) -> bool:
        return self.ready

    def list_memories(self, *, limit: int) -> list[dict[str, Any]]:
        self.list_calls.append(limit)
        return self.local_memories[:limit]

    def search_memory(self, arguments, *, wait=False):  # type: ignore[no-untyped-def]
        self.search_calls.append({"arguments": dict(arguments), "wait": wait})
        return {"status": "ready", "memories": list(self.semantic_memories)}


def _request(current_input: str, *recent_user_messages: str) -> ContextRequest:
    return ContextRequest(
        current_input=current_input,
        recent_messages=tuple(
            ContextMessage("user", content)
            for content in (*recent_user_messages, current_input)
        ),
    )


def test_explicit_recall_uses_local_chinese_entity_match_before_semantic_search() -> None:
    store = FakeMemoryStore(
        local_memories=[
            {"id": "unrelated", "memory": "用户喜欢听轻音乐"},
            {"id": "guangzhou", "memory": "用户之前去过广州塔，觉得夜景很好"},
        ],
        semantic_memories=[
            {"id": "semantic", "memory": "语义搜索不应被调用", "score": 0.9}
        ],
    )

    result = MemoryRecallService(store).recall(  # type: ignore[arg-type]
        _request("你记得广州那次吗？", "我们之前聊过别的旅行")
    )

    assert store.list_calls == [300]
    assert store.search_calls == []
    assert len(result.fragments) == 1
    assert "广州塔" in result.fragments[0].content
    assert "轻音乐" not in result.fragments[0].content


def test_explicit_recall_matches_name_across_chinese_query_boundaries() -> None:
    store = FakeMemoryStore(
        local_memories=[
            {"id": "name", "content": "用户名字是 CIKO", "source": "explicit"}
        ]
    )

    result = MemoryRecallService(store).recall(  # type: ignore[arg-type]
        _request("你还记得我的名字吗？")
    )

    assert store.search_calls == []
    assert result.fragments[0].trust == "trusted"
    assert "CIKO" in result.fragments[0].content


def test_explicit_recall_falls_back_to_semantic_search_without_local_hit() -> None:
    store = FakeMemoryStore(
        local_memories=[{"id": "other", "memory": "用户喜欢低糖咖啡"}],
        semantic_memories=[
            {
                "id": "semantic",
                "memory": "用户在广州旅行时参观了博物馆",
                "score": 0.82,
            }
        ],
    )

    result = MemoryRecallService(store).recall(  # type: ignore[arg-type]
        _request("你还记得广州那次吗？")
    )

    assert store.list_calls == [300]
    assert len(store.search_calls) == 1
    assert "广州旅行" in result.fragments[0].content


def test_expired_local_hit_falls_back_to_semantic_search() -> None:
    store = FakeMemoryStore(
        local_memories=[
            {
                "id": "expired",
                "memory": "用户以前去过广州塔",
                "expires_at": "2000-01-01T00:00:00+08:00",
            }
        ],
        semantic_memories=[
            {
                "id": "current",
                "memory": "用户最近计划再次去广州旅行",
                "score": 0.85,
            }
        ],
    )

    result = MemoryRecallService(store).recall(  # type: ignore[arg-type]
        _request("你还记得广州那次吗？")
    )

    assert len(store.search_calls) == 1
    assert len(result.fragments) == 1
    assert "再次去广州" in result.fragments[0].content


def test_normal_chat_does_not_scan_local_memory_or_trigger_on_go_character() -> None:
    store = FakeMemoryStore(
        local_memories=[{"id": "bath", "memory": "用户以前去过温泉"}],
        semantic_memories=[
            {"id": "music", "memory": "用户喜欢安静地听音乐", "score": 0.9}
        ],
    )

    result = MemoryRecallService(store).recall(  # type: ignore[arg-type]
        _request("我去洗澡，等下回来聊")
    )

    assert store.list_calls == []
    assert len(store.search_calls) == 1
    assert "听音乐" in result.fragments[0].content


def test_local_recall_skips_blocking_list_when_memory_is_not_ready() -> None:
    store = FakeMemoryStore(
        ready=False,
        local_memories=[{"id": "name", "memory": "用户名字是 CIKO"}],
        semantic_memories=[],
    )

    MemoryRecallService(store).recall(  # type: ignore[arg-type]
        _request("你还记得我的名字吗？")
    )

    assert store.list_calls == []
    assert len(store.search_calls) == 1
    assert store.search_calls[0]["wait"] is False


def test_explicit_recall_query_uses_current_input_without_recent_dilution() -> None:
    request = _request(
        "你还记得广州那次吗？",
        "上一轮在讨论完全不同的代码重构和桌面窗口问题",
    )

    assert _build_memory_query(request) == "你还记得广州那次吗？"
