"""app/agent/tools/ — 统一工具注册系统。

本包提供：
- registry.py: Tool / ToolMetadata / ToolExecutionResult / ToolRegistry
- builtin/: 内置工具 Provider
- screen/: 屏幕观察工具 Provider
"""

from app.agent.tools.registry import (
    Tool,
    ToolExecutionResult,
    ToolHandler,
    ToolMetadata,
    ToolRegistry,
)

__all__ = [
    "Tool",
    "ToolExecutionResult",
    "ToolHandler",
    "ToolMetadata",
    "ToolRegistry",
]
