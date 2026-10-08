from __future__ import annotations

import json
import threading
import wave
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.core_host.plugin_artifacts import PluginArtifactStore
from app.core_host.plugin_host_services import _ArtifactsHostService, HostServiceError
from app.core_host.speech_host import SpeechHostError, SpeechHostService
from app.core_host.tts_boundary import TTSBoundary, TTSBoundaryError
from app.plugins.host_services import HOST_CALLER, HOST_CALLER_SCOPE
from app.storage.paths import StoragePaths
from app.storage.timeline import NewTimelineEntry, TimelineKind, TimelineStore


@contextmanager
def caller(plugin="remote", scope="original"):
    owner = HOST_CALLER.set(plugin)
    instance = HOST_CALLER_SCOPE.set(scope)
    try:
        yield
    finally:
        HOST_CALLER_SCOPE.reset(instance)
        HOST_CALLER.reset(owner)


@pytest.fixture
def system(tmp_path):
    store = PluginArtifactStore(tmp_path, "speech-generation")
    accepted = {("remote", "original"), ("other", "other-scope")}
    def commit(plugin, scope, action):
        if (plugin, scope) not in accepted:
            raise SpeechHostError("PLUGIN_CALLER_INVALID")
        return action()
    artifacts = _ArtifactsHostService(store, commit)
    timeline = TimelineStore(StoragePaths(tmp_path).timeline_database())
    timeline.initialize()
    session = SimpleNamespace(character=SimpleNamespace(id="sakura"))
    generated, facts, desktop = [], [], []
    jobs = {}
    def service(key, method, *args, timeout=None):
        assert key == "sakura.tts"
        if method == "begin":
            request = args[0]
            generated.append(request)
            allocation = store.allocate("voice-provider", {"mediaType": "audio/wav", "suffix": ".wav"})
            with wave.open(allocation["path"], "wb") as wav:
                wav.setnchannels(1)
                wav.setsampwidth(2)
                wav.setframerate(16000)
                wav.writeframes(b"\x01\x00" * 160)
            jobs[request["requestId"]] = store.commit("voice-provider", allocation["artifactId"])
            return {"state": "running", "requestId": request["requestId"], "providerId": "voice-provider"}
        if method == "poll":
            return {"state": "succeeded", "requestId": args[0], "providerId": "voice-provider", "artifact": jobs[args[0]]}
        if method == "cancel":
            return {"accepted": args[0] in jobs}
        raise AssertionError(method)
    application = SimpleNamespace(
        call_service=service, resolve_committed_artifact=store.resolve_committed_by_id,
        release_committed_artifact=artifacts.release_committed,
        emit_event=lambda *args: facts.append(args),
    )
    boundary = TTSBoundary("speech-generation", "credential", tmp_path,
        session_provider=lambda: session, plugin_application_provider=lambda: application,
        event_publisher=desktop.append)
    host = SpeechHostService(boundary_provider=lambda: boundary, export_audio=artifacts.export_speech,
        release_artifact=artifacts.release_speech, revoke_exports=artifacts.revoke_speech,
        commit_scope=lambda owner, action: commit(*owner, action))
    def entry(entry_id="reply-one", character="sakura", suppressed=False):
        timeline.append(NewTimelineEntry(entry_id, "turn-" + entry_id, character,
            TimelineKind.ASSISTANT, "chat", "2026-09-28T00:00:00Z",
            {"segments": [{"text": "原文 " + entry_id, "translation": "译文", "tone": "happy",
                            "portrait": "smile", "suppressTts": suppressed}]}))
    entry()
    yield SimpleNamespace(host=host, boundary=boundary, artifacts=artifacts, store=store,
        accepted=accepted, session=session, generated=generated, facts=facts, desktop=desktop, entry=entry)
    host.close()
    boundary.close()
    artifacts.clear()


def completed(system, entry_id="reply-one"):
    job_id = system.host.begin("sakura", entry_id, 0)["jobId"]
    assert system.host._jobs[job_id].done.wait(3)
    return system.host.poll(job_id)["result"]


