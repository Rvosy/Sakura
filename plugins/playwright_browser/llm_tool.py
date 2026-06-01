"""
Playwright 浏览器工具 — 暴露给 LLM 的 function-calling 接口。
"""

from __future__ import annotations

import logging
import base64
from typing import Any

from sdk.tool_registry import tool

logger = logging.getLogger(__name__)

PLAYWRIGHT_TOOL_GROUP = "browser"

@tool(
    name="playwright_navigate",
    description=(
        "Navigate the headless browser to a URL. Use this to open a web page before "
        "extracting text or taking screenshots. Returns the page title."
    ),
    group=PLAYWRIGHT_TOOL_GROUP,
)
def playwright_navigate(url: str) -> dict[str, Any]:
    if not (url or "").strip():
        return {"error": "url is required"}
    try:
        from plugins.playwright_browser.browser import navigate

        result = navigate(url.strip())
        return {"ok": True, "result": result}
    except Exception as e:
        logger.exception("playwright_navigate 失败")
        return {"error": str(e)}


@tool(
    name="playwright_get_text",
    description=(
        "Extract all visible text from the current browser page. "
        "Use after playwright_navigate to read page content. "
        "Returns up to 8000 characters."
    ),
    group=PLAYWRIGHT_TOOL_GROUP,
)
def playwright_get_text() -> dict[str, Any]:
    try:
        from plugins.playwright_browser.browser import get_text

        text = get_text()
        return {"text": text, "length": len(text)}
    except Exception as e:
        logger.exception("playwright_get_text 失败")
        return {"error": str(e)}


@tool(
    name="playwright_search_web",
    description=(
        "Search the web using DuckDuckGo and return result summaries. "
        "A quick way to look up information without leaving the conversation. "
        "Use this instead of playwright_navigate when you just need search results. "
        "Parameters: query (search keywords)"
    ),
    group=PLAYWRIGHT_TOOL_GROUP,
)
def playwright_search_web(query: str) -> dict[str, Any]:
    q = (query or "").strip()
    if not q:
        return {"error": "query is required"}
    try:
        from plugins.playwright_browser.browser import search_web

        results = search_web(q)
        return {"query": q, "results": results}
    except Exception as e:
        logger.exception("playwright_search_web 失败")
        return {"error": str(e)}


@tool(
    name="playwright_screenshot",
    description=(
        "Take a screenshot of the current browser page and return it as screenshot_data_url. "
        "Use after playwright_navigate to understand visual page state, buttons, forms, or errors."
    ),
    group=PLAYWRIGHT_TOOL_GROUP,
)
def playwright_screenshot() -> dict[str, Any]:
    try:
        from plugins.playwright_browser.browser import screenshot

        png = screenshot()
        return {"screenshot_data_url": "data:image/png;base64," + base64.b64encode(png).decode("ascii")}
    except Exception as e:
        logger.exception("playwright_screenshot 失败")
        return {"error": str(e)}


@tool(
    name="playwright_click",
    description=(
        "Click an element on the current page by CSS selector. "
        "Use after playwright_navigate. Example selectors: 'button', '#id', '.class', 'a[href=...]'."
    ),
    group=PLAYWRIGHT_TOOL_GROUP,
)
def playwright_click(selector: str) -> dict[str, Any]:
    if not (selector or "").strip():
        return {"error": "selector is required"}
    try:
        from plugins.playwright_browser.browser import click

        result = click(selector.strip())
        return {"ok": True, "result": result}
    except Exception as e:
        logger.exception("playwright_click 失败")
        return {"error": str(e)}


@tool(
    name="playwright_evaluate",
    description=(
        "Execute JavaScript code in the current browser page and return the result. "
        "Use for scraping data that simple text extraction can't get (e.g. JSON from an API, "
        "computed values, DOM manipulation). "
        "The JS code runs in the page context; return a value to get it back. "
        "Example: 'document.querySelectorAll(\".price\").length' or "
        "'JSON.parse(document.body.innerText)'."
    ),
    group=PLAYWRIGHT_TOOL_GROUP,
    risk="high",
    requires_confirmation=True,
)
def playwright_evaluate(js_code: str) -> dict[str, Any]:
    code = (js_code or "").strip()
    if not code:
        return {"error": "js_code is required"}
    try:
        from plugins.playwright_browser.browser import evaluate

        result = evaluate(code)
        return {"result": result, "type": type(result).__name__}
    except Exception as e:
        logger.exception("playwright_evaluate 失败")
        return {"error": str(e)}


@tool(
    name="playwright_fill",
    description=(
        "Type text into an input field on the current page by CSS selector. "
        "Use after playwright_navigate."
    ),
    group=PLAYWRIGHT_TOOL_GROUP,
)
def playwright_fill(selector: str, text: str) -> dict[str, Any]:
    if not (selector or "").strip():
        return {"error": "selector is required"}
    try:
        from plugins.playwright_browser.browser import fill

        result = fill(selector.strip(), text)
        return {"ok": True, "result": result}
    except Exception as e:
        logger.exception("playwright_fill 失败")
        return {"error": str(e)}
