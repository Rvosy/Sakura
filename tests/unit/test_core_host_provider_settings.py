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
    draft = {
        "providers": [],
        "model_slots": {"chat": {}, "vision_chat": {}},
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
