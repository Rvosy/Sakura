from __future__ import annotations

import json
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import TYPE_CHECKING, Any, Iterable, Iterator, Mapping, Sequence

from sakura_cancellation import CancelChecker, OperationCancelled, check_cancelled
from sakura_assistant.diagnostics import diagnostic_attributes, log_event
from sakura_assistant.llm.api_client import ChatMessage
from sakura_assistant.agent.trace import message_provenance
from sakura_assistant.llm.prompts.runtime import (
    ContextPolicy,
    ContextWindowExceededError,
    calculate_context_budget,
    estimate_context_runtime_tokens,
    estimate_prompt_tokens,
)
from sakura_assistant.llm.token_estimation import estimate_message_tokens
from sakura_context import (
    ContextFragment,
    ContextMessage,
    ContextRequest,
    ContextSnapshot,
    ContextTurn,
    ContextTurnDecision,
)

if TYPE_CHECKING:
    ContextProviderContribution = Any


MAX_VISUAL_SUMMARIES = 6
MAX_VISUAL_SUMMARY_CHARS = 500


class ContextContributionError(RuntimeError):
    """无法满足本次互动的上下文贡献契约。"""

    code = "CONTEXT_CONTRIBUTION_FAILED"

    def __init__(self, provider_id: str, plugin_id: str = "") -> None:
        super().__init__(self.code)
        self.provider_id = provider_id
        self.plugin_id = plugin_id

    def public_message(self) -> str:
        owner = self.plugin_id or self.provider_id
        return f"插件 {owner} 的上下文贡献 {self.provider_id} 失败，本次互动已停止。"

    def log_attributes(self) -> dict[str, str]:
        return {
            "reason_code": self.code,
            "provider_id": self.provider_id,
            "plugin_id": self.plugin_id,
            "stage": "context_provider",
        }


@dataclass
class _ContextTurnState:
    providers: tuple[ContextProviderContribution, ...]
    cancel_checker: CancelChecker | None = None
    results: dict[int, tuple[ContextFragment, ...]] = field(default_factory=dict)


class ContextOrchestrator:
    """默认对话消费者：收集贡献、复用轮次结果并选择模型上下文。"""

    def __init__(self, policy: ContextPolicy | None = None) -> None:
        self.policy = policy or ContextPolicy()
        self.history = None
        self._turn: ContextVar[_ContextTurnState | None] = ContextVar(
            "context_contribution_turn", default=None
        )

    @contextmanager
    def turn(
        self,
        providers: Sequence[ContextProviderContribution],
        *,
        cancel_checker: CancelChecker | None = None,
    ) -> Iterator[None]:
        """一次互动固定提供者集合，并隔离本轮首次获取的 turn 贡献。"""
        state = _ContextTurnState(
            tuple(sorted((item for item in providers if item.enabled), key=lambda item: item.order)),
            cancel_checker,
        )
        token = self._turn.set(state)
        try:
            check_cancelled(cancel_checker)
            yield
        finally:
            self._turn.reset(token)

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
        cancel_checker: CancelChecker | None = None,
    ) -> ContextSnapshot:
        turn = self._turn.get()
        cancel_checker = cancel_checker or (turn.cancel_checker if turn else None)
        check_cancelled(cancel_checker)
        if self.history is not None:
            request = replace(request, recent_messages=_recent_context_messages([*self.history.recent_messages(), *messages]))
        fragments = [*_builtin_fragments(request), *session_fragments]
        fragments.extend(_collect_provider_fragments(
            request, turn.providers if turn else providers,
            turn=turn, cancel_checker=cancel_checker,
        ))
        check_cancelled(cancel_checker)
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
            history_source=self.history.source(model) if self.history is not None else None,
            budget=budget,
            projected_drops=(self.history.projected_drops if self.history is not None else projected_drops),
        )

    def messages_for_snapshot(self, messages, snapshot):
        history = self.history.messages(snapshot) if self.history is not None else []
        return [*history, *messages_for_context_snapshot(messages, snapshot)]


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
    *,
    turn: _ContextTurnState | None = None,
    cancel_checker: CancelChecker | None = None,
) -> list[ContextFragment]:
    fragments: list[ContextFragment] = []
    for provider_index, provider in enumerate(sorted(
        (item for item in providers if item.enabled),
        key=lambda item: item.order,
    )):
        check_cancelled(cancel_checker)
        if turn is not None and provider.scope == "turn" and provider_index in turn.results:
            fragments.extend(turn.results[provider_index])
            continue
        normalized: list[ContextFragment] = []
        try:
            provided = provider.build_context(request)
            check_cancelled(cancel_checker)
            if not isinstance(provided, Sequence) or isinstance(provided, (str, bytes)):
                raise ValueError("CONTEXT_RESULT_INVALID")
            for index, fragment in enumerate(provided):
                if not isinstance(fragment, ContextFragment):
                    if provider.failure_policy == "abort":
                        raise ValueError("CONTEXT_RESULT_INVALID")
                    log_event(
                        "ContextOrchestrator",
                        "插件上下文片段类型无效，已跳过",
                        {"provider_id": provider.provider_id, "index": index},
                    )
                    continue
                if fragment.required and not fragment.content.strip():
                    raise ContextContributionError(provider.provider_id, provider.plugin_id)
                local_id = fragment.fragment_id.strip() or str(index)
                normalized.append(
                    replace(
                        fragment,
                        fragment_id=f"plugin.{provider.provider_id}.{local_id}",
                        source=f"plugin:{provider.plugin_id or provider.provider_id}",
                        cache_scope=provider.scope,
                        provider_id=provider.provider_id,
                        provider_order=provider.order,
                    )
                )
        except (OperationCancelled, ContextContributionError):
            raise
        except Exception as exc:  # noqa: BLE001
            check_cancelled(cancel_checker)
            if (provider.failure_policy == "abort"
                    or getattr(exc, "code", None) == "CONTEXT_SCHEMA_INCOMPATIBLE"):
                raise ContextContributionError(
                    provider.provider_id, provider.plugin_id
                ) from exc
            log_event(
                "ContextOrchestrator",
                "插件上下文提供者执行失败，已跳过",
                {
                    "provider_id": provider.provider_id,
                    "plugin_id": provider.plugin_id,
                    **diagnostic_attributes(
                        exc,
                        reason_code="CONTEXT_PROVIDER_FAILED",
                        stage="context_provider",
                    ),
                },
            )
            normalized = []
        check_cancelled(cancel_checker)
        if turn is not None and provider.scope == "turn":
            turn.results[provider_index] = tuple(normalized)
        fragments.extend(normalized)
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
