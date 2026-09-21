from __future__ import annotations

import shutil
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.plugins.inventory import PluginInventory
from app.plugins.runtime_v4 import PluginRuntimeError, PluginRuntimeManager
from app.storage.runtime_roots import RuntimeRoots


PROVIDER = "fixture.provider"
SERVICE = "fixture.provider.service"
CONTROL = "fixture.provider.control"
CLIENT = "fixture.consumer.service"


def _plugin(root: Path, plugin_id: str, provides: tuple[str, ...], source: str,
            requires: tuple[str, ...] = ()) -> None:
    directory = root / "plugins" / "builtin" / plugin_id
    directory.mkdir(parents=True)
    (directory / "plugin.yaml").write_text(
        f"api: 4\nid: {plugin_id}\nname: {plugin_id}\nversion: 1.0.0\n"
        f"entry: plugin:Plugin\nprovides: [{', '.join(provides)}]\n"
        f"requires: [{', '.join(requires)}]\n", encoding="utf-8",
    )
    (directory / "plugin.py").write_text(source, encoding="utf-8")


def _manager(tmp_path: Path, *, tts: bool = False) -> PluginRuntimeManager:
    distribution = tmp_path / "distribution"
    user = tmp_path / "user"
    user.mkdir()
    source = '''
import os

class Service:
    def __init__(self, label, context):
        self.label = label
        self.context = context
        self.jobs = {}
        self.polls = []
        self.cancels = []
    def ping(self):
        return {"pid": os.getpid(), "label": self.label, "caller": self.context.caller_id}
    def status(self):
        return {"available": True}
    def begin(self, request):
        job = "job_" + str(len(self.jobs) + 1)
        self.jobs[job] = request["requestId"]
        return job
    def poll(self, job):
        self.polls.append(job)
        return {"state": "succeeded", "artifact": {
            "artifactId": "artifact_" + self.jobs[job],
            "mediaType": "audio/wav", "byteLength": 10}}
    def cancel(self, job):
        self.cancels.append(job)
        return job in self.jobs
    def inspect(self):
        return {"polls": self.polls, "cancels": self.cancels}
    def crash(self):
        os._exit(9)

class Plugin:
    def setup(self, context):
        self.context = context
        self.version = 0
        self.restore()
        context.provide("fixture.provider.control", self, exports=("withdraw", "restore"))
    def withdraw(self):
        self.dispose()
    def restore(self):
        self.version += 1
        self.dispose = self.context.provide("fixture.provider.service", Service(str(self.version), self.context),
            exports=("ping", "status", "begin", "poll", "cancel", "inspect", "crash"))
'''
    if tts:
        hub = Path(__file__).parents[2] / "plugins" / "builtin" / "sakura_tts_hub"
        shutil.copytree(hub, distribution / "plugins" / "builtin" / "sakura_tts_hub")
        source += '''
        hub = self.context.get("sakura.tts")
        hub.registerProvider({"providerId": "fixture.provider",
            "serviceKey": "fixture.provider.service", "label": "Fixture"})
        self.context.effect(lambda: hub.unregisterProvider("fixture.provider", "fixture.provider.service"))
'''
    else:
        _plugin(distribution, "fixture.consumer", (CLIENT,), '''
class Plugin:
    def setup(self, context):
        self.context = context
        self.dynamic_proxy = context.get("fixture.provider.service")
        context.provide("fixture.consumer.service", self, exports=("bind", "call", "dynamic", "raw_binding"))
    def bind(self, key="fixture.provider.service"):
        self.bound = self.context.bind(key)
        return True
    def call(self, method):
        return getattr(self.bound, method)()
    def dynamic(self):
        return self.dynamic_proxy.ping()
    def raw_binding(self, identity):
        return self.context._remote_request("service.call", {
            "serviceKey": "fixture.provider.service", "method": "ping", "args": [],
            "binding": identity, "callerId": "sakura.core"})
''')
    _plugin(distribution, PROVIDER, (SERVICE, CONTROL), source,
            requires=("sakura.tts",) if tts else ())
    roots = RuntimeRoots(distribution, user)
    manager = PluginRuntimeManager(roots, "service-binding-test",
                                   PluginInventory(roots).scan().runtime_specs, call_timeout=1.0)
    manager.install_host_service("sakura.host.logging", SimpleNamespace(emit=lambda *_: {"accepted": True}),
                                 exports=("emit",))
    return manager


