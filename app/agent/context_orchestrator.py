from __future__ import annotations

import json
from dataclasses import replace
from datetime import datetime
from typing import TYPE_CHECKING, Any, Iterable, Mapping, Sequence

from app.core.runtime_log import diagnostic_attributes, log_event
from app.llm.api_client import ChatMessage
from app.agent.trace import message_provenance
from app.llm.prompts.runtime import (
    ContextPolicy,
    ContextWindowExceededError,
    calculate_context_budget,
    estimate_context_runtime_tokens,
    estimate_prompt_tokens,
)
from app.llm.token_estimation import estimate_message_tokens
from app.llm.prompts.types import (
    ContextFragment,
    ContextMessage,
    ContextRequest,
    ContextSnapshot,
    ContextTurn,
    ContextTurnDecision,
)

if TYPE_CHECKING:
    from app.plugins.models import ContextProviderContribution


MAX_VISUAL_SUMMARIES = 6
MAX_VISUAL_SUMMARY_CHARS = 500


class ContextOrchestrator:
    """收集受限事实，经统一策略选择后生成 ContextSnapshot。"""

    def __init__(self, policy: ContextPolicy | None = None) -> None:
        self.policy = policy or ContextPolicy()

    def build_snapshot(
        self,
        request: ContextRequest,
        *,
        providers: Sequence[ContextProviderContribution] = (),
        session_fragments: Iterable[ContextFragment] = (),
        messages: Sequence[ChatMessage] = (),
        static_prompt: str = "",
        tools: Sequence[dict[str, Any]] = (),
        context_window_tokens: int | None = None,
        window_source: str = "fallback",
        max_tokens: int | None = None,
        model: str = "",
    ) -> ContextSnapshot:
        fragments = [*_builtin_fragments(request), *session_fragments]
        fragments.extend(_collect_provider_fragments(request, providers))
        if context_window_tokens is None:
            return self.policy.select(request, fragments)
        turns, required_tokens, projected_drops = _history_budget_inputs(
            messages,
            model=model,
        )
        tool_schema = json.dumps(
            list(tools), ensure_ascii=False, separators=(",", ":"), default=str
        )
        budget = calculate_context_budget(
            context_window_tokens=context_window_tokens,
            window_source=window_source,
            max_tokens=max_tokens,
            static_prompt_tokens=estimate_prompt_tokens(static_prompt),
            tool_schema_tokens=estimate_prompt_tokens(tool_schema),
            current_required_tokens=required_tokens,
        )
        required_context_tokens = estimate_context_runtime_tokens(
            fragment for fragment in fragments if fragment.required
        )
        if required_context_tokens > budget.context_budget:
            raise ContextWindowExceededError(
                context_window_tokens=budget.context_window_tokens,
                window_source=budget.window_source,
                input_target=budget.input_target,
                output_reserve=budget.output_reserve,
                safety_margin=budget.safety_margin,
                static_prompt_tokens=budget.static_prompt_tokens,
                tool_schema_tokens=budget.tool_schema_tokens,
                current_required_tokens=budget.current_required_tokens,
                required_context_tokens=required_context_tokens,
                reason="input_target",
            )
        budget = replace(
            budget,
            required_tokens=budget.required_tokens + required_context_tokens,
            context_budget=budget.context_budget - required_context_tokens,
        )
        return self.policy.select(
            request,
            fragments,
            history_turns=turns,
            budget=budget,
            projected_drops=projected_drops,
        )


