"""Public Assistant result, observation and runtime limit values; no host dependencies."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

if __package__:
    from .sakura_visual_control import raw_visual_control
else:
    from sakura_visual_control import raw_visual_control

DEFAULT_TONE = "中性"

@dataclass(frozen=True, init=False)
class ChatSegment:
    text: str
    tone: str = DEFAULT_TONE
    translation: str = ""
    portrait: str = ""
    suppress_tts: bool = False
    control: Any = None

    def __init__(
        self,
        text: str = "",
        tone: str = DEFAULT_TONE,
        translation: str = "",
        portrait: str = "",
        suppress_tts: bool = False,
        control: Any = None,
    ) -> None:
        object.__setattr__(self, "text", text)
        object.__setattr__(self, "tone", tone)
        object.__setattr__(self, "translation", translation)
        object.__setattr__(self, "portrait", portrait)
        object.__setattr__(self, "suppress_tts", suppress_tts)
        object.__setattr__(self, "control", raw_visual_control(control))

    def display_text(self, subtitle_language: str) -> str:
        """按字幕语言返回气泡显示文本；缺少译文时回退日文原文。"""
        if subtitle_language == "zh" and self.translation.strip():
            return self.translation.strip()
        return self.text


@dataclass(frozen=True)
class ChatReply:
    segments: list[ChatSegment]

    @property
    def text(self) -> str:
        return "\n".join(segment.text for segment in self.segments if segment.text.strip()).strip()

    @property
    def translation(self) -> str:
        return "\n".join(
            segment.display_text("zh")
            for segment in self.segments
            if segment.display_text("zh").strip()
        ).strip()

    def display_text(self, subtitle_language: str) -> str:
        if subtitle_language == "zh":
            return self.translation or self.text
        return self.text

    @property
    def tone(self) -> str:
        for segment in self.segments:
            if segment.text.strip() and segment.tone.strip():
                return segment.tone.strip()
        return DEFAULT_TONE


@dataclass(frozen=True)
class ScreenObservation:
    """一次按需屏幕观察结果，不负责持久化截图内容。"""

    data_url: str
    width: int
    height: int
    captured_at: str
    screen_name: str


# 每轮对话最多允许的 Agent 决策步数
MAX_AGENT_STEPS_PER_TURN = 4

# 每步最多允许的工具调用数
MAX_TOOL_CALLS_PER_STEP = 3

# 整轮最多允许的工具调用总数
MAX_TOOL_CALLS_PER_TURN = 8

# 可配置工具循环限制的 UI/配置边界。默认值仍使用上面的 MAX_* 常量，保持旧行为。
MIN_AGENT_STEPS_PER_TURN = 1
MAX_CONFIGURABLE_AGENT_STEPS_PER_TURN = 12
MIN_TOOL_CALLS_PER_STEP = 1
MAX_CONFIGURABLE_TOOL_CALLS_PER_STEP = 10
MIN_TOOL_CALLS_PER_TURN = 1
MAX_CONFIGURABLE_TOOL_CALLS_PER_TURN = 30

@dataclass(frozen=True)
class RuntimeLoopSettings:
    """Agent 工具循环的可配置运行时限制。"""

    max_agent_steps_per_turn: int = MAX_AGENT_STEPS_PER_TURN
    max_tool_calls_per_step: int = MAX_TOOL_CALLS_PER_STEP
    max_tool_calls_per_turn: int = MAX_TOOL_CALLS_PER_TURN

    def normalized(self) -> "RuntimeLoopSettings":
        steps = _clamp_int(
            self.max_agent_steps_per_turn,
            MIN_AGENT_STEPS_PER_TURN,
            MAX_CONFIGURABLE_AGENT_STEPS_PER_TURN,
        )
        per_step = _clamp_int(
            self.max_tool_calls_per_step,
            MIN_TOOL_CALLS_PER_STEP,
            MAX_CONFIGURABLE_TOOL_CALLS_PER_STEP,
        )
        per_turn = _clamp_int(
            self.max_tool_calls_per_turn,
            MIN_TOOL_CALLS_PER_TURN,
            MAX_CONFIGURABLE_TOOL_CALLS_PER_TURN,
        )
        # 整轮上限小于单步上限会让 UI 行为难理解，归一化时自动抬高。
        per_turn = max(per_turn, per_step)
        return RuntimeLoopSettings(
            max_agent_steps_per_turn=steps,
            max_tool_calls_per_step=per_step,
            max_tool_calls_per_turn=per_turn,
        )


def normalize_runtime_loop_settings(settings: RuntimeLoopSettings | None) -> RuntimeLoopSettings:
    """归一化可空设置，供启动、设置页和测试复用。"""
    return (settings or RuntimeLoopSettings()).normalized()


def _clamp_int(value: object, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = minimum
    return max(minimum, min(maximum, parsed))
