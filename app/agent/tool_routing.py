"""app/agent/tool_routing.py — 浏览器/屏幕工具路由策略。

从 runtime.py 拆出的纯函数层：根据对话内容决定浏览器页面模式、
可见浏览器模式、Windows 控制与屏幕观察的工具过滤与提示词规则。
"""

from __future__ import annotations

import json
import re
from typing import Any

from app.agent.actions import PendingToolAction
from app.agent.screen_policy import ScreenPolicy
from app.agent.tool_policy import BROWSER_SNAPSHOT_TOOL_NAME, ToolPolicy
from app.agent.tools import ToolExecutionResult, ToolRegistry
from app.core.debug_log import debug_log
from app.llm.api_client import ChatMessage, NativeToolCall


def _filter_tools_for_browser_routing(
    tools: list[dict[str, Any]],
    *,
    browser_page_mode: bool,
    visible_browser_mode: bool,
) -> list[dict[str, Any]]:
    return ToolPolicy.filter_tools_for_browser_routing(
        tools,
        browser_page_mode=browser_page_mode,
        visible_browser_mode=visible_browser_mode,
    )


def _filter_openai_tools_for_browser_routing(
    tools: list[dict[str, Any]],
    *,
    browser_page_mode: bool,
    visible_browser_mode: bool,
) -> list[dict[str, Any]]:
    if not browser_page_mode and not visible_browser_mode:
        return tools
    filtered_names = {
        str(item.get("name", ""))
        for item in _filter_tools_for_browser_routing(
            [
                {"name": tool.get("function", {}).get("name")}
                for tool in tools
                if isinstance(tool.get("function"), dict)
            ],
            browser_page_mode=browser_page_mode,
            visible_browser_mode=visible_browser_mode,
        )
    }
    return [
        tool
        for tool in tools
        if isinstance(tool.get("function"), dict)
        and str(tool["function"].get("name", "")) in filtered_names
    ]


def _should_block_windows_tool_for_browser_page(
    call: dict[str, Any],
    browser_page_mode: bool,
) -> bool:
    return ToolPolicy.should_block_windows_tool_for_browser_page(call, browser_page_mode)


def _should_block_background_web_tool_for_visible_browser(
    call: dict[str, Any],
    visible_browser_mode: bool,
) -> bool:
    return ToolPolicy.should_block_background_web_tool_for_visible_browser(
        call,
        visible_browser_mode,
    )


def _should_auto_snapshot_after_browser_navigation(
    tool_calls: list[dict[str, Any]],
    step_results: list[ToolExecutionResult],
    tools: ToolRegistry,
) -> bool:
    return ToolPolicy.should_auto_snapshot_after_browser_navigation(
        tool_calls,
        step_results,
        tools,
    )


def _execute_auto_browser_snapshot(tools: ToolRegistry, step_index: int) -> ToolExecutionResult:
    arguments: dict[str, Any] = {}
    reason = "浏览器导航成功后自动读取页面内容，减少模型往返。"
    debug_log(
        "AgentRuntime",
        "自动补充浏览器页面文本",
        {
            "step_index": step_index,
            "name": BROWSER_SNAPSHOT_TOOL_NAME,
            "arguments": arguments,
            "reason": reason,
        },
    )
    prepared = tools.prepare_or_execute(BROWSER_SNAPSHOT_TOOL_NAME, arguments, reason)
    if isinstance(prepared, PendingToolAction):
        result = ToolExecutionResult(
            tool_name="runtime",
            success=False,
            content={
                "auto_tool": BROWSER_SNAPSHOT_TOOL_NAME,
                "reason": "自动页面文本读取需要用户确认，已跳过隐藏执行。",
            },
            error="自动页面文本读取需要用户确认，已跳过。",
        )
        debug_log("AgentRuntime", "自动浏览器页面文本读取需要确认，已跳过", result.to_dict())
        return result

    # 延迟 import：脱敏函数属于 runtime 的模型消息构建层，模块级互引会成环
    from app.agent.runtime import _redact_tool_result_for_model

    debug_log("AgentRuntime", "自动浏览器页面文本读取完成", _redact_tool_result_for_model(prepared))
    return prepared


def _should_fast_forward_after_auto_browser_snapshot(
    messages: list[ChatMessage],
    snapshot_result: ToolExecutionResult,
) -> bool:
    if not _latest_user_is_browser_lookup_request(messages):
        return False
    if _latest_user_is_browser_interaction_request(messages):
        return False
    return _browser_snapshot_has_readable_content(snapshot_result)


def _latest_user_is_browser_lookup_request(messages: list[ChatMessage]) -> bool:
    text = (_latest_user_text(messages) or "").lower()
    if not text:
        return False
    lookup_keywords = (
        "搜索",
        "搜一下",
        "搜一搜",
        "查",
        "查询",
        "看看",
        "看一下",
        "百科",
        "信息",
        "资料",
        "介绍",
        "告诉我",
        "说明",
        "内容",
        "总结",
        "梳理",
        "是谁",
        "是什么",
        "検索",
        "調べ",
        "情報",
        "教えて",
        "紹介",
        "search",
        "look up",
        "lookup",
        "information",
        "info",
        "tell me",
        "wiki",
        "wikipedia",
        "summary",
        "summarize",
    )
    return any(keyword in text for keyword in lookup_keywords)


