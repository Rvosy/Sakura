"""tests/unit/test_agent_runtime.py — AgentRuntime 行为特征测试

在拆分 AgentRuntime 之前，先用这些测试锁定关键行为：
1. 工具调用上限
2. PendingAction 中断与续跑
3. 浏览器/Windows 工具路由拦截
4. 屏幕观察允许/禁止逻辑
5. Vision fallback 行为
6. 主动事件流程
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from app.agent.actions import AgentAction, AgentEvent, AgentResult, PendingToolAction
from app.agent.runtime import (
    AgentRuntime,
    _build_vision_unsupported_reply,
)
from app.core.cancellation import CancellationToken, OperationCancelled
from app.agent.tool_routing import (
    _filter_openai_tools_for_browser_routing,
    _should_block_windows_tool_for_browser_page,
)
from app.agent.runtime_limits import (
    MAX_AGENT_STEPS_PER_TURN,
    MAX_EVENT_RECENT_CONVERSATION_CONTENT_CHARS,
    MAX_EVENT_RECENT_CONVERSATION_MESSAGES,
    MAX_PENDING_CONTEXT_MESSAGES,
    MAX_PENDING_CONTEXT_TEXT_CHARS,
    MAX_TOOL_CALLS_PER_STEP,
    MAX_TOOL_CALLS_PER_TURN,
    MAX_TOOL_RESULT_CHARS,
    RuntimeLoopSettings,
)
from app.agent.tools import Tool, ToolRegistry
from app.llm.api_client import ApiRequestError, ChatMessage, NativeToolCall, OpenAICompatibleClient
from app.llm.chat_reply import ChatReply, ChatSegment
from app.storage.chat_history import ChatHistoryEntry


def _dummy_system_prompt() -> str:
    return "你是 Sakura，一个桌宠助手。"


def _dummy_tool(name: str, **kwargs: object) -> Tool:
    defaults: dict[str, object] = {
        "description": f"Tool {name}",
        "parameters": {"type": "object", "properties": {}, "required": []},
        "handler": lambda args: {"ok": True, "tool": name},
        "requires_confirmation": False,
        "group": "default",
        "risk": "low",
    }
    defaults.update(kwargs)
    return Tool(name=name, **defaults)


def _dummy_api_client() -> MagicMock:
    client = MagicMock(spec=OpenAICompatibleClient)
    client.complete_with_tools.return_value = MagicMock(
        content=json.dumps(
            {"segments": [{"ja": "おはよう", "zh": "早安", "tone": "开心", "portrait": "站立待机"}]},
            ensure_ascii=False,
        ),
        tool_calls=[],
    )
    client.chat.return_value = ChatReply(
        segments=[ChatSegment(ja="おはよう", zh="早安", tone="开心", portrait="站立待机")]
    )
    # 角色对话入口会读取生成参数；返回内置默认温度与空额外参数，保持原有调用行为。
    client.resolve_dialogue_params.return_value = (0.8, {})
    return client


class _FakeHistoryStore:
    def __init__(self, entries: list[ChatHistoryEntry]) -> None:
        self.entries = entries

    def load(self) -> list[ChatHistoryEntry]:
        return self.entries


class TestRuntimeLimits:
    """运行时限制常量验证"""

    def test_agent_steps_per_turn_positive(self) -> None:
        assert MAX_AGENT_STEPS_PER_TURN > 0

    def test_tool_calls_per_step_positive(self) -> None:
        assert MAX_TOOL_CALLS_PER_STEP > 0

    def test_tool_calls_per_turn_positive(self) -> None:
        assert MAX_TOOL_CALLS_PER_TURN > 0

    def test_tool_calls_per_turn_at_least_per_step(self) -> None:
        assert MAX_TOOL_CALLS_PER_TURN >= MAX_TOOL_CALLS_PER_STEP

    def test_tool_result_chars_positive(self) -> None:
        assert MAX_TOOL_RESULT_CHARS > 0

    def test_pending_context_limits_positive(self) -> None:
        assert MAX_PENDING_CONTEXT_MESSAGES > 0
        assert MAX_PENDING_CONTEXT_TEXT_CHARS > 0

    def test_event_context_limits_positive(self) -> None:
        assert MAX_EVENT_RECENT_CONVERSATION_MESSAGES > 0
        assert MAX_EVENT_RECENT_CONVERSATION_CONTENT_CHARS > 0

    def test_runtime_loop_settings_are_used_in_prompt(self) -> None:
        runtime = AgentRuntime(
            _dummy_api_client(),
            _dummy_system_prompt(),
            runtime_loop_settings=RuntimeLoopSettings(
                max_agent_steps_per_turn=6,
                max_tool_calls_per_step=4,
                max_tool_calls_per_turn=12,
            ),
        )

        prompt = runtime._build_tool_system_prompt()

        assert "每步最多请求 4 个工具，整轮最多 12 个工具" in prompt

    def test_session_state_is_rendered_into_runtime_context(self) -> None:
        client = _dummy_api_client()
        runtime = AgentRuntime(
            client,
            _dummy_system_prompt(),
            history_store=_FakeHistoryStore(
                [
                    ChatHistoryEntry(
                        created_at="2026-06-20T12:00:00+08:00",
                        role="user",
                        content="帮我继续执行计划",
                    ),
                    ChatHistoryEntry(
                        created_at="2026-06-20T12:00:05+08:00",
                        role="assistant",
                        content="好的，已经记下计划的下一步。",
                    ),
                    ChatHistoryEntry(
                        created_at="2026-06-20T12:00:10+08:00",
                        role="user",
                        content="本轮问题",
                    ),
                ]
            ),
        )

        runtime.handle_user_message([ChatMessage(role="user", content="本轮问题")])

        call = client.complete_with_tools.call_args
        runtime_context = call.kwargs["runtime_context"]
        request_messages = call.args[1]
        assert "最近会话状态" in runtime_context
        assert "继续执行计划" in runtime_context
        assert "用户：本轮问题" not in runtime_context
        assert all("最近会话状态" not in str(message.get("content", "")) for message in request_messages)

    def test_session_state_skipped_when_live_window_is_deep(self) -> None:
        client = _dummy_api_client()
        runtime = AgentRuntime(
            client,
            _dummy_system_prompt(),
            history_store=_FakeHistoryStore(
                [
                    ChatHistoryEntry(
                        created_at="2026-06-20T12:00:00+08:00",
                        role="user",
                        content="帮我继续执行计划",
                    ),
                ]
            ),
        )

        # 实时窗口已经够深时不再注入跨会话历史切片，避免重复 token。
        runtime.handle_user_message(
            [
                ChatMessage(role="user", content="第一句"),
                ChatMessage(role="assistant", content="第一句回复"),
                ChatMessage(role="user", content="继续"),
            ]
        )

        runtime_context = client.complete_with_tools.call_args.kwargs["runtime_context"]
        assert "最近会话状态" not in runtime_context


class TestToolCallCountLimits:
    """验证 allowed_calls 计算逻辑"""

    @staticmethod
    def _allowed_calls(tool_calls_count: int, total_tool_calls: int) -> int:
        return min(
            tool_calls_count,
            MAX_TOOL_CALLS_PER_STEP,
            max(0, MAX_TOOL_CALLS_PER_TURN - total_tool_calls),
        )

    def test_within_all_limits(self) -> None:
        assert self._allowed_calls(2, 0) == 2

    def test_exceeds_step_limit(self) -> None:
        assert self._allowed_calls(10, 0) == MAX_TOOL_CALLS_PER_STEP

    def test_exceeds_turn_limit(self) -> None:
        remaining = max(0, MAX_TOOL_CALLS_PER_TURN - (MAX_TOOL_CALLS_PER_TURN - 1))
        assert self._allowed_calls(5, MAX_TOOL_CALLS_PER_TURN - 1) == remaining

    def test_exhausted(self) -> None:
        assert self._allowed_calls(5, MAX_TOOL_CALLS_PER_TURN) == 0


class TestPendingActionFlow:
    """验证确认/取消动作后的正确行为"""

    def test_handle_confirmed_action_executes_tool(self) -> None:
        tool = _dummy_tool("test_tool")
        registry = ToolRegistry([tool])
        runtime = AgentRuntime(_dummy_api_client(), _dummy_system_prompt(), tools=registry)
        action = PendingToolAction(
            tool_name="test_tool", arguments={}, reason="test",
            tool_call_id="call_1", continuation_messages=None,
        )
        result = runtime.handle_confirmed_action(action)
        assert isinstance(result, AgentResult)
        assert any(a.type == "tool_call" for a in result.actions)

    def test_handle_cancelled_action_returns_cancel_reply(self) -> None:
        tool = _dummy_tool("test_tool")
        registry = ToolRegistry([tool])
        runtime = AgentRuntime(_dummy_api_client(), _dummy_system_prompt(), tools=registry)
        action = PendingToolAction(
            tool_name="test_tool", arguments={}, reason="test",
            tool_call_id="call_1", continuation_messages=None,
        )
        result = runtime.handle_cancelled_action(action)
        assert result.actions[0].type == "cancelled_action"
        assert len(result.reply.segments) > 0

    def test_confirmed_action_with_continuation_enters_tool_loop(self) -> None:
        tool = _dummy_tool("test_tool")
        registry = ToolRegistry([tool])
        client = _dummy_api_client()
        runtime = AgentRuntime(client, _dummy_system_prompt(), tools=registry)
        continuation = [
            ChatMessage(role="user", content="打开浏览器"),
            ChatMessage(role="assistant", content="", tool_calls=[
                NativeToolCall(id="c1", name="test_tool", arguments={}, arguments_json="{}")
            ]),
        ]
        action = PendingToolAction(
            tool_name="test_tool", arguments={}, reason="test",
            tool_call_id="c1", continuation_messages=continuation,
        )
        result = runtime.handle_confirmed_action(action)
        assert client.complete_with_tools.called


class TestBrowserRouting:
    """浏览器/Windows 工具路由拦截"""

    def test_browser_page_mode_blocks_windows_tools(self) -> None:
        call = {"name": "windows__Click", "arguments": {}, "reason": "点击"}
        assert _should_block_windows_tool_for_browser_page(call, browser_page_mode=True)

    def test_browser_page_mode_passes_non_windows_tools(self) -> None:
        call = {"name": "playwright_navigate", "arguments": {}, "reason": "导航"}
        assert not _should_block_windows_tool_for_browser_page(call, browser_page_mode=True)

    def test_no_routing_when_both_modes_false(self) -> None:
        tools = [{"function": {"name": "test_tool"}}]
        result = _filter_openai_tools_for_browser_routing(tools, browser_page_mode=False, visible_browser_mode=False)
        assert result == tools

    def test_browser_page_mode_filters_tools(self) -> None:
        tools = [
            {"function": {"name": "playwright_navigate"}},
            {"function": {"name": "windows__Click"}},
            {"function": {"name": "add_todo"}},
        ]
        result = _filter_openai_tools_for_browser_routing(tools, browser_page_mode=True, visible_browser_mode=False)
        names = {t["function"]["name"] for t in result}
        assert "playwright_navigate" in names
        assert "windows__Click" not in names


class TestScreenObservation:
    """屏幕观察开关逻辑"""

    def test_screen_observation_disabled_removes_capability(self) -> None:
        screen_tool = _dummy_tool("observe_screen", capability="screen_observation")
        registry = ToolRegistry([screen_tool])
        tools = registry.describe_openai_tools(allowed_capabilities=set())
        names = {t["function"]["name"] for t in tools}
        assert "observe_screen" not in names

    def test_screen_observation_enabled_includes_capability(self) -> None:
        screen_tool = _dummy_tool("observe_screen", capability="screen_observation")
        registry = ToolRegistry([screen_tool])
        tools = registry.describe_openai_tools(allowed_capabilities={"screen_observation"})
        names = {t["function"]["name"] for t in tools}
        assert "observe_screen" in names


class TestVisionFallback:
    """视觉不支持时的兜底行为"""

    def test_vision_unsupported_reply_has_segments(self) -> None:
        reply = _build_vision_unsupported_reply()
        assert len(reply.segments) > 0

    def test_handle_user_message_vision_fallback(self) -> None:
        client = _dummy_api_client()
        client.complete_with_tools.side_effect = ApiRequestError("Vision not supported")
        with patch("app.agent.runtime.is_vision_unsupported_error", return_value=True):
            with patch("app.agent.runtime.messages_contain_image", return_value=True):
                runtime = AgentRuntime(client, _dummy_system_prompt())
                messages = [ChatMessage(role="user", content=[
                    {"type": "text", "text": "描述图片"},
                    {"type": "image_url", "image_url": {"url": "data:image/png;base64,xxx"}},
                ])]
                result = runtime.handle_user_message(messages)
                assert isinstance(result, AgentResult)
                assert len(result.reply.segments) > 0

    def test_visual_reply_extracts_observation_from_same_response(self) -> None:
        client = _dummy_api_client()
        client.complete_with_tools.return_value = MagicMock(
            content=json.dumps(
                {
                    "segments": [{"ja": "見えたよ。", "zh": "我看到了。", "tone": "中性"}],
                    "visual_observation": {
                        "summary": "截图里是模型设置页。",
                        "visible_texts": ["模型设置"],
                        "uncertain_texts": [],
                        "notable_elements": ["设置卡片"],
                        "confidence": 0.9,
                        "sensitive_redacted": False,
                    },
                },
                ensure_ascii=False,
            ),
            tool_calls=[],
            runtime_context_role="system",
        )
        runtime = AgentRuntime(client, _dummy_system_prompt())
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "看图"},
                    {"type": "image_url", "image_url": {"url": "data:image/png;base64,xxx"}},
                ],
            }
        ]

        result = runtime.handle_user_message(messages)

        prompt = client.complete_with_tools.call_args.args[0]
        assert "visual_observation" in prompt
        assert result.reply.translation == "我看到了。"
        assert result.visual_observation is not None
        assert result.visual_observation["summary"] == "截图里是模型设置页。"


class TestProactiveEventFlow:
    """主动事件流程验证"""

    def test_unsupported_event_type_returns_fallback(self) -> None:
        runtime = AgentRuntime(_dummy_api_client(), _dummy_system_prompt())
        result = runtime.handle_event(AgentEvent(type="unknown_event", payload={}))
        assert len(result.reply.segments) > 0

    def test_proactive_check_enters_tool_loop(self) -> None:
        client = _dummy_api_client()
        runtime = AgentRuntime(client, _dummy_system_prompt())
        event = AgentEvent(type="proactive_check", payload={
            "screen_context_allowed": False, "recent_conversation": [],
        })
        runtime.handle_event(event)
        assert client.complete_with_tools.called

    def test_screen_awareness_check_enters_tool_loop(self) -> None:
        client = _dummy_api_client()
        runtime = AgentRuntime(client, _dummy_system_prompt())
        event = AgentEvent(type="screen_awareness_check", payload={
            "screen_context_allowed": False, "recent_conversation": [],
        })
        runtime.handle_event(event)
        assert client.complete_with_tools.called

    def test_reminder_due_uses_chat_not_tools(self) -> None:
        client = _dummy_api_client()
        runtime = AgentRuntime(client, _dummy_system_prompt())
        event = AgentEvent(type="reminder_due", payload={
            "reminder_id": "r1", "reminder_text": "喝水",
        })
        runtime.handle_event(event)
        assert client.chat.called


class TestAgentRuntimeBasics:
    """AgentRuntime 基本属性验证"""

    def test_default_vision_enabled(self) -> None:
        runtime = AgentRuntime(_dummy_api_client(), _dummy_system_prompt())
        assert runtime.model_vision_enabled is True

    def test_default_autonomous_screen_observation_enabled(self) -> None:
        runtime = AgentRuntime(_dummy_api_client(), _dummy_system_prompt())
        assert runtime.autonomous_screen_observation_enabled is True

    def test_set_model_vision(self) -> None:
        runtime = AgentRuntime(_dummy_api_client(), _dummy_system_prompt())
        runtime.set_model_vision_enabled(False)
        assert not runtime.model_vision_enabled

    def test_final_reply_retries_once_when_json_invalid(self) -> None:
        client = _dummy_api_client()
        client.complete_with_tools.side_effect = [
            MagicMock(
                content='{"segments":[{"ja":"原因是 Mermaid 语法。","zh":"原因是 Mermaid 语法。"}]}',
                tool_calls=[],
            ),
            MagicMock(
                content=json.dumps(
                    {"segments": [{"ja": "直したよ。", "zh": "修好了。", "tone": "中性"}]},
                    ensure_ascii=False,
                ),
                tool_calls=[],
            ),
        ]
        runtime = AgentRuntime(client, _dummy_system_prompt())

        result = runtime.handle_user_message([ChatMessage(role="user", content="hello")])

        assert client.complete_with_tools.call_count == 2
        assert result.reply.segments[0].text == "直したよ。"
        repair_messages = client.complete_with_tools.call_args_list[1].args[1]
        assert "不要用固定兜底句替代" in repair_messages[-1]["content"]

    def test_final_reply_retries_when_plain_japanese_lacks_translation(self) -> None:
        client = _dummy_api_client()
        client.complete_with_tools.side_effect = [
            MagicMock(
                content="……開いたよ。\n\n北京の天気は、今日は曇りみたい。",
                tool_calls=[],
            ),
            MagicMock(
                content=json.dumps(
                    {
                        "segments": [
                            {
                                "ja": "北京の天気を確認したよ。",
                                "zh": "我确认了北京天气。",
                                "tone": "中性",
                            }
                        ]
                    },
                    ensure_ascii=False,
                ),
                tool_calls=[],
            ),
        ]
        runtime = AgentRuntime(client, _dummy_system_prompt())

        result = runtime.handle_user_message([ChatMessage(role="user", content="北京天气")])

        assert client.complete_with_tools.call_count == 2
        assert result.reply.segments[0].text == "北京の天気を確認したよ。"
        assert result.reply.segments[0].translation == "我确认了北京天气。"

    def test_final_reply_uses_safe_fallback_when_retry_still_invalid(self) -> None:
        client = _dummy_api_client()
        bad_content = '{"segments":[{"ja":"原因是 Mermaid 语法。","zh":"原因是 Mermaid 语法。"}]}'
        client.complete_with_tools.side_effect = [
            MagicMock(content=bad_content, tool_calls=[]),
            MagicMock(content=bad_content, tool_calls=[]),
        ]
        runtime = AgentRuntime(client, _dummy_system_prompt())

        result = runtime.handle_user_message([ChatMessage(role="user", content="hello")])

        assert client.complete_with_tools.call_count == 2
        assert result.reply.segments[0].text != bad_content
        assert "segments" not in result.reply.segments[0].text
        assert result.reply.segments[0].suppress_tts is True

    def test_update_character_preserves_tools(self) -> None:
        tool = _dummy_tool("my_tool")
        registry = ToolRegistry([tool])
        runtime = AgentRuntime(_dummy_api_client(), "旧", tools=registry, reply_tones=["开心"])
        runtime.update_character("新", reply_tones=["傲娇"])
        assert runtime.tools.get("my_tool") is not None
        assert runtime.reply_tones == ["傲娇"]

    def test_cancel_after_planning_stops_before_tool_execution(self) -> None:
        token = CancellationToken()
        executed: list[str] = []
        tool = _dummy_tool(
            "my_tool",
            handler=lambda _args: executed.append("called") or {"ok": True},
        )
        registry = ToolRegistry([tool])
        client = _dummy_api_client()

        def complete_with_tools(*_args: object, **_kwargs: object) -> MagicMock:
            token.cancel()
            return MagicMock(
                content="準備するね。",
                tool_calls=[NativeToolCall(id="call_1", name="my_tool", arguments={})],
                message={
                    "role": "assistant",
                    "content": "準備するね。",
                    "tool_calls": [
                        {
                            "id": "call_1",
                            "type": "function",
                            "function": {"name": "my_tool", "arguments": "{}"},
                        }
                    ],
                },
            )

        client.complete_with_tools.side_effect = complete_with_tools
        runtime = AgentRuntime(client, _dummy_system_prompt(), tools=registry)

        with pytest.raises(OperationCancelled):
            runtime.handle_user_message(
                [ChatMessage(role="user", content="do it")],
                cancel_checker=token.throw_if_cancelled,
            )

        assert executed == []
