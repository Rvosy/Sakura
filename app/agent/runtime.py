from __future__ import annotations

import json
import time
from datetime import datetime
from typing import Any, Callable

from app.agent.actions import AgentAction, AgentEvent, AgentProgress, AgentResult, PendingToolAction
from app.agent.memory import MemoryStore
from app.agent.screen_tools import (
    OBSERVE_SCREEN_TOOL_NAME,
    SCREEN_OBSERVATION_CAPABILITY,
    SCREEN_OBSERVATION_DISABLED_ERROR,
    SCREEN_OBSERVATION_REQUEST_ACTION,
)
from app.agent.screen_policy import ScreenPolicy
from app.agent.tool_policy import (
    BROWSER_NAVIGATE_TOOL_NAME,
    BROWSER_SNAPSHOT_TOOL_NAME,
    ToolPolicy,
    WINDOWS_CLICK_TOOL_NAME,
    WINDOWS_SCREENSHOT_TOOL_NAME,
    WINDOWS_SNAPSHOT_TOOL_NAME,
)
from app.agent.tool_registry import ToolExecutionResult, ToolRegistry
from app.llm.api_client import (
    ApiRequestError,
    ChatMessage,
    NativeToolCall,
    OpenAICompatibleClient,
    is_vision_unsupported_error,
    messages_contain_image,
)
from app.llm.chat_reply import ChatReply, parse_chat_reply, parse_chat_reply_result
from app.core.debug_log import debug_log, summarize_messages
from app.agent.runtime_limits import (
    MAX_AGENT_STEPS_PER_TURN,
    MAX_EVENT_RECENT_CONVERSATION_CONTENT_CHARS,
    MAX_EVENT_RECENT_CONVERSATION_MESSAGES,
    MAX_PENDING_CONTEXT_MESSAGES,
    MAX_PENDING_CONTEXT_TEXT_CHARS,
    MAX_TOOL_CALLS_PER_STEP,
    MAX_TOOL_CALLS_PER_TURN,
    MAX_TOOL_RESULT_CHARS,
    ProgressCallback,
)
from app.llm.prompt_templates import (
    build_agent_reply_protocol,
    build_context_acquisition_strategy,
    build_event_system_prompt,
    build_proactive_check_tool_system_prompt,
)
from sdk.types import PromptPatchContribution



