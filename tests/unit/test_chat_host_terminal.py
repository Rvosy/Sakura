"""Source scope revocation must preserve RealChat's committed terminal."""

import io
import json
import threading
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from app.core_host.chat_host import ChatHost
from app.core_host.real_chat import RealChatBoundary
from app.core_host.runtime_logging import CORE_BRIDGE_PREFIX, install_runtime_logging
from app.plugin_sdk.sakura_assistant_contract import ChatReply, ChatSegment
from app.plugins.host_services import HOST_CALLER, HOST_CALLER_SCOPE, HOST_CALLER_LOG_METADATA
from app.storage.timeline import TimelineKind, TimelineStore
from sakura_assistant.agent.trace import AgentTraceRecorder


@contextmanager
def caller():
    owner = HOST_CALLER.set("source.plugin")
    scope = HOST_CALLER_SCOPE.set("source.scope")
    metadata = HOST_CALLER_LOG_METADATA.set(("屏幕观察来源", ()))
    try:
        yield
    finally:
        HOST_CALLER_LOG_METADATA.reset(metadata)
        HOST_CALLER_SCOPE.reset(scope)
        HOST_CALLER.reset(owner)


class RecordingTrace(AgentTraceRecorder):
    def __init__(self, path):
        self.documents = []
        super().__init__(path)

    def _commit_documents(self, documents):
        self.documents.extend(json.loads(json.dumps(documents)))
        super()._commit_documents(documents)


class Assistant:
    def __init__(self, trace, gate_at):
        self.trace = trace
        self.gate_at = gate_at
        self.entered = threading.Event()
        self.resume = threading.Event()
        self.release_statuses = []
        self.requests = []
        self.reply_text = "reply"
        self.failure = None

    def run_turn(self, request, cancel_checker):
        self.requests.append(request)
        with self.trace.operation(request["operationId"]):
            call = self.trace.start_model_call(model="fixture", payload={"messages": []}, prompt_provenance=[])
            assert call is not None
            if self.gate_at == "inference":
                self.entered.set()
                assert self.resume.wait(3)
            cancel_checker()
            if self.failure is not None:
                raise self.failure
            self.trace.record_model_reply(call, raw_message={"role": "assistant", "content": "reply"})
            return SimpleNamespace(reply=ChatReply([ChatSegment(text=self.reply_text)]), actions=[])

    def commit_result(self, commit):
        return commit()

    def release(self, operation_id, *, status):
        self.release_statuses.append(status)
        if self.gate_at == "release":
            self.entered.set()
            assert self.resume.wait(3)
        assert self.trace.finish_operation(operation_id, status=status)


