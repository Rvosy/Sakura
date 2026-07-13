from __future__ import annotations

import threading
import time
from types import SimpleNamespace

import pytest

from app.agent.actions import AgentAction, AgentEvent, PendingToolAction
from app.core.cancellation import OperationCancelled


class _BlockingPipeline:
    def __init__(self) -> None:
        self.started = threading.Event()
        self.release = threading.Event()
        self.confirmed: list[PendingToolAction] = []
        self.cancelled: list[PendingToolAction] = []
        self.events: list[AgentEvent] = []

    def run_user_message(self, messages, *, progress_callback=None, cancel_checker=None, **_kwargs):  # type: ignore[no-untyped-def]
        self.started.set()
        while not self.release.wait(0.01):
            if cancel_checker is not None:
                cancel_checker()
        if progress_callback is not None:
            progress_callback(SimpleNamespace(stage="reply", reply="progress", metadata={}))
        if cancel_checker is not None:
            cancel_checker()
        return SimpleNamespace(reply=messages[-1]["content"], actions=[])

    def run_confirmed_action(self, action, *, cancel_checker=None, **_kwargs):  # type: ignore[no-untyped-def]
        if cancel_checker is not None:
            cancel_checker()
        self.confirmed.append(action)
        return SimpleNamespace(reply="confirmed", actions=[])

    def run_cancelled_action(self, action, *, cancel_checker=None, **_kwargs):  # type: ignore[no-untyped-def]
        if cancel_checker is not None:
            cancel_checker()
        self.cancelled.append(action)
        return SimpleNamespace(reply="rejected", actions=[])

    def run_event(self, event, *, cancel_checker=None, **_kwargs):  # type: ignore[no-untyped-def]
        if cancel_checker is not None:
            cancel_checker()
        self.events.append(event)
        return SimpleNamespace(reply="event", actions=[])


def test_only_one_foreground_interaction_runs_at_a_time() -> None:
    from app.core.assistant_service import AssistantApplication, AssistantBusyError

    pipeline = _BlockingPipeline()
    service = AssistantApplication(pipeline, session_id="session-1")
    first = service.send_message([{"role": "user", "content": "first"}])
    assert pipeline.started.wait(1)

    with pytest.raises(AssistantBusyError):
        service.send_message([{"role": "user", "content": "second"}])

    pipeline.release.set()
    assert first.result(timeout=1).reply == "first"
    assert service.busy is False
    service.close()


def test_cancellation_marks_interaction_and_ignores_late_result() -> None:
    from app.core.assistant_service import AssistantApplication, InteractionCancelledError

    pipeline = _BlockingPipeline()
    service = AssistantApplication(pipeline, session_id="session-1")
    handle = service.send_message([{"role": "user", "content": "cancel me"}])
    assert pipeline.started.wait(1)

    assert handle.cancel() is True
    pipeline.release.set()

    with pytest.raises(InteractionCancelledError):
        handle.result(timeout=1)
    assert handle.snapshot().state == "cancelled"
    assert service.active_interaction is None
    service.close()


def test_progress_callback_runs_for_current_interaction() -> None:
    from app.core.assistant_service import AssistantApplication

    pipeline = _BlockingPipeline()
    progress: list[str] = []
    service = AssistantApplication(pipeline, session_id="session-1")
    handle = service.send_message(
        [{"role": "user", "content": "hello"}],
        progress_callback=lambda item: progress.append(item.stage),
    )
    pipeline.release.set()

    assert handle.result(timeout=1).reply == "hello"
    assert progress == ["reply"]
    service.close()


def test_pending_action_confirmation_accepts_only_stored_action_id() -> None:
    from app.core.assistant_service import AssistantApplication, PendingActionNotFound

    pending = PendingToolAction(
        "open_url",
        {"url": "https://example.com"},
        "用户要求打开网页",
        id="action-1",
    )

    class PendingPipeline(_BlockingPipeline):
        def run_user_message(self, *_args, **_kwargs):  # type: ignore[no-untyped-def]
            return SimpleNamespace(
                reply="confirm",
                actions=[
                    AgentAction(
                        type="pending_action",
                        payload=pending.to_dict(include_context=True),
                    )
                ],
            )

    pipeline = PendingPipeline()
    service = AssistantApplication(pipeline, session_id="session-1")
    service.send_message([{"role": "user", "content": "open"}]).result(timeout=1)

    assert service.pending_actions == (
        {
            "id": "action-1",
            "tool_name": "open_url",
            "arguments": {"url": "https://example.com"},
            "reason": "用户要求打开网页",
            "created_at": pending.created_at,
            "tool_call_id": "",
        },
    )
    assert service.confirm_action("action-1").result(timeout=1).reply == "confirmed"
    assert pipeline.confirmed[0].arguments == {"url": "https://example.com"}

    with pytest.raises(PendingActionNotFound):
        service.confirm_action("action-1")
    service.close()


