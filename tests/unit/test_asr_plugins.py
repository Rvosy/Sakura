from __future__ import annotations

import io
import json
import shutil
import sys
import threading
import time
import wave
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.agent.tools import ToolRegistry
from app.core_host.plugin_runtime_application import PluginRuntimeApplication
from app.plugins.inventory import PluginInventory
from app.storage.runtime_roots import RuntimeRoots
from plugins.builtin.sakura_asr_hub.plugin import SakuraASRHub
from plugins.builtin.sakura_asr_sensevoice import _resources
from plugins.builtin.sakura_asr_sensevoice.plugin import SenseVoiceProvider


def wav(path):
    with wave.open(str(path), "wb") as audio:
        audio.setparams((1, 2, 16000, 320, "NONE", "not compressed"))
        audio.writeframes(b"\x00\x00" * 320)


def wait_result(provider, job_id):
    end = time.monotonic() + 5
    while time.monotonic() < end:
        value = provider.poll(job_id)
        if value["state"] != "running":
            return value
        time.sleep(.01)
    pytest.fail("ASR job did not finish")


class CaptureLogger:
    def __init__(self):
        self.records = []

    def __getattr__(self, level):
        return lambda message, *, fields: self.records.append({"level": level, "message": message, "fields": fields})


@pytest.mark.parametrize("outcome", ["succeeded", "failed", "cancelled"])
def test_provider_logs_one_terminal_without_audio_text_or_poll_noise(tmp_path, outcome):
    logger = CaptureLogger()
    path = tmp_path / "private-audio.wav"
    wav(path)
    audio = SimpleNamespace(acquire=lambda _: {"leaseId": "lease", "path": str(path)}, release=lambda _: True)
    resources = SimpleNamespace(ready=lambda: True)
    provider = SenseVoiceProvider(SimpleNamespace(get=lambda key: logger if key == "sakura.host.logging" else audio), resources)
    provider.state = "ready"
    entered, resume = threading.Event(), threading.Event()

    def recognize(*_args):
        entered.set()
        assert resume.wait(2)
        if outcome == "failed":
            raise RuntimeError("private transcript C:/private/model.onnx")
        return "private transcript", "zh"

    provider._recognize = recognize
    job_id = provider.begin({"requestId": "logging-request", "audio": {"resourceId": "audio"}, "configVersion": provider.config_version, "language": "auto"})
    try:
        assert entered.wait(2)
        for _ in range(30):
            provider.status()
            assert provider.poll(job_id)["state"] == "running"
        assert len(logger.records) == 1
        if outcome == "cancelled":
            assert provider.cancel(job_id)
    finally:
        resume.set()
    assert wait_result(provider, job_id)["state"] == outcome
    for _ in range(30):
        provider.poll(job_id)
    assert [item["fields"]["event"] for item in logger.records] == ["asr.recognition.started", "asr.recognition." + outcome]
    terminal = logger.records[-1]["fields"]
    assert terminal["request_id"] == "logging-request"
    assert terminal["provider_id"] == "sakura.asr.sensevoice"
    assert isinstance(terminal["duration_ms"], int)
    if outcome == "failed":
        assert terminal["error_code"] == "ASR_RECOGNITION_FAILED"
    else:
        assert "error_code" not in terminal
    serialized = json.dumps(logger.records)
    assert "private transcript" not in serialized
    assert "private-audio" not in serialized
    assert "C:/private" not in serialized
    provider.close()
    provider.close()
    assert len(logger.records) == 3


