from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.agent.actions import PendingToolAction
from app.agent.builtin_tools import create_builtin_tool_registry
from app.agent.runtime import AgentRuntime
from app.agent.tools import Tool, ToolRegistry
from app.llm.api_client import ChatCompletionTurn, NativeToolCall
from app.plugins.discovery import PluginDiscovery
from app.plugins.manager import PluginManager


def _reply(text: str = "完成。") -> str:
    return json.dumps(
        {
            "segments": [
                {
                    "ja": "できたよ。",
                    "zh": text,
                    "tone": "中性",
                    "portrait": "站立待机",
                }
            ]
        },
        ensure_ascii=False,
    )


class NativeFakeClient:
    def __init__(self, turns: list[ChatCompletionTurn]) -> None:
        self.turns = turns
        self.calls: list[dict[str, Any]] = []

    def complete_with_tools(self, system_prompt, messages, **kwargs):  # type: ignore[no-untyped-def]
        self.calls.append(
            {
                "system_prompt": system_prompt,
                "messages": [dict(message) for message in messages],
                "kwargs": kwargs,
            }
        )
        return self.turns.pop(0)

    def chat(self, _system_prompt, _messages, *_args, **_kwargs):  # type: ignore[no-untyped-def]
        raise AssertionError("原生 tool_calls 流程不应回退到普通 chat 总结。")


def _tool_turn(call_id: str, name: str, arguments: dict[str, Any]) -> ChatCompletionTurn:
    arguments_json = json.dumps(arguments, ensure_ascii=False)
    return ChatCompletionTurn(
        content="",
        tool_calls=[
            NativeToolCall(
                id=call_id,
                name=name,
                arguments=arguments,
                arguments_json=arguments_json,
            )
        ],
        message={
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": call_id,
                    "type": "function",
                    "function": {"name": name, "arguments": arguments_json},
                }
            ],
        },
    )


def _final_turn(text: str = "完成。") -> ChatCompletionTurn:
    return ChatCompletionTurn(
        content=_reply(text),
        tool_calls=[],
        message={"role": "assistant", "content": _reply(text)},
    )


def test_plugin_manager_loads_playwright_browser_plugin() -> None:
    registry = create_builtin_tool_registry(Path(__file__).resolve().parents[2])
    manager = PluginManager(Path(__file__).resolve().parents[2])

    manager.load_from_config(registry)

    names = {tool.name for tool in registry.all()}
    assert {
        "playwright_navigate",
        "playwright_get_text",
        "playwright_search_web",
        "playwright_screenshot",
        "playwright_click",
        "playwright_fill",
        "playwright_evaluate",
    }.issubset(names)
    assert registry.get("playwright_evaluate").requires_confirmation  # type: ignore[union-attr]
    assert "Playwright 浏览器" in [settings.title for settings in manager.plugin_settings]
    assert "Playwright 浏览器" not in [tab.title for tab in manager.tools_tabs]

    manager.shutdown_all()


def test_plugin_config_manifest_is_read(tmp_path: Path) -> None:
    specs = PluginDiscovery(
        Path(__file__).resolve().parents[2],
        config_path=tmp_path / "plugins.yaml",
    ).discover()

    playwright = next(spec for spec in specs if spec.plugin_id == "playwright_browser")
    assert playwright.entry == "plugin:PlaywrightBrowserPlugin"
    assert playwright.enabled


def test_tool_registry_exports_openai_function_schema() -> None:
    registry = ToolRegistry(
        [
            Tool(
                name="echo_tool",
                description="Echo",
                parameters={
                    "type": "object",
                    "properties": {"value": {"type": "string"}},
                    "required": ["value"],
                },
                handler=lambda arguments: arguments,
            )
        ]
    )

    schema = registry.describe_openai_tools()

    assert schema == [
        {
            "type": "function",
            "function": {
                "name": "echo_tool",
                "description": "Echo",
                "parameters": {
                    "type": "object",
                    "properties": {"value": {"type": "string"}},
                    "required": ["value"],
                },
            },
        }
    ]


