from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from app.llm.prompts.types import ContextFragment, ContextMessage, ContextRequest
from app.plugins.models import ContextProviderContribution
from app.sensory.models import SensoryObservation, SensorySource
from app.sensory.settings import SensorySettings
from app.sensory.store import SensoryObservationStore


_SOURCE_KEYWORDS = {
    SensorySource.VISION: ("屏幕", "截图", "画面", "看", "视觉", "窗口", "文字", "台词"),
    SensorySource.SPEECH: ("语音", "说话", "听写", "麦克风", "讲话", "转写"),
    SensorySource.SOUND: ("声音", "音效", "噪声", "响", "听到", "环境音"),
}

_TOKEN_RE = re.compile(r"[A-Za-z0-9_\-]+|[\u4e00-\u9fff]")


@dataclass(frozen=True)
class SensoryContextProvider:
    settings: SensorySettings
    store: SensoryObservationStore

    def contribution(self) -> ContextProviderContribution:
        return ContextProviderContribution(
            provider_id="sensory",
            description="感官中间件短期观察上下文",
            build_context=self.build_context,
            order=20.0,
            enabled=True,
        )

    def build_context(self, request: ContextRequest | dict[str, Any]) -> tuple[ContextFragment, ...]:
        settings = self.settings.normalized()
        if not settings.enabled or not settings.context_enabled:
            return ()
        user_text = _latest_user_text(request)
        event_type = _event_type(request)
        sources = _hinted_sources(user_text)
        raw_limit = sum(source.context_limit for source in settings.sources.values())
        observations = self.store.recent(limit=max(raw_limit, 1), sources=sources or None)
        candidates = [
            observation
            for observation in observations
            if _observation_allowed(observation, settings)
        ]
        if not candidates:
            return ""
        ranked = sorted(
            candidates,
            key=lambda observation: _score_observation(observation, user_text, event_type),
            reverse=True,
        )
        content = _format_context(
            ranked,
            user_text=user_text,
            event_type=event_type,
            budget_chars=settings.context_budget_chars,
        )
        if not content:
            return ()
        return (
            ContextFragment(
                fragment_id="recent_observations",
                source="sensory",
                content=content,
                priority=70,
                freshness=ranked[0].created_at if ranked else "",
                token_budget=max(128, min(settings.context_budget_chars, 2048)),
                sensitivity="private",
                cache_scope="turn",
            ),
        )


def _observation_allowed(observation: SensoryObservation, settings: SensorySettings) -> bool:
    source_settings = settings.sources[observation.source]
    if not source_settings.context_enabled:
        return False
    if observation.confidence < source_settings.confidence_threshold:
        return False
    return True


def _score_observation(
    observation: SensoryObservation,
    user_text: str,
    event_type: str,
) -> tuple[float, float]:
    text = _observation_text(observation)
    overlap = len(_tokens(user_text) & _tokens(text))
    source_hint = 1.0 if observation.source in _hinted_sources(user_text) else 0.0
    event_match = 1.0 if event_type and observation.event_type == event_type else 0.0
    recency = _timestamp_score(observation.created_at)
    score = (overlap * 2.0) + source_hint + event_match + observation.confidence
    return score, recency


def _format_context(
    observations: list[SensoryObservation],
    *,
    user_text: str,
    event_type: str,
    budget_chars: int,
) -> str:
    header = [
        "以下是 Sakura 感官中间件整理的短期观察摘要；它们是证据线索，不包含原始媒体，不能替代工具确认或隐私设置。",
    ]
    if user_text:
        header.append(f"当前用户输入：{_compact(user_text, 160)}")
    if event_type:
        header.append(f"当前事件：{event_type}")
    lines = ["\n".join(header)]
    current = len(lines[0])
    for observation in observations:
        block = _format_observation(observation)
        if current + len(block) + 2 > budget_chars:
            break
        lines.append(block)
        current += len(block) + 2
    return "\n\n".join(lines) if len(lines) > 1 else ""


def _format_observation(observation: SensoryObservation) -> str:
    details_text = _compact(json.dumps(observation.details, ensure_ascii=False, default=str), 260)
    lines = [
        (
            f"- sensory_id={observation.id} source={observation.source.value} "
            f"time={observation.created_at} confidence={observation.confidence:.2f}"
        ),
        f"  provider={observation.provider_id or 'unknown'} mode={observation.mode.value}",
        f"  summary={_compact(observation.summary, 320) or '无摘要'}",
    ]
    if details_text and details_text != "{}":
        lines.append(f"  details={details_text}")
    if observation.sensitive_redacted:
        lines.append("  sensitive_redacted=true")
    return "\n".join(lines)


def _latest_user_text(request: ContextRequest | dict[str, Any]) -> str:
    if isinstance(request, ContextRequest):
        return request.current_input.strip()
    direct = request.get("user_text") or request.get("latest_user_text")
    if isinstance(direct, str) and direct.strip():
        return direct.strip()
    messages = request.get("messages")
    if not isinstance(messages, list):
        return ""
    for message in reversed(messages):
        if isinstance(message, ContextMessage):
            if message.role != "user":
                continue
            text = message.content
        elif isinstance(message, dict) and message.get("role") == "user":
            text = _message_content_text(message.get("content"))
        else:
            continue
        if text.strip():
            return text.strip()
    return ""


def _event_type(request: ContextRequest | dict[str, Any]) -> str:
    if isinstance(request, ContextRequest):
        return request.event_type
    return str(request.get("event_type") or "")


def _message_content_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict):
                text = item.get("text")
                if isinstance(text, str):
                    parts.append(text)
            elif isinstance(item, str):
                parts.append(item)
        return "\n".join(parts)
    return ""


def _hinted_sources(text: str) -> set[SensorySource]:
    normalized = text.casefold()
    if not normalized:
        return set()
    result: set[SensorySource] = set()
    for source, keywords in _SOURCE_KEYWORDS.items():
        if any(keyword.casefold() in normalized for keyword in keywords):
            result.add(source)
    return result


def _observation_text(observation: SensoryObservation) -> str:
    return "\n".join(
        [
            observation.summary,
            observation.user_text,
            observation.event_type,
            json.dumps(observation.details, ensure_ascii=False, default=str),
        ]
    )


def _tokens(text: str) -> set[str]:
    return {match.group(0).casefold() for match in _TOKEN_RE.finditer(text or "")}


def _timestamp_score(value: str) -> float:
    try:
        created_at = datetime.fromisoformat(value)
    except ValueError:
        return 0.0
    return created_at.timestamp()


def _compact(text: str, limit: int) -> str:
    normalized = " ".join(str(text or "").split())
    if len(normalized) <= limit:
        return normalized
    return normalized[: max(0, limit - 8)] + "...截断"
