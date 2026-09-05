from __future__ import annotations

import concurrent.futures
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.core_host.plugin_artifacts import MAX_ARTIFACTS_PER_PLUGIN, PluginArtifactStore
from app.core_host.tts_boundary import _PluginSynthesisHandle
from app.plugins.sakura_plugin_sdk import PluginContext
from plugins.builtin.sakura_genie import plugin as genie
from plugins.builtin.sakura_gpt_sovits import plugin as gpt
from plugins.builtin.sakura_tts_hub.plugin import SakuraTTSHub


@pytest.fixture(params=[genie, gpt], ids=["genie", "gpt-sovits"])
def jobs(request: pytest.FixtureRequest, tmp_path: Path):
    module = request.param
    store = PluginArtifactStore(tmp_path, "generation-jobs")
    context = PluginContext(
        plugin_id=module.PROVIDER_ID,
        plugin_root=tmp_path / "plugin",
        data_dir=tmp_path / "private",
        remote_call=lambda _key, method, args: getattr(store, method)(*args),
        remote_request=lambda *_args: None,
    )
    artifacts = context.get("sakura.host.artifacts")
    provider_type = genie.GenieProvider if module is genie else gpt.GPTSoVITSProvider
    provider = provider_type.__new__(provider_type)
    provider._jobs = {}
    provider._jobs_lock = threading.RLock()

    def begin(values):
        job_id = f"job_{values['requestId']}"
        provider._jobs[job_id] = module._Job(context, artifacts, values, None)
        return job_id

    # Exercise the actual Job/Provider/Hub ownership without an external engine.
    provider.begin = begin
    provider.status = lambda: {"available": True}
    hub = SakuraTTSHub(
        SimpleNamespace(get=lambda _key: provider),
        SimpleNamespace(get=lambda _character: {"enabled": True, "provider": module.PROVIDER_ID}),
    )
    hub.registerProvider({
        "providerId": module.PROVIDER_ID,
        "serviceKey": module.SERVICE_KEY,
        "label": "Fixture",
    })
    application = SimpleNamespace(
        call_service=lambda _key, method, *args: getattr(hub, method)(*args)
    )

    def start(request_id="request"):
        assert hub.begin({
            "requestId": request_id,
            "characterId": "fixture",
            "text": "fixture",
            "options": {},
        })["state"] == "running"
        return provider._jobs[f"job_{request_id}"]

    try:
        yield SimpleNamespace(
            context=context, store=store, provider=provider, hub=hub,
            application=application, start=start, provider_id=module.PROVIDER_ID,
        )
    finally:
        for job in provider._jobs.values():
            if not job._done.is_set():
                job.fail("TTS_SYNTHESIS_CANCELLED")
        context.close()
        store.clear()


def test_repeated_waiter_timeout_releases_queued_artifacts_before_terminal_poll(jobs) -> None:
    count = MAX_ARTIFACTS_PER_PLUGIN + 4
    for index in range(count):
        request_id = f"request_{index}"
        jobs.start(request_id)
        handle = _PluginSynthesisHandle(jobs.application, request_id, jobs.provider_id)
        with pytest.raises(concurrent.futures.TimeoutError):
            handle.result(0)
        assert handle.cancel()
        assert jobs.store.count == 0
        assert jobs.context._effects == []

    # Only the late-readable terminal records remain; they no longer consume slots.
    assert len(jobs.provider._jobs) == len(jobs.hub._jobs) == count
    for index in range(count):
        assert jobs.hub.poll(f"request_{index}")["state"] == "cancelled"
    assert jobs.provider._jobs == jobs.hub._jobs == {}


@pytest.mark.parametrize("cancel", [False, True], ids=["failed", "cancelled"])
def test_active_job_releases_artifact_only_after_writer_finishes(jobs, cancel: bool) -> None:
    job = jobs.start()
    assert job.mark_started()
    job.output_path.write_bytes(b"partial audio")
    if cancel:
        assert jobs.hub.cancel("request")["accepted"]
        assert jobs.store.count == 1
        assert job.output_path.read_bytes() == b"partial audio"
        assert jobs.hub.poll("request")["state"] == "running"

    job.fail("TTS_RUNTIME_UNAVAILABLE")

    assert jobs.store.count == 0
    assert not job.output_path.exists()
    assert jobs.context._effects == []
    terminal = jobs.hub.poll("request")
    assert terminal["state"] == ("cancelled" if cancel else "failed")
    if not cancel:
        assert terminal["errorCode"] == "TTS_RUNTIME_UNAVAILABLE"


def test_cancel_racing_with_successful_writer_releases_unpublished_audio(jobs) -> None:
    job = jobs.start()
    assert job.mark_started()
    assert jobs.hub.cancel("request")["accepted"]
    job.output_path.write_bytes(b"late audio")

    job.succeed()

    assert jobs.store.count == 0
    assert not job.output_path.exists()
    assert jobs.context._effects == []
    assert jobs.hub.poll("request")["state"] == "cancelled"


def test_rejected_late_cancel_preserves_success_and_transferred_artifact(
    jobs, monkeypatch: pytest.MonkeyPatch
) -> None:
    job = jobs.start()
    assert job.mark_started()
    job.output_path.write_bytes(b"complete audio")
    job.succeed()

    assert not jobs.hub.cancel("request")["accepted"]
    assert jobs.store.count == 1
    committed = threading.Event()
    release_commit = threading.Event()
    original_commit = jobs.store.commit

    def commit(*args):
        value = original_commit(*args)
        committed.set()
        assert release_commit.wait(2)
        return value

    monkeypatch.setattr(jobs.store, "commit", commit)
    terminal = {}
    poller = threading.Thread(target=lambda: terminal.update(jobs.hub.poll("request")))
    poller.start()
    try:
        assert committed.wait(2)
        assert not jobs.hub.cancel("request")["accepted"]
        assert job.output_path.read_bytes() == b"complete audio"
    finally:
        release_commit.set()
        poller.join(2)
    assert not poller.is_alive()
    assert terminal["state"] == "succeeded"
    artifact_id = terminal["artifact"]["artifactId"]
    assert jobs.store.resolve_committed_by_id(artifact_id).path.read_bytes() == b"complete audio"
    assert jobs.context._effects == []
    job.close()
    assert jobs.store.count == 1
    assert jobs.store.release(jobs.provider_id, artifact_id)
