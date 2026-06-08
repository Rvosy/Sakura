from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path
import uuid

from app.agent.memory import MemoryStore
from app.agent.memory_curator import (
    DEFAULT_AUTO_MEMORY_TRIGGER_TURNS,
    MemoryCurationState,
    MemoryCurator,
    _entries_for_model,
)
from app.agent.memory_organizer import MemoryOrganizer, parse_memory_organization_plan
from app.storage.chat_history import ChatHistoryEntry


def test_memory_curator_writes_history_through_mem0() -> None:
    fake = FakeMem0()
    store = MemoryStore(
        base_dir=_runtime_root("memory_curator"),
        scope_id="sakura",
        memory_client=fake,
    )
    curator = MemoryCurator(store)

    result = curator.curate_entries([_entry("user", "以后默认中文和我说话")])

    assert result.created == 1
    assert result.processed_entries == 1
    assert fake.calls[0]["infer"] is True
    assert fake.calls[0]["user_id"] == "sakura"
    assert fake.calls[0]["messages"][0]["content"] == "以后默认中文和我说话"


def test_memory_curator_falls_back_when_mem0_returns_no_results() -> None:
    fake = EmptyMem0()
    store = MemoryStore(
        base_dir=_runtime_root("memory_curator_fallback"),
        scope_id="sakura",
        memory_client=fake,
    )
    api_client = FakeFallbackApiClient()
    curator = MemoryCurator(api_client, store)

    result = curator.curate_entries([_entry("user", "明天我妈妈生日")])

    assert result.created == 1
    assert result.returned == 1
    assert result.event_counts == {"FALLBACK_ADD": 1}
    assert fake.calls[0]["infer"] is True
    assert fake.calls[1]["infer"] is False
    assert fake.calls[1]["messages"] == "用户妈妈的生日是6月4日。"
    assert fake.calls[1]["metadata"] == {"source": "curation_fallback"}


def test_memory_curator_chunks_large_history_before_mem0() -> None:
    fake = FakeMem0()
    store = MemoryStore(
        base_dir=_runtime_root("memory_curator_chunks"),
        scope_id="sakura",
        memory_client=fake,
    )
    curator = MemoryCurator(store)

    result = curator.curate_entries([_entry("user", f"偏好 {index}") for index in range(35)])

    assert result.created == 2
    assert result.returned == 2
    assert result.processed_entries == 35
    assert [len(call["messages"]) for call in fake.calls] == [32, 3]


def test_memory_delete_resets_mem0_curation_cache_for_current_scope() -> None:
    fake = FakeMem0WithCurationCache()
    store = MemoryStore(
        base_dir=_runtime_root("memory_delete_cache"),
        scope_id="sakura",
        memory_client=fake,
    )
    fake.insert_message("user_id=sakura", "user", "旧上下文")
    fake.insert_message("user_id=other", "user", "其它角色上下文")
    fake.insert_history("memory-001", "ADD")
    fake.insert_history("memory-other", "ADD")

    result = store.forget_memory({"id": "memory-001"})

    assert result["curation_cache_reset"] == {"messages": 1, "history": 1}
    assert fake.deleted == ["memory-001"]
    assert fake.count_messages("user_id=sakura") == 0
    assert fake.count_messages("user_id=other") == 1
    assert fake.count_history("memory-001") == 0
    assert fake.count_history("memory-other") == 1


def test_memory_curator_ignores_non_dialog_entries() -> None:
    fake = FakeMem0()
    store = MemoryStore(
        base_dir=_runtime_root("memory_curator_empty"),
        memory_client=fake,
    )
    curator = MemoryCurator(store)

    result = curator.curate_entries([_entry("system", "内部记录")])

    assert result.processed_entries == 1
    assert result.created == 0
    assert fake.calls == []


