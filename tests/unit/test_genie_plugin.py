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
from dataclasses import replace

import psutil
import pytest

from app.agent.tools import ToolRegistry
from app.core_host.plugin_runtime_application import PluginRuntimeApplication
from app.core_host.tts_boundary import TTSBoundary
from app.plugins.dependencies import PluginDependencyRoots
from app.plugins.inventory import PluginInventory
from app.storage.runtime_roots import RuntimeRoots
from plugins.builtin.sakura_genie import _bundle as genie_bundle
from plugins.builtin.sakura_genie import _support as genie_support


GENERATION = "generation-genie-plugin"
CREDENTIAL = "5" * 32


@pytest.mark.skipif(sys.platform != "win32", reason="verbatim paths are Windows-only")
def test_genie_process_boundaries_remove_verbatim_paths() -> None:
    from plugins.builtin.sakura_genie import plugin as provider_module

    python = Path(r"\\?\D:\Sakura\tts\cpu\runtime\python.exe")

    assert genie_bundle._external_path(r"\\?\D:\Sakura\tts\cpu") == (
        r"D:\Sakura\tts\cpu"
    )
    assert genie_support._subprocess_path(r"\\?\D:\Sakura\tts\cpu") == (
        r"D:\Sakura\tts\cpu"
    )
    assert genie_support._subprocess_path(r"\\?\UNC\server\share\tts") == (
        r"\\server\share\tts"
    )
    assert genie_support.user_facing_path(r"\\?\D:\Sakura\tts\cpu") == (
        r"D:\Sakura\tts\cpu"
    )
    assert genie_support._build_genie_start_command(python, "127.0.0.1", 9881)[0] == (
        r"D:\Sakura\tts\cpu\runtime\python.exe"
    )
    assert genie_support._local_tts_subprocess_env(python)["PATH"].startswith(
        r"D:\Sakura\tts\cpu\runtime"
    )

    patches: list[dict[str, object]] = []
    result = genie_bundle.TTSBundleInstallResult(
        work_dir=Path(r"\\?\D:\Sakura\tts\cpu")
    )
    resource = genie_bundle.TTSBundleResource(
        user_root=Path(r"D:\Sakura"),
        config_get=lambda: {},
        config_update=lambda patch: patches.append(dict(patch)),
        entry=lambda: genie_bundle.GENIE_TTS,
        custom_endpoint=lambda _config: False,
        installer=lambda *_args, **_kwargs: result,
    )
    resource._run(genie_bundle.GENIE_TTS)
    assert patches == [{"workDir": r"D:\Sakura\tts\cpu"}]

    assert provider_module._startup_config_patch(
        {"endpointMode": "managed", "workDir": r"\\?\D:\Sakura\tts\cpu"},
        Path(r"D:\Sakura"),
    ) == {"workDir": r"D:\Sakura\tts\cpu"}


@pytest.mark.skipif(sys.platform != "win32", reason="verbatim paths are Windows-only")
def test_installed_managed_genie_binds_bundle_without_verbatim_path(
    tmp_path: Path,
) -> None:
    from plugins.builtin.sakura_genie import plugin as provider_module

    user_root = tmp_path / "user"
    runtime = user_root / "tts" / "cpu" / "runtime"
    runtime.mkdir(parents=True)
    (runtime / "python.exe").write_bytes(b"runtime")
    verbatim_root = Path("\\\\?\\" + str(user_root.resolve()))

    patch = provider_module._startup_config_patch(
        {"endpointMode": "managed", "workDir": ""},
        verbatim_root,
    )

    assert patch == {"workDir": str(user_root.resolve() / "tts" / "cpu")}
    assert "\\\\?\\" not in str(patch["workDir"])


