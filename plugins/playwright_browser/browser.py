from __future__ import annotations

import base64
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable, TypeVar
from urllib.parse import quote_plus

from plugins.playwright_browser.config_model import default_config_path, load_config


T = TypeVar("T")

_playwright: Any | None = None
_browser: Any | None = None
_context: Any | None = None
_page: Any | None = None
_bg_executor: ThreadPoolExecutor | None = None
_browser_thread_id: int | None = None
_use_bg_thread = True
_launch_lock = threading.Lock()
_plugin_root = Path(__file__).resolve().parent


def set_plugin_root(plugin_root: str | Path) -> None:
    """设置插件根目录，浏览器启动时会从这里读取持久化配置。"""
    global _plugin_root
    _plugin_root = Path(plugin_root)


def navigate(url: str) -> dict[str, str]:
    """打开指定网页，浏览器类型由插件配置决定。"""

    def task() -> dict[str, str]:
        page = _ensure_browser()
        page.goto(url, wait_until="domcontentloaded")
        return {"url": getattr(page, "url", url), "title": _safe_title(page)}

    return _run_browser_task(task)


def get_text(selector: str = "body") -> str:
    """读取当前页面文本。"""

    def task() -> str:
        page = _ensure_browser()
        return str(page.inner_text(selector or "body"))

    return _run_browser_task(task)


def search_web(query: str, limit: int = 5) -> str:
    """用 Playwright 打开 Bing 搜索页并整理结果。"""

    def task() -> str:
        page = _ensure_browser()
        page.goto(f"https://www.bing.com/search?q={quote_plus(query)}", wait_until="domcontentloaded")
        results: list[str] = []
        for index, item in enumerate(page.query_selector_all("li.b_algo")[: max(1, limit)], start=1):
            title = _inner_text(item, "h2")
            snippet = _inner_text(item, "p")
            display_url = _inner_text(item, ".b_attribution cite")
            link = item.query_selector("h2 a")
            href = link.get_attribute("href") if link is not None else ""
            parts = [f"{index}. {title}".strip()]
            if snippet:
                parts.append(snippet)
            if display_url:
                parts.append(display_url)
            if href:
                parts.append(href)
            results.append("\n".join(part for part in parts if part.strip()))
        return "\n\n".join(results) if results else "没有找到可用搜索结果。"

    return _run_browser_task(task)


def screenshot(full_page: bool = False) -> dict[str, str | bool]:
    """截取当前页面并返回 data URL。"""

    def task() -> dict[str, str | bool]:
        page = _ensure_browser()
        raw = page.screenshot(type="jpeg", quality=70, full_page=full_page)
        data_url = "data:image/jpeg;base64," + base64.b64encode(raw).decode("ascii")
        return {
            "url": getattr(page, "url", ""),
            "title": _safe_title(page),
            "screenshot_data_url": data_url,
        }

    return _run_browser_task(task)


def click(selector: str) -> dict[str, str]:
    """点击当前页面中的 selector。"""

    def task() -> dict[str, str]:
        page = _ensure_browser()
        page.click(selector)
        return {"selector": selector, "url": getattr(page, "url", "")}

    return _run_browser_task(task)


def fill(selector: str, value: str) -> dict[str, str]:
    """向当前页面中的 selector 输入文本。"""

    def task() -> dict[str, str]:
        page = _ensure_browser()
        page.fill(selector, value)
        return {"selector": selector, "value": value, "url": getattr(page, "url", "")}

    return _run_browser_task(task)


def evaluate(js_code: str) -> dict[str, Any]:
    """执行页面 JavaScript。高风险工具，调用侧会要求确认。"""

    def task() -> dict[str, Any]:
        page = _ensure_browser()
        return {"result": page.evaluate(js_code)}

    return _run_browser_task(task)


def shutdown_browser() -> None:
    """关闭浏览器和后台单线程执行器。"""

    global _playwright, _browser, _context, _page, _bg_executor, _browser_thread_id
    executor = _bg_executor
    _bg_executor = None
    if executor is not None and threading.get_ident() != _browser_thread_id:
        try:
            executor.submit(_shutdown_browser_objects).result(timeout=5)
        finally:
            executor.shutdown(wait=True, cancel_futures=True)
    else:
        _shutdown_browser_objects()
        if executor is not None:
            executor.shutdown(wait=False, cancel_futures=True)
    _playwright = None
    _browser = None
    _context = None
    _page = None
    _browser_thread_id = None


def _run_browser_task(func: Callable[[], T]) -> T:
    if not _use_bg_thread or threading.get_ident() == _browser_thread_id:
        return func()
    executor = _ensure_executor()
    return executor.submit(_run_on_browser_thread, func).result()


def _run_on_browser_thread(func: Callable[[], T]) -> T:
    global _browser_thread_id
    _browser_thread_id = threading.get_ident()
    return func()


def _ensure_executor() -> ThreadPoolExecutor:
    global _bg_executor
    with _launch_lock:
        if _bg_executor is None:
            _bg_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="sakura-playwright")
        return _bg_executor


def _ensure_browser() -> Any:
    global _playwright, _browser, _context, _page, _browser_thread_id
    _browser_thread_id = threading.get_ident()
    if _page is not None:
        try:
            if not _page.is_closed():
                return _page
        except AttributeError:
            return _page
    from playwright.sync_api import sync_playwright

    _playwright = _playwright or sync_playwright().start()
    if _browser is None:
        cfg = load_config(default_config_path(_plugin_root))
        cfg.clamp()
        _browser = _launch_configured_browser(_playwright, cfg.browser_type, cfg.headless)
    _context = _context or _browser.new_context()
    _page = _context.new_page()
    return _page


def _launch_configured_browser(playwright: Any, browser_type: str, headless: bool) -> Any:
    if browser_type in {"msedge", "chrome"}:
        return playwright.chromium.launch(channel=browser_type, headless=headless)
    if browser_type == "firefox":
        return playwright.firefox.launch(headless=headless)
    if browser_type == "webkit":
        return playwright.webkit.launch(headless=headless)
    return playwright.chromium.launch(headless=headless)


def _shutdown_browser_objects() -> None:
    global _playwright, _browser, _context, _page
    for item in (_page, _context, _browser):
        if item is None:
            continue
        try:
            item.close()
        except Exception:
            pass
    if _playwright is not None:
        try:
            _playwright.stop()
        except Exception:
            pass
    _playwright = None
    _browser = None
    _context = None
    _page = None


def _safe_title(page: Any) -> str:
    try:
        return str(page.title())
    except Exception:
        return ""


def _inner_text(item: Any, selector: str) -> str:
    element = item.query_selector(selector)
    if element is None:
        return ""
    try:
        return str(element.inner_text()).strip()
    except Exception:
        return ""