def test_openai_tool_schema_drops_null_only_properties() -> None:
    registry = ToolRegistry(
        [
            Tool(
                name="add_reminder",
                description="Reminder",
                parameters={
                    "type": "object",
                    "properties": {
                        "text": {"type": "string"},
                        "repeat": {"type": ["null"], "description": "暂不支持，省略即可。"},
                        "note": {"type": ["string", "null"]},
                    },
                    "required": ["text"],
                },
                handler=lambda arguments: arguments,
            )
        ]
    )

    parameters = registry.describe_openai_tools()[0]["function"]["parameters"]

    assert parameters["type"] == "object"
    assert "repeat" not in parameters["properties"]
    assert parameters["properties"]["note"]["type"] == "string"
    assert parameters["properties"]["note"]["nullable"] is True
    assert parameters["required"] == ["text"]


def test_high_risk_playwright_evaluate_still_requires_confirmation_with_free_access() -> None:
    registry = ToolRegistry(
        [
            Tool(
                name="playwright_evaluate",
                description="Evaluate",
                parameters={"type": "object", "properties": {}, "required": []},
                handler=lambda _arguments: {"ok": True},
                requires_confirmation=True,
                risk="high",
            )
        ]
    )
    registry.set_free_access_enabled(True)

    result = registry.prepare_or_execute("playwright_evaluate", {})

    assert isinstance(result, PendingToolAction)
    assert result.tool_name == "playwright_evaluate"


def test_agent_runtime_uses_native_tool_role_messages() -> None:
    registry = ToolRegistry(
        [
            Tool(
                name="echo_tool",
                description="Echo",
                parameters={
                    "type": "object",
                    "properties": {"value": {"type": "string"}},
                    "required": ["value"],
                },
                handler=lambda arguments: {"echo": arguments["value"]},
            )
        ]
    )
    client = NativeFakeClient(
        [
            _tool_turn("call_1", "echo_tool", {"value": "ok"}),
            _final_turn("确认好了。"),
        ]
    )
    runtime = AgentRuntime(client, "system", tools=registry)  # type: ignore[arg-type]

    result = runtime.handle_user_message([{"role": "user", "content": "测试工具"}])

    assert result.reply.translation == "确认好了。"
    second_messages = client.calls[1]["messages"]
    assert second_messages[-2]["role"] == "assistant"
    assert second_messages[-2]["tool_calls"][0]["id"] == "call_1"
    assert second_messages[-1]["role"] == "tool"
    assert second_messages[-1]["tool_call_id"] == "call_1"
    assert '"echo": "ok"' in second_messages[-1]["content"]


def test_pending_confirmation_keeps_native_tool_call_id() -> None:
    registry = ToolRegistry(
        [
            Tool(
                name="danger_tool",
                description="Danger",
                parameters={"type": "object", "properties": {}, "required": []},
                handler=lambda _arguments: {"ok": True},
                requires_confirmation=True,
                risk="high",
            )
        ]
    )
    registry.set_free_access_enabled(False)
    client = NativeFakeClient([_tool_turn("call_danger", "danger_tool", {})])
    runtime = AgentRuntime(client, "system", tools=registry)  # type: ignore[arg-type]

    result = runtime.handle_user_message([{"role": "user", "content": "执行高风险动作"}])

    pending = result.actions[0].payload
    action = PendingToolAction.from_dict(pending)
    assert action.tool_call_id == "call_danger"
    assert action.continuation_messages[-1]["role"] == "assistant"
    assert action.continuation_messages[-1]["tool_calls"][0]["id"] == "call_danger"


def test_search_tools_activates_browser_tool_group() -> None:
    registry = ToolRegistry(
        [
            Tool(
                name="browser_tool",
                description="Browser",
                parameters={"type": "object", "properties": {}, "required": []},
                handler=lambda _arguments: {"ok": True},
                group="browser",
            )
        ]
    )
    registry.register(
        Tool(
            name="search_tools",
            description="Search tools",
            parameters={
                "type": "object",
                "properties": {"keyword": {"type": "string"}},
                "required": ["keyword"],
            },
            handler=registry.search_tools,
        )
    )
    client = NativeFakeClient(
        [
            _tool_turn("call_search", "search_tools", {"keyword": "browser"}),
            _final_turn("找到工具了。"),
        ]
    )
    runtime = AgentRuntime(client, "system", tools=registry)  # type: ignore[arg-type]

    runtime.handle_user_message([{"role": "user", "content": "需要更多工具能力"}])

    first_tool_names = {
        tool["function"]["name"]
        for tool in client.calls[0]["kwargs"]["tools"]
    }
    second_tool_names = {
        tool["function"]["name"]
        for tool in client.calls[1]["kwargs"]["tools"]
    }
    assert "browser_tool" not in first_tool_names
    assert "browser_tool" in second_tool_names
