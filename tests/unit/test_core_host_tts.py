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

import pytest

from app.core_host.plugin_artifacts import PluginArtifactStore
from app.core_host.tts_boundary import TTSBoundary


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


def test_runtime_v2_tts_cutover_keeps_provider_implementations_out_of_core_bridge() -> None:
    repository = Path(__file__).parents[2]
    core_source = "\n".join(
        (repository / path).read_text(encoding="utf-8")
        for path in ("app/core_host/tts_boundary.py", "app/core_host/server.py")
    )
    for forbidden in (
        "gpt-sovits",
        "genie-tts",
        "tts_synthesis_service",
        "tts_bundle",
        "load_tts_settings",
        "on_session_ready",
        "tts.settings.test",
        "tts.bundle.",
    ):
        assert forbidden not in core_source

    generic_bridge = "\n".join(
        (repository / path).read_text(encoding="utf-8")
        for path in (
            "app/plugins/kernel.py",
            "app/core_host/plugin_host_services.py",
            "app/core_host/plugin_worker.py",
            "app/core_host/plugin_worker_runtime.py",
        )
    )
    for forbidden in (
        "sakura.tts",
        "tts.start",
        "tts.end",
        "gpt-sovits",
        "genie-tts",
        "tts.settings.test",
        "tts.bundle.",
    ):
        assert forbidden not in generic_bridge

    shell_source = "\n".join(
        (repository / path).read_text(encoding="utf-8")
        for path in (
            "desktop/src-tauri/src/main.rs",
            "desktop/frontend/settings/voice-runtime.js",
        )
    )
    for forbidden in (
        "gpt-sovits",
        "genie-tts",
        "custom-gpt-sovits",
        "sakura.tts",
        "settings_voice_test",
        "settings_voice_bundle",
        "tts.settings.test",
        "tts.bundle.",
    ):
        assert forbidden not in shell_source

    for managed_runtime in (
        "plugins/sakura_genie/plugin.py",
        "app/voice/tts_service.py",
    ):
        assert "start_new_session" not in (
            repository / managed_runtime
        ).read_text(encoding="utf-8")

    product_shell = (
        repository / "desktop/src-tauri/src/product_shell.rs"
    ).read_text(encoding="utf-8")
    assert '("voice.bundle".to_string(), "unavailable".to_string())' in product_shell


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


class _ImmediatePluginWorker:
    provider_id = "com.example.instant-tts"

    def __init__(self, root: Path) -> None:
        self.store = PluginArtifactStore(root, GENERATION)
        self.jobs: dict[str, dict[str, object]] = {}
        self.calls: list[str] = []
        self.events: list[tuple[str, dict[str, object]]] = []

    def call_service(self, service_key: str, method: str, *args):
        assert service_key == "sakura.tts"
        self.calls.append(method)
        if method == "begin":
            request = args[0]
            request_id = request["requestId"]
            allocated = self.store.allocate(
                self.provider_id,
                {"mediaType": "audio/wav", "suffix": ".wav"},
            )
            _write_wav(Path(allocated["path"]))
            self.jobs[request_id] = self.store.commit(
                self.provider_id,
                allocated["artifactId"],
            )
            return {
                "state": "running",
                "requestId": request_id,
                "providerId": self.provider_id,
            }
        if method == "poll":
            request_id = args[0]
            return {
                "state": "succeeded",
                "requestId": request_id,
                "providerId": self.provider_id,
                "artifact": self.jobs[request_id],
            }
        if method == "cancel":
            return {"accepted": args[0] in self.jobs, "requestId": args[0]}
        raise AssertionError(f"unexpected service method: {method}")

    def resolve_committed_artifact(self, artifact_id: str):
        return self.store.resolve_committed_by_id(artifact_id)

    def release_committed_artifact(self, artifact_id: str) -> bool:
        artifact = self.store.resolve_committed_by_id(artifact_id)
        return self.store.release(artifact.plugin_id, artifact_id)

    def emit_event(self, event_name: str, payload: dict[str, object]) -> None:
        self.events.append((event_name, dict(payload)))