def test_memory_organization_plan_parses_standard_json() -> None:
    raw = """
    {
      "actions": [
        {
          "action": "update",
          "id": "memory-001",
          "content": "主人喜欢精简、信息密度高的设置界面。",
          "reason": "合并重复偏好",
          "related_ids": ["memory-002"]
        },
        {
          "action": "delete",
          "id": "memory-002",
          "content": "主人喜欢精简界面。",
          "reason": "已并入 memory-001"
        },
        {
          "action": "keep",
          "id": "memory-003",
          "content": "主人默认使用中文沟通。",
          "reason": "独立有效事实"
        }
      ]
    }
    """

    plan = parse_memory_organization_plan(
        raw,
        source_memories=[
            {"id": "memory-001", "content": "主人喜欢精简设置界面。"},
            {"id": "memory-002", "content": "主人喜欢精简界面。"},
            {"id": "memory-003", "content": "主人默认使用中文沟通。"},
        ],
    )

    assert len(plan.updates) == 1
    assert len(plan.deletes) == 1
    assert len(plan.keeps) == 1
    assert plan.updates[0].memory_id == "memory-001"
    assert plan.updates[0].related_ids == ("memory-002",)


def test_memory_organization_plan_filters_invalid_actions() -> None:
    raw = """
    {
      "actions": [
        {"action": "merge", "id": "memory-001", "content": "无效动作"},
        {"action": "update", "content": "缺少 ID"},
        {"action": "update", "id": "unknown", "content": "未知 ID"},
        {"action": "delete", "id": "memory-002", "content": ""},
        {"action": "keep", "id": "memory-001", "content": "有效保留"}
      ]
    }
    """

    plan = parse_memory_organization_plan(
        raw,
        source_memories=[
            {"id": "memory-001", "content": "有效保留"},
            {"id": "memory-002", "content": ""},
        ],
    )

    assert [action.memory_id for action in plan.actions] == ["memory-001"]
    assert plan.keeps[0].content == "有效保留"


def test_memory_organization_plan_parses_markdown_json_block() -> None:
    raw = """```json
    {"actions":[{"action":"delete","id":"memory-001","content":"重复记忆","reason":"重复"}]}
    ```"""

    plan = parse_memory_organization_plan(
        raw,
        source_memories=[{"id": "memory-001", "content": "重复记忆"}],
    )

    assert len(plan.deletes) == 1
    assert plan.deletes[0].reason == "重复"


def test_memory_organization_plan_parses_raw_action_list() -> None:
    raw = """[
      {"action":"keep","id":"memory-001","content":"主人默认使用中文沟通。","reason":"独立有效事实"}
    ]"""

    plan = parse_memory_organization_plan(
        raw,
        source_memories=[{"id": "memory-001", "content": "主人默认使用中文沟通。"}],
    )

    assert len(plan.keeps) == 1
    assert plan.keeps[0].memory_id == "memory-001"


def test_memory_organizer_chunks_long_memory_list() -> None:
    memories = [
        {"id": f"memory-{index:03d}", "content": f"分块记忆 {index:03d}"}
        for index in range(25)
    ]
    api_client = ChunkEchoApiClient()

    plan = MemoryOrganizer(api_client).organize_memories(memories)

    assert len(api_client.calls) == 2
    assert [len(call["memories"]) for call in api_client.calls] == [24, 1]
    assert plan.source_count == 25
    assert len(plan.keeps) == 25


def test_memory_organizer_retries_plain_request_after_structured_parse_failure() -> None:
    memories = [{"id": "memory-001", "content": "主人喜欢精简界面"}]
    api_client = PlainRetryApiClient()

    plan = MemoryOrganizer(api_client).organize_memories(memories)

    assert len(api_client.calls) == 2
    assert "response_format" in api_client.calls[0]["chat_params"]
    assert "response_format" not in api_client.calls[1]["chat_params"]
    assert len(plan.updates) == 1
    assert plan.updates[0].content == "主人喜欢精简、信息密度高的界面。"


