"""长期运行、无 Qt 的 Sakura Assistant 应用服务。"""

from __future__ import annotations

import threading
import uuid
from collections.abc import Callable
from concurrent.futures import CancelledError, Future, ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any, Literal

from app.agent.actions import AgentEvent, AgentProgress, AgentResult, PendingToolAction
from app.brain_host.pending_actions import PendingActionLookupError, PendingActionStore
from app.core.cancellation import CancellationToken, OperationCancelled
from app.core.chat_pipeline import ChatPipeline, pending_actions_from_result
from app.core.interaction import set_interaction_id


InteractionKind = Literal["chat", "confirm_action", "reject_action", "event"]
ProgressCallback = Callable[[AgentProgress], None]


class AssistantBusyError(RuntimeError):
    pass


class AssistantClosedError(RuntimeError):
    pass


class InteractionCancelledError(RuntimeError):
    pass


class PendingActionNotFound(LookupError):
    pass


@dataclass(frozen=True)
class InteractionSnapshot:
    interaction_id: str
    request_id: str
    kind: InteractionKind
    state: str
    pending_action_ids: tuple[str, ...]
    has_result: bool
    error: str


@dataclass
class _Interaction:
    interaction_id: str
    request_id: str
    kind: InteractionKind
    cancel_token: CancellationToken
    progress_callback: ProgressCallback | None
    state: str = "queued"
    future: Future[AgentResult] | None = None
    pending_action_ids: tuple[str, ...] = ()
    result: AgentResult | None = None
    error: str = ""


class InteractionHandle:
    def __init__(self, service: "AssistantApplication", interaction: _Interaction) -> None:
        self._service = service
        self._interaction = interaction

    @property
    def interaction_id(self) -> str:
        return self._interaction.interaction_id

    @property
    def request_id(self) -> str:
        return self._interaction.request_id

    def cancel(self) -> bool:
        return self._service.cancel(self.interaction_id)

    def result(self, timeout: float | None = None) -> AgentResult:
        future = self._interaction.future
        if future is None:
            raise RuntimeError("interaction was not submitted")
        try:
            return future.result(timeout)
        except CancelledError as exc:
            raise InteractionCancelledError("interaction was cancelled") from exc

    def snapshot(self) -> InteractionSnapshot:
        return self._service._snapshot(self._interaction)