def test_broken_optional_logger_cannot_prevent_recognition_or_model_install(tmp_path, monkeypatch):
    def unavailable(*_args, **_kwargs):
        raise OSError("logger unavailable")

    logger = SimpleNamespace(info=unavailable, error=unavailable)
    audio = SimpleNamespace(acquire=lambda _: {"leaseId": "lease", "path": str(tmp_path / "audio.wav")}, release=lambda _: True)
    provider = SenseVoiceProvider(SimpleNamespace(get=lambda key: logger if key == "sakura.host.logging" else audio), SimpleNamespace(ready=lambda: True))
    provider.state = "ready"
    provider._recognize = lambda *_: ("recognition survives", "en")
    job = provider.begin({"requestId": "best-effort", "audio": {"resourceId": "audio"}, "configVersion": provider.config_version})
    assert wait_result(provider, job)["state"] == "succeeded"
    provider.close()
    resources = _resources.ModelResources(tmp_path / "models", logger=logger)
    content = b"test model"
    monkeypatch.setattr(_resources, "FILES", (("model", "https://example.invalid/model", len(content)),))
    monkeypatch.setattr(_resources.urllib.request, "urlopen", lambda *_a, **_k: io.BytesIO(content))
    resources.start()
    resources.thread.join(2)
    assert resources.state == "succeeded"


def test_model_install_failure_logs_stable_code_without_source_url_or_path(tmp_path, monkeypatch):
    logger = CaptureLogger()
    resources = _resources.ModelResources(tmp_path, logger=logger)
    monkeypatch.setattr(_resources, "FILES", (("model", "https://private.invalid/model", 4),))

    def failed_download(*_args, **_kwargs):
        raise OSError("credential secret C:/private/model")

    monkeypatch.setattr(_resources.urllib.request, "urlopen", failed_download)
    resources.start()
    resources.thread.join(2)
    assert [item["fields"]["event"] for item in logger.records] == ["asr.model.install.started", "asr.model.install.failed"]
    assert logger.records[-1]["fields"]["error_code"] == "ASR_DOWNLOAD_FAILED"
    assert "private" not in json.dumps(logger.records)
    assert "secret" not in json.dumps(logger.records)


def test_provider_cancel_retains_reader_until_native_work_finishes(tmp_path):
    path = tmp_path / "audio.wav"
    wav(path)
    acquired, finish = threading.Event(), threading.Event()
    releases = []
    audio = SimpleNamespace(acquire=lambda _: (acquired.set() or {"leaseId": "lease", "path": str(path)}), release=lambda lease: releases.append(lease))
    provider = SenseVoiceProvider(SimpleNamespace(get=lambda _: audio), SimpleNamespace(ready=lambda: True))
    provider.state = "ready"
    provider._recognize = lambda *_: (finish.wait(5) and "late text", "zh")
    job = provider.begin({"audio": {"resourceId": "audio"}, "language": "auto", "configVersion": provider.config_version})
    try:
        assert acquired.wait(2)
        assert provider.cancel(job)
        assert provider.poll(job) == {"state": "running"}
        assert releases == []
        assert path.exists()
    finally:
        finish.set()
    assert wait_result(provider, job) == {"state": "cancelled"}
    assert releases == ["lease"]
    assert provider.poll(job) == {"state": "cancelled"}


def test_provider_returns_clean_text_once_and_late_cancel_keeps_terminal(tmp_path):
    path = tmp_path / "audio.wav"
    wav(path)
    releases = []
    audio = SimpleNamespace(acquire=lambda _: {"leaseId": "lease", "path": str(path)}, release=lambda lease: releases.append(lease))
    provider = SenseVoiceProvider(SimpleNamespace(get=lambda _: audio), SimpleNamespace(ready=lambda: True))
    provider.state = "ready"
    provider._recognize = lambda *_: ("<|zh|><|NEUTRAL|>你好", "zh")
    job = provider.begin({"audio": {"resourceId": "audio"}, "language": "auto", "configVersion": provider.config_version})
    terminal = wait_result(provider, job)
    assert terminal == {"state": "succeeded", "text": "你好", "language": "zh"}
    assert releases == ["lease"]
    assert not provider.cancel(job)
    assert provider.poll(job) == terminal


