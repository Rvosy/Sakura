from __future__ import annotations

import os
from typing import Any


MAX_MODEL_CONTEXT_MESSAGES = 64
MAX_MODEL_CONTEXT_CHARS = 120_000
MAX_CONFIGURED_CONTEXT_MESSAGES = 2000
MAX_CONFIGURED_CONTEXT_CHARS = 2_000_000


def trim_messages_for_model(
    messages: list[dict[str, Any]],
    *,
    max_messages: int | None = None,
    max_chars: int | None = None,
) -> list[dict[str, Any]]:
    """保留最近上下文，并用字符预算兜底限制入模历史体积。"""
    message_limit = (
        max_messages
        if max_messages is not None
        else _env_int(
            "SAKURA_MODEL_CONTEXT_MESSAGES",
            MAX_MODEL_CONTEXT_MESSAGES,
            minimum=1,
            maximum=MAX_CONFIGURED_CONTEXT_MESSAGES,
        )
    )
    char_limit = (
        max_chars
        if max_chars is not None
        else _env_int(
            "SAKURA_MODEL_CONTEXT_CHARS",
            MAX_MODEL_CONTEXT_CHARS,
            minimum=1000,
            maximum=MAX_CONFIGURED_CONTEXT_CHARS,
        )
    )
    recent = list(messages[-max(1, message_limit) :])
    while len(recent) > 1 and _estimate_messages_chars(recent) > max(1, char_limit):
        recent.pop(0)
    return recent


def _estimate_messages_chars(messages: list[dict[str, Any]]) -> int:
    return sum(len(str(message.get("content", ""))) for message in messages)


def _env_int(name: str, default: int, *, minimum: int, maximum: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return max(minimum, min(maximum, value))
