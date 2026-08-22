from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

import yaml
import pytest

from app.config.provider_model_settings import ProviderModelSettingsError
from app.core.runtime_log import log_event
from app.core_host.__main__ import GuardedStdout
from app.core_host.provider_settings import ProviderSettingsBoundary
from app.llm.api_client import ApiRequestError, OpenAICompatibleClient


GENERATION = "generation-provider-settings"
CREDENTIAL = "0123456789abcdef0123456789abcdef"
SECRET = "BOUNDARY_SECRET_MUST_NOT_ESCAPE"


def _root(tmp_path: Path) -> Path:
    config = tmp_path / "data" / "config"
    config.mkdir(parents=True)
    (config / "system_config.yaml").write_text("config_version: 4\n", encoding="utf-8")
    (config / "api.yaml").write_text(
        yaml.safe_dump(
            {
                "api_profiles": [
                    {
                        "id": "fixture",
                        "alias": "Fixture",
                        "base_url": "https://fixture.invalid/v1",
                        "api_key": SECRET,
                        "models": [{"name": "fixture-model"}],
                    }
                ],
                "model_slots": {
                    "chat": {"profile_id": "fixture", "model": "fixture-model"}
                },
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    return tmp_path


def _request(request_id: str, name: str, payload: dict[str, object]) -> dict[str, object]:
    return {
        "protocolMajor": 2,
        "protocolMinor": 2,
        "kind": "request",
        "generationId": GENERATION,
        "generationCredential": CREDENTIAL,
        "id": request_id,
        "name": name,
        "payload": payload,
        "deadlineMs": 3000,
        "priority": "interactive",
    }


def _profile(operation_id: str) -> dict[str, object]:
    return {
        "operation_id": operation_id,
        "profile": {
            "profile_id": "fixture",
            "base_url": "https://fixture.invalid/v1",
            "model": "fixture-model",
            "timeout_seconds": 3,
            "credential": {"action": "keep", "value": ""},
        },
    }


def test_get_never_returns_saved_secret(tmp_path: Path) -> None:
    boundary = ProviderSettingsBoundary(GENERATION, CREDENTIAL, _root(tmp_path))
    boundary.enable()
    result = boundary.handle(_request("get", "settings.provider_model.get", {}))
    assert result["ok"] is True
    assert result["payload"]["providers"][0]["configured"] is True
    assert SECRET not in repr(result)


def test_dynamic_plugin_slots_are_sorted_validated_and_saved_by_owner(tmp_path: Path) -> None:
    class Worker:
        def __init__(self) -> None:
            self.active = True
            self.saved: list[tuple[str, dict[str, str]]] = []

        def model_slots(self):  # type: ignore[no-untyped-def]
            if not self.active:
                return []
            return [
                {
                    "identity": "plugin:com.example.summary:summary",
                    "ownerType": "plugin",
                    "ownerId": "com.example.summary",
                    "slotId": "summary",
                    "label": "Summary",
                    "description": "Summarize content.",
                    "modelKind": "chat_completion",
                    "required": True,
                    "order": 5,
                    "reasonCode": "READY",
                    "selection": {"profileId": "fixture", "model": "fixture-model"},
                }
            ]

        def model_slot_save(self, identity, selection):  # type: ignore[no-untyped-def]
            self.saved.append((identity, dict(selection)))
            return {"applicationState": "applied"}

    worker = Worker()
    session = type("Session", (), {"plugin_worker": worker})()
    boundary = ProviderSettingsBoundary(
        GENERATION,
        CREDENTIAL,
        _root(tmp_path),
        session_provider=lambda: session,
    )
    boundary.enable()

    current = boundary.handle(_request("get", "settings.provider_model.get", {}))
    assert current["ok"] is True
    assert current["payload"]["schema_version"] == 2
    assert [slot["identity"] for slot in current["payload"]["model_slots"]] == [
        "plugin:com.example.summary:summary",
        "core:chat",
        "core:vision_chat",
    ]
    assert SECRET not in repr(current)

    draft = {
        "providers": [
            {
                **current["payload"]["providers"][0],
                "credential": {"action": "keep", "value": ""},
            }
        ],
        "model_slots": {
            slot["identity"]: dict(slot["selection"])
            for slot in current["payload"]["model_slots"]
        },
        "settings": dict(current["payload"]["settings"]),
    }
    draft["model_slots"]["plugin:com.example.summary:summary"] = {
        "profile_id": "fixture",
        "model": "fixture-model",
    }
    draft["model_slots"]["core:chat"] = {
        "profile_id": "fixture",
        "model": "fixture-model",
    }
    draft["model_slots"]["core:vision_chat"] = {"profile_id": "", "model": ""}
    unchanged = boundary.handle(
        _request("save-unchanged", "settings.provider_model.save", {"draft": draft})
    )
    assert unchanged["ok"] is True
    assert worker.saved == []

    worker.active = False
    hidden = boundary.handle(_request("get-hidden", "settings.provider_model.get", {}))
    assert [slot["identity"] for slot in hidden["payload"]["model_slots"]] == [
        "core:chat",
        "core:vision_chat",
    ]


def test_dynamic_slot_validation_precedes_writes_and_partial_save_is_explicit(
    tmp_path: Path,
    monkeypatch,
) -> None:  # type: ignore[no-untyped-def]
    class Worker:
        def __init__(self) -> None:
            self.saved: list[str] = []

        def model_slots(self):  # type: ignore[no-untyped-def]
            return [
                {
                    "identity": f"plugin:com.example.{slot_id}:{slot_id}",
                    "ownerType": "plugin",
                    "ownerId": f"com.example.{slot_id}",
                    "slotId": slot_id,
                    "label": slot_id.title(),
                    "description": "Fixture slot.",
                    "modelKind": "chat_completion",
                    "required": slot_id == "first",
                    "order": order,
                    "reasonCode": "READY",
                    "selection": {"profileId": "", "model": ""},
                }
                for slot_id, order in (("first", 30), ("second", 40))
            ]

        def model_slot_save(self, identity, _selection):  # type: ignore[no-untyped-def]
            self.saved.append(identity)
            if identity.endswith(":second"):
                raise RuntimeError("fixture failure")
            return {"applicationState": "applied"}

    worker = Worker()
    session = type("Session", (), {"plugin_worker": worker})()
    boundary = ProviderSettingsBoundary(
        GENERATION,
        CREDENTIAL,
        _root(tmp_path),
        session_provider=lambda: session,
    )
    boundary.enable()
    current = boundary.handle(_request("get", "settings.provider_model.get", {}))["payload"]
    draft = {
        "providers": [
            {
                **current["providers"][0],
                "credential": {"action": "keep", "value": ""},
            }
        ],
        "model_slots": {
            slot["identity"]: dict(slot["selection"])
            for slot in current["model_slots"]
        },
        "settings": dict(current["settings"]),
    }
    writes = 0
    real_save = boundary._repository.save

    def count_save(raw):  # type: ignore[no-untyped-def]
        nonlocal writes
        writes += 1
        return real_save(raw)

    monkeypatch.setattr(boundary._repository, "save", count_save)
    missing = boundary.handle(
        _request("missing-required", "settings.provider_model.save", {"draft": draft})
    )
    assert missing["error"]["code"] == "MODEL_SLOT_REQUIRED"
    assert writes == 0
    assert worker.saved == []

    for identity in (
        "plugin:com.example.first:first",
        "plugin:com.example.second:second",
    ):
        draft["model_slots"][identity] = {
            "profile_id": "fixture",
            "model": "fixture-model",
        }
    core_phase = boundary.handle(
        _request("core-phase", "settings.provider_model.save_core", {"draft": draft})
    )
    assert core_phase["ok"] is True
    assert worker.saved == []
    pending = core_phase["payload"]["pending_plugin_slots"]
    assert list(pending) == [
        "plugin:com.example.first:first",
        "plugin:com.example.second:second",
    ]
    plugin_phase = boundary.handle(
        _request(
            "plugin-phase",
            "settings.provider_model.save_plugins",
            {"slots": pending},
        )
    )
    assert plugin_phase["ok"] is True
    assert plugin_phase["payload"]["save_state"] == "partial"
    assert plugin_phase["payload"]["saved_slots"] == [
        "plugin:com.example.first:first",
    ]
    assert plugin_phase["payload"]["failed_slot"]["identity"] == (
        "plugin:com.example.second:second"
    )
    assert plugin_phase["payload"]["failed_slot"]["ownerId"] == "com.example.second"
    assert plugin_phase["payload"]["failed_slot"]["reasonCode"] == (
        "MODEL_SLOT_SAVE_FAILED"
    )
    assert writes == 1


def test_plugin_slot_save_exception_is_reconciled_by_exact_ready_readback(
    tmp_path: Path,
    monkeypatch,
) -> None:  # type: ignore[no-untyped-def]
    identity = "plugin:com.example.memory:curation"

    class Worker:
        def __init__(self) -> None:
            self.calls = 0
            self.selection = {"profileId": "", "model": ""}

        def model_slots(self):  # type: ignore[no-untyped-def]
            return [
                {
                    "identity": identity,
                    "ownerType": "plugin",
                    "ownerId": "com.example.memory",
                    "slotId": "curation",
                    "label": "Curation",
                    "description": "Fixture slot.",
                    "modelKind": "chat_completion",
                    "required": False,
                    "order": 30,
                    "reasonCode": "READY",
                    "selection": dict(self.selection),
                }
            ]

        def model_slot_save(self, saved_identity, selection):  # type: ignore[no-untyped-def]
            self.calls += 1
            assert saved_identity == identity
            self.selection = dict(selection)
            raise RuntimeError(f"callback failed after write: {SECRET}")

    worker = Worker()
    records: list[dict[str, object]] = []

    def capture_log(_channel, _message, attributes, **kwargs):  # type: ignore[no-untyped-def]
        records.append({"attributes": dict(attributes), **kwargs})

    monkeypatch.setattr("app.core.runtime_log.external_runtime_sink_active", lambda: True)
    monkeypatch.setattr("app.core.runtime_log.log_event", capture_log)
    boundary = ProviderSettingsBoundary(
        GENERATION,
        CREDENTIAL,
        _root(tmp_path),
        plugin_application_provider=lambda: worker,
    )
    boundary.enable()
    result = boundary.handle(
        _request(
            "save-reconciled",
            "settings.provider_model.save_plugins",
            {
                "slots": {
                    identity: {
                        "profile_id": "fixture",
                        "model": "fixture-model",
                    }
                }
            },
        )
    )

    assert result["ok"] is True
    assert result["payload"]["save_state"] == "complete"
    assert result["payload"]["saved_slots"] == [identity]
    assert result["payload"]["failed_slot"] is None
    assert worker.calls == 1
    assert records == [
        {
            "attributes": {
                "name": identity,
                "reason_code": "MODEL_SLOT_SAVE_RECONCILED",
                "diagnostic": "MODEL_SLOT_SAVE_FAILED",
            },
            "event": "settings.provider_model.slot_save_reconciled",
            "severity": "warning",
            "verbosity": 0,
        }
    ]
    assert SECRET not in repr(result)
    assert SECRET not in repr(records)


def test_plugin_slot_save_exception_without_matching_readback_remains_partial(
    tmp_path: Path,
    monkeypatch,
) -> None:  # type: ignore[no-untyped-def]
    identity = "plugin:com.example.memory:curation"

    class Worker:
        def __init__(self) -> None:
            self.calls = 0

        def model_slots(self):  # type: ignore[no-untyped-def]
            return [
                {
                    "identity": identity,
                    "ownerType": "plugin",
                    "ownerId": "com.example.memory",
                    "slotId": "curation",
                    "label": "Curation",
                    "description": "Fixture slot.",
                    "modelKind": "chat_completion",
                    "required": False,
                    "order": 30,
                    "reasonCode": "READY",
                    "selection": {"profileId": "", "model": ""},
                }
            ]

        def model_slot_save(self, _identity, _selection):  # type: ignore[no-untyped-def]
            self.calls += 1
            raise RuntimeError(f"private failure: {SECRET}")

    worker = Worker()
    records: list[dict[str, object]] = []

    def capture_log(_channel, _message, attributes, **kwargs):  # type: ignore[no-untyped-def]
        records.append({"attributes": dict(attributes), **kwargs})

    monkeypatch.setattr("app.core.runtime_log.external_runtime_sink_active", lambda: True)
    monkeypatch.setattr("app.core.runtime_log.log_event", capture_log)
    boundary = ProviderSettingsBoundary(
        GENERATION,
        CREDENTIAL,
        _root(tmp_path),
        plugin_application_provider=lambda: worker,
    )
    boundary.enable()
    result = boundary.handle(
        _request(
            "save-partial",
            "settings.provider_model.save_plugins",
            {
                "slots": {
                    identity: {
                        "profile_id": "fixture",
                        "model": "fixture-model",
                    }
                }
            },
        )
    )

    assert result["ok"] is True
    assert result["payload"]["save_state"] == "partial"
    assert result["payload"]["saved_slots"] == []
    assert result["payload"]["failed_slot"]["reasonCode"] == (
        "MODEL_SLOT_SAVE_FAILED"
    )
    assert worker.calls == 1
    assert records == [
        {
            "attributes": {
                "name": identity,
                "reason_code": "MODEL_SLOT_SAVE_FAILED",
            },
            "event": "settings.provider_model.slot_save_failed",
            "severity": "warning",
            "verbosity": 0,
        }
    ]
    assert SECRET not in repr(result)
    assert SECRET not in repr(records)


def test_generation_identity_mismatch_fails_closed(tmp_path: Path) -> None:
    boundary = ProviderSettingsBoundary(GENERATION, CREDENTIAL, _root(tmp_path))
    boundary.enable()
    invalid = _request("get", "settings.provider_model.get", {})
    invalid["generationCredential"] = "ff" * 16
    with pytest.raises(RuntimeError, match="GENERATION_IDENTITY_MISMATCH"):
        boundary.handle(invalid)


def test_probe_errors_are_stable_and_redacted(
    tmp_path: Path,
    monkeypatch,
) -> None:  # type: ignore[no-untyped-def]
    boundary = ProviderSettingsBoundary(GENERATION, CREDENTIAL, _root(tmp_path))
    boundary.enable()

    def fail(_self: OpenAICompatibleClient, **_kwargs: object) -> list[str]:
        raise ApiRequestError(f"401 response echoed {SECRET}")

    monkeypatch.setattr(OpenAICompatibleClient, "list_models", fail)
    result = boundary.handle(
        _request("probe", "settings.provider_model.list_models", _profile("probe"))
    )
    assert result["ok"] is False
    assert result["error"]["code"] == "AUTHENTICATION_FAILED"
    assert SECRET not in repr(result)


@pytest.mark.parametrize(
    ("name", "method", "result"),
    [
        ("settings.provider_model.list_models", "list_models", ["fixture-model"]),
        ("settings.provider_model.test_connection", "test_connection", "OK"),
    ],
)
def test_probe_suppresses_runtime_logs_reserved_from_core_stdout(
    tmp_path: Path,
    monkeypatch,
    name: str,
    method: str,
    result: object,
) -> None:  # type: ignore[no-untyped-def]
    boundary = ProviderSettingsBoundary(GENERATION, CREDENTIAL, _root(tmp_path))
    boundary.enable()

    def noisy_probe(_self: OpenAICompatibleClient, **_kwargs: object) -> object:
        log_event("API", "HTTP 请求成功", {"status": 200})
        return result

    monkeypatch.setattr(OpenAICompatibleClient, method, noisy_probe)
    monkeypatch.setattr(sys, "stdout", GuardedStdout())
    operation_id = f"guarded-{method}"
    profile = _profile(operation_id)
    if method == "test_connection":
        profile["profile"]["model"] = "fixture-model"  # type: ignore[index]
    response = boundary.handle(_request(operation_id, name, profile))

    assert response["ok"] is True


def test_probe_timeout_and_save_failure_have_stable_codes(
    tmp_path: Path,
    monkeypatch,
) -> None:  # type: ignore[no-untyped-def]
    boundary = ProviderSettingsBoundary(GENERATION, CREDENTIAL, _root(tmp_path))
    boundary.enable()

    def timeout(_self: OpenAICompatibleClient, **_kwargs: object) -> list[str]:
        raise ApiRequestError(f"timed out with private value {SECRET}")

    monkeypatch.setattr(OpenAICompatibleClient, "list_models", timeout)
    timed_out = boundary.handle(
        _request("timeout", "settings.provider_model.list_models", _profile("timeout"))
    )
    assert timed_out["error"]["code"] == "PROVIDER_TIMEOUT"
    assert SECRET not in repr(timed_out)

    def fail_save(_raw: object) -> dict[str, object]:
        raise ProviderModelSettingsError("CONFIG_SAVE_FAILED", "配置保存失败，原文件保持不变。")

    monkeypatch.setattr(boundary._repository, "save", fail_save)
    failed = boundary.handle(
        _request("save", "settings.provider_model.save", {"draft": {}})
    )
    assert failed["error"]["code"] == "CONFIG_SAVE_FAILED"


def test_network_probe_has_one_cancelled_terminal(
    tmp_path: Path,
    monkeypatch,
) -> None:  # type: ignore[no-untyped-def]
    boundary = ProviderSettingsBoundary(GENERATION, CREDENTIAL, _root(tmp_path))
    boundary.enable()
    entered = threading.Event()

    def block(
        _self: OpenAICompatibleClient,
        *,
        cancel_checker,
    ) -> list[str]:  # type: ignore[no-untyped-def]
        entered.set()
        while True:
            cancel_checker()
            time.sleep(0.01)

    monkeypatch.setattr(OpenAICompatibleClient, "list_models", block)
    results: list[dict[str, object]] = []
    thread = threading.Thread(
        target=lambda: results.append(
            boundary.handle(
                _request("cancel-me", "settings.provider_model.list_models", _profile("cancel-me"))
            )
        )
    )
    thread.start()
    assert entered.wait(1)
    assert boundary.cancel("cancel-me") is True
    thread.join(1)
    assert not thread.is_alive()
    assert len(results) == 1
    assert results[0]["ok"] is False
    assert results[0]["error"]["code"] == "OPERATION_CANCELLED"
    assert boundary.cancel("cancel-me") is False


def test_close_cancels_an_active_probe_once(
    tmp_path: Path,
    monkeypatch,
) -> None:  # type: ignore[no-untyped-def]
    boundary = ProviderSettingsBoundary(GENERATION, CREDENTIAL, _root(tmp_path))
    boundary.enable()
    entered = threading.Event()

    def block(
        _self: OpenAICompatibleClient,
        *,
        cancel_checker,
    ) -> list[str]:  # type: ignore[no-untyped-def]
        entered.set()
        while True:
            cancel_checker()
            time.sleep(0.01)

    monkeypatch.setattr(OpenAICompatibleClient, "list_models", block)
    results: list[dict[str, object]] = []
    thread = threading.Thread(
        target=lambda: results.append(
            boundary.handle(
                _request("close-me", "settings.provider_model.list_models", _profile("close-me"))
            )
        )
    )
    thread.start()
    assert entered.wait(1)
    boundary.close()
    thread.join(1)
    assert not thread.is_alive()
    assert len(results) == 1
    assert results[0]["error"]["code"] == "OPERATION_CANCELLED"
    assert boundary.cancel("close-me") is False


def test_repeated_saves_are_serialized(
    tmp_path: Path,
    monkeypatch,
) -> None:  # type: ignore[no-untyped-def]
    boundary = ProviderSettingsBoundary(GENERATION, CREDENTIAL, _root(tmp_path))
    boundary.enable()
    original = boundary._repository.save
    first_entered = threading.Event()
    release_first = threading.Event()
    call_count = 0
    count_lock = threading.Lock()

    def controlled_save(raw: object) -> dict[str, object]:
        nonlocal call_count
        with count_lock:
            call_count += 1
            number = call_count
        if number == 1:
            first_entered.set()
            assert release_first.wait(1)
        return original(raw)

    monkeypatch.setattr(boundary._repository, "save", controlled_save)
    current = boundary.handle(_request("get-before-save", "settings.provider_model.get", {}))[
        "payload"
    ]
    draft = {
        "providers": [
            {
                **current["providers"][0],
                "credential": {"action": "keep", "value": ""},
            }
        ],
        "model_slots": {
            "chat": {"profile_id": "fixture", "model": "fixture-model"},
            "vision_chat": {"profile_id": "", "model": ""},
        },
        "settings": {
            "timeout_seconds": 60,
            "temperature": None,
            "top_p": None,
            "max_tokens": None,
        },
    }
    results: list[dict[str, object]] = []
    first = threading.Thread(
        target=lambda: results.append(
            boundary.handle(_request("save-1", "settings.provider_model.save", {"draft": draft}))
        )
    )
    second = threading.Thread(
        target=lambda: results.append(
            boundary.handle(_request("save-2", "settings.provider_model.save", {"draft": draft}))
        )
    )
    first.start()
    assert first_entered.wait(1)
    second.start()
    time.sleep(0.05)
    assert call_count == 1
    release_first.set()
    first.join(1)
    second.join(1)
    assert not first.is_alive() and not second.is_alive()
    assert call_count == 2
    assert all(result["ok"] is True for result in results)
