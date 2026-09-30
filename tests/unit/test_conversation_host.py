from contextlib import contextmanager
from types import SimpleNamespace
import threading

import pytest

from app.core_host.conversation_host import ConversationHostError, ConversationHostService
from app.core_host.real_chat import RealChatBoundary, RealChatRejection
from app.plugin_sdk.sakura_assistant_contract import ChatReply, ChatSegment
from app.plugins.host_services import HOST_CALLER, HOST_CALLER_SCOPE
from app.storage.timeline import TimelineKind, TimelineStore


@contextmanager
def caller(plugin="input.plugin", scope="original"):
    owner_token = HOST_CALLER.set(plugin)
    scope_token = HOST_CALLER_SCOPE.set(scope)
    try:
        yield
    finally:
        HOST_CALLER_SCOPE.reset(scope_token)
        HOST_CALLER.reset(owner_token)


def setup_host(tmp_path, *, gate_at=None, reply=None):
    entered, resume = threading.Event(), threading.Event()
    requests, events, facts, speech = [], [], [], []

    def run_turn(request, cancel_checker):
        requests.append(request)
        if gate_at == "inference":
            entered.set()
            assert resume.wait(3)
        cancel_checker()
        return SimpleNamespace(reply=reply or ChatReply([ChatSegment(text="spoken reply")]), actions=[])

    def release(*args, **kwargs):
        if gate_at == "release":
            entered.set()
            assert resume.wait(3)

    session = SimpleNamespace(character=SimpleNamespace(id="sakura"), visual_binding=None,
        descriptor=lambda: {"character": {"id": "sakura"}},
        assistant=SimpleNamespace(run_turn=run_turn, commit_result=lambda commit: commit(), release=release))
    timeline = TimelineStore(tmp_path / "timeline.sqlite3")
    timeline.initialize()
    boundary = RealChatBoundary("generation", "credential", tmp_path,
        session_provider=lambda: session, timeline_store=timeline,
        plugin_application_provider=lambda: SimpleNamespace(emit_event=lambda *args: facts.append(args)),
        segment_authorizer=lambda **kwargs: speech.append(kwargs))
    host = ConversationHostService(chat_boundary_provider=lambda: boundary,
        artifact_resolver=lambda _id: pytest.fail("no artifact expected"),
        artifact_releaser=lambda _id: True, emit_callback=lambda *args: events.append(args))
    return SimpleNamespace(host=host, boundary=boundary, timeline=timeline, session=session,
        requests=requests, events=events, facts=facts, speech=speech, entered=entered, resume=resume)


@pytest.mark.parametrize("legacy_mobile", [False, True])
def test_conversation_keeps_human_text_and_publishes_normal_tts_flow(tmp_path, legacy_mobile):
    turn = setup_host(tmp_path)
    try:
        with caller():
            if legacy_mobile:
                from app.core_host.mobile_host import MobileHostService
                mobile = MobileHostService(tmp_path, session_provider=lambda: turn.session,
                    conversation=turn.host, characters=object())
                accepted = mobile.begin("input.plugin", "sakura", "我的原始问题")
            else:
                accepted = turn.host.begin("sakura", "我的原始问题")
            job = turn.host._jobs[accepted["jobId"]]
            assert job.done.wait(3)
            result = mobile.poll("input.plugin", accepted["jobId"]) if legacy_mobile else turn.host.poll(accepted["jobId"])
        assert result["result"]["reply_raw"] == "spoken reply"
        assert turn.requests[0]["message"] == "我的原始问题"
        assert [entry.kind for entry in turn.timeline.read_all("sakura")] == [TimelineKind.HUMAN, TimelineKind.ASSISTANT]
        assert turn.timeline.read_all("sakura")[0].payload["text"] == "我的原始问题"
        assert [name for name, _ in turn.events] == ["host.chat.started", "host.chat.completed"]
        assert all(payload["presentation"] == "interactive" for _, payload in turn.events)
        assert turn.events[-1][1]["reply"]["segments"][0]["text"] == "spoken reply"
        assert turn.speech[0]["operation_id"] == job.operation_id
        assert [name for name, _ in turn.facts].count("sakura.host.chat.completed") == 1
        assert [name for name, _ in turn.facts].count("message.user") == 1
    finally:
        turn.host.close()
        turn.boundary.close()


