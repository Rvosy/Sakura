from __future__ import annotations

from collections.abc import Callable
from typing import Any

from app.agent import AgentEvent, AgentProgress, AgentResult, AgentRuntime, PendingToolAction
from app.core.cancellation import CancelChecker, check_cancelled
from app.core.debug_log import debug_log, summarize_messages
from app.storage.visual_observation import (
    VisualObservationJob,
    VisualObservationRecord,
    VisualObservationStore,
    summarize_visual_observation,
)


ProgressCallback = Callable[[AgentProgress], None]


class ChatPipeline:
    """封装对话运行管线，让 Qt Worker 只保留线程和信号职责。"""

    def __init__(
        self,
        agent_runtime: AgentRuntime,
        visual_observation_store: VisualObservationStore | None = None,
    ) -> None:
        self.agent_runtime = agent_runtime
        self.visual_observation_store = visual_observation_store

    def run_user_message(
        self,
        messages: list[dict[str, Any]],
        *,
        visual_observation_jobs: list[VisualObservationJob] | None = None,
        progress_callback: ProgressCallback | None = None,
        cancel_checker: CancelChecker | None = None,
    ) -> AgentResult:
        self._record_visual_observations(
            "ChatWorker",
            visual_observation_jobs or [],
            cancel_checker=cancel_checker,
        )
        check_cancelled(cancel_checker)
        debug_log(
            "ChatWorker",
            "开始处理用户消息",
            {
                "message_count": len(messages),
                "messages": summarize_messages(messages),
            },
        )
        return self.agent_runtime.handle_user_message(
            messages,
            progress_callback=progress_callback,
            cancel_checker=cancel_checker,
        )

    def run_confirmed_action(
        self,
        action: PendingToolAction,
        *,
        progress_callback: ProgressCallback | None = None,
        cancel_checker: CancelChecker | None = None,
    ) -> AgentResult:
        debug_log("ChatWorker", "开始处理已确认动作", action.to_dict())
        return self.agent_runtime.handle_confirmed_action(
            action,
            progress_callback=progress_callback,
            cancel_checker=cancel_checker,
        )

    def run_cancelled_action(
        self,
        action: PendingToolAction,
        *,
        cancel_checker: CancelChecker | None = None,
    ) -> AgentResult:
        check_cancelled(cancel_checker)
        debug_log("ChatWorker", "开始处理已取消动作", action.to_dict())
        return self.agent_runtime.handle_cancelled_action(action)

    def run_event(
        self,
        event: AgentEvent,
        *,
        visual_observation_jobs: list[VisualObservationJob] | None = None,
        progress_callback: ProgressCallback | None = None,
        cancel_checker: CancelChecker | None = None,
    ) -> AgentResult:
        visual_records = self._record_visual_observations(
            "EventWorker",
            visual_observation_jobs or [],
            cancel_checker=cancel_checker,
        )
        check_cancelled(cancel_checker)
        if visual_records:
            event = AgentEvent(
                type=event.type,
                payload={
                    **event.payload,
                    "visual_contexts": [
                        _visual_record_to_event_context(record)
                        for record in visual_records
                    ],
                },
            )
        debug_log(
            "EventWorker",
            "开始处理主动事件",
            {
                "type": event.type,
                "payload": event.payload,
            },
        )
        return self.agent_runtime.handle_event(
            event,
            progress_callback=progress_callback,
            cancel_checker=cancel_checker,
        )

    def _record_visual_observations(
        self,
        log_scope: str,
        visual_observation_jobs: list[VisualObservationJob],
        *,
        cancel_checker: CancelChecker | None = None,
    ) -> list[VisualObservationRecord]:
        if self.visual_observation_store is None or not visual_observation_jobs:
            return []
        records: list[VisualObservationRecord] = []
        for job in visual_observation_jobs:
            check_cancelled(cancel_checker)
            record = summarize_visual_observation(
                self.agent_runtime.api_client,
                job,
                cancel_checker=cancel_checker,
            )
            check_cancelled(cancel_checker)
            records.append(record)
            self.visual_observation_store.append(record)
            debug_log(
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
        return records


def _visual_record_to_event_context(record: VisualObservationRecord) -> dict[str, Any]:
    return {
        "visual_id": record.id,
        "source": record.source,
        "created_at": record.created_at,
        "screen_name": record.screen_name,
        "summary": record.summary,
        "visible_texts": record.visible_texts[:12],
        "uncertain_texts": record.uncertain_texts[:6],
        "notable_elements": record.notable_elements[:10],
        "confidence": record.confidence,
        "sensitive_redacted": record.sensitive_redacted,
    }
