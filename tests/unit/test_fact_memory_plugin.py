from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import threading
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from plugins.optional.fact_memory.plugin import (
    FactMemoryPlugin,
    FactMemoryRuntime,
    MAX_RECALL_CHARS,
    MAX_RECALL_ITEMS,
)


@pytest.fixture
def memory(tmp_path: Path):
    character = {"id": "character-a"}
    runtime = FactMemoryRuntime(tmp_path / "facts.sqlite3", lambda: character)
    try:
        yield runtime, character
    finally:
        runtime.close()


def test_collection_crud_search_pagination_and_persistence(memory, tmp_path: Path) -> None:
    runtime, character = memory
    first = runtime.create({"content": "我不吃香菜", "keywords": "香菜、忌口"})
    second = runtime.create({"content": " Project Sakura 使用 Python "})
    first_page = runtime.query({"limit": 1})
    second_page = runtime.query({"limit": 1, "cursor": first_page["nextCursor"]})
    assert first_page["total"] == second_page["total"] == 2
    assert second_page["nextCursor"] is None
    assert {item["itemId"] for item in first_page["items"] + second_page["items"]} == {
        first["itemId"], second["itemId"],
    }
    assert runtime.query({"search": "忌口"})["items"] == [first]
    assert runtime.query({"search": "sakura"})["items"][0]["values"]["content"] == "Project Sakura 使用 Python"

    updated = runtime.update(first["itemId"], {"content": "我可以吃香菜"})
    assert updated["values"]["keywords"] == "香菜、忌口"
    assert runtime.search_tool({"query": "忌口"})["facts"][0]["content"] == "我可以吃香菜"
    runtime.close()
    with pytest.raises(RuntimeError, match="FACT_MEMORY_CLOSED"):
        runtime.query({})

    reopened = FactMemoryRuntime(tmp_path / "facts.sqlite3", lambda: character)
    try:
        assert reopened.query({"search": "忌口"})["items"] == [updated]
        assert reopened.delete(first["itemId"]) == {"deleted": True}
        assert reopened.delete(first["itemId"]) == {"deleted": False}
        assert reopened.search_tool({"query": "忌口"}) == {"facts": []}
        assert reopened.context({"character_id": character["id"], "current_input": "有什么忌口？"}) == []
        assert reopened.query({})["items"] == [second]
    finally:
        reopened.close()


def test_character_partition_applies_to_collection_tool_context_and_item_ids(memory) -> None:
    runtime, character = memory
    first = runtime.create({"content": "A 角色：不吃香菜", "keywords": "忌口"})
    character["id"] = "character-b"
    assert runtime.query({})["items"] == []
    assert runtime.search_tool({"query": "忌口"}) == {"facts": []}
    assert runtime.context({"character_id": "character-b", "current_input": "忌口"}) == []
    with pytest.raises(ValueError, match="FACT_MEMORY_NOT_FOUND"):
        runtime.update(first["itemId"], {"content": "跨角色改写"})
    assert runtime.delete(first["itemId"]) == {"deleted": False}
    second = runtime.create({"content": "B 角色：不吃洋葱", "keywords": "忌口"})

    # A stale or mismatched request must not select another character's facts.
    assert runtime.context({"character_id": "character-a", "current_input": "忌口"}) == []
    assert runtime.search_tool({"query": "忌口"})["facts"][0]["id"] == second["itemId"]
    character["id"] = "character-a"
    assert runtime.query({})["items"] == [first]
    fragment, = runtime.context({"character_id": "character-a", "current_input": "忌口"})
    assert "不吃香菜" in fragment["content"]
    assert "不吃洋葱" not in fragment["content"]
    assert runtime.context({"current_input": "忌口"}) == []


def test_explicit_search_finds_body_even_when_context_requires_keywords(memory) -> None:
    runtime, character = memory
    item = runtime.create({"content": "项目验收编号是 24680", "keywords": "验收项目"})

    assert runtime.search_tool({"query": " 24680 "})["facts"][0]["id"] == item["itemId"]
    assert runtime.search_tool({"query": "验收项目"})["facts"][0]["id"] == item["itemId"]
    assert runtime.context({"character_id": character["id"], "current_input": "24680"}) == []
    assert runtime.context({"character_id": character["id"], "current_input": "验收项目"})


@pytest.mark.parametrize("invalid", [
    {"content": " \n "},
    {"content": 42},
    {"content": "甲" * 1001},
    {"content": "有效内容", "keywords": "乙" * 201},
    {"content": "有效内容", "character_id": "character-b"},
], ids=["blank", "wrong-type", "content-limit", "keywords-limit", "unknown-field"])
def test_invalid_writes_leave_existing_data_unchanged(memory, invalid) -> None:
    runtime, _character = memory
    item = runtime.create({"content": "有效便签", "keywords": "检查"})
    before = runtime.query({})
    with pytest.raises(ValueError, match="FACT_MEMORY_VALUES_INVALID"):
        runtime.create(invalid)
    with pytest.raises(ValueError, match="FACT_MEMORY_VALUES_INVALID"):
        runtime.update(item["itemId"], invalid)
    assert runtime.query({}) == before


