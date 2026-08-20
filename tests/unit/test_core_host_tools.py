from __future__ import annotations

import threading
import time
from types import SimpleNamespace

import pytest

from app.agent.actions import AgentAction, PendingToolAction
from app.agent.tools import Tool, ToolExecutionResult, ToolRegistry
from app.core_host.tools import (
    ToolActionCoordinator,
    ToolActionError,
    create_runtime_v2_tool_registry,
    pending_actions_from_result,
)
from app.core_host.tool_settings import ToolSettingsBoundary, load_tool_runtime_configuration


def _action(name: str = "fixture_change") -> PendingToolAction:
    arguments = {"value": "changed"}
    return PendingToolAction(name, arguments, "", id="a" * 32)


def _confirmation_registry() -> ToolRegistry:
    return ToolRegistry(
        [
            Tool(
                name="fixture_change",
                description="修改 fixture",
                parameters={"type": "object", "properties": {}},
                handler=lambda arguments: dict(arguments),
                requires_confirmation=True,
                group="plugin",
                risk="high",
                source="plugin",
            )
        ]
    )


def test_runtime_v2_registry_contains_only_frozen_tools() -> None:
    registry = create_runtime_v2_tool_registry()

    assert {tool.name for tool in registry.all()} == {"get_current_time"}
    assert "add_todo" not in {tool.name for tool in registry.all()}
    assert "observe_screen" not in {tool.name for tool in registry.all()}


def test_core_read_only_tool_executes_without_confirmation() -> None:
    registry = create_runtime_v2_tool_registry()
    current_time = registry.prepare_or_execute("get_current_time", {})

    assert isinstance(current_time, ToolExecutionResult) and current_time.success is True


def test_legacy_confirm_writes_setting_is_dormant_in_assistant_mode() -> None:
    registry = create_runtime_v2_tool_registry(confirm_writes=True)

    assert isinstance(
        registry.prepare_or_execute("get_current_time", {}),
        ToolExecutionResult,
    )


def test_action_id_decision_is_one_shot_and_parameters_stay_in_core() -> None:
    registry = _confirmation_registry()
    coordinator = ToolActionCoordinator(
        "generation-1", ttl_seconds=1, tool_lookup=registry.get
    )
    action = _action()
    published: list[dict[str, object]] = []
    result: list[str] = []

    worker = threading.Thread(
        target=lambda: result.append(
            coordinator.await_decision(
                action,
                operation_id="chat-1",
                publish=published.append,
                cancel_checker=lambda: None,
            )
        )
    )
    worker.start()
    for _ in range(100):
        if published:
            break
        time.sleep(0.005)

    assert published == [
        {
            "actionId": "a" * 32,
            "title": "执行插件工具",
            "summary": "执行外部工具 fixture_change",
            "risk": "destructive",
            "expiresAt": published[0]["expiresAt"],
        }
    ]
    assert "arguments" not in published[0]
    assert coordinator.decide("a" * 32, confirm=True)["accepted"] is True
    assert coordinator.decide("a" * 32, confirm=True)["accepted"] is False
    worker.join(timeout=1)

    assert result == ["confirm"]
    assert coordinator.pending_count() == 0


def test_action_reject_and_expiry_never_confirm() -> None:
    registry = _confirmation_registry()
    rejected = ToolActionCoordinator(
        "generation-1", ttl_seconds=1, tool_lookup=registry.get
    )
    rejection: list[str] = []
    published = threading.Event()
    worker = threading.Thread(
        target=lambda: rejection.append(
            rejected.await_decision(
                _action(),
                operation_id="chat-1",
                publish=lambda _payload: published.set(),
                cancel_checker=lambda: None,
            )
        )
    )
    worker.start()
    assert published.wait(1)
    assert rejected.decide("a" * 32, confirm=False)["accepted"] is True
    worker.join(timeout=1)
    assert rejection == ["reject"]

    expired = ToolActionCoordinator(
        "generation-1", ttl_seconds=0.02, tool_lookup=registry.get
    )
    assert (
        expired.await_decision(
            _action(),
            operation_id="chat-1",
            publish=lambda _payload: None,
            cancel_checker=lambda: None,
        )
        == "expired"
    )
    assert expired.decide("a" * 32, confirm=True)["accepted"] is False


