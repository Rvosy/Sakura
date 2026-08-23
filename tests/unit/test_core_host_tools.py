from __future__ import annotations

import pytest

from app.agent.tools import ToolExecutionResult
from app.core_host.tools import create_runtime_v2_tool_registry
from app.core_host.tool_settings import ToolSettingsBoundary, load_tool_runtime_configuration


def test_runtime_v2_registry_contains_only_frozen_tools() -> None:
    registry = create_runtime_v2_tool_registry()

    assert {tool.name for tool in registry.all()} == {"get_current_time"}
    assert "add_todo" not in {tool.name for tool in registry.all()}
    assert "observe_screen" not in {tool.name for tool in registry.all()}


def test_core_read_only_tool_executes_without_confirmation() -> None:
    registry = create_runtime_v2_tool_registry()
    current_time = registry.prepare_or_execute("get_current_time", {})

    assert isinstance(current_time, ToolExecutionResult) and current_time.success is True


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
    }


def test_tool_settings_defaults_and_save_preserve_unknown_fields(tmp_path) -> None:
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
    assert saved["payload"]["changePlan"] == "applied"
    text = config.read_text(encoding="utf-8")
    assert "unknown_root:" in text and "keep: true" in text
    assert "unknown_limit: 17" in text and "keep_ui: true" in text

    snapshot = boundary.handle(_settings_request("tools.settings.get", {}))["payload"]
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
        ({**_tool_settings(), "unknown": True}, None),
    ],
)
def test_tool_settings_reject_invalid_bounds_and_fields(
    tmp_path, settings: dict[str, object], field: str | None
) -> None:
    boundary = ToolSettingsBoundary("generation-1", "c" * 32, tmp_path)
    result = boundary.handle(
        _settings_request("tools.settings.save", {"settings": settings})
    )
    assert result["ok"] is False
    assert result["error"]["code"] == ("FIELD_INVALID" if field else "INVALID_REQUEST")
    assert result["error"]["details"]["field"] == (field or "")
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

    limits = load_tool_runtime_configuration(tmp_path)
    assert (
        limits.max_agent_steps_per_turn,
        limits.max_tool_calls_per_step,
        limits.max_tool_calls_per_turn,
    ) == (6, 4, 9)
