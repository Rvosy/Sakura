from __future__ import annotations

import io
import json
import shutil
import sys
import threading
import time
import wave
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from typing import Callable

import psutil
import pytest

from app.agent.tools import ToolRegistry
from app.core_host.plugin_worker import PluginWorkerClient
from app.core_host.tts_boundary import TTSBoundary


GENERATION = "generation-genie-plugin"
CREDENTIAL = "5" * 32


def _wav_bytes() -> bytes:
    output = io.BytesIO()
    with wave.open(output, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(32_000)
        handle.writeframes(b"\x01\x00" * 640)
    return output.getvalue()


class _GenieServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self) -> None:
        super().__init__(("127.0.0.1", 0), _GenieHandler)
        self.calls: list[tuple[str, dict[str, object]]] = []
        self.finished: list[str] = []
        self.active = 0
        self.max_active = 0
        self.delay: dict[str, float] = {}
        self.lock = threading.Lock()


class _GenieHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        if self.path.rstrip("/").endswith("openapi.json"):
            body = json.dumps(
                {
                    "paths": {
                        "/load_character": {},
                        "/set_reference_audio": {},
                        "/tts": {},
                    }
                }
            ).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self.send_response(404)
        self.end_headers()

    def do_POST(self) -> None:  # noqa: N802
        endpoint = self.path.rstrip("/").rsplit("/", 1)[-1]
        length = int(self.headers.get("Content-Length", "0"))
        payload = json.loads(self.rfile.read(length).decode("utf-8"))
        server: _GenieServer = self.server  # type: ignore[assignment]
        with server.lock:
            server.calls.append((endpoint, payload))
            server.active += 1
            server.max_active = max(server.max_active, server.active)
            delay = server.delay.get(endpoint, 0)
        try:
            time.sleep(delay)
            body = _wav_bytes() if endpoint == "tts" else b"ok"
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass
        finally:
            with server.lock:
                server.active -= 1
                server.finished.append(endpoint)

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
    shutil.copytree(repository / "plugins" / "sakura_genie", plugins / "sakura_genie")
    plugin_data = root / "data" / "plugins" / "sakura.tts.genie"
    plugin_data.mkdir(parents=True)
    config: dict[str, object] = {
        "enabled": True,
        "endpointMode": "custom",
        "apiUrl": endpoint,
        "timeoutSeconds": 5,
        "workDir": str(tmp_path / "stale-managed-runtime"),
    }
    config.update(config_patch or {})
    (plugin_data / "config.json").write_text(json.dumps(config), encoding="utf-8")
    _write_custom_character(root, "alpha", "remote-alpha")
    _write_custom_character(root, "beta", "remote-beta")
    return root