def test_speech_reuses_saved_audio_without_desktop_playback_and_releases_more_than_16_exports(system):
    with caller():
        first_recording = None
        for index in range(20):
            entry_id = f"reply-{index}"
            system.entry(entry_id)
            result = completed(system, entry_id)
            descriptor = result["artifact"]
            resolved = system.artifacts.call("resolve", [descriptor["artifactId"]])
            assert Path(resolved["path"]).read_bytes().startswith(b"RIFF")
            assert result["historyEntryId"] == entry_id
            assert result["segmentIndex"] == 0
            system.artifacts.call("release_received", [descriptor["artifactId"]])
            assert system.store.count == 0
            if index == 0:
                first_recording = result["recordingId"]
        reused = completed(system, "reply-0")
        assert reused["recordingId"] == first_recording
        assert len(system.generated) == 20
        assert system.generated[0]["text"] == "原文 reply-0"
        system.artifacts.call("release_received", [reused["artifact"]["artifactId"]])
    assert system.store.count == 0
    assert system.artifacts._speech_exports == set()
    assert system.boundary._authorizations == {}
    assert system.facts == system.desktop == []


def test_speech_uses_current_cache_settings_and_reuses_the_selected_directory(system, tmp_path):
    cache = tmp_path / "selected-cache"
    config = tmp_path / "config/voice_cache.json"
    config.parent.mkdir(exist_ok=True)
    config.write_text(json.dumps({"schemaVersion": 1, "directory": str(cache), "maxBytes": 64 * 1024 * 1024, "idleFill": False}), encoding="utf-8")
    with caller():
        result = completed(system)
        recording = system.boundary._recordings.get(result["recordingId"])
        assert recording.directory.parent == cache.resolve() / "sakura"
        system.artifacts.call("release_received", [result["artifact"]["artifactId"]])
        reused = completed(system)
        assert reused["recordingId"] == result["recordingId"]
        assert len(system.generated) == 1
        system.artifacts.call("release_received", [reused["artifact"]["artifactId"]])
    assert system.store.count == 0
    assert system.facts == system.desktop == []


@pytest.mark.parametrize("preemption", ["foreground", "plugin_close"])
def test_background_speech_is_cancelled_before_recording_commit(system, tmp_path, monkeypatch, preemption):
    config = tmp_path / "config/voice_cache.json"
    config.parent.mkdir(exist_ok=True)
    config.write_text(json.dumps({"schemaVersion": 1, "directory": "", "maxBytes": 64 * 1024 * 1024, "idleFill": True}), encoding="utf-8")
    from plugins.builtin.sakura_tts_hub import _idle_fill
    monkeypatch.setattr(_idle_fill, "device_below_peak", lambda: True)
    application = system.boundary._plugin_application()
    original = application.call_service
    polling, cancelled, release = threading.Event(), threading.Event(), threading.Event()
    idle_request = None

    def service(key, method, *args, timeout=None):
        nonlocal idle_request
        if method == "begin" and idle_request is None:
            idle_request = args[0]["requestId"]
        if method == "poll" and args[0] == idle_request:
            polling.set()
            assert release.wait(5)
        if method == "cancel" and args[0] == idle_request:
            cancelled.set()
        return original(key, method, *args, timeout=timeout)

    monkeypatch.setattr(application, "call_service", service)
    results = []
    worker = _idle_fill.IdleFill(system.host, SimpleNamespace(warning=lambda *_args, **_kwargs: None))
    def fill():
        with caller():
            results.append(worker.fill_once())
    idle = threading.Thread(target=fill)
    idle.start()
    try:
        assert polling.wait(5)
        background_job = next(iter(system.host._jobs.values()))
        with caller():
            if preemption == "foreground":
                system.entry("foreground")
                result = completed(system, "foreground")
                assert system.boundary._recordings.get(result["recordingId"]) is not None
                system.artifacts.call("release_received", [result["artifact"]["artifactId"]])
            else:
                worker.close()
            assert cancelled.is_set()
    finally:
        release.set()
        idle.join(5)
    assert not idle.is_alive()
    assert background_job.done.wait(5)
    assert results == [False]
    assert system.boundary._recordings.for_segment("sakura", "reply-one", 0) is None
    assert system.store.count == 0
    assert system.facts == system.desktop == []