def setup_turn(tmp_path, monkeypatch, gate_at, *, started_gate=None, with_screen=False):
    trace = RecordingTrace(tmp_path / "trace")
    assistant = Assistant(trace, gate_at)
    session = SimpleNamespace(character=SimpleNamespace(id="character", display_name="测试角色"), assistant=assistant,
                              visual_binding=None, descriptor=lambda: {"character": {"id": "character"}})
    timeline = TimelineStore(tmp_path / "timeline.sqlite3")
    timeline.initialize()
    boundary = RealChatBoundary("generation", "credential", tmp_path,
        session_provider=lambda: session, timeline_store=timeline)
    events, cancellations, outcomes, errors = [], [], [], []
    finished = threading.Event()
    original_run = boundary.run_reserved_plugin_message
    original_cancel = boundary.cancel_host_message

    def run(operation_id, publish):
        try:
            if gate_at == "before_start":
                assistant.entered.set()
                assert assistant.resume.wait(3)
            outcomes.append(original_run(operation_id, publish))
        except BaseException as error:
            errors.append(error)
        finally:
            finished.set()

    def cancel(operation_id):
        accepted = original_cancel(operation_id)
        cancellations.append(accepted)
        return accepted

    monkeypatch.setattr(boundary, "run_reserved_plugin_message", run)
    monkeypatch.setattr(boundary, "cancel_host_message", cancel)
    observations = (SimpleNamespace(data_url="data:image/jpeg;base64,PRIVATE_SCREEN_PIXELS", width=1, height=1,
                                   captured_at="2026-10-05T00:00:00Z", screen_name="private-screen"),) if with_screen else ()
    screen = SimpleNamespace(take_resources=lambda *_args: observations)

    def emit(name, payload):
        events.append((name, payload))
        if name == "host.chat.started" and started_gate is not None:
            started_gate.entered.set()
            assert started_gate.resume.wait(3)

    host = ChatHost(boundary_provider=lambda: boundary, screen_host=screen,
                    emit_callback=emit)
    session_id = host.current()["sessionId"]
    host.set_ui_state({"sessionId": session_id, "idle": True, "activityRevision": 0})
    with caller():
        accepted = host.submit({"sessionId": session_id, "message": "proactive request", "resources": ["private-image"] if with_screen else []})
    assert accepted["accepted"]
    return SimpleNamespace(host=host, boundary=boundary, assistant=assistant, timeline=timeline, trace=trace,
        events=events, cancellations=cancellations, outcomes=outcomes, errors=errors, finished=finished,
        operation_id=accepted["operationId"])


@pytest.mark.parametrize("gate_at", ["release", "inference"])
def test_scope_revocation_keeps_timeline_trace_and_desktop_terminal_consistent(tmp_path, monkeypatch, gate_at):
    turn = setup_turn(tmp_path, monkeypatch, gate_at)
    completed = gate_at == "release"
    try:
        assert turn.assistant.entered.wait(3)
        assert [name for name, _ in turn.events] == ["host.chat.started"]
        expected_kinds = [TimelineKind.OBSERVATION, *([TimelineKind.ASSISTANT] if completed else [])]
        assert [entry.kind for entry in turn.timeline.read_all("character")] == expected_kinds
        assert turn.trace.documents == [], "the gate precedes Trace finalization and desktop terminal publication"

        turn.host.revoke_scope("source.plugin")
        assert turn.cancellations == [not completed]
        assert [name for name, _ in turn.events] == ["host.chat.started"], "Host cannot manufacture a terminal"
        turn.assistant.resume.set()
        assert turn.finished.wait(3)
        assert turn.errors == []

        status = "completed" if completed else "cancelled"
        assert [outcome.terminal for outcome in turn.outcomes] == [f"chat.{status}"]
        assert [name for name, _ in turn.events] == ["host.chat.started", f"host.chat.{status}"]
        assert turn.events[-1][1]["operationId"] == turn.operation_id
        assert turn.assistant.release_statuses == [status]
        assert turn.trace.documents
        assert {document.get("status", "completed") for document in turn.trace.documents} == {status}
        assert [entry.kind for entry in turn.timeline.read_all("character")] == expected_kinds
        if completed:
            entry = turn.timeline.read_all("character")[-1]
            assert turn.events[-1][1]["reply"]["segments"] == entry.payload["segments"]
        else:
            assert "reply" not in turn.events[-1][1]
    finally:
        turn.assistant.resume.set()
        turn.boundary.close()


def test_scope_revoked_before_started_does_not_publish_late_desktop_output(tmp_path, monkeypatch):
    turn = setup_turn(tmp_path, monkeypatch, "before_start")
    try:
        assert turn.assistant.entered.wait(3)
        turn.host.revoke_scope("source.plugin")
        assert turn.cancellations == [True]
        turn.assistant.resume.set()
        assert turn.finished.wait(3)
        assert turn.errors == []
        assert [outcome.terminal for outcome in turn.outcomes] == ["chat.cancelled"]
        assert turn.events == []
        assert turn.assistant.requests == []
        assert turn.assistant.release_statuses == []
        assert turn.trace.documents == []
        assert turn.timeline.read_all("character") == []
    finally:
        turn.assistant.resume.set()
        turn.boundary.close()


