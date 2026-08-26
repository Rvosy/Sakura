from __future__ import annotations

import pytest
from unittest.mock import MagicMock

from app.agent.context_orchestrator import (
    ContextOrchestrator,
    build_context_request,
    messages_for_context_snapshot,
)
from app.agent.runtime import AgentRuntime, _build_tool_role_message
from app.agent.tools import ToolExecutionResult
from app.agent.trace import traced_message
from app.config.model_slots import resolve_model_slot
from app.config.models import ApiConfigProfile, ModelSelectionSettings, ModelSlotSelection
from app.llm.api_client import ApiSettings, NativeToolCall, OpenAICompatibleClient
from app.llm.prompts.runtime import (
    ContextBudget,
    ContextPolicy,
    ContextWindowExceededError,
    calculate_context_budget,
    estimate_context_runtime_tokens,
    estimate_prompt_tokens,
    truncate_to_token_budget,
)
from app.llm.prompts.types import ContextFragment, ContextRequest, ContextTurn


def _history(turns: int, *, chars: int = 120) -> list[dict[str, object]]:
    messages: list[dict[str, object]] = []
    for index in range(turns):
        turn_id = f"turn-{index:03d}"
        messages.extend(
            [
                traced_message(
                    {"role": "user", "content": "问" * chars},
                    "history",
                    turn_id=turn_id,
                ),
                traced_message(
                    {"role": "assistant", "content": "答" * chars},
                    "history",
                    turn_id=turn_id,
                ),
            ]
        )
    return messages


def test_explicit_128k_selects_more_than_old_message_and_token_caps() -> None:
    messages = [
        *_history(30),
        traced_message({"role": "user", "content": "继续"}, "user_input"),
    ]
    request = build_context_request(
        messages,
        source="chat",
        mode="normal",
        event_type="",
        step_index=0,
        remaining_steps=1,
        available_tools=(),
    )

    snapshot = ContextOrchestrator().build_snapshot(
        request,
        messages=messages,
        static_prompt="system prompt",
        context_window_tokens=131_072,
        window_source="user",
        max_tokens=4_096,
    )
    selected = messages_for_context_snapshot(messages, snapshot)

    assert snapshot.context_window_tokens == 131_072
    assert snapshot.window_source == "user"
    assert snapshot.token_budget > 4_096
    assert len(snapshot.selected_turns) == 30
    assert len(selected) == 61


def test_unknown_model_uses_explainable_32k_fallback_budget() -> None:
    messages = [traced_message({"role": "user", "content": "hello"}, "user_input")]
    request = build_context_request(
        messages,
        source="chat",
        mode="normal",
        event_type="",
        step_index=0,
        remaining_steps=0,
        available_tools=(),
    )

    snapshot = ContextOrchestrator().build_snapshot(
        request,
        messages=messages,
        static_prompt="system",
        context_window_tokens=32_768,
        window_source="fallback",
    )

    assert snapshot.context_window_tokens == 32_768
    assert snapshot.window_source == "fallback"
    assert snapshot.input_target == 24_576
    assert snapshot.output_reserve == 4_096
    assert snapshot.safety_margin == 1_639
    assert snapshot.estimator == "conservative"


def test_one_million_token_context_window_keeps_a_proportional_budget() -> None:
    budget = calculate_context_budget(
        context_window_tokens=1_000_000,
        window_source="user",
        max_tokens=None,
        static_prompt_tokens=2_000,
        tool_schema_tokens=500,
        current_required_tokens=1_500,
    )

    assert budget.context_window_tokens == 1_000_000
    assert budget.window_source == "user"
    assert budget.output_reserve == 8_192
    assert budget.safety_margin == 50_000
    assert budget.input_target == 750_000
    assert budget.context_budget == 746_000


def test_required_host_facts_are_full_or_fail_with_their_rendered_envelope() -> None:
    request = ContextRequest(current_time="2026-08-26T12:00:00+08:00")
    orchestrator = ContextOrchestrator()

    snapshot = orchestrator.build_snapshot(
        request,
        messages=[],
        static_prompt="system",
        context_window_tokens=32_768,
        window_source="fallback",
    )

    required = [item for item in snapshot.selected if item.fragment.required]
    assert {item.fragment.fragment_id for item in required} == {
        "runtime.time",
        "runtime.agent_progress",
    }
    assert all(not item.truncated for item in required)
    assert not [item for item in snapshot.dropped if item.fragment.required]

    with pytest.raises(ContextWindowExceededError, match="CONTEXT_WINDOW_EXCEEDED"):
        orchestrator.build_snapshot(
            request,
            messages=[],
            static_prompt="s" * 4_000,
            context_window_tokens=4_096,
            window_source="user",
        )