class AgentRuntime:
    """封装聊天决策链路，为后续工具调用和长期记忆留下扩展点。"""

    def __init__(
        self,
        api_client: OpenAICompatibleClient,
        system_prompt: str,
        reply_tones: list[str] | None = None,
        reply_portraits: list[str] | None = None,
        tools: ToolRegistry | None = None,
        memory: MemoryStore | None = None,
        prompt_patches: list[PromptPatchContribution] | None = None,
    ) -> None:
        self.api_client = api_client
        self.system_prompt = system_prompt
        self.reply_tones = [*reply_tones] if reply_tones is not None else []
        self.reply_portraits = [*reply_portraits] if reply_portraits is not None else []
        self.tools = tools or ToolRegistry()
        self.memory = memory or MemoryStore()
        self.prompt_patches = [*prompt_patches] if prompt_patches is not None else []
        self.model_vision_enabled = True
        self.autonomous_screen_observation_enabled = True

    def update_character(
        self,
        system_prompt: str,
        reply_tones: list[str] | None = None,
        reply_portraits: list[str] | None = None,
    ) -> None:
        """角色切换后同步系统提示词、可用语气和可用立绘列表。"""
        self.system_prompt = system_prompt
        self.reply_tones = [*reply_tones] if reply_tones is not None else []
        self.reply_portraits = [*reply_portraits] if reply_portraits is not None else []

    def set_prompt_patches(self, prompt_patches: list[PromptPatchContribution] | None) -> None:
        """同步插件提示词补丁。"""
        self.prompt_patches = [*prompt_patches] if prompt_patches is not None else []

    def set_model_vision_enabled(self, enabled: bool) -> None:
        """允许模型在需要时请求一次当前屏幕截图。"""
        self.model_vision_enabled = enabled

    def set_autonomous_screen_observation_enabled(self, enabled: bool) -> None:
        """允许模型在对话或主动事件中自主决定是否观察屏幕。"""
        self.autonomous_screen_observation_enabled = enabled

    def _parse_final_reply_with_retry(
        self,
        system_prompt: str,
        working_messages: list[ChatMessage],
        raw_content: str,
    ) -> ChatReply:
        """最终回复结构不合格时，只重试一次格式修复，避免坏 JSON 进入 UI。"""
        parsed = parse_chat_reply_result(raw_content)
        retry_reason = parsed.reason if parsed.needs_retry else ""
        if not parsed.needs_retry and _reply_has_display_translation(parsed.reply):
            return parsed.reply
        if not retry_reason:
            retry_reason = "missing_translation"

        debug_log(
            "AgentRuntime",
            "最终回复结构异常，准备请求模型修复",
            {"reason": retry_reason, "raw_content": raw_content},
        )
        repair_messages: list[ChatMessage] = [
            *working_messages,
            {"role": "assistant", "content": raw_content},
            {
                "role": "user",
                "content": (
                    "上一条 assistant 输出不是合格的 Sakura 回复 JSON。"
                    "请只把上一条内容修复为合法 JSON，不新增事实、不解释、不使用 Markdown。"
                    "格式必须是 {\"segments\":[{\"ja\":\"自然日语\",\"zh\":\"中文译文\","
                    "\"tone\":\"中性\",\"portrait\":\"站立待机\"}]}。"
                    "ja 字段只能写自然日语，不能包含中文。"
                ),
            },
        ]
        try:
            repaired_turn = self.api_client.complete_with_tools(
                system_prompt,
                repair_messages,
                tools=[],
                tool_choice="none",
                temperature=0.2,
                structured_response=True,
            )
        except ApiRequestError as exc:
            debug_log("AgentRuntime", "最终回复修复请求失败，使用安全兜底", {"error": str(exc)})
            return parsed.reply

        repaired = parse_chat_reply_result(repaired_turn.content)
        if repaired.needs_retry:
            debug_log(
                "AgentRuntime",
                "最终回复修复后仍不合格，使用安全兜底",
                {"reason": repaired.reason, "raw_content": repaired_turn.content},
            )
            return parsed.reply
        debug_log("AgentRuntime", "最终回复结构修复成功", {"repaired": repaired.repaired})
        return repaired.reply

    def handle_user_message(
        self,
        messages: list[ChatMessage],
        progress_callback: ProgressCallback | None = None,
    ) -> AgentResult:
        turn_started_at = time.perf_counter()
        allow_screen_observation = (
            self.model_vision_enabled
            and self.autonomous_screen_observation_enabled
            and not messages_contain_image(messages)
            and _should_offer_screen_observation(messages)
        )
        debug_log(
            "AgentRuntime",
            "开始处理用户消息",
            {
                "message_count": len(messages),
                "allow_screen_observation": allow_screen_observation,
                "model_vision_enabled": self.model_vision_enabled,
                "autonomous_screen_observation_enabled": self.autonomous_screen_observation_enabled,
                "messages": summarize_messages(messages),
            },
        )
        return self._run_tool_loop(
            messages,
            allow_screen_observation=allow_screen_observation,
            turn_started_at=turn_started_at,
            vision_unsupported_reply=_build_vision_unsupported_reply(),
            progress_callback=progress_callback,
        )

    def _run_tool_loop(
        self,
        messages: list[ChatMessage],
        *,
        allow_screen_observation: bool,
        turn_started_at: float,
        proactive_mode: bool = False,
        planning_extra_instructions: str = "",
        initial_actions: list[AgentAction] | None = None,
        vision_unsupported_reply: ChatReply | None = None,
        progress_callback: ProgressCallback | None = None,
    ) -> AgentResult:
        """执行 OpenAI 原生 tools/tool_calls 循环。"""
        working_messages: list[ChatMessage] = [*messages]
        execution_results: list[ToolExecutionResult] = []
        emitted_actions: list[AgentAction] = [*(initial_actions or [])]
        total_tool_calls = 0
        active_groups: set[str] = {"default", "mcp", "memory"}
        for step_index in range(MAX_AGENT_STEPS_PER_TURN):
            browser_page_mode = _should_prefer_browser_page_tools(working_messages)
            browser_page_guard_active = (
                browser_page_mode
                and _browser_dom_tools_available(self.tools)
                and not _recent_browser_tool_failed(working_messages)
                and not _latest_user_explicitly_requests_windows_control(working_messages)
            )
            visible_browser_guard_active = (
                _latest_user_requests_visible_browser(working_messages)
                and _browser_dom_tools_available(self.tools)
            )
            if browser_page_mode or visible_browser_guard_active:
                active_groups.add("browser")
            allowed_capabilities = {SCREEN_OBSERVATION_CAPABILITY} if allow_screen_observation else set()
            tool_defs = _filter_openai_tools_for_browser_routing(
                self.tools.describe_openai_tools(
                    allowed_capabilities=allowed_capabilities,
                    active_groups=active_groups,
                ),
                browser_page_mode=browser_page_guard_active,
                visible_browser_mode=visible_browser_guard_active,
            )
            try:
                planning_started_at = time.perf_counter()
                system_prompt = (
                    self._build_proactive_tool_system_prompt(
                        step_index=step_index,
                        remaining_steps=MAX_AGENT_STEPS_PER_TURN - step_index - 1,
                        extra_instructions=planning_extra_instructions,
                    )
                    if proactive_mode
                    else self._build_tool_system_prompt(
                        allow_screen_observation=allow_screen_observation,
                        step_index=step_index,
                        remaining_steps=MAX_AGENT_STEPS_PER_TURN - step_index - 1,
                        extra_instructions=planning_extra_instructions,
                        browser_page_mode=browser_page_guard_active,
                        visible_browser_mode=visible_browser_guard_active,
                    )
                )
                turn = self.api_client.complete_with_tools(
                    system_prompt,
                    working_messages,
                    tools=tool_defs,
                    tool_choice="auto",
                    temperature=0.8,
                    # Some OpenAI-compatible providers return pseudo tool-call JSON
                    # in message.content instead of native tool_calls when
                    # response_format=json_object is combined with tools.
                    structured_response=not bool(tool_defs),
                )
            except ApiRequestError as exc:
                if messages_contain_image(working_messages) and is_vision_unsupported_error(exc):
                    debug_log("AgentRuntime", "视觉输入不受支持，返回兜底回复", {"error": str(exc)})
                    return AgentResult(
                        reply=vision_unsupported_reply or _build_vision_unsupported_reply(),
                        actions=emitted_actions,
                    )
                raise
            debug_log(
                "AgentRuntime",
                "原生工具模型返回",
                {
                    "step_index": step_index,
                    "content": turn.content,
                    "tool_calls": [
                        {"id": call.id, "name": call.name, "arguments": call.arguments}
                        for call in turn.tool_calls
                    ],
                    "planning_elapsed_ms": int((time.perf_counter() - planning_started_at) * 1000),
                },
            )
            if not turn.tool_calls:
                debug_log(
                    "AgentRuntime",
                    "多步循环完成，返回模型回复",
                    {
                        "step_index": step_index,
                        "tool_result_count": len(execution_results),
                        "turn_elapsed_ms": int((time.perf_counter() - turn_started_at) * 1000),
                    },
                )
                return AgentResult(
                    reply=self._parse_final_reply_with_retry(
                        system_prompt,
                        working_messages,
                        turn.content,
                    ),
                    _debug=_build_debug_meta(
                        self.api_client, execution_results,
                        total_tool_calls, turn_started_at,
                    ),
                    actions=emitted_actions,
                )

            _emit_progress_from_content(
                progress_callback,
                turn.content,
                stage="tool_planning",
                metadata={
                    "step_index": step_index,
                    "tool_names": [call.name for call in turn.tool_calls],
                    "tool_call_count": len(turn.tool_calls),
                },
            )
            step_results: list[ToolExecutionResult] = []
            pending_actions: list[PendingToolAction] = []
            tool_messages: list[ChatMessage] = []
            tools_started_at = time.perf_counter()
            should_fast_forward_final_reply = False
            allowed_calls = min(
                len(turn.tool_calls),
                MAX_TOOL_CALLS_PER_STEP,
                max(0, MAX_TOOL_CALLS_PER_TURN - total_tool_calls),
            )
            for call in turn.tool_calls[:allowed_calls]:
                total_tool_calls += 1
                execution_arguments = _tool_arguments_for_execution(call, self.tools)
                call_data = _native_tool_call_to_policy_call(call, execution_arguments)
                debug_log("AgentRuntime", "准备工具调用", {"step_index": step_index, **call_data})
                if _should_block_windows_tool_for_browser_page(call_data, browser_page_guard_active):
                    blocked_result = _build_browser_page_windows_tool_block_result(call_data)
                    debug_log("AgentRuntime", "浏览器页面模式拦截 Windows 工具", blocked_result.to_dict())
                    step_results.append(blocked_result)
                    execution_results.append(blocked_result)
                    tool_messages.extend(
                        _build_tool_messages_for_result(
                            call,
                            blocked_result,
                            include_images=self.model_vision_enabled,
                        )
                    )
                    emitted_actions.append(
                        AgentAction(
                            type="tool_call",
                            payload=_redact_tool_result_for_model(blocked_result),
                        )
                    )
                    continue
                if _should_block_background_web_tool_for_visible_browser(call_data, visible_browser_guard_active):
                    blocked_result = _build_visible_browser_web_tool_block_result(call_data)
                    debug_log("AgentRuntime", "可见浏览器模式拦截后台网页工具", blocked_result.to_dict())
                    step_results.append(blocked_result)
                    execution_results.append(blocked_result)
                    tool_messages.extend(
                        _build_tool_messages_for_result(
                            call,
                            blocked_result,
                            include_images=self.model_vision_enabled,
                        )
                    )
                    emitted_actions.append(
                        AgentAction(
                            type="tool_call",
                            payload=_redact_tool_result_for_model(blocked_result),
                        )
                    )
                    continue
                prepared = self.tools.prepare_or_execute(
                    call.name,
                    execution_arguments,
                    _tool_call_reason(call),
                    tool_call_id=call.id,
                )
                if isinstance(prepared, PendingToolAction):
                    prepared = prepared.with_continuation_messages(
                        _build_pending_continuation_messages(
                            working_messages,
                            turn.message,
                            tool_messages,
                            turn.tool_calls,
                            pending_call_id=call.id,
                        )
                    )
                    skipped_after_pending = _build_skipped_after_pending_messages(
                        turn.tool_calls,
                        start_after_call_id=call.id,
                    )
                    tool_messages.extend(skipped_after_pending)
                    debug_log(
                        "AgentRuntime",
                        "工具调用等待用户确认",
                        {
                            **prepared.to_dict(),
                            "continuation_message_count": len(prepared.continuation_messages),
                        },
                    )
                    pending_actions.append(prepared)
                    break

                if _is_screen_observation_request(prepared):
                    if allow_screen_observation:
                        screen_action = AgentAction(
                            type=SCREEN_OBSERVATION_REQUEST_ACTION,
                            payload={"reason": _tool_call_reason(call)},
                        )
                        debug_log(
                            "AgentRuntime",
                            "请求屏幕观察 follow-up",
                            {
                                "step_index": step_index,
                                "reason": _tool_call_reason(call),
                                "turn_elapsed_ms": int((time.perf_counter() - turn_started_at) * 1000),
                            },
                        )
                        return AgentResult(
                            reply=_build_screen_observation_request_reply(),
                            actions=[*emitted_actions, screen_action],
                        )
                    prepared = ToolExecutionResult(
                        tool_name=OBSERVE_SCREEN_TOOL_NAME,
                        success=False,
                        content="",
                        error=SCREEN_OBSERVATION_DISABLED_ERROR,
                    )

                debug_log("AgentRuntime", "工具调用完成", _redact_tool_result_for_model(prepared))
                step_results.append(prepared)
                execution_results.append(prepared)
                tool_messages.extend(
                    _build_tool_messages_for_result(
                        call,
                        prepared,
                        include_images=self.model_vision_enabled,
                    )
                )
                if call.name == "search_tools":
                    active_groups.update(_groups_from_search_tools_result(prepared))
                emitted_actions.append(
                    AgentAction(
                        type="tool_call",
                        payload=_redact_tool_result_for_model(prepared),
                    )
                )

            skipped_calls = len(turn.tool_calls) - allowed_calls
            if skipped_calls > 0:
                debug_log(
                    "AgentRuntime",
                    "工具调用数量超过上限",
                    {
                        "step_index": step_index,
                        "requested": len(turn.tool_calls),
                        "allowed": allowed_calls,
                        "total_tool_calls": total_tool_calls,
                        "step_limit": MAX_TOOL_CALLS_PER_STEP,
                        "turn_limit": MAX_TOOL_CALLS_PER_TURN,
                    },
                )
                for skipped_call in turn.tool_calls[allowed_calls:]:
                    limit_error = (
                        f"本步骤最多执行 {MAX_TOOL_CALLS_PER_STEP} 个工具调用，"
                        f"整轮最多执行 {MAX_TOOL_CALLS_PER_TURN} 个工具调用，"
                        f"已跳过后续调用 {skipped_call.name}。"
                    )
                    limit_result = ToolExecutionResult(
                        tool_name="runtime",
                        success=False,
                        content={
                            "skipped": True,
                            "reason": "tool_call_limit",
                            "tool_name": skipped_call.name,
                        },
                        error=limit_error,
                    )
                    step_results.append(limit_result)
                    execution_results.append(limit_result)
                    tool_messages.extend(
                        _build_tool_messages_for_result(
                            skipped_call,
                            limit_result,
                            include_images=self.model_vision_enabled,
                        )
                    )
                    emitted_actions.append(
                        AgentAction(
                            type="tool_call",
                            payload=_redact_tool_result_for_model(limit_result),
                        )
                    )

            executed_calls = [
                _native_tool_call_to_policy_call(call, _tool_arguments_for_execution(call, self.tools))
                for call in turn.tool_calls[:allowed_calls]
            ]
            if _should_auto_snapshot_after_browser_navigation(executed_calls, step_results, self.tools):
                snapshot_result = _execute_auto_browser_snapshot(self.tools, step_index)
                step_results.append(snapshot_result)
                execution_results.append(snapshot_result)
                tool_messages.extend(
                    _build_tool_messages_for_result(
                        NativeToolCall(
                            id=f"auto_browser_snapshot_{step_index}",
                            name=BROWSER_SNAPSHOT_TOOL_NAME,
                            arguments={},
                            arguments_json="{}",
                        ),
                        snapshot_result,
                        include_images=self.model_vision_enabled,
                    )
                )
                emitted_actions.append(
                    AgentAction(
                        type="tool_call",
                        payload=_redact_tool_result_for_model(snapshot_result),
                    )
                )
                should_fast_forward_final_reply = _should_fast_forward_after_auto_browser_snapshot(
                    working_messages,
                    snapshot_result,
                )

            if pending_actions:
                debug_log(
                    "AgentRuntime",
                    "返回待确认动作",
                    {
                        "step_index": step_index,
                        "pending_actions": [action.to_dict() for action in pending_actions],
                        "tools_elapsed_ms": int((time.perf_counter() - tools_started_at) * 1000),
                        "turn_elapsed_ms": int((time.perf_counter() - turn_started_at) * 1000),
                    },
                )
                return AgentResult(
                    reply=_build_pending_action_reply(pending_actions),
                    _debug=_build_debug_meta(
                        self.api_client, execution_results,
                        total_tool_calls, turn_started_at,
                    ),
                    actions=[
                        *emitted_actions,
                        *[
                            AgentAction(
                                type="pending_action",
                                payload=action.to_dict(include_context=True),
                            )
                            for action in pending_actions
                        ],
                    ],
                )

            if not step_results:
                break

            working_messages.append(turn.message)
            working_messages.extend(tool_messages)
            if should_fast_forward_final_reply:
                debug_log(
                    "AgentRuntime",
                    "自动浏览器快照后直接进入最终总结",
                    {
                        "step_index": step_index,
                        "tool_result_count": len(execution_results),
                        "turn_elapsed_ms": int((time.perf_counter() - turn_started_at) * 1000),
                    },
                )
                break
            if total_tool_calls >= MAX_TOOL_CALLS_PER_TURN:
                break

        try:
            final_started_at = time.perf_counter()
            final_reply = self.api_client.chat(
                self._build_final_reply_prompt(),
                working_messages,
                self.reply_tones,
                self.reply_portraits,
            )
        except Exception as exc:
            print(f"[AgentRuntime] 工具结果总结失败，使用本地兜底回复：{exc}")
            debug_log("AgentRuntime", "工具结果总结失败，使用本地兜底回复", {"error": str(exc)})
            final_reply = _build_fallback_tool_reply(execution_results)
        debug_log(
            "AgentRuntime",
            "最终回复生成完成",
            {
                "segments": len(final_reply.segments),
                "actions": [_redact_tool_result_for_model(result) for result in execution_results],
                "final_reply_elapsed_ms": int((time.perf_counter() - final_started_at) * 1000),
                "turn_elapsed_ms": int((time.perf_counter() - turn_started_at) * 1000),
            },
        )
        return AgentResult(
            reply=final_reply,
            actions=emitted_actions,
        )

    def handle_confirmed_action(
        self,
        action: PendingToolAction,
        progress_callback: ProgressCallback | None = None,
    ) -> AgentResult:
        turn_started_at = time.perf_counter()
        debug_log("AgentRuntime", "执行已确认动作", action.to_dict())
        result = self.tools.execute(action.tool_name, action.arguments)
        results = [result]
        verification_result = _verify_confirmed_windows_click(self.tools, action.tool_name)
        if verification_result is not None:
            results.append(verification_result)
        emitted_actions = [
            AgentAction(
                type="tool_call",
                payload=_redact_tool_result_for_model(item),
            )
            for item in results
        ]
        if action.continuation_messages:
            if action.tool_call_id:
                confirmed_messages = [
                    _build_tool_role_message(
                        NativeToolCall(
                            id=action.tool_call_id,
                            name=action.tool_name,
                            arguments=action.arguments,
                            arguments_json=json.dumps(action.arguments, ensure_ascii=False),
                        ),
                        result,
                    )
                ]
                if self.model_vision_enabled:
                    image_message = _build_tool_result_image_message([result])
                    if image_message is not None:
                        confirmed_messages.append(image_message)
                if len(results) > 1:
                    confirmed_messages.append(_build_confirmed_action_result_message(action, results[1:]))
            else:
                confirmed_messages = [_build_confirmed_action_result_message(action, results)]
            working_messages = [
                *action.continuation_messages,
                *confirmed_messages,
            ]
            allow_screen_observation = (
                self.model_vision_enabled
                and self.autonomous_screen_observation_enabled
                and not messages_contain_image(working_messages)
                and _should_offer_screen_observation(working_messages)
            )
            debug_log(
                "AgentRuntime",
                "已确认动作接回 Agent 循环",
                {
                    "tool_name": action.tool_name,
                    "message_count": len(working_messages),
                    "allow_screen_observation": allow_screen_observation,
                },
            )
            return self._run_tool_loop(
                working_messages,
                allow_screen_observation=allow_screen_observation,
                turn_started_at=turn_started_at,
                planning_extra_instructions=_build_confirmed_action_continuation_rules(action),
                initial_actions=emitted_actions,
                progress_callback=progress_callback,
            )
        try:
            reply = self.api_client.chat(
                self._build_final_reply_prompt(),
                [
                    _build_confirmed_action_result_message(action, results),
                ],
                self.reply_tones,
                self.reply_portraits,
            )
        except Exception as exc:
            print(f"[AgentRuntime] 确认动作总结失败，使用本地兜底回复：{exc}")
            debug_log("AgentRuntime", "确认动作总结失败，使用本地兜底回复", {"error": str(exc)})
            reply = _build_fallback_tool_reply(results)
        debug_log(
            "AgentRuntime",
            "已确认动作处理完成",
            {
                "results": [_redact_tool_result_for_model(item) for item in results],
                "segments": len(reply.segments),
            },
        )
        return AgentResult(
            reply=reply,
            actions=emitted_actions,
        )

    def handle_cancelled_action(self, action: PendingToolAction) -> AgentResult:
        debug_log("AgentRuntime", "用户取消待确认动作", action.to_dict())
        return AgentResult(
            reply=parse_chat_reply(
                json.dumps(
                    {
                        "segments": [
                            {
                                "ja": "わかった。実行しないでおくね。",
                                "zh": "知道了。我不会执行这个动作。",
                                "tone": "中性",
                                "portrait": "站立待机",
                            }
                        ]
                    },
                    ensure_ascii=False,
                )
            ),
            actions=[
                AgentAction(
                    type="cancelled_action",
                    payload=action.to_dict(),
                )
            ],
        )

    def handle_event(
        self,
        event: AgentEvent,
        progress_callback: ProgressCallback | None = None,
    ) -> AgentResult:
        if event.type not in {"reminder_due", "proactive_check"}:
            return AgentResult(reply=parse_chat_reply("未対応のイベントだよ。"))

        debug_log("AgentRuntime", "处理主动事件", {"event": {"type": event.type, "payload": event.payload}})
        event_messages = _build_event_messages(event)
        event_action = AgentAction(
            type="event",
            payload={
                "event_type": event.type,
                "event_payload": event.payload,
            },
        )
        if event.type == "proactive_check":
            screen_context_allowed = bool(event.payload.get("screen_context_allowed"))
            allow_screen_observation = (
                screen_context_allowed
                and not messages_contain_image(event_messages)
            )
            return self._run_tool_loop(
                event_messages,
                allow_screen_observation=allow_screen_observation,
                turn_started_at=time.perf_counter(),
                proactive_mode=True,
                initial_actions=[event_action],
                vision_unsupported_reply=_build_proactive_vision_unsupported_reply(),
                progress_callback=progress_callback,
            )

        try:
            reply = self.api_client.chat(
                self._build_event_reply_prompt(event.type),
                event_messages,
                self.reply_tones,
                self.reply_portraits,
            )
        except ApiRequestError as exc:
            if messages_contain_image(event_messages) and is_vision_unsupported_error(exc):
                debug_log("AgentRuntime", "主动事件视觉输入不受支持，返回兜底回复", {"error": str(exc)})
                return AgentResult(reply=_build_proactive_vision_unsupported_reply())
            raise
        return AgentResult(
            reply=reply,
            actions=[event_action],
        )

    def _patched_system_prompt(self) -> str:
        parts = [self.system_prompt.strip()]
        parts.extend(
            patch.system_prompt_append.strip()
            for patch in getattr(self, "prompt_patches", [])
            if patch.system_prompt_append.strip()
        )
        return "\n\n".join(part for part in parts if part)

    def _reply_protocol_patch_text(self) -> str:
        patches = [
            patch.reply_protocol_append.strip()
            for patch in getattr(self, "prompt_patches", [])
            if patch.reply_protocol_append.strip()
        ]
        if not patches:
            return ""
        return "插件回复协议补充：\n" + "\n".join(f"- {patch}" for patch in patches)

    def _apply_reply_protocol_patches(self, reply_protocol: str) -> str:
        reply_patch = self._reply_protocol_patch_text()
        if not reply_patch:
            return reply_protocol
        return f"{reply_protocol.strip()}\n\n{reply_patch}"

    def _combine_extra_instructions(self, extra_instructions: str = "") -> str:
        parts = [extra_instructions.strip(), self._reply_protocol_patch_text()]
        return "\n".join(part for part in parts if part)

    def _build_tool_system_prompt(
        self,
        allow_screen_observation: bool = False,
        step_index: int = 0,
        remaining_steps: int = MAX_AGENT_STEPS_PER_TURN - 1,
        extra_instructions: str = "",
        browser_page_mode: bool = False,
        visible_browser_mode: bool = False,
    ) -> str:
        memory_summary = self._memory_summary()
        current_time = datetime.now().astimezone().isoformat(timespec="seconds")
        reply_protocol = self._apply_reply_protocol_patches(
            build_agent_reply_protocol(self.reply_tones, self.reply_portraits)
        )
        context_strategy = build_context_acquisition_strategy(
            allow_screen_observation=allow_screen_observation
        )
        screen_observation_rule = _build_screen_and_desktop_routing_rule(allow_screen_observation)
        browser_page_rule = _build_browser_page_mode_rule(browser_page_mode)
        visible_browser_rule = _build_visible_browser_mode_rule(visible_browser_mode)
        web_tool_capability_rule = _build_web_tool_capability_rule(visible_browser_mode)
        return f"""
{self._patched_system_prompt()}

你现在是 Sakura 的桌面陪伴型 Agent。上下文不足、需要核实或工具能明显提升帮助质量时，可以主动发起 tool_calls；信息足够时直接按回复协议回答。
不要把工具计划、工具名伪代码或 tool_calls JSON 写进正文。

长期记忆摘要：
{memory_summary}

当前本地时间：
{current_time}

当前 Agent 循环：
- 这是第 {step_index + 1} 步，之后最多还可以继续 {remaining_steps} 步。
- 每步最多请求 {MAX_TOOL_CALLS_PER_STEP} 个工具，整轮最多 {MAX_TOOL_CALLS_PER_TURN} 个工具。
- 工具结果足够、受限、需要确认或同参数失败时，停止循环并自然说明状态。

{reply_protocol}

{context_strategy}

可用工具能力领域：
{web_tool_capability_rule}
- 屏幕：理解当前画面用 observe_screen（仅启用时可用）。
- 桌面控制：窗口、鼠标、键盘和系统界面操作用 windows__*。
- 提醒与记忆：add_reminder、memory_search、memory_remember、memory_forget

工具要求：
- 只调用 API tools 列表中真实存在的工具；工具能帮助完成请求时优先发起原生 tool_calls。
- 可以在 assistant content 中写一句可直接说给用户听的短句；不要提前给最终结论。
- 不要臆造工具名；只能使用 API tools 列表中的工具。
- 高风险或 requires_confirmation 工具会在用户确认后执行；你可以发起 tool_call，但正文要简短说明为什么需要确认。
- 用户明确要求浏览器可见过程或网页操作时，用 playwright_*，不要用后台 web__ 替代。
- 浏览器外的桌面点击、输入、窗口操作才用 windows__*；操作前先用 windows__Snapshot / windows__Screenshot 获取真实状态。
{screen_observation_rule}
{browser_page_rule}
{visible_browser_rule}
- 如果 playwright_ 浏览器工具不可用，说明网页自动化能力不可用；不要回退到 Sakura 内置浏览器工具。
- 需要网页交互时，只能基于当前页面真实内容选择工具，不要臆造 selector、target 或页面内容。
{self._combine_extra_instructions(extra_instructions)}
- 用户说“几分钟后/几秒后/一会儿后”等相对提醒时，add_reminder 必须使用 delay_minutes 或 delay_seconds，不要自己换算 trigger_at。
- 只有用户给出明确日期或钟点时，add_reminder 才使用 trigger_at。
- 需要跨会话信息、用户偏好或项目状态时，优先使用 memory_search。
- 只有用户明确要求记住，或信息明显长期有用且不包含敏感凭据时，才使用 memory_remember。
- 只有用户明确要求忘掉信息时，才使用 memory_forget。
""".strip()

    def _build_proactive_tool_system_prompt(
        self,
        step_index: int = 0,
        remaining_steps: int = MAX_AGENT_STEPS_PER_TURN - 1,
        extra_instructions: str = "",
    ) -> str:
        memory_summary = self._memory_summary()
        current_time = datetime.now().astimezone().isoformat(timespec='seconds')
        return build_proactive_check_tool_system_prompt(
            self._patched_system_prompt(),
            self.reply_tones,
            self.reply_portraits,
            memory_summary=memory_summary,
            current_time=current_time,
            step_index=step_index,
            remaining_steps=remaining_steps,
            max_tool_calls_per_step=MAX_TOOL_CALLS_PER_STEP,
            max_tool_calls_per_turn=MAX_TOOL_CALLS_PER_TURN,
            extra_instructions=self._combine_extra_instructions(extra_instructions),
        )
    def _build_final_reply_prompt(self) -> str:
        return f"""
{self._patched_system_prompt()}

你会收到上一轮工具调用结果。请基于这些结果给用户最终回复。
不要再次请求工具，不要提及内部 JSON、工具协议或实现细节。
如果工具结果信息丰富，可以适当展开总结、补充细节或引导对话继续，让用户能感受到信息已经被充分理解和整理。
{self._reply_protocol_patch_text()}
""".strip()

    def _build_event_reply_prompt(self, event_type: str = "reminder_due") -> str:
        prompt = build_event_system_prompt(
            self._patched_system_prompt(),
            self.reply_tones,
            self.reply_portraits,
            event_type=event_type,
        )
        reply_patch = self._reply_protocol_patch_text()
        if reply_patch:
            return f"{prompt}\n\n{reply_patch}"
        return prompt

    def _memory_summary(self) -> str:
        try:
            return self.memory.summary()
        except Exception as exc:
            return f"长期记忆读取失败：{exc}"


