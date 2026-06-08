from __future__ import annotations

import shutil
import sys
from dataclasses import replace
from pathlib import Path
from typing import Any, Callable, Protocol

from app.agent.mcp.bridge import MCPBridge, MCPToolSpec
from app.agent.mcp.config import MCPConfig, MCPServerConfig, load_mcp_config
from app.agent.mcp.settings import MCPRuntimeSettings, apply_mcp_runtime_settings
from app.agent.tool_registry import Tool, ToolRegistry
from app.core.debug_log import debug_log


class MCPBridgeLike(Protocol):
    def connect(self) -> None:
        """连接 MCP Server。"""

    def list_tools(self) -> list[MCPToolSpec]:
        """列出 MCP Server 暴露的工具。"""

    def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """调用 MCP 工具。"""

    def close(self) -> None:
        """关闭 MCP 连接。"""


BridgeFactory = Callable[[MCPServerConfig, float], MCPBridgeLike]


class MCPToolProvider:
    """把 MCP Server tools 注册为 Sakura 内部工具。"""

    def __init__(
        self,
        config: MCPConfig,
        bridge_factory: BridgeFactory | None = None,
    ) -> None:
        self.config = config
        self.bridge_factory = bridge_factory or MCPBridge
        self._bridges: list[MCPBridgeLike] = []
        self._tool_targets: dict[str, tuple[MCPBridgeLike, str]] = {}
        self._closed = False

    def register_tools(self, registry: ToolRegistry) -> int:
        self._closed = False
        if not self.config.enabled:
            debug_log("MCP", "MCP 配置未启用")
            return 0

        registered = 0
        for server in self.config.servers:
            if not server.enabled:
                debug_log("MCP", "跳过未启用服务器", {"server": server.name})
                continue
            bridge = self.bridge_factory(server, self.config.default_call_timeout)
            try:
                debug_log(
                    "MCP",
                    "连接服务器并读取工具",
                    {
                        "server": server.name,
                        "command": server.command,
                        "args": server.args,
                    },
                )
                bridge.connect()
                listed_tool_specs = bridge.list_tools()
                tool_specs = [
                    tool_spec
                    for tool_spec in listed_tool_specs
                    if server.allows_tool(tool_spec.name)
                ]
            except Exception as exc:
                print(f"[MCP] 连接或读取工具失败，已跳过 {server.name}：{exc}")
                debug_log("MCP", "连接或读取工具失败", {"server": server.name, "error": str(exc)})
                _close_quietly(bridge)
                continue

            server_registered = 0
            for tool_spec in tool_specs:
                internal_name = _build_internal_tool_name(server, tool_spec.name)
                if registry.get(internal_name) is not None:
                    print(f"[MCP] 工具名冲突，已跳过 {internal_name}。")
                    debug_log("MCP", "工具名冲突，已跳过", {"tool_name": internal_name})
                    continue
                registry.register(
                    Tool(
                        name=internal_name,
                        description=_build_description(server, tool_spec),
                        parameters=tool_spec.input_schema,
                        handler=self._make_handler(internal_name),
                        requires_confirmation=server.effective_tool_requires_confirmation(tool_spec.name),
                        group="mcp",
                        risk=server.effective_tool_risk(tool_spec.name),
                    )
                )
                self._tool_targets[internal_name] = (bridge, tool_spec.name)
                registered += 1
                server_registered += 1

            debug_log(
                "MCP",
                "服务器工具注册完成",
                {
                    "server": server.name,
                    "listed": len(listed_tool_specs),
                    "filtered": len(listed_tool_specs) - len(tool_specs),
                    "registered": server_registered,
                },
            )
            if server_registered:
                self._bridges.append(bridge)
            else:
                _close_quietly(bridge)

        return registered

    def close(self) -> None:
        debug_log("MCP", "关闭 MCP Provider", {"bridges": len(self._bridges)})
        self._closed = True
        for bridge in self._bridges:
            _close_quietly(bridge)
        self._bridges = []
        self._tool_targets = {}

    def _make_handler(self, internal_name: str) -> Callable[[dict[str, Any]], dict[str, Any]]:
        def handler(arguments: dict[str, Any]) -> dict[str, Any]:
            if self._closed:
                return _closed_tool_result(internal_name)
            target = self._tool_targets.get(internal_name)
            if target is None:
                return _closed_tool_result(internal_name)
            bridge, external_name = target
            return bridge.call_tool(external_name, arguments)

        return handler


