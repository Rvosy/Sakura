from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

import pytest

from app.agent.mcp.bridge import MCPToolSpec
from app.agent.mcp.config import MCPConfig, MCPServerConfig
from app.agent.mcp.provider import MCPToolProvider
from app.agent.tools import ToolRegistry
from app.core.runtime_resources import ResourceRegistry
from app.core_host.mcp_status import MCPStatusBoundary


class _Bridge:
    def __init__(self) -> None:
        self.closed = False
        self.calls: list[tuple[str, dict[str, object]]] = []

    def connect(self) -> None:
        return None

    def list_tools(self) -> list[MCPToolSpec]:
        return [
            MCPToolSpec(
                name="mutate",
                description="Mutate fixture state",
                input_schema={
                    "type": "object",
                    "properties": {"value": {"type": "string"}},
                    "required": ["value"],
                },
            )
        ]

    def call_tool(self, name: str, arguments: dict[str, object]) -> dict[str, object]:
        self.calls.append((name, dict(arguments)))
        return {"content": [{"type": "text", "text": "ok"}], "is_error": False}

    def close(self) -> None:
        self.closed = True


def _request(name: str, payload: dict[str, object]) -> dict[str, object]:
    return {
        "protocolMajor": 2,
        "protocolMinor": 2,
        "kind": "request",
        "generationId": "generation-1",
        "generationCredential": "c" * 32,
        "id": f"request-{name}",
        "name": name,
        "payload": payload,
        "deadlineMs": 3_000,
        "priority": "interactive",
    }


def test_mcp_provider_is_generation_scoped_and_unregisters_tools() -> None:
    bridge = _Bridge()
    registry = ToolRegistry()
    resources = ResourceRegistry()
    provider = MCPToolProvider(
        MCPConfig(
            enabled=True,
            servers=[
                MCPServerConfig(
                    name="fixture",
                    transport="stdio",
                    command=sys.executable,
                    name_prefix="fixture__",
                    risk="high",
                )
            ],
        ),
        bridge_factory=lambda _server, _timeout: bridge,
        resource_registry=resources,
    )

    provider.start_registration(registry)
    deadline = time.monotonic() + 2
    while (
        provider.status_snapshot()["reasonCode"] != "READY"
        and time.monotonic() < deadline
    ):
        time.sleep(0.01)

    tool = registry.get("fixture__mutate")
    assert tool is not None and tool.risk == "high"
    assert provider.status_snapshot()["servers"] == [
        {
            "serverId": "fixture",
            "transport": "stdio",
            "enabled": True,
            "state": "ready",
            "reasonCode": "READY",
            "toolCount": 1,
        }
    ]

    saved_handler = tool.handler
    provider.close()
    assert registry.get("fixture__mutate") is None
    assert bridge.closed is True
    assert saved_handler is not None
    assert saved_handler({"value": "late"})["isError"] is True


def test_mcp_provider_close_wins_registration_race() -> None:
    entered = threading.Event()
    release = threading.Event()

    class RacingBridge(_Bridge):
        def connect(self) -> None:
            entered.set()
            release.wait(timeout=2)

    bridge = RacingBridge()
    registry = ToolRegistry()
    provider = MCPToolProvider(
        MCPConfig(
            enabled=True,
            servers=[
                MCPServerConfig(
                    name="fixture",
                    transport="stdio",
                    command=sys.executable,
                    name_prefix="fixture__",
                )
            ],
        ),
        bridge_factory=lambda _server, _timeout: bridge,
    )
    provider.start_registration(registry)
    assert entered.wait(timeout=1)

    provider.close()
    release.set()
    deadline = time.monotonic() + 1
    while provider._registration_thread.is_alive() and time.monotonic() < deadline:
        time.sleep(0.01)

    assert registry.get("fixture__mutate") is None
    assert provider.status_snapshot() == {
        "configState": "valid",
        "reasonCode": "STOPPED",
        "servers": [
            {
                "serverId": "fixture",
                "transport": "stdio",
                "enabled": True,
                "state": "stopped",
                "reasonCode": "STOPPED",
                "toolCount": 0,
            }
        ],
    }
    assert bridge.closed is True


