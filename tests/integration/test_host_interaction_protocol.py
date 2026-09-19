"""Exercise ordinary plugin Host calls through a real Core subprocess and IPC."""
from __future__ import annotations

import json
import secrets
import shutil
import time
import threading
from types import SimpleNamespace

import pytest

from app.core_host.screen_capture import generation_resource_root
from tests.integration import test_core_host_real_chat_integration as core

# Reuse the existing isolated dependency preparation and real subprocess owner.
_isolated_assistant_distribution = core._isolated_assistant_distribution


_CONSUMER = '''
import json
from sakura_screen import ScreenClient
class Plugin:
    def setup(self, context):
        self.chat = context.get("sakura.host.chat")
        self.screen = ScreenClient(context.get("sakura.host.screen"))
        context.effect(self.screen.close)
        self.visual = context.get("sakura.host.visual")
        self.receipt = None
        self.image = None
        def action(method):
            return lambda values: {"values": {"output": json.dumps(method(values))}}
        actions = {name: action(getattr(self, name)) for name in ("inspect", "apply", "status", "capture", "release")}
        context.get("sakura.host.settings").register({
            "sectionId": "host", "title": "Host fixture",
            "fields": [{"key": "output", "type": "string", "label": "Output", "default": "", "readonly": True, "maxLength": 16000}],
            "actions": [{"actionId": name, "label": name} for name in actions],
        }, load=lambda: {"output": ""}, save=lambda values: {}, actions=actions)

    def inspect(self, values):
        return {"chat": self.chat.current(), "visual": self.visual.current()}

    def apply(self, values):
        target = self.visual.current()["target"]
        result = self.visual.apply({"target": target,
            "control": {"version": 1, "resourceId": target["resourceId"], "payload": {"angle": 12}}})
        self.receipt = result.get("requestId")
        return result

    def status(self, values):
        return self.visual.status(self.receipt)

    def capture(self, values):
        result = self.screen.capture({"sessionId": self.chat.current()["sessionId"], "resolution": "720p"})
        self.image = result["resourceId"]
        return result

    def release(self, values):
        return self.screen.release(self.image)
'''