def test_close_cancels_waiter_and_invalid_ids_fail_closed() -> None:
    registry = _confirmation_registry()
    coordinator = ToolActionCoordinator("generation-1", tool_lookup=registry.get)
    published = threading.Event()
    errors: list[BaseException] = []

    def wait() -> None:
        try:
            coordinator.await_decision(
                _action(),
                operation_id="chat-1",
                publish=lambda _payload: published.set(),
                cancel_checker=lambda: None,
            )
        except BaseException as error:  # noqa: BLE001 - test records the terminal
            errors.append(error)

    worker = threading.Thread(target=wait)
    worker.start()
    assert published.wait(1)
    coordinator.close()
    worker.join(timeout=1)

    assert errors and errors[0].__class__.__name__ == "OperationCancelled"
    assert coordinator.pending_count() == 0
    with pytest.raises(ToolActionError, match="工具确认标识无效"):
        coordinator.decide("short", confirm=True)


def test_pending_action_projection_rejects_private_or_malformed_actions() -> None:
    action = _action()
    result = SimpleNamespace(
        actions=[AgentAction("pending_action", action.to_dict(include_context=True))]
    )
    assert pending_actions_from_result(result) == (action,)

    malformed = SimpleNamespace(actions=[AgentAction("pending_action", {"id": "missing"})])
    with pytest.raises(ToolActionError, match="工具确认请求无效"):
        pending_actions_from_result(malformed)


def _settings_request(
    name: str,
    payload: dict[str, object],
    *,
    generation_id: str = "generation-1",
    generation_credential: str = "c" * 32,
) -> dict[str, object]:
    return {
        "protocolMajor": 2,
        "protocolMinor": 2,
        "kind": "request",
        "generationId": generation_id,
        "generationCredential": generation_credential,
        "id": f"request-{name}",
        "name": name,
        "payload": payload,
        "deadlineMs": 3_000,
        "priority": "interactive",
    }


def _tool_settings() -> dict[str, object]:
    return {
        "runtimeLimits": {
            "maxAgentStepsPerTurn": 6,
            "maxToolCallsPerStep": 4,
            "maxToolCallsPerTurn": 9,
        },
        "confirmationPolicy": "confirm_writes",
    }


def test_tool_settings_defaults_and_compatibility_mapping_preserve_unknown_fields(tmp_path) -> None:
    boundary = ToolSettingsBoundary("generation-1", "c" * 32, tmp_path)
    default = boundary.handle(_settings_request("tools.settings.get", {}))
    assert default["ok"] is True
    assert default["payload"] == {
        "schemaVersion": 1,
        "runtimeLimits": {
            "maxAgentStepsPerTurn": 4,
            "maxToolCallsPerStep": 3,
            "maxToolCallsPerTurn": 8,
        },
        "confirmationPolicy": "risk_based",
    }

    config = tmp_path / "data" / "config" / "system_config.yaml"
    config.parent.mkdir(parents=True)
    config.write_text(
        "config_version: 4\nunknown_root:\n  keep: true\ntool_loop:\n  unknown_limit: 17\nui:\n  keep_ui: yes\n",
        encoding="utf-8",
    )
    saved = boundary.handle(
        _settings_request("tools.settings.save", {"settings": _tool_settings()})
    )
    assert saved["ok"] is True
    assert saved["payload"]["changePlan"] == "core_restart_required"
    text = config.read_text(encoding="utf-8")
    assert "unknown_root:" in text and "keep: true" in text
    assert "unknown_limit: 17" in text and "keep_ui: true" in text
    assert "free_access_enabled: false" in text

    snapshot = boundary.handle(_settings_request("tools.settings.get", {}))["payload"]
    assert snapshot["confirmationPolicy"] == "confirm_writes"
    assert snapshot["runtimeLimits"] == _tool_settings()["runtimeLimits"]


