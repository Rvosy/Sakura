"""ToolPermissionPolicy — 统一的工具权限与确认策略。

将散落在 ToolRegistry、runtime.py、tool_policy.py 中的：
- 风险等级 → 是否需确认的映射
- free_access 模式的豁免规则
- 高风险工具的强制确认逻辑
集中到单一策略类中。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.agent.tools.registry import Tool


@dataclass
class ToolPermissionPolicy:
    """工具权限策略。

    决定：
    1. 给定工具是否需要用户确认
    2. free_access 模式下哪些工具可跳过确认
    3. 哪些工具因高风险必须始终确认
    """

    free_access_enabled: bool = True

    # ---- 高风险标记 (free_access 也不能跳过) ----

    HIGH_RISK_CONFIRMATION_PATTERNS: tuple[str, ...] = (
        "delete_file", "remove_file", "unlink_file",
        "delete_path", "remove_path",
        "delete_local_file", "remove_local_file",
    )

    # ---- 浏览器工具 (free_access 可跳过) ----

    BROWSER_FREE_ACCESS_TOOLS: frozenset[str] = frozenset({
        "playwright_navigate",
        "playwright_get_text",
        "playwright_search_web",
        "playwright_screenshot",
        "playwright_click",
        "playwright_fill",
        "playwright_evaluate",
    })

    # ---- 判断逻辑 ----

    def requires_confirmation(self, tool: Tool, arguments: dict[str, Any] | None = None) -> bool:
        """判断工具是否需要用户确认。

        返回 True 表示需要弹出确认面板。
        """
        # 工具本身不需要确认
        if not tool.requires_confirmation:
            return False

        # free_access 模式下的豁免
        if self.free_access_enabled and self._can_execute_with_free_access(tool):
            return False

        return True

    def _can_execute_with_free_access(self, tool: Tool) -> bool:
        """free_access 模式下是否可直接执行。"""
        # 高风险工具始终需要确认
        if self._is_always_high_risk(tool):
            return False
        return True

    def _is_always_high_risk(self, tool: Tool) -> bool:
        """检查是否属于不可豁免的高风险工具。"""
        if tool.risk == "high":
            return True
        if tool.confirmation_risk in {"delete_file", "file_delete", "destructive_file"}:
            return True
        normalized = tool.name.lower()
        return any(
            marker in normalized
            for marker in self.HIGH_RISK_CONFIRMATION_PATTERNS
        )

    # ---- 浏览器工具判断 ----

    def is_browser_free_access_tool(self, name: str) -> bool:
        """检查是否属于 free_access 可豁免的浏览器工具。"""
        return name in self.BROWSER_FREE_ACCESS_TOOLS