def test_hub_keeps_preparation_selection_and_rejects_restarted_scope():
    config = {"selectedProviderId": "engine.a", "language": "auto"}
    scopes = {"engine.a": "scope-a", "engine.b": "scope-b"}
    calls = []
    engines = {name: SimpleNamespace(status=lambda: {"available": True, "state": "ready", "configVersion": "v1"}, warmup=lambda: None, begin=lambda request: (calls.append(request) or "job"), poll=lambda _: {"state": "succeeded", "text": "result", "language": None}, cancel=lambda _: True) for name in scopes}
    audio = SimpleNamespace(verifyProvider=lambda provider, _: {"scopeId": scopes[provider]}, authorize=lambda audio, _: audio, revoke=lambda _: True)
    context = SimpleNamespace(config=SimpleNamespace(get=lambda: dict(config), update=config.update), get=lambda key: audio if key == "sakura.host.audio_input" else engines[key])
    hub = SakuraASRHub(context)
    for name in engines:
        context.caller_id = name
        hub.registerProvider({"providerId": name, "serviceKey": name, "label": name, "processingLocation": "local"})
    context.caller_id = None
    prepared = hub.status()
    hub.configure({"selectedProviderId": "engine.b"})
    assert hub.warmup("engine.a")["providerId"] == "engine.a"
    request = {"requestId": "request", "providerId": prepared["providerId"], "configVersion": prepared["configVersion"], "audio": {"resourceId": "audio"}, "language": "auto"}
    assert hub.begin(request)["providerId"] == "engine.a"
    assert hub.begin({**request, "requestId": "other"})["errorCode"] == "ASR_BUSY"
    scopes["engine.a"] = "restarted"
    assert hub.poll("request")["errorCode"] == "ASR_PROVIDER_UNAVAILABLE"
    assert hub.poll("request")["errorCode"] == "ASR_PROVIDER_UNAVAILABLE"
    assert len(calls) == 1


def test_models_status_and_warmup_do_not_download_and_failed_install_preserves_old_set(tmp_path, monkeypatch):
    content = b"model fixture"
    monkeypatch.setattr(_resources, "FILES", (("model", "https://example.invalid/model", len(content)),))
    resources = _resources.ModelResources(tmp_path)
    resources.path.mkdir()
    (resources.path / "model").write_bytes(content)
    (resources.path / "complete.json").write_text(json.dumps({"version": _resources.VERSION, "sha256": {"model": "legacy-unused-digest"}}))
    requests = []
    monkeypatch.setattr(_resources.urllib.request, "urlopen", lambda *a, **k: (requests.append(a) or io.BytesIO(b"bad")))
    assert resources.load()["models"]["ready"]
    resources.verify()
    assert requests == []
    # Pretend an already installed revision is no longer the requested revision.
    # A failed replacement must retain the directory until publication succeeds.
    monkeypatch.setattr(resources, "ready", lambda: False)
    resources.start()
    resources.thread.join(3)
    assert resources.state == "failed"
    assert (resources.path / "model").read_bytes() == content
    assert not list(tmp_path.glob(".install-*"))


def test_cancelled_jobs_do_not_require_a_later_consumer_poll_to_reclaim_capacity():
    config = {"selectedProviderId": "engine", "language": "auto"}
    engine = SimpleNamespace(status=lambda: {"available": True, "configVersion": "v1"}, begin=lambda _: "job", cancel=lambda _: True)
    audio = SimpleNamespace(verifyProvider=lambda *_: {"scopeId": "scope"}, authorize=lambda audio, _: audio, revoke=lambda _: True)
    hub = SakuraASRHub(SimpleNamespace(caller_id="engine", config=SimpleNamespace(get=lambda: config), get=lambda key: audio if key == "sakura.host.audio_input" else engine))
    hub.registerProvider({"providerId": "engine", "serviceKey": "engine", "label": "Fixture", "processingLocation": "local"})
    for i in range(80):
        request = {"requestId": f"request-{i}", "providerId": "engine", "configVersion": "v1", "audio": {"resourceId": f"audio-{i}"}}
        assert hub.begin(request)["state"] == "running"
        assert hub.cancel(request["requestId"])["accepted"]
    assert len(hub.jobs) <= 64


