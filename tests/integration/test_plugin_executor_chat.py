from __future__ import annotations

import shutil
import threading

import pytest

from app.agent.tools import ToolRegistry
from app.config.character_loader import CharacterRegistry
from app.core_host.assistant_adapter import AssistantSession
from app.core_host.executors import ExecutionError
from app.core_host.plugin_application import PluginApplicationHost
from app.core_host.real_chat import RealChatBoundary, RealChatRejection
from app.plugins.runtime_v4 import PluginRuntimeError
from app.storage.runtime_roots import RuntimeRoots
from app.storage.timeline import TimelineKind, TimelineStore
from test_core_host_real_chat_integration import SOURCE_ROOT, _request, GENERATION_ID, GENERATION_CREDENTIAL


SERVICE = "fixture.executor"


@pytest.fixture
def executor_chat(tmp_path):
    user = tmp_path / "user"
    shutil.copytree(SOURCE_ROOT, user)
    (user / "config/api.yaml").write_text("invalid: [", encoding="utf-8")
    plugin = tmp_path / "distribution/plugins/builtin/fixture.executor"
    plugin.mkdir(parents=True)
    (plugin / "plugin.yaml").write_text(
        "api: 4\nid: fixture.executor\nname: 离线测试\nversion: 1.0.0\nentry: plugin:Plugin\n"
        f"provides: [{SERVICE}]\nrequires: [sakura.host.executors]\n", encoding="utf-8",
    )
    (plugin / "plugin.py").write_text('''
import threading
class Plugin:
    def setup(self, context):
        self.context = context
        self.lock = threading.Lock()
        self.jobs = {}
        context.provide("fixture.executor", self, exports=("describe", "begin", "read", "cancel", "release", "inspect"))
        context.get("sakura.host.executors").register({"serviceKey": "fixture.executor", "displayName": "离线测试"})
        context.effect(self.close)
    def describe(self):
        return {"schemaVersion": 1, "ready": True, "inputs": ["text"]}
    def begin(self, request):
        assert self.context.caller_id == "sakura.core"
        op = request["operationId"]
        with self.lock:
            if op in self.jobs:
                return {"operationId": op}
            job = {"request": request, "stop": threading.Event(), "release": threading.Event(), "done": threading.Event(), "state": "running", "count": 0}
            self.jobs[op] = job
        def work():
            assert self.context.caller_id is None
            while not job["release"].is_set() and not job["stop"].wait(0.02):
                pass
            with self.lock:
                if job["stop"].is_set():
                    job["state"] = "cancelled"
                else:
                    job["count"] += 1
                    job["state"] = "completed"
            job["done"].set()
        job["thread"] = threading.Thread(target=work)
        job["thread"].start()
        if request["input"]["text"] == "begin-timeout":
            job["done"].wait(10)
        return {"operationId": op}
    def read(self, request):
        with self.lock:
            job = self.jobs[request["operationId"]]
            state = "invalid" if job.get("bad_cancel") else job["state"]
            if job["request"]["input"]["text"] == "task-failure" and state == "completed":
                state = "failed"
            return {"operationId": request["operationId"], "state": state, "sequence": 1,
                "progress": "等待完成", "reply": {"segments": [{"text": "离线完成"}]}}
    def cancel(self, request):
        job = self.jobs[request["operationId"]]
        if job["request"]["input"]["text"] == "bad-cancel":
            job["bad_cancel"] = True
        else:
            job["stop"].set()
        return {"accepted": True}
    def release(self, op):
        self.jobs[op]["release"].set()
    def inspect(self, op):
        job = self.jobs[op]
        return {"done": job["done"].is_set(), "count": job["count"], "request": job["request"]}
    def close(self):
        for job in self.jobs.values():
            job["stop"].set()
            job["thread"].join(1)
''', encoding="utf-8")
    roots = RuntimeRoots(tmp_path / "distribution", user)
    tools = ToolRegistry([])
    application = PluginApplicationHost(roots, GENERATION_ID, tools)
    application.start()
    # Developer-only binding exercises the retained execution contract. Normal
    # Assistant initialization no longer selects plugins from user settings.
    character = CharacterRegistry(user).get("sakura")
    executor = application.bind_executor(SERVICE)
    executor.initialize(character)
    session = AssistantSession(character=character, executor=executor)
    application.bind_session(session)
    events = []
    progress, terminal = threading.Event(), threading.Event()
    def publish(value):
        events.append(value)
        if value["name"] == "chat.progress":
            progress.set()
        if value["name"] in {"chat.completed", "chat.cancelled", "chat.failed"}:
            terminal.set()
    timeline = TimelineStore(user / "test-timeline.sqlite3")
    timeline.initialize()
    playback = []
    boundary = RealChatBoundary(
        GENERATION_ID, GENERATION_CREDENTIAL, user, session_provider=lambda: session,
        plugin_application_provider=lambda: application, timeline_store=timeline,
        event_publisher=publish, segment_authorizer=lambda **values: playback.append(values),
    )
    yield application, session, boundary, events, progress, terminal, timeline, playback
    boundary.close()
    executor.close()
    application.close()