@pytest.mark.parametrize(
    "settings,field",
    [
        (
            {
                **_tool_settings(),
                "runtimeLimits": {
                    "maxAgentStepsPerTurn": 0,
                    "maxToolCallsPerStep": 4,
                    "maxToolCallsPerTurn": 9,
                },
            },
            "maxAgentStepsPerTurn",
        ),
        (
            {
                **_tool_settings(),
                "runtimeLimits": {
                    "maxAgentStepsPerTurn": 6,
                    "maxToolCallsPerStep": 11,
                    "maxToolCallsPerTurn": 11,
                },
            },
            "runtimeLimits",
        ),
        (
            {
                **_tool_settings(),
                "runtimeLimits": {
                    "maxAgentStepsPerTurn": 6,
                    "maxToolCallsPerStep": 5,
                    "maxToolCallsPerTurn": 4,
                },
            },
            "runtimeLimits",
        ),
        ({**_tool_settings(), "confirmationPolicy": "always_allow"}, "confirmationPolicy"),
    ],
)
def test_tool_settings_reject_invalid_bounds_and_policy(
    tmp_path, settings: dict[str, object], field: str
) -> None:
    boundary = ToolSettingsBoundary("generation-1", "c" * 32, tmp_path)
    result = boundary.handle(
        _settings_request("tools.settings.save", {"settings": settings})
    )
    assert result["ok"] is False
    assert result["error"]["code"] == "FIELD_INVALID"
    assert result["error"]["details"]["field"] == field
    assert not (tmp_path / "data" / "config" / "system_config.yaml").exists()


@pytest.mark.parametrize(
    ("content", "code"),
    [
        ("config_version: [broken", "CONFIG_READ_ONLY"),
        ("config_version: 5\n", "CONFIG_FUTURE_SCHEMA"),
    ],
)
def test_tool_settings_damaged_or_future_schema_is_read_only(
    tmp_path, content: str, code: str
) -> None:
    config = tmp_path / "data" / "config" / "system_config.yaml"
    config.parent.mkdir(parents=True)
    config.write_text(content, encoding="utf-8")
    boundary = ToolSettingsBoundary("generation-1", "c" * 32, tmp_path)

    for name, payload in (
        ("tools.settings.get", {}),
        ("tools.settings.save", {"settings": _tool_settings()}),
    ):
        result = boundary.handle(_settings_request(name, payload))
        assert result["ok"] is False
        assert result["error"]["code"] == code
    assert config.read_text(encoding="utf-8") == content


def test_tool_settings_atomic_failure_keeps_previous_file(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import app.core_host.tool_settings as tool_settings_module

    config = tmp_path / "data" / "config" / "system_config.yaml"
    config.parent.mkdir(parents=True)
    original = "config_version: 4\nui:\n  free_access_enabled: true\n"
    config.write_text(original, encoding="utf-8")
    monkeypatch.setattr(
        tool_settings_module,
        "atomic_write_text",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("disk full")),
    )
    boundary = ToolSettingsBoundary("generation-1", "c" * 32, tmp_path)
    result = boundary.handle(
        _settings_request("tools.settings.save", {"settings": _tool_settings()})
    )

    assert result["ok"] is False
    assert result["error"]["code"] == "CONFIG_SAVE_FAILED"
    assert result["error"]["retryable"] is True
    assert config.read_text(encoding="utf-8") == original


def test_tool_settings_requires_exact_generation_identity(tmp_path) -> None:
    boundary = ToolSettingsBoundary("generation-1", "c" * 32, tmp_path)
    with pytest.raises(RuntimeError, match="GENERATION_IDENTITY_MISMATCH"):
        boundary.handle(
            _settings_request(
                "tools.settings.get", {}, generation_credential="d" * 32
            )
        )
    with pytest.raises(RuntimeError, match="GENERATION_IDENTITY_MISMATCH"):
        boundary.handle(
            _settings_request("tools.settings.get", {}, generation_id="generation-old")
        )


def test_saved_tool_settings_feed_the_next_core_generation(tmp_path) -> None:
    boundary = ToolSettingsBoundary("generation-1", "c" * 32, tmp_path)
    result = boundary.handle(
        _settings_request("tools.settings.save", {"settings": _tool_settings()})
    )
    assert result["ok"] is True

    limits, confirm_writes = load_tool_runtime_configuration(tmp_path)
    assert (
        limits.max_agent_steps_per_turn,
        limits.max_tool_calls_per_step,
        limits.max_tool_calls_per_turn,
    ) == (6, 4, 9)
    assert confirm_writes is True