def test_mcp_prompt_wait_is_bounded_cancelable_and_released_by_registration() -> None:
    entered = threading.Event()
    release = threading.Event()

    class BlockingBridge(_Bridge):
        def connect(self) -> None:
            entered.set()
            release.wait(timeout=2)

    provider = MCPToolProvider(
        MCPConfig(
            enabled=True,
            servers=[
                MCPServerConfig(
                    name="fixture",
                    transport="stdio",
                    command=sys.executable,
                    name_prefix="fixture__",
                )
            ],
        ),
        bridge_factory=lambda _server, _timeout: BlockingBridge(),
    )
    registry = ToolRegistry()
    provider.start_registration(registry)
    assert entered.wait(timeout=1)
    assert provider.wait_registration(0.02) is False

    calls = 0

    def cancel_checker() -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("cancelled-for-test")

    with pytest.raises(RuntimeError, match="cancelled-for-test"):
        provider.wait_registration(1, cancel_checker=cancel_checker)

    release.set()
    assert provider.wait_registration(1) is True
    assert registry.get("fixture__mutate") is not None
    provider.close()


def test_mcp_prompt_wait_is_released_when_provider_closes() -> None:
    entered = threading.Event()
    release = threading.Event()

    class BlockingBridge(_Bridge):
        def connect(self) -> None:
            entered.set()
            release.wait(timeout=2)

    provider = MCPToolProvider(
        MCPConfig(
            enabled=True,
            servers=[MCPServerConfig(name="fixture", transport="stdio", command=sys.executable)],
        ),
        bridge_factory=lambda _server, _timeout: BlockingBridge(),
    )
    provider.start_registration(ToolRegistry())
    assert entered.wait(timeout=1)
    registration_thread = provider._registration_thread
    assert registration_thread is not None
    waiter_result: list[bool] = []
    waiter = threading.Thread(target=lambda: waiter_result.append(provider.wait_registration(2)))
    waiter.start()
    provider.close()
    waiter.join(1)
    release.set()
    registration_thread.join(1)
    assert waiter_result == [False]
    assert not registration_thread.is_alive()


def test_mcp_status_boundary_is_exact_and_sanitized(tmp_path: Path) -> None:
    config_dir = tmp_path / "config"
    config_dir.mkdir(parents=True)
    (config_dir / "mcp.yaml").write_text(
        """
enabled: true
servers:
  fixture:
    transport: sse
    url: https://user:PRIVATE_TOKEN@example.invalid/sse
    headers:
      Authorization: PRIVATE_HEADER
""".strip(),
        encoding="utf-8",
    )
    provider = type(
        "Provider",
        (),
        {
            "status_snapshot": lambda _self: {
                "configState": "valid",
                "reasonCode": "READY",
                "servers": [
                    {
                        "serverId": "fixture",
                        "transport": "sse",
                        "enabled": True,
                        "state": "ready",
                        "reasonCode": "READY",
                        "toolCount": 2,
                    }
                ],
            }
        },
    )()
    session = type("Session", (), {"mcp_provider": provider})()
    boundary = MCPStatusBoundary(
        "generation-1",
        "c" * 32,
        tmp_path,
        session_provider=lambda: session,
    )

    snapshot = boundary.handle(_request("mcp.status.get", {}))
    assert snapshot["ok"] is True
    assert set(snapshot["payload"]) == {
        "schemaVersion",
        "configState",
        "reasonCode",
        "servers",
    }
    serialized = str(snapshot)
    assert "PRIVATE_TOKEN" not in serialized
    assert "PRIVATE_HEADER" not in serialized
    assert "Authorization" not in serialized

    invalid = boundary.handle(
        _request("mcp.status.get", {"headers": {"Authorization": "x"}})
    )
    assert invalid["ok"] is False
    assert invalid["error"]["code"] == "INVALID_REQUEST"
