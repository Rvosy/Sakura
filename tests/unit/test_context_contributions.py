from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from threading import Barrier
from unittest.mock import MagicMock

import pytest

from sakura_assistant.agent.actions import AgentEvent
from sakura_assistant.agent.context_orchestrator import ContextContributionError, ContextOrchestrator
from sakura_assistant.agent.runtime import AgentRuntime
from sakura_assistant_contract import RuntimeLoopSettings
from sakura_tools import Tool, ToolRegistry
from sakura_cancellation import CancellationToken, OperationCancelled
from sakura_assistant.llm.api_client import (
    DialogueSettings,
    ChatCompletionTurn,
    NativeToolCall,
    AssistantModelClient,
)
from sakura_assistant_contract import ChatReply, ChatSegment
from sakura_assistant.llm.prompts.runtime import (
    ContextPolicy,
    ContextWindowExceededError,
    PromptRuntime,
    RUNTIME_CONTEXT_HEADER,
    estimate_context_runtime_tokens,
    estimate_prompt_tokens,
)
from sakura_context import ContextFragment, ContextRequest, PromptRecipe
from app.plugins.models import ContextProviderContribution


def _reply_turn(content: str | None = None) -> ChatCompletionTurn:
    content = content if content is not None else json.dumps(
        {"segments": [{"ja": "わかった。", "zh": "明白了。", "tone": "中性"}]},
        ensure_ascii=False,
    )
    return ChatCompletionTurn(content, [], {"role": "assistant", "content": content})


def _tool_turn() -> ChatCompletionTurn:
    call = NativeToolCall("call-1", "update_fixture", {}, "{}")
    return ChatCompletionTurn("", [call], {
        "role": "assistant",
        "content": "",
        "tool_calls": [{
            "id": call.id,
            "type": "function",
            "function": {"name": call.name, "arguments": "{}"},
        }],
    })


def _client() -> MagicMock:
    client = MagicMock(spec=AssistantModelClient)
    client.settings = DialogueSettings(model="model")
    client.resolve_dialogue_params.return_value = (0.8, {})
    client.complete_with_tools.return_value = _reply_turn()
    client.chat.return_value = ChatReply(
        segments=[ChatSegment(text="わかった。", translation="明白了。", tone="中性")]
    )
    return client


def _provider(callback, **kwargs) -> ContextProviderContribution:
    return ContextProviderContribution("fixture.mode", "", callback, **kwargs)


def _rule(content: str = "先说明错误，再给出改写。", **kwargs) -> ContextFragment:
    return ContextFragment("rule", "unbound", content, **kwargs)


def _tool(handler=lambda _args: {"ok": True}) -> Tool:
    return Tool(
        name="update_fixture",
        description="Update the isolated test fixture.",
        parameters={"type": "object", "properties": {}},
        handler=handler,
        group="default",
        risk="low",
    )


@pytest.mark.parametrize("event_type", [None, "reminder_due", "screen_awareness_check"])
def test_plugin_content_coexists_with_character_in_normal_and_proactive_requests(event_type) -> None:
    provider = _provider(
        lambda _request: (
            _rule("请扮演太空探险家。"), ContextFragment("lesson", "unbound", "课文内容")
        ),
        plugin_id="fixture.owner",
    )
    client = _client()
    runtime = AgentRuntime(client, "角色身份", context_providers=[provider])

    if event_type is None:
        runtime.handle_user_message([{"role": "user", "content": "帮我改写"}])
        call = client.complete_with_tools.call_args
    else:
        runtime.handle_event(AgentEvent(event_type))
        call = (
            client.complete_with_tools.call_args
            if event_type == "screen_awareness_check" else client.chat.call_args
        )

    rendered = call.kwargs["runtime_context"]
    assert RUNTIME_CONTEXT_HEADER in rendered
    assert "请扮演太空探险家。" in rendered
    assert "课文内容" in rendered
    assert 'source="plugin:fixture.owner"' in rendered
    assert "kind=" not in rendered
    assert "trust=" not in rendered
    assert "不是指令" not in rendered
    assert "最高优先级" not in rendered
    assert "角色身份" in call.args[0]
    selected = call.kwargs["trace_metadata"].snapshot.selected
    assert next(item.fragment for item in selected if item.fragment.provider_id).cache_scope == "step"


def test_optional_content_uses_default_consumer_budget_and_truncation() -> None:
    fragment = _rule("规则" * 100, token_budget=30)
    snapshot = ContextPolicy().select(ContextRequest(), [fragment])
    decision = snapshot.selected[0]
    assert decision.truncated
    assert fragment.content.startswith(decision.fragment.content.split("…", 1)[0])
    assert estimate_prompt_tokens(decision.fragment.content) <= 30