def test_hub_idle_fill_uses_host_cache_without_export_or_playback(system, tmp_path, monkeypatch):
    from plugins.builtin.sakura_tts_hub import _idle_fill

    custom = tmp_path / "idle-cache"
    config = tmp_path / "config/voice_cache.json"
    config.parent.mkdir(exist_ok=True)
    config.write_text(json.dumps({"schemaVersion": 1, "directory": str(custom),
                                 "maxBytes": 64 * 1024 * 1024, "idleFill": True}))
    peak = [False]
    monkeypatch.setattr(_idle_fill, "device_below_peak", lambda: not peak[0])
    worker = _idle_fill.IdleFill(system.host, SimpleNamespace(warning=lambda *_args, **_kwargs: None))
    with caller():
        assert worker.fill_once() is True
        cached = system.boundary._recordings.for_segment("sakura", "reply-one", 0)
        assert cached is not None and cached.directory.parent == custom / "sakura"
        assert system.generated[0]["options"]["background"] is True
        assert worker.fill_once() is False
        system.entry("later")
        peak[0] = True
        assert worker.fill_once() is False
        peak[0] = False
        assert worker.fill_once() is True
        assert len(system.generated) == 2
        assert not system.host.cache_status()["busy"]
    assert system.store.count == 0
    assert system.boundary._authorizations == {}
    assert system.facts == system.desktop == []


def test_hub_idle_deferral_backs_off_and_resumes_without_failure_event(system, tmp_path, monkeypatch):
    from plugins.builtin.sakura_tts_hub import _idle_fill

    config = tmp_path / "config/voice_cache.json"
    config.parent.mkdir(exist_ok=True)
    config.write_text(json.dumps({"schemaVersion": 1, "idleFill": True}))
    now = [100.0]
    monkeypatch.setattr(_idle_fill, "monotonic", lambda: now[0])
    monkeypatch.setattr(_idle_fill, "device_below_peak", lambda: True)
    application = system.boundary._plugin_application()
    original = application.call_service
    def deferred(key, method, *args, timeout=None):
        if method == "poll":
            return {"state": "failed", "requestId": args[0], "providerId": "voice-provider",
                    "errorCode": "TTS_BACKGROUND_DEFERRED"}
        return original(key, method, *args, timeout=timeout)
    monkeypatch.setattr(application, "call_service", deferred)
    warnings = []
    worker = _idle_fill.IdleFill(system.host, SimpleNamespace(warning=lambda *args, **kwargs: warnings.append((args, kwargs))))
    with caller():
        assert worker.fill_once() is False
        assert worker.fill_once() is False
        assert len(system.generated) == 1
        now[0] += _idle_fill.IDLE_FILL_BACKOFF_SECONDS
        monkeypatch.setattr(application, "call_service", original)
        assert worker.fill_once() is True
        assert len(system.generated) == 2
    assert warnings == []
    assert system.facts == system.desktop == []


def test_background_permission_is_checked_at_begin_and_cache_queries_are_character_scoped(system):
    with caller():
        assert system.host.cache_status()["idleFill"] is False
        with pytest.raises(TTSBoundaryError, match="后台语音暂不可用"):
            system.host.begin("sakura", "reply-one", 0, {"background": True, "exportAudio": False})
        with pytest.raises(TTSBoundaryError, match="角色已切换"):
            system.host.cache_page("other", None, 40)
        page = system.host.cache_page("sakura", None, 40)
        assert page["entries"][0]["segments"] == [{"hasText": True, "suppressed": False, "recorded": False}]
    assert system.generated == []


