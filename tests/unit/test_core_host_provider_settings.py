from __future__ import annotations

import io
import json
import shutil
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import yaml
import pytest

from app.config.provider_model_settings import ProviderModelSettingsError
from app.core.runtime_log import log_event
from app.core_host.__main__ import GuardedStdout
from app.core_host.provider_settings import ProviderSettingsBoundary
from app.plugin_sdk.sakura_model import ApiRequestError, ModelProbe
from app.storage.runtime_roots import RuntimeRoots


GENERATION = "generation-provider-settings"
CREDENTIAL = "0123456789abcdef0123456789abcdef"
SECRET = "BOUNDARY_SECRET_MUST_NOT_ESCAPE"


def _root(tmp_path: Path) -> Path:
    config = tmp_path / "config"
    config.mkdir(parents=True)
    (config / "system_config.yaml").write_text("config_version: 1\n", encoding="utf-8")
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


def test_save_reports_persisted_configuration_when_runtime_apply_fails(tmp_path: Path) -> None:
    calls = []

    def fail_apply():
        calls.append("apply")
        raise OSError("fixture runtime publication failed")

    boundary = ProviderSettingsBoundary(
        GENERATION, CREDENTIAL, _root(tmp_path), runtime_apply=fail_apply,
    )
    boundary.enable()
    current = boundary.handle(_request("get", "settings.provider_model.get", {}))["payload"]
    draft = {
        "providers": [{**current["providers"][0], "alias": "Saved alias",
                       "credential": {"action": "keep", "value": ""}}],
        "model_slots": {slot["identity"]: dict(slot["selection"]) for slot in current["model_slots"]},
        "settings": dict(current["settings"]),
    }
    result = boundary.handle(_request("save", "settings.provider_model.save", {"draft": draft}))
    assert result["ok"] is False
    assert result["error"]["code"] == "CONFIG_APPLY_FAILED"
    assert "已保存" in result["error"]["message"]
    diagnostic = repr(result["error"]["details"]["diagnostics"])
    assert "fixture runtime publication failed" in diagnostic
    assert "OSError" in diagnostic
    actual = boundary.handle(_request("after", "settings.provider_model.get", {}))["payload"]
    assert actual["providers"][0]["alias"] == "Saved alias"
    assert calls == ["apply"]