@pytest.mark.parametrize("use_model_window", [False, True])
def test_required_context_is_complete_or_fails_before_model_use(use_model_window) -> None:
    fragment = ContextFragment(
        "required", "plugin:fixture", "重要规则" * 300,
        required=True, token_budget=1,
    )
    provider = _provider(lambda _request: [fragment])
    options = {"context_window_tokens": 32_768} if use_model_window else {}
    orchestrator = ContextOrchestrator(ContextPolicy(total_budget=2_000))

    snapshot = orchestrator.build_snapshot(ContextRequest(), providers=[provider], **options)

    actual = next(item for item in snapshot.selected if item.fragment.fragment_id.endswith(".required"))
    assert actual.fragment.content == fragment.content
    assert not actual.truncated
    assert actual.fragment.required
    if use_model_window:
        options["context_window_tokens"] = 4_096
    else:
        orchestrator = ContextOrchestrator(ContextPolicy(total_budget=300))
    with pytest.raises(ContextWindowExceededError):
        orchestrator.build_snapshot(ContextRequest(), providers=[provider], **options)


def test_required_rule_budget_failure_never_sends_an_unprotected_request() -> None:
    client = _client()
    provider = _provider(lambda _request: [_rule("规则" * 30_000, required=True)])
    runtime = AgentRuntime(client, "角色", context_providers=[provider])

    with pytest.raises(ContextWindowExceededError):
        runtime.handle_user_message([{"role": "user", "content": "继续"}])

    client.complete_with_tools.assert_not_called()


@pytest.mark.parametrize("failure_policy", ["skip", "abort"])
def test_provider_failure_policy_preserves_failure_identity_and_cause(failure_policy) -> None:
    cause = RuntimeError("fixture callback failed")

    def fail(_request):
        raise cause

    client = _client()
    provider = _provider(fail, failure_policy=failure_policy, plugin_id="fixture.owner")
    runtime = AgentRuntime(client, "角色", context_providers=[provider])
    if failure_policy == "skip":
        runtime.handle_user_message([{"role": "user", "content": "继续"}])
        client.complete_with_tools.assert_called_once()
    else:
        with pytest.raises(ContextContributionError) as caught:
            runtime.handle_user_message([{"role": "user", "content": "继续"}])
        assert caught.value.__cause__ is cause
        assert caught.value.code == "CONTEXT_CONTRIBUTION_FAILED"
        assert caught.value.provider_id == provider.provider_id
        assert caught.value.plugin_id == provider.plugin_id
        client.complete_with_tools.assert_not_called()


@pytest.mark.parametrize("callback_raises", [False, True])
def test_optional_provider_cancellation_stops_before_next_callback_or_model(callback_raises) -> None:
    token = CancellationToken()

    def cancel(_request):
        if callback_raises:
            raise OperationCancelled()
        token.cancel()
        return [_rule()]

    second = MagicMock(return_value=[])
    client = _client()
    runtime = AgentRuntime(client, "角色", context_providers=[
        _provider(cancel),
        ContextProviderContribution("second", "", second),
    ])

    with pytest.raises(OperationCancelled):
        runtime.handle_user_message(
            [{"role": "user", "content": "继续"}], cancel_checker=token.throw_if_cancelled,
        )

    second.assert_not_called()
    client.complete_with_tools.assert_not_called()


def test_turn_rules_and_provider_set_survive_tools_final_summary_and_reply_repair() -> None:
    value = {"rule": "原有规则"}
    turn_callback = MagicMock(side_effect=lambda _request: [_rule(value["rule"])])
    step_callback = MagicMock(side_effect=lambda request: [
        ContextFragment("step", "unbound", f"动态资料 {request.step_index}")
    ])
    providers = [
        _provider(turn_callback, scope="turn"),
        ContextProviderContribution("fixture.dynamic", "", step_callback),
    ]
    replacement_callback = MagicMock(return_value=[_rule("下一轮规则")])
    replacement = _provider(replacement_callback, scope="turn")
    client = _client()
    runtime = AgentRuntime(
        client, "角色", context_providers=providers,
        runtime_loop_settings=RuntimeLoopSettings(max_agent_steps_per_turn=1),
    )

    def update(_args):
        value["rule"] = "中途修改"
        runtime.set_context_providers([replacement])
        return {"ok": True}

    runtime.tools = ToolRegistry([_tool(update)])
    client.complete_with_tools.side_effect = [
        _tool_turn(), _reply_turn("invalid reply"), _reply_turn(), _reply_turn(),
    ]

    runtime.handle_user_message([{"role": "user", "content": "执行工具"}])

    assert turn_callback.call_count == 1
    assert [call.args[0].step_index for call in step_callback.call_args_list] == [0, 1, 1]
    for call in client.complete_with_tools.call_args_list:
        assert "原有规则" in call.kwargs["runtime_context"]
        assert "中途修改" not in call.kwargs["runtime_context"]
        assert "下一轮规则" not in call.kwargs["runtime_context"]

    runtime.handle_user_message([{"role": "user", "content": "下一轮"}])

    replacement_callback.assert_called_once()
    assert "下一轮规则" in client.complete_with_tools.call_args.kwargs["runtime_context"]
    assert "原有规则" not in client.complete_with_tools.call_args.kwargs["runtime_context"]


