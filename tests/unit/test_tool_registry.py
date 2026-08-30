"""tests/unit/test_tool_registry.py — 统一工具注册系统测试。

覆盖：
- Tool / ToolMetadata / ToolExecutionResult
- ToolRegistry 注册 / 查询 / 描述 / 执行
- search_tools / active_groups / capability filtering
"""

from __future__ import annotations

import pytest

from app.agent.tools import (
    Tool,
    ToolExecutionResult,
    ToolMetadata,
    ToolRegistry,
)


def _dummy_tool(name: str, **kwargs: object) -> Tool:
    defaults: dict[str, object] = {
        "description": f"Tool {name}",
        "parameters": {"type": "object", "properties": {}, "required": []},
        "handler": lambda args: {"ok": True},
        "group": "default",
        "risk": "low",
    }
    defaults.update(kwargs)
    return Tool(name=name, **defaults)


class TestToolMetadata:
    """ToolMetadata 统一元数据测试"""

    def test_from_tool(self) -> None:
        tool = _dummy_tool("test", description="测试工具", group="memory", risk="medium")
        meta = ToolMetadata.from_tool(tool)
        assert meta.name == "test"
        assert meta.description == "测试工具"
        assert meta.group == "memory"
        assert meta.risk == "medium"
        assert meta.source == "builtin"

    def test_tool_metadata_property(self) -> None:
        tool = _dummy_tool("test")
        assert tool.metadata.name == "test"


class TestToolRegistryBasics:
    """ToolRegistry 基本操作"""

    def test_register_and_get(self) -> None:
        registry = ToolRegistry()
        tool = _dummy_tool("test_tool")
        registry.register(tool)
        assert registry.get("test_tool") is tool

    def test_register_overwrites(self) -> None:
        registry = ToolRegistry()
        t1 = _dummy_tool("test", description="old")
        t2 = _dummy_tool("test", description="new")
        registry.register(t1)
        registry.register(t2)
        assert registry.get("test").description == "new"

    def test_all(self) -> None:
        registry = ToolRegistry()
        registry.register(_dummy_tool("a"))
        registry.register(_dummy_tool("b"))
        assert len(registry.all()) == 2

    def test_get_unknown(self) -> None:
        registry = ToolRegistry()
        assert registry.get("unknown") is None

    def test_groups(self) -> None:
        registry = ToolRegistry()
        registry.register(_dummy_tool("a", group="default"))
        registry.register(_dummy_tool("b", group="memory"))
        assert registry.groups() == {"default", "memory"}


class TestToolRegistryDescribe:
    """工具描述 (模型可见)"""

    def test_describe_tools_basic(self) -> None:
        registry = ToolRegistry([_dummy_tool("test")])
        tools = registry.describe_tools()
        assert len(tools) == 1
        assert tools[0]["name"] == "test"

    def test_capability_filtering(self) -> None:
        """capability 过滤：有 capability 的工具仅在允许时可见。"""
        registry = ToolRegistry([
            _dummy_tool("normal"),
            _dummy_tool("screen", capability="screen_observation"),
        ])
        # 不允许 screen_observation
        tools = registry.describe_tools(allowed_capabilities=set())
        names = {t["name"] for t in tools}
        assert "normal" in names
        assert "screen" not in names

        # 允许 screen_observation
        tools = registry.describe_tools(allowed_capabilities={"screen_observation"})
        names = {t["name"] for t in tools}
        assert "screen" in names

    def test_active_groups_filtering(self) -> None:
        """active_groups 过滤：不在 active_groups 中的工具不可见。"""
        registry = ToolRegistry([
            _dummy_tool("a", group="default"),
            _dummy_tool("b", group="memory"),
        ])
        tools = registry.describe_tools(active_groups={"default"})
        names = {t["name"] for t in tools}
        assert "a" in names
        assert "b" not in names

    def test_capability_overrides_active_groups(self) -> None:
        """有 capability 且在 allowed_capabilities 中的工具，即使不在 active_groups 也可见。"""
        registry = ToolRegistry([
            _dummy_tool("screen", capability="screen_observation", group="screen"),
        ])
        tools = registry.describe_tools(
            allowed_capabilities={"screen_observation"},
            active_groups={"default"},
        )
        names = {t["name"] for t in tools}
        # screen_observation capability 允许，即使 screen 不在 active_groups
        assert "screen" in names

    def test_describe_openai_tools(self) -> None:
        registry = ToolRegistry([_dummy_tool("test")])
        tools = registry.describe_openai_tools()
        assert tools[0]["type"] == "function"
        assert tools[0]["function"]["name"] == "test"


class TestToolRegistryExecution:
    """工具执行"""

    def test_execute_success(self) -> None:
        registry = ToolRegistry([_dummy_tool("test")])
        result = registry.execute("test", {})
        assert result.success
        assert result.content == {"ok": True}

    def test_execute_unknown_tool(self) -> None:
        registry = ToolRegistry()
        result = registry.execute("unknown", {})
        assert not result.success
        assert "未知工具" in result.error

    def test_execute_no_handler(self) -> None:
        registry = ToolRegistry([Tool(name="noop", description="no handler")])
        result = registry.execute("noop", {})
        assert not result.success

    def test_execute_handler_raises(self) -> None:
        def failer(args: dict) -> dict:
            raise ValueError("bang")
        registry = ToolRegistry([_dummy_tool("fail", handler=failer)])
        result = registry.execute("fail", {})
        assert not result.success
        assert "bang" in result.error

class TestToolRegistrySearch:
    """工具搜索功能"""

    def test_search_tools_by_keyword(self) -> None:
        registry = ToolRegistry([
            _dummy_tool("browser_navigate", description="导航到网页"),
            _dummy_tool("add_todo", description="添加待办"),
        ])
        results = registry.search_tools({"keyword": "待办"})
        assert len(results) == 1
        assert results[0]["name"] == "add_todo"

    def test_search_tools_self_excluded(self) -> None:
        registry = ToolRegistry()
        registry.register(_dummy_tool("search_tools"))
        results = registry.search_tools({"keyword": "search"})
        # search_tools 和 list_tool_groups 不应出现在搜索结果中
        names = {r["name"] for r in results}
        assert "search_tools" not in names

    def test_list_tool_groups(self) -> None:
        registry = ToolRegistry([
            _dummy_tool("a", group="default"),
            _dummy_tool("b", group="memory"),
        ])
        groups = registry.list_tool_groups({})
        group_names = {g["group"] for g in groups}
        assert "default" in group_names
        assert "memory" in group_names