def test_idle_policy_runs_through_plugin_process_and_preserves_remote_failure_diagnostics(system, tmp_path, monkeypatch):
    import io
    import shutil
    from app.core_host.plugin_runtime_application import PluginRuntimeApplication
    from app.plugin_sdk.sakura_tools import ToolRegistry
    from app.plugins.inventory import PluginInventory
    from app.storage.runtime_roots import RuntimeRoots
    from app.core_host.runtime_logging import CORE_BRIDGE_PREFIX, install_runtime_logging

    repository = Path(__file__).parents[2]
    distribution = tmp_path / "distribution"
    plugin = distribution / "plugins/builtin/fixture_idle"
    plugin.mkdir(parents=True)
    shutil.copyfile(repository / "VERSION", distribution / "VERSION")
    for name in ("_idle_fill.py", "_device_load.py"):
        shutil.copyfile(repository / "plugins/builtin/sakura_tts_hub" / name, plugin / name)
    (plugin / "plugin.yaml").write_text(
        "api: 4\nid: fixture.idle\nname: Idle\nversion: 1.0.0\nentry: plugin:Plugin\n"
        "requires: [sakura.host.speech, sakura.host.logging]\nprovides: [fixture.idle]\n")
    (plugin / "plugin.py").write_text('''
import _idle_fill
class Plugin:
    def setup(self, context):
        _idle_fill.device_below_peak = lambda: True
        self.worker = _idle_fill.IdleFill(context.get("sakura.host.speech"), context.get("sakura.host.logging"))
        context.effect(self.worker.close)
        context.provide("fixture.idle", self, exports=("run",))
    def run(self):
        return self.worker.fill_once()
''')
    config = tmp_path / "config/voice_cache.json"
    config.parent.mkdir(exist_ok=True)
    config.write_text(json.dumps({"schemaVersion": 1, "idleFill": True}))
    roots = RuntimeRoots(distribution, tmp_path)
    application = PluginRuntimeApplication(roots, "idle-protocol", ToolRegistry(), PluginInventory(roots).scan().runtime_specs)
    application.bind_tts_boundary(system.boundary)
    stream = io.BytesIO()
    bridge = install_runtime_logging(stream)
    try:
        application.start()
        assert application.wait_until_loaded(timeout=5)
        assert application.call_service("fixture.idle", "run") is True
        assert application.call_service("fixture.idle", "run") is False
        assert system.boundary._recordings.for_segment("sakura", "reply-one", 0) is not None
        assert system.generated[0]["options"]["background"] is True
        assert system.facts == system.desktop == []
        def unavailable(_character):
            raise PermissionError(13, "fixture cache denied api_key=private-key", str(tmp_path / "history.sqlite"))
        monkeypatch.setattr(system.boundary._timeline, "latest_cursor", unavailable)
        assert application.call_service("fixture.idle", "run") is False
    finally:
        application.close()
        bridge.close()
    records = [json.loads(line.removeprefix(CORE_BRIDGE_PREFIX))
               for line in stream.getvalue().splitlines() if line.startswith(CORE_BRIDGE_PREFIX)]
    failure = next(record for record in records if record.get("message") == "后台语音补齐失败")
    assert "fixture cache denied" in failure["attributes"]["diagnostic"]
    assert "PermissionError" in failure["attributes"]["exception_chain"]
    assert "unavailable" in failure["attributes"]["exception_stack"]
    assert failure["attributes"]["errno"] == 13
    assert "private-key" not in repr(failure)


@pytest.mark.parametrize("plugin,scope", [("other", "other-scope"), ("remote", "replacement")])
def test_jobs_and_audio_are_bound_to_the_authenticated_process(system, plugin, scope):
    with caller():
        job_id = system.host.begin("sakura", "reply-one", 0)["jobId"]
        assert system.host._jobs[job_id].done.wait(3)
    with caller(plugin, scope):
        for method in (system.host.poll, system.host.cancel):
            with pytest.raises(SpeechHostError, match="SPEECH_JOB_NOT_FOUND"):
                method(job_id)
    with caller():
        artifact_id = system.host.poll(job_id)["result"]["artifact"]["artifactId"]
    with caller(plugin, scope):
        for method in ("resolve", "release_received"):
            with pytest.raises(HostServiceError, match="ARTIFACT_NOT_FOUND"):
                system.artifacts.call(method, [artifact_id])
    with caller():
        system.artifacts.call("release_received", [artifact_id])