@pytest.mark.parametrize("caller", [None, "other.plugin"])
def test_registration_and_unregister_require_the_authenticated_provider_caller(caller):
    audio = SimpleNamespace(verifyProvider=lambda *_: {"scopeId": "scope"})
    context = SimpleNamespace(caller_id="engine", get=lambda _: audio)
    hub = SakuraASRHub(context)
    descriptor = {"providerId": "engine", "serviceKey": "engine.service", "label": "Original", "processingLocation": "local"}
    hub.registerProvider(descriptor)
    context.caller_id = caller
    with pytest.raises(ValueError, match="ASR_PROVIDER_IDENTITY_INVALID"):
        hub.registerProvider({**descriptor, "label": "Forged", "processingLocation": "remote"})
    with pytest.raises(ValueError, match="ASR_PROVIDER_IDENTITY_INVALID"):
        hub.unregisterProvider("engine", "engine.service")
    assert hub.providers["engine"]["label"] == "Original"


def test_missing_models_status_and_warmup_never_start_a_download(tmp_path, monkeypatch):
    resources = _resources.ModelResources(tmp_path)
    logger = CaptureLogger()
    monkeypatch.setattr(_resources.urllib.request, "urlopen", lambda *_a, **_k: pytest.fail("Implicit model download"))
    provider = SenseVoiceProvider(SimpleNamespace(get=lambda key: logger if key == "sakura.host.logging" else None), resources)
    for _ in range(30):
        assert provider.status()["errorCode"] == "ASR_MODEL_MISSING"
    assert logger.records == []
    assert provider.warmup()["errorCode"] == "ASR_MODEL_MISSING"
    assert resources.thread is None
    assert len(logger.records) == 1
    assert logger.records[0]["fields"]["event"] == "asr.model.load.rejected"
    assert logger.records[0]["fields"]["error_code"] == "ASR_MODEL_MISSING"


def test_cancelled_warmup_does_not_log_a_model_failure():
    logger = CaptureLogger()
    resources = SimpleNamespace()
    provider = SenseVoiceProvider(SimpleNamespace(get=lambda _: logger), resources)

    def cancelled_load(check):
        provider.closed = True
        check()

    resources.verify = cancelled_load
    provider._load()
    assert len(logger.records) == 1
    assert logger.records[0]["fields"]["event"] == "asr.model.load.cancelled"
    assert logger.records[0]["level"] == "info"
    assert "error_code" not in logger.records[0]["fields"]


def test_failed_publish_and_failed_restore_keep_the_previous_models(tmp_path, monkeypatch):
    content = b"replacement"
    monkeypatch.setattr(_resources, "FILES", (("model", "https://example.invalid/model", len(content)),))
    resources = _resources.ModelResources(tmp_path)
    resources.path.mkdir()
    (resources.path / "model").write_bytes(b"previous usable version")
    monkeypatch.setattr(_resources.urllib.request, "urlopen", lambda *_a, **_k: io.BytesIO(content))
    replace = _resources.os.replace

    def failing_replace(source, target):
        if Path(source).name.startswith((".install-", ".previous-")):
            raise PermissionError("fixture publish/rollback failure")
        return replace(source, target)

    monkeypatch.setattr(_resources.os, "replace", failing_replace)
    resources.start()
    resources.thread.join(3)
    assert resources.state == "failed"
    assert resources.error == "ASR_MODEL_RESTORE_FAILED"
    backups = list(tmp_path.glob(".previous-*"))
    assert len(backups) == 1
    assert (backups[0] / "model").read_bytes() == b"previous usable version"
    assert not list(tmp_path.glob(".install-*"))