def test_voice_settings_report_partial_provider_save_without_claiming_atomicity(
    tmp_path: Path,
) -> None:
    class Worker:
        def __init__(self) -> None:
            self.saved: list[tuple[str, str, dict[str, object]]] = []
            self.configure_calls = 0

        def settings_sections(self, surface: str) -> list[dict[str, object]]:
            assert surface == "voice"
            return [
                {"pluginId": "com.example.first", "sectionId": "runtime"},
                {"pluginId": "com.example.second", "sectionId": "runtime"},
            ]

        def settings_save(self, plugin_id: str, section_id: str, values) -> dict[str, str]:
            if plugin_id == "com.example.second":
                raise RuntimeError("injected second section failure")
            self.saved.append((plugin_id, section_id, dict(values)))
            return {"applicationState": "restart_required"}

        def call_service(self, service_key: str, method: str, *args):
            assert service_key == "sakura.tts"
            if method == "configure":
                self.configure_calls += 1
                return {"configured": True}
            assert method == "status"
            return {
                "configured": True,
                "enabled": True,
                "providerId": "com.example.first",
                "available": True,
                "providers": [{
                    "providerId": "com.example.first",
                    "label": "First",
                    "available": True,
                }],
            }

    worker = Worker()
    character = SimpleNamespace(id="alpha", display_name="Alpha")
    boundary = TTSBoundary(
        GENERATION,
        CREDENTIAL,
        tmp_path,
        session_provider=lambda: SimpleNamespace(plugin_worker=worker, character=character),
    )
    result = boundary.handle(
        _request(
            "tts.settings.save",
            {"settings": {
                "characterId": "alpha",
                "enabled": False,
                "providerId": "com.example.first",
                "sections": [
                    {
                        "pluginId": "com.example.first",
                        "sectionId": "runtime",
                        "values": {"timeoutSeconds": 90},
                    },
                    {
                        "pluginId": "com.example.second",
                        "sectionId": "runtime",
                        "values": {"timeoutSeconds": 120},
                    },
                ],
            }},
            request_id="voice-partial-save",
        )
    )

    assert result["ok"] is True
    assert result["payload"]["saveState"] == "partial"
    assert result["payload"]["savedSections"] == [{
        "pluginId": "com.example.first",
        "sectionId": "runtime",
    }]
    assert result["payload"]["selectionSaved"] is False
    assert result["payload"]["applicationState"] == "restart_required"
    assert result["payload"]["reasonCode"] == "TTS_PROVIDER_SETTINGS_SAVE_FAILED"
    assert worker.saved == [
        ("com.example.first", "runtime", {"timeoutSeconds": 90})
    ]
    assert worker.configure_calls == 0


def test_voice_settings_strip_generic_surface_routing_metadata(tmp_path: Path) -> None:
    class Worker:
        def call_service(self, service_key: str, method: str, *args):
            assert service_key == "sakura.tts"
            assert method == "status"
            assert args == ("alpha",)
            return {
                "configured": True,
                "enabled": True,
                "providerId": "com.example.first",
                "available": True,
                "providers": [{
                    "providerId": "com.example.first",
                    "label": "First",
                    "available": True,
                }],
            }

        def settings_sections(self, surface: str) -> list[dict[str, object]]:
            assert surface == "voice"
            return [{
                "pluginId": "com.example.first",
                "sectionId": "runtime",
                "title": "First Provider",
                "surface": "voice",
                "reasonCode": "READY",
                "fields": [],
                "values": {},
                "actions": [],
                "collections": [],
            }]

    boundary = TTSBoundary(
        GENERATION,
        CREDENTIAL,
        tmp_path,
        session_provider=lambda: SimpleNamespace(
            plugin_worker=Worker(),
            character=SimpleNamespace(id="alpha", display_name="Alpha"),
        ),
    )

    result = boundary.handle(
        _request("tts.settings.get", {}, request_id="voice-strip-surface")
    )

    assert result["ok"] is True
    assert result["payload"]["sections"] == [{
        "pluginId": "com.example.first",
        "sectionId": "runtime",
        "title": "First Provider",
        "reasonCode": "READY",
        "fields": [],
        "values": {},
        "actions": [],
        "collections": [],
    }]