def test_snapshot_without_plugin_application_has_only_two_core_model_slots(
    tmp_path: Path,
) -> None:
    boundary = ProviderSettingsBoundary(GENERATION, CREDENTIAL, _root(tmp_path))
    boundary.enable()

    result = boundary.handle(_request("get-clean-model-slots", "settings.provider_model.get", {}))

    assert [slot["identity"] for slot in result["payload"]["model_slots"]] == [
        "core:chat",
        "core:vision_chat",
    ]


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
    boundary = ProviderSettingsBoundary(
        GENERATION,
        CREDENTIAL,
        _root(tmp_path),
        plugin_application_provider=lambda: worker,
    )
    boundary.enable()

    current = boundary.handle(_request("get", "settings.provider_model.get", {}))
    assert current["ok"] is True
    assert current["payload"]["schema_version"] == 1
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
    boundary = ProviderSettingsBoundary(
        GENERATION,
        CREDENTIAL,
        _root(tmp_path),
        plugin_application_provider=lambda: worker,
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
    draft["model_slots"]["plugin:com.example.first:first"] = {
        "profile_id": "fixture",
        "model": "fixture-model",
    }
    draft["model_slots"]["plugin:com.example.second:second"] = {
        "profile_id": "fixture",
        "model": "",
    }
    incomplete = boundary.handle(
        _request("incomplete", "settings.provider_model.save", {"draft": draft})
    )
    assert incomplete["error"]["code"] == "MODEL_SLOT_INCOMPLETE"
    assert {key: incomplete["error"]["details"][key] for key in ("feature", "field")} == {
        "feature": "model.slots",
        "field": "plugin:com.example.second:second",
    }
    assert writes == 0
    assert worker.saved == []

    draft["model_slots"]["plugin:com.example.first:first"] = {
        "profile_id": "",
        "model": "",
    }
    draft["model_slots"]["plugin:com.example.second:second"] = {
        "profile_id": "",
        "model": "",
    }
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
        _request("single-phase", "settings.provider_model.save", {"draft": draft})
    )
    assert core_phase["ok"] is True
    assert core_phase["payload"]["save_state"] == "partial"
    assert core_phase["payload"]["saved_slots"] == [
        "core:chat",
        "core:vision_chat",
        "plugin:com.example.first:first",
    ]
    assert core_phase["payload"]["failed_slot"]["identity"] == (
        "plugin:com.example.second:second"
    )
    assert core_phase["payload"]["failed_slot"]["ownerId"] == "com.example.second"
    assert core_phase["payload"]["failed_slot"]["reasonCode"] == (
        "MODEL_SLOT_SAVE_FAILED"
    )
    assert worker.saved == [
        "plugin:com.example.first:first",
        "plugin:com.example.second:second",
    ]
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
    result = boundary._save_plugin_slots(
        {
            identity: {
                "profile_id": "fixture",
                "model": "fixture-model",
            }
        }
    )

    assert result["save_state"] == "complete"
    assert result["saved_slots"] == [identity]
    assert result["failed_slot"] is None
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
    result = boundary._save_plugin_slots(
        {
            identity: {
                "profile_id": "fixture",
                "model": "fixture-model",
            }
        }
    )

    assert result["save_state"] == "partial"
    assert result["saved_slots"] == []
    assert result["failed_slot"]["reasonCode"] == (
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


@pytest.mark.parametrize(
    ("status", "expected_code"),
    [(401, "AUTHENTICATION_FAILED"), (403, "PROVIDER_ACCESS_FORBIDDEN")],
)
def test_probe_http_errors_keep_status_and_provider_details_after_redaction(
    tmp_path: Path,
    monkeypatch,
    status: int,
    expected_code: str,
) -> None:  # type: ignore[no-untyped-def]
    boundary = ProviderSettingsBoundary(GENERATION, CREDENTIAL, _root(tmp_path))
    boundary.enable()

    def fail(_self: ModelProbe, **_kwargs: object) -> list[str]:
        body = json.dumps(
            {
                "error": {
                    "message": "Invalid credential PRIVATE_PROVIDER_FAILURE",
                    "code": "invalid_api_key",
                    "type": "authentication_error",
                }
            }
        )
        import httpx
        from openai import APIStatusError

        http_error = APIStatusError(
            "failed", body=json.loads(body),
            response=httpx.Response(status, content=body, request=httpx.Request("GET", "https://fixture.invalid/v1/models")),
        )
        raise ApiRequestError(f"API HTTP {status}: {body}") from http_error

    monkeypatch.setattr(ModelProbe, "list_models", fail)
    result = boundary.handle(
        _request("probe", "settings.provider_model.list_models", _profile("probe"))
    )
    assert result["ok"] is False
    assert result["error"]["code"] == expected_code
    assert result["error"]["message"] == (
        f"API HTTP {status}: Invalid credential [REDACTED] "
        "(code: invalid_api_key; type: authentication_error)"
    )
    assert result["error"]["details"]["feature"] == "providers.list_models"
    assert SECRET not in repr(result)
    assert "Invalid credential PRIVATE_PROVIDER_FAILURE" in result["error"]["details"]["diagnostics"]["diagnostic"]


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

    def noisy_probe(_self: ModelProbe, **_kwargs: object) -> object:
        log_event("API", "HTTP 请求成功", {"status": 200})
        return result

    monkeypatch.setattr(ModelProbe, method, noisy_probe)
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

    def timeout(_self: ModelProbe, **_kwargs: object) -> list[str]:
        raise ApiRequestError(f"timed out with private value {SECRET}")

    monkeypatch.setattr(ModelProbe, "list_models", timeout)
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
        _self: ModelProbe,
        *,
        cancel_checker,
    ) -> list[str]:  # type: ignore[no-untyped-def]
        entered.set()
        while True:
            cancel_checker()
            time.sleep(0.01)

    monkeypatch.setattr(ModelProbe, "list_models", block)
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
        _self: ModelProbe,
        *,
        cancel_checker,
    ) -> list[str]:  # type: ignore[no-untyped-def]
        entered.set()
        while True:
            cancel_checker()
            time.sleep(0.01)

    monkeypatch.setattr(ModelProbe, "list_models", block)
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


def test_plugin_replacement_publishes_new_binding_without_adopting_the_old_turn(tmp_path):
    from app.core_host.assistant_adapter import ReadinessResult
    from app.core_host.server import HostConfig, ReadinessController
    from app.plugins.runtime_v4 import PluginRuntimeError

    selected = {"identity": {"providerId": "assistant", "scopeId": "first"}}
    initialized = []

    class Initializer:
        def initialize(self, _cancel):
            identity = selected["identity"]
            initialized.append(identity)
            return ReadinessResult(
                "ready" if identity else "setup_required",
                "READY" if identity else "ASSISTANT_PROVIDER_REQUIRED", "fixture", False, None,
                session=SimpleNamespace(assistant=SimpleNamespace(identity=dict(identity))) if identity else None,
            )

        def close(self):
            pass

    class Application:
        def service_identity(self, _key):
            if selected["identity"] is None:
                raise PluginRuntimeError("SERVICE_MISSING")
            return dict(selected["identity"])

        def bind_session(self, _session):
            pass

        def unbind_session(self):
            pass

        def close(self):
            pass

    controller = ReadinessController(
        HostConfig(RuntimeRoots(tmp_path, tmp_path), GENERATION, CREDENTIAL),
        initializer_factory=lambda _root, _tools: Initializer(),
    )
    controller.begin({})
    controller._worker.join(2)
    controller._plugin_application = Application()
    original = controller.published_session()
    try:
        controller.refresh_assistant_binding()
        assert len(initialized) == 1  # Unrelated plugin changes leave this Session intact.
        selected["identity"] = {"providerId": "assistant", "scopeId": "replacement"}
        controller.refresh_assistant_binding()
        assert controller.published_session().assistant.identity == selected["identity"]
        assert original.assistant.identity["scopeId"] == "first"
        selected["identity"] = None
        controller.refresh_assistant_binding()
        assert controller.published_session() is None
        assert controller.readiness() == "setup_required"
        selected["identity"] = {"providerId": "custom", "scopeId": "third"}
        controller.refresh_assistant_binding()
        assert controller.published_session().assistant.identity == selected["identity"]
        assert len(initialized) == 4
    finally:
        controller.close()