def test_real_core_routes_scoped_screen_visual_and_detach_protocol(tmp_path):
    app_root = core._configure_app_root(tmp_path, 9)
    distribution = tmp_path / "distribution"
    numeric = distribution / "plugins/builtin/numeric"
    numeric.mkdir(parents=True)
    source = core.REPO_ROOT / "tests/fixtures/visual_numeric"
    shutil.copyfile(source / "plugin.py", numeric / "plugin.py")
    (numeric / "renderer.js").write_text("export function mount() {}", encoding="utf-8")
    (numeric / "plugin.yaml").write_text(
        "api: 4\nid: fixture.numeric\nentry: plugin:Plugin\nprovides: [fixture.numeric.control]\n"
        "requires: [sakura.host.character]\nvisuals:\n  - type: fixture.numeric@1\n"
        "    service: fixture.numeric.control\n    contract: 1\n    renderer: renderer.js\n", encoding="utf-8")
    consumer = distribution / "plugins/builtin/host_client"
    consumer.mkdir()
    (consumer / "plugin.py").write_text(_CONSUMER, encoding="utf-8")
    (consumer / "plugin.yaml").write_text(
        "api: 4\nid: fixture.host_client\nentry: plugin:Plugin\nprovides: []\n"
        "requires: [sakura.host.settings, sakura.host.chat, sakura.host.screen, sakura.host.visual]\n", encoding="utf-8")
    package = app_root / "characters/sakura"
    manifest = json.loads((package / "character.json").read_text(encoding="utf-8"))
    manifest.pop("portrait", None)
    manifest["visuals"] = {"resources": [{"id": "numeric", "type": "fixture.numeric@1", "root": "numeric", "entry": "resource.json"}], "default": "numeric"}
    (package / "character.json").write_text(json.dumps(manifest), encoding="utf-8")
    (package / "numeric").mkdir()
    (package / "numeric/resource.json").write_text('{"maxAngle": 30}', encoding="utf-8")
    process = core._start_host(app_root, distribution_root=distribution)
    events = []
    sequence = 0

    def receive_response(request_id):
        while True:
            frame = core._read(process)
            if frame["kind"] == "event":
                events.append(frame)
            else:
                assert frame["id"] == request_id, frame
                return frame

    def exchange(name, payload, **overrides):
        nonlocal sequence
        sequence += 1
        request = core._request(f"host-{sequence}", name, payload)
        request.update(overrides)
        core._send(process, request)
        return receive_response(request["id"])

    def desktop(name, payload=None, **overrides):
        return exchange(name, {"generationId": core.GENERATION_ID, **(payload or {})}, **overrides)

    def action(name):
        response = exchange("plugins.settings.action", {"pluginId": "fixture.host_client", "sectionId": "host", "actionId": name, "values": {}})
        assert response["ok"], response
        return json.loads(response["payload"]["values"]["output"])

    resource_path = None
    try:
        core._wait_ready(process, ["transport.concurrent-router", "assistant.plugins-v1"])
        deadline = time.monotonic() + 5
        while True:
            inventory = exchange("plugins.settings.get", {})["payload"]
            record = next(item for item in inventory["plugins"] if item["pluginId"] == "fixture.host_client")
            if record["state"] == "active":
                break
            assert time.monotonic() < deadline, record
        facts = desktop("host.interaction.current")["payload"]
        assert facts["sessionId"] and not facts["idle"]
        ready = {"sessionId": facts["sessionId"], "idle": True, "activityRevision": 1}
        assert desktop("host.interaction.state", ready)["payload"]["accepted"]
        inspected = action("inspect")
        assert inspected["chat"]["idle"]
        target = inspected["visual"]["target"]
        assert target["resourceId"] == "numeric"
        assert "rendererData" not in inspected["visual"]

        for name in ("host.interaction.current", "host.interaction.state", "host.interaction.detach",
                     "host.screen.result", "host.visual.claim", "host.visual.result"):
            rejected = desktop(name, {"generationId": "retired"})
            assert rejected["error"]["code"] == "GENERATION_MISMATCH"
        unauthorized = desktop("host.interaction.detach", generationCredential="invalid")
        assert unauthorized["error"]["code"] == "GENERATION_CREDENTIAL_MISMATCH"
        assert desktop("host.interaction.current")["payload"]["idle"], "rejected detach cannot change UI state"

        receipt = action("apply")
        assert receipt["accepted"]
        assert events[-1]["name"] == "host.visual.apply"
        claim = {"requestId": receipt["requestId"], "target": target}
        assert desktop("host.visual.claim", claim)["payload"]["accepted"]
        assert desktop("host.visual.result", {**claim, "status": "displayed"})["payload"]["accepted"]
        assert action("status")["status"] == "displayed"

        capture_request = core._request("capture-action", "plugins.settings.action", {
            "pluginId": "fixture.host_client", "sectionId": "host", "actionId": "capture", "values": {}})
        core._send(process, capture_request)
        capture_event = core._read(process)
        assert capture_event["name"] == "host.screen.capture", capture_event
        pending = capture_event["payload"]
        assert set(pending) == {"requestId", "sessionId", "resolution"}
        token = secrets.token_hex(16)
        resource_path = generation_resource_root(core.GENERATION_ID) / f"{token}.jpg"
        resource_path.parent.mkdir(parents=True, exist_ok=True)
        image = (b"\xff\xd8\xff\xc0\x00\x11\x08\x00\x02\x00\x03\x03\x01\x11\x00\x02\x11\x00\x03\x11\x00"
                 b"\xff\xda\x00\x0c\x03\x01\x00\x02\x11\x03\x11\x00\x3f\x00\x01\xff\xd9")
        resource_path.write_bytes(image)
        resource = {"generationId": core.GENERATION_ID, "resourceToken": token, "mimeType": "image/jpeg",
                    "width": 3, "height": 2, "byteLength": len(image), "capturedAt": "2026-09-19T00:00:00Z", "screenName": "fixture"}
        result = core._request("capture-result", "host.screen.result", {"generationId": core.GENERATION_ID,
            "requestId": pending["requestId"], "sessionId": pending["sessionId"], "resource": resource})
        core._send(process, result)
        responses = [core._read(process), core._read(process)]
        by_id = {response["id"]: response for response in responses}
        assert by_id["capture-result"]["payload"]["accepted"]
        captured = json.loads(by_id["capture-action"]["payload"]["values"]["output"])
        assert captured["resourceId"].startswith("image-")
        assert "resourceToken" not in captured and "data_url" not in captured
        assert not resource_path.exists()

        assert desktop("host.interaction.detach")["payload"]["accepted"]
        assert events[-1]["name"] == "host.visual.cancel"
        assert action("status")["status"] == "cancelled"
        assert action("release") == {"released": False}
        assert not desktop("host.interaction.current")["payload"]["idle"]
        assert not desktop("host.visual.result", {**claim, "status": "displayed"})["payload"]["accepted"]

        assert desktop("host.interaction.state", ready)["payload"]["accepted"]
        next_receipt = action("apply")
        assert next_receipt["accepted"]
        inventory = exchange("plugins.settings.get", {})["payload"]
        disabled = exchange("plugins.enabled.set", {"revision": inventory["revision"], "installId": record["installId"], "enabled": False})
        assert disabled["ok"], disabled
        assert any(event["name"] == "host.visual.cancel" and event["payload"]["requestId"] == next_receipt["requestId"] for event in events)
        assert not desktop("host.visual.claim", {"requestId": next_receipt["requestId"], "target": target})["payload"]["accepted"]
    finally:
        core._stop(process)
        if resource_path is not None:
            resource_path.unlink(missing_ok=True)


