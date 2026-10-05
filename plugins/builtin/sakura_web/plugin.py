from __future__ import annotations

from typing import Any
import threading
import json

try:
    from . import web, search
except ImportError:
    import web
    import search


class WebPlugin:
    """Public web tools; the Assistant model owns all search decisions."""

    def __init__(self):
        self.test_result = ""
        self.test_status = {"state": "neutral", "label": "搜索", "message": ""}
        self.test_lock = threading.Lock()
        self.closed = False

    def setup(self, context: Any) -> None:
        config = context.config.get()
        self.context = context
        context.effect(self.close)
        context.get("sakura.host.settings").register(
            _settings_descriptor(), load=self.load, save=self.save,
            actions={"test_search": self.test_search},
        )
        allowed = config.get("allowed_tools", ["web_search", "fetch_url"])
        risks = config.get("tool_risks", {})
        timeout = config.get("call_timeout", 20)
        tools = context.get("sakura.host.tools")
        logger = context.get("sakura.host.logging")
        for tool in web.TOOLS:
            name = tool["name"]
            if name not in allowed:
                continue
            tools.register({
                "name": "web__" + name,
                "description": tool["description"],
                "parameters": tool["inputSchema"],
                "risk": risks.get(name, "low"),
                "timeoutSeconds": timeout,
            }, _handler(name, logger, context.config.get, context.exception_diagnostics))


    def load(self):
        with self.test_lock:
            return {**search.configuration(self.context.config.get()), "test_query": "", "test_result": self.test_result, "test_status": self.test_status}

    def save(self, values):
        self.context.config.update(search.configuration(values))

    def close(self):
        with self.test_lock:
            self.closed = True

    def test_search(self, values):
        config = search.configuration(values)
        query = web._required_string(values, "test_query")
        with self.test_lock:
            if self.closed or self.test_status["state"] == "working":
                return {}
            self.test_status = {"state": "working", "label": "正在搜索", "message": ""}
            self.test_result = ""
        self.test_thread = threading.Thread(target=self._run_test, args=(query, config), daemon=True)
        self.test_thread.start()
        return {}

    def _run_test(self, query, config):
        result = _execute_handler("web_search", lambda: config,
                                  self.context.get("sakura.host.logging"),
                                  self.context.exception_diagnostics)({"query": query, "max_results": 5})
        if result.get("isError"):
            text = result["error"]
            status = {"state": "error", "label": "搜索失败", "message": ""}
        else:
            text = "\n\n".join(
                "\n".join(filter(None, [item["title"], item["url"], item.get("snippet", "")]))
                for item in result["results"]
            ) or "未找到结果。"
            status = {"state": "ready", "label": "搜索完成", "message": ""}
        # The desktop bounds each complete field to 16 KiB of serialized JSON.
        # Reserve room for the descriptor; count escaping and UTF-8, not characters.
        if len(json.dumps(text, ensure_ascii=False).encode("utf-8")) > 10000:
            low, high = 0, len(text)
            while low < high:
                middle = (low + high + 1) // 2
                if len(json.dumps(text[:middle], ensure_ascii=False).encode("utf-8")) <= 9900:
                    low = middle
                else:
                    high = middle - 1
            text = text[:low] + "\n（结果过长，已截断）"
        with self.test_lock:
            if not self.closed:
                self.test_result = text
                self.test_status = status


def _settings_descriptor():
    tavily = {"field": "provider", "equals": "tavily", "hide": True}
    return {
        "sectionId": "search", "title": "查询服务", "order": 10,
        "fields": [
            {"key": "test_status", "label": "搜索状态", "type": "status", "placement": "row",
             "default": {"state": "neutral", "label": "搜索", "message": ""}},
            {"key": "provider", "label": "查询服务", "type": "select", "default": "baidu",
             "options": [{"label": label, "value": key} for key, label in search.PROVIDERS.items()]},
            {"key": "tavily_api_key", "label": "Tavily API Key", "type": "string", "default": "",
             "maxLength": 512, "enabledWhen": tavily},
            {"key": "tavily_depth", "label": "搜索深度", "type": "select", "default": "basic", "enabledWhen": tavily,
             "options": [{"label": "Basic", "value": "basic"}, {"label": "Advanced", "value": "advanced"}]},
            {"key": "test_query", "label": "测试搜索", "type": "string", "default": "", "maxLength": 1000},
            {"key": "test_result", "label": "搜索结果", "type": "readonly", "default": "", "maxLength": 16000, "copyable": True},
        ],
        "actions": [{"actionId": "test_search", "label": "测试搜索", "danger": False}],
    }


def _handler(name: str, logger=None, config_get=None, diagnose=None):
    return _execute_handler(name, config_get, logger, diagnose)


def _execute_handler(name: str, config_get=None, logger=None, diagnose=None):
    def execute(arguments: dict[str, Any]) -> dict[str, Any]:
        config = {}
        try:
            config = config_get() if config_get else {"provider": "bing"}
            if name == "web_search":
                return search.search(
                    web._required_string(arguments, "query"),
                    web._clamp_int(arguments.get("max_results"), 5, 1, 10),
                    config,
                )
            return search.fetch(
                web._required_string(arguments, "url"),
                web._clamp_int(arguments.get("max_chars"), 6000, 500, 20000),
                config,
            )
        except (web.WebError, TimeoutError, ValueError, OSError) as error:
            from sakura_provider_errors import sanitize_provider_diagnostic
            code = error.code if isinstance(error, web.WebError) else "WEB_TIMEOUT" if isinstance(error, TimeoutError) else "WEB_INVALID_REQUEST" if isinstance(error, ValueError) else "WEB_NETWORK_ERROR"
            secrets = (config.get("tavily_api_key", ""),)
            evidence = diagnose(error, secrets=secrets) if diagnose else {}
            result = {"isError": True, "reasonCode": code,
                      "error": sanitize_provider_diagnostic(evidence.get("diagnostic") or str(error) or type(error).__name__, secrets=secrets)}
        # The exception was captured with this request's credentials. Logging
        # after the handler prevents automatic capture from replacing that copy.
        if logger is not None:
            logger.warning("网页工具执行失败", fields={"tool": name, "reason_code": code, **evidence})
        return result
    return execute