def test_same_size_corrupt_model_exposes_explicit_retry_and_recovers(tmp_path, monkeypatch):
    content = b"correct model"
    monkeypatch.setattr(_resources, "FILES", (("model", "https://example.invalid/model", len(content)),))
    resources = _resources.ModelResources(tmp_path)
    resources.path.mkdir()
    (resources.path / "model").write_bytes(b"x" * len(content))
    (resources.path / "complete.json").write_text(json.dumps({"version": _resources.VERSION, "sha256": {"model": "legacy-unused-digest"}}))
    from plugins.builtin.sakura_asr_sensevoice import plugin as provider_module

    def load_model(**_kwargs):
        if (resources.path / "model").read_bytes() != content:
            raise ValueError("native model format rejected")
        return object()

    native = SimpleNamespace(OfflineRecognizer=SimpleNamespace(from_sense_voice=load_model))
    monkeypatch.setattr(provider_module.importlib, "import_module", lambda name: native if name == "sherpa_onnx" else SimpleNamespace())
    provider = SenseVoiceProvider(SimpleNamespace(get=lambda _: None), resources)
    assert resources.load()["models"]["ready"]
    provider.warmup()
    provider.thread.join(3)
    assert provider.status()["errorCode"] == "ASR_MODEL_INVALID"
    state = resources.load()["models"]
    assert not state["ready"]
    assert state["availableActionIds"] == ["retryModels"]
    monkeypatch.setattr(_resources.urllib.request, "urlopen", lambda *_a, **_k: io.BytesIO(content))
    previous_version = provider.config_version
    provider.install_models()
    resources.thread.join(3)
    assert provider.config_version != previous_version
    assert resources.ready()
    assert not resources.invalid
    resources.verify()


def test_model_installation_cannot_race_an_active_reader_or_model_loading(tmp_path):
    from plugins.builtin.sakura_asr_sensevoice.plugin import Job

    resources = _resources.ModelResources(tmp_path)
    provider = SenseVoiceProvider(SimpleNamespace(get=lambda _: None), resources)
    provider.state = "loading"
    with pytest.raises(ValueError, match="ASR_BUSY"):
        provider.install_models()
    provider.state = "ready"
    provider.jobs["active"] = Job({})
    version = provider.config_version
    with pytest.raises(ValueError, match="ASR_BUSY"):
        provider.install_models()
    assert provider.config_version == version
    assert resources.thread is None
    resources.state = "running"
    assert not provider.status()["available"]
    assert provider.warmup()["state"] == "installing"


def test_new_model_resource_scope_removes_only_its_abandoned_download_staging(tmp_path):
    stale = tmp_path / (".install-" + "a" * 32)
    backup = tmp_path / (".previous-" + "b" * 32)
    unrelated = tmp_path / ".install-manual"
    for directory in (stale, backup, unrelated):
        directory.mkdir()
        (directory / "keep.bin").write_bytes(b"owned bytes")
    _resources.ModelResources(tmp_path)
    assert not stale.exists()
    assert (backup / "keep.bin").read_bytes() == b"owned bytes"
    assert (unrelated / "keep.bin").read_bytes() == b"owned bytes"


def test_official_provider_unregisters_on_disable_and_reregisters_in_a_new_scope(tmp_path):
    root = Path(__file__).parents[2]
    bundled = tmp_path / "distribution/plugins/builtin"
    bundled.mkdir(parents=True)
    for name in ("sakura_asr_hub", "sakura_asr_sensevoice"):
        # Setup and teardown do not import inference dependencies or need model files.
        shutil.copytree(root / "plugins/builtin" / name, bundled / name,
                        ignore=shutil.ignore_patterns("__pycache__", "requirements.txt"))
    roots = RuntimeRoots(tmp_path / "distribution", tmp_path / "user")
    application = PluginRuntimeApplication(roots, "asr-unregister-test", ToolRegistry(),
                                           PluginInventory(roots).scan().runtime_specs)
    provider_id = "sakura.asr.sensevoice"
    try:
        application.start()
        providers = application.call_service("sakura.asr", "listProviders")
        assert [item["providerId"] for item in providers] == [provider_id]
        first_identity = application.service_identity(providers[0]["serviceKey"])
        application.set_plugin_enabled(provider_id, False)
        assert application.call_service("sakura.asr", "listProviders") == []
        # Removing a registration must not overwrite the user's explicit selection.
        assert application.call_service("sakura.asr", "status")["providerId"] == provider_id
        application.set_plugin_enabled(provider_id, True)
        providers = application.call_service("sakura.asr", "listProviders")
        assert [item["providerId"] for item in providers] == [provider_id]
        assert application.service_identity(providers[0]["serviceKey"]) != first_identity
        application.reload_plugin(provider_id)
        assert len(application.call_service("sakura.asr", "listProviders")) == 1
    finally:
        application.close()


