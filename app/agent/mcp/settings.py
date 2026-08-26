from __future__ import annotations

import sys
from dataclasses import dataclass
from dataclasses import replace

from app.agent.mcp.config import MCPConfig


DESKTOP_MCP_EXPERIMENTAL_TEXT = "实验性功能，供想要尝鲜的用户使用；可能不稳定，请谨慎开启"


@dataclass(frozen=True)
class DesktopMCP:
    """某平台对应的桌面控制 MCP：mcp.yaml 里的 server 名 + UI 显示名。"""

    server_name: str
    label: str


# 平台 -> 桌面控制 MCP；不在表内的平台视为暂不支持（如 Linux）。
_DESKTOP_MCP_BY_PLATFORM: dict[str, DesktopMCP] = {
    "darwin": DesktopMCP(server_name="macos", label="macOS MCP"),
}
_DESKTOP_MCP_SERVER_NAMES = frozenset(
    desktop.server_name for desktop in _DESKTOP_MCP_BY_PLATFORM.values()
)
_RETIRED_DESKTOP_MCP_SERVER_NAMES = frozenset({"windows"})


def resolve_desktop_mcp(platform: str | None = None) -> DesktopMCP | None:
    """返回当前（或指定）平台的桌面控制 MCP；不支持的平台返回 None。"""

    key = sys.platform if platform is None else platform
    return _DESKTOP_MCP_BY_PLATFORM.get(key)


# 当前平台是否提供桌面控制 MCP。
DESKTOP_MCP_AVAILABLE = resolve_desktop_mcp() is not None


@dataclass(frozen=True)
class MCPRuntimeSettings:
    """MCP 运行时开关；由 user_root/config/system_config.yaml 提供。

    desktop_enabled 语义为“启用当前平台对应的桌面控制 MCP”。Windows
    不再提供内置桌面 MCP，因此该偏好只会在受支持的平台生效。
    """

    desktop_enabled: bool = False


def normalize_mcp_runtime_settings(settings: MCPRuntimeSettings) -> MCPRuntimeSettings:
    """归一化 MCP 运行时开关。

    桌面控制开关是用户偏好，跨平台原样保留（持久化忠实回写）；是否真正启用某个
    server 由 apply_mcp_runtime_settings 按当前平台决定——不支持的平台不会启用任何
    server，因此无需在此抹掉用户偏好。
    """

    return settings


def apply_mcp_runtime_settings(
    config: MCPConfig,
    settings: MCPRuntimeSettings,
) -> MCPConfig:
    """按运行时开关覆盖当前平台对应桌面控制 MCP server 的启停。

    只动当前平台那一个 server，并从运行时配置中移除已退役的桌面 server；
    其他平台的桌面 server 保持禁用，不会被跨平台误启用。
    """

    normalized_settings = normalize_mcp_runtime_settings(settings)
    desktop = resolve_desktop_mcp()
    servers = [
        replace(
            server,
            enabled=(
                normalized_settings.desktop_enabled
                if desktop is not None and server.name == desktop.server_name
                else False
            ),
        )
        if server.name in _DESKTOP_MCP_SERVER_NAMES
        else server
        for server in config.servers
        if server.name not in _RETIRED_DESKTOP_MCP_SERVER_NAMES
    ]
    return replace(config, servers=servers)
