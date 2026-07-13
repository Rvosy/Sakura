"""app/agent/tools/ — 统一工具注册系统。

本包提供：
- registry.py: Tool / ToolMetadata / ToolExecutionResult / ToolRegistry
- permission_policy.py: ToolPermissionPolicy (确认策略与风险控制)
- builtin/: 内置工具 Provider
- screen/: 屏幕观察工具 Provider
"""

from app.agent.tools.registry import (
    Tool,
    ToolExecutionResult,
    ToolHandler,
    ToolMetadata,
    ToolGroupMetadata,
    ToolConfirmationBuilder,
    ToolConfirmationPredicate,
    ToolApprovalHandler,
    ToolRegistry,
)
from app.agent.tools.permission_policy import ToolPermissionPolicy

__all__ = [
    "Tool",
    "ToolExecutionResult",
    "ToolHandler",
    "ToolMetadata",
    "ToolGroupMetadata",
    "ToolConfirmationBuilder",
    "ToolConfirmationPredicate",
    "ToolApprovalHandler",
    "ToolPermissionPolicy",
    "ToolRegistry",
]