def _emit_progress_from_content(
    progress_callback: ProgressCallback | None,
    content: str,
    *,
    stage: str,
    metadata: dict[str, Any],
) -> None:
    if progress_callback is None or not content.strip():
        return
    if not _should_emit_progress(metadata):
        return
    try:
        reply = parse_chat_reply(content)
    except Exception:
        return
    if not reply.text.strip():
        return
    try:
        progress_callback(AgentProgress(reply=reply, stage=stage, metadata=metadata))
    except Exception as exc:
        debug_log("AgentRuntime", "中间回复回调失败，已忽略", {"error": str(exc), "stage": stage})


def _should_emit_progress(metadata: dict[str, Any]) -> bool:
    """只播报关键等待点，避免工具链每一步都打断用户。"""
    step_index = metadata.get("step_index")
    if not isinstance(step_index, int):
        return True
    if step_index == 0:
        return True
    tool_names = metadata.get("tool_names", [])
    if not isinstance(tool_names, list):
        return False
    return any(str(name).startswith("windows__") for name in tool_names)


def _reply_has_display_translation(reply: ChatReply) -> bool:
    """最终回复需要中文显示文本，避免兼容模型的纯日语正文漏到中文字幕 UI。"""

    return any(
        segment.text.strip() and segment.translation.strip()
        for segment in reply.segments
    )


