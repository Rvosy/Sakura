from __future__ import annotations

import json
import re
import sys
import threading
import time
from dataclasses import replace
from pathlib import Path
from typing import Any, Callable, Protocol

from app.agent.mcp.bridge import MCPBridge, MCPToolSpec
from app.agent.mcp.config import MCPConfig, MCPServerConfig, load_mcp_config
from app.agent.tools import Tool, ToolRegistry
from app.core.runtime_log import log_event
from app.core.runtime_resources import ResourceRegistry, ServiceResource
from app.storage.paths import StoragePaths, user_facing_path


MAX_MCP_TOOL_NAME_CHARS = 64
MAX_MCP_TOOL_DESCRIPTION_CHARS = 1_024
MAX_MCP_TOOL_SCHEMA_BYTES = 64 * 1024
MAX_MCP_TOOL_SCHEMA_DEPTH = 16
MAX_MCP_TOOL_SCHEMA_NODES = 2_048
_PUBLIC_SERVER_ID = re.compile(r"[^A-Za-z0-9_.-]+")


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
        *,
        resource_registry: ResourceRegistry | None = None,
        config_state: str = "valid",
        reason_code: str = "STARTING",
    ) -> None:
        self.config = config
        self.bridge_factory = bridge_factory
        self.resource_registry = resource_registry or ResourceRegistry()
        self._bridges: list[MCPBridgeLike] = []
        self._all_bridges: list[MCPBridgeLike] = []
        self._tool_targets: dict[str, tuple[MCPBridgeLike, str]] = {}
        self._registered_tools: dict[str, Tool] = {}
        self._registry: ToolRegistry | None = None
        self._lock = threading.RLock()
        self._registration_thread: threading.Thread | None = None
        self._registration_complete = threading.Event()
        self._closed = False
        self._config_state = config_state
        self._reason_code = reason_code
        self._server_status: dict[str, dict[str, Any]] = {
            server.name: {
                "serverId": _public_server_id(server.name),
                "transport": server.transport,
                "enabled": bool(self.config.enabled and server.enabled),
                "state": "starting" if self.config.enabled and server.enabled else "disabled",
                "reasonCode": "STARTING" if self.config.enabled and server.enabled else "SERVER_DISABLED",
                "toolCount": 0,
            }
            for server in self.config.servers
        }
        self._provider_resource: ServiceResource = self.resource_registry.track_service(
            stop=self.close,
            is_running=lambda: not self._closed and bool(self._bridges),
            label="mcp_provider",
            shutdown_order=800,
        )

    def start_registration(self, registry: ToolRegistry) -> None:
        """Start server discovery without delaying Core readiness."""

        with self._lock:
            if self._closed:
                raise RuntimeError("MCP Provider 已关闭。")
            if self._registration_thread is not None:
                return
            self._registry = registry
            thread = threading.Thread(
                target=self._run_registration,
                args=(registry,),
                name="sakura-mcp-register",
                daemon=True,
            )
            self._registration_thread = thread
            thread.start()

    def _run_registration(self, registry: ToolRegistry) -> None:
        try:
            self.register_tools(registry)
        finally:
            self._registration_complete.set()

    def wait_registration(
        self,
        timeout: float,
        *,
        cancel_checker: Callable[[], None] | None = None,
    ) -> bool:
        """Wait boundedly for discovery so a prompt does not silently lose tools."""

        deadline = time.monotonic() + max(0.0, float(timeout))
        while not self._registration_complete.is_set():
            if cancel_checker is not None:
                cancel_checker()
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False
            self._registration_complete.wait(timeout=min(0.05, remaining))
        if cancel_checker is not None:
            cancel_checker()
        with self._lock:
            return not self._closed

    def register_tools(self, registry: ToolRegistry) -> int:
        with self._lock:
            if self._closed:
                return 0
            self._registry = registry
        if not self.config.enabled:
            with self._lock:
                if self._config_state == "valid":
                    self._reason_code = "CONFIG_DISABLED"
                reason_code = self._reason_code
            log_event("MCP", "MCP 配置未启用", {"reason_code": reason_code})
            return 0

        registered = 0
        for server in self.config.servers:
            with self._lock:
                if self._closed:
                    break
            if not server.enabled:
                self._set_server_status(server, "disabled", "SERVER_DISABLED", 0)
                continue
            self._set_server_status(server, "starting", "STARTING", 0)
            bridge = self._create_bridge(server)
            with self._lock:
                if self._closed:
                    close_before_connect = True
                else:
                    self._all_bridges.append(bridge)
                    close_before_connect = False
            if close_before_connect:
                _close_quietly(bridge)
                break
            try:
                log_event(
                    "MCP",
                    "连接服务器并读取工具",
                    {"server_id": _public_server_id(server.name), "transport": server.transport},
                )
                bridge.connect()
                listed_tool_specs = bridge.list_tools()
                tool_specs = [
                    validated
                    for tool_spec in listed_tool_specs
                    if server.allows_tool(tool_spec.name)
                    if (validated := _validated_tool_spec(server, tool_spec)) is not None
                ]
            except Exception as exc:  # noqa: BLE001 - isolate one configured server
                reason = _stable_failure_code(exc)
                self._set_server_status(server, "degraded", reason, 0)
                log_event(
                    "MCP",
                    "连接或读取工具失败，已跳过",
                    {"server_id": _public_server_id(server.name), "reason_code": reason},
                )
                _close_quietly(bridge)
                continue

            server_registered = 0
            for tool_spec in tool_specs:
                internal_name = _build_internal_tool_name(server, tool_spec.name)
                if registry.get(internal_name) is not None:
                    log_event("MCP", "工具名冲突，已跳过", {"reason_code": "TOOL_NAME_CONFLICT"})
                    continue
                tool = Tool(
                    name=internal_name,
                    description=_build_description(server, tool_spec),
                    parameters=tool_spec.input_schema,
                    handler=self._make_handler(internal_name),
                    group="mcp",
                    risk=server.effective_tool_risk(tool_spec.name),
                    source="mcp",
                )
                with self._lock:
                    if self._closed:
                        break
                    registry.register(tool)
                    self._registered_tools[internal_name] = tool
                    self._tool_targets[internal_name] = (bridge, tool_spec.name)
                registered += 1
                server_registered += 1

            log_event(
                "MCP",
                "服务器工具注册完成",
                {
                    "server_id": _public_server_id(server.name),
                    "listed": len(listed_tool_specs),
                    "filtered": len(listed_tool_specs) - len(tool_specs),
                    "registered": server_registered,
                },
            )
            if server_registered:
                with self._lock:
                    if self._closed:
                        keep_bridge = False
                    else:
                        self._bridges.append(bridge)
                        status = self._server_status.get(server.name)
                        if status is not None:
                            status.update(
                                state="ready",
                                reasonCode="READY",
                                toolCount=server_registered,
                            )
                        keep_bridge = True
                if not keep_bridge:
                    _close_quietly(bridge)
                    break
            else:
                _close_quietly(bridge)
                self._set_server_status(server, "degraded", "NO_TOOLS", 0)

        with self._lock:
            if self._closed:
                return registered
            ready = any(item["state"] == "ready" for item in self._server_status.values())
            self._reason_code = "READY" if ready else "NO_READY_SERVERS"

        return registered

    def close(self) -> None:
        with self._lock:
            if self._closed and not self._all_bridges:
                return
            self._closed = True
            self._reason_code = "STOPPING"
            self._registration_complete.set()
            for status in self._server_status.values():
                if status["state"] not in {"disabled", "stopped"}:
                    status["state"] = "stopping"
                    status["reasonCode"] = "STOPPING"
            bridges = list(self._all_bridges)
            self._all_bridges = []
            self._bridges = []
            registry = self._registry
            registered_tools = dict(self._registered_tools)
            self._registered_tools = {}
            self._tool_targets = {}
            registration_thread = self._registration_thread
        log_event("MCP", "关闭 MCP Provider", {"bridges": len(bridges)})
        _close_bridges_bounded(bridges, timeout=5.0)
        if registry is not None:
            for name, tool in registered_tools.items():
                registry.unregister(name, expected=tool)
        if registration_thread is not None and registration_thread is not threading.current_thread():
            registration_thread.join(timeout=1.0)
        with self._lock:
            for status in self._server_status.values():
                if status["state"] != "disabled":
                    status["state"] = "stopped"
                    status["reasonCode"] = "STOPPED"
                    status["toolCount"] = 0
            self._reason_code = "STOPPED"
        self._provider_resource.detach()

    def status_snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "configState": self._config_state,
                "reasonCode": self._reason_code,
                "servers": [dict(self._server_status[server.name]) for server in self.config.servers],
            }

    def _create_bridge(self, server: MCPServerConfig) -> MCPBridgeLike:
        if self.bridge_factory is not None:
            return self.bridge_factory(server, self.config.default_call_timeout)
        return MCPBridge(
            server,
            self.config.default_call_timeout,
            resource_registry=self.resource_registry,
        )

    def _make_handler(self, internal_name: str) -> Callable[[dict[str, Any]], dict[str, Any]]:
        def handler(arguments: dict[str, Any]) -> dict[str, Any]:
            with self._lock:
                if self._closed:
                    return _closed_tool_result(internal_name)
                target = self._tool_targets.get(internal_name)
            if target is None:
                return _closed_tool_result(internal_name)
            bridge, external_name = target
            try:
                return bridge.call_tool(external_name, arguments)
            except Exception as exc:  # noqa: BLE001 - never expose transport details to the model
                reason = _stable_failure_code(exc)
                log_event("MCP", "工具调用失败", {"reason_code": reason})
                return _failed_tool_result(reason)

        return handler

    def _set_server_status(
        self,
        server: MCPServerConfig,
        state: str,
        reason_code: str,
        tool_count: int,
    ) -> None:
        with self._lock:
            status = self._server_status.get(server.name)
            if status is None or (self._closed and state not in {"stopping", "stopped"}):
                return
            status.update(
                state=state,
                reasonCode=reason_code,
                toolCount=max(0, int(tool_count)),
            )