@pytest.mark.parametrize("revoke", ["cancel", "scope", "character", "generation"])
def test_revocation_before_worker_runs_prevents_synthesis(system, monkeypatch, revoke):
    workers = []
    monkeypatch.setattr("app.core_host.speech_host.threading.Thread.start", lambda worker: workers.append(worker))
    with caller():
        job_id = system.host.begin("sakura", "reply-one", 0)["jobId"]
        if revoke == "cancel":
            assert system.host.cancel(job_id) == {"accepted": True}
        elif revoke == "scope":
            system.accepted.remove(("remote", "original"))
            system.host.revoke_scope("remote")
        elif revoke == "character":
            system.host.invalidate_session()
            system.session.character.id = "other"
            system.boundary.reset_character()
        else:
            system.host.close()
        workers[0].run()
        with pytest.raises(SpeechHostError, match="TTS_SYNTHESIS_CANCELLED" if revoke == "cancel" else "SPEECH_JOB_NOT_FOUND"):
            system.host.poll(job_id)
    assert system.generated == []
    assert system.store.count == 0


@pytest.mark.parametrize("revoke", ["scope", "character", "generation"])
def test_revocation_releases_export_without_removing_retained_recording(system, revoke):
    with caller():
        result = completed(system)
        artifact_id = result["artifact"]["artifactId"]
        issued_path = Path(system.artifacts.call("resolve", [artifact_id])["path"])
        if revoke == "scope":
            system.artifacts.revoke_scope("remote")
            system.host.revoke_scope("remote")
        elif revoke == "character":
            system.host.invalidate_session()
        else:
            system.host.close()
        with pytest.raises(HostServiceError, match="ARTIFACT_NOT_FOUND"):
            system.artifacts.call("resolve", [result["artifact"]["artifactId"]])
        if revoke != "scope":
            assert issued_path.read_bytes().startswith(b"RIFF")
            system.artifacts.call("release_received", [artifact_id])
    assert system.boundary._recordings.get(result["recordingId"]) is not None
    assert system.store.count == 0


def test_cancellation_after_audio_is_prepared_cannot_export_a_late_result(system, monkeypatch):
    prepared, release = threading.Event(), threading.Event()
    original = system.boundary.prepare_speech
    def delayed(*args):
        recording = original(*args)
        prepared.set()
        assert release.wait(3)
        return recording
    monkeypatch.setattr(system.boundary, "prepare_speech", delayed)
    with caller():
        job_id = system.host.begin("sakura", "reply-one", 0)["jobId"]
        job = system.host._jobs[job_id]
        try:
            assert prepared.wait(3)
            assert system.host.cancel(job_id)["accepted"]
        finally:
            release.set()
        assert job.done.wait(3)
        with pytest.raises(SpeechHostError, match="TTS_SYNTHESIS_CANCELLED"):
            system.host.poll(job_id)
    assert system.store.count == 0
    assert system.artifacts._speech_exports == set()
    assert system.facts == []


def test_character_switch_closes_speech_admission_until_the_new_character_is_published(system):
    with caller():
        result = completed(system)
        with system.host.suspend_for_character_change():
            with pytest.raises(SpeechHostError, match="SPEECH_UNAVAILABLE"):
                system.host.begin("sakura", "reply-one", 0)
            with pytest.raises(HostServiceError, match="ARTIFACT_NOT_FOUND"):
                system.artifacts.call("resolve", [result["artifact"]["artifactId"]])
            system.session.character.id = "other"
            system.boundary.reset_character()
        system.entry("other-entry", character="other")
        with pytest.raises(TTSBoundaryError) as caught:
            system.host.begin("sakura", "reply-one", 0)
        assert caught.value.code == "SPEECH_CHARACTER_NOT_CURRENT"
        job_id = system.host.begin("other", "other-entry", 0)["jobId"]
        assert system.host._jobs[job_id].done.wait(3)
        assert system.host.poll(job_id)["result"]["characterId"] == "other"


