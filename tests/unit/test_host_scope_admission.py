"""A validated RPC must not acquire Host effects after its process scope ends."""

import threading
from types import SimpleNamespace

import pytest

from app.core_host.chat_host import ChatHost
from app.core_host.real_chat import RealChatBoundary
from app.core_host.screen_host import ScreenHost
from app.core_host.visual_control_host import HostVisualService
from app.plugins.runtime_v4 import PluginRuntimeManager
from app.plugins.sakura_plugin_sdk import PluginApiError


def _manager(tmp_path):
    manager = PluginRuntimeManager(tmp_path, "generation", [])
    process = SimpleNamespace(scope_id="original-scope")
    manager._records["consumer"] = SimpleNamespace(
        process=process, state="active", reason_code="READY",
        spec=SimpleNamespace(name="Consumer", provides=(), requires=()),
    )
    return manager, process


def _invoke_after_scope_clear(manager, process, service, method, request, replacement, monkeypatch):
    checked, resume = threading.Event(), threading.Event()
    route = manager._route_service_call
    results, errors = [], []

    def delayed_route(*args, **kwargs):
        # _handle_plugin_request has authenticated this exact process already.
        checked.set()
        assert resume.wait(3)
        return route(*args, **kwargs)

    monkeypatch.setattr(manager, "_route_service_call", delayed_route)

    def invoke():
        try:
            results.append(manager._handle_plugin_request(
                "consumer", "service.call",
                {"serviceKey": service, "method": method, "args": [request]},
                calling_process=process,
            ))
        except BaseException as error:
            errors.append(error)

    worker = threading.Thread(target=invoke)
    worker.start()
    try:
        assert checked.wait(3)
        with manager._lock:
            record = manager._records["consumer"]
            record.process = None
            record.state = "disabled"
            record.reason_code = "PLUGIN_DISABLED"
        manager._clear_plugin_scope("consumer", process.scope_id)
        if replacement:
            with manager._lock:
                record.process = SimpleNamespace(scope_id="replacement-scope")
                record.state = "active"
                record.reason_code = "READY"
    finally:
        resume.set()
        worker.join(3)
    assert not worker.is_alive()
    assert results == []
    assert len(errors) == 1
    assert isinstance(errors[0], PluginApiError)
    assert errors[0].code == "SERVICE_BINDING_EXPIRED"


@pytest.mark.parametrize("replacement", [False, True], ids=["disabled", "reloaded"])
def test_late_capture_cannot_create_resources_after_scope_clear(tmp_path, monkeypatch, replacement):
    manager, process = _manager(tmp_path)
    events = []

    def emit(name, payload):
        events.append(name)
        screen.complete({**payload, "resource": {}})

    screen = ScreenHost(
        "generation", session_provider=lambda: "session", emit_callback=emit,
        resource_consumer=lambda *args, **kwargs: SimpleNamespace(
            data_url="data:image/jpeg;base64,AA==", width=1, height=1,
            captured_at="2026-09-19T00:00:00Z", screen_name="test",
        ),
        commit_scope=lambda owner, commit: manager.commit_plugin_scope(*owner, commit),
    )
    manager.install_host_service("sakura.host.screen", screen, exports=("capture", "release"))

    _invoke_after_scope_clear(manager, process, "sakura.host.screen", "capture",
        {"sessionId": "session", "resolution": "720p"}, replacement, monkeypatch)

    assert events == []
    assert screen._pending == {}
    assert screen._resources == {}


@pytest.mark.parametrize("replacement", [False, True], ids=["disabled", "reloaded"])
def test_late_chat_releases_real_admission_without_starting_a_turn(tmp_path, monkeypatch, replacement):
    manager, process = _manager(tmp_path)
    session = SimpleNamespace(character=SimpleNamespace(id="character"))
    boundary = RealChatBoundary("generation", "credential", tmp_path,
        session_provider=lambda: session, timeline_store=object())
    session_id = boundary.current_host_state()["sessionId"]
    screen = ScreenHost("generation", session_provider=lambda: session_id, emit_callback=lambda *args: None)
    started, events = [], []

    def run(operation_id, emit):
        started.append(operation_id)
        boundary.abandon_host_message(operation_id)

    monkeypatch.setattr(boundary, "run_reserved_plugin_message", run)
    chat = ChatHost(boundary_provider=lambda: boundary, screen_host=screen,
        emit_callback=lambda *args: events.append(args),
        commit_scope=lambda owner, commit: manager.commit_plugin_scope(*owner, commit))
    assert chat.set_ui_state({"sessionId": session_id, "idle": True, "activityRevision": 0})["accepted"]
    manager.install_host_service("sakura.host.chat", chat, exports=("submit",))

    _invoke_after_scope_clear(manager, process, "sakura.host.chat", "submit",
        {"sessionId": session_id, "message": "late input", "resources": []}, replacement, monkeypatch)

    assert started == []
    assert events == []
    assert chat._active == {}
    state = boundary.current_host_state()
    assert state["interactionRevision"] == 1  # The actual reservation was made, then abandoned.
    assert state["idle"]
    boundary.close()


@pytest.mark.parametrize("replacement", [False, True], ids=["disabled", "reloaded"])
def test_late_visual_apply_cannot_parse_or_publish_after_scope_clear(tmp_path, monkeypatch, replacement):
    manager, process = _manager(tmp_path)
    parsed, events = [], []
    binding_id = "a" * 32

    def parse(control):
        parsed.append(control)
        return SimpleNamespace(control={"version": 1, "bindingId": binding_id,
            "resourceId": "portrait", "state": {"portrait": "smile"}}, reason_code="READY")

    binding = SimpleNamespace(
        presentation=lambda: {"bindingId": binding_id, "resourceId": "portrait"},
        parse_control=parse,
    )
    visual = HostVisualService(binding_provider=lambda: ("character", binding),
        emit_callback=lambda *args: events.append(args), is_idle=lambda: True,
        select_callback=lambda *args: None,
        commit_scope=lambda owner, commit: manager.commit_plugin_scope(*owner, commit))
    manager.install_host_service("sakura.host.visual", visual, exports=("apply",))

    _invoke_after_scope_clear(manager, process, "sakura.host.visual", "apply", {
        "target": {"characterId": "character", "bindingId": binding_id, "resourceId": "portrait"},
        "control": {"version": 1, "resourceId": "portrait", "payload": {"portrait": "smile"}},
    }, replacement, monkeypatch)

    assert parsed == []
    assert events == []
    assert visual._receipts == {}
