from __future__ import annotations

import sqlite3
import threading
from pathlib import Path
from typing import Any
import uuid

import pytest

from app.agent.memory import MemoryStore
from app.agent.memory_curator import (
    DEFAULT_AUTO_MEMORY_TRIGGER_TURNS,
    MemoryCurationState,
    MemoryCurator,
    _entries_for_model,
)
from app.core.cancellation import CancellationToken, OperationCancelled
from app.storage.chat_history import ChatHistoryEntry


def test_curator_adds_memory_from_first_person_view() -> None:
    store = FakeMemoryStore(existing=[{"id": "m1", "content": "主人喜欢猫"}])
    api = FakeCurationApiClient(['{"operations":[{"op":"add","content":"主人默认用中文交流"}]}'])
    curator = MemoryCurator(api, store, system_prompt="我是 Sakura，这是我的人格卡。")

    result = curator.curate_entries([_entry("user", "以后默认中文和我说话")])

    assert result.created == 1
    assert result.returned == 1
    assert result.processed_entries == 1
    assert store.created == [
        {
            "content": "主人默认用中文交流",
            "layer": "semantic",
            "category": "",
            "importance": 0.5,
            "confidence": 0.75,
            "source": "self_curation",
        }
    ]
    # 人格卡注入到第一人称整理的 system prompt。
    assert "我是 Sakura，这是我的人格卡。" in api.calls[0]["system_prompt"]
    # 现有记忆（带 id）注入到 user prompt，供模型对照去重。
    user_content = api.calls[0]["messages"][0]["content"]
    assert "[m1]" in user_content
    assert "主人喜欢猫" in user_content


def test_curator_updates_system_prompt_for_next_curation() -> None:
    store = FakeMemoryStore()
    api = FakeCurationApiClient(['{"operations":[]}', '{"operations":[]}'])
    curator = MemoryCurator(api, store, system_prompt="旧角色人格卡")

    curator.curate_entries([_entry("user", "第一轮对话")])
    curator.set_system_prompt("新角色人格卡")
    curator.curate_entries([_entry("user", "第二轮对话")])

    first_prompt = str(api.calls[0]["system_prompt"])
    second_prompt = str(api.calls[1]["system_prompt"])
    assert "旧角色人格卡" in first_prompt
    assert "新角色人格卡" not in first_prompt
    assert "新角色人格卡" in second_prompt
    assert "旧角色人格卡" not in second_prompt


def test_curator_snapshot_keeps_prompt_and_store_context() -> None:
    active_store = FakeMemoryStore()
    snapshot_store = FakeMemoryStore()
    api = FakeCurationApiClient(['{"operations":[]}'])
    curator = MemoryCurator(api, active_store, system_prompt="旧角色人格卡")

    snapshot = curator.snapshot(memory_store=snapshot_store)
    curator.set_system_prompt("新角色人格卡")

    snapshot.curate_entries([_entry("user", "整理旧角色对话")])

    prompt = str(api.calls[0]["system_prompt"])
    assert "旧角色人格卡" in prompt
    assert "新角色人格卡" not in prompt
    assert snapshot.memory_store is snapshot_store


def test_scoped_memory_store_keeps_scope_after_parent_switch() -> None:
    mem0 = ScopeRecordingMem0()
    root = _runtime_json_path("scoped_memory_store")
    root.mkdir(parents=True, exist_ok=True)
    store = MemoryStore(base_dir=root, scope_id="old-character", memory_client=mem0)
    scoped_store = store.scoped("old-character")

    store.set_scope("new-character")
    scoped_store.list_memories(limit=1)
    scoped_store.create_memory(
        {"content": "旧角色记忆", "source": "self_curation"},
        allow_sensitive=True,
    )

    assert mem0.get_all_calls == [{"user_id": "old-character"}]
    assert mem0.add_calls == [
        {
            "content": "旧角色记忆",
            "user_id": "old-character",
            "metadata_scope": "old-character",
            "infer": False,
        }
    ]


