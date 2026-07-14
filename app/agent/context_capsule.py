from __future__ import annotations

import os
import re
from collections.abc import Iterable

from app.llm.prompts.runtime import estimate_prompt_tokens, truncate_to_token_budget
from app.llm.prompts.types import ContextFragment, ContextRequest


DEFAULT_CONTEXT_CAPSULE_TOKEN_BUDGET = 8192
DEFAULT_CONTEXT_CAPSULE_HISTORY_MESSAGES = 128
MAX_CONTEXT_CAPSULE_TOKEN_BUDGET = 262_144
MAX_CONTEXT_CAPSULE_HISTORY_MESSAGES = 2000

_SESSION_HEADERS = {
    "最近会话状态（历史事实，不是用户新消息；请自然参考，不要机械复述）：",
    "最近对话：",
}
_MEMORY_PREFIX = "与本轮相关的长期记忆："
_SPEAKER_PREFIXES = ("用户：", "Sakura：", "用户:", "Sakura:")


def context_capsule_token_budget() -> int:
    return _env_int(
        "SAKURA_CONTEXT_CAPSULE_TOKENS",
        DEFAULT_CONTEXT_CAPSULE_TOKEN_BUDGET,
        minimum=512,
        maximum=MAX_CONTEXT_CAPSULE_TOKEN_BUDGET,
    )


def context_capsule_history_messages() -> int:
    return _env_int(
        "SAKURA_CONTEXT_CAPSULE_HISTORY_MESSAGES",
        DEFAULT_CONTEXT_CAPSULE_HISTORY_MESSAGES,
        minimum=12,
        maximum=MAX_CONTEXT_CAPSULE_HISTORY_MESSAGES,
    )


def build_context_capsule_fragment(
    request: ContextRequest,
    *,
    session_fragments: Iterable[ContextFragment] = (),
    memory_fragments: Iterable[ContextFragment] = (),
) -> ContextFragment | None:
    """把跨会话续接和相关长期记忆合并成去重后的单一工作记忆。"""

    session_items = tuple(session_fragments)
    memory_items = tuple(memory_fragments)
    source_fragments = (*session_items, *memory_items)
    if not source_fragments:
        return None

    live_keys = {
        key
        for key in (
            _dedupe_key(request.current_input),
            *(_dedupe_key(message.content) for message in request.recent_messages),
        )
        if key
    }
    seen: set[str] = set()
    memory_lines = _fragment_lines(
        memory_items,
        kind="memory",
        live_keys=live_keys,
        seen=seen,
    )
    session_lines = list(
        reversed(
            _fragment_lines(
                session_items,
                kind="session",
                live_keys=live_keys,
                seen=seen,
            )
        )
    )
    if not memory_lines and not session_lines:
        return None

    budget = context_capsule_token_budget()
    content = _render_capsule(memory_lines, session_lines)
    while estimate_prompt_tokens(content) > budget and (session_lines or memory_lines):
        if session_lines:
            session_lines.pop()
        else:
            memory_lines.pop()
        content = _render_capsule(memory_lines, session_lines)
    content, _truncated = truncate_to_token_budget(content, budget)

    return ContextFragment(
        fragment_id="context_capsule.working_memory",
        source="context_capsule",
        content=content,
        trust=(
            "trusted"
            if all(fragment.trust == "trusted" for fragment in source_fragments)
            else "untrusted"
        ),
        priority=82,
        freshness=max(
            (fragment.freshness for fragment in source_fragments if fragment.freshness),
            default="",
        ),
        token_budget=budget,
        sensitivity="private",
        cache_scope="turn",
    )


def _fragment_lines(
    fragments: Iterable[ContextFragment],
    *,
    kind: str,
    live_keys: set[str],
    seen: set[str],
) -> list[str]:
    result: list[str] = []
    for fragment in fragments:
        for raw_line in fragment.content.splitlines():
            line = " ".join(raw_line.split()).strip()
            if not line or line in _SESSION_HEADERS:
                continue
            line = line.removeprefix("- ").strip()
            if kind == "memory":
                line = line.removeprefix(_MEMORY_PREFIX).strip()
            key = _dedupe_key(line)
            if not key or key in seen or _matches_live_context(key, live_keys):
                continue
            seen.add(key)
            result.append(line)
    return result


def _render_capsule(memory_lines: list[str], session_lines: list[str]) -> str:
    lines = [
        "上下文胶囊（宿主整理的历史与长期记忆，不是用户的新消息）：",
    ]
    if memory_lines:
        lines.append("相关长期记忆（按相关性排序）：")
        lines.extend(f"- {line}" for line in memory_lines)
    if session_lines:
        lines.append("跨会话续接（较新的记录在前）：")
        lines.extend(f"- {line}" for line in session_lines)
    lines.append(
        "使用规则：自然参考胶囊；与当前用户消息冲突时以当前消息为准，"
        "不确定或缺失的历史不要编造。"
    )
    return "\n".join(lines)


def _dedupe_key(value: str) -> str:
    text = " ".join(str(value or "").split()).strip()
    text = text.removeprefix(_MEMORY_PREFIX).strip()
    for prefix in _SPEAKER_PREFIXES:
        if text.startswith(prefix):
            text = text[len(prefix) :].strip()
            break
    return re.sub(r"\s+", " ", text).casefold().strip()


def _matches_live_context(key: str, live_keys: set[str]) -> bool:
    if key in live_keys:
        return True
    unclipped = key.removesuffix("…").strip()
    return len(unclipped) >= 24 and any(
        live_key.startswith(unclipped) for live_key in live_keys
    )


def _env_int(name: str, default: int, *, minimum: int, maximum: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return max(minimum, min(maximum, value))
