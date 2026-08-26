from __future__ import annotations

import uuid
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from app.agent.actions import AgentEvent, AgentProgress, AgentResult, PendingToolAction
from app.agent.runtime import AgentRuntime
from app.agent.trace import TRACE_PROVENANCE_KEY, MessageProvenance, message_provenance
from app.core.cancellation import CancelChecker, check_cancelled
from app.core.interaction import get_interaction_id
from app.core.runtime_log import log_event, summarize_messages

if TYPE_CHECKING:
    from app.storage.visual_observation import VisualObservationJob, VisualObservationStore


ProgressCallback = Callable[[AgentProgress], None]


class ChatPipeline:
    """封装对话运行管线，让 Qt Worker 只保留线程和信号职责。"""

    def __init__(
        self,
        agent_runtime: AgentRuntime,
        visual_observation_store: VisualObservationStore | None = None,
        *,
        finalize_trace_operations: bool = True,
    ) -> None:
        self.agent_runtime = agent_runtime
        self.visual_observation_store = visual_observation_store
        self.finalize_trace_operations = finalize_trace_operations

    def run_user_message(
        self,
        messages: list[dict[str, Any]],
        *,
        visual_observation_jobs: list[VisualObservationJob] | None = None,
        progress_callback: ProgressCallback | None = None,
        cancel_checker: CancelChecker | None = None,
    ) -> AgentResult:
        log_event(
            "ChatWorker",
            "开始处理用户消息",
            {
                "message_count": len(messages),
                "visual_jobs": len(visual_observation_jobs or []),
                "messages": summarize_messages(messages),
            },
        )
        result = self._run_traced(
            lambda: self.agent_runtime.handle_user_message(
                messages,
                progress_callback=progress_callback,
                cancel_checker=cancel_checker,
            ),
            operation_id=_messages_trace_operation_id(messages),
        )
        self._record_visual_observation_from_result(
            "ChatWorker",
            visual_observation_jobs or [],
            result,
        )
        return result

    def run_confirmed_action(
        self,
        action: PendingToolAction,
        *,
        progress_callback: ProgressCallback | None = None,
        cancel_checker: CancelChecker | None = None,
    ) -> AgentResult:
        log_event("ChatWorker", "开始处理已确认动作", action.to_dict())
        operation_id = _pending_trace_operation_id(action)
        return self._run_traced(
            lambda: self.agent_runtime.handle_confirmed_action(
                action,
                progress_callback=progress_callback,
                cancel_checker=cancel_checker,
            ),
            operation_id=operation_id,
        )

    def run_cancelled_action(
        self,
        action: PendingToolAction,
        *,
        cancel_checker: CancelChecker | None = None,
    ) -> AgentResult:
        check_cancelled(cancel_checker)
        log_event("ChatWorker", "开始处理已取消动作", action.to_dict())
        return self._run_traced(
            lambda: self.agent_runtime.handle_cancelled_action(action),
            operation_id=_pending_trace_operation_id(action),
        )

    def run_event(
        self,
        event: AgentEvent,
        *,
        visual_observation_jobs: list[VisualObservationJob] | None = None,
        progress_callback: ProgressCallback | None = None,
        cancel_checker: CancelChecker | None = None,
    ) -> AgentResult:
        log_event(
            "EventWorker",
            "开始处理主动事件",
            {
                "type": event.type,
                "payload": event.payload,
            },
        )
        result = self._run_traced(
            lambda: self.agent_runtime.handle_event(
                event,
                progress_callback=progress_callback,
                cancel_checker=cancel_checker,
            ),
        )
        self._record_visual_observation_from_result(
            "EventWorker",
            visual_observation_jobs or [],
            result,
        )
        return result

    def _run_traced(
        self,
        callback: Callable[[], AgentResult],
        *,
        operation_id: str = "",
    ) -> AgentResult:
        """Keep every legacy operation together while Runtime v2 owns its outer terminal."""

        if not self.finalize_trace_operations:
            return callback()
        requested_operation_id = (
            operation_id
            or get_interaction_id()
            or f"legacy-{uuid.uuid4().hex}"
        )
        with self.agent_runtime.trace_operation(requested_operation_id) as resolved_operation_id:
            try:
                result = callback()
            except BaseException:
                self.agent_runtime.finish_trace_operation(resolved_operation_id, status="failed")
                raise
            continuation = any(
                action.type in {"pending_action", "screen_observation_request"}
                for action in result.actions
            )
            if continuation:
                _bind_pending_trace_operation(result, resolved_operation_id)
            if not continuation:
                self.agent_runtime.finish_trace_operation(
                    resolved_operation_id,
                    status="completed",
                )
            return result

    def _record_visual_observation_from_result(
        self,
        log_scope: str,
        visual_observation_jobs: list[VisualObservationJob],
        result: AgentResult,
    ) -> None:
        if self.visual_observation_store is None or not visual_observation_jobs:
            return
        if result.visual_observation is None:
            log_event(log_scope, "视觉观察摘要缺失，跳过保存", {"visual_jobs": len(visual_observation_jobs)})
            return
        from app.storage.visual_observation import visual_observation_record_from_summary

        record = visual_observation_record_from_summary(
            visual_observation_jobs[0],
            result.visual_observation,
        )
        if record is None:
            log_event(log_scope, "视觉观察摘要为空，跳过保存", {"visual_jobs": len(visual_observation_jobs)})
            return
        try:
            self.visual_observation_store.append(record)
        except Exception as exc:  # noqa: BLE001 - 视觉记忆失败不能击穿聊天成功结果
            log_event(
                log_scope,
                "视觉观察记录保存失败，已保留聊天结果",
                {"visual_id": record.id, "error": str(exc)},
            )
            return
        log_event(
            log_scope,
            "视觉观察记录已保存",
            {
                "visual_id": record.id,
                "source": record.source,
                "summary": record.summary,
                "visible_text_count": len(record.visible_texts),
                "sensitive_redacted": record.sensitive_redacted,
            },
        )


def _bind_pending_trace_operation(result: AgentResult, operation_id: str) -> None:
    if not operation_id:
        return
    for action in result.actions:
        if action.type not in {"pending_action", "screen_observation_request"}:
            continue
        messages = action.payload.get("continuation_messages")
        if not isinstance(messages, list):
            continue
        for message in messages:
            if not isinstance(message, dict):
                continue
            provenance = message_provenance(message)
            if provenance is None:
                continue
            message[TRACE_PROVENANCE_KEY] = MessageProvenance(
                provenance.kind,
                runtime_items=provenance.runtime_items,
                operation_id=operation_id,
                turn_id=provenance.turn_id,
                history_drops=provenance.history_drops,
            )
            break


def _pending_trace_operation_id(action: PendingToolAction) -> str:
    return _messages_trace_operation_id(action.continuation_messages)


def _messages_trace_operation_id(messages: list[dict[str, Any]]) -> str:
    for message in messages:
        provenance = message_provenance(message)
        if provenance is not None and provenance.operation_id:
            return provenance.operation_id
    return ""