def test_scope_revocation_during_started_publication_keeps_the_boundary_terminal(tmp_path, monkeypatch):
    started = SimpleNamespace(entered=threading.Event(), resume=threading.Event())
    turn = setup_turn(tmp_path, monkeypatch, "inference", started_gate=started)
    revoking, revoked = threading.Event(), threading.Event()

    def revoke():
        revoking.set()
        turn.host.revoke_scope("source.plugin")
        revoked.set()

    revoker = threading.Thread(target=revoke)
    try:
        assert started.entered.wait(3)
        revoker.start()
        assert revoking.wait(3)
        started.resume.set()
        assert revoked.wait(3), "scope cancellation must not hold the publication lock over RealChat"
        assert turn.cancellations == [True]
        turn.assistant.resume.set()
        assert turn.finished.wait(3)
        assert turn.errors == []
        assert [outcome.terminal for outcome in turn.outcomes] == ["chat.cancelled"]
        assert [name for name, _ in turn.events] == ["host.chat.started", "host.chat.cancelled"]
        assert all(entry.kind is not TimelineKind.ASSISTANT for entry in turn.timeline.read_all("character"))
        assert turn.assistant.release_statuses in ([], ["cancelled"])
    finally:
        started.resume.set()
        turn.assistant.resume.set()
        if revoker.ident is not None:
            revoker.join(3)
        turn.boundary.close()
    assert not revoker.is_alive()


@pytest.mark.parametrize("outcome", ["reply", "empty", "failed", "cancelled", "text_only"])
def test_screen_observation_logs_follow_real_terminal_and_keep_plugin_identity(tmp_path, monkeypatch, outcome):
    stream = io.BytesIO()
    bridge = install_runtime_logging(stream)
    turn = None
    try:
        turn = setup_turn(tmp_path, monkeypatch, "inference", with_screen=outcome != "text_only")
        assert turn.assistant.entered.wait(3)
        if outcome == "empty":
            turn.assistant.reply_text = ""
        elif outcome == "failed":
            turn.assistant.failure = OSError("model connection lost api_key=private-model-secret")
        elif outcome == "cancelled":
            turn.host.revoke_scope("source.plugin")
        assert [name for name, _ in turn.events] == ["host.chat.started"]
        turn.assistant.resume.set()
        assert turn.finished.wait(3)
        assert turn.errors == []
    finally:
        if turn is not None:
            turn.assistant.resume.set()
            turn.boundary.close()
        bridge.close()

    records = [json.loads(line.removeprefix(CORE_BRIDGE_PREFIX)) for line in stream.getvalue().splitlines()
               if line.startswith(CORE_BRIDGE_PREFIX)]
    records = [record for record in records if record.get("plugin_id") == "source.plugin"]
    if outcome == "text_only":
        assert records == []
        return
    terminal = {"failed": "failed", "cancelled": "cancelled"}.get(outcome, "completed")
    assert [record["attributes"]["event"] for record in records] == ["screen.observation.started", f"screen.observation.{terminal}"]
    assert all(record["plugin_name"] == "屏幕观察来源" and record["operation_id"] == turn.operation_id for record in records)
    assert all(record["attributes"]["screen_count"] == 1 and "测试角色" in record["message"] for record in records)
    assert records[-1]["severity"] == ("warning" if outcome == "failed" else "info")
    if outcome == "failed":
        assert "model connection lost" in records[-1]["attributes"]["diagnostic"]
        assert records[-1]["attributes"]["exception_chain"]
    if outcome in {"reply", "empty"}:
        assert ("没有回复" in records[-1]["message"]) == (outcome == "empty")
    serialized = json.dumps(records)
    assert all(private not in serialized for private in ("PRIVATE_SCREEN_PIXELS", "private-screen", "private-image", "private-model-secret", "proactive request"))
