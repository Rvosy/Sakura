from __future__ import annotations

import json
import subprocess
import sys
import threading
import time
import wave
from pathlib import Path
from types import SimpleNamespace

from app.core_host.tts_boundary import TTSBoundary
from app.voice.tts_settings import GPTSoVITSTTSSettings
from app.voice.tts_synthesis_service import SynthesizedAudio


GENERATION = "generation-tts-1"
CREDENTIAL = "1" * 32


def test_runtime_v2_tts_boundary_is_qt_free() -> None:
    source = """
import importlib.abc
import sys

class RejectPySide(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == 'PySide6' or fullname.startswith('PySide6.'):
            raise AssertionError(f'forbidden Qt import: {fullname}')
        return None

sys.meta_path.insert(0, RejectPySide())
import app.core_host.tts_boundary
"""
    subprocess.run([sys.executable, "-c", source], check=True)


def _request(name: str, payload: dict, *, request_id: str = "request-1") -> dict:
    return {
        "protocolMajor": 2,
        "protocolMinor": 2,
        "kind": "request",
        "generationId": GENERATION,
        "generationCredential": CREDENTIAL,
        "id": request_id,
        "name": name,
        "payload": payload,
    }


def _write_wav(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(16_000)
        handle.writeframes(b"\x01\x00" * 160)


class _Handle:
    def __init__(self, result: SynthesizedAudio) -> None:
        self._result = result
        self.cancelled = False

    def result(self, timeout: float | None = None) -> SynthesizedAudio:
        assert timeout is not None
        return self._result

    def cancel(self) -> bool:
        self.cancelled = True
        return True


class _Service:
    provider = "gpt-sovits"

    def __init__(self, cache_dir: Path) -> None:
        self.cache_dir = cache_dir
        self.closed = False
        self.ready_calls = 0

    def ensure_ready(self) -> tuple[bool, str]:
        self.ready_calls += 1
        return True, "ready"

    def synthesize(self, text: str, tone: str, *, request_id: str) -> _Handle:
        assert text == "こんにちは"
        assert tone == "happy"
        target = self.cache_dir / f"source-{request_id}.wav"
        _write_wav(target)
        return _Handle(SynthesizedAudio(request_id, target, target.stat().st_size))

    def close(self) -> None:
        self.closed = True


def _boundary(tmp_path: Path, events: list[dict]) -> TTSBoundary:
    boundary = TTSBoundary(
        GENERATION,
        CREDENTIAL,
        tmp_path,
        session_provider=lambda: SimpleNamespace(character=SimpleNamespace(id="sakura")),
        event_publisher=events.append,
        synthesis_factory=lambda _settings, *, base_dir, cache_dir: _Service(cache_dir),
    )
    boundary._load_settings = lambda **_kwargs: SimpleNamespace(  # type: ignore[method-assign]
        enabled=True,
        provider="gpt-sovits",
        timeout_seconds=10,
    )
    return boundary


def test_authorized_segment_persists_before_opaque_descriptor(tmp_path: Path) -> None:
    events: list[dict] = []
    boundary = _boundary(tmp_path, events)
    boundary.authorize_segment(
        operation_id="operation-1",
        segment_index=0,
        text="こんにちは",
        tone="happy",
        portrait="smile",
        character_id="sakura",
        history_entry_id="entry-0001",
    )

    result = boundary.handle(
        _request(
            "tts.synthesis.start",
            {"operationId": "operation-1", "segmentIndex": 0},
        )
    )

    assert result["ok"] is True
    descriptor = result["payload"]
    assert set(descriptor) == {
        "opaqueId",
        "recordingId",
        "mediaType",
        "byteLength",
        "expiresAt",
    }
    assert "path" not in descriptor
    recording_dir = (
        tmp_path / "data" / "voice" / "recordings" / "sakura" / descriptor["recordingId"]
    )
    metadata = json.loads((recording_dir / "record.json").read_text(encoding="utf-8"))
    assert metadata["historyEntryId"] == "entry-0001"
    assert metadata["favorite"] is False
    playback = (
        tmp_path
        / "data"
        / "cache"
        / "tts"
        / "runtime-v2"
        / GENERATION
        / f"{descriptor['opaqueId']}.wav"
    )
    assert playback.is_file()
    assert events[-1]["name"] == "tts.synthesis.ready"
    assert "path" not in events[-1]["payload"]

    duplicate = boundary.handle(
        _request(
            "tts.synthesis.start",
            {"operationId": "operation-1", "segmentIndex": 0},
            request_id="request-duplicate",
        )
    )
    assert duplicate["ok"] is False
    assert duplicate["error"]["code"] == "TTS_SEGMENT_NOT_AUTHORIZED"

    boundary.close()
    assert not playback.exists()
    assert (recording_dir / "audio.wav").exists()


def test_recording_os_error_is_reported_as_audio_recording_invalid(tmp_path: Path) -> None:
    class FailingRecordingStore:
        def commit(self, *_args, **_kwargs):
            raise OSError("fixture storage failure")

        def cleanup_generation(self, _generation_id: str) -> None:
            return None

    events: list[dict] = []
    boundary = TTSBoundary(
        GENERATION,
        CREDENTIAL,
        tmp_path,
        session_provider=lambda: SimpleNamespace(character=SimpleNamespace(id="sakura")),
        event_publisher=events.append,
        synthesis_factory=lambda _settings, *, base_dir, cache_dir: _Service(cache_dir),
        recording_store=FailingRecordingStore(),  # type: ignore[arg-type]
    )
    boundary._load_settings = lambda **_kwargs: SimpleNamespace(  # type: ignore[method-assign]
        enabled=True,
        provider="gpt-sovits",
        timeout_seconds=10,
    )
    boundary.authorize_segment(
        operation_id="operation-recording-failure",
        segment_index=0,
        text="こんにちは",
        tone="happy",
        portrait="smile",
        character_id="sakura",
        history_entry_id="entry-0002",
    )

    result = boundary.handle(
        _request(
            "tts.synthesis.start",
            {"operationId": "operation-recording-failure", "segmentIndex": 0},
        )
    )

    assert result["ok"] is False
    assert result["error"]["code"] == "AUDIO_RECORDING_INVALID"
    assert events[-1]["name"] == "tts.synthesis.failed"
    assert events[-1]["payload"]["error"]["code"] == "AUDIO_RECORDING_INVALID"


def test_untrusted_text_or_segment_cannot_be_submitted(tmp_path: Path) -> None:
    boundary = _boundary(tmp_path, [])
    result = boundary.handle(
        _request(
            "tts.synthesis.start",
            {"operationId": "missing", "segmentIndex": 0, "text": "attacker supplied"},
        )
    )
    assert result["ok"] is False
    assert result["error"]["code"] == "TTS_SEGMENT_NOT_AUTHORIZED"
    assert not (tmp_path / "data" / "voice").exists()


def test_stale_generation_is_rejected(tmp_path: Path) -> None:
    boundary = _boundary(tmp_path, [])
    request = _request("tts.bundle.status", {})
    request["generationId"] = "old-generation"
    result = boundary.handle(request)
    assert result["ok"] is False
    assert result["error"]["code"] == "STALE_GENERATION"


def test_rust_playback_observation_publishes_only_bounded_plugin_summary(tmp_path: Path) -> None:
    observed: list[tuple[str, dict]] = []
    worker = SimpleNamespace(emit_event=lambda name, payload: observed.append((name, payload)))
    boundary = TTSBoundary(
        GENERATION,
        CREDENTIAL,
        tmp_path,
        session_provider=lambda: SimpleNamespace(plugin_worker=worker),
    )

    started = boundary.handle(
        _request(
            "tts.playback.observe",
            {"playbackId": "playback-1", "recordingId": "recording-1", "state": "started"},
        )
    )
    finished = boundary.handle(
        _request(
            "tts.playback.observe",
            {"playbackId": "playback-1", "recordingId": "recording-1", "state": "finished"},
            request_id="request-2",
        )
    )

    assert started["ok"] is True
    assert finished["ok"] is True
    assert observed == [
        (
            "tts.start",
            {"playbackId": "playback-1", "recordingId": "recording-1", "outcome": "started"},
        ),
        (
            "tts.end",
            {"playbackId": "playback-1", "recordingId": "recording-1", "outcome": "finished"},
        ),
    ]


def test_bundle_cancel_joins_worker_and_preserves_resumable_state(
    tmp_path: Path, monkeypatch
) -> None:
    from app.voice import tts_bundle

    started = threading.Event()
    entry = SimpleNamespace(
        key="fixture-bundle",
        label="Fixture Bundle",
        provider="gpt-sovits",
        size=10,
    )

    def fake_install(_entry, _root, *, check_cancel, on_progress):
        on_progress(25)
        started.set()
        assert started.wait(1)
        while True:
            check_cancel()

    monkeypatch.setattr(tts_bundle, "compatible_tts_bundles", lambda: (entry,))
    monkeypatch.setattr(tts_bundle, "install_tts_bundle", fake_install)
    monkeypatch.setattr(tts_bundle, "default_bundle_work_dir", lambda _entry, root: root / "missing")
    boundary = _boundary(tmp_path, [])
    install = boundary.handle(_request("tts.bundle.install", {"bundleKey": entry.key}))
    assert install["ok"] is True
    assert started.wait(1)

    cancelled = boundary.handle(
        _request(
            "tts.bundle.cancel",
            {"taskId": install["payload"]["taskId"]},
            request_id="cancel-bundle",
        )
    )
    status = boundary.handle(
        _request("tts.bundle.status", {}, request_id="bundle-status")
    )

    assert cancelled["payload"] == {
        "accepted": True,
        "taskId": install["payload"]["taskId"],
        "joined": True,
    }
    assert status["payload"]["activeTask"]["state"] == "cancelled"
    assert status["payload"]["activeTask"]["progress"] == 25


def test_status_is_strict_path_free_and_disabled_does_not_start_service(tmp_path: Path) -> None:
    created: list[_Service] = []
    boundary = TTSBoundary(
        GENERATION,
        CREDENTIAL,
        tmp_path,
        session_provider=lambda: SimpleNamespace(character=SimpleNamespace(id="sakura")),
        synthesis_factory=lambda _settings, *, base_dir, cache_dir: created.append(_Service(cache_dir)) or created[-1],
    )
    boundary._load_settings = lambda **_kwargs: SimpleNamespace(  # type: ignore[method-assign]
        enabled=False,
        provider="gpt-sovits",
        api_url="http://127.0.0.1:9880/tts",
        work_dir=None,
        python_path=None,
        timeout_seconds=10,
    )
    boundary.on_session_ready()
    result = boundary.handle(_request("tts.status.get", {}))

    assert result["ok"] is True
    payload = result["payload"]
    assert set(payload) == {
        "schemaVersion", "enabled", "selectedProvider", "providers", "bundles", "runtime", "activeTask",
    }
    assert payload["runtime"]["state"] == "disabled"
    assert {provider["id"] for provider in payload["providers"]} == {
        "gpt-sovits", "custom-gpt-sovits", "genie-tts",
    }
    assert "path" not in json.dumps(payload).lower()
    assert created == []


def test_runtime_v2_resolves_installed_bundled_provider_when_legacy_work_dir_is_blank(
    tmp_path: Path, monkeypatch
) -> None:
    from app.config.settings_service import AppSettingsService
    from app.voice import runtime_compat, tts_bundle

    reference = tmp_path / "reference.wav"
    reference.write_bytes(b"reference")
    installed = tmp_path / "tts" / "cpu"
    runtime = installed / "runtime"
    runtime.mkdir(parents=True)
    runtime_python = runtime / ("python.exe" if sys.platform == "win32" else "bin/python")
    settings = GPTSoVITSTTSSettings(
        enabled=True,
        provider="genie-tts",
        api_url="http://127.0.0.1:9881/",
        ref_audio_path=reference,
        ref_text_path=reference,
        ref_text="reference",
        work_dir=None,
        onnx_model_dir=tmp_path / "onnx",
    )
    monkeypatch.setattr(
        AppSettingsService,
        "load_tts_settings",
        lambda _self, **_kwargs: settings,
    )
    monkeypatch.setattr(
        tts_bundle,
        "default_provider_bundle_work_dir",
        lambda _provider, _root: installed,
    )
    monkeypatch.setattr(
        runtime_compat,
        "find_usable_runtime_python",
        lambda directory: runtime_python if directory == runtime else None,
    )
    boundary = TTSBoundary(
        GENERATION,
        CREDENTIAL,
        tmp_path,
        session_provider=lambda: SimpleNamespace(character=SimpleNamespace(id="sakura")),
    )

    loaded = boundary._load_settings(validate_enabled=False)

    assert loaded.work_dir == installed.resolve()
    assert settings.work_dir is None


def test_session_ready_warms_enabled_service_once_and_updates_status(
    tmp_path: Path, monkeypatch
) -> None:
    created: list[_Service] = []
    events: list[str] = []
    monkeypatch.setattr(
        "app.core_host.tts_boundary.log_event",
        lambda _channel, _message, _attributes=None, **kwargs: events.append(kwargs.get("event", "")),
    )
    boundary = TTSBoundary(
        GENERATION,
        CREDENTIAL,
        tmp_path,
        session_provider=lambda: SimpleNamespace(character=SimpleNamespace(id="sakura")),
        synthesis_factory=lambda _settings, *, base_dir, cache_dir: created.append(_Service(cache_dir)) or created[-1],
    )
    boundary._load_settings = lambda **_kwargs: SimpleNamespace(  # type: ignore[method-assign]
        enabled=True,
        provider="gpt-sovits",
        api_url="http://127.0.0.1:9880/tts",
        work_dir=None,
        python_path=None,
        timeout_seconds=10,
    )
    boundary.on_session_ready()
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        status = boundary.handle(_request("tts.status.get", {}, request_id="status-ready"))
        if status["payload"]["runtime"]["state"] == "ready":
            break
        time.sleep(0.01)

    assert status["payload"]["runtime"]["state"] == "ready"
    assert len(created) == 1
    assert created[0].ready_calls == 1
    assert events[:2] == ["tts.startup.started", "tts.startup.ready"]
    boundary.close()
