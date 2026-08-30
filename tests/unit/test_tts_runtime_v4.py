from __future__ import annotations

import json
import os
import shutil
import sys
import threading
import time
import urllib.error
import wave
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace

from app.agent.tools import ToolRegistry
from app.core_host.plugin_runtime_application import PluginRuntimeApplication
from app.core_host.tts_boundary import TTSBoundary
from app.plugins.inventory import PluginDesiredStateStore, PluginInventory
from app.plugins.dependencies import PluginDependencyRoots
from app.storage.runtime_roots import RuntimeRoots


_CREDENTIAL = "0123456789abcdef0123456789abcdef"


class _TtsServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, kind: str) -> None:
        super().__init__(("127.0.0.1", 0), _TtsHandler)
        self.kind = kind
        self.delay = 0.0


class _TtsHandler(BaseHTTPRequestHandler):
    server: _TtsServer

    def do_GET(self) -> None:  # noqa: N802
        if self.server.kind == "genie" and self.path.endswith("openapi.json"):
            body = json.dumps({
                "paths": {
                    "/load_character": {},
                    "/set_reference_audio": {},
                    "/tts": {},
                }
            }).encode("utf-8")
        else:
            body = b"ready"
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length", "0"))
        payload = json.loads(self.rfile.read(length) or b"{}")
        if payload.get("text") == "cancel me":
            time.sleep(self.server.delay)
        body = _wav_bytes() if self.path.rstrip("/").endswith("tts") else b"ok"
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def log_message(self, _format: str, *_args: object) -> None:
        return None


