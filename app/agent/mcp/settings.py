from __future__ import annotations

from dataclasses import dataclass
from dataclasses import replace

from app.agent.mcp.config import MCPConfig


WINDOWS_MCP_ENABLED_KEY = "WINDOWS_MCP_ENABLED"
WINDOWS_MCP_AVAILABLE = False
WINDOWS_MCP_UNAVAILABLE_TEXT = "待测试，未开放"


@dataclass(frozen=True)
class MCPRuntimeSettings:
    """MCP 运行时开关；由 data/config/system_config.yaml 提供。"""

    windows_enabled: bool = False


def normalize_mcp_runtime_settings(settings: MCPRuntimeSettings) -> MCPRuntimeSettings:
    """归一化 MCP 运行时开关，屏蔽当前未开放的能力。"""

    if WINDOWS_MCP_AVAILABLE:
        return settings
    return replace(settings, windows_enabled=False)


def apply_mcp_runtime_settings(
    config: MCPConfig,
    settings: MCPRuntimeSettings,
) -> MCPConfig:
    """按运行时开关覆盖需要重启加载的 MCP server。"""

    normalized_settings = normalize_mcp_runtime_settings(settings)
    servers = [
        replace(server, enabled=normalized_settings.windows_enabled)
        if server.name == "windows"
        else server
        for server in config.servers
    ]
    return replace(config, servers=servers)