@pytest.mark.parametrize("cancelled", [False, True])
def test_explicit_plugin_binding_uses_chat_boundary_and_stops_actual_work(executor_chat, cancelled):
    app, session, boundary, events, progress, terminal, timeline, playback = executor_chat
    request = _request("offline-operation", "chat.send", {"operationId": "offline-operation", "message": "开始整理"})
    boundary.reserve_send(request)
    boundary.start_send(request)
    assert progress.wait(5)
    actual = app.call_service(SERVICE, "inspect", request["id"])["request"]
    assert actual["characterId"] == session.character.id
    assert actual["generationId"] == GENERATION_ID
    assert actual["input"] == {"text": "开始整理"}
    app.call_service(SERVICE, "begin", actual)  # Repeated admission must not start a second worker.
    if cancelled:
        assert boundary.cancel_host_message(request["id"])
    else:
        app.call_service(SERVICE, "release", request["id"])
    assert terminal.wait(5)
    inspection = app.call_service(SERVICE, "inspect", request["id"])
    assert inspection["done"] is True
    assert inspection["count"] == (0 if cancelled else 1)
    for _ in range(2):
        app.call_service(SERVICE, "read", {"operationId": request["id"], "afterSequence": 1})
    terminals = [event for event in events if event["name"] in {"chat.completed", "chat.cancelled", "chat.failed"}]
    assert [event["name"] for event in terminals] == ["chat.cancelled" if cancelled else "chat.completed"]
    rows = timeline.read_all(session.character.id)
    assert len([entry for entry in rows if entry.kind == TimelineKind.ASSISTANT]) == (0 if cancelled else 1)
    assert len(playback) == (0 if cancelled else 1)


def test_same_id_reload_cannot_adopt_pending_operation_or_commit_old_result(executor_chat):
    app, session, boundary, events, progress, terminal, timeline, playback = executor_chat
    request = _request("old-operation", "chat.send", {"operationId": "old-operation", "message": "等待"})
    boundary.reserve_send(request)
    boundary.start_send(request)
    assert progress.wait(5)
    old = session.executor
    app.application._manager.reload_plugin("fixture.executor")
    assert terminal.wait(5)
    assert [event["name"] for event in events][-1] == "chat.failed"
    assert events[-1]["payload"]["error"]["code"] == "EXECUTOR_BINDING_EXPIRED"
    commits = []
    with pytest.raises(ExecutionError, match="EXECUTOR_BINDING_EXPIRED"):
        old.commit(lambda: commits.append(True))
    assert not commits and not playback
    assert not [entry for entry in timeline.read_all(session.character.id) if entry.kind == TimelineKind.ASSISTANT]
    replacement = app.bind_executor(SERVICE)
    replacement.initialize(session.character)
    assert replacement.identity != old.identity
    with pytest.raises(PluginRuntimeError):
        app.call_service(SERVICE, "inspect", request["id"])


def test_begin_timeout_keeps_original_operation_queryable_and_cancellable(executor_chat):
    app, session, boundary, events, progress, terminal, timeline, playback = executor_chat
    request = _request("slow-admission", "chat.send", {"operationId": "slow-admission", "message": "begin-timeout"})
    boundary.reserve_send(request)
    boundary.start_send(request)
    # This progress can only be read after begin timed out without returning a job ID.
    assert progress.wait(5)
    assert not terminal.is_set()
    assert boundary.cancel_host_message(request["id"])
    assert terminal.wait(5)
    assert app.call_service(SERVICE, "inspect", request["id"])["done"] is True
    assert [event["name"] for event in events][-1] == "chat.cancelled"
    assert not playback