def test_official_hub_and_third_party_provider_cross_process_audio_contract(tmp_path, monkeypatch):
    from app.core_host import plugin_host_services

    logs = []
    monkeypatch.setattr(plugin_host_services, "log_message", lambda severity, message, **values: logs.append({"severity": severity, "message": message, **values}))
    root = Path(__file__).parents[2]
    distribution = tmp_path / "distribution"
    bundled = distribution / "plugins/builtin"
    bundled.mkdir(parents=True)
    shutil.copytree(root / "plugins/builtin/sakura_asr_hub", bundled / "sakura_asr_hub", ignore=shutil.ignore_patterns("__pycache__"))
    for name in ("a", "b"):
        plugin = bundled / name
        plugin.mkdir()
        (plugin / "plugin.yaml").write_text(f"api: 4\nid: fixture.asr.{name}\nname: Fixture {name}\nversion: 1.0.0\nentry: plugin:Plugin\nenabled: true\nprovides: [fixture.asr.provider.{name}]\nrequires: [sakura.asr, sakura.host.audio_input]\n")
        (plugin / "plugin.py").write_text('''import wave
class Plugin:
    def setup(self, context):
        self.audio = context.get("sakura.host.audio_input")
        self.jobs = {}
        self.name = context.plugin_id
        self.key = self.name.replace("fixture.asr.", "fixture.asr.provider.")
        context.provide(self.key, self, exports=("status", "warmup", "begin", "poll", "cancel"))
        context.get("sakura.asr").registerProvider({"providerId":self.name,"serviceKey":self.key,"label":self.name,"processingLocation":"local"})
    def status(self):
        return {"available":True,"state":"ready","configVersion":"fixture-v1"}
    def warmup(self):
        return self.status()
    def begin(self, request):
        lease = self.audio.acquire(request["audio"]["resourceId"])
        try:
            with wave.open(lease["path"], "rb") as wav:
                assert wav.getframerate() == 16000
            self.jobs[request["requestId"]] = {"state":"succeeded","text":self.name,"language":None}
        finally:
            self.audio.release(lease["leaseId"])
        return request["requestId"]
    def poll(self, job):
        return self.jobs[job]
    def cancel(self, job):
        return False
''', encoding="utf-8")
    user = tmp_path / "user"
    user.mkdir()
    roots = RuntimeRoots(distribution, user)
    application = PluginRuntimeApplication(roots, "asr-process-test", ToolRegistry(), PluginInventory(roots).scan().runtime_specs)
    try:
        application.start()
        records = application.public_snapshot()["plugins"]
        assert all(item["state"] == "active" for item in records), records
        assert len({item["pid"] for item in records}) == 3
        application.call_service("sakura.asr", "configure", {"selectedProviderId": "fixture.asr.a"})
        status = application.call_service("sakura.asr", "status")
        identity = application.service_identity(status["serviceKey"])
        allocation = application.audio_input.allocate("recording", status["providerId"], status["serviceKey"], identity["scopeId"])
        wav(Path(allocation["path"]))
        audio = application.audio_input.commit(allocation["resourceId"])
        application.call_service("sakura.asr", "configure", {"selectedProviderId": "fixture.asr.b"})
        result = application.call_service("sakura.asr", "begin", {"requestId": "first", "providerId": status["providerId"], "configVersion": status["configVersion"], "audio": audio, "language": "auto"})
        assert result["state"] == "running", result
        result = application.call_service("sakura.asr", "poll", "first")
        assert result["text"] == "fixture.asr.a"
        assert application.call_service("sakura.asr", "poll", "first") == result
        for _ in range(20):
            application.call_service("sakura.asr", "status")
            application.call_service("sakura.asr", "poll", "first")
        assert application.audio_input.count == 0
        assert not Path(allocation["path"]).exists()
    finally:
        application.close()
    hub_logs = [item for item in logs if item.get("plugin_id") == "sakura.asr"]
    events = [item["fields"]["event"] for item in hub_logs]
    assert events.count("asr.hub.started") == 1
    assert events.count("asr.provider.registered") == 2
    assert events.count("asr.selection.changed") == 2
    assert events.count("asr.request.started") == events.count("asr.request.succeeded") == 1
    assert events.count("asr.hub.stopped") == 1
    assert len(hub_logs) == 8
    request_log = next(item for item in hub_logs if item["fields"]["event"] == "asr.request.succeeded")
    assert request_log["fields"]["request_id"] == "first"
    assert request_log["fields"]["provider_id"] == "fixture.asr.a"
    assert "text" not in json.dumps(hub_logs)
    assert str(allocation["path"]) not in json.dumps(hub_logs)