def test_managed_genie_uses_internal_endpoint_and_standard_character_layout(
    tmp_path: Path,
) -> None:
    from plugins.builtin.sakura_genie import plugin as provider_module

    config = provider_module._parse_config(
        {
            "endpointMode": "managed",
            "apiUrl": "https://external.example.invalid/",
            "timeoutSeconds": 60,
            "workDir": str(tmp_path),
        }
    )
    assert config.api_url == provider_module.DEFAULT_GENIE_TTS_API_URL

    package = tmp_path / "character"
    refs = package / "voice" / "refs"
    onnx = package / "voice" / "onnx"
    refs.mkdir(parents=True)
    onnx.mkdir(parents=True)
    (refs / "neutral.wav").write_bytes(_wav_bytes())
    (refs / "ref.txt").write_text(
        "voice/refs/neutral.wav|JA|reference|中性\n",
        encoding="utf-8",
    )
    (onnx / "model.onnx").write_bytes(b"onnx")

    class Character:
        @staticmethod
        def resolve_resource(_character_id: str, relative: str) -> str:
            path = package / relative
            if not path.exists():
                raise OSError("missing character resource")
            return str(path)

    voice = provider_module._parse_character_voice(
        Character(),
        "alpha",
        {},
        endpoint_mode="managed",
    )
    assert voice.onnx_model_dir == onnx
    assert voice.reference("中性").ref_audio_path == refs / "neutral.wav"


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


def _root(
    tmp_path: Path,
    endpoint: str,
    *,
    config_patch: dict[str, object] | None = None,
) -> Path:
    root = tmp_path / "assistant"
    plugins = root / "plugins" / "builtin"
    plugins.mkdir(parents=True)
    (root / "plugins" / "__init__.py").write_text("", encoding="utf-8")
    (plugins / "__init__.py").write_text("", encoding="utf-8")
    repository = Path(__file__).parents[2]
    shutil.copytree(repository / "plugins" / "builtin" / "sakura_tts_hub", plugins / "sakura_tts_hub")
    shutil.copytree(repository / "plugins" / "builtin" / "sakura_genie", plugins / "sakura_genie")
    plugin_root = plugins / "sakura_genie"
    declaration = PluginDependencyRoots(root).declaration(plugin_root)
    assert declaration is not None
    dependency_root = root / "plugins/dependencies/sakura.tts.genie"
    dependency_root.mkdir(parents=True)
    (dependency_root / ".sakura-dependencies.json").write_text(
        json.dumps({
            "schemaVersion": 1,
            "kind": declaration.kind,
            "fingerprint": declaration.fingerprint,
            "python": f"{sys.version_info.major}.{sys.version_info.minor}",
        }),
        encoding="utf-8",
    )
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


def _worker(root: Path, *, call_timeout: float) -> PluginRuntimeApplication:
    roots = RuntimeRoots(root, root)
    return PluginRuntimeApplication(
        roots,
        GENERATION,
        ToolRegistry(),
        PluginInventory(roots).scan().runtime_specs,
        call_timeout=call_timeout,
    )


def _poll_terminal(worker: PluginRuntimeApplication, request_id: str) -> dict[str, object]:
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
    worker = _worker(root, call_timeout=0.5)
    session = SimpleNamespace(plugin_application=worker, character=SimpleNamespace(id="alpha"))
    boundary = TTSBoundary(
        GENERATION,
        CREDENTIAL,
        root,
        session_provider=lambda: session,
    )
    try:
        worker.start()
        assert worker.wait_until_loaded(timeout=5)
        snapshot = worker.public_snapshot()
        by_id = {item["pluginId"]: item for item in snapshot["plugins"]}
        assert by_id["sakura.tts.genie"]["state"] == "active"
        warmup = worker.call_service("sakura.tts", "warmup", "alpha")
        assert warmup["accepted"] is False
        assert warmup["reasonCode"] == "TTS_WARMUP_SKIPPED"
        assert server.calls == []

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
        assert worker.public_snapshot()["state"] == "ready"

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
    worker = _worker(root, call_timeout=1.5)
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
            "state"
        ] == "cancelled"
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
    worker = _worker(root, call_timeout=0.5)
    try:
        worker.start()
        assert worker.wait_until_loaded(timeout=5)
        snapshot = worker.public_snapshot()
        by_id = {item["pluginId"]: item for item in snapshot["plugins"]}
        assert by_id["sakura.tts.genie"]["state"] == "active"
        assert worker.call_service("sakura.tts", "status", "alpha")["available"] is False
        sections = worker.settings_sections("voice")
        assert len(sections) == 1
        assert sections[0]["pluginId"] == "sakura.tts.genie"
        assert {field["key"] for field in sections[0]["fields"]} == {
            "endpointMode",
            "apiUrl",
            "timeoutSeconds",
        }
        fields = {field["key"]: field for field in sections[0]["fields"]}
        assert fields["endpointMode"]["label"] == "服务来源"
        assert fields["apiUrl"]["enabledWhen"] == {
            "field": "endpointMode",
            "equals": "custom",
        }
        assert fields["timeoutSeconds"]["enabledWhen"] is None
        assert worker.settings_sections("about") == []
        component = worker.settings_sections("plugin")
        assert len(component) == 1
        assert component[0]["pluginId"] == "sakura.tts.genie"
        assert component[0]["values"]["bundleResource"]["applicability"] == "not_required"
        assert component[0]["values"]["bundleResource"]["availableActionIds"] == []
        saved = worker.settings_save(
            "sakura.tts.genie",
            "runtime",
            {"timeoutSeconds": 60},
        )
        assert saved["applicationState"] == "applied"
    finally:
        worker.close()


