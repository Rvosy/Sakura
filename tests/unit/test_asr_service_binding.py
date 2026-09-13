from __future__ import annotations

import shutil
import threading
import wave
from pathlib import Path

import pytest

from app.agent.tools import ToolRegistry
from app.core_host.asr_boundary import ASRBoundary
from app.core_host.plugin_runtime_application import PluginRuntimeApplication
from app.plugins.inventory import PluginInventory
from app.storage.runtime_roots import RuntimeRoots


PROVIDER = "fixture.asr"
SERVICE = "fixture.asr.service"


@pytest.fixture
def runtime(tmp_path: Path):
    distribution = tmp_path / "distribution"
    bundled = distribution / "plugins" / "builtin"
    source = Path(__file__).parents[2] / "plugins" / "builtin" / "sakura_asr_hub"
    shutil.copytree(source, bundled / "hub", ignore=shutil.ignore_patterns("__pycache__"))
    provider = bundled / "provider"
    provider.mkdir()
    (provider / "plugin.yaml").write_text(
        f"api: 4\nid: {PROVIDER}\nname: ASR binding fixture\nversion: 1.0.0\n"
        f"entry: plugin:Plugin\nprovides: [{SERVICE}]\nrequires: [sakura.asr, sakura.host.audio_input]\n",
        encoding="utf-8",
    )
    (provider / "plugin.py").write_text('''
class Plugin:
    def setup(self, context):
        self.context = context
        self.audio = context.get("sakura.host.audio_input")
        self.jobs = {}
        self.calls = []
        context.provide("fixture.asr.service", self,
            exports=("register", "status", "warmup", "begin", "poll", "cancel", "inspect"))
    def register(self):
        return self.context.get("sakura.asr").registerProvider({"providerId": "fixture.asr",
            "serviceKey": "fixture.asr.service", "label": "Fixture", "processingLocation": "local"})
    def status(self):
        return {"state": "ready", "available": True, "configVersion": "fixture-v1"}
    def warmup(self):
        return self.status()
    def begin(self, request):
        lease = self.audio.acquire(request["audio"]["resourceId"])
        try:
            job = "job_" + str(len(self.jobs) + 1)
            self.jobs[job] = {"state": "succeeded", "text": "original transcript", "language": "en"}
            return job
        finally:
            self.audio.release(lease["leaseId"])
    def poll(self, job):
        self.calls.append(["poll", job])
        return self.jobs.get(job, {"state": "failed", "errorCode": "ASR_JOB_NOT_FOUND"})
    def cancel(self, job):
        self.calls.append(["cancel", job])
        return job in self.jobs
    def inspect(self):
        return list(self.calls)
''', encoding="utf-8")
    roots = RuntimeRoots(distribution, tmp_path / "user")
    application = PluginRuntimeApplication(roots, "asr-binding-test", ToolRegistry(),
                                           PluginInventory(roots).scan().runtime_specs)
    try:
        application.start()
        application.call_service(SERVICE, "register")
        application.call_service("sakura.asr", "configure", {"selectedProviderId": PROVIDER})
        yield application, roots.user_root
    finally:
        application.close()


