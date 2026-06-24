from __future__ import annotations

import sys
import uuid
from pathlib import Path

import pytest

import app.agent.mcp.provider as mcp_provider_module
from app.agent.mcp.bridge import MCPBridge, MCPToolSpec
from app.agent.mcp.config import MCPConfig, MCPServerConfig, load_mcp_config
from app.agent.mcp.provider import MCPToolProvider
from app.agent.tools import ToolRegistry
from app.core.resource_manager import ResourceRegistry


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
  windows:
    enabled: true
    transport: stdio
    command: "{uv}"
""".strip(),
        encoding="utf-8",
    )
    monkeypatch.setattr(mcp_provider_module.sys, "executable", str(python_exe))

    resolved = mcp_provider_module._resolve_runtime_tokens(load_mcp_config(config_path), root)

    assert resolved.servers[0].command == str(uv_exe)


def test_mcp_bridge_missing_stdio_command_has_actionable_error() -> None:
    bridge = MCPBridge(
        MCPServerConfig(
            name="windows",
            transport="stdio",
            command=f"sakura_missing_mcp_command_{uuid.uuid4().hex}",
        ),
        default_call_timeout=1,
    )

    with pytest.raises(RuntimeError) as exc_info:
        bridge.connect()

    error = str(exc_info.value)
    assert "找不到命令" in error
    assert "install.bat" in error
    assert "WinError" not in error
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

    registry.stop_all()
    closed_result = tool_registry.execute("echo", {"text": "late"}).content

    assert bridge.closed_count == 1
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