@pytest.mark.skipif(sys.platform != "win32", reason="managed Genie bundle is Windows-only")
def test_switching_to_managed_genie_hot_binds_installed_bundle(tmp_path: Path) -> None:
    root = _root(tmp_path, "http://127.0.0.1:1/")
    runtime = root / "tts" / "cpu" / "runtime"
    runtime.mkdir(parents=True)
    (runtime / "python.exe").write_bytes(b"runtime")
    worker = _worker(root, call_timeout=0.5)
    try:
        worker.start()
        assert worker.wait_until_loaded(timeout=5)

        saved = worker.settings_save(
            "sakura.tts.genie",
            "runtime",
            {"endpointMode": "managed"},
        )

        assert saved["applicationState"] == "applied"
        config = json.loads(
            (root / "data/plugins/sakura.tts.genie/config.json").read_text(
                encoding="utf-8"
            )
        )
        assert config["workDir"] == str(root / "tts" / "cpu")
        assert "\\\\?\\" not in config["workDir"]
        assert worker.call_service("sakura.tts", "status", "alpha")["available"] is True
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
    worker = _worker(root, call_timeout=0.5)
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
        assert result["errorCode"] == "TTS_CHARACTER_CONFIG_INVALID"
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


@pytest.mark.parametrize("missing,code", [
    ("references", "TTS_REFERENCE_UNAVAILABLE"),
    ("source", "TTS_SOURCE_MODEL_UNAVAILABLE"),
    ("models", "TTS_ONNX_UNAVAILABLE"),
])
def test_genie_resource_errors_survive_provider_ipc(tmp_path: Path, missing: str, code: str) -> None:
    root = _root(tmp_path, "http://127.0.0.1:1/", config_patch={"endpointMode": "managed", "workDir": str(tmp_path)})
    package = root / "characters/alpha"
    refs = package / "voice/refs"
    refs.mkdir(parents=True)
    (refs / "ref.txt").write_text("voice/refs/ref.wav|JA|reference|中性\n", encoding="utf-8")
    if missing != "references":
        (refs / "ref.wav").write_bytes(_wav_bytes())
    manifest_path = package / "character.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["extensions"].pop("sakura.tts.genie")
    manifest["extensions"]["sakura.tts.gpt-sovits"] = {
        "toneRefs": "voice/refs/ref.txt",
        **({"gptModel": "missing.ckpt", "sovitsModel": "missing.pth"} if missing != "models" else {}),
    }
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    worker = _worker(root, call_timeout=0.5)
    try:
        worker.start()
        worker.wait_until_loaded(timeout=5)
        warmup = worker.call_service("sakura.tts", "warmup", "alpha")
        assert warmup["accepted"] is False
        assert warmup["reasonCode"] == code
        assert warmup["stage"] == "character_configuration"
        result = worker.call_service("sakura.tts", "begin", {
            "requestId": "missing", "characterId": "alpha", "text": "hello", "options": {},
        })
        assert result["state"] == "failed"
        assert result["errorCode"] == code
        assert getattr(worker._host_services, "artifact_count") == 0
    finally:
        worker.close()


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
    from plugins.builtin.sakura_genie import plugin as provider_module

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
    from plugins.builtin.sakura_genie import plugin as provider_module

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
    from plugins.builtin.sakura_genie import plugin as provider_module

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
        assert (tmp_path / "genie-converter.log").is_file()

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