def _native_tool_call_to_policy_call(
    call: NativeToolCall,
    arguments: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "id": call.id,
        "name": call.name,
        "arguments": arguments if arguments is not None else call.arguments,
        "reason": _tool_call_reason(call),
    }


def _tool_call_reason(call: NativeToolCall) -> str:
    reason = call.arguments.get("reason")
    return reason.strip() if isinstance(reason, str) else ""


def _tool_arguments_for_execution(call: NativeToolCall, tools: ToolRegistry) -> dict[str, Any]:
    """移除规划层的 reason 字段，避免它污染真实工具参数。"""

    arguments = dict(call.arguments)
    if "reason" not in arguments:
        return arguments
    tool = tools.get(call.name)
    properties = {}
    if tool is not None and isinstance(tool.parameters, dict):
        raw_properties = tool.parameters.get("properties", {})
        if isinstance(raw_properties, dict):
            properties = raw_properties
    if "reason" not in properties:
        arguments.pop("reason", None)
    return arguments


def _groups_from_search_tools_result(result: ToolExecutionResult) -> set[str]:
    if not result.success:
        return set()
    content = result.content
    if isinstance(content, dict):
        raw_tools = content.get("tools") or content.get("results") or content.get("content")
    else:
        raw_tools = content
    if not isinstance(raw_tools, list):
        return set()
    groups: set[str] = set()
    for item in raw_tools:
        if not isinstance(item, dict):
            continue
        group = item.get("group")
        if isinstance(group, str) and group.strip():
            groups.add(group.strip())
    return groups


def _build_tool_role_message(call: NativeToolCall, result: ToolExecutionResult) -> ChatMessage:
    return {
        "role": "tool",
        "tool_call_id": call.id,
        "name": call.name,
        "content": json.dumps(_redact_tool_result_for_model(result), ensure_ascii=False, default=str),
    }


def _build_tool_messages_for_result(
    call: NativeToolCall,
    result: ToolExecutionResult,
    *,
    include_images: bool,
) -> list[ChatMessage]:
    messages = [_build_tool_role_message(call, result)]
    if include_images:
        image_message = _build_tool_result_image_message([result])
        if image_message is not None:
            messages.append(image_message)
    return messages


def _build_tool_result_image_message(results: list[ToolExecutionResult]) -> ChatMessage | None:
    images = _extract_tool_result_images(results)
    if not images:
        return None
    content: list[dict[str, Any]] = [
        {
            "type": "text",
            "text": "上一个工具结果包含截图，以下图片用于辅助判断页面视觉状态。",
        }
    ]
    content.extend(
        {
            "type": "image_url",
            "image_url": {
                "url": image_url,
                "detail": "low",
            },
        }
        for image_url in images
    )
    return {"role": "user", "content": content}


