from __future__ import annotations

import json
import urllib.error
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from plugins.builtin.sakura_gpt_sovits import _support


def _settings(
    tmp_path: Path, *, custom_base_url: str = "https://tts.example.com", **values: object
) -> _support.GPTSoVITSTTSSettings:
    package = tmp_path / "characters" / "sakura"
    reference = package / "voice" / "neutral.wav"
    reference.parent.mkdir(parents=True)
    reference.write_bytes(b"reference")
    return _support.GPTSoVITSTTSSettings(
        enabled=True,
        api_url=f"{custom_base_url}/tts",
        custom_base_url=custom_base_url,
        ref_audio_path=reference,
        ref_text_path=reference,
        ref_text="reference",
        character_id="sakura",
        character_package_dir=package,
        **values,
    )


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
    settings = _settings(tmp_path, remote_reference_root=remote_root)

    assert _support._reference_path(settings, settings.ref_audio_path) == expected


def test_remote_reference_requires_mapping_and_contained_audio(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    with pytest.raises(ValueError, match="^TTS_REFERENCE_AUDIO_UNAVAILABLE$"):
        _support._reference_path(settings, settings.ref_audio_path)

    outside = tmp_path / "outside.wav"
    outside.write_bytes(b"reference")
    with pytest.raises(ValueError, match="^TTS_REFERENCE_AUDIO_UNAVAILABLE$"):
        _support._reference_path(replace(settings, remote_reference_root="/voices"), outside)

    loopback = replace(settings, custom_base_url="http://localhost:9880")
    assert _support._reference_path(loopback, loopback.ref_audio_path) == str(loopback.ref_audio_path)


def _queue(settings: _support.GPTSoVITSTTSSettings) -> SimpleNamespace:
    resolver = _support.GptSovitsEndpointResolver(
        settings, base_dir=Path(), resource_manager=None, is_closed=lambda: False
    )
    # Endpoint reachability is outside these synthesis payload/error cases.
    resolver._custom_checked = True
    return SimpleNamespace(
        settings=settings,
        _supervisor=_support.GptSovitsEndpointSupervisor(resolver),
        _select_reference=lambda _tone: _support.ToneReference(
            "neutral", settings.ref_audio_path, "reference", "ja"
        ),
    )


@pytest.mark.parametrize("error", [urllib.error.URLError("private address"), TimeoutError("private address")])
def test_custom_synthesis_reports_stable_network_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, error: BaseException
) -> None:
    def unavailable(*_args: object, **_kwargs: object) -> None:
        raise error

    monkeypatch.setattr(_support, "_read_url", unavailable)
    failures: list[str] = []
    result = _support.GPTSoVITSSynthesisEngine().synthesize(
        _queue(_settings(tmp_path, custom_base_url="http://localhost:9880")),
        _support._TTSRequest(text="hello", tone="neutral"),
        fail=failures.append,
        skip=lambda _message: None,
    )

    assert result is None
    assert failures == ["TTS_RUNTIME_UNAVAILABLE"]


@pytest.mark.parametrize(
    ("text", "language", "expected"),
    [
        ("Steamを開いているんだね。", "ja", "auto"),
        ("でも私、大丈夫だよ。", "ja", "ja"),
        ("Steam is open.", "en", "en"),
        ("Steam 打开咗。", "all_yue", "auto_yue"),
    ],
)
def test_synthesis_payload_resolves_mixed_text_language(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, text: str, language: str, expected: str
) -> None:
    payloads: list[dict[str, object]] = []

    def capture(request, **_kwargs):
        payloads.append(json.loads(request.data))
        return b"", 200

    monkeypatch.setattr(_support, "_read_url", capture)
    settings = _settings(tmp_path, custom_base_url="http://localhost:9880", text_lang=language)
    _support.GPTSoVITSSynthesisEngine().synthesize(
        _queue(settings),
        _support._TTSRequest(text=text, tone="neutral"),
        fail=lambda _message: None,
        skip=lambda _message: None,
    )

    assert payloads[0]["text_lang"] == expected


def test_managed_runtime_rejects_existing_listener_without_adopting_or_stopping_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = replace(_settings(tmp_path), custom_base_url=None)
    runtime = _support._ManagedRuntime(settings, base_dir=tmp_path, is_closed=lambda: False)
    monkeypatch.setattr(_support, "_probe_tcp", lambda *_args: True)

    def forbidden(*_args: object, **_kwargs: object) -> None:
        pytest.fail("an existing listener must not be started, adopted or terminated")

    monkeypatch.setattr(runtime, "_start", forbidden)
    monkeypatch.setattr(_support, "terminate_process_tree", forbidden)
    failures: list[str] = []

    assert not runtime.ensure_available(failures.append)
    runtime.close()
    assert failures == ["TTS_PORT_OCCUPIED"]
    assert runtime._server_process is None
