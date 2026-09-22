from __future__ import annotations

import shutil
import threading
import json
from pathlib import Path

import pytest

from app.core_host.plugin_application import PluginApplicationHost
from app.core_host.real_chat import RealChatBoundary
from app.core_host.server import HostConfig, ReadinessController
from app.core_host.plugin_settings import PluginSettingsBoundary
from app.core_host.tts_boundary import TTSBoundary
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


@pytest.mark.parametrize("outcome", ["ready", "optional_failed", "migration_failed", "assistant_ack_lost", "assistant_release_stuck"])
def test_visual_and_real_chat_are_published_before_optional_start_finishes(tmp_path, monkeypatch, outcome):
    optional_fails = outcome == "optional_failed"
    if outcome == "migration_failed":
        monkeypatch.setattr("app.plugins.bundled_migrations.migrate_bundled_plugins",
            lambda roots, *, progress: {"sakura.memory.mem0": "PLUGIN_MIGRATION_SOURCE_MISSING"})
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
        context.get("sakura.host.settings").register(
            {{"sectionId": "fixture", "title": "Fixture", "fields": [{{"key": "value", "label": "Value", "type": "readonly", "default": ""}}]}},
            load=lambda: {{"value": "ready"}}, save=lambda values: None)
        context.get("fixture.startup.gate").wait("optional")
        if {optional_fails!r}:
            raise RuntimeError("optional fixture setup failed")
        context.provide("fixture.optional", object(), exports=())
''', requires=("fixture.startup.gate", "sakura.host.settings"))
    shutil.copytree(REPO / "plugins/builtin/sakura_tts_hub", distribution / "plugins/builtin/sakura_tts_hub",
                    ignore=shutil.ignore_patterns("__pycache__"))
    _plugin(distribution, "fixture.voice", "fixture.voice", '''
class Plugin:
    def setup(self, context):
        self.gate = context.get("fixture.startup.gate")
        self.gate.wait("tts")
        context.provide("fixture.voice", self, exports=("status", "warmup"))
        context.get("sakura.tts").registerProvider({"providerId": "fixture.voice", "serviceKey": "fixture.voice", "label": "Fixture"})
    def status(self):
        return {"available": True}
    def warmup(self, character_id):
        self.gate.warmed(character_id)
        return True
