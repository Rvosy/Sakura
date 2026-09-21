from __future__ import annotations

import io
import queue
import threading
from types import SimpleNamespace

from app.core_host.protocol import decode_frame
from app.core_host.server import ControlDispatcher, HostConfig, run_host
from app.core_host.visual_host import VisualControlResult
from app.storage.timeline import TimelineKind
from app.storage.runtime_roots import RuntimeRoots
from tests.integration.test_context_plugin_chat import chat, GENERATION_ID, GENERATION_CREDENTIAL
from tests.integration.test_visual_plugin_boundary import numeric_application


def test_slow_visual_parser_does_not_hold_chat_commit_release_or_next_admission(chat, tmp_path, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    with numeric_application(tmp_path / "visual") as (application, package, resource):
        host = application.visuals
        binding = host.bind("character", package, resource)
        chat.session.visual_binding = binding
        read_result = chat.application.read_assistant_result
        def with_control(descriptor):
            result = read_result(descriptor)
            result["reply"]["segments"][0]["control"] = {"version": 1, "resourceId": resource.id, "payload": {"angle": 10}}
            return result
        monkeypatch.setattr(chat.application, "read_assistant_result", with_control)
        call = host._runtime.call_service
        def delayed_parse(service, method, *args):
            if method == "parseControl":
                entered.set()
                assert release.wait(6), "parser gate not released"
            return call(service, method, *args)
        monkeypatch.setattr(host._runtime, "call_service", delayed_parse)
        result, errors, parsed = [], [], []
        finished = threading.Event()
        def send(operation):
            try:
                result.append(chat.send(operation, "你好"))
            except BaseException as error:
                errors.append(error)
            finally:
                finished.set()
        worker = threading.Thread(target=send, args=("first",))
        parser = None
        worker.start()
        try:
            assert finished.wait(3), "optional parser held the Assistant turn open"
            worker.join(1)
            assert not errors
            assert result[0]["name"] == "chat.completed"
            assert not entered.is_set()
            assert not chat.application._assistant_inputs
            assert chat.session.assistant._operation_id is None
            segments = result[0]["payload"]["reply"]["segments"]
            stored = [entry for entry in chat.timeline.read_all(chat.session.character.id) if entry.kind is TimelineKind.ASSISTANT]
            assert stored[0].payload["segments"] == segments
            envelope = segments[0]["control"]
            parser = threading.Thread(target=lambda: parsed.append(host.resolve_control(envelope)))
            parser.start()
            assert entered.wait(2)
            finished.clear()
            worker = threading.Thread(target=send, args=("second",))
            worker.start()
            assert finished.wait(3), "slow visual parser blocked the next chat admission"
            worker.join(1)
            assert not errors
            assert result[1]["name"] == "chat.completed"
            assert not chat.application._assistant_inputs
            # Retiring a binding does not wait for its in-flight parser.
            host.clear()
            assert host.resolve_control(envelope).reason_code == "VISUAL_BINDING_EXPIRED"
            release.set()
            parser.join(2)
            assert not parser.is_alive()
            assert parsed[0].control is None
            assert parsed[0].reason_code == "VISUAL_BINDING_EXPIRED"
        finally:
            release.set()
            worker.join(5)
            if parser is not None:
                parser.join(5)
            assert not worker.is_alive()


def test_visual_parse_uses_concurrent_lane_and_checks_generation(tmp_path, monkeypatch):
    incoming = queue.Queue()
    messages = queue.Queue()
    entered, release = threading.Event(), threading.Event()
    errors = []
    calls = []
    def resolve(envelope):
        calls.append(envelope)
        entered.set()
        assert release.wait(4)
        return VisualControlResult(None, "VISUAL_BINDING_EXPIRED")
    application = SimpleNamespace(visuals=SimpleNamespace(resolve_control=resolve))
    monkeypatch.setattr(ControlDispatcher, "published_plugin_application", lambda self: application)
    monkeypatch.setattr("app.core_host.server.read_frame", lambda _stream: incoming.get())
    class Output(io.BytesIO):
        def write(self, data):
            messages.put(decode_frame(data))
            return len(data)
    def request(request_id, name, payload=None, **overrides):
        return {"protocolMajor": 2, "protocolMinor": 2, "kind": "request", "id": request_id,
            "name": name, "payload": payload or {}, "generationId": GENERATION_ID,
            "generationCredential": GENERATION_CREDENTIAL, "priority": "interactive", "deadlineMs": 5000, **overrides}
    def run():
        try:
            run_host(io.BytesIO(), Output(), HostConfig(RuntimeRoots(tmp_path, tmp_path), GENERATION_ID, GENERATION_CREDENTIAL))
        except BaseException as error:
            errors.append(error)
    worker = threading.Thread(target=run)
    worker.start()
    try:
        incoming.put(request("hello", "system.hello", {"protocol": {"major": 2, "minMinor": 2, "maxMinor": 2},
            "requiredCapabilities": ["system.hello"], "optionalCapabilities": []}))
        assert messages.get(timeout=2)["ok"]
        incoming.put(request("parse", "visual.control.parse", {"deferred": {}}))
        assert entered.wait(2)
        incoming.put(request("health", "system.health"))
        health = messages.get(timeout=2)
        assert health["id"] == "health" and health["ok"]
        incoming.put(request("stale", "visual.control.parse", generationId="old"))
        assert messages.get(timeout=2)["error"]["code"] == "GENERATION_MISMATCH"
        incoming.put(request("credential", "visual.control.parse", generationCredential="invalid"))
        assert messages.get(timeout=2)["error"]["code"] == "GENERATION_CREDENTIAL_MISMATCH"
        assert len(calls) == 1
        release.set()
        result = messages.get(timeout=2)
        assert result["id"] == "parse"
        assert result["payload"] == {"control": None, "reasonCode": "VISUAL_BINDING_EXPIRED"}
    finally:
        release.set()
        incoming.put(None)
        worker.join(5)
        assert not worker.is_alive()
        assert not errors
