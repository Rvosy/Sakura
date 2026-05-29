from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable


ToolHandler = Callable[[dict[str, Any]], Any]


@dataclass(frozen=True)
class Tool:
    """内部工具定义。"""

    name: str
    description: str
    parameters: dict[str, Any] = field(default_factory=dict)
    handler: ToolHandler | None = None


@dataclass(frozen=True)
class ToolExecutionResult:
    """工具执行结果，统一交回模型做最终表述。"""

    tool_name: str
    success: bool
    content: dict[str, Any] | str
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "tool_name": self.tool_name,
            "success": self.success,
            "content": self.content,
        }
        if self.error:
            data["error"] = self.error
        return data


class ToolRegistry:
    """管理 Agent 可用工具，后续 MCP Provider 会挂到这一层。"""

    def __init__(self, tools: list[Tool] | None = None) -> None:
        self._tools: dict[str, Tool] = {}
        for tool in tools or []:
            self.register(tool)

    def register(self, tool: Tool) -> None:
        self._tools[tool.name] = tool

    def all(self) -> list[Tool]:
        return list(self._tools.values())

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def describe_tools(self) -> list[dict[str, Any]]:
        return [
            {
                "name": tool.name,
                "description": tool.description,
                "parameters": tool.parameters,
            }
            for tool in self.all()
        ]

    def execute(self, name: str, arguments: dict[str, Any]) -> ToolExecutionResult:
        tool = self.get(name)
        if tool is None:
            return ToolExecutionResult(
                tool_name=name,
                success=False,
                content="",
                error=f"未知工具：{name}",
            )
        if tool.handler is None:
            return ToolExecutionResult(
                tool_name=name,
                success=False,
                content="",
                error=f"工具未配置处理器：{name}",
            )
        if not isinstance(arguments, dict):
            return ToolExecutionResult(
                tool_name=name,
                success=False,
                content="",
                error="工具参数必须是 JSON object。",
            )

        try:
            content = tool.handler(arguments)
        except Exception as exc:
            return ToolExecutionResult(
                tool_name=name,
                success=False,
                content="",
                error=str(exc),
            )
        return ToolExecutionResult(
            tool_name=name,
            success=True,
            content=content if isinstance(content, (dict, str)) else str(content),
        )
