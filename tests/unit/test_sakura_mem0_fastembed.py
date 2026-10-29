from __future__ import annotations

import threading
import time
from pathlib import Path

import numpy as np
import pytest
from fastembed import TextEmbedding

from plugins.optional.sakura_mem0.memory import MemoryStore
from plugins.optional.sakura_mem0 import vector_index


class Embedding(TextEmbedding):
    failure = False
    entered = None
    resume = None

    def __init__(self, *args, **kwargs):
        pass

    def embed(self, texts):
        if self.entered is not None:
            self.entered.set()
            assert self.resume.wait(5)
        if self.failure:
            raise RuntimeError("模型读取失败")
        for text in texts:
            vector = np.zeros(512)
            vector[0 if "咖啡" in text else 1] = 1.0
            yield vector


@pytest.fixture
def indexed(tmp_path, monkeypatch):
    import fastembed
    import qdrant_client
    monkeypatch.setattr(fastembed, "TextEmbedding", Embedding)
    monkeypatch.setattr(vector_index, "_embedding_model_snapshot", lambda **_: Path("model"))
    monkeypatch.setattr(Embedding, "failure", False)
    monkeypatch.setattr(Embedding, "entered", None)
    monkeypatch.setattr(Embedding, "resume", None)
    store = MemoryStore(tmp_path, "Sakura")
    yield store
    store.close()


def await_state(store, state):
    deadline = time.monotonic() + 5
    while store.index_status()["state"] != state and time.monotonic() < deadline:
        time.sleep(.01)
    assert store.index_status()["state"] == state, store.index_status()


def test_acknowledgment_does_not_recall_friends(indexed):
    indexed.create_memory({"content": "用户说小樱是自己的好朋友"})
    await_state(indexed, "ready")
    for query in ("好的", "嗯", "谢谢"):
        assert indexed.search_memory({"query": query, "automatic": True})["memories"] == []
    assert indexed.search_memory({"query": "好朋友", "automatic": True})["memories"]


def test_inapplicable_preferences_leave_chat_but_remain_searchable(indexed):
    item = indexed.create_memory({"content": "咖啡不加糖", "layer": "core_profile", "pinned": True})["memory"]
    await_state(indexed, "ready")
    assert indexed.profile_memories()[0]["id"] == item["id"]
    indexed.update_memory({"id": item["id"], "state": "cancelled"})
    await_state(indexed, "ready")
    assert indexed.profile_memories() == []
    assert indexed.search_memory({"query": "咖啡不加糖", "automatic": True})["memories"] == []
    assert indexed.search_memory({"query": "咖啡不加糖"})["memories"][0]["id"] == item["id"]


def test_failed_index_keeps_text_and_can_rebuild(indexed, monkeypatch):
    monkeypatch.setattr(Embedding, "failure", True)
    item = indexed.create_memory({"content": "咖啡不加糖"})["memory"]
    await_state(indexed, "failed")
    indexed._index.thread.join()
    assert indexed.search_memory({"query": "咖啡不加糖"})["memories"][0]["id"] == item["id"]
    assert indexed.index_rows()
    monkeypatch.setattr(Embedding, "failure", False)
    indexed.rebuild_index()
    await_state(indexed, "ready")
    assert not indexed.index_rows()
    assert indexed._index.search("咖啡", "Sakura", 5)[0]["id"] == item["id"]


def test_rebuild_catches_edits_committed_during_embedding(indexed, monkeypatch):
    entered, resume = threading.Event(), threading.Event()
    monkeypatch.setattr(Embedding, "entered", entered)
    monkeypatch.setattr(Embedding, "resume", resume)
    item = indexed.create_memory({"content": "咖啡不加糖"})["memory"]
    assert entered.wait(5)
    updated = indexed.update_memory({"id": item["id"], "content": "现在喝茶"})["memory"]
    resume.set()
    await_state(indexed, "ready")
    hit = indexed._index.search("喝茶", "Sakura", 5)[0]
    assert hit["id"] == item["id"] and hit["revision"] == updated["revision"]
    assert not indexed.index_rows()


def test_temporary_vector_search_failure_can_recover_on_next_query(indexed, monkeypatch):
    item = indexed.create_memory({"content": "咖啡不加糖"})["memory"]
    await_state(indexed, "ready")
    query_points = indexed._index.client.query_points

    def unavailable(*args, **kwargs):
        raise RuntimeError("暂时无法读取向量索引")

    monkeypatch.setattr(indexed._index.client, "query_points", unavailable)
    assert indexed._index.search("咖啡", "Sakura", 5) == []
    assert indexed.search_memory({"query": "咖啡"})["memories"][0]["id"] == item["id"]
    monkeypatch.setattr(indexed._index.client, "query_points", query_points)
    assert indexed._index.search("咖啡", "Sakura", 5)[0]["id"] == item["id"]


def test_reopen_reuses_index_and_deleted_records_do_not_return(indexed, tmp_path):
    item = indexed.create_memory({"content": "咖啡不加糖"})["memory"]
    await_state(indexed, "ready")
    collection = indexed.index_status()["collection"]
    indexed.forget_memory({"id": item["id"]})
    indexed.close()
    reopened = MemoryStore(tmp_path, "Sakura")
    try:
        reopened.preload()
        await_state(reopened, "ready")
        assert reopened.index_status()["collection"] == collection
        assert reopened.search_memory({"query": "咖啡"})["memories"] == []
        assert reopened._index.search("咖啡", "Other", 5) == []
    finally:
        reopened.close()
