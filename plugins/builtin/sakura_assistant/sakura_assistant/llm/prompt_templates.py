from __future__ import annotations

from sakura_assistant.llm.prompts.blocks import (
    DEFAULT_REPLY_TONES,
    DESKTOP_PET_CONTEXT,
    JSON_ONLY_INSTRUCTION,
    build_segment_protocol as _build_segment_protocol,
    labels_or_default as _labels_or_default,
    with_desktop_pet_context,
)
from sakura_assistant.llm.prompts.recipes import (
    build_agent_reply_protocol,
    build_context_acquisition_strategy,
    build_event_system_prompt,
    build_runtime_context_text,
    build_segmented_reply_instruction,
)

__all__ = [
    "DEFAULT_REPLY_TONES",
    "DESKTOP_PET_CONTEXT",
    "JSON_ONLY_INSTRUCTION",
    "build_agent_reply_protocol",
    "build_context_acquisition_strategy",
    "build_event_system_prompt",
    "build_runtime_context_text",
    "build_segmented_reply_instruction",
    "with_desktop_pet_context",
]