def test_tts_crashed_provider_cannot_receive_old_job_after_same_id_reload(tmp_path: Path) -> None:
    manager = _manager(tmp_path, tts=True)
    request = {"characterId": "fixture", "text": "hello", "options": {}}
    try:
        manager.start()
        manager.call_service("sakura.tts", "configure", "fixture", {"enabled": True, "provider": PROVIDER})
        assert manager.call_service("sakura.tts", "begin", {**request, "requestId": "old-request"})["state"] == "running"
        old_process = manager._records[PROVIDER].process
        with pytest.raises(PluginRuntimeError):
            manager.call_service(SERVICE, "crash")
        old_process.wait_for_cleanup()
        manager.reload_plugin(PROVIDER)
        assert manager._records[PROVIDER].process is not old_process
        assert manager.call_service("sakura.tts", "begin", {**request, "requestId": "new-request"})["state"] == "running"
        assert manager.call_service("sakura.tts", "cancel", "old-request")["accepted"] is False
        expired = manager.call_service("sakura.tts", "poll", "old-request")
        assert expired["state"] == "failed"
        assert expired["errorCode"] == "TTS_PROVIDER_UNAVAILABLE"
        assert manager.call_service(SERVICE, "inspect") == {"polls": [], "cancels": []}
        assert manager.call_service("sakura.tts", "poll", "new-request")["artifact"]["artifactId"] == "artifact_new-request"
    finally:
        manager.close()


def test_bound_proxy_expires_on_disable_and_does_not_rebind(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    try:
        manager.start()
        assert manager.call_service(CLIENT, "bind") is True
        original = manager.call_service(CLIENT, "call", "ping")
        manager.set_enabled(PROVIDER, False)
        with pytest.raises(PluginRuntimeError, match="SERVICE_BINDING_EXPIRED"):
            manager.call_service(CLIENT, "call", "ping")
        with pytest.raises(PluginRuntimeError, match="SERVICE_MISSING"):
            manager.call_service(CLIENT, "bind")
        manager.set_enabled(PROVIDER, True)
        with pytest.raises(PluginRuntimeError, match="SERVICE_BINDING_EXPIRED"):
            manager.call_service(CLIENT, "call", "ping")
        current = manager.call_service(CLIENT, "dynamic")
        assert current["pid"] != original["pid"]
        assert manager.call_service(CLIENT, "bind") is True
        assert manager.call_service(CLIENT, "call", "ping") == current
        with pytest.raises(PluginRuntimeError, match="SERVICE_BINDING_UNSUPPORTED"):
            manager.call_service(CLIENT, "bind", "sakura.host.logging")
    finally:
        manager.close()


def test_bound_proxy_rejects_result_returning_after_reload(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = _manager(tmp_path)
    entered = threading.Event()
    release = threading.Event()
    outcomes = []
    caller = None
    try:
        manager.start()
        manager.call_service(CLIENT, "bind")
        old_process = manager._records[PROVIDER].process
        original_call = old_process.call_service

        def delayed_return(*args, **kwargs):
            result = original_call(*args, **kwargs)
            entered.set()
            assert release.wait(3)
            return result

        def invoke():
            try:
                outcomes.append(manager.call_service(CLIENT, "call", "ping", timeout=4))
            except PluginRuntimeError as error:
                outcomes.append(error.code)

        monkeypatch.setattr(old_process, "call_service", delayed_return)
        caller = threading.Thread(target=invoke)
        caller.start()
        assert entered.wait(2)
        manager.reload_plugin(PROVIDER)
        release.set()
        caller.join(2)
        assert not caller.is_alive()
        assert outcomes == ["SERVICE_BINDING_EXPIRED"]
    finally:
        release.set()
        if caller is not None:
            caller.join(3)
        manager.close()


def test_bound_proxy_tracks_process_lifetime_not_local_service_object(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    try:
        manager.start()
        manager.call_service(CLIENT, "bind")
        original = manager.call_service(CLIENT, "call", "ping")
        manager.call_service(CONTROL, "withdraw")
        with pytest.raises(PluginRuntimeError, match="SERVICE_MISSING"):
            manager.call_service(CLIENT, "call", "ping")
        manager.call_service(CONTROL, "restore")
        replacement = manager.call_service(CLIENT, "call", "ping")
        assert replacement["pid"] == original["pid"]
        assert replacement["label"] != original["label"]
    finally:
        manager.close()


def test_binding_payload_is_validated_and_never_overrides_the_real_caller(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    try:
        manager.start()
        identity = manager.service_identity(SERVICE)
        result = manager.call_service(CLIENT, "raw_binding", identity)
        assert result["caller"] == "fixture.consumer"
        with pytest.raises(PluginRuntimeError, match="PLUGIN_PROTOCOL_INVALID"):
            manager.call_service(CLIENT, "raw_binding", {"providerId": PROVIDER})
        with pytest.raises(PluginRuntimeError, match="SERVICE_BINDING_EXPIRED"):
            manager.call_service(CLIENT, "raw_binding", {**identity, "scopeId": "previous"})
    finally:
        manager.close()