def build_context_request(
    messages: Sequence[ChatMessage],
    *,
    source: str,
    mode: str,
    event_type: str,
    step_index: int,
    remaining_steps: int,
    available_tools: Iterable[str],
    event_payload: dict[str, Any] | None = None,
    service_status: dict[str, str] | None = None,
    current_time: str | None = None,
    character_id: str = "",
    character_name: str = "",
) -> ContextRequest:
    recent_messages = _recent_context_messages(messages)
    current_provenance = next(
        (
            provenance
            for message in reversed(messages)
            if (provenance := message_provenance(message)) is not None
            and provenance.kind in {"user_input", "observation_input"}
        ),
        None,
    )
    current_input = next(
        (item.content for item in reversed(recent_messages) if item.role == "user"),
        "",
    )
    if (
        current_provenance is not None
        and current_provenance.kind == "observation_input"
        and not current_provenance.human_entry_id
    ):
        # A scheduled observation prompt is Host control text, not a human query.
        current_input = ""
    payload = event_payload or {}
    seconds_since = _optional_float(payload.get("seconds_since_pet_interaction"))
    return ContextRequest(
        current_input=current_input,
        character_id=character_id.strip(),
        character_name=character_name.strip(),
        current_turn_id=current_provenance.turn_id if current_provenance else "",
        source_entry_ids=current_provenance.entry_ids if current_provenance else (),
        human_entry_id=current_provenance.human_entry_id if current_provenance else "",
        observation_entry_ids=(
            current_provenance.observation_entry_ids if current_provenance else ()
        ),
        source=source if source in {"chat", "event"} else "chat",  # type: ignore[arg-type]
        mode=mode if mode in {"normal", "screen_awareness"} else "normal",  # type: ignore[arg-type]
        event_type=event_type.strip(),
        step_index=max(0, step_index),
        remaining_steps=max(0, remaining_steps),
        recent_messages=recent_messages,
        available_tools=tuple(dict.fromkeys(str(name).strip() for name in available_tools if str(name).strip())),
        visual_summaries=_visual_summaries(payload),
        screen_context_available=_screen_context_available(payload, messages),
        seconds_since_pet_interaction=seconds_since,
        service_status=dict(service_status or {}),
        current_time=current_time or datetime.now().astimezone().isoformat(timespec="seconds"),
    )


def _builtin_fragments(request: ContextRequest) -> list[ContextFragment]:
    return [
        ContextFragment(
            fragment_id="runtime.time",
            source="runtime",
            content=f"当前本地时间：{request.current_time}",
            trust="trusted",
            priority=100,
            token_budget=128,
            sensitivity="public",
            cache_scope="step",
            required=True,
        ),
        ContextFragment(
            fragment_id="runtime.agent_progress",
            source="runtime",
            content=(
                f"当前 Agent 循环是第 {request.step_index + 1} 步，"
                f"之后最多还可以继续 {request.remaining_steps} 步。"
            ),
            trust="trusted",
            priority=100,
            token_budget=128,
            sensitivity="public",
            cache_scope="step",
            required=True,
        ),
    ]


def _collect_provider_fragments(
    request: ContextRequest,
    providers: Sequence[ContextProviderContribution],
) -> list[ContextFragment]:
    fragments: list[ContextFragment] = []
    for provider in sorted(
        (item for item in providers if item.enabled),
        key=lambda item: item.order,
    ):
        try:
            provided = provider.build_context(request)
        except Exception as exc:  # noqa: BLE001
            log_event(
                "ContextOrchestrator",
                "插件上下文提供者执行失败，已跳过",
                {
                    "provider_id": provider.provider_id,
                    **diagnostic_attributes(
                        exc,
                        reason_code="CONTEXT_PROVIDER_FAILED",
                        stage="context_provider",
                    ),
                },
            )
            continue
        if not isinstance(provided, Sequence) or isinstance(provided, (str, bytes)):
            log_event(
                "ContextOrchestrator",
                "插件上下文提供者返回类型无效，已跳过",
                {"provider_id": provider.provider_id},
            )
            continue
        for index, fragment in enumerate(provided):
            if not isinstance(fragment, ContextFragment):
                log_event(
                    "ContextOrchestrator",
                    "插件上下文片段类型无效，已跳过",
                    {"provider_id": provider.provider_id, "index": index},
                )
                continue
            local_id = fragment.fragment_id.strip() or str(index)
            fragments.append(
                replace(
                    fragment,
                    fragment_id=f"plugin.{provider.provider_id}.{local_id}",
                    source=f"plugin:{provider.provider_id}",
                    trust="untrusted",
                    cache_scope="step",
                    provider_order=provider.order,
                    required=False,
                )
            )
    return fragments


def _recent_context_messages(messages: Sequence[ChatMessage]) -> tuple[ContextMessage, ...]:
    normalized: list[ContextMessage] = []
    for message in messages:
        role = str(message.get("role", "")).strip()
        if role not in {"user", "assistant"}:
            continue
        content = _message_text(message.get("content"))
        if content:
            normalized.append(ContextMessage(role, content))
    return tuple(normalized)


