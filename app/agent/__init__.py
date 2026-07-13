from __future__ import annotations

from importlib import import_module
from typing import Any

__all__ = [
    "AgentAction",
    "AgentEvent",
    "AgentProgress",
    "AgentResult",
    "AgentRuntime",
    "MAX_AGENT_STEPS_PER_TURN",
    "MAX_TOOL_CALLS_PER_STEP",
    "MAX_TOOL_CALLS_PER_TURN",
    "MCPToolProvider",
    "MemoryStore",
    "PendingToolAction",
    "ProgressCallback",
    "ReminderStore",
    "RuntimeLoopSettings",
    "ScheduledReminder",
    "Tool",
    "ToolExecutionResult",
    "ToolMetadata",
    "ToolPermissionPolicy",
    "ToolRegistry",
    "create_builtin_tool_registry",
    "register_mcp_tools_from_config",
]


_EXPORT_MODULES = {
    "AgentAction": "app.agent.actions",
    "AgentEvent": "app.agent.actions",
    "AgentProgress": "app.agent.actions",
    "AgentResult": "app.agent.actions",
    "PendingToolAction": "app.agent.actions",
    "create_builtin_tool_registry": "app.agent.builtin_tools",
    "MemoryStore": "app.agent.memory",
    "MCPToolProvider": "app.agent.mcp.provider",
    "register_mcp_tools_from_config": "app.agent.mcp.provider",
    "ReminderStore": "app.agent.reminders",
    "ScheduledReminder": "app.agent.reminders",
    "AgentRuntime": "app.agent.runtime",
    "Tool": "app.agent.tools",
    "ToolExecutionResult": "app.agent.tools",
    "ToolMetadata": "app.agent.tools",
    "ToolPermissionPolicy": "app.agent.tools",
    "ToolRegistry": "app.agent.tools",
    "MAX_AGENT_STEPS_PER_TURN": "app.agent.runtime_limits",
    "MAX_TOOL_CALLS_PER_STEP": "app.agent.runtime_limits",
    "MAX_TOOL_CALLS_PER_TURN": "app.agent.runtime_limits",
    "ProgressCallback": "app.agent.runtime_limits",
    "RuntimeLoopSettings": "app.agent.runtime_limits",
}


def __getattr__(name: str) -> Any:
    """按需加载公开符号，避免纯配置模块导入完整 Agent/Qt 依赖链。"""
    module_name = _EXPORT_MODULES.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(import_module(module_name), name)
    globals()[name] = value
    return value
