from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Mapping, Sequence


@dataclass(frozen=True)
class PromptContext:
    """提示词场景渲染所需的上下文。"""

    character_prompt: str = ""
    reply_tones: list[str] | None = None
    reply_portraits: list[str] | None = None
    memory_summary: str = ""
    current_time: str = ""
    step_index: int = 0
    remaining_steps: int = 0
    max_tool_calls_per_step: int = 0
    max_tool_calls_per_turn: int = 0
    extra_instructions: str = ""
    allow_screen_observation: bool = False
    event_type: str = "reminder_due"


@dataclass(frozen=True)
class PromptBlock:
    """可复用提示词块。"""

    title: str | None
    body: str


@dataclass(frozen=True)
class ContextMessage:
    role: str
    content: str


@dataclass(frozen=True)
class ContextRequest:
    """可安全暴露给上下文贡献者的运行时事实。"""

    current_input: str = ""
    source: Literal["chat", "event", "confirmed_action"] = "chat"
    mode: Literal["normal", "proactive"] = "normal"
    event_type: str = ""
    step_index: int = 0
    remaining_steps: int = 0
    recent_messages: tuple[ContextMessage, ...] = ()
    available_tools: tuple[str, ...] = ()
    visual_summaries: tuple[str, ...] = ()
    screen_context_available: bool = False
    seconds_since_pet_interaction: float | None = None
    service_status: Mapping[str, str] = field(default_factory=dict)
    current_time: str = ""


@dataclass(frozen=True)
class ContextFragment:
    """一个可预算、可追踪的动态事实片段。"""

    fragment_id: str
    source: str
    content: str
    trust: Literal["trusted", "untrusted"] = "untrusted"
    priority: int = 50
    freshness: str = ""
    token_budget: int = 512
    sensitivity: Literal["public", "private", "sensitive"] = "private"
    cache_scope: Literal["turn", "step"] = "turn"
    provider_order: float = 100.0
    required: bool = False


@dataclass(frozen=True)
class ContextFragmentDecision:
    fragment: ContextFragment
    estimated_tokens: int
    included: bool
    truncated: bool = False
    drop_reason: str = ""


@dataclass(frozen=True)
class ContextSnapshot:
    request: ContextRequest
    selected: tuple[ContextFragmentDecision, ...] = ()
    dropped: tuple[ContextFragmentDecision, ...] = ()
    estimated_tokens: int = 0
    token_budget: int = 0


@dataclass(frozen=True)
class PromptSection:
    section_id: str
    body: str
    title: str | None = None
    source: str = "host"
    trust: Literal["trusted", "untrusted"] = "trusted"
    priority: int = 50
    token_budget: int = 0
    sensitivity: Literal["public", "private", "sensitive"] = "public"
    cache_scope: Literal["static", "turn", "step"] = "static"
    required: bool = True


@dataclass(frozen=True)
class PromptRecipe:
    name: str
    blocks: Sequence[PromptSection]


@dataclass(frozen=True)
class PromptSectionInspection:
    section_id: str
    source: str
    trust: str
    sensitivity: str
    cache_scope: str
    chars: int
    estimated_tokens: int
    included: bool
    truncated: bool = False
    drop_reason: str = ""
    static_hash: str = ""


@dataclass(frozen=True)
class PromptInspection:
    recipe_name: str
    sections: tuple[PromptSectionInspection, ...]
    total_chars: int
    estimated_tokens: int
    runtime_role: str = "system"
    redacted_prompt: str = ""

    def to_dict(self, *, include_content: bool = False) -> dict[str, Any]:
        data: dict[str, Any] = {
            "recipe_name": self.recipe_name,
            "sections": [section.__dict__ for section in self.sections],
            "total_chars": self.total_chars,
            "estimated_tokens": self.estimated_tokens,
            "runtime_role": self.runtime_role,
        }
        if include_content:
            data["redacted_prompt"] = self.redacted_prompt
        return data


@dataclass(frozen=True)
class PromptBuildResult:
    system_prompt: str
    runtime_context: str
    inspection: PromptInspection