def test_visual_selection_reports_saved_preference_when_runtime_rebind_fails(tmp_path, monkeypatch):
    from app.config.character_loader import CharacterRegistry
    from app.core_host.character_settings import CharacterSettingsBoundary
    from app.plugins.host_services import HOST_CALLER, HOST_CALLER_SCOPE
    from tests.integration.test_visual_plugin_boundary import numeric_application

    with numeric_application(tmp_path) as (application, package, _):
        manifest_path = package / "character.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["visuals"]["resources"].append({**manifest["visuals"]["resources"][0], "id": "numeric-2"})
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        user_root = package.parents[1]
        settings = CharacterSettingsBoundary("generation", "credential", user_root,
            plugin_application_provider=lambda: application)
        registry = CharacterRegistry(user_root)
        settings._settings.save_character_selection(registry, "character", {})
        application.bind_visual_character(registry.get("character"))
        target = application.visual_controls.current()["target"]
        identity = application.service_identity("fixture.numeric.control")
        original_rebind = application.bind_character_presentation
        def fail_rebind(character_id):
            raise RuntimeError("fixture render preparation failed")
        monkeypatch.setattr(application, "bind_character_presentation", fail_rebind)
        owner = HOST_CALLER.set(identity["providerId"])
        scope = HOST_CALLER_SCOPE.set(identity["scopeId"])
        try:
            result = settings.select_visual_resource(target, "numeric-2")
        finally:
            HOST_CALLER_SCOPE.reset(scope)
            HOST_CALLER.reset(owner)
        assert result == {"accepted": False, "saved": True, "reasonCode": "VISUAL_SELECTION_APPLY_FAILED"}
        assert settings._settings.load_visual_selections()["character"] == "numeric-2"
        assert application.visual_controls.current()["target"] == target
        monkeypatch.setattr(application, "bind_character_presentation", original_rebind)
        owner = HOST_CALLER.set(identity["providerId"])
        scope = HOST_CALLER_SCOPE.set(identity["scopeId"])
        try:
            retried = settings.select_visual_resource(target, "numeric-2")
        finally:
            HOST_CALLER_SCOPE.reset(scope)
            HOST_CALLER.reset(owner)
        assert retried == {"accepted": True, "changePlan": "visual_rebind"}
        assert application.visual_controls.current()["target"]["resourceId"] == "numeric-2"