def messages_for_context_snapshot(
    messages: Sequence[ChatMessage],
    snapshot: ContextSnapshot,
) -> list[ChatMessage]:
    selected = {decision.turn_id for decision in snapshot.selected_turns}
    output: list[ChatMessage] = []
    for message in messages:
        provenance = message_provenance(message)
        if (
            provenance is not None
            and provenance.kind == "history"
            and provenance.turn_id
            and provenance.turn_id not in selected
        ):
            continue
        output.append(dict(message))
    return output


def _history_budget_inputs(
    messages: Sequence[ChatMessage],
    *,
    model: str = "",
) -> tuple[list[ContextTurn], int, list[ContextTurnDecision]]:
    grouped: dict[str, list[ChatMessage]] = {}
    categories: dict[str, str] = {}
    required_tokens = 0
    drops: dict[tuple[str, str], ContextTurnDecision] = {}
    for message in messages:
        provenance = message_provenance(message)
        if provenance is not None:
            for turn_id, reason, category in provenance.history_drops:
                drops[(turn_id, reason)] = ContextTurnDecision(
                    turn_id=turn_id,
                    estimated_tokens=0,
                    included=False,
                    drop_reason=reason,
                    category=(
                        category
                        if category in {"conversation", "observation"}
                        else "conversation"
                    ),  # type: ignore[arg-type]
                )
        if (
            provenance is not None
            and provenance.kind == "history"
            and provenance.turn_id
        ):
            grouped.setdefault(provenance.turn_id, []).append(message)
            category = (
                provenance.history_category
                if provenance.history_category in {"conversation", "observation"}
                else "conversation"
            )
            existing = categories.setdefault(provenance.turn_id, category)
            if existing != category:
                categories[provenance.turn_id] = "conversation"
            continue
        required_tokens += estimate_message_tokens(
            message,
            image_metadata=_message_image_metadata(provenance),
            model=model,
        )
    turns = [
        ContextTurn(
            turn_id=turn_id,
            estimated_tokens=sum(
                estimate_message_tokens(
                    item,
                    image_metadata=_message_image_metadata(message_provenance(item)),
                    model=model,
                )
                for item in turn_messages
            ),
            category=categories.get(turn_id, "conversation"),  # type: ignore[arg-type]
        )
        for turn_id, turn_messages in grouped.items()
    ]
    return turns, required_tokens, list(drops.values())


def _message_image_metadata(
    provenance: Any,
) -> tuple[Mapping[str, Any], ...]:
    if provenance is None:
        return ()
    return tuple(
        item
        for item in provenance.runtime_items
        if isinstance(item, Mapping) and item.get("kind") == "image_input"
    )


def _message_text(content: Any) -> str:
    if isinstance(content, str):
        return " ".join(content.split())
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for item in content:
        if not isinstance(item, dict) or item.get("type") != "text":
            continue
        text = item.get("text")
        if isinstance(text, str) and text.strip():
            parts.append(" ".join(text.split()))
    return " ".join(parts)


def _visual_summaries(payload: dict[str, Any]) -> tuple[str, ...]:
    summaries: list[str] = []
    candidates: list[Any] = []
    for key in ("visual_contexts", "screen_contexts"):
        value = payload.get(key)
        if isinstance(value, list):
            candidates.extend(value)
    for key in ("visual_context", "screen_context"):
        value = payload.get(key)
        if isinstance(value, dict):
            candidates.append(value)
    for item in candidates:
        if not isinstance(item, dict):
            continue
        summary = item.get("summary")
        if isinstance(summary, str) and summary.strip():
            summaries.append(_truncate(" ".join(summary.split()), MAX_VISUAL_SUMMARY_CHARS))
    return tuple(dict.fromkeys(summaries[-MAX_VISUAL_SUMMARIES:]))


def _screen_context_available(
    payload: dict[str, Any],
    messages: Sequence[ChatMessage],
) -> bool:
    if payload.get("screen_context") or payload.get("screen_contexts"):
        return True
    for message in messages:
        content = message.get("content")
        if isinstance(content, list) and any(
            isinstance(item, dict) and item.get("type") == "image_url" for item in content
        ):
            return True
    return False


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "…"


def _optional_float(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None
