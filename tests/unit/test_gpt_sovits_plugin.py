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

import pytest

from app.agent.tools import ToolRegistry
from app.core_host.plugin_runtime_application import PluginRuntimeApplication
from app.core_host.tts_boundary import TTSBoundary
from app.plugins.dependencies import PluginDependencyRoots
from app.plugins.inventory import PluginInventory
from app.storage.runtime_roots import RuntimeRoots


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
    shutil.copytree(
        repository / "plugins" / "builtin" / "sakura_gpt_sovits",
        plugins / "sakura_gpt_sovits",
    )
    plugin_root = plugins / "sakura_gpt_sovits"
    declaration = PluginDependencyRoots(root).declaration(plugin_root)
    assert declaration is not None
    dependency_root = root / "plugins/dependencies/sakura.tts.gpt-sovits"
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
                    "sakura.tts": {
                        "enabled": True,
                        "provider": "sakura.tts.gpt-sovits",
                    },
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
    raise AssertionError("GPT-SoVITS plugin job did not finish")


def test_gpt_provider_availability_requires_runtime_or_valid_custom_endpoint() -> None:
    from plugins.builtin.sakura_gpt_sovits.plugin import _config_available, _parse_config

    assert _config_available(_parse_config({})) is False
    assert _config_available(
        _parse_config({"customBaseUrl": "https://tts.example.com"})
    ) is True
    assert _config_available(
        _parse_config({
            "endpointMode": "managed",
            "customBaseUrl": "not-an-active-endpoint",
        })
    ) is False
    assert _parse_config({
        "endpointMode": "custom",
        "customBaseUrl": "https://tts.example.com",
    }).custom_base_url == "https://tts.example.com"
    with pytest.raises(ValueError, match="TTS_CONFIG_INVALID"):
        _parse_config({"endpointMode": "custom", "customBaseUrl": ""})
    with pytest.raises(ValueError, match="TTS_CONFIG_INVALID"):
        _parse_config({"customBaseUrl": "not-an-endpoint"})


def test_real_gpt_sovits_provider_is_character_scoped_serial_and_core_consumed(
    tmp_path: Path,
) -> None:
    server = _TtsServer()
    endpoint = f"http://127.0.0.1:{server.server_port}"
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
        assert by_id["sakura.tts.gpt-sovits"]["state"] == "active"
        warmup = worker.call_service("sakura.tts", "warmup", "alpha")
        assert warmup["accepted"] is False
        assert warmup["reasonCode"] == "TTS_WARMUP_SKIPPED"
        assert server.get_paths == []

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
        assert worker.public_snapshot()["state"] == "ready"

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


def test_managed_gpt_warmup_prepares_service_and_weights_in_coordinator(
    tmp_path: Path,
    monkeypatch,
) -> None:  # type: ignore[no-untyped-def]
    from plugins.builtin.sakura_gpt_sovits import plugin as provider_module

    config = provider_module._ProviderConfig(
        enabled=True,
        custom_base_url=None,
        tts_path="/tts",
        timeout_seconds=5,
        remote_reference_root=None,
        work_dir=tmp_path,
        python_path=None,
        tts_config_path=None,
    )
    coordinator = provider_module._Coordinator(config)
    ready = threading.Event()
    calls: list[str] = []

    class Supervisor:
        def _ensure_service_available(self, _fail) -> bool:  # type: ignore[no-untyped-def]
            calls.append("service")
            return True

        def _ensure_character_weights(
            self,
            _fail,
            *,
            cancel_checker=None,
        ) -> bool:  # type: ignore[no-untyped-def]
            calls.append("weights")
            if cancel_checker is not None:
                cancel_checker()
            ready.set()
            return True

    monkeypatch.setattr(
        coordinator,
        "_configure",
        lambda _voice: (SimpleNamespace(), Supervisor()),
    )
    try:
        coordinator.warmup(SimpleNamespace(character_id="sakura"))
        assert ready.wait(1)
        assert calls == ["service", "weights"]
    finally:
        coordinator.close()