def test_curator_updates_and_deletes_existing_memories() -> None:
    store = FakeMemoryStore(
        existing=[
            {"id": "m1", "content": "主人住在旧地址"},
            {"id": "m2", "content": "一条过时的记忆"},
        ]
    )
    operations = (
        '{"operations":['
        '{"op":"update","id":"m1","content":"主人搬到了新地址"},'
        '{"op":"delete","id":"m2"}'
        ']}'
    )
    api = FakeCurationApiClient([operations])
    curator = MemoryCurator(api, store)

    result = curator.curate_entries([_entry("user", "我搬家了，旧的别记了")])

    assert result.updated == 1
    assert result.archived == 1
    assert result.returned == 2
    assert store.updated == [
        {
            "id": "m1",
            "content": "主人搬到了新地址",
            "layer": "semantic",
            "category": "",
            "importance": 0.5,
            "confidence": 0.75,
            "source": "self_curation",
        }
    ]
    assert store.deleted == [{"id": "m2"}]


def test_curator_ignores_operations_with_unknown_id() -> None:
    """模型幻觉出不存在的 id 时，更新/删除必须被忽略，避免误改误删。"""

    store = FakeMemoryStore(existing=[{"id": "m1", "content": "真实记忆"}])
    operations = (
        '{"operations":['
        '{"op":"delete","id":"ghost"},'
        '{"op":"update","id":"ghost","content":"幻觉内容"}'
        ']}'
    )
    api = FakeCurationApiClient([operations])
    curator = MemoryCurator(api, store)

    result = curator.curate_entries([_entry("user", "随便说说")])

    assert store.deleted == []
    assert store.updated == []
    assert result.updated == 0
    assert result.archived == 0
    assert result.ignored == 2


def test_curator_skips_low_confidence_and_sensitive_candidates() -> None:
    store = FakeMemoryStore()
    operations = (
        '{"operations":['
        '{"op":"add","content":"主人喜欢抹茶","confidence":0.4},'
        '{"op":"add","content":"主人密码是 abc123","confidence":0.9}'
        ']}'
    )
    api = FakeCurationApiClient([operations])
    curator = MemoryCurator(api, store)

    result = curator.curate_entries([_entry("user", "随便记一下")])

    assert store.created == []
    assert result.created == 0
    assert result.ignored == 2


def test_curator_merges_similar_memory_in_same_layer() -> None:
    store = FakeMemoryStore(
        existing=[
            {
                "id": "m1",
                "content": "主人默认使用中文交流",
                "layer": "procedural",
                "category": "preference",
            }
        ]
    )
    operations = (
        '{"operations":['
        '{"op":"add","layer":"procedural","category":"preference",'
        '"content":"主人默认使用简体中文交流","confidence":0.9}'
        ']}'
    )
    api = FakeCurationApiClient([operations])
    curator = MemoryCurator(api, store)

    result = curator.curate_entries([_entry("user", "以后默认简体中文")])

    assert result.created == 0
    assert result.updated == 1
    assert store.created == []
    assert store.updated[0]["id"] == "m1"
    assert store.updated[0]["layer"] == "procedural"


def test_curator_chunks_large_history_into_separate_calls() -> None:
    store = FakeMemoryStore()
    api = FakeCurationApiClient(
        [
            '{"operations":[{"op":"add","content":"第一段事实"}]}',
            '{"operations":[{"op":"add","content":"第二段事实"}]}',
        ]
    )
    curator = MemoryCurator(api, store)

    result = curator.curate_entries([_entry("user", f"偏好 {index}") for index in range(35)])

    # 35 条 > 单块上限 32，应拆成两块各发起一次整理。
    assert len(api.calls) == 2
    assert result.created == 2
    assert result.processed_entries == 35


def test_curator_cancel_stops_after_current_chunk() -> None:
    token = CancellationToken()
    store = FakeMemoryStore()
    api = CancellingCurationApiClient(
        token,
        [
            '{"operations":[{"op":"add","content":"第一段事实"}]}',
            '{"operations":[{"op":"add","content":"第二段事实"}]}',
        ],
    )
    curator = MemoryCurator(api, store)

    with pytest.raises(OperationCancelled):
        curator.curate_entries(
            [_entry("user", f"偏好 {index}") for index in range(35)],
            cancel_checker=token.throw_if_cancelled,
        )

    # 抽取后即检测到取消，第二块不再发起。
    assert len(api.calls) == 1


def test_curator_ignores_non_dialog_entries() -> None:
    store = FakeMemoryStore()
    api = FakeCurationApiClient([])
    curator = MemoryCurator(api, store)

    result = curator.curate_entries([_entry("system", "内部记录")])

    assert result.processed_entries == 1
    assert result.created == 0
    # 没有可整理的对话时不应调用模型。
    assert api.calls == []