''', requires=("fixture.startup.gate", "sakura.tts"))
    tts_config = user / "data/plugins/sakura.tts/config.json"
    tts_config.parent.mkdir(parents=True, exist_ok=True)
    tts_config.write_text(json.dumps({"selections": {"sakura": {"enabled": True, "provider": "fixture.voice"}}}), encoding="utf-8")

    entered = {stage: threading.Event() for stage in ("assistant", "optional", "tts")}
    release = {stage: threading.Event() for stage in entered}
    applications = []
    visits = []
    warmed = []

    class Gate:
        def wait(self, stage):
            visits.append(stage)
            entered[stage].set()
            assert release[stage].wait(6), stage

        def warmed(self, character_id):
            warmed.append(character_id)

    def create_application(*args, **kwargs):
        application = PluginApplicationHost(*args, **kwargs)
        application._manager.install_host_service("fixture.startup.gate", Gate(), exports=("wait", "warmed"))
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
    tts = TTSBoundary(config.generation_id, config.generation_credential, user,
        session_provider=controller.published_session,
        plugin_application_provider=controller.published_plugin_application)
    controller.set_session_published_callback(tts.warmup_current_selection)
    settings = PluginSettingsBoundary(config.generation_id, config.generation_credential, config.roots,
        application_provider=controller.published_plugin_application)
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
        pending = settings.snapshot()
        assert pending["state"] == "starting"
        pending_plugins = {item["pluginId"]: item for item in pending["plugins"]}
        assert pending_plugins["aaa.optional"]["state"] == "starting"
        assert pending_plugins["aaa.optional"]["reasonCode"] == "NOT_STARTED"
        assert pending_plugins["fixture.assistant"]["state"] == "starting"
        assert pending_plugins["fixture.assistant"]["reasonCode"] == "PLUGIN_STARTING"
        visual_identity = application.service_identity("sakura.visual.portrait")
        dependency_identity = application.service_identity("fixture.visual.dep")
        assert not entered["optional"].is_set()

        release["assistant"].set()
        assert entered["optional"].wait(5), controller.snapshot()
        assert controller.readiness() == "ready"
        assert controller.snapshot()["components"]["assistant"]["code"] == "vendor.chat_ready"
        assert not application.wait_until_loaded(timeout=0)
        assert warmed == []
        optional_pending = next(item for item in settings.snapshot()["plugins"] if item["pluginId"] == "aaa.optional")
        assert optional_pending["state"] == "starting"
        assert optional_pending["sections"] == []
        request = {"id": "early-chat", "kind": "request", "name": "chat.send",
            "generationId": config.generation_id, "generationCredential": config.generation_credential,
            "payload": {"operationId": "early-chat", "message": "现在能聊天吗？"}}
        boundary.reserve_send(request)
        boundary.handle_send(request)
        assert events[-1]["name"] == "chat.completed", events

        if outcome in {"assistant_ack_lost", "assistant_release_stuck"}:
            from app.plugins.runtime_v4 import PluginRuntimeError
            call = application.call_bound_service
            clock = [0.0]
            releasing = [False]
            if outcome == "assistant_release_stuck":
                monkeypatch.setattr("app.core_host.assistant_adapter.monotonic", lambda: clock[0])
            def failed_cleanup(service, identity, method, *args, **kwargs):
                if outcome == "assistant_release_stuck" and method == "release":
                    releasing[0] = True
                    return {"released": False}
                if releasing[0] and method == "poll":
                    clock[0] += 0.4
                    return {"state": "running", "sequence": 0, "progress": []}
                response = call(service, identity, method, *args, **kwargs)
                if outcome == "assistant_ack_lost" and method == "begin":
                    raise PluginRuntimeError("PLUGIN_CALL_TIMEOUT")
                return response
            monkeypatch.setattr(application, "call_bound_service", failed_cleanup)
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
            assert events[-1]["name"] == ("chat.failed" if outcome == "assistant_ack_lost" else "chat.completed"), events
            with pytest.raises(PluginRuntimeError, match="SERVICE_MISSING"):
                application.service_identity("sakura.assistant")
            optional_after = next(item for item in application._manager.snapshot()["plugins"] if item["pluginId"] == "aaa.optional")
            assert optional_after == optional_before

        release["optional"].set()
        assert entered["tts"].wait(5)
        assert controller.readiness() == "ready"
        assert warmed == []
        during_tts = settings.snapshot()
        assert during_tts["state"] == "starting"
        optional_state = next(item for item in during_tts["plugins"] if item["pluginId"] == "aaa.optional")
        assert optional_state["state"] == ("failed" if optional_fails else "active")
        if not optional_fails:
            assert optional_state["sections"][0]["values"] == {"value": "ready"}
        release["tts"].set()
        assert application.wait_until_loaded(timeout=5)
        controller._worker.join(2)
        assert not controller._worker.is_alive()
        assert controller.readiness() == "ready"
        assert warmed == ["sakura"]
        settled = settings.snapshot()
        assert settled["state"] == ("degraded" if optional_fails or outcome in {"migration_failed", "assistant_ack_lost", "assistant_release_stuck"} else "ready"), json.dumps(settled, ensure_ascii=False)
        if outcome == "migration_failed":
            missing = next(item for item in settled["plugins"] if item["pluginId"] == "sakura.memory.mem0")
            assert missing["state"] == "failed"
            assert missing["reasonCode"] == "PLUGIN_MIGRATION_SOURCE_MISSING"
        assert application.service_identity("sakura.visual.portrait") == visual_identity
        assert application.service_identity("fixture.visual.dep") == dependency_identity
        optional = next(item for item in application.public_snapshot()["plugins"] if item["pluginId"] == "aaa.optional")
        assert optional["state"] == ("failed" if optional_fails else "active")
        # Completing or explicitly revisiting startup never retries failed setup.
        application.start()
        assert controller.readiness() == "ready"
        assert visits == ["assistant", "optional", "tts"]
        assert warmed == ["sakura"]
    finally:
        for gate in release.values():
            gate.set()
        if chat_worker is not None:
            chat_worker.join(5)
            assert not chat_worker.is_alive()
        boundary.close()
        tts.close()
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


@pytest.mark.parametrize("fails", [False, True])
def test_migration_progress_is_visible_before_runtime_and_failure_is_actionable(tmp_path, monkeypatch, fails):
    entered, release = threading.Event(), threading.Event()

    def migrate(roots, *, progress):
        progress({"state": "running", "completed": 0, "total": 1, "pluginId": "sakura.memory.mem0"})
        entered.set()
        assert release.wait(5)
        progress({"state": "failed" if fails else "completed", "completed": 0 if fails else 1,
                  "total": 1, "pluginId": "sakura.memory.mem0" if fails else None})
        return {"sakura.memory.mem0": "PLUGIN_MIGRATION_FAILED"} if fails else {}

    monkeypatch.setattr("app.plugins.bundled_migrations.migrate_bundled_plugins", migrate)
    controller = ReadinessController(HostConfig(RuntimeRoots(tmp_path / "distribution", tmp_path / "user"), "migration-progress", "a" * 32))
    try:
        controller.begin({})
        assert entered.wait(5)
        pending = controller.snapshot()
        assert pending["readiness"] == "initializing"
        assert pending["pluginMigration"]["state"] == "running"
        assert controller.minimal_snapshot(None)["pluginMigration"] == pending["pluginMigration"]
        assert controller.published_plugin_application() is None
        release.set()
        controller._worker.join(5)
        assert not controller._worker.is_alive()
        settled = controller.snapshot()
        assert settled["revision"] > pending["revision"]
        assert settled["pluginMigration"]["state"] == ("failed" if fails else "completed")
        assert settled["readiness"] == "setup_required"
        application = controller.published_plugin_application()
        assert application is not None
        settings = PluginSettingsBoundary("migration-progress", "a" * 32, controller._config.roots,
            application_provider=controller.published_plugin_application)
        snapshot = settings.snapshot()
        if fails:
            assert snapshot["state"] == "degraded"
            missing = next(item for item in snapshot["plugins"] if item["pluginId"] == "sakura.memory.mem0")
            assert missing["state"] == "failed"
            assert missing["reasonCode"] == "PLUGIN_MIGRATION_FAILED"
            assert missing["supported"] is False
            # The normal installation and enable paths repair the missing plugin
            # in this same Core generation, without rerunning startup migration.
            package_distribution = tmp_path / "repair-package"
            _plugin(package_distribution, "sakura.memory.mem0", "fixture.memory", '''
class Plugin:
    def setup(self, context):
        context.provide("fixture.memory", object(), exports=())
''')
            source = package_distribution / "plugins/builtin/sakura.memory.mem0"
            archive = shutil.make_archive(str(tmp_path / "repair"), "zip", source)
            settings.marketplace_install({"revision": snapshot["revision"], "sourcePath": archive,
                                          "pluginId": "sakura.memory.mem0", "version": "1.0.0"})
            installed = settings.snapshot()
            memory = next(item for item in installed["plugins"] if item["pluginId"] == "sakura.memory.mem0")
            assert memory["source"] == "user"
            settings.set_enabled(installed["revision"], memory["installId"], True)
            repaired = settings.snapshot()
            assert repaired["state"] == "ready"
            memory = next(item for item in repaired["plugins"] if item["pluginId"] == "sakura.memory.mem0")
            assert memory["state"] == "active"
            assert application.service_identity("fixture.memory")["providerId"] == "sakura.memory.mem0"
    finally:
        release.set()
        controller.close()