def _latest_user_is_browser_interaction_request(messages: list[ChatMessage]) -> bool:
    text = (_latest_user_text(messages) or "").lower()
    if not text:
        return False
    interaction_keywords = (
        "点击",
        "点开",
        "点进",
        "输入",
        "填写",
        "登录",
        "登陆",
        "提交",
        "下载",
        "滚动",
        "选择",
        "勾选",
        "购买",
        "支付",
        "播放",
        "打开菜单",
        "切换",
        "上传",
        "发帖",
        "评论",
        "回复",
        "删除",
        "编辑",
        "下一页",
        "上一页",
        "クリック",
        "入力",
        "ログイン",
        "送信",
        "ダウンロード",
        "スクロール",
        "選択",
        "click",
        "type",
        "login",
        "log in",
        "submit",
        "download",
        "scroll",
        "select",
        "choose",
        "upload",
    )
    return any(keyword in text for keyword in interaction_keywords)


def _browser_snapshot_has_readable_content(result: ToolExecutionResult) -> bool:
    if result.tool_name != BROWSER_SNAPSHOT_TOOL_NAME or not result.success:
        return False
    text = _tool_result_content_text(result.content).strip()
    if len(text) < 20:
        return False
    normalized = text.lower()
    if _browser_snapshot_looks_like_search_results(normalized):
        return False
    blocked_markers = (
        "error executing tool",
        "http 403",
        "forbidden",
        "timeout",
        '"is_error": true',
        "'is_error': true",
        '"loading": true',
        "'loading': true",
        "加载失败",
        "访问被拒绝",
        "无法访问",
    )
    return not any(marker in normalized for marker in blocked_markers)


def _browser_snapshot_looks_like_search_results(normalized_text: str) -> bool:
    search_page_markers = (
        "google.com/search",
        "bing.com/search",
        "baidu.com/s?",
        "duckduckgo.com/",
        "search.yahoo.com/search",
        "sogou.com/web",
        "yandex.com/search",
        "google 搜索",
        "google search",
        "bing search",
        "百度一下",
        "搜索结果",
        "search results",
    )
    return any(marker in normalized_text for marker in search_page_markers)


def _tool_result_content_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    try:
        return json.dumps(content, ensure_ascii=False, default=str)
    except TypeError:
        return str(content)


def _build_browser_page_windows_tool_block_result(call: dict[str, Any]) -> ToolExecutionResult:
    tool_name = str(call.get("name", "")).strip() or "unknown"
    return ToolExecutionResult(
        tool_name="runtime",
        success=False,
        content={
            "blocked_tool": tool_name,
            "reason": "当前上下文是浏览器页面内部操作，已阻止 Windows-MCP 坐标/截图工具抢路由。",
            "guidance": (
                "请使用 playwright_navigate 直达目标 URL，或 playwright_search_web 执行可见搜索；"
                "需要页面文本后调用 playwright_get_text，视觉状态用 playwright_screenshot，"
                "点击或填写时基于真实 selector 调用 playwright_click/playwright_fill。"
            ),
        },
        error=f"已阻止 {tool_name}：浏览器页面内部操作应优先使用 playwright_ 工具。",
    )


def _build_visible_browser_web_tool_block_result(call: dict[str, Any]) -> ToolExecutionResult:
    tool_name = str(call.get("name", "")).strip() or "unknown"
    return ToolExecutionResult(
        tool_name="runtime",
        success=False,
        content={
            "blocked_tool": tool_name,
            "reason": "用户明确要求打开浏览器或看到搜索过程，已阻止后台网页搜索/抓取工具。",
            "guidance": (
                "请优先用 playwright_navigate 直接打开目标 URL，或用 playwright_search_web 搜索；"
                "再按需用 playwright_get_text、playwright_screenshot、playwright_click、"
                "playwright_fill 完成可见浏览器流程。"
            ),
        },
        error=f"已阻止 {tool_name}：显式浏览器任务应使用 playwright_ 工具，不要只做后台搜索。",
    )


def _browser_dom_tools_available(tools: ToolRegistry) -> bool:
    return ToolPolicy.browser_dom_tools_available(tools)


def _should_prefer_browser_page_tools(messages: list[ChatMessage]) -> bool:
    text = _messages_text_for_tool_routing(messages).lower()
    if "playwright_" in text:
        return True

    latest_text = (_latest_user_text(messages) or "").lower()
    if not latest_text:
        return False
    browser_keywords = (
        "浏览器",
        "网页",
        "页面",
        "链接",
        "搜索结果",
        "搜索框",
        "输入框",
        "点进",
        "点开",
        "打开网页",
        "标签页",
        "网址",
        "url",
        "http://",
        "https://",
        "百科",
        "必应",
        "bing",
        "百度",
        "google",
    )
    return any(keyword in latest_text for keyword in browser_keywords)