def test_memory_organizer_treats_natural_no_change_response_as_keep() -> None:
    memories = [
        {"id": "memory-001", "content": "主人默认使用中文沟通"},
        {"id": "memory-002", "content": "主人喜欢精简界面"},
    ]
    api_client = NaturalNoChangeApiClient()

    plan = MemoryOrganizer(api_client).organize_memories(memories)

    assert len(api_client.calls) == 1
    assert plan.warnings == ()
    assert len(plan.keeps) == 2
    assert {action.memory_id for action in plan.keeps} == {"memory-001", "memory-002"}


def test_memory_organizer_keeps_failed_chunk_and_continues() -> None:
    memories = [
        {"id": f"memory-{index:03d}", "content": f"分块记忆 {index:03d}"}
        for index in range(25)
    ]
    api_client = OneBadChunkApiClient()

    plan = MemoryOrganizer(api_client).organize_memories(memories)

    assert len(api_client.calls) > 2
    assert len(plan.warnings) == 4
    assert all("返回格式无效" in warning for warning in plan.warnings)
    assert len(plan.keeps) == 24
    assert len(plan.updates) == 1
    assert plan.updates[0].memory_id == "memory-024"
    assert plan.updates[0].content == "分块记忆 024，已整理。"


def test_memory_curation_state_waits_until_trigger_turns() -> None:
    state = MemoryCurationState(_runtime_json_path("memory_curation_state"))

    for _ in range(DEFAULT_AUTO_MEMORY_TRIGGER_TURNS - 1):
        state.increment_pending_turns()

    assert state.pending_turns() == DEFAULT_AUTO_MEMORY_TRIGGER_TURNS - 1
    assert state.pending_turns() < DEFAULT_AUTO_MEMORY_TRIGGER_TURNS

    state.increment_pending_turns()

    assert state.pending_turns() == DEFAULT_AUTO_MEMORY_TRIGGER_TURNS


def test_memory_entries_ignore_tone_and_portrait_metadata() -> None:
    entries = _entries_for_model(
        [
            ChatHistoryEntry(
                created_at="2026-05-31T12:00:00+08:00",
                role="assistant",
                content="覚えておくね。",
                translation="我会记住。",
                tone="中性",
                portrait="站立待机",
            )
        ]
    )

    assert entries == [
        {
            "created_at": "2026-05-31T12:00:00+08:00",
            "role": "assistant",
            "content": "覚えておくね。",
            "translation": "我会记住。",
        }
    ]


