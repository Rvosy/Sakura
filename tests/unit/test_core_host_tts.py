from __future__ import annotations

import json
import shutil
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


def test_authorized_plugin_artifact_is_committed_by_core_before_playback(
    tmp_path: Path,
) -> None:
    from app.core_host.plugin_artifacts import PluginArtifactStore

    store = PluginArtifactStore(tmp_path, GENERATION)
    allocated = store.allocate(
        "com.example.tts-provider",
        {"mediaType": "audio/wav", "suffix": ".wav"},
    )
    _write_wav(Path(allocated["path"]))
    committed = store.commit(
        "com.example.tts-provider",
        allocated["artifactId"],
    )

    class Worker:
        def resolve_committed_artifact(self, artifact_id: str):
            return store.resolve_committed_by_id(artifact_id)

        def release_committed_artifact(self, artifact_id: str) -> bool:
            artifact = store.resolve_committed_by_id(artifact_id)
            return store.release(artifact.plugin_id, artifact_id)

    boundary = TTSBoundary(
        GENERATION,
        CREDENTIAL,
        tmp_path,
        session_provider=lambda: SimpleNamespace(plugin_worker=Worker()),
    )
    boundary.authorize_segment(
        operation_id="operation-plugin",
        segment_index=0,
        text="こんにちは",
        tone="happy",
        portrait="smile",
        character_id="sakura",
        history_entry_id="entry-plugin",
    )
    authorization = boundary._authorizations[("operation-plugin", 0)]

    descriptor, recording = boundary._consume_plugin_audio_artifact(
        committed,
        authorization,
        provider="com.example.tts-provider",
    )

    assert set(descriptor) == {
        "opaqueId",
        "recordingId",
        "mediaType",
        "byteLength",
        "expiresAt",
    }
    assert descriptor["recordingId"] == recording.recording_id
    metadata_path = recording.directory / "record.json"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    assert metadata["historyEntryId"] == "entry-plugin"
    assert metadata["provider"] == "com.example.tts-provider"
    assert store.count == 0
    assert not Path(allocated["path"]).exists()
    boundary.close()


