"""Agent 运行时限制常量和类型。

将 MAX_* 常量从 runtime.py 中提取出来，
让这些限制值可以被独立测试和引用。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from app.agent.actions import AgentProgress

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

# 工具结果截断字符数
MAX_TOOL_RESULT_CHARS = 6000

# 屏幕观察续跑时保留的消息数上限
MAX_CONTINUATION_CONTEXT_MESSAGES = 12

# 屏幕观察续跑时保留的文本字符上限
MAX_CONTINUATION_CONTEXT_TEXT_CHARS = 4000

# 主动事件中保留的最近对话消息数上限
MAX_EVENT_RECENT_CONVERSATION_MESSAGES = 12

# 主动事件中保留的最近对话文本字符上限
MAX_EVENT_RECENT_CONVERSATION_CONTENT_CHARS = 800

# 进度回调类型
ProgressCallback = Callable[[AgentProgress], None]


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
