from __future__ import annotations

import io
import json
import shutil
import threading
import time
import wave
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from typing import Callable

from app.agent.tools import ToolRegistry
from app.core_host.plugin_worker import PluginWorkerClient
from app.core_host.tts_boundary import TTSBoundary


GENERATION = "generation-gpt-plugin"
CREDENTIAL = "4" * 32


def _wav_bytes() -> bytes:
    output = io.BytesIO()
    with wave.open(output, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(16_000)
        handle.writeframes(b"\x01\x00" * 320)
    return output.getvalue()


class _TtsServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self) -> None:
        super().__init__(("127.0.0.1", 0), _TtsHandler)
        self.requests: list[dict[str, object]] = []
        self.get_paths: list[str] = []
        self.active = 0
        self.max_active = 0
        self.lock = threading.Lock()
        self.delay_seconds = 0.08


class _TtsHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        server: _TtsServer = self.server  # type: ignore[assignment]
        with server.lock:
            server.get_paths.append(self.path)
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"ready")

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length", "0"))
        payload = json.loads(self.rfile.read(length).decode("utf-8"))
        server: _TtsServer = self.server  # type: ignore[assignment]
        with server.lock:
            server.active += 1
            server.max_active = max(server.max_active, server.active)
            server.requests.append(payload)
        try:
            time.sleep(server.delay_seconds)
            body = _wav_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "audio/wav")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass
        finally:
            with server.lock:
                server.active -= 1

    def log_message(self, _format: str, *_args: object) -> None:
        return None


class _Runtime:
    def set_prompt_patches(self, _values: object) -> None:
        pass

    def set_context_providers(self, _values: object) -> None:
        pass


def _root(
    tmp_path: Path,
    endpoint: str,
    *,
    config_patch: dict[str, object] | None = None,
) -> Path:
    root = tmp_path / "assistant"
    plugins = root / "plugins"
    plugins.mkdir(parents=True)
    (plugins / "__init__.py").write_text("", encoding="utf-8")
    repository = Path(__file__).parents[2]
    shutil.copytree(repository / "plugins" / "sakura_tts_hub", plugins / "sakura_tts_hub")
    shutil.copytree(
        repository / "plugins" / "sakura_gpt_sovits",
        plugins / "sakura_gpt_sovits",
    )
    plugin_data = root / "data" / "plugins" / "sakura.tts.gpt-sovits"
    plugin_data.mkdir(parents=True)
    config: dict[str, object] = {
        "enabled": True,
        "customBaseUrl": endpoint,
        "ttsPath": "/tts",
        "timeoutSeconds": 5,
    }
    config.update(config_patch or {})
    (plugin_data / "config.json").write_text(
        json.dumps(config),
        encoding="utf-8",
    )
    _write_character(root, "alpha", "alpha reference")
    _write_character(root, "beta", "beta reference")
    return root


def _write_character(root: Path, character_id: str, prompt: str) -> None:
    package = root / "characters" / character_id
    refs = package / "voice" / "refs"
    refs.mkdir(parents=True)
    (package / "card.md").write_text(character_id, encoding="utf-8")
    (package / "portrait.png").write_bytes(b"portrait")
    reference = refs / "neutral.wav"
    reference.write_bytes(_wav_bytes())
    (refs / "ref.txt").write_text(
        f"voice/refs/neutral.wav|JA|{prompt}|中性\n",
        encoding="utf-8",
    )
    (package / "character.json").write_text(
        json.dumps(
            {
                "id": character_id,
                "display_name": character_id,
                "card": "card.md",
                "portrait": {"default": "portrait.png"},
                "extensions": {
                    "sakura.tts": {"provider": "sakura.tts.gpt-sovits"},
                    "sakura.tts.gpt-sovits": {
                        "toneRefs": "voice/refs/ref.txt",
                        "refLang": "ja",
                        "textLang": "ja",
                    },
                },
            }
        ),
        encoding="utf-8",
    )


