from __future__ import annotations

import time
from typing import Any

from PySide6.QtCore import QObject, Signal, Slot

from app.agent import AgentEvent, AgentProgress, AgentResult, AgentRuntime, PendingToolAction
from app.core.chat_pipeline import ChatPipeline
from app.core.cancellation import CancellationToken, OperationCancelled
from app.core.debug_log import debug_log
from app.core.interaction import set_interaction_id
from app.storage.visual_observation import (
    VisualObservationJob,
    VisualObservationStore,
)


class ChatWorker(QObject):
    finished = Signal(object)
    failed = Signal(str)
    progress = Signal(object)
    cancelled = Signal()

    def __init__(
        self,
        agent_runtime: AgentRuntime,
        messages: list[dict[str, Any]] | None = None,
        confirmed_action: PendingToolAction | None = None,
        cancelled_action: PendingToolAction | None = None,
        visual_observation_store: VisualObservationStore | None = None,
        visual_observation_jobs: list[VisualObservationJob] | None = None,
        interaction_id: str = "",
    ) -> None:
        super().__init__()
        self.agent_runtime = agent_runtime
        self.messages = messages or []
        self.confirmed_action = confirmed_action
        self.cancelled_action = cancelled_action
        self.visual_observation_store = visual_observation_store
        self.visual_observation_jobs = visual_observation_jobs or []
        self.interaction_id = interaction_id
        self.pipeline = ChatPipeline(
            agent_runtime,
            visual_observation_store=visual_observation_store,
        )
        self._cancel_token = CancellationToken()

    @Slot()
    def cancel(self) -> None:
        self._cancel_token.cancel()

    @Slot()
    def run(self) -> None:
        # 跨线程恢复交互 ID：worker 在独立 QThread 中执行，ContextVar 不自动传播
        set_interaction_id(self.interaction_id)
        started_at = time.perf_counter()
        try:
            self._cancel_token.throw_if_cancelled()
            if self.confirmed_action is not None:
                result: AgentResult = self.pipeline.run_confirmed_action(
                    self.confirmed_action,
                    progress_callback=self._emit_progress,
                    cancel_checker=self._cancel_token.throw_if_cancelled,
                )
            elif self.cancelled_action is not None:
                result = self.pipeline.run_cancelled_action(
                    self.cancelled_action,
                    cancel_checker=self._cancel_token.throw_if_cancelled,
                )
            else:
                result = self.pipeline.run_user_message(
                    self.messages,
                    visual_observation_jobs=self.visual_observation_jobs,
                    progress_callback=self._emit_progress,
                    cancel_checker=self._cancel_token.throw_if_cancelled,
                )
            self._cancel_token.throw_if_cancelled()
        except OperationCancelled:
            debug_log(
                "ChatWorker",
                "处理已取消",
                {"elapsed_ms": int((time.perf_counter() - started_at) * 1000)},
            )
            self.cancelled.emit()
            return
        except Exception as exc:  # UI 边界统一转成可读错误。
            if self._cancel_token.is_cancelled():
                self.cancelled.emit()
                return
            debug_log(
                "ChatWorker",
                "处理失败",
                {
                    "error": str(exc),
                    "elapsed_ms": int((time.perf_counter() - started_at) * 1000),
                },
            )
            self.failed.emit(str(exc))
            return
        debug_log(
            "ChatWorker",
            "处理完成",
            {
                "segments": len(result.reply.segments),
                "actions": [action.type for action in result.actions],
                "elapsed_ms": int((time.perf_counter() - started_at) * 1000),
            },
        )
        self.finished.emit(result)

    def _emit_progress(self, progress: AgentProgress) -> None:
        self._cancel_token.throw_if_cancelled()
        debug_log(
            "ChatWorker",
            "转发中间回复",
            {
                "stage": progress.stage,
                "segments": len(progress.reply.segments),
                "metadata": progress.metadata,
            },
        )
        self._cancel_token.throw_if_cancelled()
        self.progress.emit(progress)


class EventWorker(QObject):
    finished = Signal(object)
    failed = Signal(str)
    progress = Signal(object)
    cancelled = Signal()

    def __init__(
        self,
        agent_runtime: AgentRuntime,
        event: AgentEvent,
        interaction_id: str = "",
    ) -> None:
        super().__init__()
        self.agent_runtime = agent_runtime
        # 避免覆盖 QObject.event() 虚函数名；PySide 在 moveToThread 时会访问该方法。
        self.agent_event = event
        self.visual_observation_store: VisualObservationStore | None = None
        self.visual_observation_jobs: list[VisualObservationJob] = []
        self.interaction_id = interaction_id
        self._cancel_token = CancellationToken()

    @Slot()
    def cancel(self) -> None:
        self._cancel_token.cancel()

    @Slot()
    def run(self) -> None:
        set_interaction_id(self.interaction_id)
        started_at = time.perf_counter()
        try:
            self._cancel_token.throw_if_cancelled()
            pipeline = ChatPipeline(
                self.agent_runtime,
                visual_observation_store=self.visual_observation_store,
            )
            result = pipeline.run_event(
                self.agent_event,
                visual_observation_jobs=self.visual_observation_jobs,
                progress_callback=self._emit_progress,
                cancel_checker=self._cancel_token.throw_if_cancelled,
            )
            self._cancel_token.throw_if_cancelled()
        except OperationCancelled:
            debug_log(
                "EventWorker",
                "处理已取消",
                {"elapsed_ms": int((time.perf_counter() - started_at) * 1000)},
            )
            self.cancelled.emit()
            return
        except Exception as exc:  # 主动事件同样在 UI 边界转成可读错误。
            if self._cancel_token.is_cancelled():
                self.cancelled.emit()
                return
            debug_log(
                "EventWorker",
                "处理失败",
                {
                    "error": str(exc),
                    "elapsed_ms": int((time.perf_counter() - started_at) * 1000),
                },
            )
            self.failed.emit(str(exc))
            return
        debug_log(
            "EventWorker",
            "处理完成",
            {
                "segments": len(result.reply.segments),
                "elapsed_ms": int((time.perf_counter() - started_at) * 1000),
            },
        )
        self.finished.emit(result)

    def _emit_progress(self, progress: AgentProgress) -> None:
        self._cancel_token.throw_if_cancelled()
        debug_log(
            "EventWorker",
            "转发中间回复",
            {
                "stage": progress.stage,
                "segments": len(progress.reply.segments),
                "metadata": progress.metadata,
            },
        )
        self._cancel_token.throw_if_cancelled()
        self.progress.emit(progress)