class AssistantApplication:
    def __init__(
        self,
        pipeline: ChatPipeline,
        *,
        session_id: str,
        pending_actions: PendingActionStore | None = None,
        max_workers: int = 1,
    ) -> None:
        self.pipeline = pipeline
        self.session_id = session_id
        self._pending_actions = pending_actions or PendingActionStore()
        self._executor = ThreadPoolExecutor(
            max_workers=max(1, int(max_workers)),
            thread_name_prefix="sakura-assistant",
        )
        self._lock = threading.RLock()
        self._active: _Interaction | None = None
        self._closed = False
        self._shutdown_callbacks: list[Callable[[], None]] = []

    @property
    def busy(self) -> bool:
        with self._lock:
            return self._active is not None

    @property
    def closed(self) -> bool:
        with self._lock:
            return self._closed

    @property
    def active_interaction(self) -> InteractionSnapshot | None:
        with self._lock:
            return self._snapshot(self._active) if self._active is not None else None

    @property
    def pending_actions(self) -> tuple[dict[str, object], ...]:
        return self._pending_actions.list_for_session(self.session_id)

    def add_shutdown_callback(self, callback: Callable[[], None]) -> None:
        with self._lock:
            self._shutdown_callbacks.append(callback)

    def send_message(
        self,
        messages: list[dict[str, Any]],
        *,
        visual_observation_jobs: list[Any] | None = None,
        progress_callback: ProgressCallback | None = None,
        request_id: str | None = None,
    ) -> InteractionHandle:
        copied_messages = [dict(message) for message in messages]
        copied_jobs = list(visual_observation_jobs or [])
        return self._submit(
            "chat",
            lambda interaction: self.pipeline.run_user_message(
                copied_messages,
                visual_observation_jobs=copied_jobs,
                progress_callback=self._progress_callback(interaction),
                cancel_checker=interaction.cancel_token.throw_if_cancelled,
            ),
            progress_callback=progress_callback,
            request_id=request_id,
        )

    def confirm_action(
        self,
        action_id: str,
        *,
        progress_callback: ProgressCallback | None = None,
        request_id: str | None = None,
    ) -> InteractionHandle:
        with self._lock:
            self._ensure_available()
            try:
                action = self._pending_actions.get(action_id, session_id=self.session_id)
            except PendingActionLookupError as exc:
                raise PendingActionNotFound(str(exc)) from exc
            handle = self._submit_locked(
                "confirm_action",
                lambda interaction: self.pipeline.run_confirmed_action(
                    action,
                    progress_callback=self._progress_callback(interaction),
                    cancel_checker=interaction.cancel_token.throw_if_cancelled,
                ),
                progress_callback=progress_callback,
                request_id=request_id,
            )
            self._consume_submitted_action(action_id, handle)
            return handle

    def reject_action(
        self,
        action_id: str,
        *,
        request_id: str | None = None,
    ) -> InteractionHandle:
        with self._lock:
            self._ensure_available()
            try:
                action = self._pending_actions.get(action_id, session_id=self.session_id)
            except PendingActionLookupError as exc:
                raise PendingActionNotFound(str(exc)) from exc
            handle = self._submit_locked(
                "reject_action",
                lambda interaction: self.pipeline.run_cancelled_action(
                    action,
                    cancel_checker=interaction.cancel_token.throw_if_cancelled,
                ),
                progress_callback=None,
                request_id=request_id,
            )
            self._consume_submitted_action(action_id, handle)
            return handle

    def dispatch_event(
        self,
        event: AgentEvent,
        *,
        visual_observation_jobs: list[Any] | None = None,
        progress_callback: ProgressCallback | None = None,
    ) -> InteractionHandle | None:
        copied_jobs = list(visual_observation_jobs or [])
        with self._lock:
            if self._closed or self._active is not None:
                return None
            return self._submit_locked(
                "event",
                lambda interaction: self.pipeline.run_event(
                    event,
                    visual_observation_jobs=copied_jobs,
                    progress_callback=self._progress_callback(interaction),
                    cancel_checker=interaction.cancel_token.throw_if_cancelled,
                ),
                progress_callback=progress_callback,
                request_id=None,
            )

    def cancel(self, interaction_id: str) -> bool:
        with self._lock:
            interaction = self._active
            if interaction is None or interaction.interaction_id != interaction_id:
                return False
            interaction.cancel_token.cancel()
            return True

    def close(self, *, wait: bool = True) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            active = self._active
            if active is not None:
                active.cancel_token.cancel()
            callbacks = tuple(self._shutdown_callbacks)
        self._executor.shutdown(wait=wait, cancel_futures=False)
        self._pending_actions.clear_session(self.session_id)
        for callback in callbacks:
            try:
                callback()
            except Exception:  # noqa: BLE001
                pass

    def _submit(
        self,
        kind: InteractionKind,
        operation: Callable[[_Interaction], AgentResult],
        *,
        progress_callback: ProgressCallback | None,
        request_id: str | None,
    ) -> InteractionHandle:
        with self._lock:
            self._ensure_available()
            return self._submit_locked(
                kind,
                operation,
                progress_callback=progress_callback,
                request_id=request_id,
            )

    def _submit_locked(
        self,
        kind: InteractionKind,
        operation: Callable[[_Interaction], AgentResult],
        *,
        progress_callback: ProgressCallback | None,
        request_id: str | None,
    ) -> InteractionHandle:
        interaction = _Interaction(
            interaction_id=f"interaction-{uuid.uuid4().hex}",
            request_id=request_id or f"request-{uuid.uuid4().hex}",
            kind=kind,
            cancel_token=CancellationToken(),
            progress_callback=progress_callback,
        )
        self._active = interaction
        try:
            interaction.future = self._executor.submit(
                self._execute,
                interaction,
                operation,
            )
        except Exception:
            self._active = None
            raise
        return InteractionHandle(self, interaction)

    def _execute(
        self,
        interaction: _Interaction,
        operation: Callable[[_Interaction], AgentResult],
    ) -> AgentResult:
        set_interaction_id(interaction.interaction_id)
        with self._lock:
            interaction.state = "running"
        try:
            interaction.cancel_token.throw_if_cancelled()
            result = operation(interaction)
            pending = pending_actions_from_result(result)
            with self._lock:
                interaction.cancel_token.throw_if_cancelled()
                for action in pending:
                    self._pending_actions.add(
                        action,
                        session_id=self.session_id,
                        interaction_id=interaction.interaction_id,
                    )
                interaction.result = result
                interaction.pending_action_ids = tuple(action.id for action in pending)
                interaction.state = "completed"
                if self._active is interaction:
                    self._active = None
            return result
        except OperationCancelled as exc:
            with self._lock:
                interaction.state = "cancelled"
                interaction.error = "interaction cancelled"
            raise InteractionCancelledError("interaction was cancelled") from exc
        except Exception as exc:
            with self._lock:
                interaction.state = "failed"
                interaction.error = str(exc)
            raise
        finally:
            with self._lock:
                if self._active is interaction:
                    self._active = None

    def _consume_submitted_action(
        self,
        action_id: str,
        handle: InteractionHandle,
    ) -> None:
        try:
            self._pending_actions.take(action_id, session_id=self.session_id)
        except PendingActionLookupError as exc:
            handle.cancel()
            raise PendingActionNotFound(str(exc)) from exc

    def _progress_callback(self, interaction: _Interaction) -> ProgressCallback:
        def forward(progress: AgentProgress) -> None:
            interaction.cancel_token.throw_if_cancelled()
            callback = interaction.progress_callback
            if callback is not None:
                callback(progress)

        return forward

    def _ensure_available(self) -> None:
        if self._closed:
            raise AssistantClosedError("AssistantApplication is closed")
        if self._active is not None:
            raise AssistantBusyError("another foreground interaction is running")

    def _snapshot(self, interaction: _Interaction | None) -> InteractionSnapshot:
        if interaction is None:
            raise RuntimeError("interaction is not available")
        with self._lock:
            return InteractionSnapshot(
                interaction_id=interaction.interaction_id,
                request_id=interaction.request_id,
                kind=interaction.kind,
                state=interaction.state,
                pending_action_ids=interaction.pending_action_ids,
                has_result=interaction.result is not None,
                error=interaction.error,
            )