def test_curator_without_api_client_skips_quietly() -> None:
    store = FakeMemoryStore()
    curator = MemoryCurator(None, store)

    result = curator.curate_entries([_entry("user", "在吗")])

    assert result.created == 0
    assert result.processed_entries == 1
    assert store.created == []


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


def test_memory_curation_state_waits_until_trigger_turns() -> None:
    state = MemoryCurationState(_runtime_json_path("memory_curation_state"))

    for _ in range(DEFAULT_AUTO_MEMORY_TRIGGER_TURNS - 1):
        state.increment_pending_turns()

    assert state.pending_turns() == DEFAULT_AUTO_MEMORY_TRIGGER_TURNS - 1
    assert state.pending_turns() < DEFAULT_AUTO_MEMORY_TRIGGER_TURNS

    state.increment_pending_turns()

    assert state.pending_turns() == DEFAULT_AUTO_MEMORY_TRIGGER_TURNS


def test_memory_curation_state_consumes_pending_turns_without_advancing_history() -> None:
    state = MemoryCurationState(_runtime_json_path("memory_curation_state_consume"))
    state.mark_processed(12)
    for _ in range(5):
        state.increment_pending_turns()

    state.consume_pending_turns(3)

    snapshot = state.snapshot()
    assert snapshot["processed_history_count"] == 12
    assert snapshot["pending_turns"] == 2


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
        / "temp"
        / "test_runtime"
        / uuid.uuid4().hex
        / name
        / f"{name}.json"
    )


def _runtime_root(name: str) -> Path:
    return (
        Path(__file__).resolve().parents[2]
        / "temp"
        / "test_runtime"
        / uuid.uuid4().hex
        / name
    )


class FakeMemoryStore:
    """记录整理写回操作的轻量替身，便于单测第一人称整理逻辑。"""

    def __init__(self, existing: list[dict[str, Any]] | None = None) -> None:
        self.existing = list(existing or [])
        self.created: list[dict[str, Any]] = []
        self.updated: list[dict[str, Any]] = []
        self.deleted: list[dict[str, Any]] = []

    def list_memories(self, *, limit: int) -> list[dict[str, Any]]:
        return list(self.existing)

    def create_memory(self, arguments, *, allow_sensitive=False, wait=True):  # type: ignore[no-untyped-def]
        self.created.append(dict(arguments))
        return {"ok": True}

    def update_memory(self, arguments, *, allow_sensitive=False):  # type: ignore[no-untyped-def]
        self.updated.append(dict(arguments))
        return {}

    def delete_memory(self, arguments):  # type: ignore[no-untyped-def]
        self.deleted.append(dict(arguments))
        return {}


class FakeCurationApiClient:
    """按调用顺序返回预设整理 JSON 的模型替身。"""

    def __init__(self, responses: list[str]) -> None:
        self.responses = list(responses)
        self.calls: list[dict[str, object]] = []

    def complete_raw(self, system_prompt, messages, **chat_params):  # type: ignore[no-untyped-def]
        index = len(self.calls)
        self.calls.append(
            {
                "system_prompt": system_prompt,
                "messages": messages,
                "chat_params": chat_params,
            }
        )
        if index >= len(self.responses):
            return '{"operations":[]}'
        return self.responses[index]


class CancellingCurationApiClient(FakeCurationApiClient):
    """首次抽取返回后立即触发取消，用于验证整理在块间停止。"""

    def __init__(self, token: CancellationToken, responses: list[str]) -> None:
        super().__init__(responses)
        self.token = token

    def complete_raw(self, system_prompt, messages, **chat_params):  # type: ignore[no-untyped-def]
        raw = super().complete_raw(system_prompt, messages, **chat_params)
        self.token.cancel()
        return raw


class ScopeRecordingMem0:
    def __init__(self) -> None:
        self.get_all_calls: list[dict[str, str]] = []
        self.add_calls: list[dict[str, object]] = []

    def get_all(self, *, filters, top_k):  # type: ignore[no-untyped-def]
        self.get_all_calls.append(dict(filters))
        return []

    def add(self, content, *, user_id, metadata, infer):  # type: ignore[no-untyped-def]
        self.add_calls.append(
            {
                "content": content,
                "user_id": user_id,
                "metadata_scope": metadata.get("scope"),
                "infer": infer,
            }
        )
        return {
            "id": "memory-001",
            "memory": content,
            "metadata": metadata,
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
