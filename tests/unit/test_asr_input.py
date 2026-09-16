from __future__ import annotations

import json
import io
import shutil
import time
import wave
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from app.agent.tools import ToolRegistry
from app.core_host.asr_boundary import ASRBoundary
from app.core_host import asr_boundary
from app.core_host.audio_input import AudioInputError, AudioInputResources
from app.core_host.plugin_runtime_application import PluginRuntimeApplication
from app.plugins.host_services import HOST_CALLER
from app.plugins.inventory import PluginInventory
from app.plugins.runtime_v4 import PluginRuntimeError
from app.storage.runtime_roots import RuntimeRoots


def wav(path: Path, rate=16000):
    with wave.open(str(path), "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(rate)
        out.writeframes(b"\x01\x00" * 1600)


def until(callback, predicate, timeout=8):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        result = callback()
        if predicate(result):
            return result
        time.sleep(0.02)
    raise AssertionError(f"ASR did not reach expected state: {result}")


@pytest.fixture
def runtime(tmp_path):
    distribution = tmp_path / "distribution"
    bundled = distribution / "plugins" / "builtin"
    bundled.mkdir(parents=True)
    shutil.copytree(Path(__file__).parents[2] / "plugins/builtin/sakura_asr_hub", bundled / "hub")
    for name in ("one", "two"):
        root = bundled / name
        root.mkdir()
        (root / "plugin.yaml").write_text(f"""api: 4
id: test.asr.{name}
name: Test {name}
version: 1.0.0
enabled: true
entry: plugin:Plugin
provides: [test.asr.{name}.service]
requires: [sakura.asr, sakura.host.audio_input]
""", encoding="utf-8")
        (root / "plugin.py").write_text('''import threading, time, uuid
from pathlib import Path

class Provider:
    def __init__(self, context):
        self.language = "ja" if context.plugin_id.endswith("one") else "en"
        self.audio = context.get("sakura.host.audio_input")
        self.hub = context.get("sakura.asr")
        self.jobs = {}
        self.reading = False
        self.version = uuid.uuid4().hex
    def status(self):
        return {"state":"ready", "available":True, "configVersion":self.version, "language":self.language}
    def warmup(self): return self.status()
    def begin(self, request):
        job_id = uuid.uuid4().hex
        self.jobs[job_id] = {"state":"running"}
        def run():
            lease = self.audio.acquire(request["audio"]["resourceId"])
            self.reading = True
            try:
                time.sleep(0.45)
                assert Path(lease["path"]).is_file()
                self.jobs[job_id] = {"state":"succeeded", "text":"测试完整文字", "language":request["language"]}
            finally:
                self.reading = False
                self.audio.release(lease["leaseId"])
        threading.Thread(target=run, daemon=True).start()
        return job_id
    def poll(self, job_id): return self.jobs[job_id]
    def cancel(self, job_id): return True  # Native reader cannot be interrupted.
    def probe(self): return {"reading":self.reading, "jobs":len(self.jobs)}
    def impersonate(self):
        return self.hub.registerProvider({"providerId":"test.asr.one", "serviceKey":"test.asr.one.service", "label":"spoofed", "processingLocation":"remote"})

class Plugin:
    def setup(self, context):
        provider = Provider(context)
        service = "test.asr.NAME.service"
        context.provide(service, provider, exports=("status","warmup","begin","poll","cancel","probe","impersonate"))
        context.get("sakura.asr").registerProvider({"providerId":"test.asr.NAME", "serviceKey":service, "label":"NAME", "processingLocation":"local"})
'''.replace("NAME", name), encoding="utf-8")
    roots = RuntimeRoots(distribution, tmp_path / "user")
    application = PluginRuntimeApplication(roots, "asr-test", ToolRegistry(), PluginInventory(roots).scan().runtime_specs)
    application.start()
    assert len(application.public_snapshot()["plugins"]) == 3
    assert all(item["state"] == "active" for item in application.public_snapshot()["plugins"]), application.public_snapshot()
    application.call_service("sakura.asr", "configure", {"selectedProviderId": "test.asr.one"})
    character = {"characterId": "alpha"}
    boundary = ASRBoundary("asr-test", "secret", user_root=roots.user_root, plugin_application_provider=lambda: application,
                           character_presentation_provider=lambda: character)
    def request(name, **payload):
        return boundary.handle({"name": name, "id": "test-call", "generationId": "asr-test",
                                "generationCredential": "secret", "payload": payload})
    yield application, boundary, request, character
    boundary.close()
    application.close()
    assert application.audio_input.count == 0


def ready(request, recording_id="record-1", purpose="draft"):
    assert request("asr.input.prepare", recordingId=recording_id, contextId="alpha-conversation",
                   draftVersion=4, selectionStart=1, selectionEnd=3, purpose=purpose)["ok"]
    result = until(lambda: request("asr.input.poll", recordingId=recording_id),
                   lambda r: r.get("payload", {}).get("state") != "preparing")
    assert result["payload"]["state"] == "ready", result
    target = request("asr.input.capture_target", recordingId=recording_id)
    assert target["ok"], target
    path = Path(target["payload"]["path"])
    assert request("asr.input.capture_ready", recordingId=recording_id)["payload"]["state"] == "recording"
    return path


def test_real_process_route_selection_and_exactly_one_draft_result(runtime):
    app, boundary, request, _ = runtime
    path = ready(request)
    assert boundary._tasks["record-1"].language == "ja"
    assert request("asr.settings.save", language="zh")["error"]["code"] == "ASR_SELECTION_INVALID"
    # Switching the selected engine affects the next recording, not this audio.
    assert request("asr.settings.save", selectedProviderId="test.asr.two")["ok"]
    wav(path)
    assert request("asr.input.submit", recordingId="record-1")["payload"]["state"] == "recognizing"
    assert request("asr.input.submit", recordingId="record-1")["error"]["code"] == "ASR_STATE_INVALID"
    until(lambda: boundary._tasks["record-1"].state, lambda s: s == "succeeded")
    assert request("asr.input.capture_status", recordingId="record-1")["payload"] == {
        "recordingId": "record-1", "state": "succeeded"}
    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(lambda _: request("asr.input.poll", recordingId="record-1"), range(2)))
    transcripts = [r["payload"] for r in results if r["payload"]["state"] == "succeeded"]
    assert len(transcripts) == 1
    assert transcripts[0]["text"] == "测试完整文字"
    assert transcripts[0]["providerId"] == "test.asr.one"
    assert transcripts[0]["draftVersion"] == 4 and transcripts[0]["selectionEnd"] == 3
    assert {r["payload"]["state"] for r in results} == {"succeeded", "consumed"}
    until(lambda: path.exists(), lambda exists: not exists)
    assert app.call_service("test.asr.two.service", "probe")["jobs"] == 0
    # A fresh input now uses the new explicit selection.
    ready(request, "record-2")
    assert boundary._tasks["record-2"].provider_id == "test.asr.two"
    request("asr.input.cancel", recordingId="record-2")
    request("asr.input.capture_discarded", recordingId="record-2")


