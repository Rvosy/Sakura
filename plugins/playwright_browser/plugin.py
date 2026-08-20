from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any, Callable

from plugins.playwright_browser import browser
from plugins.playwright_browser.config_model import (
    BROWSER_CHOICES,
    PlaywrightBrowserConfig,
    config_from_mapping,
    config_to_mapping,
)


PLUGIN_ID = "playwright_browser"
SETTINGS_SECTION_ID = "playwright_browser"
_CALLBACK_RESULT_BUDGET = 48 * 1024
_TRUNCATED_PREVIEW_BUDGET = 24 * 1024


class PlaywrightBrowserPlugin:
    """Sakura bundled browser automation through ordinary v3 Host Services."""

    def setup(self, context: object) -> None:
        config = getattr(context, "config")
        runtime_config = config_from_mapping(getattr(config, "get")())

        def saved_config() -> PlaywrightBrowserConfig:
            return config_from_mapping(getattr(config, "get")())

        browser.set_config_loader(lambda: runtime_config)

        def cleanup_browser() -> None:
            try:
                browser.shutdown_browser()
            finally:
                browser.set_config_loader(None)

        getattr(context, "effect")(cleanup_browser)
        getattr(config, "on_change")(lambda _values: "restart_required")

        tools = getattr(context, "get")("sakura.host.tools")
        artifacts = getattr(context, "get")("sakura.host.artifacts")
        for descriptor, callback in _tool_registrations(artifacts):
            tools.register(descriptor, callback)

        getattr(context, "get")("sakura.host.settings").register(
            _settings_descriptor(),
            load=lambda: config_to_mapping(saved_config()),
            save=lambda values: _save_settings(config, values),
        )


def _tool_registrations(
    artifacts: object,
) -> list[tuple[dict[str, Any], Callable[[Mapping[str, Any]], Any]]]:
    return [
        (
            {
                "name": "playwright_navigate",
                "description": "使用 Playwright 浏览器打开网页 URL，并返回当前页面标题。",
                "parameters": _object_schema({"url": {"type": "string"}}, ["url"]),
                "group": "browser",
                "risk": "medium",
            },
            lambda args: _bounded_callback_result(browser.navigate(str(args["url"]))),
        ),
        (
            {
                "name": "playwright_get_text",
                "description": "读取当前 Playwright 页面文本。selector 默认 body。",
                "parameters": _object_schema({"selector": {"type": "string"}}, []),
                "group": "browser",
                "risk": "low",
            },
            lambda args: _bounded_callback_result(
                browser.get_text(str(args.get("selector", "body") or "body"))
            ),
        ),
        (
            {
                "name": "playwright_search_web",
                "description": "使用 Playwright 浏览器执行网页搜索，并返回结构化搜索结果。",
                "parameters": _object_schema(
                    {
                        "query": {"type": "string"},
                        "limit": {"type": "integer"},
                    },
                    ["query"],
                ),
                "group": "browser",
                "risk": "medium",
            },
            lambda args: _bounded_callback_result(
                browser.search_web(
                    str(args["query"]),
                    int(args.get("limit", 5)),
                )
            ),
        ),
        (
            {
                "name": "playwright_screenshot",
                "description": "截取当前 Playwright 页面截图，并以图像结果返回。",
                "parameters": _object_schema({"full_page": {"type": "boolean"}}, []),
                "group": "browser",
                "risk": "medium",
            },
            lambda args: _capture_screenshot(
                artifacts,
                bool(args.get("full_page", False)),
            ),
        ),
        (
            {
                "name": "playwright_click",
                "description": "点击当前 Playwright 页面中的 CSS selector。",
                "parameters": _object_schema({"selector": {"type": "string"}}, ["selector"]),
                "group": "browser",
                "risk": "medium",
            },
            lambda args: _bounded_callback_result(browser.click(str(args["selector"]))),
        ),
        (
            {
                "name": "playwright_fill",
                "description": "向当前 Playwright 页面中的 CSS selector 输入文本。",
                "parameters": _object_schema(
                    {
                        "selector": {"type": "string"},
                        "value": {"type": "string"},
                    },
                    ["selector", "value"],
                ),
                "group": "browser",
                "risk": "medium",
            },
            lambda args: _bounded_callback_result(
                browser.fill(str(args["selector"]), str(args["value"]))
            ),
        ),
        (
            {
                "name": "playwright_evaluate",
                "description": "在当前 Playwright 页面执行 JavaScript 代码。",
                "parameters": _object_schema({"js_code": {"type": "string"}}, ["js_code"]),
                "group": "browser",
                "risk": "high",
            },
            lambda args: _bounded_callback_result(browser.evaluate(str(args["js_code"]))),
        ),
    ]


def _capture_screenshot(artifacts: object, full_page: bool) -> dict[str, Any]:
    allocation = getattr(artifacts, "allocate")(
        {"mediaType": "image/jpeg", "suffix": ".jpg"}
    )
    artifact_id = allocation["artifactId"]
    try:
        content = _bounded_callback_result(
            browser.screenshot(allocation["path"], full_page=full_page)
        )
        descriptor = getattr(artifacts, "commit")(artifact_id)
    except Exception:
        getattr(artifacts, "release")(artifact_id)
        raise
    return {"content": content, "artifact": descriptor}


def _bounded_callback_result(value: Any) -> Any:
    encoded = json.dumps(value, ensure_ascii=False).encode("utf-8")
    if len(encoded) <= _CALLBACK_RESULT_BUDGET:
        return value
    if isinstance(value, str):
        return _truncate_string_for_json(value, _CALLBACK_RESULT_BUDGET)
    preview = _truncate_string_for_json(
        encoded.decode("utf-8"),
        _TRUNCATED_PREVIEW_BUDGET,
    )
    return {
        "truncated": True,
        "originalBytes": len(encoded),
        "preview": preview,
    }


def _truncate_string_for_json(value: str, maximum: int) -> str:
    if len(json.dumps(value, ensure_ascii=False).encode("utf-8")) <= maximum:
        return value
    marker = f"\n...[truncated {len(value)} chars]...\n"
    low = 0
    high = len(value)
    best = marker
    while low <= high:
        kept = (low + high) // 2
        head = (kept + 1) // 2
        tail = kept // 2
        candidate = value[:head] + marker + (value[-tail:] if tail else "")
        if len(json.dumps(candidate, ensure_ascii=False).encode("utf-8")) <= maximum:
            best = candidate
            low = kept + 1
        else:
            high = kept - 1
    return best


def _settings_descriptor() -> dict[str, Any]:
    return {
        "sectionId": SETTINGS_SECTION_ID,
        "title": "Playwright 浏览器",
        "order": 40,
        "fields": [
            {
                "key": "browser_type",
                "label": "浏览器类型",
                "type": "select",
                "default": "msedge",
                "options": [
                    {"value": key, "label": _browser_label(key)}
                    for key in BROWSER_CHOICES
                ],
                "restartRequired": True,
            },
            {
                "key": "headless",
                "label": "无头模式",
                "type": "boolean",
                "default": False,
                "description": "无头模式（Headless）",
                "restartRequired": True,
            },
        ],
    }


def _save_settings(config: object, values: Mapping[str, Any]) -> list[str]:
    current = getattr(config, "get")()
    merged = {**dict(current), **dict(values)}
    normalized = config_to_mapping(config_from_mapping(merged))
    updates = {key: normalized[key] for key in values if key in normalized}
    return getattr(config, "update")(updates)


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