def _request(name: str, payload: dict[str, object]) -> dict[str, object]:
    return {
        "protocolMajor": 2,
        "protocolMinor": 2,
        "kind": "request",
        "generationId": GENERATION,
        "generationCredential": CREDENTIAL,
        "id": f"request-{time.monotonic_ns()}",
        "name": name,
        "payload": payload,
    }


def _poll_terminal(worker: PluginWorkerClient, request_id: str) -> dict[str, object]:
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        result = worker.call_service("sakura.tts", "poll", request_id)
        assert isinstance(result, dict)
        if result["state"] != "running":
            return result
        time.sleep(0.02)
    raise AssertionError("GPT-SoVITS plugin job did not finish")


def test_real_gpt_sovits_provider_is_character_scoped_serial_and_core_consumed(
    tmp_path: Path,
) -> None:
    server = _TtsServer()
    endpoint = f"http://127.0.0.1:{server.server_port}"
    root = _root(tmp_path, endpoint)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    worker = PluginWorkerClient(root, GENERATION, call_timeout=0.5)
    worker.configure_host_services(ToolRegistry(), _Runtime())
    session = SimpleNamespace(plugin_worker=worker, character=SimpleNamespace(id="alpha"))
    boundary = TTSBoundary(
        GENERATION,
        CREDENTIAL,
        root,
        session_provider=lambda: session,
        synthesis_factory=lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("legacy TTS must not run for an explicit GPT plugin selection")
        ),
    )
    try:
        worker.start()
        snapshot = worker.wait_until_loaded(timeout=5)
        by_id = {item["pluginId"]: item for item in snapshot["plugins"]}
        assert by_id["sakura.tts.gpt-sovits"]["state"] == "active"
        baseline_effects = by_id["sakura.tts.gpt-sovits"]["effectCount"]

        boundary.authorize_segment(
            operation_id="operation-real-gpt",
            segment_index=0,
            text="こんにちは",
            tone="中性",
            portrait="default",
            character_id="alpha",
            history_entry_id="entry-real-gpt",
        )
        result = boundary.handle(
            _request(
                "tts.synthesis.start",
                {"operationId": "operation-real-gpt", "segmentIndex": 0},
            )
        )
        assert result["ok"] is True
        recording = (
            root
            / "data"
            / "voice"
            / "recordings"
            / "alpha"
            / result["payload"]["recordingId"]
            / "record.json"
        )
        metadata = json.loads(recording.read_text(encoding="utf-8"))
        assert metadata["provider"] == "sakura.tts.gpt-sovits"
        assert server.requests[0]["prompt_text"] == "alpha reference"
        assert not any(path.startswith("/set_") for path in server.get_paths)
        assert getattr(worker._host_services, "artifact_count") == 0
        live = worker.refresh_status()
        live_by_id = {item["pluginId"]: item for item in live["plugins"]}
        assert live_by_id["sakura.tts.gpt-sovits"]["effectCount"] == baseline_effects

        first = worker.call_service(
            "sakura.tts",
            "begin",
            {
                "requestId": "job-alpha",
                "characterId": "alpha",
                "text": "alpha",
                "options": {"tone": "中性"},
            },
        )
        second = worker.call_service(
            "sakura.tts",
            "begin",
            {
                "requestId": "job-beta",
                "characterId": "beta",
                "text": "beta",
                "options": {"tone": "中性"},
            },
        )
        assert first["state"] == second["state"] == "running"
        first_terminal = _poll_terminal(worker, "job-alpha")
        second_terminal = _poll_terminal(worker, "job-beta")
        assert first_terminal["state"] == second_terminal["state"] == "succeeded"
        assert server.max_active == 1
        assert [item["text"] for item in server.requests[-2:]] == ["alpha", "beta"]
        worker.release_committed_artifact(first_terminal["artifact"]["artifactId"])
        worker.release_committed_artifact(second_terminal["artifact"]["artifactId"])
    finally:
        boundary.close()
        worker.close()
        server.shutdown()
        server.server_close()
        server_thread.join(1)