@pytest.mark.parametrize("onnx_state", ["absent", "missing_override", "empty", "zero", "valid"])
def test_partial_character_warmup_converts_and_synthesis_reuses_cache(
    tmp_path: Path, monkeypatch, onnx_state: str,
) -> None:
    from app.config.character_packages import repair_character_packages
    from app.core_host.plugin_character import PluginCharacterStore
    from plugins.builtin.sakura_genie import plugin as p

    server = _GenieServer()
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    root = _root(tmp_path, "http://127.0.0.1:1/")
    package = root / "characters/alpha"
    refs = package / "voice/refs"
    refs.mkdir(parents=True)
    (refs / "ref.txt").write_text("voice/refs/ref.wav|JA|reference|中性\n", encoding="utf-8")
    (refs / "ref.wav").write_bytes(_wav_bytes())
    (package / "model.ckpt").write_bytes(b"gpt")
    (package / "model.pth").write_bytes(b"sovits")
    manifest_path = package / "character.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["id"] = "alpha."  # Logical ID differs from directory name.
    (package / "legacy.ckpt").write_bytes(b"legacy-gpt")
    manifest["voice"] = {"gpt_model": "legacy.ckpt", "tone_refs": "voice/refs/ref.txt"}
    manifest["extensions"] = {
        "sakura.tts": {"enabled": True, "provider": p.PROVIDER_ID},
        "sakura.tts.gpt-sovits": {
            "gptModel": "model.ckpt", "sovitsModel": "model.pth",
            "toneRefs": "voice/refs/ref.txt", "refLang": "ja",
        },
    }
    if onnx_state == "missing_override":
        manifest["extensions"][p.PROVIDER_ID] = {"onnxModelDir": "missing-onnx"}
    elif onnx_state in {"empty", "zero", "valid"}:
        onnx = package / "voice/onnx"
        onnx.mkdir()
        if onnx_state != "empty":
            (onnx / "model.onnx").write_bytes(b"onnx" if onnx_state == "valid" else b"")
        if onnx_state == "valid":
            manifest["extensions"]["sakura.tts.gpt-sovits"]["gptModel"] = "removed.ckpt"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    repair_character_packages(root, issue_sink=lambda *_args: None)
    repaired = manifest_path.read_bytes()
    store = PluginCharacterStore(root)
    character = SimpleNamespace(
        get=lambda cid: store.get(p.PROVIDER_ID, cid), resolve_resource=store.resolve_resource,
    )
    context = _EffectContext()
    context.config = SimpleNamespace(get=lambda: {"endpointMode": "managed", "workDir": str(tmp_path)})
    context.data_path = lambda path: tmp_path / "provider" / path
    provider = p.GenieProvider(context, character, _LocalArtifacts(tmp_path / "artifacts"))
    provider.start()
    coordinator = provider._coordinator
    coordinator._config = replace(coordinator._config, api_url=f"http://127.0.0.1:{server.server_port}/")
    monkeypatch.setattr(coordinator, "_ensure_managed_endpoint", lambda _job: None)
    conversions = []

    def convert(gpt, sovits, staging, job):
        conversions.append((gpt.read_bytes(), sovits.read_bytes()))
        (staging / "model.onnx").write_bytes(b"converted")

    monkeypatch.setattr(coordinator, "_run_converter", convert)
    try:
        assert provider.warmup("alpha.") is True
        deadline = time.monotonic() + 5
        while len(server.calls) < 2:
            assert time.monotonic() < deadline
            time.sleep(0.01)
        job_id = provider.begin({"requestId": "after-warmup", "characterId": "alpha.", "text": "hello", "options": {}})
        assert isinstance(job_id, str)
        assert _direct_terminal(provider._jobs[job_id])["state"] == "succeeded"
        assert conversions == ([] if onnx_state == "valid" else [(b"gpt", b"sovits")])
        assert [endpoint for endpoint, _ in server.calls] == ["load_character", "set_reference_audio", "tts"]
        assert manifest_path.read_bytes() == repaired
        if onnx_state != "valid":
            (package / "model.ckpt").write_bytes(b"updated-gpt")
            job_id = provider.begin({"requestId": "changed-source", "characterId": "alpha.", "text": "hello", "options": {}})
            assert _direct_terminal(provider._jobs[job_id])["state"] == "succeeded"
            assert conversions[-1] == (b"updated-gpt", b"sovits")
            assert len(conversions) == 2
    finally:
        provider.close()
        server.shutdown()
        server.server_close()
        thread.join(1)