def test_managed_gpt_warmup_reports_configuration_failure_fallback(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from plugins.builtin.sakura_gpt_sovits import plugin as provider_module

    config = provider_module._ProviderConfig(
        enabled=True,
        custom_base_url=None,
        tts_path="/tts",
        timeout_seconds=5,
        remote_reference_root=None,
        work_dir=tmp_path,
        python_path=None,
        tts_config_path=None,
    )
    diagnostics: list[tuple[str, str, dict[str, str]]] = []
    reported = threading.Event()

    def capture(event: str, severity: str, attributes) -> None:  # type: ignore[no-untyped-def]
        diagnostics.append((event, severity, dict(attributes)))
        reported.set()

    coordinator = provider_module._Coordinator(config, capture)

    monkeypatch.setattr(
        coordinator,
        "_configure",
        lambda _voice: (_ for _ in ()).throw(ValueError("TTS_RUNTIME_INVALID")),
    )
    try:
        coordinator.warmup(SimpleNamespace(character_id="sakura"))
        assert reported.wait(1)
        assert diagnostics == [
            (
                "tts.service.warmup_failed",
                "warning",
                {
                    "provider": "sakura.tts.gpt-sovits",
                    "reason_code": "TTS_RUNTIME_INVALID",
                    "stage": "configuration",
                    "error_type": "ValueError",
                },
            )
        ]
    finally:
        coordinator.close()


def test_managed_runtime_reports_five_stages_once_and_replays_after_restart(
    monkeypatch,
) -> None:  # type: ignore[no-untyped-def]
    from plugins.builtin.sakura_gpt_sovits import _support

    diagnostics: list[tuple[str, str, dict[str, str]]] = []
    settings = SimpleNamespace(
        api_url="http://127.0.0.1:9880/tts",
        timeout_seconds=1,
        gpt_model_path=Path("gpt.ckpt"),
        sovits_model_path=Path("sovits.pth"),
    )
    runtime = _support._ManagedRuntime(
        settings,
        base_dir=Path("."),
        is_closed=lambda: False,
        diagnostic=lambda event, severity, attributes: diagnostics.append(
            (event, severity, dict(attributes))
        ),
    )

    class Process:
        def poll(self) -> None:
            return None

    def start(_fail) -> bool:  # type: ignore[no-untyped-def]
        runtime._server_process = Process()
        return True

    monkeypatch.setattr(runtime, "_start", start)
    monkeypatch.setattr(_support, "_probe_tcp", lambda *_args: False)
    monkeypatch.setattr(_support, "_probe_http", lambda *_args: True)
    monkeypatch.setattr(_support, "_read_url", lambda *_args, **_kwargs: b"ok")
    monkeypatch.setattr(_support, "terminate_process_tree", lambda *_args, **_kwargs: None)

    assert runtime.ensure_available(pytest.fail) is True
    assert runtime.ensure_weights(pytest.fail, None) is True
    assert runtime.ensure_available(pytest.fail) is True
    assert runtime.ensure_weights(pytest.fail, None) is True
    lifecycle = [
        "tts.service.started",
        "tts.service.waiting_ready",
        "tts.service.ready",
        "tts.weights.loading",
        "tts.weights.ready",
    ]
    assert [event for event, _severity, _attributes in diagnostics] == lifecycle
    assert diagnostics[2][2]["elapsed_ms"]
    assert diagnostics[4][2]["elapsed_ms"]

    assert runtime.restart_after_failure(400, "tts failed: Broken pipe") is True
    assert runtime.ensure_available(pytest.fail) is True
    assert runtime.ensure_weights(pytest.fail, None) is True
    assert [event for event, _severity, _attributes in diagnostics] == lifecycle * 2


def test_managed_runtime_reports_timeout_and_weight_failure_stage(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from plugins.builtin.sakura_gpt_sovits import _support

    diagnostics: list[tuple[str, str, dict[str, str]]] = []
    settings = SimpleNamespace(
        api_url="http://127.0.0.1:9880/tts",
        timeout_seconds=0,
        gpt_model_path=Path("gpt.ckpt"),
        sovits_model_path=Path("sovits.pth"),
    )
    runtime = _support._ManagedRuntime(
        settings,
        base_dir=Path("."),
        is_closed=lambda: False,
        diagnostic=lambda event, severity, attributes: diagnostics.append(
            (event, severity, dict(attributes))
        ),
    )

    class Process:
        def poll(self) -> None:
            return None

    def start(_fail) -> bool:  # type: ignore[no-untyped-def]
        runtime._server_process = Process()
        return True

    monkeypatch.setattr(runtime, "_start", start)
    monkeypatch.setattr(_support, "_probe_tcp", lambda *_args: False)
    errors: list[str] = []
    assert runtime.ensure_available(errors.append) is False
    assert errors == ["TTS_RUNTIME_TIMEOUT"]
    failed = diagnostics[-1]
    assert failed[0] == "tts.service.failed"
    assert failed[2]["reason_code"] == "TTS_RUNTIME_TIMEOUT"
    assert failed[2]["status"] == "failed"
    assert failed[2]["elapsed_ms"]

    runtime._service_ready = True
    runtime._server_process = Process()
    settings.timeout_seconds = 1

    def read_url(request, **_kwargs) -> bytes:  # type: ignore[no-untyped-def]
        if "set_sovits_weights" in request.full_url:
            raise TimeoutError("private detail")
        return b"ok"

    monkeypatch.setattr(_support, "_read_url", read_url)
    errors.clear()
    assert runtime.ensure_weights(errors.append, None) is False
    assert errors == ["TTS_WEIGHTS_UNAVAILABLE"]
    failed = diagnostics[-1]
    assert failed[0] == "tts.weights.failed"
    assert failed[2]["stage"] == "sovits_weights"
    assert failed[2]["error_type"] == "TimeoutError"
    assert "private detail" not in str(failed)


def test_disabling_provider_cancels_active_job_releases_artifact_and_can_restore(
    tmp_path: Path,
) -> None:
    server = _TtsServer()
    server.delay_seconds = 2
    root = _root(tmp_path, f"http://127.0.0.1:{server.server_port}")
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
            "state"
        ] == "cancelled"

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
    worker = _worker(root, call_timeout=0.5)
    try:
        worker.start()
        assert worker.wait_until_loaded(timeout=5)
        snapshot = worker.public_snapshot()
        by_id = {item["pluginId"]: item for item in snapshot["plugins"]}
        assert by_id["sakura.tts.gpt-sovits"]["state"] == "active"
        status = worker.call_service("sakura.tts", "status", "alpha")
        assert status["configured"] is True
        assert status["providerId"] == "sakura.tts.gpt-sovits"
        assert status["available"] is False
        sections = worker.settings_sections("voice")
        assert len(sections) == 1
        assert sections[0]["pluginId"] == "sakura.tts.gpt-sovits"
        assert sections[0]["sectionId"] == "runtime"
        fields = {field["key"]: field for field in sections[0]["fields"]}
        assert "enabled" not in fields
        assert fields["endpointMode"]["label"] == "服务来源"
        assert fields["workDir"]["placement"] == "advanced"
        assert fields["customBaseUrl"]["enabledWhen"] == {
            "field": "endpointMode",
            "equals": "custom",
        }
        assert fields["timeoutSeconds"]["enabledWhen"] is None
        assert worker.settings_sections("about") == []
        component = worker.settings_sections("plugin")
        assert len(component) == 1
        assert component[0]["pluginId"] == "sakura.tts.gpt-sovits"
        assert component[0]["values"]["bundleResource"]["applicability"] == "not_required"
        assert component[0]["values"]["bundleResource"]["availableActionIds"] == []
        saved = worker.settings_save(
            "sakura.tts.gpt-sovits",
            "runtime",
            {"endpointMode": "managed", "timeoutSeconds": 60},
        )
        assert saved["applicationState"] == "applied"
        persisted = json.loads(
            (
                root
                / "data"
                / "plugins"
                / "sakura.tts.gpt-sovits"
                / "config.json"
            ).read_text(encoding="utf-8")
        )
        assert persisted["endpointMode"] == "managed"
        assert persisted["customBaseUrl"] == "http://127.0.0.1:1"
    finally:
        worker.close()


def test_managed_bundle_binding_replaces_stale_optional_paths(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from plugins.builtin.sakura_gpt_sovits import _bundle
    from plugins.builtin.sakura_gpt_sovits import plugin as provider_module

    work_dir = tmp_path / "tts" / "gpt"
    runtime = work_dir / "runtime"
    runtime.mkdir(parents=True)
    (work_dir / "api_v2.py").write_text("", encoding="utf-8")
    runtime_python = (
        runtime / "python.exe"
        if sys.platform == "win32"
        else runtime / "bin" / "python3"
    )
    runtime_python.parent.mkdir(parents=True, exist_ok=True)
    runtime_python.write_bytes(b"runtime")
    if sys.platform != "win32":
        runtime_python.chmod(0o755)
    result = _bundle.TTSBundleInstallResult(work_dir=work_dir)
    monkeypatch.setattr(provider_module, "installed_bundle_result", lambda _root: result)

    patch = provider_module._startup_config_patch(
        {
            "endpointMode": "managed",
            "workDir": str(tmp_path / "old-runtime"),
            "pythonPath": str(tmp_path / "old-python.exe"),
            "ttsConfigPath": str(tmp_path / "old-config.yaml"),
        },
        tmp_path,
    )

    assert patch == {
        "workDir": str(work_dir),
        "pythonPath": "",
        "ttsConfigPath": "",
    }
    config = provider_module._parse_config({"endpointMode": "managed", **patch})
    assert provider_module._config_available(config) is True


def test_explicit_managed_mode_ignores_retained_custom_endpoint() -> None:
    from plugins.builtin.sakura_gpt_sovits import plugin as provider_module

    assert provider_module._uses_custom_endpoint({
        "endpointMode": "managed",
        "customBaseUrl": "http://127.0.0.1:9880",
    }) is False
    assert provider_module._uses_custom_endpoint({
        "customBaseUrl": "http://127.0.0.1:9880",
    }) is True


def test_bundle_install_clears_stale_optional_runtime_overrides(tmp_path: Path) -> None:
    from plugins.builtin.sakura_gpt_sovits import _bundle

    updates: list[dict[str, object]] = []
    work_dir = tmp_path / "tts" / "gpt"
    resource = _bundle.TTSBundleResource(
        user_root=tmp_path,
        config_get=lambda: {
            "endpointMode": "managed",
            "pythonPath": str(tmp_path / "old-python.exe"),
            "ttsConfigPath": str(tmp_path / "old-config.yaml"),
        },
        config_update=lambda values: updates.append(dict(values)),
        entry=lambda: _bundle.GPT_SOVITS_STANDARD,
        custom_endpoint=lambda _values: False,
        installer=lambda *_args, **_kwargs: _bundle.TTSBundleInstallResult(work_dir),
    )

    resource._run(_bundle.GPT_SOVITS_STANDARD)

    assert updates == [{
        "workDir": str(work_dir),
        "pythonPath": "",
        "ttsConfigPath": "",
    }]


@pytest.mark.skipif(sys.platform != "win32", reason="managed Windows bundle layout")
def test_installed_managed_bundle_with_stale_paths_is_available_after_startup(
    tmp_path: Path,
) -> None:
    from plugins.builtin.sakura_gpt_sovits import _bundle

    root = _root(
        tmp_path,
        "",
        config_patch={
            "endpointMode": "managed",
            "customBaseUrl": "",
            "workDir": str(tmp_path / "old-runtime"),
            "pythonPath": str(tmp_path / "old-python.exe"),
            "ttsConfigPath": str(tmp_path / "old-config.yaml"),
        },
    )
    entry = _bundle.recommend_gpt_sovits_bundle()
    assert entry is not None
    short_name = {
        _bundle.GPT_SOVITS_STANDARD.key: "gpt",
        _bundle.GPT_SOVITS_NVIDIA50.key: "g50",
    }[entry.key]
    work_dir = root / "tts" / short_name
    runtime = work_dir / "runtime"
    runtime.mkdir(parents=True)
    (work_dir / "api_v2.py").write_text("", encoding="utf-8")
    (runtime / "python.exe").write_bytes(b"runtime")
    worker = _worker(root, call_timeout=0.5)
    try:
        worker.start()
        assert worker.wait_until_loaded(timeout=5)
        assert worker.call_service("sakura.tts", "status", "alpha")["available"] is True
        config = json.loads(
            (root / "data/plugins/sakura.tts.gpt-sovits/config.json").read_text(
                encoding="utf-8"
            )
        )
        assert config["workDir"] == str(work_dir)
        assert config["pythonPath"] == ""
        assert config["ttsConfigPath"] == ""
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
    from plugins.builtin.sakura_gpt_sovits import plugin as provider_module

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
                    "sakura.tts": {
                        "enabled": True,
                        "provider": "sakura.tts.gpt-sovits",
                    },
                    "sakura.tts.gpt-sovits": {"toneRefs": "../alpha/voice/refs/ref.txt"},
                },
            }
        ),
        encoding="utf-8",
    )
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    worker = _worker(root, call_timeout=0.5)
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
        assert escaped_result["errorCode"] == "CHARACTER_RESOURCE_INVALID"
        assert getattr(worker._host_services, "artifact_count") == 0
    finally:
        worker.close()
        server.shutdown()
        server.server_close()
        server_thread.join(1)
