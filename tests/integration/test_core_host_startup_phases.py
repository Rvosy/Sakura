from __future__ import annotations

import shutil
import threading
from pathlib import Path

import pytest

from app.core_host.plugin_application import PluginApplicationHost
from app.core_host.real_chat import RealChatBoundary
from app.core_host.server import HostConfig, ReadinessController
from app.storage.runtime_roots import RuntimeRoots


REPO = Path(__file__).resolve().parents[2]


def _plugin(distribution, plugin_id, service, body, requires=()):
    root = distribution / "plugins/builtin" / plugin_id
    root.mkdir(parents=True)
    (root / "plugin.yaml").write_text(
        f"api: 4\nid: {plugin_id}\nname: Fixture\nversion: 1.0.0\nentry: plugin:Plugin\n"
        f"provides: [{service}]\nrequires: [{', '.join(requires)}]\n", encoding="utf-8",
    )
    (root / "plugin.py").write_text(body, encoding="utf-8")


@pytest.mark.parametrize("outcome", ["ready", "optional_failed", "assistant_ack_lost"])
def test_visual_and_real_chat_are_published_before_optional_start_finishes(tmp_path, monkeypatch, outcome):
    optional_fails = outcome == "optional_failed"
    user, distribution = tmp_path / "user", tmp_path / "distribution"
    shutil.copytree(REPO / "tests/fixtures/runtime_v2/wp_3_01/ready", user)
    # The portrait fixture must be a loadable image, not its placeholder text.
    import base64
    (user / "characters/sakura/portraits/neutral.txt").write_bytes(base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+j1ioAAAAASUVORK5CYII="
    ))
    portrait = distribution / "plugins/builtin/sakura_portrait"
    shutil.copytree(REPO / "plugins/builtin/sakura_portrait", portrait, ignore=shutil.ignore_patterns("__pycache__"))
    manifest = portrait / "plugin.yaml"
    manifest.write_text(manifest.read_text(encoding="utf-8").replace(
        "requires:\n", "requires:\n  - fixture.visual.dep\n"), encoding="utf-8")
    _plugin(distribution, "fixture.visual.dep", "fixture.visual.dep", '''
class Plugin:
    def setup(self, context):
        context.provide("fixture.visual.dep", object(), exports=())
''')
    _plugin(distribution, "fixture.assistant", "sakura.assistant", '''
import json
from pathlib import Path
class Plugin:
    def setup(self, context):
        context.get("fixture.startup.gate").wait("assistant")
        self.artifacts = context.get("sakura.host.artifacts")
        context.provide("sakura.assistant", self, exports=("prepare", "begin", "poll", "result", "cancel", "release"))
    def prepare(self, session):
        return {"state": "ready", "code": "vendor.chat_ready", "message": "", "retryable": False}
    def begin(self, request):
        allocated = self.artifacts.allocate({"mediaType": "application/json", "suffix": ".json"})
        Path(allocated["path"]).write_text(json.dumps({"reply": {"segments": [{"text": "可选插件仍在启动。"}]}, "actions": []}), encoding="utf-8")
        self.output = self.artifacts.commit(allocated["artifactId"])
        return {"operationId": request["operationId"]}
    def poll(self, operation_id, sequence, wait_ms):
        return {"state": "completed", "sequence": 0, "progress": []}
    def result(self, operation_id):
        return self.output
    def cancel(self, operation_id):
        return {"cancelled": True}
    def release(self, operation_id, status):
        self.artifacts.release(self.output["artifactId"])
        return {"released": True}
''', requires=("fixture.startup.gate", "sakura.host.artifacts"))
    # Its ID sorts before both required providers; priority sorting alone would fail.
    _plugin(distribution, "aaa.optional", "fixture.optional", f'''
class Plugin:
    def setup(self, context):
        context.get("fixture.startup.gate").wait("optional")
        if {optional_fails!r}:
            raise RuntimeError("optional fixture setup failed")
        context.provide("fixture.optional", object(), exports=())
''', requires=("fixture.startup.gate",))

    entered = {stage: threading.Event() for stage in ("assistant", "optional")}
    release = {stage: threading.Event() for stage in entered}
    applications = []
    visits = []

    class Gate:
        def wait(self, stage):
            visits.append(stage)
            entered[stage].set()
            assert release[stage].wait(6), stage

    def create_application(*args):
        application = PluginApplicationHost(*args)
        application._manager.install_host_service("fixture.startup.gate", Gate(), exports=("wait",))
        applications.append(application)
        return application

    monkeypatch.setattr("app.core_host.plugin_application.PluginApplicationHost", create_application)
    config = HostConfig(RuntimeRoots(distribution, user), "startup-phases", "a" * 32)
    controller = ReadinessController(config)
    events = []
    boundary = RealChatBoundary(config.generation_id, config.generation_credential, user,
        session_provider=controller.published_session,
        plugin_application_provider=controller.published_plugin_application,
        event_publisher=events.append)
    controller.bind_chat_boundary(boundary)
    chat_worker = None
    try:
        controller.begin({})
        assert entered["assistant"].wait(5), controller.snapshot()
        early = controller.snapshot()
        assert early["readiness"] == "initializing"
        assert early["currentCharacterSummary"] is None
        assert controller.published_session() is None
        assert early["characterPresentation"]["visual"]["providerId"] == "sakura.portrait"
        assert controller.published_character_presentation() == early["characterPresentation"]
        application = controller.published_plugin_application()
        visual_identity = application.service_identity("sakura.visual.portrait")
        dependency_identity = application.service_identity("fixture.visual.dep")
        assert not entered["optional"].is_set()

        release["assistant"].set()
        assert entered["optional"].wait(5), controller.snapshot()
        assert controller.readiness() == "ready"
        assert controller.snapshot()["components"]["assistant"]["code"] == "vendor.chat_ready"
        assert not application.wait_until_loaded(timeout=0)
        request = {"id": "early-chat", "kind": "request", "name": "chat.send",
            "generationId": config.generation_id, "generationCredential": config.generation_credential,
            "payload": {"operationId": "early-chat", "message": "现在能聊天吗？"}}
        boundary.reserve_send(request)
        boundary.handle_send(request)
        assert events[-1]["name"] == "chat.completed", events

        if outcome == "assistant_ack_lost":
            from app.plugins.runtime_v4 import PluginRuntimeError
            call = application.call_bound_service
            def lose_begin_ack(service, identity, method, *args):
                response = call(service, identity, method, *args)
                if method == "begin":
                    raise PluginRuntimeError("PLUGIN_CALL_TIMEOUT")
                return response
            monkeypatch.setattr(application, "call_bound_service", lose_begin_ack)
            failed_request = {**request, "id": "lost-ack", "payload": {"operationId": "lost-ack", "message": "请求已执行但确认丢失。"}}
            finished, failures = threading.Event(), []
            def send_with_lost_ack():
                try:
                    boundary.reserve_send(failed_request)
                    boundary.handle_send(failed_request)
                except BaseException as error:
                    failures.append(error)
                finally:
                    finished.set()
            optional_before = next(item for item in application._manager.snapshot()["plugins"] if item["pluginId"] == "aaa.optional")
            chat_worker = threading.Thread(target=send_with_lost_ack)
            chat_worker.start()
            # Cleanup must finish while the unrelated startup gate is closed.
            assert finished.wait(2), "Assistant abort was blocked by optional startup"
            assert not failures
            assert events[-1]["name"] == "chat.failed", events
            with pytest.raises(PluginRuntimeError, match="SERVICE_MISSING"):
                application.service_identity("sakura.assistant")
            optional_after = next(item for item in application._manager.snapshot()["plugins"] if item["pluginId"] == "aaa.optional")
            assert optional_after == optional_before

        release["optional"].set()
        assert application.wait_until_loaded(timeout=5)
        controller._worker.join(2)
        assert not controller._worker.is_alive()
        assert controller.readiness() == "ready"
        assert application.service_identity("sakura.visual.portrait") == visual_identity
        assert application.service_identity("fixture.visual.dep") == dependency_identity
        optional = next(item for item in application.public_snapshot()["plugins"] if item["pluginId"] == "aaa.optional")
        assert optional["state"] == ("failed" if optional_fails else "active")
        # Completing or explicitly revisiting startup never retries failed setup.
        application.start()
        assert controller.readiness() == "ready"
        assert visits == ["assistant", "optional"]
    finally:
        for gate in release.values():
            gate.set()
        if chat_worker is not None:
            chat_worker.join(5)
            assert not chat_worker.is_alive()
        boundary.close()
        controller.close()
    assert all(item["pid"] is None for item in applications[0]._manager.snapshot()["plugins"])


def test_malformed_character_configuration_keeps_its_stable_readiness_reason(tmp_path):
    shutil.copytree(REPO / "tests/fixtures/runtime_v2/wp_3_01/ready", tmp_path / "user")
    (tmp_path / "user/config/characters.yaml").write_text("[invalid", encoding="utf-8")
    controller = ReadinessController(HostConfig(RuntimeRoots(tmp_path / "distribution", tmp_path / "user"), "bad-character-config", "a" * 32))
    try:
        controller.begin({})
        controller._worker.join(5)
        assert not controller._worker.is_alive()
        assert controller.snapshot()["components"]["assistant"]["code"] == "CONFIG_DATA_INVALID"
    finally:
        controller.close()