def test_disabling_provider_cancels_active_job_releases_artifact_and_can_restore(
    tmp_path: Path,
) -> None:
    server = _TtsServer()
    server.delay_seconds = 2
    root = _root(tmp_path, f"http://127.0.0.1:{server.server_port}")
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    worker = PluginWorkerClient(root, GENERATION, call_timeout=1.5)
    worker.configure_host_services(ToolRegistry(), _Runtime())
    try:
        worker.start()
        worker.wait_until_loaded(timeout=5)
        assert worker.call_service(
            "sakura.tts",
            "begin",
            {
                "requestId": "disable-active",
                "characterId": "alpha",
                "text": "disable active",
                "options": {"tone": "中性"},
            },
        )["state"] == "running"
        deadline = time.monotonic() + 2
        while server.active == 0:
            assert time.monotonic() < deadline
            time.sleep(0.01)

        disabled = worker.set_plugin_enabled("sakura.tts.gpt-sovits", False)
        by_id = {item["pluginId"]: item for item in disabled["plugins"]}
        assert by_id["sakura.tts.gpt-sovits"]["state"] == "disabled"
        assert by_id["sakura.tts"]["state"] == "active"
        assert getattr(worker._host_services, "artifact_count") == 0
        assert worker.call_service("sakura.tts", "poll", "disable-active")[
            "errorCode"
        ] == "TTS_JOB_NOT_FOUND"

        restored = worker.set_plugin_enabled("sakura.tts.gpt-sovits", True)
        restored_by_id = {item["pluginId"]: item for item in restored["plugins"]}
        assert restored_by_id["sakura.tts.gpt-sovits"]["state"] == "active"
        assert worker.call_service("sakura.tts", "status", "alpha")["available"] is True
    finally:
        worker.close()
        server.shutdown()
        server.server_close()
        server_thread.join(1)


def test_invalid_provider_config_stays_active_but_reports_unavailable(tmp_path: Path) -> None:
    root = _root(
        tmp_path,
        "http://127.0.0.1:1",
        config_patch={"timeoutSeconds": True},
    )
    worker = PluginWorkerClient(root, GENERATION, call_timeout=0.5)
    worker.configure_host_services(ToolRegistry(), _Runtime())
    try:
        worker.start()
        snapshot = worker.wait_until_loaded(timeout=5)
        by_id = {item["pluginId"]: item for item in snapshot["plugins"]}
        assert by_id["sakura.tts.gpt-sovits"]["state"] == "active"
        status = worker.call_service("sakura.tts", "status", "alpha")
        assert status["configured"] is True
        assert status["providerId"] == "sakura.tts.gpt-sovits"
        assert status["available"] is False
    finally:
        worker.close()


class _EffectContext:
    def __init__(self) -> None:
        self.effects: list[Callable[[], None]] = []

    def effect(self, cleanup: Callable[[], None]) -> Callable[[], None]:
        active = True
        self.effects.append(cleanup)

        def dispose() -> None:
            nonlocal active
            if not active:
                return
            active = False
            cleanup()

        return dispose


class _LocalArtifacts:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.values: dict[str, Path] = {}
        self.sequence = 0

    def allocate(self, _descriptor: object) -> dict[str, object]:
        self.sequence += 1
        artifact_id = f"artifact_{self.sequence}"
        directory = self.root / artifact_id
        directory.mkdir(parents=True)
        path = directory / "payload.wav"
        self.values[artifact_id] = path
        return {"artifactId": artifact_id, "path": str(path), "mediaType": "audio/wav"}

    def commit(self, artifact_id: str) -> dict[str, object]:
        path = self.values[artifact_id]
        return {
            "artifactId": artifact_id,
            "mediaType": "audio/wav",
            "byteLength": path.stat().st_size,
        }

    def release(self, artifact_id: str) -> bool:
        path = self.values.pop(artifact_id, None)
        if path is None:
            return False
        shutil.rmtree(path.parent, ignore_errors=True)
        return True


