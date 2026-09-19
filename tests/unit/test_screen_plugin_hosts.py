import threading
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from app.core_host.chat_host import ChatHost
from app.core_host.real_chat import RealChatBoundary, RealChatRejection
from app.core_host.screen_host import ScreenHost, ScreenHostError
from app.plugins.host_services import HOST_CALLER, HOST_CALLER_SCOPE
from app.plugin_sdk.sakura_assistant_contract import ChatReply, ChatSegment
from app.storage.timeline import TimelineStore, TimelineKind
from plugins.builtin.sakura_screen_awareness.plugin import ScreenAwarenessRuntime


@contextmanager
def caller(owner="plugin-a", scope="scope-a"):
    first, second = HOST_CALLER.set(owner), HOST_CALLER_SCOPE.set(scope)
    try:
        yield
    finally:
        HOST_CALLER_SCOPE.reset(second)
        HOST_CALLER.reset(first)


def observation():
    return SimpleNamespace(data_url="data:image/jpeg;base64,AA==", width=1, height=1,
                           captured_at="2026-09-19T00:00:00Z", screen_name="test")


def test_capture_ownership_and_late_scope_result_is_released():
    entered = threading.Event()
    requests, reads, errors = [], [], []
    def emit(name, payload):
        requests.append((name, payload))
        entered.set()
    screen = ScreenHost("generation", session_provider=lambda: "session", emit_callback=emit,
                        resource_consumer=lambda resource, **kw: reads.append(resource) or observation())
    def capture():
        with caller():
            try:
                screen.capture({"operationId": "capture-1", "sessionId": "session", "resolution": "720p"})
            except ScreenHostError as error:
                errors.append(error.code)
    worker = threading.Thread(target=capture)
    worker.start()
    assert entered.wait(3)
    screen.revoke_scope("plugin-a")
    worker.join(3)
    assert not worker.is_alive()
    assert errors == ["SCREEN_CAPTURE_CANCELLED"]
    assert screen.complete({**requests[0][1], "resource": {"token": "late"}}) == {"accepted": False}
    assert reads == [{"token": "late"}]
    assert not screen._resources


def captured_screen(session_provider=lambda: "session"):
    screen = None
    def emit(name, payload):
        screen.complete({**payload, "resource": {}})
    screen = ScreenHost("generation", session_provider=session_provider, emit_callback=emit,
                        resource_consumer=lambda *a, **kw: observation())
    return screen


def test_resource_handles_are_scoped_and_consumed_once():
    screen = captured_screen()
    with caller():
        image = screen.capture({"operationId": "capture-1", "sessionId": "session", "resolution": "720p"})
    with caller(scope="replacement"):
        with pytest.raises(ScreenHostError, match="UNAUTHORIZED"):
            screen.release(image["resourceId"])
    with pytest.raises(ScreenHostError, match="UNAUTHORIZED"):
        screen.take_resources(("plugin-b", "scope-b"), "session", [image["resourceId"]])
    assert len(screen.take_resources(("plugin-a", "scope-a"), "session", [image["resourceId"]])) == 1
    with pytest.raises(ScreenHostError, match="UNAUTHORIZED"):
        screen.take_resources(("plugin-a", "scope-a"), "session", [image["resourceId"]])


class Session(SimpleNamespace):
    visual_binding = None
    def descriptor(self):
        return {"character": {"id": self.character.id}}


class Assistant:
    def __init__(self):
        self.entered, self.release_gate = threading.Event(), threading.Event()
        self.requests = []
    def run_turn(self, request, cancel_checker):
        self.requests.append(request)
        self.entered.set()
        assert self.release_gate.wait(3)
        cancel_checker()
        return SimpleNamespace(reply=ChatReply(segments=[ChatSegment(text="reply", tone="neutral", portrait="neutral")]), actions=[], visual_observation=None)
    def commit_result(self, callback):
        return callback()
    def release(self, *args, **kwargs):
        pass


def test_plugin_turn_uses_real_chat_admission_timeline_and_output(tmp_path):
    assistant = Assistant()
    session = Session(character=SimpleNamespace(id="character"), assistant=assistant)
    timeline = TimelineStore(tmp_path / "timeline.sqlite3")
    timeline.initialize()
    boundary = RealChatBoundary("generation", "credential", tmp_path, session_provider=lambda: session, timeline_store=timeline)
    screen = captured_screen(lambda: boundary.current_host_state()["sessionId"])
    events, terminal = [], threading.Event()
    def emit(name, payload):
        events.append((name, payload))
        if name != "host.chat.started":
            terminal.set()
    host = ChatHost(boundary_provider=lambda: boundary, screen_host=screen, emit_callback=emit)
    session_id = host.current()["sessionId"]
    host.set_ui_state({"sessionId": session_id, "idle": True, "activityRevision": 1})
    with caller():
        accepted = host.submit({"sessionId": session_id, "message": "plugin input", "resources": []})
        assert accepted["accepted"]
        assert assistant.entered.wait(3)
        assert host.submit({"sessionId": session_id, "message": "second", "resources": []}) == {"accepted": False, "reasonCode": "CHAT_BUSY"}
    with caller(owner="plugin-b"):
        with pytest.raises(RealChatRejection, match="UNAUTHORIZED"):
            host.cancel(accepted["operationId"])
    assistant.release_gate.set()
    assert terminal.wait(3)
    assert [event[0] for event in events] == ["host.chat.started", "host.chat.completed"]
    assert events[0][1]["sourcePluginId"] == "plugin-a"
    assert events[0][1]["sessionId"] == session_id
    entries, _ = timeline.read_recent("character", 10)
    assert [entry.kind for entry in entries] == [TimelineKind.OBSERVATION, TimelineKind.ASSISTANT]
    assert entries[0].origin == "host"
    assert entries[0].payload["sourcePluginId"] == "plugin-a"
    assert "plugin input" not in str(entries[0].payload)
    assert assistant.requests[0]["message"] == "plugin input"
    assert assistant.requests[0]["event"] is None