def _write_custom_character(root: Path, character_id: str, remote_name: str) -> None:
    package = root / "characters" / character_id
    package.mkdir(parents=True)
    (package / "card.md").write_text(character_id, encoding="utf-8")
    (package / "portrait.png").write_bytes(b"portrait")
    (package / "character.json").write_text(
        json.dumps(
            {
                "id": character_id,
                "display_name": "same display name",
                "card": "card.md",
                "portrait": {"default": "portrait.png"},
                "extensions": {
                    "sakura.tts": {
                        "enabled": True,
                        "provider": "sakura.tts.genie",
                    },
                    "sakura.tts.genie": {"remoteCharacterName": remote_name},
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
    raise AssertionError("Genie plugin job did not finish")


def test_custom_genie_provider_reaches_core_without_owning_or_mutating_endpoint(
    tmp_path: Path,
) -> None:
    server = _GenieServer()
    endpoint = f"http://127.0.0.1:{server.server_port}/"
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
            AssertionError("legacy TTS must not run for an explicit Genie plugin selection")
        ),
    )
    try:
        worker.start()
        snapshot = worker.wait_until_loaded(timeout=5)
        by_id = {item["pluginId"]: item for item in snapshot["plugins"]}
        assert by_id["sakura.tts.genie"]["state"] == "active"
        baseline_effects = by_id["sakura.tts.genie"]["effectCount"]

        boundary.authorize_segment(
            operation_id="operation-real-genie",
            segment_index=0,
            text="hello",
            tone="中性",
            portrait="default",
            character_id="alpha",
            history_entry_id="entry-real-genie",
        )
        result = boundary.handle(
            _request(
                "tts.synthesis.start",
                {"operationId": "operation-real-genie", "segmentIndex": 0},
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
        assert metadata["provider"] == "sakura.tts.genie"
        assert server.calls[0][0] == "tts"
        assert not any(endpoint in {"load_character", "set_reference_audio"} for endpoint, _ in server.calls)
        assert not (tmp_path / "stale-managed-runtime").exists()
        assert getattr(worker._host_services, "artifact_count") == 0
        live = worker.refresh_status()
        live_by_id = {item["pluginId"]: item for item in live["plugins"]}
        assert live_by_id["sakura.tts.genie"]["effectCount"] == baseline_effects

        for request_id, character_id in (("job-alpha", "alpha"), ("job-beta", "beta")):
            assert worker.call_service(
                "sakura.tts",
                "begin",
                {
                    "requestId": request_id,
                    "characterId": character_id,
                    "text": character_id,
                    "options": {"tone": "中性"},
                },
            )["state"] == "running"
        first = _poll_terminal(worker, "job-alpha")
        second = _poll_terminal(worker, "job-beta")
        assert first["state"] == second["state"] == "succeeded"
        assert server.max_active == 1
        worker.release_committed_artifact(first["artifact"]["artifactId"])
        worker.release_committed_artifact(second["artifact"]["artifactId"])
    finally:
        boundary.close()
        worker.close()
        server.shutdown()
        server.server_close()
        server_thread.join(1)


def test_custom_genie_active_cancel_and_disable_leave_worker_healthy(tmp_path: Path) -> None:
    server = _GenieServer()
    server.delay["tts"] = 2
    root = _root(tmp_path, f"http://127.0.0.1:{server.server_port}/")
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
                "text": "active",
                "options": {"tone": "中性"},
            },
        )["state"] == "running"
        deadline = time.monotonic() + 2
        while server.active == 0:
            assert time.monotonic() < deadline
            time.sleep(0.01)

        disabled = worker.set_plugin_enabled("sakura.tts.genie", False)
        by_id = {item["pluginId"]: item for item in disabled["plugins"]}
        assert by_id["sakura.tts.genie"]["state"] == "disabled"
        assert by_id["sakura.tts"]["state"] == "active"
        assert getattr(worker._host_services, "artifact_count") == 0
        assert worker.call_service("sakura.tts", "poll", "disable-active")[
            "errorCode"
        ] == "TTS_JOB_NOT_FOUND"
        restored = worker.set_plugin_enabled("sakura.tts.genie", True)
        restored_by_id = {item["pluginId"]: item for item in restored["plugins"]}
        assert restored_by_id["sakura.tts.genie"]["state"] == "active"
    finally:
        worker.close()
        server.shutdown()
        server.server_close()
        server_thread.join(1)


def test_invalid_genie_config_stays_active_but_unavailable(tmp_path: Path) -> None:
    root = _root(
        tmp_path,
        "http://127.0.0.1:1/",
        config_patch={"timeoutSeconds": True},
    )
    worker = PluginWorkerClient(root, GENERATION, call_timeout=0.5)
    worker.configure_host_services(ToolRegistry(), _Runtime())
    try:
        worker.start()
        snapshot = worker.wait_until_loaded(timeout=5)
        by_id = {item["pluginId"]: item for item in snapshot["plugins"]}
        assert by_id["sakura.tts.genie"]["state"] == "active"
        assert worker.call_service("sakura.tts", "status", "alpha")["available"] is False
    finally:
        worker.close()


def test_managed_genie_rejects_character_resource_escape_before_artifact(
    tmp_path: Path,
) -> None:
    work_dir = tmp_path / "managed-runtime"
    work_dir.mkdir()
    root = _root(
        tmp_path,
        "http://127.0.0.1:1/",
        config_patch={"endpointMode": "managed", "workDir": str(work_dir)},
    )
    package = root / "characters" / "alpha"
    refs = package / "voice" / "refs"
    refs.mkdir(parents=True)
    (refs / "neutral.wav").write_bytes(_wav_bytes())
    (refs / "ref.txt").write_text(
        "voice/refs/neutral.wav|JA|reference|中性\n",
        encoding="utf-8",
    )
    manifest = json.loads((package / "character.json").read_text(encoding="utf-8"))
    manifest["extensions"]["sakura.tts.genie"] = {
        "toneRefs": "voice/refs/ref.txt",
        "onnxModelDir": "../outside-onnx",
    }
    (package / "character.json").write_text(json.dumps(manifest), encoding="utf-8")
    worker = PluginWorkerClient(root, GENERATION, call_timeout=0.5)
    worker.configure_host_services(ToolRegistry(), _Runtime())
    try:
        worker.start()
        worker.wait_until_loaded(timeout=5)
        result = worker.call_service(
            "sakura.tts",
            "begin",
            {
                "requestId": "escape-onnx",
                "characterId": "alpha",
                "text": "escape",
                "options": {"tone": "中性"},
            },
        )
        assert result["state"] == "failed"
        assert result["errorCode"] == "TTS_SYNTHESIS_FAILED"
        assert getattr(worker._host_services, "artifact_count") == 0
    finally:
        worker.close()


class _EffectContext:
    def effect(self, cleanup: Callable[[], None]) -> Callable[[], None]:
        active = True

        def dispose() -> None:
            nonlocal active
            if active:
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


def _direct_job(provider_module, context, artifacts, voice, request_id: str):  # type: ignore[no-untyped-def]
    return provider_module._Job(
        context,
        artifacts,
        {
            "requestId": request_id,
            "characterId": voice.character_id,
            "text": request_id,
            "options": {"tone": "中性"},
        },
        voice,
    )


def _direct_terminal(job: object) -> dict[str, object]:
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        result = job.poll()
        if result["state"] != "running":
            return result
        time.sleep(0.01)
    raise AssertionError("direct Genie job did not finish")


def test_managed_genie_serializes_model_reference_and_tts_by_character(
    tmp_path: Path,
    monkeypatch,
) -> None:  # type: ignore[no-untyped-def]
    from plugins.sakura_genie import plugin as provider_module

    server = _GenieServer()
    server.delay = {"load_character": 0.03, "set_reference_audio": 0.03, "tts": 0.03}
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    config = provider_module._ProviderConfig(
        enabled=True,
        endpoint_mode="managed",
        api_url=f"http://127.0.0.1:{server.server_port}/",
        timeout_seconds=5,
        work_dir=tmp_path,
    )
    coordinator = provider_module._Coordinator(
        config,
        tmp_path / "cache",
        tmp_path / "genie.log",
    )
    monkeypatch.setattr(
        coordinator,
        "_ensure_managed_endpoint",
        lambda _job: setattr(coordinator, "_endpoint_ready", True),
    )
    context = _EffectContext()
    artifacts = _LocalArtifacts(tmp_path / "artifacts")
    try:
        jobs = []
        for character_id in ("alpha", "beta"):
            onnx = tmp_path / character_id
            onnx.mkdir()
            (onnx / "model.onnx").write_bytes(b"onnx")
            reference = tmp_path / f"{character_id}.wav"
            reference.write_bytes(_wav_bytes())
            voice = provider_module._CharacterVoice(
                character_id=character_id,
                remote_character_name="",
                ref_lang="ja",
                tone_references={
                    "中性": [
                        provider_module.ToneReference(
                            "中性",
                            reference,
                            f"reference-{character_id}",
                            "ja",
                        )
                    ]
                },
                onnx_model_dir=onnx,
                gpt_model_path=None,
                sovits_model_path=None,
            )
            job = _direct_job(provider_module, context, artifacts, voice, f"job-{character_id}")
            coordinator.submit(job)
            jobs.append(job)

        terminals = [_direct_terminal(job) for job in jobs]
        assert [item["state"] for item in terminals] == ["succeeded", "succeeded"]
        assert [endpoint for endpoint, _payload in server.calls] == [
            "load_character",
            "set_reference_audio",
            "tts",
            "load_character",
            "set_reference_audio",
            "tts",
        ]
        assert server.max_active == 1
        assert server.calls[0][1]["onnx_model_dir"].endswith("alpha")
        assert server.calls[3][1]["onnx_model_dir"].endswith("beta")
    finally:
        coordinator.close()
        server.shutdown()
        server.server_close()
        thread.join(1)


def test_state_change_cancel_waits_for_response_before_next_character(
    tmp_path: Path,
    monkeypatch,
) -> None:  # type: ignore[no-untyped-def]
    from plugins.sakura_genie import plugin as provider_module

    server = _GenieServer()
    server.delay["load_character"] = 0.3
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    config = provider_module._ProviderConfig(
        True,
        "managed",
        f"http://127.0.0.1:{server.server_port}/",
        5,
        tmp_path,
    )
    coordinator = provider_module._Coordinator(config, tmp_path / "cache", tmp_path / "log")
    monkeypatch.setattr(coordinator, "_ensure_managed_endpoint", lambda _job: None)
    context = _EffectContext()
    artifacts = _LocalArtifacts(tmp_path / "artifacts")

    def voice(character_id: str):  # type: ignore[no-untyped-def]
        onnx = tmp_path / character_id
        onnx.mkdir()
        (onnx / "model.onnx").write_bytes(b"onnx")
        reference = tmp_path / f"{character_id}.wav"
        reference.write_bytes(_wav_bytes())
        return provider_module._CharacterVoice(
            character_id,
            "",
            "ja",
            {"中性": [provider_module.ToneReference("中性", reference, character_id, "ja")]},
            onnx,
            None,
            None,
        )

    try:
        first = _direct_job(provider_module, context, artifacts, voice("alpha"), "cancel-alpha")
        coordinator.submit(first)
        deadline = time.monotonic() + 2
        while not server.calls:
            assert time.monotonic() < deadline
            time.sleep(0.01)
        assert server.calls[0][0] == "load_character"
        first.cancel()
        second = _direct_job(provider_module, context, artifacts, voice("beta"), "next-beta")
        coordinator.submit(second)
        assert _direct_terminal(first)["state"] == "cancelled"
        assert _direct_terminal(second)["state"] == "succeeded"
        endpoints = [endpoint for endpoint, _payload in server.calls]
        assert endpoints[:2] == ["load_character", "load_character"]
        assert server.finished[0] == "load_character"
        assert server.max_active == 1
    finally:
        coordinator.close()
        server.shutdown()
        server.server_close()
        thread.join(1)


class _ConversionJob:
    def check_cancelled(self) -> None:
        return None

    def wait_or_cancel(self, seconds: float) -> None:
        time.sleep(min(seconds, 0.01))


def _conversion_voice(provider_module, root: Path):  # type: ignore[no-untyped-def]
    gpt = root / "model.ckpt"
    sovits = root / "model.pth"
    gpt.write_bytes(b"gpt")
    sovits.write_bytes(b"sovits")
    return provider_module._CharacterVoice(
        "alpha",
        "",
        "ja",
        {},
        None,
        gpt,
        sovits,
    )


def test_onnx_conversion_failure_never_promotes_partial_cache(
    tmp_path: Path,
    monkeypatch,
) -> None:  # type: ignore[no-untyped-def]
    from plugins.sakura_genie import plugin as provider_module

    work_dir = tmp_path / "runtime"
    work_dir.mkdir()
    converter = work_dir / "convert.py"
    converter.write_text(
        """
import argparse
from pathlib import Path
parser = argparse.ArgumentParser()
parser.add_argument('--pth')
parser.add_argument('--ckpt')
parser.add_argument('--out')
args = parser.parse_args()
Path(args.out, 'partial.onnx').write_bytes(b'partial')
raise SystemExit(7)
""".strip(),
        encoding="utf-8",
    )
    monkeypatch.setattr(provider_module, "find_usable_runtime_python", lambda _root: Path(sys.executable))
    config = provider_module._ProviderConfig(True, "managed", "http://127.0.0.1:9881/", 5, work_dir)
    cache = tmp_path / "cache"
    cache.mkdir()
    coordinator = provider_module._Coordinator(config, cache, tmp_path / "log")
    voice = _conversion_voice(provider_module, tmp_path)
    try:
        with pytest.raises(RuntimeError, match="TTS_ONNX_CONVERSION_FAILED"):
            coordinator._ensure_onnx_model(voice, _ConversionJob())
        assert not list(cache.glob("*.staging-*"))
        assert not list(cache.glob("*/.sakura-complete.json"))

        converter.write_text(
            converter.read_text(encoding="utf-8").replace(
                "Path(args.out, 'partial.onnx').write_bytes(b'partial')\nraise SystemExit(7)",
                "Path(args.out, 'model.onnx').write_bytes(b'complete')",
            ),
            encoding="utf-8",
        )
        result = coordinator._ensure_onnx_model(voice, _ConversionJob())
        assert (result / "model.onnx").read_bytes() == b"complete"
        assert (result / ".sakura-complete.json").is_file()
    finally:
        coordinator.close()


def test_onnx_conversion_cancel_kills_child_tree_and_cleans_staging(
    tmp_path: Path,
    monkeypatch,
) -> None:  # type: ignore[no-untyped-def]
    from plugins.sakura_genie import plugin as provider_module

    work_dir = tmp_path / "runtime"
    work_dir.mkdir()
    marker = tmp_path / "pids.txt"
    child_code = (
        "import subprocess,sys,time; "
        "g=subprocess.Popen([sys.executable,'-c','import time; time.sleep(30)']); "
        "open(sys.argv[1],'w').write(f'{__import__(\"os\").getpid()},{g.pid}'); "
        "time.sleep(30)"
    )
    (work_dir / "convert.py").write_text(
        "import subprocess,sys,time\n"
        f"subprocess.Popen([sys.executable, '-c', {child_code!r}, {str(marker)!r}])\n"
        "time.sleep(30)\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(provider_module, "find_usable_runtime_python", lambda _root: Path(sys.executable))
    config = provider_module._ProviderConfig(True, "managed", "http://127.0.0.1:9881/", 5, work_dir)
    cache = tmp_path / "cache"
    coordinator = provider_module._Coordinator(config, cache, tmp_path / "log")
    monkeypatch.setattr(coordinator, "_ensure_managed_endpoint", lambda _job: None)
    context = _EffectContext()
    artifacts = _LocalArtifacts(tmp_path / "artifacts")
    voice = _conversion_voice(provider_module, tmp_path)
    job = _direct_job(provider_module, context, artifacts, voice, "cancel-conversion")
    pids: list[int] = []
    try:
        coordinator.submit(job)
        deadline = time.monotonic() + 5
        while not marker.is_file():
            assert time.monotonic() < deadline
            time.sleep(0.02)
        pids = [int(value) for value in marker.read_text(encoding="utf-8").split(",")]
        job.cancel()
        assert _direct_terminal(job)["state"] == "cancelled"
        deadline = time.monotonic() + 3
        while any(psutil.pid_exists(pid) for pid in pids) and time.monotonic() < deadline:
            time.sleep(0.05)
        assert all(not psutil.pid_exists(pid) for pid in pids)
        assert not list(cache.glob("*.staging-*"))
    finally:
        coordinator.close()
        for pid in pids:
            try:
                psutil.Process(pid).kill()
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