@pytest.mark.parametrize("purpose", ["draft", "test"])
def test_cancel_native_reader_retains_file_then_cleans_without_ui_poll(runtime, purpose):
    app, boundary, request, _ = runtime
    path = ready(request, purpose=purpose)
    wav(path)
    request("asr.input.submit", recordingId="record-1")
    until(lambda: app.call_service("test.asr.one.service", "probe"), lambda p: p["reading"])
    assert request("asr.input.cancel", recordingId="record-1")["payload"]["state"] == "cancelled"
    assert path.is_file(), "Native reader still owns a lease"
    until(lambda: app.audio_input.count, lambda n: n == 0)
    assert not path.exists()
    result = request("asr.input.poll", recordingId="record-1")["payload"]
    assert result["state"] == "cancelled" and "text" not in result


def test_settings_test_routes_explicit_provider_and_device_without_saving_or_draft_delivery(runtime):
    app, boundary, request, _ = runtime
    assert request("asr.settings.save", inputDeviceId="device-saved")["ok"]
    settings = request("asr.settings.get")["payload"]
    assert settings["inputDeviceId"] == "device-saved"
    assert settings["hubPluginId"] == app.service_identity("sakura.asr")["providerId"]
    assert request("asr.input.prepare", recordingId="test-input", contextId="settings-test",
                   purpose="test", providerId="test.asr.two", inputDeviceId="device-preview")["ok"]
    until(lambda: request("asr.input.poll", recordingId="test-input"),
          lambda r: r["payload"]["state"] == "ready")
    assert request("asr.input.prepare", recordingId="draft-busy", contextId="draft")["error"]["code"] == "ASR_BUSY"
    target = request("asr.input.capture_target", recordingId="test-input")["payload"]
    assert target["inputDeviceId"] == "device-preview"
    path = Path(target["path"])
    wav(path)
    request("asr.input.capture_ready", recordingId="test-input")
    request("asr.input.submit", recordingId="test-input")
    result = until(lambda: request("asr.input.poll", recordingId="test-input"),
                   lambda r: r["payload"]["state"] == "succeeded")["payload"]
    assert result["purpose"] == "test" and result["contextId"] == "settings-test"
    assert result["providerId"] == "test.asr.two" and result["text"] == "测试完整文字"
    assert request("asr.input.poll", recordingId="test-input")["payload"]["state"] == "consumed"
    settings = request("asr.settings.get")["payload"]
    assert settings["selectedProviderId"] == "test.asr.one" and settings["inputDeviceId"] == "device-saved"
    assert boundary._tasks["test-input"].language == "en"
    assert app.call_service("test.asr.one.service", "probe")["jobs"] == 0
    until(lambda: path.exists(), lambda exists: not exists)
    ready(request, "draft-after-test")
    assert boundary._tasks["draft-after-test"].input_device_id == "device-saved"
    request("asr.input.capture_discarded", recordingId="draft-after-test")


