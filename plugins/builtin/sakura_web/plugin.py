from __future__ import annotations

from typing import Any

try:
    from . import web
except ImportError:
    import web


class WebPlugin:
    """Public web tools; the Assistant model owns all search decisions."""

    def setup(self, context: Any) -> None:
        config = context.config.get()
        if config.get("migration_error"):
            from sakura_plugin_sdk import PluginApiError

            raise PluginApiError(str(config["migration_error"]))
        # These values only carry old MCP restrictions. No settings surface is
        # contributed: new installations use the complete built-in defaults.
        allowed = config.get("allowed_tools", ["web_search", "fetch_url"])
        risks = config.get("tool_risks", {})
        timeout = config.get("call_timeout", 20)
        tools = context.get("sakura.host.tools")
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
            }, _handler(name))


def _handler(name: str):
    def execute(arguments: dict[str, Any]) -> dict[str, Any]:
        try:
            if name == "web_search":
                return web.search_web(
                    web._required_string(arguments, "query"),
                    web._clamp_int(arguments.get("max_results"), 5, 1, 10),
                )
            return web.fetch_url(
                web._required_string(arguments, "url"),
                web._clamp_int(arguments.get("max_chars"), 6000, 500, 20000),
            )
        except web.WebError as error:
            return {"isError": True, "reasonCode": error.code, "error": str(error)}
        except TimeoutError:
            return {"isError": True, "reasonCode": "WEB_TIMEOUT", "error": "网页请求超时。"}
        except ValueError as error:
            return {"isError": True, "reasonCode": "WEB_INVALID_REQUEST", "error": str(error)}
        except OSError:
            return {"isError": True, "reasonCode": "WEB_NETWORK_ERROR", "error": "无法连接目标网站。"}
    return execute
