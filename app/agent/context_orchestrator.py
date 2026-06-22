from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from typing import Any, Iterable, Sequence

from app.core.debug_log import debug_log
from app.llm.api_client import ChatMessage
from app.llm.prompts.runtime import ContextPolicy
from app.llm.prompts.types import (
    ContextFragment,
    ContextMessage,
    ContextRequest,
    ContextSnapshot,
)
from app.plugins.models import ContextProviderContribution


MAX_CONTEXT_INPUT_CHARS = 4000
MAX_CONTEXT_RECENT_MESSAGES = 8
MAX_CONTEXT_MESSAGE_CHARS = 1000
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
        memory_fragments: Iterable[ContextFragment] = (),
    ) -> ContextSnapshot:
        fragments = [*_builtin_fragments(request), *session_fragments, *memory_fragments]
        fragments.extend(_collect_provider_fragments(request, providers))
        return self.policy.select(request, fragments)


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
    current_input = next(
        (item.content for item in reversed(recent_messages) if item.role == "user"),
        "",
    )
    payload = event_payload or {}
    seconds_since = _optional_float(payload.get("seconds_since_pet_interaction"))
    return ContextRequest(
        current_input=_truncate(current_input, MAX_CONTEXT_INPUT_CHARS),
        character_id=character_id.strip(),
        character_name=character_name.strip(),
        source=source if source in {"chat", "event", "confirmed_action"} else "chat",  # type: ignore[arg-type]
        mode=mode if mode in {"normal", "proactive"} else "normal",  # type: ignore[arg-type]
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
            debug_log(
                "ContextOrchestrator",
                "插件上下文提供者执行失败，已跳过",
                {"provider_id": provider.provider_id, "error": str(exc)},
            )
            continue
        if not isinstance(provided, Sequence) or isinstance(provided, (str, bytes)):
            debug_log(
                "ContextOrchestrator",
                "插件上下文提供者返回类型无效，已跳过",
                {"provider_id": provider.provider_id},
            )
            continue
        for index, fragment in enumerate(provided):
            if not isinstance(fragment, ContextFragment):
                debug_log(
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
            normalized.append(ContextMessage(role, _truncate(content, MAX_CONTEXT_MESSAGE_CHARS)))
    return tuple(normalized[-MAX_CONTEXT_RECENT_MESSAGES:])


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
