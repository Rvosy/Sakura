from __future__ import annotations

import urllib.error
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.core.cancellation import OperationCancelled
from app.voice.tts_contracts import TtsError
from app.voice.tts_endpoint import (
    GptSovitsEndpointResolver,
    GptSovitsEndpointSupervisor,
    reference_path_for_endpoint,
)
from app.voice.tts_registry import default_tts_provider_registry
from app.voice.tts_settings import GPTSoVITSTTSSettings, ToneReference
from app.voice.tts_synthesis import GPTSoVITSSynthesisEngine
from app.voice.tts_synthesis_service import TTSSynthesisService
from app.voice.tts_service import TTSServiceSupervisor
from app.voice.tts_types import _TTSRequest


def _settings(
    tmp_path: Path,
    *,
    provider: str = "gpt-sovits",
    custom_base_url: str | None = None,
    tts_path: str = "/tts",
    remote_reference_root: str | None = None,
) -> GPTSoVITSTTSSettings:
    package = tmp_path / "characters" / "sakura"
    reference = package / "voice" / "neutral.wav"
    reference.parent.mkdir(parents=True, exist_ok=True)
    reference.write_bytes(b"reference")
    return GPTSoVITSTTSSettings(
        enabled=True,
        provider=provider,
        api_url=f"{custom_base_url or 'http://127.0.0.1:9880'}{tts_path}",
        custom_base_url=custom_base_url,
        tts_path=tts_path,
        remote_reference_root=remote_reference_root,
        ref_audio_path=reference,
        ref_text_path=reference,
        ref_text="reference",
        character_id="sakura",
        character_package_dir=package,
    )


class _ManagedRuntime:
    def __init__(self, settings, **_kwargs) -> None:  # type: ignore[no-untyped-def]
        self.settings = settings
        self.service_ready = False
        self.available_calls = 0
        self.weights_calls = 0
        self.close_calls = 0

    def _ensure_service_available(self, _fail) -> bool:  # type: ignore[no-untyped-def]
        self.available_calls += 1
        self.service_ready = True
        return True

    def _ensure_character_weights(self, _fail) -> bool:  # type: ignore[no-untyped-def]
        self.weights_calls += 1
        return True

    def _restart_local_service_after_http_failure(self, _status, _body) -> bool:  # type: ignore[no-untyped-def]
        return True

    def close(self) -> None:
        self.close_calls += 1


