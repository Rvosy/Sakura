"""Core-owned built-in tools for Runtime v2."""

from __future__ import annotations

from datetime import datetime

from app.agent.tools import Tool, ToolRegistry


def get_current_time() -> dict[str, str]:
    now = datetime.now().astimezone()
    return {
        "datetime": now.isoformat(timespec="seconds"),
        "timezone": now.tzname() or "",
    }


def create_runtime_v2_tool_registry() -> ToolRegistry:
    """Build the Core-owned registry; plugins add their own domain tools."""

    return ToolRegistry(
        [
            Tool(
                name="get_current_time",
                description="获取当前本机时间和时区。",
                parameters={"type": "object", "properties": {}, "required": []},
                handler=lambda _arguments: get_current_time(),
            ),
        ]
    )


__all__ = [
    "create_runtime_v2_tool_registry",
]