def test_pending_action_is_bound_to_session() -> None:
    from app.brain_host.pending_actions import PendingActionStore
    from app.core.assistant_service import AssistantApplication, PendingActionNotFound

    store = PendingActionStore()
    store.add(
        PendingToolAction("open_url", {"url": "https://example.com"}, "", id="action-1"),
        session_id="session-1",
        interaction_id="interaction-1",
    )
    service = AssistantApplication(
        _BlockingPipeline(),
        session_id="session-2",
        pending_actions=store,
    )

    with pytest.raises(PendingActionNotFound):
        service.confirm_action("action-1")
    service.close()


def test_pending_action_is_retained_when_submission_fails(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from app.brain_host.pending_actions import PendingActionStore
    from app.core.assistant_service import AssistantApplication

    store = PendingActionStore()
    store.add(
        PendingToolAction("open_url", {"url": "https://example.com"}, "", id="action-1"),
        session_id="session-1",
        interaction_id="interaction-1",
    )
    service = AssistantApplication(
        _BlockingPipeline(),
        session_id="session-1",
        pending_actions=store,
    )

    def fail_submit(*_args, **_kwargs):  # type: ignore[no-untyped-def]
        raise RuntimeError("executor unavailable")

    monkeypatch.setattr(service._executor, "submit", fail_submit)
    with pytest.raises(RuntimeError, match="executor unavailable"):
        service.confirm_action("action-1")

    assert tuple(action["id"] for action in service.pending_actions) == ("action-1",)
    service.close()


def test_proactive_event_does_not_preempt_user_chat() -> None:
    from app.core.assistant_service import AssistantApplication

    pipeline = _BlockingPipeline()
    service = AssistantApplication(pipeline, session_id="session-1")
    handle = service.send_message([{"role": "user", "content": "busy"}])
    assert pipeline.started.wait(1)

    assert service.dispatch_event(AgentEvent("reminder_due", {"id": "r1"})) is None
    assert pipeline.events == []

    pipeline.release.set()
    handle.result(timeout=1)
    event_handle = service.dispatch_event(AgentEvent("reminder_due", {"id": "r1"}))
    assert event_handle is not None
    assert event_handle.result(timeout=1).reply == "event"
    service.close()


def test_close_cancels_worker_then_runs_shutdown_callbacks_without_leaks() -> None:
    from app.core.assistant_service import AssistantApplication

    class CancelPipeline(_BlockingPipeline):
        def run_user_message(self, _messages, *, cancel_checker=None, **_kwargs):  # type: ignore[no-untyped-def]
            self.started.set()
            while True:
                try:
                    if cancel_checker is not None:
                        cancel_checker()
                except OperationCancelled:
                    raise
                time.sleep(0.005)

    pipeline = CancelPipeline()
    order: list[str] = []
    service = AssistantApplication(pipeline, session_id="session-1")
    service.add_shutdown_callback(lambda: order.append("resources"))
    service.send_message([{"role": "user", "content": "busy"}])
    assert pipeline.started.wait(1)

    service.close(wait=True)

    assert order == ["resources"]
    assert service.closed is True
    assert not any(
        thread.name.startswith("sakura-assistant") and thread.is_alive()
        for thread in threading.enumerate()
    )


def test_thread_scheduler_runs_jobs_and_stops_cleanly() -> None:
    from app.brain_host.scheduler import PeriodicScheduler

    fired = threading.Event()
    scheduler = PeriodicScheduler(poll_interval=0.005)
    scheduler.add_job("reminders", 0.01, fired.set, run_immediately=True)

    scheduler.start()
    assert fired.wait(1)
    scheduler.stop(timeout=1)

    assert scheduler.running is False
    assert not any(
        thread.name == "sakura-brain-scheduler" and thread.is_alive()
        for thread in threading.enumerate()
    )


def test_memory_curation_task_is_headless_and_cancellable() -> None:
    from app.agent.memory_curation_task import MemoryCurationTask

    class Curator:
        def curate_entries(self, entries, *, cancel_checker=None):  # type: ignore[no-untyped-def]
            if cancel_checker is not None:
                cancel_checker()
            return list(entries)

    task = MemoryCurationTask(Curator(), ["entry"])  # type: ignore[arg-type]
    assert task.run() == ["entry"]
    task.cancel()
    with pytest.raises(OperationCancelled):
        task.run()