@pytest.mark.parametrize("saved_language, expected", [(None, "ja"), ("ko", "ko")])
def test_sensevoice_language_is_owned_by_plugin_and_survives_restart(tmp_path, saved_language, expected):
    from app.storage.paths import StoragePaths

    root = Path(__file__).parents[2]
    distribution, user = tmp_path / "distribution", tmp_path / "user"
    bundled = distribution / "plugins/builtin"
    bundled.mkdir(parents=True)
    for name in ("sakura_asr_hub", "sakura_asr_sensevoice"):
        shutil.copytree(root / "plugins/builtin" / name, bundled / name, ignore=shutil.ignore_patterns("__pycache__"))
    # This test exercises settings IPC only; inference dependencies are never imported.
    (bundled / "sakura_asr_sensevoice/requirements.txt").unlink()
    paths = StoragePaths(user)
    hub_config = paths.plugin_data_for("sakura.asr") / "config.json"
    hub_config.parent.mkdir(parents=True)
    hub_config.write_text(json.dumps({"selectedProviderId": "sakura.asr.sensevoice", "language": "ja"}))
    if saved_language:
        own_config = paths.plugin_data_for("sakura.asr.sensevoice") / "config.json"
        own_config.parent.mkdir(parents=True)
        own_config.write_text(json.dumps({"language": saved_language}))
    roots = RuntimeRoots(distribution, user)
    application = PluginRuntimeApplication(roots, "asr-language-test", ToolRegistry(), PluginInventory(roots).scan().runtime_specs)
    try:
        application.start()
        assert all(item["state"] == "active" for item in application.public_snapshot()["plugins"]), application.public_snapshot()
        assert application.call_service("sakura.asr", "status")["language"] == expected
        sections = application.settings_sections("voice-input")
        recognition = next(item for item in sections if item["sectionId"] == "recognition")
        assert recognition["pluginId"] == "sakura.asr.sensevoice"
        assert {option["value"] for option in recognition["fields"][0]["options"]} == {"auto", "zh", "yue", "en", "ja", "ko"}
        assert recognition["fields"][0]["value"] == expected
        application.settings_save("sakura.asr.sensevoice", "recognition", {"language": "en"})
        assert application.call_service("sakura.asr", "status")["language"] == "en"
        assert json.loads(hub_config.read_text())["language"] == "ja"
        application.reload_plugin("sakura.asr.sensevoice")
        assert application.call_service("sakura.asr", "status")["language"] == "en"
    finally:
        application.close()


def test_legacy_model_marker_reuses_installed_files_without_content_scan(tmp_path, monkeypatch):
    content = b"installed model"
    monkeypatch.setattr(_resources, "FILES", (("model", "https://unused.invalid/model", len(content)),))
    resources = _resources.ModelResources(tmp_path)
    resources.path.mkdir()
    (resources.path / "model").write_bytes(content)
    marker = resources.path / "complete.json"
    marker.write_text(json.dumps({"version": _resources.VERSION, "sha256": {"model": "ignored"}}))
    original_open = Path.open

    def open_without_model_scan(path, *args, **kwargs):
        if path == resources.path / "model":
            pytest.fail("Resource readiness must not scan installed model content")
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", open_without_model_scan)
    monkeypatch.setattr(_resources.urllib.request, "urlopen", lambda *_a, **_k: pytest.fail("Installed models must not download"))
    assert resources.ready()
    resources.verify()
    resources.start()
    resources.thread.join(3)
    assert resources.state == "succeeded"
    assert json.loads(marker.read_text())["sha256"] == {"model": "ignored"}