def test_managed_coordinator_serializes_weight_switch_and_synthesis(
    tmp_path: Path,
    monkeypatch,
) -> None:  # type: ignore[no-untyped-def]
    from plugins.sakura_gpt_sovits import plugin as provider_module

    events: list[tuple[str, str]] = []
    resolvers: list[object] = []

    class FakeRuntime:
        def __init__(self, settings: object) -> None:
            self.settings = settings
            self._weights_ready = False
            self._server_process = None
            self._process_resource = None

    class FakeResolver:
        def __init__(self, settings: object, **_kwargs: object) -> None:
            self.settings = settings
            self.endpoint = SimpleNamespace(synthesis_url="http://127.0.0.1/tts")
            self.runtime = FakeRuntime(settings)
            resolvers.append(self)

    class FakeSupervisor:
        endpoint_kind = "managed"

        def __init__(self, resolver: FakeResolver) -> None:
            self.resolver = resolver
            self.settings = resolver.settings

        def _ensure_service_available(self, _fail: object) -> bool:
            return True

        def _ensure_character_weights(
            self,
            _fail: object,
            *,
            cancel_checker: Callable[[], None] | None = None,
        ) -> bool:
            if self.resolver.runtime._weights_ready:
                return True
            if cancel_checker is not None:
                cancel_checker()
            events.extend(
                [
                    ("gpt", str(self.settings.gpt_model_path)),
                    ("sovits", str(self.settings.sovits_model_path)),
                ]
            )
            self.resolver.runtime._weights_ready = True
            return True

        def _restart_local_service_after_http_failure(self, _status: int, _body: str) -> bool:
            return False

    class FakeEngine:
        def synthesize(self, queue: object, request: object, *, fail: object, skip: object) -> Path:
            del fail, skip
            supervisor = getattr(queue, "_supervisor")
            assert supervisor._ensure_service_available(lambda _message: None)
            assert supervisor._ensure_character_weights(lambda _message: None)
            settings = getattr(queue, "settings")
            events.append(("tts", str(settings.gpt_model_path)))
            target = getattr(queue, "_cache_dir") / f"{getattr(request, 'request_id')}.wav"
            target.write_bytes(_wav_bytes())
            return target

    monkeypatch.setattr(provider_module, "GptSovitsEndpointResolver", FakeResolver)
    monkeypatch.setattr(provider_module, "GptSovitsEndpointSupervisor", FakeSupervisor)
    monkeypatch.setattr(provider_module, "GPTSoVITSSynthesisEngine", FakeEngine)

    package = tmp_path / "character"
    package.mkdir()
    reference = package / "neutral.wav"
    reference.write_bytes(_wav_bytes())
    context = _EffectContext()
    artifacts = _LocalArtifacts(tmp_path / "artifacts")
    runtime_dir = tmp_path / "runtime"
    runtime_dir.mkdir()
    python_path = tmp_path / "python"
    python_path.write_bytes(b"python")
    tts_config_path = tmp_path / "config.yaml"
    tts_config_path.write_text("model: test", encoding="utf-8")
    config = provider_module._ProviderConfig(
        enabled=True,
        custom_base_url=None,
        tts_path="/tts",
        timeout_seconds=5,
        remote_reference_root=None,
        work_dir=runtime_dir,
        python_path=python_path,
        tts_config_path=tts_config_path,
    )
    coordinator = provider_module._Coordinator(config)
    try:
        jobs = []
        for character_id in ("alpha", "beta"):
            gpt_model = package / f"{character_id}.ckpt"
            sovits_model = package / f"{character_id}.pth"
            gpt_model.write_bytes(b"gpt")
            sovits_model.write_bytes(b"sovits")
            voice = provider_module._CharacterVoice(
                character_id=character_id,
                package_dir=package,
                ref_text_path=reference,
                ref_audio_path=reference,
                ref_text="reference",
                ref_lang="ja",
                text_lang="ja",
                tone_references={"中性": []},
                gpt_model_path=gpt_model,
                sovits_model_path=sovits_model,
            )
            job = provider_module._Job(
                context,
                artifacts,
                {
                    "requestId": f"job-{character_id}",
                    "characterId": character_id,
                    "text": character_id,
                    "options": {"tone": "中性"},
                },
                voice,
            )
            coordinator.submit(job)
            jobs.append(job)

        terminals = []
        deadline = time.monotonic() + 2
        for job in jobs:
            while True:
                terminal = job.poll()
                if terminal["state"] != "running":
                    terminals.append(terminal)
                    break
                assert time.monotonic() < deadline
                time.sleep(0.01)
        assert [terminal["state"] for terminal in terminals] == ["succeeded", "succeeded"]
        assert len(resolvers) == 1
        assert [kind for kind, _value in events] == [
            "gpt",
            "sovits",
            "tts",
            "gpt",
            "sovits",
            "tts",
        ]
        assert events[0][1].endswith("alpha.ckpt")
        assert events[3][1].endswith("beta.ckpt")
    finally:
        coordinator.close()


