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
    build_visual_context_message,
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
        # 步骤 1: 视觉模型预处理 → 结构化 JSON 摘要
        visual_records = self._record_visual_observations(
            "ChatWorker",
            visual_observation_jobs or [],
            cancel_checker=cancel_checker,
        )
        check_cancelled(cancel_checker)

        # 步骤 2: 如果有关联的视觉摘要，剥离图片、注入摘要，交给文本模型做角色扮演
        if visual_records:
            user_text = _extract_user_text(messages)
            context_message = build_visual_context_message(user_text, visual_records)
            messages = _strip_images_from_messages(messages)
            if context_message is not None:
                messages = _inject_context_message(messages, context_message)

        debug_log(
            "ChatWorker",
            "开始处理用户消息",
            {
                "message_count": len(messages),
                "visual_records": len(visual_records),
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
            # 视觉预处理优先使用 vision_api_client，没有则回退到主 api_client
            vision_client = (
                self.agent_runtime.vision_api_client
                or self.agent_runtime.api_client
            )
            record = summarize_visual_observation(
                vision_client,
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


def _extract_user_text(messages: list[dict[str, Any]]) -> str:
    """从消息列表中提取最后一条 user 消息的纯文本。"""
    for msg in reversed(messages):
        if msg.get("role") == "user":
            content = msg.get("content", "")
            if isinstance(content, str):
                return content
            if isinstance(content, list):
                parts = [p.get("text", "") for p in content if isinstance(p, dict) and p.get("type") == "text"]
                return " ".join(parts)
    return ""


def _strip_images_from_messages(
    messages: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """从消息列表中移除所有 image_url 内容块，只保留文本部分。"""
    stripped: list[dict[str, Any]] = []
    for msg in messages:
        content = msg.get("content")
        if isinstance(content, list):
            text_parts = [
                p for p in content
                if isinstance(p, dict) and p.get("type") != "image_url"
            ]
            if text_parts:
                stripped.append({**msg, "content": text_parts})
            # 如果所有内容都是图片，则跳过整条消息
        else:
            stripped.append(dict(msg))
    return stripped


def _inject_context_message(
    messages: list[dict[str, Any]],
    context_message: dict[str, str],
) -> list[dict[str, Any]]:
    """将视觉摘要作为 system 上下文注入消息列表，插在历史与最后一条用户消息之间。"""
    if not messages:
        return [context_message]
    return [*messages[:-1], context_message, messages[-1]]