def _wav_bytes() -> bytes:
    output = BytesIO()
    with wave.open(output, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(16000)
        handle.writeframes(b"\x01\x00" * 320)
    return output.getvalue()


def _runtime_root(
    tmp_path: Path,
    genie_endpoint: str,
    gpt_endpoint: str,
) -> RuntimeRoots:
    repository = Path(__file__).parents[2]
    distribution = tmp_path / "distribution"
    bundled = distribution / "plugins" / "builtin"
    bundled.mkdir(parents=True)
    for name in ("sakura_tts_hub", "sakura_genie", "sakura_gpt_sovits"):
        shutil.copytree(repository / "plugins" / "builtin" / name, bundled / name)
    user = tmp_path / "user"
    dependencies = PluginDependencyRoots(user, distribution_root=distribution)
    for plugin_id, directory in (
        ("sakura.tts.genie", bundled / "sakura_genie"),
        ("sakura.tts.gpt-sovits", bundled / "sakura_gpt_sovits"),
    ):
        declaration = dependencies.declaration(directory)
        assert declaration is not None
        dependency_root = distribution / "plugins" / "dependencies" / plugin_id
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
    (user / "data" / "plugins" / "sakura.tts.genie").mkdir(parents=True)
    (user / "data" / "plugins" / "sakura.tts.gpt-sovits").mkdir(parents=True)
    (user / "data" / "plugins" / "sakura.tts.genie" / "config.json").write_text(
        json.dumps({
            "endpointMode": "custom",
            "apiUrl": genie_endpoint,
            "timeoutSeconds": 5,
        }),
        encoding="utf-8",
    )
    (user / "data" / "plugins" / "sakura.tts.gpt-sovits" / "config.json").write_text(
        json.dumps({
            "endpointMode": "custom",
            "customBaseUrl": gpt_endpoint,
            "ttsPath": "/tts",
            "timeoutSeconds": 5,
        }),
        encoding="utf-8",
    )
    _write_genie_character(user, "genie-character")
    _write_gpt_character(user, "gpt-character")
    return RuntimeRoots(distribution, user)


def _write_base_character(user: Path, character_id: str, extensions: dict[str, object]) -> Path:
    root = user / "characters" / character_id
    root.mkdir(parents=True)
    (root / "card.md").write_text(character_id, encoding="utf-8")
    (root / "portrait.png").write_bytes(b"portrait")
    (root / "character.json").write_text(
        json.dumps({
            "id": character_id,
            "display_name": character_id,
            "card": "card.md",
            "portrait": {"default": "portrait.png"},
            "extensions": extensions,
        }),
        encoding="utf-8",
    )
    return root


def _write_genie_character(user: Path, character_id: str) -> None:
    _write_base_character(user, character_id, {
        "sakura.tts": {"enabled": True, "provider": "sakura.tts.genie"},
        "sakura.tts.genie": {"remoteCharacterName": "remote-genie"},
    })


def _write_gpt_character(user: Path, character_id: str) -> None:
    root = _write_base_character(user, character_id, {
        "sakura.tts": {"enabled": True, "provider": "sakura.tts.gpt-sovits"},
        "sakura.tts.gpt-sovits": {
            "toneRefs": "voice/refs/ref.txt",
            "refLang": "ja",
            "textLang": "ja",
        },
    })
    refs = root / "voice" / "refs"
    refs.mkdir(parents=True)
    (refs / "neutral.wav").write_bytes(_wav_bytes())
    (refs / "ref.txt").write_text(
        "voice/refs/neutral.wav|JA|reference|中性\n",
        encoding="utf-8",
    )


def _poll(application: PluginRuntimeApplication, request_id: str) -> dict[str, object]:
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        result = application.call_service("sakura.tts", "poll", request_id)
        assert isinstance(result, dict)
        if result["state"] != "running":
            return result
        time.sleep(0.02)
    raise AssertionError("TTS job did not reach a terminal state")


def _request(name: str, payload: dict[str, object]) -> dict[str, object]:
    return {
        "protocolMajor": 2,
        "protocolMinor": 2,
        "kind": "request",
        "generationId": "generation-third-party-hub",
        "generationCredential": _CREDENTIAL,
        "id": f"request-{time.monotonic_ns()}",
        "name": name,
        "payload": payload,
    }


def test_official_tts_v4_uses_three_processes_descriptor_and_job_ids(
    tmp_path: Path,
) -> None:
    genie = _TtsServer("genie")
    gpt = _TtsServer("gpt")
    genie_thread = threading.Thread(target=genie.serve_forever, daemon=True)
    gpt_thread = threading.Thread(target=gpt.serve_forever, daemon=True)
    genie_thread.start()
    gpt_thread.start()
    roots = _runtime_root(
        tmp_path,
        f"http://127.0.0.1:{genie.server_port}/",
        f"http://127.0.0.1:{gpt.server_port}",
    )
    application = PluginRuntimeApplication(
        roots,
        "generation-tts-v4",
        ToolRegistry(),
        PluginInventory(roots).scan().runtime_specs,
        call_timeout=1.0,
    )
    try:
        application.start()
        snapshot = application.public_snapshot()
        records = {item["pluginId"]: item for item in snapshot["plugins"]}
        assert set(records) == {
            "sakura.tts",
            "sakura.tts.genie",
            "sakura.tts.gpt-sovits",
        }
        assert all(item["state"] == "active" for item in records.values())
        pids = {item["pid"] for item in records.values()}
        assert len(pids) == 3
        assert os.getpid() not in pids

        providers = application.call_service("sakura.tts", "listProviders")
        assert providers == [
            {
                "providerId": "sakura.tts.genie",
                "label": "Genie TTS",
                "available": True,
            },
            {
                "providerId": "sakura.tts.gpt-sovits",
                "label": "GPT-SoVITS",
                "available": True,
            },
        ]

        for request_id, character_id in (
            ("through-genie", "genie-character"),
            ("through-gpt", "gpt-character"),
        ):
            started = application.call_service("sakura.tts", "begin", {
                "requestId": request_id,
                "characterId": character_id,
                "text": "hello",
                "options": {"tone": "中性"},
            })
            assert started == {
                "state": "running",
                "requestId": request_id,
                "providerId": (
                    "sakura.tts.genie"
                    if character_id == "genie-character"
                    else "sakura.tts.gpt-sovits"
                ),
            }
            terminal = _poll(application, request_id)
            assert terminal["state"] == "succeeded"
            application.release_committed_artifact(terminal["artifact"]["artifactId"])

        provider_job_id = application.call_service(
            "sakura.tts.provider.genie",
            "begin",
            {
                "requestId": "provider-job-id",
                "characterId": "genie-character",
                "text": "hello",
                "options": {},
            },
        )
        assert isinstance(provider_job_id, str) and provider_job_id.startswith("job_")
        application.call_service("sakura.tts.provider.genie", "cancel", provider_job_id)
        deadline = time.monotonic() + 2
        while True:
            terminal = application.call_service(
                "sakura.tts.provider.genie",
                "poll",
                provider_job_id,
            )
            if terminal["state"] != "running":
                break
            assert time.monotonic() < deadline
            time.sleep(0.02)
        assert terminal == {"state": "cancelled"}
    finally:
        application.close()
        genie.shutdown()
        gpt.shutdown()
        genie.server_close()
        gpt.server_close()
        genie_thread.join(1)
        gpt_thread.join(1)


def test_tts_provider_crash_leaves_hub_and_unrelated_provider_running(
    tmp_path: Path,
) -> None:
    genie = _TtsServer("genie")
    gpt = _TtsServer("gpt")
    gpt.delay = 2.0
    threads = [
        threading.Thread(target=genie.serve_forever, daemon=True),
        threading.Thread(target=gpt.serve_forever, daemon=True),
    ]
    for thread in threads:
        thread.start()
    roots = _runtime_root(
        tmp_path,
        f"http://127.0.0.1:{genie.server_port}/",
        f"http://127.0.0.1:{gpt.server_port}",
    )
    application = PluginRuntimeApplication(
        roots,
        "generation-tts-v4-crash",
        ToolRegistry(),
        PluginInventory(roots).scan().runtime_specs,
        call_timeout=0.3,
    )
    try:
        application.start()
        before = {
            item["pluginId"]: item
            for item in application.public_snapshot()["plugins"]
        }
        gpt_pid = before["sakura.tts.gpt-sovits"]["pid"]
        job_id = application.call_service(
            "sakura.tts.provider.gpt-sovits",
            "begin",
            {
                "requestId": "crash-with-artifact",
                "characterId": "gpt-character",
                "text": "cancel me",
                "options": {},
            },
        )
        assert isinstance(job_id, str) and job_id.startswith("job_")
        deadline = time.monotonic() + 1
        while application._host_services.artifact_count != 1:  # noqa: SLF001
            assert time.monotonic() < deadline
            time.sleep(0.01)
        os.kill(gpt_pid, 9)
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            after = {
                item["pluginId"]: item
                for item in application.public_snapshot()["plugins"]
            }
            if after["sakura.tts.gpt-sovits"]["state"] == "failed":
                break
            time.sleep(0.02)
        else:
            raise AssertionError(application.public_snapshot())
        assert after["sakura.tts"]["state"] == "active"
        assert after["sakura.tts"]["pid"] == before["sakura.tts"]["pid"]
        assert after["sakura.tts.genie"]["state"] == "active"
        assert after["sakura.tts.genie"]["pid"] == before["sakura.tts.genie"]["pid"]
        assert application._host_services.artifact_count == 0  # noqa: SLF001
        assert application.call_service("sakura.tts", "status", "gpt-character")[
            "available"
        ] is False
        assert application.call_service("sakura.tts", "status", "genie-character")[
            "available"
        ] is True
    finally:
        application.close()
        genie.shutdown()
        gpt.shutdown()
        genie.server_close()
        gpt.server_close()
        for thread in threads:
            thread.join(1)


def test_third_party_hub_replaces_official_hub_without_core_changes(
    tmp_path: Path,
) -> None:
    genie = _TtsServer("genie")
    thread = threading.Thread(target=genie.serve_forever, daemon=True)
    thread.start()
    roots = _runtime_root(
        tmp_path,
        f"http://127.0.0.1:{genie.server_port}/",
        "http://127.0.0.1:1",
    )
    replacement = roots.user_root / "plugins" / "user" / "third_party_hub"
    replacement.mkdir(parents=True)
    (replacement / "plugin.yaml").write_text(
        """
api: 4
id: com.example.tts-hub
name: Third Party TTS Hub
version: 1.0.0
entry: plugin:Plugin
provides: [sakura.tts]
requires: []
""".strip(),
        encoding="utf-8",
    )
    (replacement / "plugin.py").write_text(
        """
class Hub:
    def __init__(self, context):
        self.context = context
        self.providers = {}
        self.jobs = {}

    def registerProvider(self, descriptor):
        self.providers[descriptor["providerId"]] = dict(descriptor)
        return {"registered": True}

    def unregisterProvider(self, provider_id, service_key):
        descriptor = self.providers.get(provider_id)
        removed = descriptor is not None and descriptor["serviceKey"] == service_key
        if removed:
            self.providers.pop(provider_id, None)
        return {"removed": removed}

    def status(self, _character_id):
        descriptor = self.providers.get("sakura.tts.genie")
        available = False
        if descriptor is not None:
            try:
                available = bool(self.context.get(descriptor["serviceKey"]).status()["available"])
            except Exception:
                available = False
        return {
            "configured": True,
            "enabled": True,
            "providerId": "sakura.tts.genie",
            "available": available,
            "providers": [{
                "providerId": "sakura.tts.genie",
                "label": "Genie TTS",
                "available": available,
            }],
        }

    def begin(self, request):
        descriptor = self.providers["sakura.tts.genie"]
        job_id = self.context.get(descriptor["serviceKey"]).begin(request)
        self.jobs[request["requestId"]] = (descriptor["serviceKey"], job_id)
        return {
            "state": "running",
            "requestId": request["requestId"],
            "providerId": "sakura.tts.genie",
        }

    def poll(self, request_id):
        service_key, job_id = self.jobs[request_id]
        result = self.context.get(service_key).poll(job_id)
        value = {
            **result,
            "requestId": request_id,
            "providerId": "sakura.tts.genie",
        }
        if result["state"] != "running":
            self.jobs.pop(request_id, None)
        return value

    def cancel(self, request_id):
        binding = self.jobs.get(request_id)
        accepted = False if binding is None else bool(
            self.context.get(binding[0]).cancel(binding[1])
        )
        return {"accepted": accepted, "requestId": request_id}

class Plugin:
    def setup(self, context):
        context.provide(
            "sakura.tts",
            Hub(context),
            exports=(
                "registerProvider",
                "unregisterProvider",
                "status",
                "begin",
                "poll",
                "cancel",
            ),
        )
""".strip(),
        encoding="utf-8",
    )
    PluginDesiredStateStore(roots.user_root).write({
        "sakura.tts": False,
        "sakura.tts.gpt-sovits": False,
        "com.example.tts-hub": True,
    })
    inventory = PluginInventory(roots).scan()
    replacement_record = next(
        item for item in inventory.records if item.plugin_id == "com.example.tts-hub"
    )
    assert replacement_record.source == "user"
    application = PluginRuntimeApplication(
        roots,
        "generation-third-party-hub",
        ToolRegistry(),
        inventory.runtime_specs,
        call_timeout=1.0,
    )
    session = SimpleNamespace(
        plugin_application=application,
        character=SimpleNamespace(id="genie-character"),
    )
    boundary = TTSBoundary(
        "generation-third-party-hub",
        _CREDENTIAL,
        roots.user_root,
        session_provider=lambda: session,
    )
    try:
        application.start()
        records = {
            item["pluginId"]: item
            for item in application.public_snapshot()["plugins"]
        }
        assert records["sakura.tts"]["state"] == "disabled"
        assert records["com.example.tts-hub"]["state"] == "active"
        assert records["sakura.tts.genie"]["state"] == "active"
        assert boundary.authorize_segment(
            operation_id="replacement-hub",
            segment_index=0,
            text="hello",
            tone="中性",
            portrait="default",
            character_id="genie-character",
            history_entry_id="entry-replacement-hub",
        ) is True
        result = boundary.handle(
            _request(
                "tts.synthesis.start",
                {"operationId": "replacement-hub", "segmentIndex": 0},
            )
        )
        assert result["ok"] is True
    finally:
        boundary.close()
        application.close()
        genie.shutdown()
        genie.server_close()
        thread.join(1)


def test_genie_bundle_action_installs_and_updates_plugin_config(tmp_path: Path) -> None:
    from plugins.builtin.sakura_genie import _bundle

    updates: list[dict[str, object]] = []

    def install(_entry, user_root, **_callbacks):  # type: ignore[no-untyped-def]
        work_dir = Path(user_root) / "tts" / "cpu"
        python = work_dir / "runtime" / "bin" / "python"
        python.parent.mkdir(parents=True)
        python.write_text("", encoding="utf-8")
        return _bundle.TTSBundleInstallResult(work_dir)

    resource = _bundle.TTSBundleResource(
        user_root=tmp_path,
        config_get=lambda: {"endpointMode": "managed"},
        config_update=lambda values: updates.append(dict(values)),
        entry=lambda: _bundle.GENIE_TTS,
        custom_endpoint=lambda _values: False,
        installer=install,
    )
    try:
        started = resource.start({})
        assert started["message"] == "已开始安装组件。"
        deadline = time.monotonic() + 2
        while resource.load()["bundleResource"]["taskState"] != "succeeded":
            assert time.monotonic() < deadline
            time.sleep(0.01)
        assert updates == [{"workDir": str(tmp_path / "tts" / "cpu")}]
    finally:
        resource.close()


def test_gpt_sovits_bundle_action_updates_runtime_paths(tmp_path: Path) -> None:
    from plugins.builtin.sakura_gpt_sovits import _bundle

    updates: list[dict[str, object]] = []
    entry = _bundle.GPT_SOVITS_MACOS

    def install(_entry, user_root, **_callbacks):  # type: ignore[no-untyped-def]
        installed = Path(user_root) / "tts" / entry.key
        work_dir = installed / "GPT-SoVITS"
        python = installed / "miniforge3" / "envs" / "gpt-sovits310" / "bin" / "python"
        config = work_dir / "GPT_SoVITS" / "configs" / "tts_infer_sakura_macos.yaml"
        work_dir.mkdir(parents=True)
        (work_dir / "api_v2.py").write_text("", encoding="utf-8")
        python.parent.mkdir(parents=True)
        python.write_text("", encoding="utf-8")
        config.parent.mkdir(parents=True)
        config.write_text("", encoding="utf-8")
        return _bundle.TTSBundleInstallResult(work_dir, python, config)

    resource = _bundle.TTSBundleResource(
        user_root=tmp_path,
        config_get=lambda: {"endpointMode": "managed"},
        config_update=lambda values: updates.append(dict(values)),
        entry=lambda: entry,
        custom_endpoint=lambda _values: False,
        installer=install,
    )
    try:
        resource.start({})
        deadline = time.monotonic() + 2
        while resource.load()["bundleResource"]["taskState"] != "succeeded":
            assert time.monotonic() < deadline
            time.sleep(0.01)
        assert updates == [{
            "workDir": str(tmp_path / "tts" / entry.key / "GPT-SoVITS"),
            "pythonPath": str(tmp_path / "tts" / entry.key / "miniforge3" / "envs" / "gpt-sovits310" / "bin" / "python"),
            "ttsConfigPath": str(tmp_path / "tts" / entry.key / "GPT-SoVITS" / "GPT_SoVITS" / "configs" / "tts_infer_sakura_macos.yaml"),
        }]
    finally:
        resource.close()


def _wait_bundle_terminal(resource: object) -> dict[str, object]:
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        value = resource.load()["bundleResource"]
        if value["taskState"] not in {"queued", "running"}:
            return value
        time.sleep(0.01)
    raise AssertionError("bundle resource did not reach a terminal state")


def test_genie_bundle_failure_exposes_sanitized_network_code(tmp_path: Path) -> None:
    from plugins.builtin.sakura_genie import _bundle

    def install(_entry, _user_root, **callbacks):  # type: ignore[no-untyped-def]
        callbacks["on_status"]("download")
        raise urllib.error.URLError("https://secret.invalid/?token=private")

    resource = _bundle.TTSBundleResource(
        user_root=tmp_path,
        config_get=lambda: {},
        config_update=lambda _values: None,
        entry=lambda: _bundle.GENIE_TTS,
        custom_endpoint=lambda _values: False,
        installer=install,
    )
    try:
        resource.start({})
        failed = _wait_bundle_terminal(resource)
        assert failed["taskState"] == "failed"
        assert failed["availableActionIds"] == ["retryBundle"]
        assert "DOWNLOAD_NETWORK_FAILED" in failed["detail"]
        assert "secret.invalid" not in failed["detail"]
        assert "private" not in failed["detail"]
    finally:
        resource.close()


def test_gpt_sovits_bundle_failure_exposes_sanitized_extract_code(tmp_path: Path) -> None:
    from plugins.builtin.sakura_gpt_sovits import _bundle

    def install(_entry, _user_root, **callbacks):  # type: ignore[no-untyped-def]
        callbacks["on_status"]("extract")
        raise RuntimeError(r"private path C:\\Users\\name\\bundle.7z")

    resource = _bundle.TTSBundleResource(
        user_root=tmp_path,
        config_get=lambda: {},
        config_update=lambda _values: None,
        entry=lambda: _bundle.GPT_SOVITS_STANDARD,
        custom_endpoint=lambda _values: False,
        installer=install,
    )
    try:
        resource.start({})
        failed = _wait_bundle_terminal(resource)
        assert failed["taskState"] == "failed"
        assert "EXTRACT_FAILED" in failed["detail"]
        assert "Users" not in failed["detail"]
    finally:
        resource.close()


def test_tts_bundle_error_taxonomy_covers_integrity_and_extractor_failures() -> None:
    from plugins.builtin.sakura_genie import _bundle as genie_bundle
    from plugins.builtin.sakura_gpt_sovits import _bundle as gpt_bundle

    for bundle in (genie_bundle, gpt_bundle):
        assert bundle._failure_code(RuntimeError("TTS_BUNDLE_SIZE_MISMATCH"), "download") == "DOWNLOAD_SIZE_MISMATCH"
        assert bundle._failure_code(RuntimeError("TTS_BUNDLE_SHA256_MISMATCH"), "download") == "DOWNLOAD_CHECKSUM_MISMATCH"
        assert bundle._failure_code(RuntimeError("TTS_BUNDLE_EXTRACTOR_MISSING"), "extract") == "EXTRACTOR_MISSING"


def test_tts_bundle_reuses_complete_part_without_out_of_range_request(
    tmp_path: Path,
) -> None:
    from plugins.builtin.sakura_genie import _bundle as genie_bundle
    from plugins.builtin.sakura_gpt_sovits import _bundle as gpt_bundle

    payload = b"complete bundle"
    for bundle in (genie_bundle, gpt_bundle):
        root = tmp_path / bundle.__package__.rsplit(".", 1)[-1]
        root.mkdir()
        archive = root / "fixture.7z"
        archive.with_name("fixture.7z.part").write_bytes(payload)
        entry = bundle.TTSBundleEntry(
            key="fixture",
            label="Fixture",
            filename=archive.name,
            download_url="https://must-not-be-opened.invalid/fixture.7z",
            size=len(payload),
            sha256=bundle.hashlib.sha256(payload).hexdigest(),
        )
        bundle._download(
            entry,
            archive,
            check_cancel=lambda: None,
            on_progress=lambda _value: None,
            on_download=lambda _value: None,
        )
        assert archive.read_bytes() == payload
        assert not archive.with_name("fixture.7z.part").exists()
