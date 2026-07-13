from __future__ import annotations

from importlib import import_module
from typing import Any

__all__ = [
    "DESKTOP_MCP_EXPERIMENTAL_TEXT",
    "DesktopMCP",
    "MCPConfig",
    "MCPServerConfig",
    "MCPRuntimeSettings",
    "MCPToolProvider",
    "WINDOWS_MCP_EXPERIMENTAL_TEXT",
    "load_mcp_config",
    "normalize_mcp_runtime_settings",
    "register_mcp_tools_from_config",
    "resolve_desktop_mcp",
]


_EXPORT_MODULES = {
    "MCPConfig": "app.agent.mcp.config",
    "MCPServerConfig": "app.agent.mcp.config",
    "load_mcp_config": "app.agent.mcp.config",
    "MCPToolProvider": "app.agent.mcp.provider",
    "register_mcp_tools_from_config": "app.agent.mcp.provider",
    "DESKTOP_MCP_EXPERIMENTAL_TEXT": "app.agent.mcp.settings",
    "DesktopMCP": "app.agent.mcp.settings",
    "MCPRuntimeSettings": "app.agent.mcp.settings",
    "WINDOWS_MCP_EXPERIMENTAL_TEXT": "app.agent.mcp.settings",
    "normalize_mcp_runtime_settings": "app.agent.mcp.settings",
    "resolve_desktop_mcp": "app.agent.mcp.settings",
}


def __getattr__(name: str) -> Any:
    """按需加载 MCP 实现，读取设置时不启动 Provider 依赖。"""
    module_name = _EXPORT_MODULES.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(import_module(module_name), name)
    globals()[name] = value
    return value