def test_plugin_disabled_settings_and_close_stop_capture():
    now, captured = [0.0], []
    config = SimpleNamespace(get=lambda: {"enabled": False, "checkIntervalMinutes": 1})
    runtime = ScreenAwarenessRuntime(SimpleNamespace(capture=lambda value: captured.append(value)),
        SimpleNamespace(current=lambda: {"sessionId": "session", "idle": True, "activityRevision": 0}), config, clock=lambda: now[0])
    runtime.tick()
    now[0] = 1000
    runtime.tick()
    runtime.close()
    runtime.tick()
    assert captured == []


def test_plugin_settings_change_during_capture_discards_late_image():
    now = [0.0]
    entered, finish = threading.Event(), threading.Event()
    released, sent = [], []
    def capture(request):
        entered.set()
        assert finish.wait(3)
        return {"resourceId": "late-image"}
    runtime = ScreenAwarenessRuntime(SimpleNamespace(capture=capture, release=released.append),
        SimpleNamespace(current=lambda: {"sessionId": "session", "idle": True, "activityRevision": 0}, submit=sent.append),
        SimpleNamespace(get=lambda: {"checkIntervalMinutes": 1}), clock=lambda: now[0])
    runtime.tick()
    now[0] = 60
    worker = threading.Thread(target=runtime.tick)
    worker.start()
    assert entered.wait(3)
    runtime.apply_settings({"enabled": False})
    finish.set()
    worker.join(3)
    assert not worker.is_alive()
    assert released == ["late-image"] and sent == []


@pytest.mark.parametrize("disable_during_submit", [False, True])
def test_plugin_disabling_settings_cancels_accepted_and_inflight_submission(disable_during_submit):
    now, cancelled = [0.0], []
    entered, finish = threading.Event(), threading.Event()
    def submit(request):
        entered.set()
        assert finish.wait(3)
        return {"accepted": True, "operationId": "plugin-operation"}
    runtime = ScreenAwarenessRuntime(
        SimpleNamespace(capture=lambda _: {"resourceId": "image"}, release=lambda _: None),
        SimpleNamespace(current=lambda: {"sessionId": "session", "idle": True, "activityRevision": 0},
                        submit=submit, cancel=lambda operation: cancelled.append(operation)),
        SimpleNamespace(get=lambda: {"checkIntervalMinutes": 1, "cooldownMinutes": 1}), clock=lambda: now[0])
    runtime.tick()
    now[0] = 60
    runtime.tick()
    now[0] = 120
    worker = threading.Thread(target=runtime.tick)
    worker.start()
    assert entered.wait(3)
    if disable_during_submit:
        runtime.apply_settings({"enabled": False})
    finish.set()
    worker.join(3)
    assert not worker.is_alive()
    if not disable_during_submit:
        runtime.apply_settings({"enabled": False})
    assert cancelled == ["plugin-operation"]
    runtime.close()


def test_manual_interaction_clears_plugin_batch_even_when_cancelled_without_history(tmp_path):
    session = Session(character=SimpleNamespace(id="character"), assistant=Assistant())
    boundary = RealChatBoundary("generation", "credential", tmp_path, session_provider=lambda: session)
    now, released, sent = [0.0], [], []
    runtime = ScreenAwarenessRuntime(
        SimpleNamespace(capture=lambda _: {"resourceId": "old-image"}, release=released.append),
        SimpleNamespace(current=lambda: {**boundary.current_host_state(), "activityRevision": 0}, submit=sent.append),
        SimpleNamespace(get=lambda: {"checkIntervalMinutes": 1, "cooldownMinutes": 1}), clock=lambda: now[0])
    runtime.tick()
    now[0] = 60
    runtime.tick()
    assert released == []
    assert boundary.current_host_state()["interactionRevision"] == 0
    boundary.reserve_send({"id": "manual-operation", "generationId": "generation", "generationCredential": "credential",
                           "kind": "request", "name": "chat.send",
                           "payload": {"operationId": "manual-operation", "message": "manual input"}})
    boundary.abandon_host_message("manual-operation")
    assert boundary.current_host_state()["interactionRevision"] == 1
    now[0] = 120
    runtime.tick()
    assert released == ["old-image"]
    assert sent == []
    runtime.close()