def test_genie_voice_inheritance_preserves_explicit_values() -> None:
    from plugins.builtin.sakura_genie.plugin import _effective_voice_extension

    manifest = {"voice": {"gpt_model": "legacy.ckpt", "sovits_model": "legacy.pth", "ref_lang": "ja"},
                "extensions": {"sakura.tts.gpt-sovits": {"gptModel": "studio.ckpt", "toneRefs": "studio.txt"}}}
    explicit = {"gptModel": "genie.ckpt", "toneRefs": "genie.txt", "onnxModelDir": "custom", "refLang": "zh"}
    assert _effective_voice_extension(manifest, explicit) == {**explicit, "sovitsModel": "legacy.pth"}
    assert _effective_voice_extension(manifest, {"gptModel": None})["gptModel"] is None


def test_genie_background_warmup_reports_conversion_failure(tmp_path: Path) -> None:
    from plugins.builtin.sakura_genie import plugin as p

    diagnostics = []
    config = p._ProviderConfig(True, "managed", "http://127.0.0.1:9881/", 5, tmp_path)
    coordinator = p._Coordinator(config, tmp_path / "cache", tmp_path / "log", diagnostics.append)
    try:
        coordinator.warmup(_conversion_voice(p, tmp_path))
        deadline = time.monotonic() + 5
        while not any(item["event"] == "tts.service.warmup_failed" for item in diagnostics):
            assert time.monotonic() < deadline
            time.sleep(0.01)
        failure = next(item for item in diagnostics if item["event"] == "tts.service.warmup_failed")
        assert failure["attributes"]["reason_code"] == "TTS_ONNX_CONVERSION_UNAVAILABLE"
        assert not list((tmp_path / "cache").glob("*.staging-*"))
    finally:
        coordinator.close()


def test_conversion_cancelled_at_export_completion_does_not_commit(tmp_path: Path, monkeypatch) -> None:
    from plugins.builtin.sakura_genie import plugin as p

    config = p._ProviderConfig(True, "managed", "http://127.0.0.1:9881/", 5, tmp_path)
    cache = tmp_path / "cache"
    coordinator = p._Coordinator(config, cache, tmp_path / "log")
    voice = _conversion_voice(p, tmp_path)
    operation = p._Warmup(voice)

    def convert(_gpt, _sovits, staging, job):
        (staging / "model.onnx").write_bytes(b"complete")
        job.cancel()

    monkeypatch.setattr(coordinator, "_run_converter", convert)
    try:
        with pytest.raises(p.OperationCancelled):
            coordinator._ensure_onnx_model(voice, operation)
        assert list(cache.iterdir()) == []
    finally:
        coordinator.close()