def test_mem0_openai_llm_retries_empty_structured_response(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
        monkeypatch.delenv(key, raising=False)

    from mem0.llms.openai import OpenAILLM

    llm = OpenAILLM({"api_key": "test-key", "model": "test-model"})
    fake_client = FakeOpenAIClient()
    llm.client = fake_client

    response = llm.generate_response(
        messages=[{"role": "user", "content": "Return JSON"}],
        response_format={"type": "json_object"},
    )

    assert response == '{"memory":[]}'
    assert len(fake_client.chat.completions.calls) == 2
    assert "response_format" in fake_client.chat.completions.calls[0]
    assert "response_format" not in fake_client.chat.completions.calls[1]


def _entry(role: str, content: str) -> ChatHistoryEntry:
    return ChatHistoryEntry(
        created_at="2026-05-31T12:00:00+08:00",
        role=role,
        content=content,
    )


def _runtime_json_path(name: str) -> Path:
    return (
        Path(__file__).resolve().parents[2]
        / "__pycache__"
        / "test_runtime"
        / name
        / uuid.uuid4().hex
        / f"{name}.json"
    )


def _runtime_root(name: str) -> Path:
    return (
        Path(__file__).resolve().parents[2]
        / "__pycache__"
        / "test_runtime"
        / name
        / uuid.uuid4().hex
    )


class FakeMem0:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def add(self, messages, *, user_id=None, infer=True, metadata=None):  # type: ignore[no-untyped-def]
        self.calls.append(
            {
                "messages": messages,
                "user_id": user_id,
                "infer": infer,
                "metadata": metadata,
            }
        )
        return {
            "results": [
                {
                    "id": "mem1",
                    "memory": "主人希望默认用中文沟通",
                    "user_id": user_id,
                    "event": "ADD",
                }
            ]
        }


class EmptyMem0:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def add(self, messages, *, user_id=None, infer=True, metadata=None):  # type: ignore[no-untyped-def]
        self.calls.append(
            {
                "messages": messages,
                "user_id": user_id,
                "infer": infer,
                "metadata": metadata,
            }
        )
        if infer:
            return {"results": []}
        return {
            "results": [
                {
                    "id": "fallback-1",
                    "memory": messages,
                    "user_id": user_id,
                    "event": "ADD",
                }
            ]
        }


class FakeMem0WithCurationCache:
    def __init__(self) -> None:
        self.records: dict[str, dict[str, str]] = {
            "memory-001": {"id": "memory-001", "memory": "第一条记忆"},
        }
        self.deleted: list[str] = []
        self.db = FakeMem0Db()

    def get(self, memory_id):  # type: ignore[no-untyped-def]
        return self.records.get(memory_id)

    def delete(self, memory_id):  # type: ignore[no-untyped-def]
        self.deleted.append(memory_id)
        self.records.pop(memory_id, None)

    def insert_message(self, session_scope: str, role: str, content: str) -> None:
        self.db.connection.execute(
            "INSERT INTO messages (id, session_scope, role, content, name, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (str(uuid.uuid4()), session_scope, role, content, None, "2026-06-05T00:00:00+00:00"),
        )
        self.db.connection.commit()

    def insert_history(self, memory_id: str, event: str) -> None:
        self.db.connection.execute(
            "INSERT INTO history (id, memory_id, old_memory, new_memory, event, created_at, updated_at, is_deleted, actor_id, role) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                str(uuid.uuid4()),
                memory_id,
                None,
                "记忆",
                event,
                "2026-06-05T00:00:00+00:00",
                None,
                0,
                None,
                "user",
            ),
        )
        self.db.connection.commit()

    def count_messages(self, session_scope: str) -> int:
        return int(
            self.db.connection.execute(
                "SELECT COUNT(*) FROM messages WHERE session_scope = ?",
                (session_scope,),
            ).fetchone()[0]
        )

    def count_history(self, memory_id: str) -> int:
        return int(
            self.db.connection.execute(
                "SELECT COUNT(*) FROM history WHERE memory_id = ?",
                (memory_id,),
            ).fetchone()[0]
        )


class FakeMem0Db:
    def __init__(self) -> None:
        self.connection = sqlite3.connect(":memory:")
        self._lock = threading.Lock()
        self.connection.execute(
            """
            CREATE TABLE messages (
                id TEXT PRIMARY KEY,
                session_scope TEXT,
                role TEXT,
                content TEXT,
                name TEXT,
                created_at DATETIME
            )
            """
        )
        self.connection.execute(
            """
            CREATE TABLE history (
                id TEXT PRIMARY KEY,
                memory_id TEXT,
                old_memory TEXT,
                new_memory TEXT,
                event TEXT,
                created_at DATETIME,
                updated_at DATETIME,
                is_deleted INTEGER,
                actor_id TEXT,
                role TEXT
            )
            """
        )
        self.connection.commit()


class FakeFallbackApiClient:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def complete_raw(self, system_prompt, messages, temperature=0.8, **chat_params):  # type: ignore[no-untyped-def]
        self.calls.append(
            {
                "system_prompt": system_prompt,
                "messages": messages,
                "temperature": temperature,
                "chat_params": chat_params,
            }
        )
        return '{"memories":["用户妈妈的生日是6月4日。"]}'