def test_hub_availability_does_not_depend_on_provider_readiness(runtime):
    app, _, request, _ = runtime
    assert request("asr.input.availability")["payload"] == {"enabled": True}
    request("asr.settings.save", selectedProviderId="not.installed")
    assert not request("asr.settings.get")["payload"]["available"]
    assert request("asr.input.availability")["payload"] == {"enabled": True}
    app.set_plugin_enabled("sakura.asr", False)
    assert request("asr.input.availability")["payload"] == {"enabled": False}
    result = request("asr.settings.save", inputDeviceId="mic-without-hub")
    assert result["ok"] and result["payload"]["inputDeviceId"] == "mic-without-hub"
    app.set_plugin_enabled("sakura.asr", True)
    assert request("asr.settings.get")["payload"]["selectedProviderId"] == "not.installed"


def test_draft_rejects_test_overrides_and_invalid_device_does_not_change_saved_setting(runtime):
    _, _, request, _ = runtime
    for payload in ({"providerId": "test.asr.two"}, {"inputDeviceId": "preview"}, {"purpose": "unknown"}):
        assert request("asr.input.prepare", recordingId="invalid", contextId="draft", **payload)["error"]["code"] == "ASR_CONTEXT_INVALID"
    assert request("asr.settings.save", inputDeviceId="saved")["ok"]
    for invalid in (None, 10, "invalid\x00id", "x" * 4097):
        assert request("asr.settings.save", inputDeviceId=invalid)["error"]["code"] == "ASR_INPUT_DEVICE_INVALID"
    assert request("asr.settings.get")["payload"]["inputDeviceId"] == "saved"