def register_mcp_tools_from_config(
    base_dir: Path,
    registry: ToolRegistry,
    bridge_factory: BridgeFactory | None = None,
    resource_registry: ResourceRegistry | None = None,
    distribution_root: Path | None = None,
) -> MCPToolProvider | None:
    try:
        config = load_mcp_config(StoragePaths(base_dir).mcp_config())
    except Exception as exc:
        log_event(
            "MCP",
            "配置读取失败，已跳过 MCP",
            {
                "diagnostic": str(exc),
                "error_type": type(exc).__name__,
                "reason_code": "MCP_CONFIG_LOAD_FAILED",
                "stage": "config_load",
            },
        )
        return None
    config = _resolve_runtime_tokens(config, base_dir, distribution_root)
    provider = MCPToolProvider(config, bridge_factory=bridge_factory, resource_registry=resource_registry)
    registered = provider.register_tools(registry)
    if registered == 0:
        provider.close()
        log_event("MCP", "没有注册任何 MCP 工具")
        return None
    log_event("MCP", "MCP 工具注册完成", {"registered": registered})
    return provider


def start_mcp_tools_from_config(
    base_dir: Path,
    registry: ToolRegistry,
    *,
    bridge_factory: BridgeFactory | None = None,
    resource_registry: ResourceRegistry | None = None,
    distribution_root: Path | None = None,
) -> MCPToolProvider:
    """Create the generation owner and discover configured servers in the background."""

    config_path = StoragePaths(base_dir).mcp_config()
    config_state = "missing" if not config_path.exists() else "valid"
    reason_code = "CONFIG_MISSING" if config_state == "missing" else "STARTING"
    try:
        config = load_mcp_config(config_path)
        config = _resolve_runtime_tokens(config, base_dir, distribution_root)
    except Exception:  # noqa: BLE001 - damaged MCP config degrades only this domain
        config = MCPConfig()
        config_state = "invalid"
        reason_code = "CONFIG_INVALID"
        log_event("MCP", "配置读取失败，已跳过 MCP", {"reason_code": reason_code})
    provider = MCPToolProvider(
        config,
        bridge_factory=bridge_factory,
        resource_registry=resource_registry,
        config_state=config_state,
        reason_code=reason_code,
    )
    provider.start_registration(registry)
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