def _build_skipped_after_pending_messages(
    tool_calls: list[NativeToolCall],
    *,
    start_after_call_id: str,
) -> list[ChatMessage]:
    messages: list[ChatMessage] = []
    seen_pending = False
    for call in tool_calls:
        if call.id == start_after_call_id:
            seen_pending = True
            continue
        if not seen_pending:
            continue
        result = ToolExecutionResult(
            tool_name=call.name,
            success=False,
            content={
                "skipped": True,
                "reason": "waiting_for_previous_confirmation",
            },
            error="前一个高风险工具需要用户确认，后续同批工具调用已跳过，请在确认后重新规划。",
        )
        messages.append(_build_tool_role_message(call, result))
    return messages


def _is_screen_observation_request(result: ToolExecutionResult) -> bool:
    if result.tool_name != OBSERVE_SCREEN_TOOL_NAME or not result.success:
        return False
    if not isinstance(result.content, dict):
        return False
    return result.content.get("action") == SCREEN_OBSERVATION_REQUEST_ACTION


def _verify_confirmed_windows_click(
    tools: ToolRegistry,
    tool_name: str,
) -> ToolExecutionResult | None:
    """Windows 桌面点击后追加一次只读截图验证。"""
    if tool_name != WINDOWS_CLICK_TOOL_NAME:
        return None

    screenshot_tool = tools.get(WINDOWS_SCREENSHOT_TOOL_NAME)
    snapshot_tool = tools.get(WINDOWS_SNAPSHOT_TOOL_NAME)

    screenshot_result: ToolExecutionResult | None = None
    if screenshot_tool is not None:
        screenshot_result = tools.execute(WINDOWS_SCREENSHOT_TOOL_NAME, {})
        if screenshot_result.success or snapshot_tool is None:
            return screenshot_result

    if snapshot_tool is not None:
        snapshot_result = tools.execute(
            WINDOWS_SNAPSHOT_TOOL_NAME,
            {
                "use_vision": True,
                "use_ui_tree": False,
            },
        )
        if snapshot_result.success or screenshot_result is None:
            return snapshot_result
        return ToolExecutionResult(
            tool_name="windows__verification",
            success=False,
            content="",
            error=(
                f"Screenshot 验证失败：{screenshot_result.error or '未知错误'}；"
                f"Snapshot 验证失败：{snapshot_result.error or '未知错误'}"
            ),
        )

    return ToolExecutionResult(
        tool_name="windows__verification",
        success=False,
        content="",
        error="没有可用的 windows__Screenshot 或 windows__Snapshot，无法自动验证点击结果。",
    )


def _filter_tools_for_browser_routing(
    tools: list[dict[str, Any]],
    *,
    browser_page_mode: bool,
    visible_browser_mode: bool,
) -> list[dict[str, Any]]:
    return ToolPolicy.filter_tools_for_browser_routing(
        tools,
        browser_page_mode=browser_page_mode,
        visible_browser_mode=visible_browser_mode,
    )


def _filter_openai_tools_for_browser_routing(
    tools: list[dict[str, Any]],
    *,
    browser_page_mode: bool,
    visible_browser_mode: bool,
) -> list[dict[str, Any]]:
    if not browser_page_mode and not visible_browser_mode:
        return tools
    filtered_names = {
        str(item.get("name", ""))
        for item in _filter_tools_for_browser_routing(
            [
                {"name": tool.get("function", {}).get("name")}
                for tool in tools
                if isinstance(tool.get("function"), dict)
            ],
            browser_page_mode=browser_page_mode,
            visible_browser_mode=visible_browser_mode,
        )
    }
    return [
        tool
        for tool in tools
        if isinstance(tool.get("function"), dict)
        and str(tool["function"].get("name", "")) in filtered_names
    ]


def _should_block_windows_tool_for_browser_page(
    call: dict[str, Any],
    browser_page_mode: bool,
) -> bool:
    return ToolPolicy.should_block_windows_tool_for_browser_page(call, browser_page_mode)


def _should_block_background_web_tool_for_visible_browser(
    call: dict[str, Any],
    visible_browser_mode: bool,
) -> bool:
    return ToolPolicy.should_block_background_web_tool_for_visible_browser(
        call,
        visible_browser_mode,
    )


def _should_auto_snapshot_after_browser_navigation(
    tool_calls: list[dict[str, Any]],
    step_results: list[ToolExecutionResult],
    tools: ToolRegistry,
) -> bool:
    return ToolPolicy.should_auto_snapshot_after_browser_navigation(
        tool_calls,
        step_results,
        tools,
    )


def _execute_auto_browser_snapshot(tools: ToolRegistry, step_index: int) -> ToolExecutionResult:
    arguments: dict[str, Any] = {}
    reason = "浏览器导航成功后自动读取页面内容，减少模型往返。"
    debug_log(
        "AgentRuntime",
        "自动补充浏览器页面文本",
        {
            "step_index": step_index,
            "name": BROWSER_SNAPSHOT_TOOL_NAME,
            "arguments": arguments,
            "reason": reason,
        },
    )
    prepared = tools.prepare_or_execute(BROWSER_SNAPSHOT_TOOL_NAME, arguments, reason)
    if isinstance(prepared, PendingToolAction):
        result = ToolExecutionResult(
            tool_name="runtime",
            success=False,
            content={
                "auto_tool": BROWSER_SNAPSHOT_TOOL_NAME,
                "reason": "自动页面文本读取需要用户确认，已跳过隐藏执行。",
            },
            error="自动页面文本读取需要用户确认，已跳过。",
        )
        debug_log("AgentRuntime", "自动浏览器页面文本读取需要确认，已跳过", result.to_dict())
        return result

    debug_log("AgentRuntime", "自动浏览器页面文本读取完成", _redact_tool_result_for_model(prepared))
    return prepared


def _should_fast_forward_after_auto_browser_snapshot(
    messages: list[ChatMessage],
    snapshot_result: ToolExecutionResult,
) -> bool:
    if not _latest_user_is_browser_lookup_request(messages):
        return False
    if _latest_user_is_browser_interaction_request(messages):
        return False
    return _browser_snapshot_has_readable_content(snapshot_result)


def _latest_user_is_browser_lookup_request(messages: list[ChatMessage]) -> bool:
    text = (_latest_user_text(messages) or "").lower()
    if not text:
        return False
    lookup_keywords = (
        "搜索",
        "搜一下",
        "搜一搜",
        "查",
        "查询",
        "看看",
        "看一下",
        "百科",
        "信息",
        "资料",
        "介绍",
        "告诉我",
        "说明",
        "内容",
        "总结",
        "梳理",
        "是谁",
        "是什么",
        "検索",
        "調べ",
        "情報",
        "教えて",
        "紹介",
        "search",
        "look up",
        "lookup",
        "information",
        "info",
        "tell me",
        "wiki",
        "wikipedia",
        "summary",
        "summarize",
    )
    return any(keyword in text for keyword in lookup_keywords)


def _latest_user_is_browser_interaction_request(messages: list[ChatMessage]) -> bool:
    text = (_latest_user_text(messages) or "").lower()
    if not text:
        return False
    interaction_keywords = (
        "点击",
        "点开",
        "点进",
        "输入",
        "填写",
        "登录",
        "登陆",
        "提交",
        "下载",
        "滚动",
        "选择",
        "勾选",
        "购买",
        "支付",
        "播放",
        "打开菜单",
        "切换",
        "上传",
        "发帖",
        "评论",
        "回复",
        "删除",
        "编辑",
        "下一页",
        "上一页",
        "クリック",
        "入力",
        "ログイン",
        "送信",
        "ダウンロード",
        "スクロール",
        "選択",
        "click",
        "type",
        "login",
        "log in",
        "submit",
        "download",
        "scroll",
        "select",
        "choose",
        "upload",
    )
    return any(keyword in text for keyword in interaction_keywords)


def _browser_snapshot_has_readable_content(result: ToolExecutionResult) -> bool:
    if result.tool_name != BROWSER_SNAPSHOT_TOOL_NAME or not result.success:
        return False
    text = _tool_result_content_text(result.content).strip()
    if len(text) < 20:
        return False
    normalized = text.lower()
    if _browser_snapshot_looks_like_search_results(normalized):
        return False
    blocked_markers = (
        "error executing tool",
        "http 403",
        "forbidden",
        "timeout",
        '"is_error": true',
        "'is_error': true",
        '"loading": true',
        "'loading': true",
        "加载失败",
        "访问被拒绝",
        "无法访问",
    )
    return not any(marker in normalized for marker in blocked_markers)


def _browser_snapshot_looks_like_search_results(normalized_text: str) -> bool:
    search_page_markers = (
        "google.com/search",
        "bing.com/search",
        "baidu.com/s?",
        "duckduckgo.com/",
        "search.yahoo.com/search",
        "sogou.com/web",
        "yandex.com/search",
        "google 搜索",
        "google search",
        "bing search",
        "百度一下",
        "搜索结果",
        "search results",
    )
    return any(marker in normalized_text for marker in search_page_markers)


def _tool_result_content_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    try:
        return json.dumps(content, ensure_ascii=False, default=str)
    except TypeError:
        return str(content)


