from __future__ import annotations

from typing import Any

from app.agent.tools import ToolExecutionResult, ToolRegistry


PLAYWRIGHT_NAVIGATE_TOOL_NAME = "playwright_navigate"
PLAYWRIGHT_GET_TEXT_TOOL_NAME = "playwright_get_text"
PLAYWRIGHT_BROWSER_TOOL_NAMES = {
    PLAYWRIGHT_NAVIGATE_TOOL_NAME,
    PLAYWRIGHT_GET_TEXT_TOOL_NAME,
    "playwright_search_web",
    "playwright_screenshot",
    "playwright_click",
    "playwright_fill",
    "playwright_evaluate",
}
BROWSER_NAVIGATE_TOOL_NAME = PLAYWRIGHT_NAVIGATE_TOOL_NAME
BROWSER_SNAPSHOT_TOOL_NAME = PLAYWRIGHT_GET_TEXT_TOOL_NAME
BROWSER_DOM_TOOL_NAMES = {
    *PLAYWRIGHT_BROWSER_TOOL_NAMES,
}
WEB_BACKGROUND_TOOL_NAMES = {
    "web__web_search",
    "web__fetch_url",
}


class ToolPolicy:
    """集中维护 Agent 对不同工具族的路由约束。"""

    @staticmethod
    def filter_tools_for_browser_routing(
        tools: list[dict[str, Any]],
        *,
        browser_page_mode: bool,
        visible_browser_mode: bool,
    ) -> list[dict[str, Any]]:
        """按浏览器路由模式隐藏容易诱导模型走错路径的工具。"""

        if not visible_browser_mode:
            return tools
        hidden_names: set[str] = set()
        hidden_names.update(WEB_BACKGROUND_TOOL_NAMES)
        return [tool for tool in tools if str(tool.get("name", "")) not in hidden_names]

    @staticmethod
    def should_block_background_web_tool_for_visible_browser(
        call: dict[str, Any],
        visible_browser_mode: bool,
    ) -> bool:
        if not visible_browser_mode:
            return False
        return str(call.get("name", "")) in WEB_BACKGROUND_TOOL_NAMES

    @staticmethod
    def should_auto_snapshot_after_browser_navigation(
        tool_calls: list[dict[str, Any]],
        step_results: list[ToolExecutionResult],
        tools: ToolRegistry,
    ) -> bool:
        """浏览器导航成功后自动补一次只读页面快照，减少固定流程的模型往返。"""

        if tools.get(BROWSER_SNAPSHOT_TOOL_NAME) is None:
            return False
        if any(call.get("name") == BROWSER_SNAPSHOT_TOOL_NAME for call in tool_calls):
            return False
        return any(
            result.tool_name == BROWSER_NAVIGATE_TOOL_NAME and result.success
            for result in step_results
        )

    @staticmethod
    def browser_dom_tools_available(tools: ToolRegistry) -> bool:
        return any(tools.get(name) is not None for name in BROWSER_DOM_TOOL_NAMES)