def _failed_tool_result(reason_code: str) -> dict[str, Any]:
    message = "MCP 工具调用失败，请稍后重试或检查服务器运行状态。"
    return {
        "isError": True,
        "content": [{"type": "text", "text": message}],
        "error": message,
        "reason_code": reason_code,
    }


def _resolve_runtime_tokens(
    config: MCPConfig,
    base_dir: Path,
    distribution_root: Path | None = None,
) -> MCPConfig:
    """解析本地运行时占位符，避免 MCP 配置写死 Python 路径和项目目录。"""

    servers = [
        replace(
            server,
            command=_expand_runtime_tokens(server.command, base_dir, distribution_root),
            args=[
                _expand_runtime_tokens(arg, base_dir, distribution_root)
                for arg in server.args
            ],
            env={
                key: _expand_runtime_tokens(value, base_dir, distribution_root)
                for key, value in server.env.items()
            },
            url=_expand_runtime_tokens(server.url, base_dir, distribution_root),
        )
        for server in config.servers
    ]
    return replace(config, servers=servers)


def _expand_runtime_tokens(
    value: str,
    base_dir: Path,
    distribution_root: Path | None = None,
) -> str:
    distribution_root = distribution_root or base_dir
    packaged_core_root = distribution_root / "core"
    core_root = (
        packaged_core_root
        if (packaged_core_root / "app").is_dir()
        else distribution_root
    )
    return (
        value.replace("{python}", sys.executable)
        .replace("{node}", _runtime_executable("node"))
        .replace("{uv}", _runtime_executable("uv"))
        .replace("{uvx}", _runtime_executable("uvx"))
        .replace("{base_dir}", user_facing_path(base_dir))
        .replace("{distribution_root}", user_facing_path(distribution_root))
        .replace("{core_root}", user_facing_path(core_root))
    )