def test_voice_settings_validate_all_sections_before_the_first_write(tmp_path: Path) -> None:
    class Worker:
        def __init__(self) -> None:
            self.saved = 0

        def settings_sections(self, _surface: str) -> list[dict[str, str]]:
            return [{"pluginId": "com.example.first", "sectionId": "runtime"}]

        def settings_save(self, *_args) -> dict[str, str]:
            self.saved += 1
            return {"applicationState": "applied"}

    worker = Worker()
    boundary = TTSBoundary(
        GENERATION,
        CREDENTIAL,
        tmp_path,
        session_provider=lambda: SimpleNamespace(
            plugin_worker=worker,
            character=SimpleNamespace(id="alpha", display_name="Alpha"),
        ),
    )
    result = boundary.handle(
        _request(
            "tts.settings.save",
            {"settings": {
                "characterId": "alpha",
                "enabled": True,
                "providerId": "com.example.first",
                "sections": [
                    {
                        "pluginId": "com.example.first",
                        "sectionId": "runtime",
                        "values": {},
                    },
                    {
                        "pluginId": "com.example.unknown",
                        "sectionId": "runtime",
                        "values": {},
                    },
                ],
            }},
            request_id="voice-invalid-late-section",
        )
    )

    assert result["ok"] is False
    assert result["error"]["code"] == "INVALID_TTS_SETTINGS"
    assert worker.saved == 0


def test_voice_settings_report_partial_when_character_selection_save_fails(
    tmp_path: Path,
) -> None:
    class Worker:
        def settings_sections(self, _surface: str) -> list[dict[str, str]]:
            return [{"pluginId": "com.example.first", "sectionId": "runtime"}]

        def settings_save(self, *_args) -> dict[str, str]:
            return {"applicationState": "applied"}

        def call_service(self, _service_key: str, method: str, *args):
            if method == "configure":
                raise RuntimeError("injected character write failure")
            return {
                "configured": True,
                "enabled": True,
                "providerId": "com.example.first",
                "available": True,
                "providers": [],
            }

    boundary = TTSBoundary(
        GENERATION,
        CREDENTIAL,
        tmp_path,
        session_provider=lambda: SimpleNamespace(
            plugin_worker=Worker(),
            character=SimpleNamespace(id="alpha", display_name="Alpha"),
        ),
    )
    result = boundary.handle(
        _request(
            "tts.settings.save",
            {"settings": {
                "characterId": "alpha",
                "enabled": False,
                "providerId": "com.example.first",
                "sections": [{
                    "pluginId": "com.example.first",
                    "sectionId": "runtime",
                    "values": {"timeoutSeconds": 90},
                }],
            }},
            request_id="voice-selection-partial",
        )
    )

    assert result["ok"] is True
    assert result["payload"]["saveState"] == "partial"
    assert result["payload"]["selectionSaved"] is False
    assert result["payload"]["reasonCode"] == "TTS_SELECTION_SAVE_FAILED"