def register_mcp_tools_from_config(
    base_dir: Path,
    registry: ToolRegistry,
    bridge_factory: BridgeFactory | None = None,
    runtime_settings: MCPRuntimeSettings | None = None,
) -> MCPToolProvider | None:
    try:
        config = load_mcp_config(base_dir / "data" / "config" / "mcp.yaml")
        mcp_settings = runtime_settings or MCPRuntimeSettings()
    except Exception as exc:
        print(f"[MCP] 配置读取失败，已跳过 MCP：{exc}")
        debug_log("MCP", "配置读取失败，已跳过 MCP", {"error": str(exc)})
        return None
    config = apply_mcp_runtime_settings(config, mcp_settings)
    config = _resolve_runtime_tokens(config, base_dir)
    provider = MCPToolProvider(config, bridge_factory=bridge_factory)
    registered = provider.register_tools(registry)
    if registered == 0:
        provider.close()
        debug_log("MCP", "没有注册任何 MCP 工具")
        return None
    print(f"[MCP] 已注册 {registered} 个 MCP 工具。")
    debug_log("MCP", "MCP 工具注册完成", {"registered": registered})
    return provider


def _build_internal_tool_name(server: MCPServerConfig, external_name: str) -> str:
    return f"{server.effective_name_prefix()}{external_name}"


def _build_description(server: MCPServerConfig, tool_spec: MCPToolSpec) -> str:
    description = tool_spec.description.strip() or "MCP Server 提供的外部工具。"
    return f"[MCP:{server.name}] {description}"


def _closed_tool_result(tool_name: str) -> dict[str, Any]:
    message = f"MCP 工具 {tool_name} 所属连接已关闭，请重新打开设置或重启 Sakura 后再试。"
    return {
        "isError": True,
        "content": [{"type": "text", "text": message}],
        "error": message,
    }


def _resolve_runtime_tokens(config: MCPConfig, base_dir: Path) -> MCPConfig:
    """解析本地运行时占位符，避免 MCP 配置写死 Python 路径和项目目录。"""

    servers = [
        replace(
            server,
            command=_expand_runtime_tokens(server.command, base_dir),
            args=[_expand_runtime_tokens(arg, base_dir) for arg in server.args],
            env={
                key: _expand_runtime_tokens(value, base_dir)
                for key, value in server.env.items()
            },
            url=_expand_runtime_tokens(server.url, base_dir),
        )
        for server in config.servers
    ]
    return replace(config, servers=servers)


def _expand_runtime_tokens(value: str, base_dir: Path) -> str:
    return (
        value.replace("{python}", sys.executable)
        .replace("{node}", _runtime_executable("node"))
        .replace("{uv}", _runtime_executable("uv"))
        .replace("{uvx}", _runtime_executable("uvx"))
        .replace("{base_dir}", str(base_dir))
    )


def _runtime_executable(command: str) -> str:
    for candidate in _python_script_candidates(command):
        if candidate.is_file():
            return str(candidate)
    return shutil.which(command) or command


def _python_script_candidates(command: str) -> list[Path]:
    script_name = command
    if sys.platform == "win32" and not script_name.lower().endswith(".exe"):
        script_name = f"{script_name}.exe"

    executable_dir = Path(sys.executable).resolve().parent
    return [
        executable_dir / script_name,
        executable_dir / "Scripts" / script_name,
    ]


def _close_quietly(bridge: MCPBridgeLike) -> None:
    try:
        bridge.close()
    except Exception as exc:
        print(f"[MCP] 关闭连接失败：{exc}")
        debug_log("MCP", "关闭连接失败", {"error": str(exc)})