def _latest_user_requests_visible_browser(messages: list[ChatMessage]) -> bool:
    text = (_latest_user_text(messages) or "").lower()
    if not text:
        return False
    visible_browser_keywords = (
        "打开浏览器",
        "用浏览器",
        "浏览器搜索",
        "在浏览器",
        "打开网页",
        "打开页面",
        "看搜索过程",
        "看到搜索过程",
        "让我看到",
        "给我看搜索",
        "搜给我看",
        "可见浏览器",
        "前台浏览器",
    )
    return any(keyword in text for keyword in visible_browser_keywords)


def _recent_browser_tool_failed(messages: list[ChatMessage]) -> bool:
    recent_text = _messages_text_for_tool_routing(messages[-4:]).lower()
    return (
        "playwright_" in recent_text
        and (
            '"success": false' in recent_text
            or '"success":false' in recent_text
            or "'success': false" in recent_text
            or "'success':false" in recent_text
            or '"is_error": true' in recent_text
            or '"is_error":true' in recent_text
            or "'is_error': true" in recent_text
            or "'is_error':true" in recent_text
            or "工具执行异常" in recent_text
            or "工具执行失败" in recent_text
        )
    )


def _latest_user_explicitly_requests_windows_control(messages: list[ChatMessage]) -> bool:
    text = (_latest_user_text(messages) or "").lower()
    if not text:
        return False
    explicit_keywords = (
        "真实鼠标",
        "物理鼠标",
        "鼠标",
        "坐标",
        "windows",
        "桌面",
        "窗口",
        "浏览器窗口",
        "地址栏",
        "任务栏",
        "快捷键",
        "键盘",
        "系统界面",
    )
    return any(keyword in text for keyword in explicit_keywords)


def _messages_text_for_tool_routing(messages: list[ChatMessage]) -> str:
    # 延迟 import：内容压缩函数属于 runtime 的上下文构建层，模块级互引会成环
    from app.agent.runtime import _compact_pending_context_content

    return "\n".join(_compact_pending_context_content(message.get("content")) for message in messages)


def _build_browser_page_mode_rule(browser_page_mode: bool) -> str:
    if not browser_page_mode:
        return ""
    return (
        "- 当前上下文已识别为浏览器页面内部操作模式：Windows-MCP 坐标、截图、输入、滚动工具已从可用工具中隐藏。"
        "能直达 URL 时先用 playwright_navigate；需要搜索时用 playwright_search_web；"
        "搜索后如果已经出现目标站点或词条页 URL，优先直接导航到目标页，再继续读取页面正文。"
        "继续读取、截图、点击或填写页面时，必须使用 playwright_ 前缀的原生 Playwright 工具。"
    )


def _build_visible_browser_mode_rule(visible_browser_mode: bool) -> str:
    if not visible_browser_mode:
        return ""
    return (
        "- 用户明确要求打开浏览器或看到搜索过程：后台 web__ 搜索/抓取工具已从可用工具中隐藏。"
        "必须优先用 playwright_navigate 直达目标 URL，或 playwright_search_web 打开可见搜索结果；"
        "能直达页面就不要先打开搜索首页再操作输入框；"
        "需要交互时再用 playwright_get_text/screenshot/click/fill 等工具完成可见浏览器流程。"
    )


def _build_web_tool_capability_rule(visible_browser_mode: bool) -> str:
    if visible_browser_mode:
        return (
            "- 网页：本轮是显式可见浏览器任务，使用 playwright_*；"
            "后台 web__ 搜索/抓取只用于非可见浏览器的轻量公开资料。"
        )
    return "- 网页：轻量公开资料用 web__web_search / web__fetch_url；可见浏览器操作用 playwright_*。"


def _build_screen_and_desktop_routing_rule(allow_screen_observation: bool) -> str:
    if allow_screen_observation:
        return "\n".join(
            [
                "- 当用户询问当前屏幕内容、可见文字、报错含义、界面状态或“这个是什么意思”时，优先调用 observe_screen；这是 Sakura 内置视觉观察，只用于理解画面和解释，不用于鼠标坐标。",
                "- 当用户要求你点击、移动鼠标、输入、切换窗口或操作桌面应用时，不要用 observe_screen 推理坐标；改用 Windows MCP 的 windows__Snapshot / windows__Screenshot 作为操作前观察。",
            ]
        )
    return "\n".join(
        [
            "- 当前没有可用的 Sakura 内置屏幕理解工具；不要臆造当前屏幕内容。",
            "- 如果用户要求桌面点击、移动鼠标、输入或窗口操作，并且 Windows MCP 截图工具可用，先用 windows__Snapshot / windows__Screenshot 获取真实桌面状态。",
        ]
    )


def _should_offer_screen_observation(messages: list[ChatMessage]) -> bool:
    return ScreenPolicy.should_offer_screen_observation_text(_latest_user_text(messages))


def _latest_user_text(messages: list[ChatMessage]) -> str | None:
    for message in reversed(messages):
        if message.get("role") != "user":
            continue
        content = message.get("content")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts = [
                str(part.get("text", ""))
                for part in content
                if isinstance(part, dict) and part.get("type") == "text"
            ]
            return "\n".join(parts)
        return ""
    return None