def test_keyword_recall_takes_priority_and_does_not_fall_back_to_content(memory) -> None:
    runtime, character = memory
    runtime.create({"content": "我不吃香菜", "keywords": "香菜、饮食；忌口"})
    runtime.create({"content": "讨论忌口和饮食的便签", "keywords": "咖啡"})
    runtime.create({"content": "忌口和饮食需要提前确认"})
    fragment, = runtime.context({"character_id": character["id"], "current_input": "饮食有什么忌口？"})
    assert fragment["content"].index("我不吃香菜") < fragment["content"].index("忌口和饮食需要提前确认")
    assert "讨论忌口和饮食的便签" not in fragment["content"]
    assert runtime.context({"character_id": character["id"], "current_input": "天气如何"}) == []


def test_content_fallback_matches_chinese_pairs_and_whole_english_words(memory) -> None:
    runtime, character = memory
    runtime.create({"content": "猫耳项目下周交付"})
    runtime.create({"content": "I drink tea after lunch"})
    runtime.create({"content": "The steam engine needs maintenance"})
    chinese, = runtime.context({"character_id": character["id"], "current_input": "猫耳进度"})
    english, = runtime.context({"character_id": character["id"], "current_input": "TEA"})
    assert "猫耳项目下周交付" in chinese["content"]
    assert "I drink tea after lunch" in english["content"]
    assert "steam" not in english["content"]
    assert runtime.context({"character_id": character["id"], "current_input": "coffee"}) == []


def test_recall_budget_skips_whole_items_and_respects_item_limits(memory) -> None:
    runtime, character = memory
    expected = []
    for length, keywords, char in (
        (900, "alpha,beta,gamma,delta", "甲"),
        (900, "alpha,beta,gamma", "乙"),
        (500, "alpha,beta", "丙"),
        (100, "alpha", "丁"),
    ):
        runtime.create({"content": char * length, "keywords": keywords})
        if char != "丙":
            expected.append(char * length)
    query = "alpha beta gamma delta"
    result = runtime.search_tool({"query": "alpha"})["facts"]
    assert sum(len(item["content"]) for item in result) <= MAX_RECALL_CHARS
    assert all(len(item["content"]) in {900, 500, 100} for item in result)
    fragment, = runtime.context({"character_id": character["id"], "current_input": query})
    assert all(content in fragment["content"] for content in expected)
    assert "丙" not in fragment["content"]

    character["id"] = "many-short-facts"
    for index in range(7):
        runtime.create({"content": f"便签 {index}", "keywords": "数量"})
    assert len(runtime.search_tool({"query": "数量"})["facts"]) == MAX_RECALL_ITEMS
    assert len(runtime.search_tool({"query": "数量", "limit": 2})["facts"]) == 2
    for invalid in ({"query": " "}, {"query": "数" * 201}, {"query": "数量", "limit": True}, {"query": "数量", "limit": 6}):
        with pytest.raises(ValueError, match="FACT_MEMORY_SEARCH_INVALID"):
            runtime.search_tool(invalid)


def test_parallel_callbacks_persist_without_cross_thread_connection_errors(memory) -> None:
    runtime, _character = memory
    ready = threading.Barrier(4)

    def worker(index: int) -> str:
        ready.wait(timeout=5)
        item = runtime.create({"content": f"并发便签 {index}", "keywords": f"key-{index}"})
        runtime.update(item["itemId"], {"content": f"已更新便签 {index}"})
        result = runtime.search_tool({"query": f"key-{index}"})["facts"]
        assert result[0]["content"] == f"已更新便签 {index}"
        return item["itemId"]

    with ThreadPoolExecutor(max_workers=4) as pool:
        item_ids = set(pool.map(worker, range(4)))
    assert {item["itemId"] for item in runtime.query({})["items"]} == item_ids


def test_registration_and_cleanup_do_not_require_a_ready_character(tmp_path: Path) -> None:
    character = SimpleNamespace(current=Mock(side_effect=RuntimeError("CHARACTER_NOT_FOUND")))
    services = {
        "sakura.host.character": character,
        "sakura.host.context": SimpleNamespace(
            describe=lambda: {"schemaVersion": 2, "scopes": ["turn"], "failurePolicies": ["skip"]},
            register=Mock(),
        ),
        "sakura.host.settings": SimpleNamespace(register=Mock()),
        "sakura.host.settings.collection-v0": SimpleNamespace(register=Mock()),
        "sakura.host.tools": SimpleNamespace(register=Mock()),
    }
    effects = []
    context = SimpleNamespace(
        plugin_id="fact_memory", get=services.__getitem__,
        data_path=lambda name: tmp_path / name, effect=effects.append,
    )
    FactMemoryPlugin().setup(context)
    try:
        character.current.assert_not_called()
        callbacks = services["sakura.host.settings.collection-v0"].register.call_args.kwargs
        with pytest.raises(RuntimeError, match="CHARACTER_NOT_FOUND"):
            callbacks["query"]({})
        character.current.side_effect = None
        character.current.return_value = {"id": "character-a"}
        item = callbacks["create"]({"content": "首次启动后补选角色"})
        assert callbacks["query"]({})["items"] == [item]
    finally:
        for close in reversed(effects):
            close()
    with pytest.raises(RuntimeError, match="FACT_MEMORY_CLOSED"):
        callbacks["query"]({})