def test_unknown_state_after_cancel_reclaims_plugin_before_releasing_operation(executor_chat):
    app, session, boundary, events, progress, terminal, timeline, playback = executor_chat
    request = _request("bad-cancel", "chat.send", {"operationId": "bad-cancel", "message": "bad-cancel"})
    boundary.reserve_send(request)
    boundary.start_send(request)
    assert progress.wait(5)
    assert boundary.cancel_host_message(request["id"])
    assert terminal.wait(5)
    record = app.application.public_snapshot()["plugins"][0]
    assert record["state"] == "failed" and record["pid"] is None
    assert record["reasonCode"] == "EXECUTOR_PROTOCOL_FAILED"
    assert not playback


def test_business_failure_does_not_stop_the_executor_process(executor_chat):
    app, session, boundary, events, progress, terminal, timeline, playback = executor_chat
    request = _request("task-failure", "chat.send", {"operationId": "task-failure", "message": "task-failure"})
    boundary.reserve_send(request)
    boundary.start_send(request)
    assert progress.wait(5)
    app.call_service(SERVICE, "release", request["id"])
    assert terminal.wait(5)
    assert events[-1]["name"] == "chat.failed"
    assert events[-1]["payload"]["error"]["code"] == "EXECUTOR_TASK_FAILED"
    assert app.application.public_snapshot()["plugins"][0]["state"] == "active"
    assert not playback


def test_input_save_failure_never_starts_plugin_work(executor_chat, monkeypatch):
    app, session, boundary, events, progress, terminal, timeline, playback = executor_chat
    def fail(_entry):
        raise OSError("isolated fixture write failure")
    monkeypatch.setattr(timeline, "append", fail)
    request = _request("write-failure", "chat.send", {"operationId": "write-failure", "message": "等待"})
    boundary.reserve_send(request)
    boundary.start_send(request)
    assert terminal.wait(5)
    assert events[-1]["payload"]["error"]["code"] == "TIMELINE_WRITE_FAILED"
    assert events[-1]["payload"]["historyStatus"] == "degraded"
    with pytest.raises(PluginRuntimeError):
        app.call_service(SERVICE, "inspect", request["id"])
    assert not progress.is_set() and not playback


def test_unconfirmed_process_stop_never_claims_cancelled_or_accepts_new_work(executor_chat, monkeypatch):
    app, session, boundary, events, progress, terminal, timeline, playback = executor_chat
    request = _request("cleanup-failure", "chat.send", {"operationId": "cleanup-failure", "message": "bad-cancel"})
    boundary.reserve_send(request)
    boundary.start_send(request)
    assert progress.wait(5)
    manager = app.application._manager
    stop_process = manager.stop_bound_service
    def fail_stop(*args, **kwargs):
        raise PluginRuntimeError("PLUGIN_CLEANUP_FAILED", plugin_id="fixture.executor")
    monkeypatch.setattr(manager, "stop_bound_service", fail_stop)
    try:
        assert boundary.cancel_host_message(request["id"])
        assert terminal.wait(3)
        assert events[-1]["name"] == "chat.failed"
        assert events[-1]["payload"]["error"]["code"] == "EXECUTOR_STOP_UNCONFIRMED"
        assert app.call_service(SERVICE, "inspect", request["id"])["done"] is False
        next_request = _request("after-cleanup-failure", "chat.send", {
            "operationId": "after-cleanup-failure", "message": "下一项",
        })
        with pytest.raises(RealChatRejection, match="EXECUTOR_STOP_UNCONFIRMED"):
            boundary.reserve_send(next_request)
        applied = threading.Event()
        boundary.schedule_runtime_update("provider", applied.set)
        assert not applied.is_set()
        assert not playback
        assert not [entry for entry in timeline.read_all(session.character.id) if entry.kind == TimelineKind.ASSISTANT]
    finally:
        monkeypatch.setattr(manager, "stop_bound_service", stop_process)
        stop_process(SERVICE, session.executor.identity, reason="TEST_CLEANUP")
        # Test teardown has now stopped the process; production requires exiting the Core.
        with boundary._changed:
            boundary._executions.pop(request["id"], None)
            boundary._pending_runtime_updates.pop("provider", None)