def _build_browser_page_windows_tool_block_result(call: dict[str, Any]) -> ToolExecutionResult:
    tool_name = str(call.get("name", "")).strip() or "unknown"
    return ToolExecutionResult(
        tool_name="runtime",
        success=False,
        content={
            "blocked_tool": tool_name,
            "reason": "当前上下文是浏览器页面内部操作，已阻止 Windows-MCP 坐标/截图工具抢路由。",
            "guidance": (
                "请使用 playwright_navigate 直达目标 URL，或 playwright_search_web 执行可见搜索；"
                "需要页面文本后调用 playwright_get_text，视觉状态用 playwright_screenshot，"
                "点击或填写时基于真实 selector 调用 playwright_click/playwright_fill。"
            ),
        },
        error=f"已阻止 {tool_name}：浏览器页面内部操作应优先使用 playwright_ 工具。",
    )


def _build_visible_browser_web_tool_block_result(call: dict[str, Any]) -> ToolExecutionResult:
    tool_name = str(call.get("name", "")).strip() or "unknown"
    return ToolExecutionResult(
        tool_name="runtime",
        success=False,
        content={
            "blocked_tool": tool_name,
            "reason": "用户明确要求打开浏览器或看到搜索过程，已阻止后台网页搜索/抓取工具。",
            "guidance": (
                "请优先用 playwright_navigate 直接打开目标 URL，或用 playwright_search_web 搜索；"
                "再按需用 playwright_get_text、playwright_screenshot、playwright_click、"
                "playwright_fill 完成可见浏览器流程。"
            ),
        },
        error=f"已阻止 {tool_name}：显式浏览器任务应使用 playwright_ 工具，不要只做后台搜索。",
    )


def _browser_dom_tools_available(tools: ToolRegistry) -> bool:
    return ToolPolicy.browser_dom_tools_available(tools)


def _should_prefer_browser_page_tools(messages: list[ChatMessage]) -> bool:
    text = _messages_text_for_tool_routing(messages).lower()
    if "playwright_" in text:
        return True

    latest_text = (_latest_user_text(messages) or "").lower()
    if not latest_text:
        return False
    browser_keywords = (
        "浏览器",
        "网页",
        "页面",
        "链接",
        "搜索结果",
        "搜索框",
        "输入框",
        "点进",
        "点开",
        "打开网页",
        "标签页",
        "网址",
        "url",
        "http://",
        "https://",
        "百科",
        "必应",
        "bing",
        "百度",
        "google",
    )
    return any(keyword in latest_text for keyword in browser_keywords)


def _latest_user_requests_visible_browser(messages: list[ChatMessage]) -> bool:
    text = (_latest_user_text(messages) or "").lower()
    if not text:
        return False
    visible_browser_keywords = (
        "打开浏览器",
        "用浏览器",
        "浏览器搜索",
        "在浏览器",
        "打开网页",
        "打开页面",
        "看搜索过程",
        "看到搜索过程",
        "让我看到",
        "给我看搜索",
        "搜给我看",
        "可见浏览器",
        "前台浏览器",
    )
    return any(keyword in text for keyword in visible_browser_keywords)


def _recent_browser_tool_failed(messages: list[ChatMessage]) -> bool:
    recent_text = _messages_text_for_tool_routing(messages[-4:]).lower()
    return (
        "playwright_" in recent_text
        and (
            '"success": false' in recent_text
            or '"success":false' in recent_text
            or "'success': false" in recent_text
            or "'success':false" in recent_text
            or '"is_error": true' in recent_text
            or '"is_error":true' in recent_text
            or "'is_error': true" in recent_text
            or "'is_error':true" in recent_text
            or "工具执行异常" in recent_text
            or "工具执行失败" in recent_text
        )
    )


def _latest_user_explicitly_requests_windows_control(messages: list[ChatMessage]) -> bool:
    text = (_latest_user_text(messages) or "").lower()
    if not text:
        return False
    explicit_keywords = (
        "真实鼠标",
        "物理鼠标",
        "鼠标",
        "坐标",
        "windows",
        "桌面",
        "窗口",
        "浏览器窗口",
        "地址栏",
        "任务栏",
        "快捷键",
        "键盘",
        "系统界面",
    )
    return any(keyword in text for keyword in explicit_keywords)


def _messages_text_for_tool_routing(messages: list[ChatMessage]) -> str:
    return "\n".join(_compact_pending_context_content(message.get("content")) for message in messages)


def _build_browser_page_mode_rule(browser_page_mode: bool) -> str:
    if not browser_page_mode:
        return ""
    return (
        "- 当前上下文已识别为浏览器页面内部操作模式：Windows-MCP 坐标、截图、输入、滚动工具已从可用工具中隐藏。"
        "能直达 URL 时先用 playwright_navigate；需要搜索时用 playwright_search_web；"
        "搜索后如果已经出现目标站点或词条页 URL，优先直接导航到目标页，再继续读取页面正文。"
        "继续读取、截图、点击或填写页面时，必须使用 playwright_ 前缀的原生 Playwright 工具。"
    )


def _build_visible_browser_mode_rule(visible_browser_mode: bool) -> str:
    if not visible_browser_mode:
        return ""
    return (
        "- 用户明确要求打开浏览器或看到搜索过程：后台 web__ 搜索/抓取工具已从可用工具中隐藏。"
        "必须优先用 playwright_navigate 直达目标 URL，或 playwright_search_web 打开可见搜索结果；"
        "能直达页面就不要先打开搜索首页再操作输入框；"
        "需要交互时再用 playwright_get_text/screenshot/click/fill 等工具完成可见浏览器流程。"
    )


def _build_web_tool_capability_rule(visible_browser_mode: bool) -> str:
    if visible_browser_mode:
        return (
            "- 网页：本轮是显式可见浏览器任务，使用 playwright_*；"
            "后台 web__ 搜索/抓取只用于非可见浏览器的轻量公开资料。"
        )
    return "- 网页：轻量公开资料用 web__web_search / web__fetch_url；可见浏览器操作用 playwright_*。"


def _build_screen_and_desktop_routing_rule(allow_screen_observation: bool) -> str:
    if allow_screen_observation:
        return "\n".join(
            [
                "- 当用户询问当前屏幕内容、可见文字、报错含义、界面状态或“这个是什么意思”时，优先调用 observe_screen；这是 Sakura 内置视觉观察，只用于理解画面和解释，不用于鼠标坐标。",
                "- 当用户要求你点击、移动鼠标、输入、切换窗口或操作桌面应用时，不要用 observe_screen 推理坐标；改用 Windows MCP 的 windows__Snapshot / windows__Screenshot 作为操作前观察。",
            ]
        )
    return "\n".join(
        [
            "- 当前没有可用的 Sakura 内置屏幕理解工具；不要臆造当前屏幕内容。",
            "- 如果用户要求桌面点击、移动鼠标、输入或窗口操作，并且 Windows MCP 截图工具可用，先用 windows__Snapshot / windows__Screenshot 获取真实桌面状态。",
        ]
    )


def _should_offer_screen_observation(messages: list[ChatMessage]) -> bool:
    return ScreenPolicy.should_offer_screen_observation_text(_latest_user_text(messages))


def _latest_user_text(messages: list[ChatMessage]) -> str | None:
    for message in reversed(messages):
        if message.get("role") != "user":
            continue
        content = message.get("content")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts = [
                str(part.get("text", ""))
                for part in content
                if isinstance(part, dict) and part.get("type") == "text"
            ]
            return "\n".join(parts)
        return ""
    return None


def _build_pending_continuation_messages(
    working_messages: list[ChatMessage],
    assistant_message: ChatMessage,
    completed_tool_messages: list[ChatMessage],
    tool_calls: list[NativeToolCall],
    *,
    pending_call_id: str,
) -> list[ChatMessage]:
    """为待确认动作保存原生 tool_calls 上下文，确认后可继续回填 tool role。"""
    messages = [
        *_compact_messages_for_pending_context(working_messages),
        _compact_message_for_pending_context(assistant_message),
        *[
            _compact_message_for_pending_context(message)
            for message in completed_tool_messages
        ],
        *_build_skipped_after_pending_messages(
            tool_calls,
            start_after_call_id=pending_call_id,
        ),
    ]
    return messages[-MAX_PENDING_CONTEXT_MESSAGES:]


def _compact_messages_for_pending_context(messages: list[ChatMessage]) -> list[ChatMessage]:
    return [_compact_message_for_pending_context(message) for message in messages]


def _compact_message_for_pending_context(message: ChatMessage) -> ChatMessage:
    role = message.get("role")
    compacted: ChatMessage = {
        "role": role if isinstance(role, str) and role else "user",
        "content": _compact_pending_context_content(message.get("content")),
    }
    tool_call_id = message.get("tool_call_id")
    if isinstance(tool_call_id, str) and tool_call_id:
        compacted["tool_call_id"] = tool_call_id
    name = message.get("name")
    if isinstance(name, str) and name:
        compacted["name"] = name
    tool_calls = message.get("tool_calls")
    if isinstance(tool_calls, list):
        compacted["tool_calls"] = tool_calls
    return compacted