def _boundary(tmp_path: Path, events: list[dict]) -> TTSBoundary:
    worker = _ImmediatePluginWorker(tmp_path)
    boundary = TTSBoundary(
        GENERATION,
        CREDENTIAL,
        tmp_path,
        session_provider=lambda: SimpleNamespace(
            plugin_worker=worker,
            character=SimpleNamespace(id="sakura"),
        ),
        event_publisher=events.append,
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
""".strip(),
        encoding="utf-8",
    )
    (provider_root / "plugin.py").write_text(
        """
import threading
import time
import wave

class InstantJob:
    def __init__(self, context, artifacts, request):
        self.context = context
        self.artifacts = artifacts
        self.request = request
        self.cancelled = threading.Event()
        self.done = threading.Event()
        self.failed = False
        self.committed = False
        self.allocated = artifacts.allocate({"mediaType": "audio/wav", "suffix": ".wav"})
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()
        self.dispose_effect = context.effect(self.close)

    def _run(self):
        if self.cancelled.wait(0.3):
            self.done.set()
            return
        try:
            with wave.open(self.allocated["path"], "wb") as handle:
                handle.setnchannels(1)
                handle.setsampwidth(2)
                handle.setframerate(16000)
                handle.writeframes(b"\\x01\\x00" * 160)
        except Exception:
            self.failed = True
        finally:
            self.done.set()

    def poll(self):
        if not self.done.is_set():
            return {"state": "running"}
        if self.cancelled.is_set():
            self.dispose_effect()
            return {"state": "cancelled"}
        if self.failed:
            self.dispose_effect()
            return {"state": "failed", "errorCode": "TTS_SYNTHESIS_FAILED"}
        artifact = self.artifacts.commit(self.allocated["artifactId"])
        self.committed = True
        self.dispose_effect()
        return {"state": "succeeded", "artifact": artifact}

    def cancel(self):
        accepted = not self.done.is_set()
        self.cancelled.set()
        return accepted

    def close(self):
        self.cancelled.set()
        if self.thread is not threading.current_thread():
            self.thread.join(timeout=1)
        if not self.committed:
            self.artifacts.release(self.allocated["artifactId"])

class InstantProvider:
    def __init__(self, context, artifacts):
        self.context = context
        self.artifacts = artifacts

    def status(self):
        return {"available": True}

    def begin(self, request):
        assert request["characterId"] == "sakura"
        assert request["text"] == "こんにちは"
        return InstantJob(self.context, self.artifacts, request)

class InstantTTSPlugin:
    def setup(self, context):
        hub = context.get("sakura.tts")
        artifacts = context.get("sakura.host.artifacts")
        context.effect(
            hub.registerProvider(
                "com.example.instant-tts",
                InstantProvider(context, artifacts),
            )
        )
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
                    "sakura.tts": {
                        "enabled": True,
                        "provider": "com.example.instant-tts",
                    },
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

    worker = PluginWorkerClient(root, GENERATION, call_timeout=0.1)
    worker.configure_host_services(ToolRegistry(), Runtime())
    session = SimpleNamespace(plugin_worker=worker, character=SimpleNamespace(id="sakura"))
    boundary = TTSBoundary(
        GENERATION,
        CREDENTIAL,
        root,
        session_provider=lambda: session,
    )
    try:
        worker.start()
        snapshot = worker.wait_until_loaded(timeout=5)
        by_id = {item["pluginId"]: item for item in snapshot["plugins"]}
        assert by_id["sakura.tts"]["state"] == "active"
        assert by_id["com.example.instant-tts"]["state"] == "active"
        status = worker.call_service("sakura.tts", "status", "sakura")
        assert status["enabled"] is True
        assert status["providerId"] == "com.example.instant-tts"
        assert status["available"] is True

        settings_snapshot = boundary.handle(
            _request("tts.settings.get", {}, request_id="settings-dynamic-get")
        )
        assert settings_snapshot["ok"] is True
        assert settings_snapshot["payload"]["schemaVersion"] == 2
        assert settings_snapshot["payload"]["character"] == {
            "characterId": "sakura",
            "displayName": "sakura",
        }
        assert settings_snapshot["payload"]["providers"] == [{
            "providerId": "com.example.instant-tts",
            "label": "com.example.instant-tts",
            "available": True,
        }]
        saved_disabled = boundary.handle(
            _request(
                "tts.settings.save",
                {"settings": {
                    "characterId": "sakura",
                    "enabled": False,
                    "providerId": "com.example.instant-tts",
                    "sections": [],
                }},
                request_id="settings-dynamic-disable",
            )
        )
        assert saved_disabled["ok"] is True
        assert saved_disabled["payload"]["applicationState"] == "applied"
        assert saved_disabled["payload"]["saveState"] == "complete"
        assert saved_disabled["payload"]["savedSections"] == []
        assert saved_disabled["payload"]["selectionSaved"] is True
        assert saved_disabled["payload"]["reasonCode"] == "READY"
        disabled_status = worker.call_service("sakura.tts", "status", "sakura")
        assert disabled_status["configured"] is True
        assert disabled_status["enabled"] is False
        assert disabled_status["providerId"] == "com.example.instant-tts"
        assert disabled_status["available"] is False
        disabled_begin = worker.call_service(
            "sakura.tts",
            "begin",
            {
                "requestId": "disabled-by-character",
                "characterId": "sakura",
                "text": "こんにちは",
                "options": {},
            },
        )
        assert disabled_begin == {
            "state": "failed",
            "requestId": "disabled-by-character",
            "providerId": "com.example.instant-tts",
            "errorCode": "TTS_DISABLED",
        }
        saved_enabled = boundary.handle(
            _request(
                "tts.settings.save",
                {"settings": {
                    "characterId": "sakura",
                    "enabled": True,
                    "providerId": "com.example.instant-tts",
                    "sections": [],
                }},
                request_id="settings-dynamic-enable",
            )
        )
        assert saved_enabled["ok"] is True
        restored_status = worker.call_service("sakura.tts", "status", "sakura")
        assert restored_status["available"] is True

        boundary.authorize_segment(
            operation_id="operation-hub",
            segment_index=0,
            text="こんにちは",
            tone="happy",
            portrait="smile",
            character_id="sakura",
            history_entry_id="entry-hub",
        )
        first_token = worker._token
        started_at = time.monotonic()
        result = boundary.handle(
            _request(
                "tts.synthesis.start",
                {"operationId": "operation-hub", "segmentIndex": 0},
            )
        )
        assert time.monotonic() - started_at >= 0.2
        assert worker._token == first_token
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
        assert worker._request("status.get", {})["state"] == "ready"

        boundary.authorize_segment(
            operation_id="operation-cancel",
            segment_index=0,
            text="こんにちは",
            tone="happy",
            portrait="smile",
            character_id="sakura",
            history_entry_id="entry-cancel",
        )
        boundary.authorize_segment(
            operation_id="operation-cancel",
            segment_index=1,
            text="こんにちは",
            tone="happy",
            portrait="smile",
            character_id="sakura",
            history_entry_id="entry-cancel-pending",
        )
        boundary.authorize_segment(
            operation_id="operation-concurrent",
            segment_index=0,
            text="こんにちは",
            tone="happy",
            portrait="smile",
            character_id="sakura",
            history_entry_id="entry-concurrent",
        )
        original_call_service = worker.call_service
        begin_returned = threading.Event()
        release_begin = threading.Event()

        def pause_after_begin(service_key, method, *args):
            value = original_call_service(service_key, method, *args)
            if service_key == "sakura.tts" and method == "begin":
                begin_returned.set()
                release_begin.wait(1)
            return value

        worker.call_service = pause_after_begin  # type: ignore[method-assign]
        cancelled_result: dict[str, object] = {}
        concurrent_result: dict[str, object] = {}

        def synthesize_cancelled() -> None:
            cancelled_result.update(
                boundary.handle(
                    _request(
                        "tts.synthesis.start",
                        {"operationId": "operation-cancel", "segmentIndex": 0},
                        request_id="request-start-cancel",
                    )
                )
            )

        synthesis_thread = threading.Thread(target=synthesize_cancelled)
        synthesis_thread.start()
        assert begin_returned.wait(1)

        def synthesize_concurrent() -> None:
            concurrent_result.update(
                boundary.handle(
                    _request(
                        "tts.synthesis.start",
                        {"operationId": "operation-concurrent", "segmentIndex": 0},
                        request_id="request-start-concurrent",
                    )
                )
            )

        concurrent_thread = threading.Thread(target=synthesize_concurrent)
        concurrent_thread.start()
        deadline = time.monotonic() + 1
        while getattr(worker._host_services, "artifact_count") != 2:
            assert time.monotonic() < deadline
            time.sleep(0.01)
        cancelled = boundary.handle(
            _request(
                "tts.synthesis.cancel",
                {"operationId": "operation-cancel"},
                request_id="request-cancel",
            )
        )
        assert cancelled["ok"] is True
        assert cancelled["payload"] == {
            "accepted": True,
            "operationId": "operation-cancel",
        }
        pending_after_cancel = boundary.handle(
            _request(
                "tts.synthesis.start",
                {"operationId": "operation-cancel", "segmentIndex": 1},
                request_id="request-start-cancelled-pending",
            )
        )
        assert pending_after_cancel["ok"] is False
        assert pending_after_cancel["error"]["code"] == "TTS_SEGMENT_NOT_AUTHORIZED"
        release_begin.set()
        synthesis_thread.join(2)
        concurrent_thread.join(2)
        worker.call_service = original_call_service  # type: ignore[method-assign]
        assert not synthesis_thread.is_alive()
        assert not concurrent_thread.is_alive()
        assert cancelled_result["ok"] is False
        assert cancelled_result["error"]["code"] == "TTS_SYNTHESIS_CANCELLED"
        assert concurrent_result["ok"] is True
        assert getattr(worker._host_services, "artifact_count") == 0
        assert worker._request("status.get", {})["state"] == "ready"

        boundary.authorize_segment(
            operation_id="operation-disable",
            segment_index=0,
            text="こんにちは",
            tone="happy",
            portrait="smile",
            character_id="sakura",
            history_entry_id="entry-disable",
        )
        disabled_result: dict[str, object] = {}

        def synthesize_during_disable() -> None:
            disabled_result.update(
                boundary.handle(
                    _request(
                        "tts.synthesis.start",
                        {"operationId": "operation-disable", "segmentIndex": 0},
                        request_id="request-start-disable",
                    )
                )
            )

        disable_thread = threading.Thread(target=synthesize_during_disable)
        disable_thread.start()
        deadline = time.monotonic() + 1
        while getattr(worker._host_services, "artifact_count") != 1:
            assert time.monotonic() < deadline
            time.sleep(0.01)
        disabled = worker.set_plugin_enabled("com.example.instant-tts", False)
        disable_thread.join(2)
        assert not disable_thread.is_alive()
        assert disabled_result["ok"] is False
        assert disabled_result["error"]["code"] == "TTS_SERVICE_UNAVAILABLE"
        assert getattr(worker._host_services, "artifact_count") == 0
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
    worker = _ImmediatePluginWorker(tmp_path)
    boundary = TTSBoundary(
        GENERATION,
        CREDENTIAL,
        tmp_path,
        session_provider=lambda: SimpleNamespace(
            plugin_worker=worker,
            character=SimpleNamespace(id="sakura"),
        ),
        event_publisher=events.append,
        recording_store=FailingRecordingStore(),  # type: ignore[arg-type]
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
    request = _request("tts.status.get", {})
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
            "sakura.host.tts.started",
            {"playbackId": "playback-1", "recordingId": "recording-1", "outcome": "started"},
        ),
        (
            "sakura.host.tts.ended",
            {"playbackId": "playback-1", "recordingId": "recording-1", "outcome": "finished"},
        ),
    ]


