from __future__ import annotations

from pathlib import Path
from typing import Any

from app.plugins import PluginBase, PluginCapabilityRegistry, PluginContext
from app.plugins import ToolContribution, ToolsTabContribution

from plugins.playwright_browser import browser


class PlaywrightBrowserPlugin(PluginBase):
    """Sakura 内置 Playwright 浏览器插件。"""

    plugin_id = "playwright_browser"
    plugin_version = "1.0.0"

    def initialize(
        self,
        register: PluginCapabilityRegistry,
        context: PluginContext,
    ) -> None:
        plugin_root = context.plugin_root
        browser.set_plugin_root(plugin_root)
        _register_tools(register)
        register.register_tools_tab(
            ToolsTabContribution(
                tab_id="playwright_browser",
                title="Playwright 浏览器",
                build=lambda parent=None: _build_tools_tab(plugin_root, parent),
                order=40.0,
            )
        )

    def shutdown(self) -> None:
        browser.shutdown_browser()


def _register_tools(register: PluginCapabilityRegistry) -> None:
    for contribution in [
        ToolContribution(
            name="playwright_navigate",
            description="使用 Playwright 浏览器打开网页 URL，并返回当前页面标题。",
            parameters=_object_schema({"url": {"type": "string"}}, ["url"]),
            handler=lambda args: browser.navigate(str(args["url"])),
            group="browser",
            risk="medium",
            requires_confirmation=True,
        ),
        ToolContribution(
            name="playwright_get_text",
            description="读取当前 Playwright 页面文本。selector 默认 body。",
            parameters=_object_schema({"selector": {"type": "string"}}, []),
            handler=lambda args: browser.get_text(str(args.get("selector", "body") or "body")),
            group="browser",
            risk="low",
            requires_confirmation=False,
        ),
        ToolContribution(
            name="playwright_search_web",
            description="使用 Playwright 浏览器执行网页搜索，并返回结构化搜索结果。",
            parameters=_object_schema(
                {
                    "query": {"type": "string"},
                    "limit": {"type": "integer"},
                },
                ["query"],
            ),
            handler=lambda args: browser.search_web(
                str(args["query"]),
                int(args.get("limit", 5)),
            ),
            group="browser",
            risk="medium",
            requires_confirmation=True,
        ),
        ToolContribution(
            name="playwright_screenshot",
            description="截取当前 Playwright 页面截图，返回 data URL。",
            parameters=_object_schema({"full_page": {"type": "boolean"}}, []),
            handler=lambda args: browser.screenshot(bool(args.get("full_page", False))),
            group="browser",
            risk="medium",
            requires_confirmation=False,
        ),
        ToolContribution(
            name="playwright_click",
            description="点击当前 Playwright 页面中的 CSS selector。",
            parameters=_object_schema({"selector": {"type": "string"}}, ["selector"]),
            handler=lambda args: browser.click(str(args["selector"])),
            group="browser",
            risk="medium",
            requires_confirmation=True,
        ),
        ToolContribution(
            name="playwright_fill",
            description="向当前 Playwright 页面中的 CSS selector 输入文本。",
            parameters=_object_schema(
                {
                    "selector": {"type": "string"},
                    "value": {"type": "string"},
                },
                ["selector", "value"],
            ),
            handler=lambda args: browser.fill(str(args["selector"]), str(args["value"])),
            group="browser",
            risk="medium",
            requires_confirmation=True,
        ),
        ToolContribution(
            name="playwright_evaluate",
            description="在当前 Playwright 页面执行 JavaScript 代码。",
            parameters=_object_schema({"js_code": {"type": "string"}}, ["js_code"]),
            handler=lambda args: browser.evaluate(str(args["js_code"])),
            group="browser",
            risk="high",
            requires_confirmation=True,
        ),
    ]:
        register.register_tool(contribution)


def _object_schema(properties: dict[str, Any], required: list[str]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": required,
    }


def _build_tools_tab(plugin_root: Path, parent: Any = None) -> Any:
    try:
        from plugins.playwright_browser.settings_tab import PlaywrightBrowserSettingsTab
    except Exception:
        try:
            from PySide6.QtWidgets import QLabel
        except Exception:
            return None
        return QLabel("Playwright 浏览器设置加载失败。")
    return PlaywrightBrowserSettingsTab(plugin_root, parent)