def test_managed_endpoint_delegates_runtime_lifecycle(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr("app.voice.tts_endpoint.TTSServiceSupervisor", _ManagedRuntime)
    resolver = GptSovitsEndpointResolver(
        _settings(tmp_path), base_dir=tmp_path, resource_manager=object(), is_closed=lambda: False
    )
    supervisor = GptSovitsEndpointSupervisor(resolver)

    assert resolver.endpoint.kind == "managed"
    assert resolver.endpoint.lifecycle_owned is True
    assert supervisor.ensure_ready()[0] is True
    runtime = resolver.runtime
    assert runtime is not None
    assert runtime.available_calls == 1
    assert runtime.weights_calls == 1
    supervisor.close()
    assert runtime.close_calls == 1


def test_managed_weight_switch_observes_job_cancellation(
    tmp_path: Path,
    monkeypatch,
) -> None:  # type: ignore[no-untyped-def]
    gpt_model = tmp_path / "model.ckpt"
    sovits_model = tmp_path / "model.pth"
    gpt_model.write_bytes(b"gpt")
    sovits_model.write_bytes(b"sovits")
    supervisor = SimpleNamespace(
        _weights_ready=False,
        settings=SimpleNamespace(
            api_url="http://127.0.0.1:9880/tts",
            timeout_seconds=60,
            gpt_model_path=gpt_model,
            sovits_model_path=sovits_model,
        ),
    )
    supervisor._request_weight_switch = (  # type: ignore[attr-defined]
        TTSServiceSupervisor._request_weight_switch.__get__(supervisor)
    )
    checks = 0
    requested: list[str] = []

    def cancel_checker() -> None:
        nonlocal checks
        checks += 1
        if checks >= 2:
            raise OperationCancelled("cancel weight switch")

    def cancellable_read(_opener, request, **kwargs):  # type: ignore[no-untyped-def]
        requested.append(request.full_url)
        kwargs["cancel_checker"]()
        raise AssertionError("cancel checker must interrupt the weight request")

    monkeypatch.setattr("app.voice.tts_service.read_url_cancellable", cancellable_read)
    with pytest.raises(OperationCancelled, match="cancel weight switch"):
        TTSServiceSupervisor._ensure_character_weights(
            supervisor,
            lambda _message: None,
            cancel_checker=cancel_checker,
        )

    assert len(requested) == 1
    assert "set_gpt_weights" in requested[0]
    assert supervisor._weights_ready is False


@pytest.mark.parametrize(
    "base_url",
    ["http://127.0.0.1:9880", "http://192.168.1.20:9880", "https://tts.example.com"],
)
def test_custom_endpoint_only_probes_and_never_owns_runtime(
    tmp_path: Path, monkeypatch, base_url: str
) -> None:  # type: ignore[no-untyped-def]
    def forbidden_runtime(*_args, **_kwargs):  # type: ignore[no-untyped-def]
        raise AssertionError("custom endpoints must never construct a managed runtime")

    monkeypatch.setattr("app.voice.tts_endpoint.TTSServiceSupervisor", forbidden_runtime)
    monkeypatch.setattr("app.voice.tts_endpoint._probe_tcp_port", lambda *_args: True)
    monkeypatch.setattr("app.voice.tts_endpoint._probe_gpt_sovits_http", lambda *_args: True)
    resolver = GptSovitsEndpointResolver(
        _settings(tmp_path, custom_base_url=base_url),
        base_dir=tmp_path,
        resource_manager=object(),
        is_closed=lambda: False,
    )
    supervisor = GptSovitsEndpointSupervisor(resolver)

    assert resolver.runtime is None
    assert resolver.endpoint.kind == "custom"
    assert resolver.endpoint.lifecycle_owned is False
    assert supervisor.settings.api_url == f"{base_url}/tts"
    assert supervisor.ensure_ready()[0] is True
    assert supervisor._ensure_character_weights(lambda _message: None) is True
    assert supervisor._restart_local_service_after_http_failure(500, "broken pipe") is False
    supervisor.close()


@pytest.mark.parametrize(
    ("remote_root", "expected"),
    [
        ("/data/voices", "/data/voices/sakura/voice/neutral.wav"),
        (r"D:\voices", r"D:\voices\sakura\voice\neutral.wav"),
        (r"\\server\voices", r"\\server\voices\sakura\voice\neutral.wav"),
    ],
)
def test_remote_reference_root_maps_character_relative_path(
    tmp_path: Path, remote_root: str, expected: str
) -> None:
    settings = _settings(
        tmp_path,
        custom_base_url="https://tts.example.com",
        remote_reference_root=remote_root,
    )
    assert reference_path_for_endpoint(settings, settings.ref_audio_path) == expected


def test_remote_reference_requires_mapping_but_custom_loopback_uses_local_path(tmp_path: Path) -> None:
    remote = _settings(tmp_path, custom_base_url="https://tts.example.com")
    with pytest.raises(TtsError, match="REFERENCE_AUDIO_UNAVAILABLE"):
        reference_path_for_endpoint(remote, remote.ref_audio_path)

    loopback = _settings(tmp_path, custom_base_url="http://localhost:9880")
    assert reference_path_for_endpoint(loopback, loopback.ref_audio_path) == str(loopback.ref_audio_path)


def test_registry_has_only_public_providers_and_unknown_provider_is_stable(tmp_path: Path) -> None:
    registry = default_tts_provider_registry()
    assert registry.provider_ids == ("gpt-sovits", "genie-tts")
    unknown = _settings(tmp_path, provider="future-tts")
    with pytest.raises(TtsError, match="PROVIDER_NOT_FOUND"):
        registry.create(
            unknown, base_dir=tmp_path, resource_manager=object(), is_closed=lambda: False
        )
    with pytest.raises(TtsError, match="PROVIDER_NOT_FOUND"):
        TTSSynthesisService(
            unknown,
            base_dir=tmp_path,
            cache_dir=tmp_path / "cache",
            registry=registry,
        )


def test_registry_switch_gpt_genie_gpt_keeps_lifecycles_isolated(
    tmp_path: Path, monkeypatch
) -> None:  # type: ignore[no-untyped-def]
    created: list[_ManagedRuntime] = []

    class TrackedRuntime(_ManagedRuntime):
        def __init__(self, settings, **kwargs) -> None:  # type: ignore[no-untyped-def]
            super().__init__(settings, **kwargs)
            created.append(self)

    monkeypatch.setattr("app.voice.tts_endpoint.TTSServiceSupervisor", TrackedRuntime)
    monkeypatch.setattr("app.voice.tts_registry.GenieServiceSupervisor", TrackedRuntime)
    registry = default_tts_provider_registry()
    gpt = _settings(tmp_path)
    genie = replace(
        gpt,
        provider="genie-tts",
        api_url="http://127.0.0.1:9881/",
        work_dir=tmp_path / "genie",
    )

    components = [
        registry.create(gpt, base_dir=tmp_path, resource_manager=object(), is_closed=lambda: False),
        registry.create(genie, base_dir=tmp_path, resource_manager=object(), is_closed=lambda: False),
        registry.create(gpt, base_dir=tmp_path, resource_manager=object(), is_closed=lambda: False),
    ]
    for item in components:
        item.supervisor.close()

    assert [item.provider_id for item in components] == ["gpt-sovits", "genie-tts", "gpt-sovits"]
    assert len({id(item) for item in created}) == 3
    assert [item.close_calls for item in created] == [1, 1, 1]


class _CustomSupervisor:
    endpoint_kind = "custom"

    def __init__(self, settings: GPTSoVITSTTSSettings) -> None:
        self.settings = settings

    def _ensure_service_available(self, _fail) -> bool:  # type: ignore[no-untyped-def]
        return True

    def _ensure_character_weights(self, _fail) -> bool:  # type: ignore[no-untyped-def]
        return True

    def _restart_local_service_after_http_failure(self, _status, _body) -> bool:  # type: ignore[no-untyped-def]
        return False


class _Queue:
    def __init__(self, settings: GPTSoVITSTTSSettings) -> None:
        self.settings = settings
        self._supervisor = _CustomSupervisor(settings)

    def _select_reference(self, _tone: str) -> ToneReference:
        return ToneReference("neutral", self.settings.ref_audio_path, "reference", "ja")


@pytest.mark.parametrize(
    ("raised", "expected_code"),
    [
        (urllib.error.URLError("unreachable"), "CONNECTION_FAILED"),
        (TimeoutError(), "REQUEST_TIMEOUT"),
    ],
)
def test_custom_synthesis_reports_stable_network_errors(
    tmp_path: Path, monkeypatch, raised: BaseException, expected_code: str
) -> None:  # type: ignore[no-untyped-def]
    settings = _settings(tmp_path, custom_base_url="http://localhost:9880")
    monkeypatch.setattr(
        "app.voice.tts_synthesis.read_url_cancellable",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(raised),
    )
    failures: list[str] = []

    result = GPTSoVITSSynthesisEngine().synthesize(
        _Queue(settings),
        _TTSRequest(text="hello", tone="neutral", request_id="request-1"),
        fail=failures.append,
        skip=lambda _message: None,
    )

    assert result is None
    assert failures and failures[-1].startswith(f"{expected_code}:")