@pytest.mark.parametrize("gate_at", ["inference", "release"])
def test_revocation_keeps_realchat_terminal_after_started(tmp_path, gate_at):
    turn = setup_host(tmp_path, gate_at=gate_at)
    try:
        with caller():
            accepted = turn.host.begin("sakura", "hello")
            job = turn.host._jobs[accepted["jobId"]]
        assert turn.entered.wait(3)
        turn.host.revoke_scope("input.plugin")
        turn.resume.set()
        assert job.done.wait(3)
        terminal = "completed" if gate_at == "release" else "cancelled"
        assert [name for name, _ in turn.events] == ["host.chat.started", "host.chat." + terminal]
        assert len(turn.timeline.read_all("sakura")) == (2 if terminal == "completed" else 1)
        with caller(scope="replacement"), pytest.raises(ConversationHostError, match="CHAT_JOB_NOT_FOUND"):
            turn.host.poll(accepted["jobId"])
    finally:
        turn.resume.set()
        turn.host.close()
        turn.boundary.close()


def test_reloaded_scope_cannot_poll_or_cancel_previous_job(tmp_path, monkeypatch):
    turn = setup_host(tmp_path)
    workers = []
    monkeypatch.setattr("app.core_host.conversation_host.threading.Thread.start", lambda worker: workers.append(worker))
    try:
        with caller():
            accepted = turn.host.begin("sakura", "hello")
        with caller(scope="replacement"):
            for action in (turn.host.poll, turn.host.cancel):
                with pytest.raises(ConversationHostError, match="CHAT_JOB_NOT_FOUND"):
                    action(accepted["jobId"])
        turn.host.invalidate_session()
        workers[0].run()
        assert turn.events == []
        assert turn.requests == []
        assert turn.boundary.current_host_state()["idle"]
    finally:
        turn.host.close()
        turn.boundary.close()


def test_role_session_changed_between_check_and_reservation_is_rejected(tmp_path, monkeypatch):
    turn = setup_host(tmp_path)
    reserve = turn.boundary.reserve_host_message

    def reserve_after_switch(*args, **kwargs):
        turn.boundary._screen_session_id = "new-session-same-character"
        return reserve(*args, **kwargs)

    monkeypatch.setattr(turn.boundary, "reserve_host_message", reserve_after_switch)
    try:
        with caller(), pytest.raises(RealChatRejection, match="CHAT_SESSION_STALE"):
            turn.host.begin("sakura", "hello")
        assert turn.events == []
        assert turn.boundary.current_host_state()["idle"]
    finally:
        turn.host.close()
        turn.boundary.close()


def test_worker_failure_preserves_cause_and_diagnostic(tmp_path, monkeypatch):
    turn = setup_host(tmp_path)
    failure = OSError("fixture unreadable input")
    diagnostics = []

    def fail(*args, **kwargs):
        raise failure

    monkeypatch.setattr(turn.boundary, "run_reserved_host_message", fail)
    monkeypatch.setattr("app.core_host.conversation_host.log_event", lambda *args, **kwargs: diagnostics.append(args[2]))
    try:
        with caller():
            accepted = turn.host.begin("sakura", "hello")
            assert turn.host._jobs[accepted["jobId"]].done.wait(3)
            with pytest.raises(ConversationHostError, match="CHAT_FAILED") as caught:
                turn.host.poll(accepted["jobId"])
        assert caught.value.__cause__ is failure
        assert diagnostics[0]["stage"] == "conversation_chat"
        assert "fixture unreadable input" in diagnostics[0]["diagnostic"]
    finally:
        turn.boundary.abandon_host_message(accepted["operationId"])
        turn.host.close()
        turn.boundary.close()



