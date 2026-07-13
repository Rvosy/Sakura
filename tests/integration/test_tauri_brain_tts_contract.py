from __future__ import annotations

import threading
import time
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from app.brain_host.application import BrainHostApplication, BrainHostConfig
from app.brain_host.errors import BrainHostError
from app.voice.tts_synthesis_service import (
    NullTTSSynthesisService,
    TTSSynthesisService,
)


ROOT = Path(__file__).resolve().parents[2]


def _module_url(relative: str) -> str:
    return (ROOT / relative).resolve().as_uri()


def _run_node(source: str) -> dict[str, Any]:
    result = subprocess.run(
        ["node", "--experimental-default-type=module", "--input-type=module", "-e", source],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return json.loads(result.stdout)


class Events:
    def __init__(self) -> None:
        self.items: list[tuple[str, dict[str, Any]]] = []
        self.condition = threading.Condition()

    def __call__(self, name: str, payload: dict[str, Any]) -> None:
        with self.condition:
            self.items.append((name, payload))
            self.condition.notify_all()

    def wait(self, name: str, timeout: float = 2) -> dict[str, Any]:
        deadline = time.monotonic() + timeout
        with self.condition:
            while True:
                for event_name, payload in self.items:
                    if event_name == name:
                        return payload
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise AssertionError(f"missing {name}: {self.items!r}")
                self.condition.wait(remaining)


class Engine:
    def synthesize(self, queue, request, *, fail, skip):  # type: ignore[no-untyped-def]
        _ = request, fail, skip
        path = queue._cache_dir / "brain.wav"
        path.write_bytes(b"RIFF-brain")
        return path


class LateEngine:
    def __init__(self) -> None:
        self.entered = threading.Event()
        self.release = threading.Event()

    def synthesize(self, queue, request, *, fail, skip):  # type: ignore[no-untyped-def]
        _ = request, fail, skip
        self.entered.set()
        assert self.release.wait(2)
        path = queue._cache_dir / "late.wav"
        path.write_bytes(b"RIFF-late")
        return path


def _service(tmp_path: Path, engine: object) -> TTSSynthesisService:
    supervisor = SimpleNamespace(
        settings=SimpleNamespace(text_lang="ja", tone_references={}),
        service_ready=True,
        ensure_ready=lambda: (True, "ready"),
    )
    return TTSSynthesisService(
        supervisor=supervisor,
        engine=engine,
        cache_dir=tmp_path / "data" / "cache" / "tts",
        resource_manager=None,
    )


def _application(tmp_path: Path, tts_service: object) -> tuple[BrainHostApplication, Events]:
    application = BrainHostApplication(
        BrainHostConfig(tmp_path, "session-tts", "credential-tts", 1)
    )
    application.state = "ready"
    application.context = SimpleNamespace()
    application.tts_service = tts_service
    events = Events()
    application.set_event_sink(events)
    return application, events


def test_brain_tts_synthesize_emits_private_resource_descriptor(tmp_path: Path) -> None:
    application, events = _application(tmp_path, _service(tmp_path, Engine()))

    accepted = application.handle_request(
        "tts.synthesize",
        {"text": "こんにちは", "tone": "温和", "segment_id": "segment-1"},
        request_id="ipc-tts-1",
    )
    ready = events.wait("tts.audio_ready")

    assert accepted["version"] == 1
    assert accepted["synthesisId"].startswith("tts-")
    assert ready["synthesisId"] == accepted["synthesisId"]
    assert ready["requestId"] == "ipc-tts-1"
    assert ready["segmentId"] == "segment-1"
    assert ready["resource"]["id"].startswith("audio-")
    assert Path(ready["resource"]["path"]).is_file()
    assert ready["resource"]["mediaType"] == "audio/wav"
    application.shutdown()


def test_brain_tts_cancel_suppresses_late_audio_event(tmp_path: Path) -> None:
    engine = LateEngine()
    application, events = _application(tmp_path, _service(tmp_path, engine))
    accepted = application.handle_request("tts.synthesize", {"text": "遅い"})
    assert engine.entered.wait(1)

    assert application.handle_request(
        "tts.cancel", {"synthesis_id": accepted["synthesisId"]}
    ) == {
        "version": 1,
        "synthesisId": accepted["synthesisId"],
        "cancelled": True,
    }
    engine.release.set()
    cancelled = events.wait("tts.cancelled")
    assert cancelled["synthesisId"] == accepted["synthesisId"]
    time.sleep(0.05)
    assert not any(name == "tts.audio_ready" for name, _payload in events.items)
    application.shutdown()


def test_brain_null_tts_reports_skip_and_invalid_cancel(tmp_path: Path) -> None:
    application, events = _application(tmp_path, NullTTSSynthesisService())
    accepted = application.handle_request("tts.synthesize", {"text": "静音"})
    ready = events.wait("tts.audio_ready")

    assert ready["synthesisId"] == accepted["synthesisId"]
    assert ready["resource"] is None
    assert ready["skippedReason"] == "tts_disabled"
    with pytest.raises(BrainHostError) as missing:
        application.handle_request("tts.cancel", {"synthesis_id": "missing"})
    assert missing.value.code == "TTS_REQUEST_NOT_FOUND"
    application.shutdown()


def test_brain_tts_rejects_empty_text(tmp_path: Path) -> None:
    application, _events = _application(tmp_path, NullTTSSynthesisService())

    with pytest.raises(BrainHostError) as invalid:
        application.handle_request("tts.synthesize", {"text": "  "})

    assert invalid.value.code == "INVALID_REQUEST"
    application.shutdown()


def test_frontend_audio_controller_synthesizes_and_plays_segments_in_order() -> None:
    payload = _run_node(
        f"""
import {{ createPetStore }} from {json.dumps(_module_url('desktop/frontend/core/store.js'))};
import {{ AudioController }} from {json.dumps(_module_url('desktop/frontend/audio/audio_controller.js'))};
const store = createPetStore();
const calls = [];
let synthesisCount = 0;
const invoke = async (command, args) => {{
  calls.push([command, args]);
  if (command === "tts_synthesize") {{
    synthesisCount += 1;
    return {{ synthesisId: `synthesis-${{synthesisCount}}` }};
  }}
  return {{}};
}};
const controller = new AudioController({{ store, invoke, setStatus: () => {{}} }});
controller.queueSegments([
  {{ ja: "一", zh: "一", tone: "温和", suppressTts: false }},
  {{ ja: "二", zh: "二", tone: "中性", suppressTts: true }},
  {{ ja: "三", zh: "三", tone: "开心", suppressTts: false }},
]);
await new Promise((resolve) => setTimeout(resolve, 0));
controller.handleAudioReady({{
  synthesisId: "synthesis-1",
  segmentId: "audio-segment-1-0",
  resource: {{ id: "audio-1" }},
}});
await new Promise((resolve) => setTimeout(resolve, 0));
const playbackId = store.getState().audio.playbackId;
controller.handlePlaybackState({{ playbackId, state: "started" }});
const speaking = store.getState().audio.speaking;
controller.handlePlaybackState({{ playbackId, state: "finished" }});
await new Promise((resolve) => setTimeout(resolve, 0));
await controller.stop();
console.log(JSON.stringify({{ calls, speaking, state: store.getState().audio }}));
"""
    )

    assert payload["calls"][0] == [
        "tts_synthesize",
        {"text": "一", "tone": "温和", "segmentId": "audio-segment-1-0"},
    ]
    assert payload["calls"][1][0] == "play_tts_audio"
    assert payload["calls"][1][1]["resourceId"] == "audio-1"
    assert "path" not in json.dumps(payload["calls"])
    assert payload["calls"][2] == [
        "tts_synthesize",
        {"text": "三", "tone": "开心", "segmentId": "audio-segment-1-2"},
    ]
    assert payload["speaking"] is True
    assert payload["state"]["speaking"] is False


def test_tauri_tts_frontend_and_rust_commands_are_wired() -> None:
    frontend = ROOT / "desktop" / "frontend"
    audio_controller = frontend / "audio" / "audio_controller.js"
    app_source = (frontend / "app.js").read_text(encoding="utf-8")
    store_source = (frontend / "core" / "store.js").read_text(encoding="utf-8")
    rust_state = (ROOT / "desktop" / "src-tauri" / "src" / "app_state.rs").read_text(
        encoding="utf-8"
    )
    rust_lib = (ROOT / "desktop" / "src-tauri" / "src" / "lib.rs").read_text(
        encoding="utf-8"
    )

    assert audio_controller.is_file()
    assert 'from "./audio/audio_controller.js"' in app_source
    for event_name in (
        "sakura://tts-audio-ready",
        "sakura://tts-error",
        "sakura://tts-cancelled",
        "sakura://tts-playback-state",
        "sakura://assistant-backchannel",
    ):
        assert event_name in app_source
    assert "speaking" in store_source
    for command in (
        "tts_synthesize",
        "tts_cancel",
        "play_tts_audio",
        "stop_tts_audio",
        "set_tts_volume",
    ):
        assert f"fn {command}" in rust_state
        assert f"app_state::{command}" in rust_lib
