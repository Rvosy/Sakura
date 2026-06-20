from __future__ import annotations

from collections.abc import Callable
from typing import Any

from app.agent import AgentEvent, AgentProgress, AgentResult, AgentRuntime, PendingToolAction
from app.config.models import MODEL_SLOT_VISUAL_CONTEXT
from app.core.cancellation import CancelChecker, check_cancelled
from app.core.debug_log import debug_log, summarize_messages
from app.llm.api_client import messages_contain_image
from app.sensory.models import SensoryRequest, SensorySource
from app.sensory.pipeline import SensoryPipeline
from app.storage.visual_observation import (
    VisualObservationJob,
    VisualObservationRecord,
    VisualObservationStore,
    build_visual_context_message,
    fallback_visual_observation_record,
    summarize_visual_observation,
    visual_observation_media_refs,
    visual_observation_record_from_summary,
    visual_record_from_sensory_observation,
)


ProgressCallback = Callable[[AgentProgress], None]


class ChatPipeline:
    """封装对话运行管线，让 Qt Worker 只保留线程和信号职责。"""

    def __init__(
        self,
        agent_runtime: AgentRuntime,
        visual_observation_store: VisualObservationStore | None = None,
        sensory_pipeline: SensoryPipeline | None = None,
    ) -> None:
        self.agent_runtime = agent_runtime
        self.visual_observation_store = visual_observation_store
        self.sensory_pipeline = sensory_pipeline or getattr(agent_runtime, "sensory_pipeline", None)

    def run_user_message(
        self,
        messages: list[dict[str, Any]],
        *,
        visual_observation_jobs: list[VisualObservationJob] | None = None,
        progress_callback: ProgressCallback | None = None,
        cancel_checker: CancelChecker | None = None,
    ) -> AgentResult:
        visual_observation_jobs = visual_observation_jobs or []
        visual_summary_jobs = _visual_summary_jobs(visual_observation_jobs)
        visual_records = self._record_visual_observations(
            "ChatWorker",
            visual_summary_jobs,
            cancel_checker=cancel_checker,
        )
        check_cancelled(cancel_checker)
        if visual_records and _should_inject_visual_context(messages, visual_summary_jobs):
            context_message = build_visual_context_message(
                _latest_user_text(messages),
                visual_records,
            )
            if context_message is not None:
                messages = [*messages, context_message]
                debug_log(
                    "ChatWorker",
                    "视觉摘要已作为纯文本上下文注入",
                    {
                        "visual_ids": [record.id for record in visual_records],
                        "message_count": len(messages),
                    },
                )
        debug_log(
            "ChatWorker",
            "开始处理用户消息",
            {
                "message_count": len(messages),
                "visual_jobs": len(visual_observation_jobs or []),
                "messages": summarize_messages(messages),
            },
        )
        result = self.agent_runtime.handle_user_message(
            messages,
            progress_callback=progress_callback,
            cancel_checker=cancel_checker,
        )
        self._record_visual_observation_from_result(
            "ChatWorker",
            _visual_result_jobs(visual_observation_jobs),
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
        visual_observation_jobs = visual_observation_jobs or []
        visual_summary_jobs = _event_visual_summary_jobs(
            visual_observation_jobs,
            self.sensory_pipeline,
        )
        visual_records = self._record_visual_observations(
            "EventWorker",
            visual_summary_jobs,
            cancel_checker=cancel_checker,
        )
        check_cancelled(cancel_checker)
        if visual_records:
            event = _event_with_visual_contexts(event, visual_records)
            debug_log(
                "EventWorker",
                "视觉摘要已作为主动事件上下文注入",
                {
                    "visual_ids": [record.id for record in visual_records],
                    "event_type": event.type,
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
        result = self.agent_runtime.handle_event(
            event,
            progress_callback=progress_callback,
            cancel_checker=cancel_checker,
        )
        self._record_visual_observation_from_result(
            "EventWorker",
            [
                job
                for job in visual_observation_jobs
                if job not in visual_summary_jobs
            ],
            result,
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
            debug_log(log_scope, "视觉观察摘要缺失，跳过保存", {"visual_jobs": len(visual_observation_jobs)})
            return
        record = visual_observation_record_from_summary(
            visual_observation_jobs[0],
            result.visual_observation,
        )
        if record is None:
            debug_log(log_scope, "视觉观察摘要为空，跳过保存", {"visual_jobs": len(visual_observation_jobs)})
            return
        self.visual_observation_store.append(record)
        if self.sensory_pipeline is not None:
            self.sensory_pipeline.record_visual_observation(record)
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
            record = self._summarize_visual_observation_job(
                job,
                cancel_checker=cancel_checker,
            )
            check_cancelled(cancel_checker)
            records.append(record)
            self.visual_observation_store.append(record)
            if self.sensory_pipeline is not None and not job.use_sensory_provider:
                self.sensory_pipeline.record_visual_observation(record)
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

    def _summarize_visual_observation_job(
        self,
        job: VisualObservationJob,
        *,
        cancel_checker: CancelChecker | None = None,
    ) -> VisualObservationRecord:
        if not job.use_sensory_provider:
            return summarize_visual_observation(
                _visual_summary_client(self.agent_runtime),
                job,
                cancel_checker=cancel_checker,
            )
        refs = visual_observation_media_refs(job)
        if not refs:
            return fallback_visual_observation_record(job, "增强视觉摘要生成失败：没有可用截图。")
        if self.sensory_pipeline is None:
            return fallback_visual_observation_record(job, "增强视觉摘要生成失败：未配置增强感知管线。")
        debug_log(
            "ChatWorker",
            "开始调用增强视觉模型生成截图摘要",
            {
                "visual_id": job.id,
                "source": job.source,
                "media_count": len(refs),
            },
        )
        observation = self.sensory_pipeline.observe(
            SensoryRequest(
                id=f"req_{job.id}",
                source=SensorySource.VISION,
                user_text=job.user_text,
                event_type=job.source,
                text=job.user_text,
                media_ref=refs[0],
                metadata={
                    "visual_id": job.id,
                    "visual_source": job.source,
                    "image_urls": refs,
                    "screen_context_count": len(job.screen_contexts or []) or 1,
                },
            )
        )
        check_cancelled(cancel_checker)
        if observation is None:
            return fallback_visual_observation_record(job, "增强视觉摘要生成失败：视觉感官模型不可用。")
        return visual_record_from_sensory_observation(job, observation)


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


def _event_with_visual_contexts(
    event: AgentEvent,
    records: list[VisualObservationRecord],
) -> AgentEvent:
    payload = dict(event.payload)
    existing_contexts = payload.get("visual_contexts")
    visual_contexts = (
        [dict(item) for item in existing_contexts if isinstance(item, dict)]
        if isinstance(existing_contexts, list)
        else []
    )
    visual_contexts.extend(_visual_record_to_event_context(record) for record in records)
    payload["visual_contexts"] = visual_contexts
    return AgentEvent(type=event.type, payload=payload)


def _visual_summary_jobs(
    visual_observation_jobs: list[VisualObservationJob],
) -> list[VisualObservationJob]:
    return [
        job
        for job in visual_observation_jobs
        if job.use_sensory_provider or job.inject_as_context
    ]


def _visual_result_jobs(
    visual_observation_jobs: list[VisualObservationJob],
) -> list[VisualObservationJob]:
    return [
        job
        for job in visual_observation_jobs
        if not (job.use_sensory_provider or job.inject_as_context)
    ]


def _event_visual_summary_jobs(
    visual_observation_jobs: list[VisualObservationJob],
    sensory_pipeline: SensoryPipeline | None,
) -> list[VisualObservationJob]:
    if _sensory_visual_mirroring_enabled(sensory_pipeline):
        return [*visual_observation_jobs]
    return _visual_summary_jobs(visual_observation_jobs)


def _sensory_visual_mirroring_enabled(
    sensory_pipeline: SensoryPipeline | None,
) -> bool:
    if sensory_pipeline is None:
        return False
    settings = sensory_pipeline.settings.normalized()
    return bool(
        settings.enabled
        and settings.sources[SensorySource.VISION].context_enabled
    )


def _visual_summary_client(agent_runtime: AgentRuntime) -> Any:
    client_for_slot = getattr(agent_runtime, "api_client_for_slot", None)
    if callable(client_for_slot):
        return client_for_slot(MODEL_SLOT_VISUAL_CONTEXT)
    return agent_runtime.api_client


def _should_inject_visual_context(
    messages: list[dict[str, Any]],
    visual_observation_jobs: list[VisualObservationJob],
) -> bool:
    return any(job.inject_as_context for job in visual_observation_jobs) or not messages_contain_image(messages)


def _latest_user_text(messages: list[dict[str, Any]]) -> str:
    for message in reversed(messages):
        if message.get("role") != "user":
            continue
        content = message.get("content")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts: list[str] = []
            for part in content:
                if isinstance(part, dict) and part.get("type") == "text":
                    parts.append(str(part.get("text") or ""))
            return "\n".join(part for part in parts if part).strip()
    return ""
