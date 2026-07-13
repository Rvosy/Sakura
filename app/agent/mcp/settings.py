from __future__ import annotations

import sys
from dataclasses import dataclass
from dataclasses import replace

from app.agent.mcp.config import MCPConfig


WINDOWS_MCP_ENABLED_KEY = "WINDOWS_MCP_ENABLED"
# 文案与平台无关；保留旧常量名做向后兼容别名。
DESKTOP_MCP_EXPERIMENTAL_TEXT = "实验性功能，供想要尝鲜的用户使用；可能不稳定，请谨慎开启"
WINDOWS_MCP_EXPERIMENTAL_TEXT = DESKTOP_MCP_EXPERIMENTAL_TEXT


@dataclass(frozen=True)
class DesktopMCP:
    """某平台对应的桌面控制 MCP：mcp.yaml 里的 server 名 + UI 显示名。"""

    server_name: str
    label: str


# 平台 -> 桌面控制 MCP；不在表内的平台视为暂不支持（如 Linux）。
_DESKTOP_MCP_BY_PLATFORM: dict[str, DesktopMCP] = {
    "win32": DesktopMCP(server_name="windows", label="Windows MCP"),
    "darwin": DesktopMCP(server_name="macos", label="macOS MCP"),
}
_DESKTOP_MCP_SERVER_NAMES = frozenset(
    desktop.server_name for desktop in _DESKTOP_MCP_BY_PLATFORM.values()
)


def resolve_desktop_mcp(platform: str | None = None) -> DesktopMCP | None:
    """返回当前（或指定）平台的桌面控制 MCP；不支持的平台返回 None。"""

    key = sys.platform if platform is None else platform
    return _DESKTOP_MCP_BY_PLATFORM.get(key)


# 当前平台是否提供桌面控制 MCP；旧名保留以兼容既有引用。
DESKTOP_MCP_AVAILABLE = resolve_desktop_mcp() is not None
WINDOWS_MCP_AVAILABLE = DESKTOP_MCP_AVAILABLE


@dataclass(frozen=True)
class MCPRuntimeSettings:
    """MCP 运行时开关；由 data/config/system_config.yaml 提供。

    字段名 windows_enabled 与持久化键 WINDOWS_MCP_ENABLED 保留做向后兼容，
    语义为“启用当前平台对应的桌面控制 MCP”。
    """

    windows_enabled: bool = False


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

    只动当前平台那一个 server（其余平台的 server 保持 mcp.yaml 中的原状，
    因此 Windows 上的 macos server、macOS 上的 windows server 都不会被误启用）。
    """

    normalized_settings = normalize_mcp_runtime_settings(settings)
    desktop = resolve_desktop_mcp()
    servers = [
        replace(
            server,
            enabled=(
                normalized_settings.windows_enabled
                if desktop is not None and server.name == desktop.server_name
                else False
            ),
        )
        if server.name in _DESKTOP_MCP_SERVER_NAMES
        else server
        for server in config.servers
    ]
    return replace(config, servers=servers)