def _compact_pending_context_content(content: Any) -> str:
    if isinstance(content, str):
        return _truncate_pending_context_text(content)
    if isinstance(content, list):
        parts: list[str] = []
        for part in content:
            if not isinstance(part, dict):
                continue
            if part.get("type") == "text":
                text = part.get("text", "")
                parts.append(_truncate_pending_context_text(str(text)))
            elif part.get("type") == "image_url":
                parts.append("[图片内容已省略，确认后继续时请根据文本工具结果判断。]")
        return "\n".join(part for part in parts if part)
    if content is None:
        return ""
    try:
        text = json.dumps(content, ensure_ascii=False, default=str)
    except TypeError:
        text = str(content)
    return _truncate_pending_context_text(text)


def _truncate_pending_context_text(text: str) -> str:
    if len(text) <= MAX_PENDING_CONTEXT_TEXT_CHARS:
        return text
    head_chars = max(1, MAX_PENDING_CONTEXT_TEXT_CHARS // 2)
    tail_chars = MAX_PENDING_CONTEXT_TEXT_CHARS - head_chars
    return (
        text[:head_chars]
        + f"\n...[已省略 {len(text) - head_chars - tail_chars} 字确认上下文]...\n"
        + text[-tail_chars:]
    )


def _build_tool_results_message(
    results: list[ToolExecutionResult],
    include_images: bool = False,
) -> ChatMessage:
    text = _format_tool_results_for_model(results)
    images = _extract_tool_result_images(results) if include_images else []
    if not images:
        return {"role": "user", "content": text}

    content: list[dict[str, Any]] = [{"type": "text", "text": text}]
    content.extend(
        {
            "type": "image_url",
            "image_url": {
                "url": image_url,
                "detail": "low",
            },
        }
        for image_url in images
    )
    return {"role": "user", "content": content}


def _build_confirmed_action_result_message(
    action: PendingToolAction,
    results: list[ToolExecutionResult],
) -> ChatMessage:
    text = (
        "用户刚刚确认并执行了一个待确认工具动作。"
        "这不是新的用户任务，请结合此前上下文继续完成原请求；"
        "如果该动作只是中间步骤，不要把当前窗口状态误当成新问题。\n"
        f"已确认动作：{action.tool_name}\n"
        f"动作参数：{json.dumps(action.arguments, ensure_ascii=False, default=str)}\n"
        f"动作原因：{action.reason or '未提供'}\n\n"
        + _format_tool_results_for_model(results)
    )
    return {"role": "user", "content": text}


def _build_confirmed_action_continuation_rules(action: PendingToolAction) -> str:
    rules = [
        "确认动作续接规则：",
        f"- 用户刚刚确认执行了 {action.tool_name}，这只是前一轮任务的一个中间步骤。",
        "- 不要把工具执行后的界面当成用户发起的新闲聊问题；必须回到前文的原始用户目标继续推进。",
        "- 如果动作成功但任务尚未完成，请继续请求下一步必要工具；如果已经完成，再给最终回复。",
        "- 如果刚打开的是 Windows“运行”窗口，且前文已经计划通过命令完成任务，应继续输入/提交对应命令，而不是询问用户想使用什么工具。",
    ]
    if action.tool_name.startswith("playwright_"):
        rules.append(
            "- 刚确认执行的是 playwright_ 工具，后续网页内点击、输入、读取、截图仍应继续使用 playwright_ 工具；不要因为页面可见就切换到 windows__ 坐标点击。"
        )
    return "\n".join(rules)


def _format_tool_results_for_model(results: list[ToolExecutionResult]) -> str:
    return (
        "工具执行结果如下，请据此给用户最终回复。"
        "如果工具结果标记已附加浏览器截图，请结合截图兜底判断页面内容，不要臆造看不到的信息：\n"
        + json.dumps(
            [_redact_tool_result_for_model(result) for result in results],
            ensure_ascii=False,
            indent=2,
        )
    )


def _redact_tool_result_for_model(result: ToolExecutionResult) -> dict[str, Any]:
    data = result.to_dict()
    content = data.get("content")
    if isinstance(content, str):
        data["content"] = _truncate_text_for_model(content, MAX_TOOL_RESULT_CHARS)
        return data
    if not isinstance(content, dict):
        return data

    redacted, image_count = _redact_tool_images_from_content(content)
    if image_count:
        redacted["screenshot_attached"] = True
        redacted["screenshot_image_count"] = image_count
    data["content"] = _truncate_value_for_model(redacted, MAX_TOOL_RESULT_CHARS)
    return data


def _truncate_value_for_model(value: Any, max_chars: int) -> Any:
    text = json.dumps(value, ensure_ascii=False, default=str)
    if len(text) <= max_chars:
        return value
    head_chars = max(1, max_chars // 2)
    tail_chars = max(0, max_chars - head_chars)
    return {
        "truncated": True,
        "original_chars": len(text),
        "omitted_chars": max(0, len(text) - head_chars - tail_chars),
        "head": text[:head_chars],
        "tail": text[-tail_chars:] if tail_chars else "",
    }


def _truncate_text_for_model(text: str, max_chars: int) -> str | dict[str, Any]:
    if len(text) <= max_chars:
        return text
    head_chars = max(1, max_chars // 2)
    tail_chars = max(0, max_chars - head_chars)
    return {
        "truncated": True,
        "original_chars": len(text),
        "omitted_chars": max(0, len(text) - head_chars - tail_chars),
        "head": text[:head_chars],
        "tail": text[-tail_chars:] if tail_chars else "",
    }


def _extract_tool_result_images(results: list[ToolExecutionResult]) -> list[str]:
    images: list[str] = []
    for result in results:
        if not isinstance(result.content, dict):
            continue
        images.extend(_extract_image_data_urls_from_value(result.content))
    return images[:1]


def _redact_tool_images_from_content(content: dict[str, Any]) -> tuple[dict[str, Any], int]:
    image_count = 0

    def redact(value: Any) -> Any:
        nonlocal image_count
        if isinstance(value, dict):
            if _mcp_image_item_to_data_url(value) is not None:
                image_count += 1
                return {
                    "type": value.get("type", "image"),
                    "image_attached": True,
                    "mime_type": _mcp_image_mime_type(value),
                }
            redacted_dict: dict[str, Any] = {}
            for key, item in value.items():
                if key in {"screenshot_data_url", "mcp_image_data_urls"}:
                    if isinstance(item, str) and item.startswith("data:image/"):
                        image_count += 1
                    elif isinstance(item, list):
                        image_count += len(
                            [
                                image_url
                                for image_url in item
                                if isinstance(image_url, str) and image_url.startswith("data:image/")
                            ]
                        )
                    continue
                redacted_dict[str(key)] = redact(item)
            return redacted_dict
        if isinstance(value, list):
            return [redact(item) for item in value]
        return value

    redacted = redact(content)
    return redacted if isinstance(redacted, dict) else {}, image_count


def _extract_image_data_urls_from_value(value: Any) -> list[str]:
    images: list[str] = []
    if isinstance(value, dict):
        screenshot = value.get("screenshot_data_url")
        if isinstance(screenshot, str) and screenshot.startswith("data:image/"):
            images.append(screenshot)

        mcp_images = value.get("mcp_image_data_urls")
        if isinstance(mcp_images, list):
            images.extend(
                image_url
                for image_url in mcp_images
                if isinstance(image_url, str) and image_url.startswith("data:image/")
            )

        data_url = _mcp_image_item_to_data_url(value)
        if data_url is not None:
            images.append(data_url)

        for item in value.values():
            images.extend(_extract_image_data_urls_from_value(item))
    elif isinstance(value, list):
        for item in value:
            images.extend(_extract_image_data_urls_from_value(item))
    return _deduplicate_preserving_order(images)


def _mcp_image_item_to_data_url(item: dict[str, Any]) -> str | None:
    if str(item.get("type", "")).lower() != "image":
        return None
    data = item.get("data")
    if not isinstance(data, str) or not data.strip():
        return None
    if data.startswith("data:image/"):
        return data
    mime_type = _mcp_image_mime_type(item)
    if not mime_type.startswith("image/"):
        return None
    return f"data:{mime_type};base64,{data}"


def _mcp_image_mime_type(item: dict[str, Any]) -> str:
    mime_type = item.get("mimeType")
    if not isinstance(mime_type, str) or not mime_type.strip():
        mime_type = item.get("mime_type")
    if not isinstance(mime_type, str) or not mime_type.strip():
        mime_type = "image/png"
    return mime_type.strip()


def _deduplicate_preserving_order(items: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        result.append(item)
    return result


def _build_pending_action_reply(actions: list[PendingToolAction]) -> ChatReply:
    if len(actions) == 1:
        action = actions[0]
        text = _describe_pending_action(action)
        return parse_chat_reply(
            json.dumps(
                {
                    "segments": [
                        {
                            "ja": "実行する前に確認させて。",
                            "zh": f"执行前需要你确认：{text}",
                            "tone": "请求",
                            "portrait": "伸手命令",
                        }
                    ]
                },
                ensure_ascii=False,
            )
        )

    return parse_chat_reply(
        json.dumps(
            {
                "segments": [
                    {
                        "ja": "いくつか確認が必要な操作があるよ。",
                        "zh": f"有 {len(actions)} 个动作需要你确认，我会先处理第一个。",
                        "tone": "请求",
                        "portrait": "伸手命令",
                    }
                ]
            },
            ensure_ascii=False,
        )
    )


def _describe_pending_action(action: PendingToolAction) -> str:
    if action.tool_name == "open_url":
        return f"打开网页 {action.arguments.get('url', '')}"
    if action.tool_name == "open_local_folder":
        return f"打开文件夹 {action.arguments.get('path', '')}"
    if action.tool_name.startswith("playwright_"):
        return f"执行浏览器操作 {action.tool_name.removeprefix('playwright_')}"
    if action.tool_name.startswith("windows__"):
        return f"执行 Windows 桌面 MCP 操作 {action.tool_name.removeprefix('windows__')}"
    return f"执行 {action.tool_name}"


def _build_screen_observation_request_reply() -> ChatReply:
    return parse_chat_reply(
        json.dumps(
            {
                "segments": [
                    {
                        "ja": "画面を確認してから答えるね。",
                        "zh": "我先看一下当前画面再回答。",
                        "tone": "请求",
                        "portrait": "伸手命令",
                    }
                ]
            },
            ensure_ascii=False,
        )
    )


def _build_fallback_tool_reply(results: list[ToolExecutionResult]) -> ChatReply:
    if not results:
        return parse_chat_reply("ツール結果の確認に失敗したよ。")

    succeeded = [result for result in results if result.success]
    failed = [result for result in results if not result.success]
    if succeeded and not failed:
        summary = _summarize_tool_results(succeeded)
        return parse_chat_reply(
            json.dumps(
                {
                    "segments": [
                        {
                            "ja": f"処理は終わったよ。{summary}",
                            "zh": f"已经处理好了。{summary}",
                            "tone": "请求",
                            "portrait": "自信拍胸",
                        }
                    ]
                },
                ensure_ascii=False,
            )
        )

    error_text = "；".join(
        f"{result.tool_name}: {result.error or '执行失败'}"
        for result in failed
    )
    return parse_chat_reply(
        json.dumps(
            {
                "segments": [
                    {
                        "ja": "処理中に問題が起きたみたい。設定かネットワークを確認して。",
                        "zh": f"工具执行时出了点问题：{error_text}",
                        "tone": "困惑",
                        "portrait": "张嘴疑问",
                    }
                ]
            },
            ensure_ascii=False,
        )
    )


def _build_vision_unsupported_reply() -> ChatReply:
    return parse_chat_reply(
        json.dumps(
            {
                "segments": [
                    {
                        "ja": "今のモデルでは画像を見られないみたい。画面の内容は勝手に想像しないでおくね。",
                        "zh": "当前模型或接口似乎不支持图片输入。我不会猜屏幕内容，请换成支持视觉的模型后再试。",
                        "tone": "困惑",
                        "portrait": "张嘴疑问",
                    }
                ]
            },
            ensure_ascii=False,
        )
    )


def _summarize_tool_results(results: list[ToolExecutionResult]) -> str:
    parts: list[str] = []
    for result in results:
        if isinstance(result.content, dict):
            if isinstance(result.content.get("reminder"), dict):
                reminder = result.content["reminder"]
                text = reminder.get("text", "")
                trigger_at = reminder.get("trigger_at", "")
                parts.append(f"提醒「{text}」已设置在 {trigger_at}。")
            elif isinstance(result.content.get("task"), dict):
                task = result.content["task"]
                parts.append(f"待办「{task.get('text', '')}」已更新。")
            elif isinstance(result.content.get("forgotten"), dict):
                memory = result.content["forgotten"]
                content = memory.get("content") or memory.get("id", "")
                parts.append(f"记忆「{content}」已删除。")
            elif isinstance(result.content.get("memory"), dict):
                memory = result.content["memory"]
                parts.append(f"记忆「{memory.get('content', '')}」已更新。")
            elif result.content.get("status") == "loading":
                parts.append(str(result.content.get("message", "工具正在初始化。")))
            elif result.tool_name == "open_url":
                parts.append(f"网页已打开：{result.content.get('url', '')}。")
            elif result.tool_name == "open_local_folder":
                parts.append(f"文件夹已打开：{result.content.get('path', '')}。")
            elif result.tool_name == "read_note":
                parts.append(f"笔记「{result.content.get('name', '')}」已读取。")
            elif result.tool_name == "write_note":
                parts.append(f"笔记「{result.content.get('name', '')}」已保存。")
            else:
                parts.append(f"{result.tool_name} 已完成。")
        else:
            parts.append(f"{result.tool_name} 已完成。")
    return " ".join(part for part in parts if part).strip()


def _build_event_messages(event: AgentEvent) -> list[ChatMessage]:
    text = _format_event_for_model(event)
    image_parts = _build_event_screen_context_image_parts(event.payload)
    if not image_parts:
        return [{"role": "user", "content": text}]

    return [
        {
            "role": "user",
            "content": [
                {
                    "type": "text",
                    "text": text,
                },
                *image_parts,
            ],
        }
    ]


def _build_event_screen_context_image_parts(payload: dict[str, Any]) -> list[dict[str, Any]]:
    screen_contexts = payload.get("screen_contexts")
    image_parts: list[dict[str, Any]] = []
    if isinstance(screen_contexts, list):
        for screen_context in screen_contexts:
            if isinstance(screen_context, dict):
                image_part = _build_screen_context_image_part(screen_context)
                if image_part is not None:
                    image_parts.append(image_part)
    if image_parts:
        return image_parts

    screen_context = payload.get("screen_context")
    if isinstance(screen_context, dict):
        image_part = _build_screen_context_image_part(screen_context)
        if image_part is not None:
            return [image_part]
    return []


def _build_screen_context_image_part(screen_context: dict[str, Any]) -> dict[str, Any] | None:
    data_url = screen_context.get("data_url")
    if not isinstance(data_url, str) or not data_url.startswith("data:image/"):
        return None
    return {
        "type": "image_url",
        "image_url": {
            "url": data_url,
            "detail": "low",
        },
    }


def _format_event_for_model(event: AgentEvent) -> str:
    instruction = (
        "主动事件如下，请基于屏幕或事件内容生成要直接说给用户听的自然搭话："
        if event.type == "proactive_check"
        else "主动事件如下，请生成要直接说给用户听的提醒："
    )
    return instruction + "\n" + json.dumps(
        _redact_event_for_model(event),
        ensure_ascii=False,
        indent=2,
    )


def _redact_event_for_model(event: AgentEvent) -> dict[str, Any]:
    payload = dict(event.payload)
    recent_conversation = payload.get("recent_conversation")
    if isinstance(recent_conversation, list):
        payload["recent_conversation"] = _sanitize_event_recent_conversation(
            recent_conversation,
        )
    screen_context = payload.get("screen_context")
    if isinstance(screen_context, dict):
        payload["screen_context"] = _redact_screen_context_for_model(screen_context)
    screen_contexts = payload.get("screen_contexts")
    if isinstance(screen_contexts, list):
        payload["screen_contexts"] = [
            _redact_screen_context_for_model(screen_context)
            if isinstance(screen_context, dict)
            else screen_context
            for screen_context in screen_contexts
        ]
    return {
        "type": event.type,
        "payload": payload,
    }


def _sanitize_event_recent_conversation(
    recent_conversation: list[Any],
) -> list[dict[str, str]]:
    sanitized: list[dict[str, str]] = []
    for item in recent_conversation:
        if not isinstance(item, dict):
            continue
        role = str(item.get("role", "")).strip()
        if role not in {"user", "assistant"}:
            continue
        content = item.get("content")
        if not isinstance(content, str):
            continue
        normalized_content = " ".join(content.split())
        if not normalized_content:
            continue
        sanitized.append(
            {
                "role": role,
                "content": _truncate_event_recent_conversation_content(
                    normalized_content,
                ),
            }
        )
    return sanitized[-MAX_EVENT_RECENT_CONVERSATION_MESSAGES:]


def _truncate_event_recent_conversation_content(content: str) -> str:
    if len(content) <= MAX_EVENT_RECENT_CONVERSATION_CONTENT_CHARS:
        return content
    return content[: MAX_EVENT_RECENT_CONVERSATION_CONTENT_CHARS - 1].rstrip() + "…"


def _redact_screen_context_for_model(screen_context: dict[str, Any]) -> dict[str, Any]:
    redacted_context = dict(screen_context)
    if redacted_context.pop("data_url", None):
        redacted_context["image_attached"] = True
    return redacted_context


def _build_proactive_vision_unsupported_reply() -> ChatReply:
    return parse_chat_reply(
        json.dumps(
            {
                "segments": [
                    {
                        "ja": "今のモデルでは画面までは見られないみたい。勝手に想像しないで、少しだけ休憩の合図にしておくね。",
                        "zh": "当前模型似乎还不能看屏幕。我不会乱猜，就先轻轻提醒你休息一下。",
                        "tone": "请求",
                        "portrait": "伸手命令",
                    }
                ]
            },
            ensure_ascii=False,
        )
    )



def _build_debug_meta(
    api_client: Any,
    execution_results: list,
    total_tool_calls: int,
    turn_started_at: float,
) -> dict[str, Any]:
    """构建写入聊天记录的调试元数据，包含工具调用摘要和耗时。"""
    return {
        "model": getattr(api_client, "model", getattr(api_client, "model_name", "unknown")),
        "turn_elapsed_ms": int((time.perf_counter() - turn_started_at) * 1000),
        "tool_calls_total": total_tool_calls,
        "tool_results": [
            {
                "name": result.tool_name,
                "success": result.success,
                "error": result.error or "",
            }
            for result in execution_results
        ],
    }