def _wav(path: Path) -> None:
    with wave.open(str(path), "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(16000)
        audio.writeframes(b"\x01\x00" * 160)


@pytest.mark.parametrize("method", ["poll", "cancel"])
def test_hub_never_sends_old_job_to_provider_reloaded_after_scope_check(
    runtime, monkeypatch: pytest.MonkeyPatch, method: str,
) -> None:
    application, _ = runtime
    identity = application.service_identity(SERVICE)
    resource = application.audio_input.allocate("recording", PROVIDER, SERVICE, identity["scopeId"])
    _wav(Path(resource["path"]))
    audio = application.audio_input.commit(resource["resourceId"])
    assert application.call_service("sakura.asr", "begin", {
        "requestId": "recording", "providerId": PROVIDER,
        "configVersion": "fixture-v1", "audio": audio,
    })["state"] == "running"
    entered = threading.Event()
    release = threading.Event()
    results = []
    errors = []
    manager = application._manager
    route = manager._route_service_call

    def gated_route(caller, key, name, args, **kwargs):
        if caller == "sakura.asr" and key == SERVICE and name == method:
            entered.set()
            assert release.wait(4)
        return route(caller, key, name, args, **kwargs)

    def invoke():
        try:
            results.append(application.call_service("sakura.asr", method, "recording"))
        except Exception as error:
            errors.append(error)

    monkeypatch.setattr(manager, "_route_service_call", gated_route)
    worker = threading.Thread(target=invoke)
    try:
        worker.start()
        assert entered.wait(2)
        application.reload_plugin(PROVIDER)
        assert application.service_identity(SERVICE) != identity
        release.set()
        worker.join(3)
        assert not worker.is_alive()
        assert errors == []
        assert application.call_service(SERVICE, "inspect") == []
        if method == "poll":
            assert results[0]["errorCode"] == "ASR_PROVIDER_UNAVAILABLE"
        else:
            assert results[0] == {"accepted": True}
        assert application.audio_input.count == 0
    finally:
        release.set()
        worker.join(3)


@pytest.mark.parametrize("reloaded", [PROVIDER, "sakura.asr"])
def test_success_returning_after_reload_never_becomes_a_draft(
    runtime, monkeypatch: pytest.MonkeyPatch, reloaded: str,
) -> None:
    application, user_root = runtime
    boundary = ASRBoundary("asr-binding-test", "credential", user_root=user_root,
        plugin_application_provider=lambda: application,
        character_presentation_provider=lambda: {"characterId": "fixture"})
    ready = threading.Event()
    entered = threading.Event()
    release = threading.Event()
    monkeypatch.setattr(boundary, "_log_state", lambda _task, state: ready.set() if state == "ready" else None)
    hub = application._manager._records["sakura.asr"].process
    original_call = hub.call_service

    def delayed_success(key, method, args, **kwargs):
        result = original_call(key, method, args, **kwargs)
        if method == "poll" and result.get("state") == "succeeded":
            entered.set()
            assert release.wait(4)
        return result

    def request(name, **payload):
        return boundary.handle({"generationId": "asr-binding-test", "generationCredential": "credential",
            "id": name, "name": name, "payload": payload})

    monkeypatch.setattr(hub, "call_service", delayed_success)
    try:
        assert request("asr.input.prepare", recordingId="recording", contextId="draft")["ok"]
        assert ready.wait(2)
        target = request("asr.input.capture_target", recordingId="recording")
        _wav(Path(target["payload"]["path"]))
        assert request("asr.input.capture_ready", recordingId="recording")["ok"]
        assert request("asr.input.submit", recordingId="recording")["ok"]
        assert entered.wait(2)
        application.reload_plugin(reloaded)
        release.set()
        task = boundary._tasks["recording"]
        task.worker.join(3)
        assert not task.worker.is_alive()
        result = request("asr.input.poll", recordingId="recording")
        assert result["payload"]["state"] == "failed"
        assert result["payload"]["errorCode"] == "ASR_PROVIDER_UNAVAILABLE"
        assert "text" not in result["payload"]
        assert task.text == ""
        assert application.audio_input.count == 0
    finally:
        release.set()
        boundary.close()


@pytest.mark.parametrize("reloaded", [PROVIDER, "sakura.asr"])
def test_ready_transcript_is_discarded_if_its_source_reloads_before_first_delivery(
    runtime, monkeypatch: pytest.MonkeyPatch, reloaded: str,
) -> None:
    application, user_root = runtime
    boundary = ASRBoundary("asr-binding-test", "credential", user_root=user_root,
        plugin_application_provider=lambda: application,
        character_presentation_provider=lambda: {"characterId": "fixture"})
    ready = threading.Event()
    monkeypatch.setattr(boundary, "_log_state", lambda _task, state: ready.set() if state == "ready" else None)

    def request(name, **payload):
        return boundary.handle({"generationId": "asr-binding-test", "generationCredential": "credential",
            "id": name, "name": name, "payload": payload})

    try:
        assert request("asr.input.prepare", recordingId="recording", contextId="draft")["ok"]
        assert ready.wait(2)
        target = request("asr.input.capture_target", recordingId="recording")
        _wav(Path(target["payload"]["path"]))
        assert request("asr.input.capture_ready", recordingId="recording")["ok"]
        assert request("asr.input.submit", recordingId="recording")["ok"]
        task = boundary._tasks["recording"]
        task.worker.join(3)
        assert not task.worker.is_alive()
        assert task.state == "succeeded"
        assert task.text == "original transcript"
        application.reload_plugin(reloaded)
        result = request("asr.input.poll", recordingId="recording")
        assert result["payload"]["state"] == "failed"
        assert result["payload"]["errorCode"] == "ASR_PROVIDER_UNAVAILABLE"
        assert "text" not in result["payload"]
        assert task.text == ""
        assert application.audio_input.count == 0
    finally:
        boundary.close()
