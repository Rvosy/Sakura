from __future__ import annotations

import sys
import uuid
from pathlib import Path

import pytest

import app.agent.mcp.provider as mcp_provider_module
from app.agent.mcp.bridge import MCPBridge, MCPToolSpec
from app.agent.mcp.config import MCPConfig, MCPServerConfig, load_mcp_config
from app.agent.mcp.provider import MCPToolProvider
from app.agent.mcp.settings import (
    MCPRuntimeSettings,
    apply_mcp_runtime_settings,
    resolve_desktop_mcp,
)
from app.agent.tools import ToolRegistry
from app.core.runtime_resources import ResourceRegistry


def test_windows_desktop_mcp_is_unsupported_and_retired_from_runtime_config() -> None:
    config = MCPConfig(
        enabled=True,
        servers=[
            MCPServerConfig(name="windows", transport="stdio", command="windows-mcp"),
            MCPServerConfig(name="fixture", transport="stdio", command="python"),
        ],
    )

    applied = apply_mcp_runtime_settings(config, MCPRuntimeSettings(desktop_enabled=True))

    assert resolve_desktop_mcp("win32") is None
    assert [server.name for server in applied.servers] == ["fixture"]


def test_mcp_runtime_token_prefers_current_python_scripts(monkeypatch: pytest.MonkeyPatch) -> None:
    root = _runtime_root_path("mcp_uv_runtime_token")
    python_dir = root / "runtime"
    scripts_dir = python_dir / "Scripts"
    scripts_dir.mkdir(parents=True)
    python_exe = python_dir / ("python.exe" if sys.platform == "win32" else "python")
    python_exe.write_text("", encoding="utf-8")
    uv_exe = scripts_dir / ("uv.exe" if sys.platform == "win32" else "uv")
    uv_exe.write_text("", encoding="utf-8")
    config_path = root / "mcp.yaml"
    config_path.write_text(
        """
enabled: true
servers:
  fixture:
    enabled: true
    transport: stdio
    command: "{uv}"
""".strip(),
        encoding="utf-8",
    )
    monkeypatch.setattr(mcp_provider_module.sys, "executable", str(python_exe))

    resolved = mcp_provider_module._resolve_runtime_tokens(load_mcp_config(config_path), root)

    assert resolved.servers[0].command == str(uv_exe)


@pytest.mark.parametrize(
    "retired_field",
    [
        "requires_confirmation: true",
        "tool_policies:\n      mutate:\n        requires_confirmation: true",
    ],
)
def test_mcp_config_rejects_retired_confirmation_fields(
    tmp_path: Path,
    retired_field: str,
) -> None:
    config_path = tmp_path / "mcp.yaml"
    config_path.write_text(
        (
            "enabled: true\n"
            "servers:\n"
            "  fixture:\n"
            "    transport: stdio\n"
            "    command: python\n"
            f"    {retired_field}\n"
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="requires_confirmation"):
        load_mcp_config(config_path)


@pytest.mark.parametrize(
    "servers_block",
    [
        "- name: fixture\n    transport: stdio\n    command: python",
        "fixture:\n    type: stdio\n    command: python",
        "fixture:\n    transport: stdio\n    command: python\n    allow_tools: [mutate]",
        "fixture:\n    transport: stdio\n    command: python\n    deny_tools: [mutate]",
        "fixture:\n    transport: stdio\n    command: python\n    tools: [mutate]",
    ],
)
def test_mcp_config_rejects_retired_shape_aliases(
    tmp_path: Path,
    servers_block: str,
) -> None:
    config_path = tmp_path / "mcp.yaml"
    config_path.write_text(
        f"enabled: true\nservers:\n  {servers_block}\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError):
        load_mcp_config(config_path)


def test_mcp_bridge_missing_stdio_command_has_actionable_error() -> None:
    bridge = MCPBridge(
        MCPServerConfig(
            name="fixture",
            transport="stdio",
            command=f"sakura_missing_mcp_command_{uuid.uuid4().hex}",
        ),
        default_call_timeout=1,
    )

    with pytest.raises(RuntimeError) as exc_info:
        bridge.connect()

    error = str(exc_info.value)
    assert "找不到命令" in error
    assert "bundled runtime" in error
    assert "WinError" not in error
    bridge.close()


def test_mcp_bridge_timeout_replaces_polluted_event_loop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry = ResourceRegistry()
    bridge = MCPBridge(
        MCPServerConfig(name="demo", transport="sse", url="https://example.com/mcp"),
        default_call_timeout=1,
        resource_registry=registry,
    )
    polluted_loop = bridge._loop_resource
    monkeypatch.setattr(polluted_loop, "stop", lambda _timeout_ms: False)

    bridge._invalidate_timed_out_connection()

    assert bridge._loop_resource is not polluted_loop
    assert bridge._loop_resource in registry._resources
    bridge.close()


def test_mcp_provider_closes_via_resource_registry_and_handlers_fail_closed() -> None:
    registry = ResourceRegistry()
    tool_registry = ToolRegistry()
    bridge = _FakeBridge()
    provider = MCPToolProvider(
        MCPConfig(
            enabled=True,
            default_call_timeout=1,
            servers=[
                MCPServerConfig(
                    name="demo",
                    transport="stdio",
                    command="python",
                    name_prefix="",
                )
            ],
        ),
        bridge_factory=lambda _server, _timeout: bridge,
        resource_registry=registry,
    )

    assert provider.register_tools(tool_registry) == 1
    assert tool_registry.execute("echo", {"text": "hi"}).content == {"ok": {"text": "hi"}}
    tool = tool_registry.get("echo")
    assert tool is not None and tool.handler is not None

    registry.stop_all()
    closed_result = tool.handler({"text": "late"})

    assert bridge.closed_count == 1
    assert tool_registry.get("echo") is None
    assert closed_result["isError"] is True
    assert "已关闭" in closed_result["error"]

    provider.close()
    registry.stop_all()
    assert bridge.closed_count == 1


class _FakeBridge:
    def __init__(self) -> None:
        self.closed_count = 0

    def connect(self) -> None:
        pass

    def list_tools(self) -> list[MCPToolSpec]:
        return [MCPToolSpec(name="echo", description="Echo", input_schema={"type": "object"})]

    def call_tool(self, name: str, arguments: dict[str, object]) -> dict[str, object]:
        return {"ok": arguments}

    def close(self) -> None:
        self.closed_count += 1


def _runtime_root_path(name: str) -> Path:
    return (
        Path(__file__).resolve().parents[2]
        / "temp"
        / "test_runtime"
        / uuid.uuid4().hex
        / name
    )
