from __future__ import annotations

import threading
import time
from types import SimpleNamespace
from pathlib import Path

from app.agent.actions import AgentAction, PendingToolAction
from app.agent.tools import Tool, ToolRegistry
from app.core_host.real_chat import RealChatBoundary
from app.core_host.tools import ToolActionCoordinator
from app.llm.chat_reply import ChatReply, ChatSegment
from tests.integration.test_core_host_real_chat_integration import (
    CAPABILITIES,
    _configure_app_root,
    _exchange,
    _request as _host_request,
    _start_host,
    _start_provider,
    _stop,
    _stop_provider,
)


def _reply(text: str) -> ChatReply:
    return ChatReply([ChatSegment(text=text, translation=text, tone="中性", portrait="站立待机")])


class Pipeline:
    def __init__(self) -> None:
        self.executed: list[tuple[str, dict[str, object]]] = []

    def run_user_message(self, _messages, **_kwargs):  # type: ignore[no-untyped-def]
        action = PendingToolAction(
            "fixture_change",
            {"value": "immutable-value"},
            "",
            id="b" * 32,
        )
        return SimpleNamespace(
            reply=_reply("等待确认"),
            actions=[AgentAction("pending_action", action.to_dict(include_context=True))],
        )

    def run_confirmed_action(self, action, **_kwargs):  # type: ignore[no-untyped-def]
        self.executed.append((action.tool_name, dict(action.arguments)))
        return SimpleNamespace(reply=_reply("已经按原参数执行"), actions=[])

    def run_cancelled_action(self, _action, **_kwargs):  # type: ignore[no-untyped-def]
        return SimpleNamespace(reply=_reply("已取消"), actions=[])


class History:
    def __init__(self, *_args) -> None:  # type: ignore[no-untyped-def]
        self.entries: list[tuple[object, ...]] = []

    def assert_compatible_append(self) -> None:
        return None

    def load_recent(self, _limit: int):  # type: ignore[no-untyped-def]
        return []

    def append(self, *values: object) -> None:
        self.entries.append(values)


def _confirmation_registry() -> ToolRegistry:
    return ToolRegistry(
        [
            Tool(
                name="fixture_change",
                description="修改集成测试 fixture。",
                parameters={"type": "object", "properties": {}},
                handler=lambda arguments: dict(arguments),
                requires_confirmation=True,
                group="plugin",
                risk="high",
                source="plugin",
            )
        ]
    )


def _request(name: str, request_id: str, payload: dict[str, object]) -> dict[str, object]:
    return {
        "protocolMajor": 2,
        "protocolMinor": 2,
        "kind": "request",
        "generationId": "generation-1",
        "generationCredential": "c" * 32,
        "id": request_id,
        "name": name,
        "deadlineMs": 30_000,
        "priority": "interactive" if name == "chat.send" else "control",
        "payload": payload,
    }


def test_real_chat_action_id_journey_executes_only_core_stored_parameters(tmp_path) -> None:
    pipeline = Pipeline()
    registry = _confirmation_registry()
    coordinator = ToolActionCoordinator(
        "generation-1", ttl_seconds=1, tool_lookup=registry.get
    )
    session = SimpleNamespace(
        character=SimpleNamespace(id="sakura", display_name="Sakura"),
        pipeline=pipeline,
        tool_actions=coordinator,
        memory_boundary=None,
    )
    events: list[dict[str, object]] = []
    boundary = RealChatBoundary(
        "generation-1",
        "c" * 32,
        tmp_path,
        session_provider=lambda: session,
        event_publisher=events.append,
        history_factory=History,
    )
    send = _request("chat.send", "chat-1", {"message": "忘掉它", "operationId": "chat-1"})
    boundary.reserve_send(send)
    result: list[dict[str, object]] = []
    worker = threading.Thread(target=lambda: result.append(boundary.handle_send(send)))
    worker.start()

    for _ in range(200):
        if any(event.get("name") == "tool.confirmation.requested" for event in events):
            break
        time.sleep(0.005)
    confirmation = next(
        event for event in events if event.get("name") == "tool.confirmation.requested"
    )
    assert confirmation["payload"] == {
        **confirmation["payload"],  # type: ignore[dict-item]
        "actionId": "b" * 32,
    }
    assert "arguments" not in confirmation["payload"]  # type: ignore[operator]

    decision = boundary.handle_tool_decision(
        _request("tool.confirm", "confirm-1", {"actionId": "b" * 32}),
        confirm=True,
    )
    assert decision["payload"]["accepted"] is True  # type: ignore[index]
    worker.join(timeout=2)

    assert pipeline.executed == [
        ("fixture_change", {"value": "immutable-value"})
    ]
    assert [event["name"] for event in events] == [
        "chat.started",
        "tool.confirmation.requested",
        "chat.completed",
    ]
    assert len(result) == 1
    boundary.close()