def test_assistant_readiness_republishes_session_with_shared_application_tools(tmp_path):
    from app.core_host.assistant_adapter import ReadinessResult
    from app.core_host.server import HostConfig, ReadinessController

    presentation = {
        "schemaVersion": 1, "generationId": "initializer-owned",
        "characterId": "fixture-character", "displayName": "Fixture Character",
        "initialMessage": "hello", "themeTokens": {}, "defaultPortraitKey": "__default__",
        "portraitKeys": ["__default__"], "portraitResourceIds": {"__default__": "fixture-resource"},
    }
    selected = {"ready": True}
    provider = object()

    class Initializer:
        def initialize(self, _cancel):
            ready = selected["ready"]
            return ReadinessResult(
                state="ready" if ready else "setup_required",
                code="READY" if ready else "PROVIDER_SETUP_REQUIRED", message="fixture", retryable=False,
                current_character_summary=None, current_character_presentation=presentation,
                session=SimpleNamespace(assistant=provider) if ready else None,
            )

        def close(self):
            pass

    class Application:
        def __init__(self):
            self.bound = []
            self.unbound = 0

        def bind_session(self, session):
            self.bound.append(session)

        def unbind_session(self):
            self.unbound += 1

        def close(self):
            pass

    controller = ReadinessController(
        HostConfig(RuntimeRoots(tmp_path, tmp_path), GENERATION, CREDENTIAL),
        initializer_factory=lambda _root, _tools: Initializer(),
    )
    controller.begin({})
    controller._worker.join(2)
    assert controller.readiness() == "ready"
    original = controller.published_session()
    tools = controller._application_tools
    initial_revision = controller.snapshot()["revision"]
    application = Application()
    with controller._lock:
        controller._plugin_application = application
    try:
        selected["ready"] = False
        controller.apply_provider_configuration()
        assert controller.readiness() == "setup_required"
        assert controller.published_session() is None
        assert application.unbound == 1
        assert controller.snapshot()["characterPresentation"] == {**presentation, "generationId": GENERATION}
        selected["ready"] = True
        controller.apply_provider_configuration()
        replacement = controller.published_session()
        assert replacement is not original
        assert replacement.assistant is original.assistant
        assert controller._application_tools is tools
        assert application.bound == [replacement]
        assert controller.snapshot()["revision"] == initial_revision + 2
    finally:
        controller.close()


@pytest.mark.parametrize("kind", ["list_models", "test_connection"])
@pytest.mark.parametrize("latency", [8, 16])
def test_google_probe_uses_full_timeout_without_restarting_request(
    tmp_path: Path, monkeypatch, kind: str, latency: int,
) -> None:
    boundary = ProviderSettingsBoundary(GENERATION, CREDENTIAL, _root(tmp_path))
    boundary.enable()
    calls = []

    import httpx

    def read_response(request):
        calls.append(request)
        # 虚拟供应商耗时：8 秒落在 15 秒预算内，16 秒返回真实传输层超时类型。
        if latency > request.extensions["timeout"]["read"]:
            raise httpx.ReadTimeout("provider timed out", request=request)
        assert request.headers["Authorization"] == f"Bearer {SECRET}"
        if kind == "list_models":
            assert str(request.url) == "https://generativelanguage.googleapis.com/v1beta/openai/models"
            return httpx.Response(200, json={"data": [{"id": "gemini-2.5-flash"}]})
        assert str(request.url) == "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"
        payload = json.loads(request.content)
        assert payload == {"model": "gemini-2.5-flash", "messages": [{"role": "user", "content": "Reply with only OK."}]}
        return httpx.Response(200, json={"choices": [{"message": {"content": "OK"}}]})

    class MockClient(httpx.AsyncClient):
        def __init__(self, **kwargs):
            kwargs.pop("proxy", None)
            super().__init__(**kwargs, transport=httpx.MockTransport(read_response))

    monkeypatch.setattr("httpx.AsyncClient", MockClient)
    profile = _profile("google-probe")
    profile["profile"].update(base_url="https://generativelanguage.googleapis.com/v1", model="gemini-2.5-flash", timeout_seconds=15)
    result = boundary.handle(_request("google-probe", f"settings.provider_model.{kind}", profile))
    assert len(calls) == 1
    assert result["ok"] is (latency <= 15)
    if latency > 15:
        assert result["error"]["code"] == "PROVIDER_TIMEOUT"
    elif kind == "list_models":
        assert result["payload"]["models"] == ["gemini-2.5-flash"]
    else:
        assert result["payload"]["message"] == "OK"