def test_tts_hub_selects_character_provider_and_core_owns_final_audio(
    tmp_path: Path,
) -> None:
    from app.agent.tools import ToolRegistry
    from app.core_host.plugin_worker import PluginWorkerClient

    root = tmp_path / "assistant"
    plugins_root = root / "plugins"
    plugins_root.mkdir(parents=True)
    (plugins_root / "__init__.py").write_text("", encoding="utf-8")
    repository_root = Path(__file__).parents[2]
    shutil.copytree(
        repository_root / "plugins" / "sakura_tts_hub",
        plugins_root / "sakura_tts_hub",
    )
    provider_root = plugins_root / "instant_tts"
    provider_root.mkdir()
    (provider_root / "__init__.py").write_text("", encoding="utf-8")
    (provider_root / "plugin.yaml").write_text(
        """
api: 3
id: com.example.instant-tts
name: Instant TTS
version: 0.1.0
entry: plugin:InstantTTSPlugin
provides: []
requires: [sakura.tts, sakura.host.artifacts]
optional: []
""".strip(),
        encoding="utf-8",
    )
    (provider_root / "plugin.py").write_text(
        """
import wave
from pathlib import Path

class InstantProvider:
    def __init__(self, artifacts):
        self.artifacts = artifacts

    def status(self):
        return {"available": True}

    def synthesize(self, request):
        assert request["characterId"] == "sakura"
        assert request["text"] == "こんにちは"
        allocated = self.artifacts.allocate({"mediaType": "audio/wav", "suffix": ".wav"})
        with wave.open(allocated["path"], "wb") as handle:
            handle.setnchannels(1)
            handle.setsampwidth(2)
            handle.setframerate(16000)
            handle.writeframes(b"\\x01\\x00" * 160)
        return self.artifacts.commit(allocated["artifactId"])

class InstantTTSPlugin:
    def setup(self, context):
        hub = context.get("sakura.tts")
        artifacts = context.get("sakura.host.artifacts")
        context.effect(hub.registerProvider("com.example.instant-tts", InstantProvider(artifacts)))
""".strip(),
        encoding="utf-8",
    )
    character_root = root / "characters" / "sakura"
    character_root.mkdir(parents=True)
    (character_root / "card.md").write_text("sakura", encoding="utf-8")
    (character_root / "portrait.png").write_bytes(b"portrait")
    (character_root / "character.json").write_text(
        json.dumps(
            {
                "id": "sakura",
                "display_name": "Sakura",
                "card": "card.md",
                "portrait": {"default": "portrait.png"},
                "extensions": {
                    "sakura.tts": {"provider": "com.example.instant-tts"},
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    class Runtime:
        def set_prompt_patches(self, _values) -> None:
            pass

        def set_context_providers(self, _values) -> None:
            pass

    worker = PluginWorkerClient(root, GENERATION)
    worker.configure_host_services(ToolRegistry(), Runtime())
    session = SimpleNamespace(plugin_worker=worker, character=SimpleNamespace(id="sakura"))
    boundary = TTSBoundary(
        GENERATION,
        CREDENTIAL,
        root,
        session_provider=lambda: session,
        synthesis_factory=lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("legacy TTS must not be constructed")
        ),
    )
    try:
        worker.start()
        snapshot = worker.wait_until_loaded(timeout=5)
        by_id = {item["pluginId"]: item for item in snapshot["plugins"]}
        assert by_id["sakura.tts"]["state"] == "active"
        assert by_id["com.example.instant-tts"]["state"] == "active"
        status = worker.call_service("sakura.tts", "status", "sakura")
        assert status["providerId"] == "com.example.instant-tts"
        assert status["available"] is True

        boundary.authorize_segment(
            operation_id="operation-hub",
            segment_index=0,
            text="こんにちは",
            tone="happy",
            portrait="smile",
            character_id="sakura",
            history_entry_id="entry-hub",
        )
        result = boundary.handle(
            _request(
                "tts.synthesis.start",
                {"operationId": "operation-hub", "segmentIndex": 0},
            )
        )
        assert result["ok"] is True
        descriptor = result["payload"]
        recording_root = (
            root
            / "data"
            / "voice"
            / "recordings"
            / "sakura"
            / descriptor["recordingId"]
        )
        metadata = json.loads((recording_root / "record.json").read_text(encoding="utf-8"))
        assert metadata["provider"] == "com.example.instant-tts"
        assert metadata["historyEntryId"] == "entry-hub"
        assert getattr(worker._host_services, "artifact_count") == 0

        disabled = worker.set_plugin_enabled("com.example.instant-tts", False)
        disabled_by_id = {item["pluginId"]: item for item in disabled["plugins"]}
        assert disabled_by_id["com.example.instant-tts"]["state"] == "disabled"
        unavailable = worker.call_service("sakura.tts", "status", "sakura")
        assert unavailable["configured"] is True
        assert unavailable["available"] is False
        boundary.authorize_segment(
            operation_id="operation-no-fallback",
            segment_index=0,
            text="こんにちは",
            tone="happy",
            portrait="smile",
            character_id="sakura",
            history_entry_id="entry-no-fallback",
        )
        failed = boundary.handle(
            _request(
                "tts.synthesis.start",
                {"operationId": "operation-no-fallback", "segmentIndex": 0},
                request_id="request-no-fallback",
            )
        )
        assert failed["ok"] is False
        assert failed["error"]["code"] == "TTS_SERVICE_UNAVAILABLE"
    finally:
        boundary.close()
        worker.close()


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
    events: list[dict] = []
    boundary = _boundary(tmp_path, events)
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
    assert events == []


def test_bundle_completion_is_polled_without_events_from_completed_request(
    tmp_path: Path, monkeypatch
) -> None:
    from app.core_host import tts_boundary
    from app.voice import tts_bundle

    release = threading.Event()
    log_started = threading.Event()
    release_log = threading.Event()
    entry = SimpleNamespace(
        key="fixture-bundle",
        label="Fixture Bundle",
        provider="gpt-sovits",
        size=10,
    )

    def fake_install(_entry, root, *, check_cancel, on_progress):
        on_progress(40)
        assert release.wait(1)
        check_cancel()
        return SimpleNamespace(
            provider="gpt-sovits",
            work_dir=root / "tts" / "gpt",
            python_path=root / "tts" / "gpt" / "runtime" / "python.exe",
            tts_config_path=None,
        )

    monkeypatch.setattr(tts_bundle, "compatible_tts_bundles", lambda: (entry,))
    monkeypatch.setattr(tts_bundle, "install_tts_bundle", fake_install)
    monkeypatch.setattr(tts_bundle, "default_bundle_work_dir", lambda _entry, root: root / "missing")
    real_log_event = tts_boundary.log_event

    def blocking_log_event(*args, **kwargs):
        if kwargs.get("event") == "tts.bundle.completed":
            log_started.set()
            assert release_log.wait(1)
        return real_log_event(*args, **kwargs)

    monkeypatch.setattr(tts_boundary, "log_event", blocking_log_event)
    events: list[dict] = []
    boundary = _boundary(tmp_path, events)

    install = boundary.handle(_request("tts.bundle.install", {"bundleKey": entry.key}))
    assert install["ok"] is True
    release.set()
    assert log_started.wait(1)

    running = boundary.handle(
        _request("tts.bundle.status", {}, request_id="bundle-running-status")
    )
    assert running["payload"]["activeTask"]["state"] == "running"
    assert running["payload"]["activeTask"]["result"] is None
    release_log.set()

    deadline = time.monotonic() + 1
    while True:
        status = boundary.handle(
            _request("tts.bundle.status", {}, request_id="bundle-completed-status")
        )
        if status["payload"]["activeTask"]["state"] == "completed":
            break
        assert time.monotonic() < deadline
        time.sleep(0.01)

    assert status["payload"]["activeTask"]["progress"] == 100
    assert events == []


def test_voice_status_does_not_pair_a_preinstall_scan_with_completed_task(
    tmp_path: Path, monkeypatch
) -> None:
    from app.core_host.tts_boundary import _BundleTask
    from app.voice import tts_bundle

    entry = SimpleNamespace(
        key="fixture-bundle",
        label="Fixture Bundle",
        provider="gpt-sovits",
        size=10,
    )
    scan_started = threading.Event()
    release_scan = threading.Event()
    task_completed = threading.Event()

    class ProbePath:
        def is_dir(self) -> bool:
            scan_started.set()
            assert release_scan.wait(1)
            return False

    monkeypatch.setattr(tts_bundle, "TTS_BUNDLES", (entry,))
    monkeypatch.setattr(tts_bundle, "compatible_tts_bundles", lambda: (entry,))
    monkeypatch.setattr(tts_bundle, "recommend_tts_bundle", lambda: None)
    monkeypatch.setattr(tts_bundle, "default_bundle_work_dir", lambda _entry, _root: ProbePath())
    boundary = _boundary(tmp_path, [])
    boundary._load_settings = lambda **_kwargs: SimpleNamespace(  # type: ignore[method-assign]
        enabled=False,
        provider="gpt-sovits",
        custom_base_url=None,
        work_dir=None,
    )
    boundary._bundle_task = _BundleTask(
        "task-1", entry.key, threading.Event(), state="running"
    )

    result: dict[str, dict] = {}

    def read_status() -> None:
        result["status"] = boundary.handle(
            _request("tts.status.get", {}, request_id="status-race")
        )

    status_thread = threading.Thread(target=read_status)
    status_thread.start()
    assert scan_started.wait(1)

    def complete_task() -> None:
        with boundary._lock:
            assert boundary._bundle_task is not None
            boundary._bundle_task.state = "completed"
            task_completed.set()

    completion_thread = threading.Thread(target=complete_task)
    completion_thread.start()
    assert not task_completed.wait(0.1)
    release_scan.set()
    status_thread.join(timeout=1)
    completion_thread.join(timeout=1)

    assert result["status"]["payload"]["activeTask"]["state"] == "running"


def test_voice_status_reports_installed_bundle_with_completed_task(tmp_path: Path, monkeypatch) -> None:
    from app.voice import tts_bundle

    entry = SimpleNamespace(
        key="fixture-bundle",
        label="Fixture Bundle",
        provider="gpt-sovits",
        size=10,
    )
    installed_dir = tmp_path / "tts" / "fixture"

    def fake_install(_entry, _root, *, check_cancel, on_progress):
        check_cancel()
        on_progress(100)
        installed_dir.mkdir(parents=True)
        return SimpleNamespace(
            provider="gpt-sovits",
            work_dir=installed_dir,
            python_path=None,
            tts_config_path=None,
        )

    monkeypatch.setattr(tts_bundle, "TTS_BUNDLES", (entry,))
    monkeypatch.setattr(tts_bundle, "compatible_tts_bundles", lambda: (entry,))
    monkeypatch.setattr(tts_bundle, "recommend_tts_bundle", lambda: None)
    monkeypatch.setattr(tts_bundle, "install_tts_bundle", fake_install)
    monkeypatch.setattr(tts_bundle, "default_bundle_work_dir", lambda _entry, _root: installed_dir)
    boundary = _boundary(tmp_path, [])
    boundary._load_settings = lambda **_kwargs: SimpleNamespace(  # type: ignore[method-assign]
        enabled=False,
        provider="gpt-sovits",
        custom_base_url=None,
        work_dir=None,
    )

    install = boundary.handle(_request("tts.bundle.install", {"bundleKey": entry.key}))
    assert install["ok"] is True
    deadline = time.monotonic() + 1
    while True:
        status = boundary.handle(
            _request("tts.status.get", {}, request_id="voice-status-completed")
        )
        if status["payload"]["activeTask"]["state"] == "completed":
            break
        assert time.monotonic() < deadline
        time.sleep(0.01)

    assert status["payload"]["bundles"][0]["installed"] is True
    assert status["payload"]["providers"][0]["availability"] == "installed"


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
        "gpt-sovits", "genie-tts",
    }
    assert payload["runtime"]["endpointKind"] == "managed"
    assert "path" not in json.dumps(payload).lower()
    assert created == []


def test_gpt_settings_draft_derives_endpoint_without_deployment_mode(tmp_path: Path) -> None:
    boundary = _boundary(tmp_path, [])
    reference = tmp_path / "reference.wav"
    reference.write_bytes(b"reference")
    current = GPTSoVITSTTSSettings(
        enabled=False,
        provider="gpt-sovits",
        api_url="http://127.0.0.1:9880/tts",
        ref_audio_path=reference,
        ref_text_path=reference,
        ref_text="reference",
    )
    draft = {
        "enabled": True,
        "provider": "gpt-sovits",
        "apiUrl": "http://127.0.0.1:9880/tts",
        "customBaseUrl": "https://tts.example.com/",
        "ttsPath": "api/tts",
        "remoteReferenceRoot": "/data/voices",
        "workDir": "",
        "pythonPath": "",
        "timeoutSeconds": 45,
    }

    updated = boundary._settings_from_draft(current, draft, validate=False)

    assert updated.provider == "gpt-sovits"
    assert updated.custom_base_url == "https://tts.example.com"
    assert updated.tts_path == "/api/tts"
    assert updated.api_url == "https://tts.example.com/api/tts"
    assert updated.remote_reference_root == "/data/voices"
    assert boundary._endpoint_kind_for_settings(updated) == "custom"


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