def test_gpt_provider_cancels_queued_job_and_rejects_character_escape(tmp_path: Path) -> None:
    server = _TtsServer()
    server.delay_seconds = 0.3
    root = _root(tmp_path, f"http://127.0.0.1:{server.server_port}")
    escaped = root / "characters" / "escaped" / "character.json"
    escaped.parent.mkdir(parents=True)
    (escaped.parent / "card.md").write_text("escaped", encoding="utf-8")
    (escaped.parent / "portrait.png").write_bytes(b"portrait")
    escaped.write_text(
        json.dumps(
            {
                "id": "escaped",
                "display_name": "escaped",
                "card": "card.md",
                "portrait": {"default": "portrait.png"},
                "extensions": {
                    "sakura.tts": {"provider": "sakura.tts.gpt-sovits"},
                    "sakura.tts.gpt-sovits": {"toneRefs": "../alpha/voice/refs/ref.txt"},
                },
            }
        ),
        encoding="utf-8",
    )
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    worker = PluginWorkerClient(root, GENERATION, call_timeout=0.5)
    worker.configure_host_services(ToolRegistry(), _Runtime())
    try:
        worker.start()
        worker.wait_until_loaded(timeout=5)
        for request_id in ("active", "queued"):
            result = worker.call_service(
                "sakura.tts",
                "begin",
                {
                    "requestId": request_id,
                    "characterId": "alpha",
                    "text": request_id,
                    "options": {"tone": "中性"},
                },
            )
            assert result["state"] == "running"
        cancelled = worker.call_service("sakura.tts", "cancel", "queued")
        assert cancelled["accepted"] is True
        assert _poll_terminal(worker, "queued")["state"] == "cancelled"
        active = _poll_terminal(worker, "active")
        assert active["state"] == "succeeded"
        worker.release_committed_artifact(active["artifact"]["artifactId"])

        running = worker.call_service(
            "sakura.tts",
            "begin",
            {
                "requestId": "active-cancel",
                "characterId": "alpha",
                "text": "active cancel",
                "options": {"tone": "中性"},
            },
        )
        assert running["state"] == "running"
        deadline = time.monotonic() + 2
        while server.active == 0:
            assert time.monotonic() < deadline
            time.sleep(0.01)
        active_cancelled = worker.call_service("sakura.tts", "cancel", "active-cancel")
        assert active_cancelled["accepted"] is True
        assert _poll_terminal(worker, "active-cancel")["state"] == "cancelled"

        escaped_result = worker.call_service(
            "sakura.tts",
            "begin",
            {
                "requestId": "escaped",
                "characterId": "escaped",
                "text": "escape",
                "options": {"tone": "中性"},
            },
        )
        assert escaped_result["state"] == "failed"
        assert escaped_result["errorCode"] == "TTS_SYNTHESIS_FAILED"
        assert getattr(worker._host_services, "artifact_count") == 0
    finally:
        worker.close()
        server.shutdown()
        server.server_close()
        server_thread.join(1)