@pytest.mark.parametrize(
    ("mode", "hub_error_code"),
    [
        ("worker-missing", None),
        ("hub-missing", "SERVICE_MISSING"),
        ("not-selected", "TTS_PROVIDER_NOT_SELECTED"),
        ("character-disabled", "TTS_DISABLED"),
        ("provider-unavailable", "TTS_PROVIDER_UNAVAILABLE"),
    ],
)
def test_plugin_cutover_never_falls_back_when_tts_is_unavailable(
    tmp_path: Path,
    mode: str,
    hub_error_code: str | None,
) -> None:
    class Worker:
        def __init__(self) -> None:
            self.calls: list[str] = []

        def call_service(self, service_key: str, method: str, request):
            assert service_key == "sakura.tts"
            assert method == "begin"
            self.calls.append(method)
            if mode == "hub-missing":
                error = RuntimeError("missing service")
                error.code = "SERVICE_MISSING"  # type: ignore[attr-defined]
                raise error
            return {
                "state": "failed",
                "requestId": request["requestId"],
                "providerId": None,
                "errorCode": hub_error_code,
            }

    worker = None if mode == "worker-missing" else Worker()
    boundary = TTSBoundary(
        GENERATION,
        CREDENTIAL,
        tmp_path,
        session_provider=lambda: SimpleNamespace(
            plugin_worker=worker,
            character=SimpleNamespace(id="sakura"),
        ),
    )
    boundary.authorize_segment(
        operation_id="operation-no-fallback",
        segment_index=0,
        text="こんにちは",
        tone="happy",
        portrait="smile",
        character_id="sakura",
        history_entry_id="entry-no-fallback",
    )

    result = boundary.handle(
        _request(
            "tts.synthesis.start",
            {"operationId": "operation-no-fallback", "segmentIndex": 0},
        )
    )

    assert result["ok"] is False
    assert result["error"]["code"] == "TTS_SERVICE_UNAVAILABLE"
    assert not (tmp_path / "data" / "voice" / "recordings").exists()
    if worker is not None:
        assert worker.calls == ["begin"]