def test_idle_preference_commit_checks_local_activity_without_reentering_boundary():
    state = {"sessionId": "session", "characterId": "character", "idle": True, "interactionRevision": 0}
    read = [lambda: dict(state)]
    host = ChatHost(boundary_provider=lambda: SimpleNamespace(current_host_state=lambda: read[0]()),
                    screen_host=None, emit_callback=lambda *_: None)
    host.set_ui_state({"sessionId": "session", "idle": True, "activityRevision": 1})
    expected = host.current()
    writes = []
    host.commit_idle(expected, lambda: writes.append("initial"))
    host.set_ui_state({"sessionId": "session", "idle": True, "activityRevision": 2})
    with pytest.raises(RealChatRejection, match="CHAT_BUSY"):
        host.commit_idle(expected, lambda: writes.append("stale"))
    expected = host.current()
    # RealChat has reserved an idle update and its reader must not be reentered.
    read[0] = lambda: pytest.fail("idle commit cannot read or acquire the chat boundary")
    host.commit_idle(expected, lambda: writes.append("current"))
    host.invalidate_session()
    with pytest.raises(RealChatRejection, match="CHAT_BUSY"):
        host.commit_idle(expected, lambda: writes.append("detached"))
    assert writes == ["initial", "current"]


def test_scope_revocation_cancels_active_plugin_turn_without_late_output(tmp_path):
    assistant = Assistant()
    session = Session(character=SimpleNamespace(id="character"), assistant=assistant)
    timeline = TimelineStore(tmp_path / "timeline.sqlite3")
    timeline.initialize()
    boundary = RealChatBoundary("generation", "credential", tmp_path, session_provider=lambda: session, timeline_store=timeline)
    screen = captured_screen(lambda: boundary.current_host_state()["sessionId"])
    events = []
    host = ChatHost(boundary_provider=lambda: boundary, screen_host=screen,
                    emit_callback=lambda name, payload: events.append(name))
    session_id = host.current()["sessionId"]
    host.set_ui_state({"sessionId": session_id, "idle": True, "activityRevision": 0})
    with caller():
        assert host.submit({"sessionId": session_id, "message": "proactive", "resources": []})["accepted"]
    assert assistant.entered.wait(3)
    host.revoke_scope("plugin-a")
    assistant.release_gate.set()
    boundary.close()
    assert events == ["host.chat.started", "host.chat.cancelled"]
    assert all(entry.kind is not TimelineKind.ASSISTANT for entry in timeline.read_all("character"))


def test_capture_session_invalidation_does_not_wait_for_boundary_reader():
    entered, finish, invalidated = threading.Event(), threading.Event(), threading.Event()
    errors, emitted = [], []
    def session():
        entered.set()
        assert finish.wait(3)
        return "session"
    screen = ScreenHost("generation", session_provider=session, emit_callback=lambda *args: emitted.append(args))
    def capture():
        with caller():
            try:
                screen.capture({"operationId": "capture-1", "sessionId": "session", "resolution": "720p"})
            except ScreenHostError as error:
                errors.append(error.code)
    worker = threading.Thread(target=capture)
    worker.start()
    assert entered.wait(3)
    def invalidate():
        screen.invalidate_session()
        invalidated.set()
    invalidator = threading.Thread(target=invalidate)
    invalidator.start()
    try:
        assert invalidated.wait(1), "host lock must not be held by the session reader"
    finally:
        finish.set()
        worker.join(3)
        invalidator.join(3)
    assert errors == ["SCREEN_SESSION_STALE"] and emitted == []


def test_scope_revocation_during_admission_abandons_reservation(tmp_path):
    entered, finish = threading.Event(), threading.Event()
    session = Session(character=SimpleNamespace(id="character"), assistant=Assistant())
    boundary = RealChatBoundary("generation", "credential", tmp_path, session_provider=lambda: session, timeline_store=object())
    original = boundary.reserve_plugin_message
    def reserve(*args):
        entered.set()
        assert finish.wait(3)
        return original(*args)
    boundary.reserve_plugin_message = reserve
    screen = captured_screen(lambda: boundary.current_host_state()["sessionId"])
    host = ChatHost(boundary_provider=lambda: boundary, screen_host=screen, emit_callback=lambda *args: None)
    session_id = host.current()["sessionId"]
    host.set_ui_state({"sessionId": session_id, "idle": True, "activityRevision": 0})
    results = []
    def submit():
        with caller():
            results.append(host.submit({"sessionId": session_id, "message": "test", "resources": []}))
    worker = threading.Thread(target=submit)
    worker.start()
    assert entered.wait(3)
    host.revoke_scope("plugin-a")
    finish.set()
    worker.join(3)
    assert not worker.is_alive()
    assert results == [{"accepted": False, "reasonCode": "CHAT_ADMISSION_EXPIRED"}]
    assert boundary.current_host_state()["idle"]