class ChunkEchoApiClient:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def complete_raw(self, _system_prompt, messages, temperature=0.8, **chat_params):  # type: ignore[no-untyped-def]
        payload = _memory_organization_prompt_payload(messages)
        memories = payload["memories"]
        self.calls.append(
            {
                "chunk_index": payload["chunk_index"],
                "chunk_count": payload["chunk_count"],
                "memories": memories,
                "temperature": temperature,
                "chat_params": chat_params,
            }
        )
        actions = [
            {
                "action": "keep",
                "id": memory["id"],
                "content": memory["content"],
                "reason": "测试保留",
            }
            for memory in memories
        ]
        return json.dumps({"actions": actions}, ensure_ascii=False)


class PlainRetryApiClient(ChunkEchoApiClient):
    def complete_raw(self, _system_prompt, messages, temperature=0.8, **chat_params):  # type: ignore[no-untyped-def]
        payload = _memory_organization_prompt_payload(messages)
        memories = payload["memories"]
        self.calls.append(
            {
                "chunk_index": payload["chunk_index"],
                "chunk_count": payload["chunk_count"],
                "memories": memories,
                "temperature": temperature,
                "chat_params": chat_params,
            }
        )
        if "response_format" in chat_params:
            return "这是自然语言，不是 JSON。"
        return json.dumps(
            {
                "actions": [
                    {
                        "action": "update",
                        "id": memories[0]["id"],
                        "content": "主人喜欢精简、信息密度高的界面。",
                        "reason": "普通请求重试后返回有效 JSON",
                    }
                ]
            },
            ensure_ascii=False,
        )


class NaturalNoChangeApiClient(ChunkEchoApiClient):
    def complete_raw(self, _system_prompt, messages, temperature=0.8, **chat_params):  # type: ignore[no-untyped-def]
        payload = _memory_organization_prompt_payload(messages)
        memories = payload["memories"]
        self.calls.append(
            {
                "chunk_index": payload["chunk_index"],
                "chunk_count": payload["chunk_count"],
                "memories": memories,
                "temperature": temperature,
                "chat_params": chat_params,
            }
        )
        return "未发现明显重复或冲突，建议全部保留。"


class OneBadChunkApiClient(ChunkEchoApiClient):
    def complete_raw(self, _system_prompt, messages, temperature=0.8, **chat_params):  # type: ignore[no-untyped-def]
        payload = _memory_organization_prompt_payload(messages)
        memories = payload["memories"]
        self.calls.append(
            {
                "chunk_index": payload["chunk_index"],
                "chunk_count": payload["chunk_count"],
                "memories": memories,
                "temperature": temperature,
                "chat_params": chat_params,
            }
        )
        if payload["chunk_index"] == 1:
            return "这不是 JSON"
        actions = [
            {
                "action": "update",
                "id": memories[0]["id"],
                "content": f"{memories[0]['content']}，已整理。",
                "reason": "测试更新",
            }
        ]
        return json.dumps({"actions": actions}, ensure_ascii=False)


def _memory_organization_prompt_payload(messages) -> dict[str, object]:  # type: ignore[no-untyped-def]
    content = str(messages[0]["content"])
    return json.loads(content.split("\n\n", 1)[1])


class FakeOpenAIClient:
    def __init__(self) -> None:
        completions = FakeChatCompletions()
        self.chat = type("FakeChat", (), {"completions": completions})()


class FakeChatCompletions:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def create(self, **params):  # type: ignore[no-untyped-def]
        self.calls.append(params)
        content = "" if "response_format" in params else '{"memory":[]}'
        return _fake_openai_response(content)


def _fake_openai_response(content: str):  # type: ignore[no-untyped-def]
    message = type("FakeMessage", (), {"content": content, "tool_calls": None})()
    choice = type("FakeChoice", (), {"message": message})()
    return type("FakeResponse", (), {"choices": [choice]})()