def test_real_runtime_scope_stop_can_interrupt_export_copy_without_lock_inversion(system, tmp_path, monkeypatch):
    from app.plugins.runtime_v4 import PluginRuntimeManager, _RuntimeRecord
    from app.core_host import plugin_host_services

    copying, release_copy, process_stopped = threading.Event(), threading.Event(), threading.Event()
    manager = PluginRuntimeManager(tmp_path, "speech-generation", [])
    process = SimpleNamespace(scope_id="original", close=lambda **_kwargs: process_stopped.set())
    manager._records["remote"] = _RuntimeRecord(process=process, pid=1, state="active", reason_code="READY",
        spec=SimpleNamespace(plugin_id="remote", name="Remote", provides=(), requires=()))
    manager.install_host_service("sakura.host.artifacts", system.artifacts, exports=())
    manager.install_host_service("sakura.host.speech", system.host, exports=("begin", "poll", "cancel"))
    system.artifacts._commit_scope = manager.commit_plugin_scope
    system.host._commit_scope = lambda owner, action: manager.commit_plugin_scope(*owner, action)
    original = plugin_host_services.shutil.copyfile
    def delayed_copy(*args, **kwargs):
        copying.set()
        assert release_copy.wait(3)
        return original(*args, **kwargs)
    monkeypatch.setattr(plugin_host_services.shutil, "copyfile", delayed_copy)
    errors = []
    def stop():
        try:
            manager._stop_process("remote", reason="PLUGIN_DISABLED", failed=False)
        except BaseException as error:
            errors.append(error)
    with caller():
        job_id = system.host.begin("sakura", "reply-one", 0)["jobId"]
        job = system.host._jobs[job_id]
        stopper = threading.Thread(target=stop)
        try:
            assert copying.wait(3)
            stopper.start()
            # Stopping must acquire the runtime lock while the file copy waits.
            assert process_stopped.wait(3)
        finally:
            release_copy.set()
        stopper.join(3)
        assert not stopper.is_alive()
        assert job.done.wait(3)
        assert errors == []
        with pytest.raises(SpeechHostError, match="SPEECH_JOB_NOT_FOUND"):
            system.host.poll(job_id)
    assert system.store.count == 0
    assert system.artifacts._speech_exports == set()


@pytest.mark.parametrize("entry_id,index,character,code", [
    ("suppressed", 0, "sakura", "TTS_SEGMENT_NOT_AUTHORIZED"),
    ("foreign", 0, "sakura", "TTS_SEGMENT_NOT_AUTHORIZED"),
    ("missing", 0, "sakura", "TTS_SEGMENT_NOT_AUTHORIZED"),
    ("reply-one", True, "sakura", "TTS_SEGMENT_NOT_AUTHORIZED"),
    ("reply-one", 1, "sakura", "TTS_SEGMENT_NOT_AUTHORIZED"),
    ("reply-one", 0, "other", "SPEECH_CHARACTER_NOT_CURRENT"),
])
def test_speech_only_accepts_current_character_playable_history(system, entry_id, index, character, code):
    system.entry("suppressed", suppressed=True)
    system.entry("foreign", character="other")
    with caller(), pytest.raises(TTSBoundaryError, match=".*") as caught:
        system.host.begin(character, entry_id, index)
    assert caught.value.code == code
    assert system.generated == []


def test_playback_events_identify_the_actual_recording_and_unknown_recordings_stay_unidentified(system):
    with caller():
        result = completed(system)
    assert system.facts == []
    for recording_id in (result["recordingId"], "missing-recording"):
        for state in ("started", "finished"):
            system.boundary._handle_playback_observe({"payload": {
                "playbackId": "play-" + recording_id, "recordingId": recording_id, "state": state}})
    for _, payload in system.facts[:2]:
        assert (payload["characterId"], payload["historyEntryId"], payload["segmentIndex"]) == ("sakura", "reply-one", 0)
    for _, payload in system.facts[2:]:
        assert not {"characterId", "historyEntryId", "segmentIndex"} & payload.keys()
