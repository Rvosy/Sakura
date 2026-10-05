import threading
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from app.core_host.chat_host import ChatHost
from app.core.runtime_log import log_message, register_external_sink, unregister_external_sink
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


def test_capture_failure_keeps_native_diagnostic_and_stable_code():
    def emit(name, payload):
        screen.complete({**payload, "error": {"code": "SCREEN_CAPTURE_FAILED",
                         "diagnostic": "CreateWindow: win32=5 C:\\runtime api_key=private-key"}})
    screen = ScreenHost("generation", session_provider=lambda: "session", emit_callback=emit)
    with caller(), pytest.raises(ScreenHostError) as caught:
        screen.capture({"operationId": "capture-failure", "sessionId": "session", "resolution": "720p"})
    assert caught.value.code == "SCREEN_CAPTURE_FAILED"
    assert "CreateWindow: win32=5 C:\\runtime" in str(caught.value)
    assert "private-key" not in str(caught.value)


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
    session = Session(character=SimpleNamespace(id="character", display_name="测试角色"), assistant=assistant)
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
        from plugins.builtin.sakura_screen_awareness.prompts import PROACTIVE_PROMPT
        assert request["message"] == PROACTIVE_PROMPT
        assert request["resources"]
        entered.set()
        assert finish.wait(3)
        return {"accepted": True, "operationId": "plugin-operation"}
    runtime = ScreenAwarenessRuntime(
        SimpleNamespace(capture=lambda _: {"resourceId": "image", "capturedAt": "2026-10-05T00:00:00Z"}, release=lambda _: None),
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
    session = Session(character=SimpleNamespace(id="character", display_name="测试角色"), assistant=Assistant())
    boundary = RealChatBoundary("generation", "credential", tmp_path, session_provider=lambda: session)
    now, released, sent = [0.0], [], []
    runtime = ScreenAwarenessRuntime(
        SimpleNamespace(capture=lambda _: {"resourceId": "old-image", "capturedAt": "2026-10-05T00:00:00Z"}, release=released.append),
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
    session = Session(character=SimpleNamespace(id="character", display_name="测试角色"), assistant=assistant)
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
    session = Session(character=SimpleNamespace(id="character", display_name="测试角色"), assistant=Assistant())
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


@pytest.fixture
def awareness_logs():
    now, records, captured, released, submitted = [0.0], [], [], [], []
    facts = {"sessionId": "session-a", "characterId": "role-a", "characterName": "角色甲",
             "idle": True, "activityRevision": 0, "interactionRevision": 0}

    def capture(_request):
        resource = {"resourceId": f"private-image-{len(captured) + 1}",
                    "capturedAt": f"2026-10-05T00:{int(now[0] // 60):02}:00Z"}
        captured.append(resource)
        return resource

    def submit(request):
        submitted.append(request)
        return {"accepted": True, "operationId": "observation-operation"}

    def sink(record):
        records.append(record)
        return True

    logger = SimpleNamespace(
        info=lambda message, fields=None: log_message("info", message, fields=fields,
                                                     component="plugin", plugin_id="sakura.screen_awareness"),
        warning=lambda message, fields=None: log_message("warning", message, fields=fields,
                                                        component="plugin", plugin_id="sakura.screen_awareness"),
    )
    screen = SimpleNamespace(capture=capture, release=released.append)
    chat = SimpleNamespace(current=lambda: dict(facts), submit=submit, cancel=lambda _: {"accepted": False})
    runtime = ScreenAwarenessRuntime(screen, chat,
        SimpleNamespace(get=lambda: {"checkIntervalMinutes": 1, "cooldownMinutes": 2, "batchLimit": 3}),
        clock=lambda: now[0], logger=logger)
    register_external_sink(sink)
    try:
        runtime.tick()
        yield SimpleNamespace(runtime=runtime, now=now, facts=facts, records=records, screen=screen, chat=chat,
                              captured=captured, released=released, submitted=submitted)
    finally:
        runtime.close()
        unregister_external_sink(sink)


def test_screen_awareness_logs_retained_count_without_polling_noise(awareness_logs):
    run = awareness_logs
    for timestamp in (10, 60, 70, 120, 130, 180):
        run.now[0] = timestamp
        run.runtime.tick()
    assert [record.attributes["event"] for record in run.records] == ["screen.awareness.captured"] * 3
    assert [record.attributes["screen_count"] for record in run.records] == [1, 2, 3]
    assert all(record.attributes["screen_limit"] == 3 for record in run.records)
    for count, record in enumerate(run.records, 1):
        assert "角色甲" in record.message and f"{count}/3" in record.message
    assert run.submitted[0]["resources"] == [item["resourceId"] for item in run.captured]
    assert "private-image" not in str(run.records)


def test_screen_awareness_logs_replacement_and_can_send_before_full(awareness_logs):
    run = awareness_logs
    run.runtime.apply_settings({"checkIntervalMinutes": 1, "cooldownMinutes": 4, "batchLimit": 3})
    run.runtime.tick()
    for timestamp in (60, 120, 180, 240, 300):
        run.now[0] = timestamp
        run.runtime.tick()
    assert [record.attributes["screen_count"] for record in run.records] == [1, 2, 3, 3, 3]
    assert all(record.attributes.get("screen_note") for record in run.records[3:])
    assert run.records[-2].attributes["screen_captured_at"] != run.records[-1].attributes["screen_captured_at"]
    assert run.submitted[0]["resources"] == [item["resourceId"] for item in run.captured[-3:]]

    run.runtime.apply_settings({"checkIntervalMinutes": 2, "cooldownMinutes": 1, "batchLimit": 3})
    run.runtime.tick()
    run.now[0] = 420
    run.runtime.tick()
    run.now[0] = 480
    run.runtime.tick()
    assert len(run.submitted[-1]["resources"]) == 1
    assert run.records[-1].attributes["screen_count"] == 1


@pytest.mark.parametrize("reason", ["CHAT_BUSY", "CHAT_SESSION_STALE", "CHAT_ADMISSION_EXPIRED"])
def test_screen_awareness_logs_rejected_submission_and_clears_batch(awareness_logs, reason):
    run = awareness_logs
    run.chat.submit = lambda _: {"accepted": False, "reasonCode": reason}
    for timestamp in (60, 120, 180):
        run.now[0] = timestamp
        run.runtime.tick()
    rejected = run.records[-1]
    assert rejected.attributes["event"] == "screen.awareness.skipped"
    assert rejected.attributes["reason_code"] == reason and rejected.severity == "info"
    assert rejected.attributes["screen_count"] == 3
    assert len(run.released) == 3
    run.runtime.tick()
    assert run.records[-1] is rejected


@pytest.mark.parametrize("change", ["session", "interaction", "settings", "disabled"])
def test_screen_awareness_logs_why_a_collected_batch_is_cleared(awareness_logs, change):
    run = awareness_logs
    run.now[0] = 60
    run.runtime.tick()
    if change == "session":
        run.facts.update(sessionId="session-b", characterId="role-b", characterName="角色乙")
    elif change == "interaction":
        run.facts["interactionRevision"] += 1
    else:
        run.runtime.apply_settings({"enabled": change != "disabled", "checkIntervalMinutes": 1})
    run.runtime.tick()
    assert len(run.records) == 2
    assert run.records[-1].attributes["screen_cleared_count"] == 1
    assert run.records[-1].attributes["event"] == ("screen.awareness.disabled" if change == "disabled" else "screen.awareness.cleared")
    assert run.released == [run.captured[0]["resourceId"]]
    if change == "session":
        run.now[0] = 120
        run.runtime.tick()
        assert "角色乙" in run.records[-1].message and "1/3" in run.records[-1].message


def test_screen_awareness_late_capture_never_logs_success(awareness_logs):
    run = awareness_logs
    entered, resume = threading.Event(), threading.Event()
    capture = run.screen.capture

    def delayed_capture(request):
        entered.set()
        assert resume.wait(3)
        return capture(request)

    run.screen.capture = delayed_capture
    run.now[0] = 60
    worker = threading.Thread(target=run.runtime.tick)
    worker.start()
    try:
        assert entered.wait(3)
        run.runtime.apply_settings({"enabled": False})
    finally:
        resume.set()
        worker.join(3)
    assert not worker.is_alive()
    assert [record.attributes["event"] for record in run.records] == ["screen.awareness.disabled"]
    assert run.released == [run.captured[0]["resourceId"]]


@pytest.mark.parametrize("stage", ["capture", "submit"])
def test_screen_awareness_failure_preserves_diagnostic_without_success(awareness_logs, stage):
    run = awareness_logs

    def fail(_request):
        raise OSError("permission denied api_key=private-screen-secret")

    if stage == "capture":
        run.screen.capture = fail
    else:
        run.chat.submit = fail
    for timestamp in (60,) if stage == "capture" else (60, 120, 180):
        run.now[0] = timestamp
        run.runtime.tick()
    failure = run.records[-1]
    assert failure.attributes["event"] == f"screen.awareness.{stage}_failed"
    assert failure.severity == "warning"
    assert "permission denied" in failure.attributes["diagnostic"]
    assert "private-screen-secret" not in str(run.records)
    assert "角色甲" in failure.message
    assert run.released == [item["resourceId"] for item in run.captured]