@pytest.mark.parametrize("desktop_changes", [False, True])
def test_visual_selection_reserves_idle_chat_while_validating_resource(tmp_path, monkeypatch, desktop_changes):
    from app.config.character_loader import CharacterRegistry
    from app.core_host.character_settings import CharacterSettingsBoundary
    from app.core_host.real_chat import ChatTurnInput, RealChatBoundary, RealChatRejection
    from app.plugins.host_services import HOST_CALLER, HOST_CALLER_SCOPE
    from tests.integration.test_visual_plugin_boundary import numeric_application

    with numeric_application(tmp_path) as (application, package, _):
        manifest_path = package / "character.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["visuals"]["resources"].append({**manifest["visuals"]["resources"][0], "id": "numeric-2"})
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        user_root = package.parents[1]
        settings = CharacterSettingsBoundary("generation", "credential", user_root,
            plugin_application_provider=lambda: application)
        registry = CharacterRegistry(user_root)
        settings._settings.save_character_selection(registry, "character", {})
        application.bind_visual_character(registry.get("character"))
        session = SimpleNamespace(character=registry.get("character"))
        boundary = RealChatBoundary("generation", "credential", user_root, session_provider=lambda: session)
        application._chat_boundary = boundary
        application.bind_visual_selector(settings.select_visual_resource)
        state = boundary.current_host_state()
        application.chat.set_ui_state({"sessionId": state["sessionId"], "idle": True, "activityRevision": 1})
        target = application.visual_controls.current()["target"]
        identity = application.service_identity("fixture.numeric.control")
        entered, resume = threading.Event(), threading.Event()
        validate = application.validate_visual_choice
        def blocking_validate(*args):
            entered.set()
            assert resume.wait(5)
            return validate(*args)
        monkeypatch.setattr(application, "validate_visual_choice", blocking_validate)
        results, errors = [], []
        def select():
            owner = HOST_CALLER.set(identity["providerId"])
            scope = HOST_CALLER_SCOPE.set(identity["scopeId"])
            try:
                results.append(application.visual_controls.select({"target": target, "resourceId": "numeric-2"}))
            except BaseException as error:
                errors.append(error)
            finally:
                HOST_CALLER_SCOPE.reset(scope)
                HOST_CALLER.reset(owner)
        worker = threading.Thread(target=select)
        worker.start()
        try:
            assert entered.wait(5)
            # This also proves no admission lock is held across the plugin RPC.
            assert not boundary.current_host_state()["idle"]
            with pytest.raises(RealChatRejection, match="RUNTIME_UPDATE_BUSY"):
                boundary._reserve_turn(ChatTurnInput("during-selection", "hello"))
            if desktop_changes:
                application.chat.set_ui_state({"sessionId": state["sessionId"], "idle": False, "activityRevision": 2})
        finally:
            resume.set()
            worker.join(5)
        assert not worker.is_alive()
        assert not errors
        if desktop_changes:
            assert results == [{"accepted": False, "reasonCode": "CHAT_BUSY"}]
            assert settings._settings.load_visual_selections().get("character") is None
            assert application.visual_controls.current()["target"] == target
        else:
            assert results == [{"accepted": True, "changePlan": "visual_rebind"}]
            assert application.visual_controls.current()["target"]["resourceId"] == "numeric-2"
        boundary._reserve_turn(ChatTurnInput("after-selection", "hello"))
        boundary.abandon_host_message("after-selection")
        boundary.close()
