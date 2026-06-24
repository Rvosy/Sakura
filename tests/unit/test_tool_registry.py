"""tests/unit/test_tool_registry.py — 统一工具注册系统测试。

覆盖：
- Tool / ToolMetadata / ToolExecutionResult
- ToolRegistry 注册 / 查询 / 描述 / 执行
- ToolPermissionPolicy 确认策略
- search_tools / active_groups / capability filtering
- BuiltinToolProvider
"""

from __future__ import annotations

import pytest

from app.agent.tools import (
    Tool,
    ToolExecutionResult,
    ToolMetadata,
    ToolPermissionPolicy,
    ToolRegistry,
)
from app.agent.actions import PendingToolAction


def _dummy_tool(name: str, **kwargs: object) -> Tool:
    defaults: dict[str, object] = {
        "description": f"Tool {name}",
        "parameters": {"type": "object", "properties": {}, "required": []},
        "handler": lambda args: {"ok": True},
        "group": "default",
        "risk": "low",
        "requires_confirmation": False,
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

    def test_prepare_or_execute_no_confirmation(self) -> None:
        registry = ToolRegistry([_dummy_tool("safe")])
        result = registry.prepare_or_execute("safe", {})
        assert isinstance(result, ToolExecutionResult)

    def test_prepare_or_execute_with_confirmation(self) -> None:
        registry = ToolRegistry([_dummy_tool("risky", requires_confirmation=True)])
        registry.set_free_access_enabled(False)
        result = registry.prepare_or_execute("risky", {})
        assert isinstance(result, PendingToolAction)

    def test_register_from_provider(self) -> None:
        """register_from_provider 批量注册测试"""

        class SimpleProvider:
            def contribute_tools(self) -> list[Tool]:
                return [_dummy_tool("p1"), _dummy_tool("p2")]

        registry = ToolRegistry()
        count = registry.register_from_provider(SimpleProvider())
        assert count == 2
        assert registry.get("p1") is not None

    def test_register_from_provider_no_contribute(self) -> None:
        class BadProvider:
            pass

        registry = ToolRegistry()
        count = registry.register_from_provider(BadProvider())
        assert count == 0


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


class TestToolPermissionPolicy:
    """工具权限策略"""

    def test_no_confirmation_low_risk(self) -> None:
        policy = ToolPermissionPolicy(free_access_enabled=False)
        tool = _dummy_tool("safe", requires_confirmation=False)
        assert not policy.requires_confirmation(tool)

    def test_requires_confirmation_normal(self) -> None:
        policy = ToolPermissionPolicy(free_access_enabled=False)
        tool = _dummy_tool("risky", requires_confirmation=True)
        assert policy.requires_confirmation(tool)

    def test_free_access_skips_confirmation(self) -> None:
        policy = ToolPermissionPolicy(free_access_enabled=True)
        tool = _dummy_tool("risky", requires_confirmation=True, risk="medium")
        assert not policy.requires_confirmation(tool)

    def test_high_risk_always_confirms(self) -> None:
        policy = ToolPermissionPolicy(free_access_enabled=True)
        tool = _dummy_tool("delete_file_xxx", requires_confirmation=True, risk="high")
        assert policy.requires_confirmation(tool)

    def test_destructive_file_always_confirms(self) -> None:
        policy = ToolPermissionPolicy(free_access_enabled=True)
        tool = _dummy_tool("delete_local_file", requires_confirmation=True,
                           risk="medium", confirmation_risk="destructive_file")
        assert policy.requires_confirmation(tool)

    def test_browser_free_access_tool_recognized(self) -> None:
        policy = ToolPermissionPolicy()
        assert policy.is_browser_free_access_tool("playwright_navigate")
        assert not policy.is_browser_free_access_tool("unknown_tool")

    def test_sensory_audio_capture_always_requires_confirmation(self) -> None:
        policy = ToolPermissionPolicy(free_access_enabled=True)
        tool = _dummy_tool(
            "observe_sensory",
            requires_confirmation=True,
            confirmation_risk="sensory_audio_capture",
            risk="medium",
            capability="sensory_observation",
        )

        assert policy.requires_confirmation(tool, {"source": "speech"})
        assert policy.requires_confirmation(tool, {"source": "sound", "text": "已有线索"})
        assert not policy.requires_confirmation(
            tool,
            {"source": "speech", "media_ref": "data:audio/wav;base64,abc"},
        )
        assert policy.requires_confirmation(tool, {"source": "speech", "metadata": {"image_url": "x"}})
        assert not policy.requires_confirmation(tool, {"source": "vision"})

        environment_tool = _dummy_tool(
            "observe_environment_sound",
            requires_confirmation=True,
            confirmation_risk="sensory_audio_capture",
            risk="medium",
            capability="sensory_sound_observation",
        )
        system_tool = _dummy_tool(
            "observe_system_speech",
            requires_confirmation=True,
            confirmation_risk="sensory_audio_capture",
            risk="medium",
            capability="sensory_speech_observation",
        )

        assert policy.requires_confirmation(environment_tool, {})
        assert policy.requires_confirmation(system_tool, {"duration_seconds": 1.0})
        assert not policy.requires_confirmation(
            environment_tool,
            {"media_ref": "data:audio/wav;base64,abc"},
        )
