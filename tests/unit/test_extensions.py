from __future__ import annotations

from app.agent.tools import Tool, ToolRegistry
from app.core.extensions import ExtensionRegistry


class DemoToolContributor:
    def contribute_tools(self) -> list[Tool]:
        return [
            Tool(
                name="demo_tool",
                description="测试工具",
                handler=lambda _args: {"ok": True},
                group="extension",
            )
        ]


def test_extension_registry_applies_tool_contributors() -> None:
    registry = ToolRegistry()
    extensions = ExtensionRegistry()

    extensions.register_tool_contributor(DemoToolContributor())
    extensions.apply_tools(registry)

    tool = registry.get("demo_tool")
    assert tool is not None
    assert tool.group == "extension"