def test_foreign_image_descriptor_is_not_read_or_released(tmp_path, monkeypatch):
    turn = setup_host(tmp_path)
    released = []
    image = tmp_path / "private.png"
    image.write_bytes(b"private")
    monkeypatch.setattr(turn.host, "_artifact_resolver", lambda _id: SimpleNamespace(
        plugin_id="other.plugin", path=image, media_type="image/png", byte_length=7))
    monkeypatch.setattr(turn.host, "_artifact_releaser", released.append)
    try:
        with caller(), pytest.raises(ConversationHostError, match="CHAT_IMAGE_INVALID"):
            turn.host.begin("sakura", "hello", {"artifactId": "foreign", "mediaType": "image/png", "byteLength": 7})
        assert released == []
        assert turn.boundary.current_host_state()["idle"]
        assert turn.requests == []
    finally:
        turn.host.close()
        turn.boundary.close()


def test_conversation_returns_complete_segments_with_original_indexes(tmp_path):
    turn = setup_host(tmp_path, reply=ChatReply([
        ChatSegment(text="silent", translation="静音", suppress_tts=True),
        ChatSegment(text="spoken", translation="朗读", portrait="smile"),
    ]))
    try:
        with caller():
            accepted = turn.host.begin("sakura", "hello")
            assert turn.host._jobs[accepted["jobId"]].done.wait(3)
            result = turn.host.poll(accepted["jobId"])["result"]
        published = turn.events[-1][1]["reply"]
        assert result["historyEntryId"] == published["historyEntryId"]
        for index, (segment, original) in enumerate(zip(result["segments"], published["segments"], strict=True)):
            assert segment == {**original, "segmentIndex": index,
                "content": original["translation"], "raw_content": original["text"]}
        assert result["segments"][0]["suppressTts"] is True
        assert result["segments"][1]["suppressTts"] is False
    finally:
        turn.host.close()
        turn.boundary.close()


@pytest.mark.parametrize("rejection", ["unready", "character", "payload"])
def test_rejected_conversation_releases_its_committed_image(tmp_path, monkeypatch, rejection):
    turn = setup_host(tmp_path)
    released = []
    image = tmp_path / "image.png"
    image.write_bytes(b"owned")
    monkeypatch.setattr(turn.host, "_artifact_resolver", lambda _id: SimpleNamespace(
        plugin_id="input.plugin", path=image, media_type="image/png", byte_length=5))
    monkeypatch.setattr(turn.host, "_artifact_releaser", released.append)
    if rejection == "unready":
        monkeypatch.setattr(turn.host, "_boundary_provider", lambda: None)
    character = "other" if rejection == "character" else "sakura"
    text = None if rejection == "payload" else "hello"
    code = {"unready": "ASSISTANT_NOT_READY", "character": "CHAT_CHARACTER_NOT_CURRENT",
            "payload": "INVALID_CHAT_PAYLOAD"}[rejection]
    try:
        with caller(), pytest.raises(ConversationHostError, match=code):
            turn.host.begin(character, text, {"artifactId": "owned", "mediaType": "image/png", "byteLength": 5})
        assert released == ["owned"]
        assert turn.boundary.current_host_state()["idle"]
    finally:
        turn.host.close()
        turn.boundary.close()


@pytest.mark.parametrize("descriptor", ["", "data:image/png;base64,aGVsbG8="])
def test_conversation_rejects_string_image_descriptors(tmp_path, descriptor):
    turn = setup_host(tmp_path)
    try:
        with caller(), pytest.raises(ConversationHostError, match="CHAT_IMAGE_INVALID"):
            turn.host.begin("sakura", "hello", descriptor)
        assert turn.requests == []
    finally:
        turn.host.close()
        turn.boundary.close()