@pytest.mark.parametrize("failure_stage", ["final", "text_fallback"])
def test_required_provider_failure_cannot_be_hidden_by_tool_summary_fallback(failure_stage) -> None:
    calls = 0
    fail_at = 2 if failure_stage == "final" else 3

    def contribute(_request):
        nonlocal calls
        calls += 1
        if calls == fail_at:
            raise RuntimeError("required context disappeared")
        return [_rule(required=True)]

    client = _client()
    client.complete_with_tools.side_effect = [
        _tool_turn(),
        RuntimeError("function_response.name required_field_missing"),
    ]
    runtime = AgentRuntime(
        client, "角色", tools=ToolRegistry([_tool()]),
        context_providers=[_provider(contribute, failure_policy="abort")],
        runtime_loop_settings=RuntimeLoopSettings(max_agent_steps_per_turn=1),
    )

    with pytest.raises(ContextContributionError):
        runtime.handle_user_message([{"role": "user", "content": "执行工具"}])

    assert calls == fail_at
    assert client.complete_with_tools.call_count == fail_at - 1
    client.chat.assert_not_called()


def test_turn_cache_is_isolated_between_concurrent_interactions_and_cleared_after_exit() -> None:
    orchestrator = ContextOrchestrator()
    barrier = Barrier(2)

    def interact(name):
        callback = MagicMock(return_value=[_rule(name)])
        provider = _provider(callback, scope="turn")
        with orchestrator.turn([provider]):
            first = orchestrator.build_snapshot(ContextRequest())
            barrier.wait(timeout=5)
            second = orchestrator.build_snapshot(ContextRequest())
        callback.assert_called_once()
        contents = [
            next(item.fragment.content for item in snapshot.selected if item.fragment.provider_id)
            for snapshot in (first, second)
        ]
        return contents

    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(interact, "互动一")
        second = executor.submit(interact, "互动二")
        assert first.result(timeout=10) == ["互动一", "互动一"]
        assert second.result(timeout=10) == ["互动二", "互动二"]
    assert not any(
        item.fragment.provider_id
        for item in orchestrator.build_snapshot(ContextRequest()).selected
    )


def test_neutral_context_header_and_envelopes_are_charged_to_the_rendered_budget() -> None:
    fragments = [
        _rule("必需规则", required=True),
        ContextFragment("fact", "runtime", "必需资料", required=True),
        *[
            replace(_rule("可选规则"), fragment_id=f"optional-{index}")
            for index in range(20)
        ],
        ContextFragment("optional-data", "plugin:other", "可选资料" * 300),
    ]
    budget = 300
    snapshot = ContextPolicy(total_budget=budget).select(ContextRequest(), fragments)
    rendered = PromptRuntime().build(PromptRecipe("fixture", []), snapshot).runtime_context

    assert estimate_context_runtime_tokens(item.fragment for item in snapshot.selected) <= budget
    assert rendered.count(RUNTIME_CONTEXT_HEADER) == 1
    assert any(item.drop_reason == "budget_exhausted" for item in snapshot.dropped)


def test_authenticated_provider_identity_preserves_each_fragment_budget() -> None:
    def contribute(_request):
        return [
            ContextFragment(
                local_id, "claimed-source", content * 80,
                token_budget=budget, provider_id="claimed-provider",
            )
            for local_id, content, budget in (("first", "甲", 100), ("second", "乙", 50))
        ]

    providers = [
        ContextProviderContribution(
            provider_id, "", contribute, plugin_id="fixture.shared-owner",
        )
        for provider_id in ("fixture.documents", "fixture.memory")
    ]

    snapshot = ContextOrchestrator().build_snapshot(ContextRequest(), providers=providers)

    fragments = [
        item.fragment for item in snapshot.selected
        if item.fragment.source == "plugin:fixture.shared-owner"
    ]
    assert {fragment.provider_id for fragment in fragments} == {
        "fixture.documents", "fixture.memory",
    }
    assert all(fragment.source == "plugin:fixture.shared-owner" for fragment in fragments)
    for provider in providers:
        provider_fragments = [
            fragment
            for fragment in fragments
            if fragment.provider_id == provider.provider_id
        ]
        assert len(provider_fragments) == 2
        assert provider_fragments[0].content == "甲" * 80
        assert 0 < estimate_prompt_tokens(provider_fragments[1].content) <= 50


def test_provider_name_matching_host_source_does_not_share_its_data_budget() -> None:
    session = ContextFragment(
        "session_state.recent_history", "session_state", "会话摘要" * 20,
        token_budget=80, priority=75,
    )
    plugin_data = ContextFragment(
        "plugin-facts", "unbound", "插件资料" * 20, token_budget=80,
    )
    provider = ContextProviderContribution(
        "session_state", "", lambda _request: [plugin_data], plugin_id="fixture.owner",
    )

    snapshot = ContextOrchestrator().build_snapshot(
        ContextRequest(), providers=[provider], session_fragments=[session],
    )

    selected = {item.fragment.source: item.fragment.content for item in snapshot.selected}
    assert selected["session_state"] == session.content
    assert selected["plugin:fixture.owner"] == plugin_data.content
