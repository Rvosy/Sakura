from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any

from app.llm.prompts.runtime import estimate_prompt_tokens


LOW_OR_UNKNOWN_IMAGE_TOKENS = 1_024
HIGH_IMAGE_WITHOUT_SIZE_TOKENS = 2_048


def estimate_message_tokens(
    message: Mapping[str, Any],
    *,
    image_metadata: Sequence[Mapping[str, Any]] = (),
    model: str = "",
) -> int:
    tokens = estimate_prompt_tokens(str(message.get("role", ""))) + 4
    content = message.get("content")
    if isinstance(content, str):
        tokens += estimate_prompt_tokens(content)
    elif isinstance(content, list):
        image_index = 0
        for item in content:
            if not isinstance(item, dict):
                tokens += estimate_prompt_tokens(str(item))
            elif item.get("type") == "image_url":
                metadata = (
                    image_metadata[image_index]
                    if image_index < len(image_metadata)
                    else {}
                )
                tokens += estimate_image_tokens(item, metadata=metadata, model=model)
                image_index += 1
            elif item.get("type") == "text":
                tokens += estimate_prompt_tokens(str(item.get("text", "")))
            else:
                tokens += estimate_prompt_tokens(
                    json.dumps(item, ensure_ascii=False, separators=(",", ":"), default=str)
                )
    elif content is not None:
        tokens += estimate_prompt_tokens(str(content))
    for key in ("tool_calls", "tool_call_id", "name"):
        if key in message:
            tokens += estimate_prompt_tokens(
                json.dumps(
                    message[key],
                    ensure_ascii=False,
                    separators=(",", ":"),
                    default=str,
                )
            )
    return tokens


def estimate_message_image_tokens(
    message: Mapping[str, Any],
    *,
    image_metadata: Sequence[Mapping[str, Any]] = (),
    model: str = "",
) -> tuple[int, int]:
    content = message.get("content")
    if not isinstance(content, list):
        return 0, 0
    count = 0
    tokens = 0
    for item in content:
        if not isinstance(item, dict) or item.get("type") != "image_url":
            continue
        metadata = image_metadata[count] if count < len(image_metadata) else {}
        count += 1
        tokens += estimate_image_tokens(item, metadata=metadata, model=model)
    return count, tokens


def estimate_image_tokens(
    image_item: Mapping[str, Any],
    *,
    metadata: Mapping[str, Any] | None = None,
    model: str = "",
) -> int:
    metadata = metadata or {}
    image = image_item.get("image_url")
    detail = (
        str(image.get("detail", "")).strip().lower()
        if isinstance(image, Mapping)
        else ""
    )
    if detail in {"high", "original", "auto"}:
        width = _positive_int(metadata.get("width"))
        height = _positive_int(metadata.get("height"))
        if width is not None and height is not None:
            from app.agent.screen_awareness import (
                estimate_screen_context_image_tokens_for_size,
            )

            return max(
                LOW_OR_UNKNOWN_IMAGE_TOKENS,
                estimate_screen_context_image_tokens_for_size(
                    width,
                    height,
                    model=model,
                ),
            )
        return HIGH_IMAGE_WITHOUT_SIZE_TOKENS
    return LOW_OR_UNKNOWN_IMAGE_TOKENS


def _positive_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


__all__ = [
    "estimate_image_tokens",
    "estimate_message_image_tokens",
    "estimate_message_tokens",
]