def test_input_logs_transitions_once_without_poll_noise_or_private_audio(runtime):
    from app.core_host.runtime_logging import CORE_BRIDGE_PREFIX, install_runtime_logging

    _, boundary, request, _ = runtime
    stream = io.BytesIO()
    bridge = install_runtime_logging(stream)
    try:
        path = ready(request)
        for _ in range(12):
            request("asr.input.availability")
            request("asr.input.poll", recordingId="record-1")
            request("asr.input.capture_status", recordingId="record-1")
        wav(path)
        request("asr.input.submit", recordingId="record-1")
        until(lambda: request("asr.input.poll", recordingId="record-1"),
              lambda r: r["payload"]["state"] == "succeeded")
        boundary._tasks["record-1"].worker.join(2)
        # A consumer's terminal cleanup must not claim a completed input was cancelled.
        request("asr.input.cancel", recordingId="record-1")
        ready(request, "cancelled-input")
        request("asr.input.cancel", recordingId="cancelled-input")
        request("asr.input.capture_discarded", recordingId="cancelled-input")
        boundary._tasks["cancelled-input"].worker.join(2)
    finally:
        bridge.close()
    records = [json.loads(line.removeprefix(CORE_BRIDGE_PREFIX)) for line in stream.getvalue().splitlines()
               if line.startswith(CORE_BRIDGE_PREFIX)]
    rows = [row for row in records if str(row.get("attributes", {}).get("event", "")).startswith("asr.input.")]
    completed = [row for row in rows if row["attributes"]["recording_id"] == "record-1"]
    assert [row["attributes"]["event"] for row in completed] == [
        "asr.input.preparing", "asr.input.ready", "asr.input.recognizing", "asr.input.succeeded"]
    cancelled = [row for row in rows if row["attributes"]["event"] == "asr.input.cancelled"]
    assert len(cancelled) == 1 and cancelled[0]["severity"] == "info"
    assert all(row["channel"] == "core" and not row.get("plugin_id") for row in rows)
    serialized = stream.getvalue().decode("utf-8")
    assert "测试完整文字" not in serialized and str(path) not in serialized


@pytest.mark.parametrize("change", ["character", "provider_restart", "generation"])
def test_context_or_scope_change_never_delivers_old_transcript(runtime, change):
    app, boundary, request, character = runtime
    path = ready(request)
    wav(path)
    request("asr.input.submit", recordingId="record-1")
    until(lambda: app.call_service("test.asr.one.service", "probe"), lambda p: p["reading"])
    if change == "character":
        character["characterId"] = "beta"
    elif change == "provider_restart":
        app.reload_plugin("test.asr.one")
    else:
        boundary.close()
    until(lambda: boundary._tasks["record-1"].state, lambda s: s in {"failed", "cancelled"})
    result = request("asr.input.poll", recordingId="record-1")
    assert "text" not in result.get("payload", {})
    until(lambda: app.audio_input.count, lambda n: n == 0)


def test_cancel_before_device_ready_and_invalid_wav_release_producer(runtime):
    app, boundary, request, _ = runtime
    path = ready(request)
    request("asr.input.cancel", recordingId="record-1")
    assert app.audio_input.count == 1  # Producer may still finish writing.
    wav(path)
    request("asr.input.capture_discarded", recordingId="record-1")
    assert not path.exists() and app.audio_input.count == 0
    path = ready(request, "record-2")
    wav(path, 44100)
    request("asr.input.submit", recordingId="record-2")
    result = until(lambda: request("asr.input.poll", recordingId="record-2"),
                   lambda r: r["payload"]["state"] == "failed")
    assert result["payload"]["errorCode"] == "ASR_AUDIO_INVALID"
    assert app.audio_input.count == 0