def test_reject_action_completes_chat_without_execution(tmp_path) -> None:
    pipeline = Pipeline()
    registry = _confirmation_registry()
    coordinator = ToolActionCoordinator(
        "generation-1", ttl_seconds=1, tool_lookup=registry.get
    )
    session = SimpleNamespace(
        character=SimpleNamespace(id="sakura", display_name="Sakura"),
        pipeline=pipeline,
        tool_actions=coordinator,
        memory_boundary=None,
    )
    events: list[dict[str, object]] = []
    boundary = RealChatBoundary(
        "generation-1",
        "c" * 32,
        tmp_path,
        session_provider=lambda: session,
        event_publisher=events.append,
        history_factory=History,
    )
    send = _request("chat.send", "chat-2", {"message": "忘掉它", "operationId": "chat-2"})
    boundary.reserve_send(send)
    worker = threading.Thread(target=lambda: boundary.handle_send(send))
    worker.start()
    for _ in range(200):
        if coordinator.pending_count() == 1:
            break
        time.sleep(0.005)
    boundary.handle_tool_decision(
        _request("tool.reject", "reject-1", {"actionId": "b" * 32}),
        confirm=False,
    )
    worker.join(timeout=2)

    assert pipeline.executed == []
    assert events[-1]["name"] == "chat.completed"
    boundary.close()


def test_tools_settings_capability_is_negotiated_and_unknown_fields_fail_closed(
    tmp_path: Path,
) -> None:
    provider, provider_thread = _start_provider("complete")
    app_root = _configure_app_root(tmp_path, provider.server_address[1])
    process = _start_host(app_root)
    try:
        hello = _host_request(
            "tools-hello",
            "system.hello",
            {
                "protocol": {"major": 2, "minMinor": 2, "maxMinor": 2},
                "requiredCapabilities": CAPABILITIES,
                "optionalCapabilities": [
                    "transport.concurrent-router",
                    "assistant.tools-v1",
                ],
            },
        )
        negotiated = _exchange(process, hello)
        assert "assistant.tools-v1" in negotiated["payload"]["capabilities"]
        _exchange(process, _host_request("tools-initialize", "core.initialize", {}))

        snapshot = _exchange(
            process, _host_request("tools-get", "tools.settings.get", {})
        )
        assert snapshot["ok"] is True
        assert set(snapshot["payload"]) == {
            "schemaVersion",
            "runtimeLimits",
            "confirmationPolicy",
        }
        invalid = _exchange(
            process,
            _host_request(
                "tools-invalid-save",
                "tools.settings.save",
                {
                    "settings": _tool_settings_fixture(),
                    "arguments": {"forged": True},
                },
            ),
        )
        assert invalid["ok"] is False
        assert invalid["error"]["code"] == "INVALID_REQUEST"
        _exchange(process, _host_request("tools-shutdown", "system.shutdown", {}))
        process.wait(timeout=5)
        assert process.returncode == 0
    finally:
        _stop(process)
        _stop_provider(provider, provider_thread)


def test_tools_settings_request_without_capability_is_denied(tmp_path: Path) -> None:
    provider, provider_thread = _start_provider("complete")
    app_root = _configure_app_root(tmp_path, provider.server_address[1])
    process = _start_host(app_root)
    try:
        _exchange(
            process,
            _host_request(
                "plain-hello",
                "system.hello",
                {
                    "protocol": {"major": 2, "minMinor": 2, "maxMinor": 2},
                    "requiredCapabilities": CAPABILITIES,
                    "optionalCapabilities": ["transport.concurrent-router"],
                },
            ),
        )
        _exchange(process, _host_request("plain-initialize", "core.initialize", {}))
        denied = _exchange(
            process, _host_request("denied-tools", "tools.settings.get", {})
        )
        assert denied["ok"] is False
        assert denied["error"]["code"] == "CAPABILITY_NEGOTIATION_FAILED"
        _exchange(process, _host_request("plain-shutdown", "system.shutdown", {}))
        process.wait(timeout=5)
        assert process.returncode == 0
    finally:
        _stop(process)
        _stop_provider(provider, provider_thread)


def _tool_settings_fixture() -> dict[str, object]:
    return {
        "runtimeLimits": {
            "maxAgentStepsPerTurn": 4,
            "maxToolCallsPerStep": 3,
            "maxToolCallsPerTurn": 8,
        },
        "confirmationPolicy": "risk_based",
    }