@pytest.mark.parametrize("terminal", ["finished", "failed", "cancelled"])
def test_conversion_events_reach_core_bridge_and_live_converter_log(tmp_path: Path, monkeypatch, terminal: str) -> None:
    from app.core_host.plugin_host_services import _DiagnosticsHostService
    from app.core_host.runtime_logging import CORE_BRIDGE_PREFIX, install_runtime_logging
    from plugins.builtin.sakura_genie import plugin as p

    work = tmp_path / "work"
    work.mkdir()
    (work / "convert.py").write_text(
        "import argparse,time\nfrom pathlib import Path\n"
        "p=argparse.ArgumentParser()\n"
        "p.add_argument('--pth');p.add_argument('--ckpt');p.add_argument('--out')\n"
        "args=p.parse_args()\nprint('converter active')\ntime.sleep(0.15)\n"
        "Path(args.out, 'model.onnx').write_bytes(b'onnx')\n"
        + ("raise SystemExit(7)\n" if terminal == "failed" else ""),
        encoding="utf-8",
    )
    monkeypatch.setattr(p, "find_usable_runtime_python", lambda _root: Path(sys.executable))
    monkeypatch.setattr(p, "_CONVERSION_LOG_INTERVAL_SECONDS", 0.02)
    config = p._ProviderConfig(True, "managed", "http://127.0.0.1:9881/", 5, work)
    voice = _conversion_voice(p, tmp_path)
    operation = p._Warmup(voice)
    raw_log = tmp_path / "logs/genie-converter.log"
    live_output = []
    host = _DiagnosticsHostService()

    def report(descriptor):
        host.call("emit", [p.PROVIDER_ID, descriptor])
        if descriptor["event"] == "tts.conversion.running":
            output = raw_log.read_text(encoding="utf-8")
            live_output.append(output)
            if terminal == "cancelled" and "converter active" in output:
                operation.cancel()

    coordinator = p._Coordinator(config, tmp_path / "cache", tmp_path / "logs/genie.log", report)
    stream = io.BytesIO()
    bridge = install_runtime_logging(stream)
    try:
        if terminal == "finished":
            model = coordinator._ensure_onnx_model(voice, operation)
            assert coordinator._ensure_onnx_model(voice, operation) == model
            assert coordinator._ensure_onnx_model(replace(voice, onnx_model_dir=model), operation) == model
        else:
            with pytest.raises(p.OperationCancelled if terminal == "cancelled" else RuntimeError):
                coordinator._ensure_onnx_model(voice, operation)
            assert list((tmp_path / "cache").iterdir()) == []
        assert any("converter active" in output for output in live_output)
        assert "converter active" in raw_log.read_text(encoding="utf-8")
    finally:
        coordinator.close()
        bridge.close()
    records = [json.loads(line.removeprefix(CORE_BRIDGE_PREFIX))
               for line in stream.getvalue().splitlines() if line.startswith(CORE_BRIDGE_PREFIX)]
    events = [record["event"] for record in records]
    assert events[:2] == ["tts.conversion.checking", "tts.conversion.started"]
    assert "tts.conversion.running" in events
    assert f"tts.conversion.{terminal}" in events
    assert events.count("tts.conversion.started") == 1
    noisy_events = {"tts.conversion.checking", "tts.conversion.cache_hit", "tts.conversion.reused", "tts.conversion.running"}
    assert all(record["severity"] == "debug" and record["verbosity"] == "debug"
               for record in records if record["event"] in noisy_events)
    if terminal == "finished":
        assert events[-4:] == ["tts.conversion.checking", "tts.conversion.cache_hit",
                               "tts.conversion.checking", "tts.conversion.reused"]
    else:
        assert events[-1] == f"tts.conversion.{terminal}"
        assert "tts.conversion.finished" not in events
    assert str(tmp_path).encode() not in stream.getvalue()
    assert b"converter active" not in stream.getvalue()


def test_onnx_conversion_cancel_kills_child_tree_and_cleans_staging(
    tmp_path: Path,
    monkeypatch,
) -> None:  # type: ignore[no-untyped-def]
    from plugins.builtin.sakura_genie import plugin as provider_module

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
        while (
            any(psutil.pid_exists(pid) for pid in pids)
            or list(cache.glob("*.staging-*"))
        ) and time.monotonic() < deadline:
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


def test_managed_genie_warmup_prepares_character_without_synthesis(
    tmp_path: Path,
    monkeypatch,
) -> None:  # type: ignore[no-untyped-def]
    from plugins.builtin.sakura_genie import plugin as provider_module

    config = provider_module._ProviderConfig(
        enabled=True,
        endpoint_mode="managed",
        api_url="http://127.0.0.1:9881/",
        timeout_seconds=5,
        work_dir=tmp_path,
    )
    coordinator = provider_module._Coordinator(
        config,
        tmp_path / "cache",
        tmp_path / "genie.log",
    )
    ready = threading.Event()
    calls: list[tuple[object, object]] = []

    def prepare(voice, tone, operation):  # type: ignore[no-untyped-def]
        operation.check_cancelled()
        calls.append((voice, tone))
        ready.set()
        return "sakura"

    monkeypatch.setattr(coordinator, "_prepare_voice", prepare)
    voice = SimpleNamespace(character_id="sakura")
    try:
        coordinator.warmup(voice)
        assert ready.wait(1)
        assert calls == [(voice, provider_module.DEFAULT_TONE)]
    finally:
        coordinator.close()