def _runtime_executable(command: str) -> str:
    for candidate in _python_script_candidates(command):
        if candidate.is_file():
            return str(candidate)
    # Keep unresolved runtime tokens inside the bundled runtime.  The bridge's
    # command preflight will publish a stable missing-command state instead of
    # silently executing a same-named binary from the user's PATH.
    return str(_python_script_candidates(command)[-1])


def _python_script_candidates(command: str) -> list[Path]:
    script_name = command
    if sys.platform == "win32" and not script_name.lower().endswith(".exe"):
        script_name = f"{script_name}.exe"

    executable_dir = Path(sys.executable).resolve().parent
    return [
        executable_dir / "tools" / script_name,
        executable_dir / script_name,
        executable_dir / "Scripts" / script_name,
    ]


def _close_quietly(bridge: MCPBridgeLike) -> None:
    try:
        bridge.close()
    except Exception:  # noqa: BLE001 - cleanup is best effort and details may contain credentials
        log_event("MCP", "关闭连接失败", {"reason_code": "CLOSE_FAILED"})


def _close_bridges_bounded(bridges: list[MCPBridgeLike], *, timeout: float) -> None:
    workers = [
        threading.Thread(
            target=_close_quietly,
            args=(bridge,),
            name="sakura-mcp-close",
            daemon=True,
        )
        for bridge in bridges
    ]
    for worker in workers:
        worker.start()
    deadline = time.monotonic() + max(0.0, timeout)
    for worker in workers:
        worker.join(max(0.0, deadline - time.monotonic()))
    if any(worker.is_alive() for worker in workers):
        log_event("MCP", "MCP 连接清理超过总时限", {"reason_code": "CLOSE_TIMEOUT"})


def _public_server_id(value: str) -> str:
    normalized = _PUBLIC_SERVER_ID.sub("_", value.strip())[:64].strip("._-")
    return normalized or "server"


def _validated_tool_spec(
    server: MCPServerConfig,
    tool_spec: MCPToolSpec,
) -> MCPToolSpec | None:
    external_name = tool_spec.name.strip()
    internal_name = _build_internal_tool_name(server, external_name)
    if (
        not external_name
        or len(internal_name) > MAX_MCP_TOOL_NAME_CHARS
        or any(not (character.isascii() and (character.isalnum() or character in "_-")) for character in internal_name)
    ):
        return None
    schema = tool_spec.input_schema
    try:
        encoded = json.dumps(schema, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    except (TypeError, ValueError):
        return None
    if len(encoded) > MAX_MCP_TOOL_SCHEMA_BYTES:
        return None
    nodes, depth = _json_shape(schema)
    if nodes > MAX_MCP_TOOL_SCHEMA_NODES or depth > MAX_MCP_TOOL_SCHEMA_DEPTH:
        return None
    return MCPToolSpec(
        name=external_name,
        description=tool_spec.description[:MAX_MCP_TOOL_DESCRIPTION_CHARS],
        input_schema=schema,
    )


def _json_shape(value: Any, depth: int = 1) -> tuple[int, int]:
    nodes = 1
    maximum_depth = depth
    children = value.values() if isinstance(value, dict) else value if isinstance(value, list) else ()
    for child in children:
        child_nodes, child_depth = _json_shape(child, depth + 1)
        nodes += child_nodes
        maximum_depth = max(maximum_depth, child_depth)
        if nodes > MAX_MCP_TOOL_SCHEMA_NODES or maximum_depth > MAX_MCP_TOOL_SCHEMA_DEPTH:
            break
    return nodes, maximum_depth


def _stable_failure_code(error: BaseException) -> str:
    declared = getattr(error, "reason_code", None)
    if isinstance(declared, str) and declared in {
        "COMMAND_NOT_FOUND",
        "COMMAND_NOT_EXECUTABLE",
        "TIMEOUT",
        "CANCELLED",
        "TRANSPORT_FAILED",
    }:
        return declared
    name = type(error).__name__.upper()
    if isinstance(error, TimeoutError) or "TIMEOUT" in name:
        return "TIMEOUT"
    if isinstance(error, FileNotFoundError):
        return "COMMAND_NOT_FOUND"
    if isinstance(error, PermissionError):
        return "COMMAND_NOT_EXECUTABLE"
    if "CANCEL" in name:
        return "CANCELLED"
    return "TRANSPORT_FAILED"