@pytest.mark.parametrize("cancel_first", [False, True])
def test_capture_failure_survives_cleanup_and_worker_exit_without_overriding_user_cancel(runtime, cancel_first):
    app, boundary, request, _ = runtime
    path = ready(request)
    wav(path)
    if cancel_first:
        request("asr.input.cancel", recordingId="record-1")
    result = request("asr.input.capture_discarded", recordingId="record-1",
                     errorCode="ASR_MICROPHONE_DISCONNECTED")["payload"]
    expected = "cancelled" if cancel_first else "failed"
    assert result["state"] == expected
    boundary._tasks["record-1"].worker.join(2)
    assert not boundary._tasks["record-1"].worker.is_alive()
    # A frontend cleanup or duplicate producer notification cannot erase the failure.
    request("asr.input.cancel", recordingId="record-1")
    request("asr.input.capture_discarded", recordingId="record-1")
    polled = request("asr.input.poll", recordingId="record-1")["payload"]
    assert polled["state"] == expected
    assert polled.get("errorCode") == (None if cancel_first else "ASR_MICROPHONE_DISCONNECTED")
    assert "text" not in polled
    assert app.audio_input.count == 0 and not path.exists()
    assert app.call_service("test.asr.one.service", "probe")["jobs"] == 0


def test_cancel_overtaking_prepare_blocks_late_capture_and_service_caller_cannot_be_spoofed(runtime):
    app, boundary, request, _ = runtime
    assert request("asr.input.cancel", recordingId="not-started")["payload"]["state"] == "cancelled"
    assert request("asr.input.prepare", recordingId="not-started", contextId="ctx")["error"]["code"] == "ASR_CANCELLED"
    assert app.audio_input.count == 0
    with pytest.raises(PluginRuntimeError):
        app.call_service("test.asr.two.service", "impersonate")
    status = app.call_service("sakura.asr", "status", "test.asr.one")
    assert status["label"] == "one" and status["processingLocation"] == "local"


def test_permission_wait_does_not_reduce_recording_budget(runtime, monkeypatch):
    app, boundary, request, _ = runtime
    clock = [100.0]
    monkeypatch.setattr(asr_boundary, "monotonic", lambda: clock[0])
    assert request("asr.input.prepare", recordingId="permission", contextId="ctx")["ok"]
    until(lambda: request("asr.input.poll", recordingId="permission"),
          lambda r: r["payload"]["state"] == "ready")
    assert request("asr.input.capture_target", recordingId="permission")["ok"]
    clock[0] += 80  # User is deciding in the system permission dialog.
    assert request("asr.input.capture_ready", recordingId="permission")["ok"]
    clock[0] += 40  # Only 40 seconds of the actual recording budget elapsed.
    time.sleep(0.15)
    assert request("asr.input.capture_status", recordingId="permission")["payload"]["state"] == "recording"
    request("asr.input.capture_discarded", recordingId="permission")
    assert app.audio_input.count == 0


def test_host_authorizes_only_selected_scope_and_validated_descriptor(tmp_path):
    identities = {"sakura.asr": {"providerId": "hub", "scopeId": "hub-1"},
                  "provider.service": {"providerId": "provider", "scopeId": "provider-1"}}
    resources = AudioInputResources(tmp_path, "generation", lambda key, **_: identities[key])
    target = resources.allocate("record", "provider", "provider.service", "provider-1")
    path = Path(target["path"])
    wav(path)
    audio = resources.commit(target["resourceId"])
    token = HOST_CALLER.set("hub")
    try:
        with pytest.raises(AudioInputError, match="UNAUTHORIZED"):
            resources.authorize({**audio, "durationMs": 42}, "provider.service")
        resources.authorize(audio, "provider.service")
    finally:
        HOST_CALLER.reset(token)
    token = HOST_CALLER.set("other-provider")
    try:
        with pytest.raises(AudioInputError, match="UNAUTHORIZED"):
            resources.acquire(audio["resourceId"])
    finally:
        HOST_CALLER.reset(token)
    token = HOST_CALLER.set("provider")
    try:
        lease = resources.acquire(audio["resourceId"])
        resources.revoke_resource(audio["resourceId"])
        assert path.exists()
        with pytest.raises(AudioInputError, match="UNAUTHORIZED"):
            resources.acquire(audio["resourceId"])
        resources.release(lease["leaseId"])
        assert not path.exists()
    finally:
        HOST_CALLER.reset(token)
        resources.close()
