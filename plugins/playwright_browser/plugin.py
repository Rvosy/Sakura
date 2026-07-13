from __future__ import annotations

from typing import Any

from app.plugins import PluginBase, PluginCapabilityRegistry, PluginContext
from app.plugins import PluginSettingsContribution, PluginSettingsField, ToolContribution

from plugins.playwright_browser import browser
from plugins.playwright_browser.config_model import (
    BROWSER_CHOICES,
    PlaywrightBrowserConfig,
    config_from_mapping,
    config_to_mapping,
)


class PlaywrightBrowserPlugin(PluginBase):
    """Sakura 内置 Playwright 浏览器插件。"""

    plugin_id = "playwright_browser"
    plugin_version = "1.0.0"

    def __init__(self) -> None:
        self._context: PluginContext | None = None
        self._resource_cleanup_registered = False

    def initialize(
        self,
        register: PluginCapabilityRegistry,
        context: PluginContext,
    ) -> None:
        self._context = context
        plugin_root = context.plugin_root
        browser.set_plugin_root(plugin_root)
        browser.set_config_loader(self.config)
        resources = getattr(getattr(context, "services", None), "resources", None)
        register_cleanup = getattr(resources, "register_cleanup", None)
        if callable(register_cleanup):
            register_cleanup(browser.shutdown_browser, label="browser", shutdown_order=650)
            self._resource_cleanup_registered = True
        _register_tools(register)
        register.register_plugin_settings(
            PluginSettingsContribution(
                section_id="playwright_browser",
                title="Playwright 浏览器",
                fields=(
                    PluginSettingsField(
                        key="browser_type",
                        label="浏览器类型",
                        field_type="select",
                        default="msedge",
                        options=tuple(
                            {"value": key, "label": _browser_label(key)}
                            for key in BROWSER_CHOICES
                        ),
                        restart_required=True,
                    ),
                    PluginSettingsField(
                        key="headless",
                        label="无头模式",
                        field_type="boolean",
                        default=False,
                        description="无头模式（Headless）",
                        restart_required=True,
                    ),
                ),
                load=self.settings_values,
                save=self.save_settings_values,
                order=40.0,
            )
        )

    def shutdown(self) -> None:
        if not self._resource_cleanup_registered:
            browser.shutdown_browser()

    def config(self) -> PlaywrightBrowserConfig:
        return config_from_mapping(self._require_context().get_config())

    def settings_values(self) -> dict[str, Any]:
        return config_to_mapping(self.config())

    def save_settings_values(self, values: dict[str, Any]) -> None:
        cfg = PlaywrightBrowserConfig(
            headless=bool(values.get("headless", False)),
            browser_type=str(values.get("browser_type") or "msedge").strip().lower(),
        )
        cfg.clamp()
        self._require_context().save_config(config_to_mapping(cfg))
        browser.shutdown_browser()
        browser.set_config_loader(self.config)

    def _require_context(self) -> PluginContext:
        if self._context is None:
            raise RuntimeError("Playwright 浏览器插件尚未初始化。")
        return self._context


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


def _browser_label(key: str) -> str:
    labels = {
        "chromium": "Chromium（Playwright 内置，需下载）",
        "firefox": "Firefox（Playwright 内置，需下载）",
        "webkit": "WebKit（Playwright 内置，需下载）",
        "msedge": "Microsoft Edge（使用系统已安装的 Edge）",
        "chrome": "Google Chrome（使用系统已安装的 Chrome）",
    }
    return labels.get(key, key)