def test_hub_provider_disposer_keeps_cancelled_job_pollable_until_terminal() -> None:
    from plugins.sakura_tts_hub.plugin import SakuraTTSHub

    class Character:
        def get(self, character_id: str):
            assert character_id == "sakura"
            return {"enabled": True, "provider": "com.example.provider"}

    class Job:
        def __init__(self) -> None:
            self.cancelled = False

        def cancel(self) -> bool:
            self.cancelled = True
            return True

        def poll(self):
            return {"state": "cancelled" if self.cancelled else "running"}

    job = Job()
    provider = SimpleNamespace(
        status=lambda: {"available": True},
        begin=lambda _request: job,
    )
    hub = SakuraTTSHub(Character())
    dispose = hub.registerProvider("com.example.provider", provider)
    started = hub.begin({
        "requestId": "request-dispose",
        "characterId": "sakura",
        "text": "hello",
        "options": {},
    })
    assert started["state"] == "running"

    dispose()

    assert hub.poll("request-dispose")["state"] == "cancelled"
    assert hub.poll("request-dispose")["errorCode"] == "TTS_JOB_NOT_FOUND"


def test_cancel_is_rejected_after_synthesis_enters_recording_commit(tmp_path: Path) -> None:
    events: list[dict] = []
    worker = _ImmediatePluginWorker(tmp_path)
    boundary = TTSBoundary(
        GENERATION,
        CREDENTIAL,
        tmp_path,
        session_provider=lambda: SimpleNamespace(
            plugin_worker=worker,
            character=SimpleNamespace(id="sakura"),
        ),
        event_publisher=events.append,
    )
    boundary.authorize_segment(
        operation_id="operation-commit",
        segment_index=0,
        text="こんにちは",
        tone="happy",
        portrait="smile",
        character_id="sakura",
        history_entry_id="entry-commit",
    )
    commit_started = threading.Event()
    release_commit = threading.Event()
    real_consume = boundary._consume_plugin_audio_artifact

    def paused_consume(*args, **kwargs):
        commit_started.set()
        assert release_commit.wait(1)
        return real_consume(*args, **kwargs)

    boundary._consume_plugin_audio_artifact = paused_consume  # type: ignore[method-assign]
    result: dict[str, object] = {}
    thread = threading.Thread(
        target=lambda: result.update(
            boundary.handle(
                _request(
                    "tts.synthesis.start",
                    {"operationId": "operation-commit", "segmentIndex": 0},
                )
            )
        )
    )
    thread.start()
    assert commit_started.wait(1)

    cancelled = boundary.handle(
        _request(
            "tts.synthesis.cancel",
            {"operationId": "operation-commit"},
            request_id="cancel-during-commit",
        )
    )
    release_commit.set()
    thread.join(2)

    assert not thread.is_alive()
    assert cancelled["payload"]["accepted"] is False
    assert result["ok"] is True
    assert [item["name"] for item in events if item["name"].startswith("tts.synthesis.")] == [
        "tts.synthesis.ready"
    ]
