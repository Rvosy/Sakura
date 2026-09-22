"""Agent 运行时限制常量和类型。

将 MAX_* 常量从 runtime.py 中提取出来，
让这些限制值可以被独立测试和引用。
"""

from __future__ import annotations

from collections.abc import Callable

from sakura_assistant.agent.actions import AgentProgress

from sakura_assistant_contract import (
    RuntimeLoopSettings, normalize_runtime_loop_settings,
    MAX_AGENT_STEPS_PER_TURN, MAX_TOOL_CALLS_PER_STEP, MAX_TOOL_CALLS_PER_TURN,
    MIN_AGENT_STEPS_PER_TURN, MAX_CONFIGURABLE_AGENT_STEPS_PER_TURN,
    MIN_TOOL_CALLS_PER_STEP, MAX_CONFIGURABLE_TOOL_CALLS_PER_STEP,
    MIN_TOOL_CALLS_PER_TURN, MAX_CONFIGURABLE_TOOL_CALLS_PER_TURN,
)

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