def test_chat_model_slot_propagates_explicit_window_without_model_name_guessing() -> None:
    resolved = resolve_model_slot(
        [
            ApiConfigProfile(
                id="provider",
                alias="Provider",
                base_url="https://example.invalid/v1",
                api_key="secret",
                models=("opaque-model-name",),
            )
        ],
        ModelSelectionSettings(
            chat=ModelSlotSelection(
                profile_id="provider",
                model="opaque-model-name",
                context_window_tokens=131_072,
            )
        ),
        "chat",
        ApiSettings("", "", ""),
    )

    assert resolved is not None
    assert resolved.settings.context_window_tokens == 131_072
    assert resolved.settings.context_window_source == "user"


def test_current_tool_result_over_window_fails_without_truncation() -> None:
    result_text = "结" * 7_000
    message = _build_tool_role_message(
        NativeToolCall("call-1", "large", {}, "{}"),
        ToolExecutionResult("large", True, result_text),
    )
    messages = [message]
    request = ContextRequest()

    with pytest.raises(ContextWindowExceededError, match="CONTEXT_WINDOW_EXCEEDED"):
        ContextOrchestrator().build_snapshot(
            request,
            messages=messages,
            static_prompt="system",
            context_window_tokens=4_096,
            window_source="user",
        )

    assert result_text in str(messages[0]["content"])
    assert "truncated" not in str(messages[0]["content"])


def test_agent_runtime_rejects_required_atoms_before_provider_call() -> None:
    client = MagicMock(spec=OpenAICompatibleClient)
    client.settings = ApiSettings(
        "https://example.invalid/v1",
        "secret",
        "model",
        context_window_tokens=4_096,
        context_window_source="user",
    )
    runtime = AgentRuntime(client, "system")

    with pytest.raises(ContextWindowExceededError):
        runtime.handle_user_message([{"role": "user", "content": "问" * 2_000}])

    client.complete_with_tools.assert_not_called()


def test_old_history_is_selected_and_dropped_as_complete_turns_in_order() -> None:
    messages = _history(3, chars=8)
    turns = [
        ContextTurn(
            turn_id=f"turn-{index:03d}",
            estimated_tokens=100,
        )
        for index in range(3)
    ]
    budget = ContextBudget(
        context_window_tokens=4_096,
        window_source="user",
        input_target=3_000,
        output_reserve=1_000,
        safety_margin=1_024,
        required_tokens=0,
        context_budget=150,
    )

    snapshot = ContextPolicy().select(
        ContextRequest(),
        (),
        history_turns=turns,
        budget=budget,
    )
    selected = messages_for_context_snapshot(messages, snapshot)

    assert [item.turn_id for item in snapshot.selected_turns] == ["turn-002"]
    assert [item["role"] for item in selected] == ["user", "assistant"]
    assert selected == messages[-2:]
    assert {item.turn_id for item in snapshot.dropped_turns} == {
        "turn-000",
        "turn-001",
    }


def test_fragment_budget_is_aggregated_by_contributor_source() -> None:
    fragments = [
        ContextFragment("a", "plugin:memory", "甲" * 80, token_budget=100),
        ContextFragment("b", "plugin:memory", "乙" * 80, token_budget=100),
        ContextFragment("c", "plugin:other", "丙" * 80, token_budget=100),
    ]
    budget = ContextBudget(
        context_window_tokens=4_096,
        window_source="user",
        input_target=3_000,
        output_reserve=1_000,
        safety_margin=1_024,
        required_tokens=0,
        context_budget=500,
    )

    snapshot = ContextPolicy().select(ContextRequest(), fragments, budget=budget)

    memory_tokens = sum(
        estimate_prompt_tokens(item.fragment.content)
        for item in snapshot.selected
        if item.fragment.source == "plugin:memory"
    )
    other_tokens = sum(
        estimate_prompt_tokens(item.fragment.content)
        for item in snapshot.selected
        if item.fragment.source == "plugin:other"
    )
    assert memory_tokens <= 100
    assert other_tokens == 80


def test_optional_fragment_envelopes_share_the_global_budget() -> None:
    fragments = [
        ContextFragment(
            f"fragment-{index}",
            "plugin:memory",
            "甲",
            token_budget=1_000,
        )
        for index in range(100)
    ]
    budget = ContextBudget(
        context_window_tokens=4_096,
        window_source="user",
        input_target=1_024,
        output_reserve=2_048,
        safety_margin=1_024,
        required_tokens=124,
        context_budget=900,
    )

    snapshot = ContextPolicy().select(ContextRequest(), fragments, budget=budget)
    rendered_tokens = estimate_context_runtime_tokens(
        item.fragment for item in snapshot.selected
    )

    assert rendered_tokens <= budget.context_budget
    assert len(snapshot.selected) < len(fragments)


def test_tiny_fragment_budget_never_grows_due_to_truncation_suffix() -> None:
    rendered, truncated = truncate_to_token_budget("甲" * 20, 1)

    assert truncated is True
    assert estimate_prompt_tokens(rendered) <= 1
