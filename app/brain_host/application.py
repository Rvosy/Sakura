"""无 Qt Brain Host 应用装配与系统方法。"""

from __future__ import annotations

import secrets
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from app.brain_host.dto import (
    agent_progress_dto,
    chat_reply_dto,
    pending_action_dto,
    startup_state_dto,
)
from app.brain_host.errors import BrainHostError
from app.brain_host.protocol import PROTOCOL_VERSION


ContextBuilder = Callable[[Path], Any]
EventSink = Callable[[str, dict[str, Any]], None]


@dataclass(frozen=True)
class BrainHostConfig:
    base_dir: Path
    session_id: str
    session_credential: str
    protocol_version: int = PROTOCOL_VERSION

    def __post_init__(self) -> None:
        object.__setattr__(self, "base_dir", Path(self.base_dir).resolve())
        if not self.base_dir.is_dir():
            raise BrainHostError("INVALID_STARTUP_CONFIG", "base_dir must be an existing directory")
        if not self.session_id.strip():
            raise BrainHostError("INVALID_STARTUP_CONFIG", "session_id is required")
        if not self.session_credential.strip():
            raise BrainHostError("INVALID_STARTUP_CONFIG", "session credential is required")
        if self.protocol_version != PROTOCOL_VERSION:
            raise BrainHostError("PROTOCOL_VERSION_UNSUPPORTED", "unsupported protocol version")

    @classmethod
    def from_environment(cls, environment: Mapping[str, str]) -> "BrainHostConfig":
        base_dir = environment.get("SAKURA_BASE_DIR", "").strip()
        session_id = environment.get("SAKURA_SESSION_ID", "").strip()
        credential = environment.get("SAKURA_SESSION_CREDENTIAL", "").strip()
        protocol_text = environment.get("SAKURA_PROTOCOL_VERSION", str(PROTOCOL_VERSION)).strip()
        try:
            protocol_version = int(protocol_text)
        except ValueError as exc:
            raise BrainHostError(
                "INVALID_STARTUP_CONFIG",
                "protocol version must be an integer",
            ) from exc
        if not base_dir:
            raise BrainHostError("INVALID_STARTUP_CONFIG", "base_dir is required")
        return cls(Path(base_dir), session_id, credential, protocol_version)


class BrainHostApplication:
    def __init__(
        self,
        config: BrainHostConfig,
        *,
        context_builder: ContextBuilder | None = None,
    ) -> None:
        self.config = config
        self._context_builder = context_builder or _build_context
        self.context: Any | None = None
        self.assistant: Any | None = None
        self.tts_service: Any | None = None
        self.backchannel_service: Any | None = None
        self.scheduler: Any | None = None
        self.state = "starting"
        self.startup: dict[str, Any] | None = None
        self.initialization_error: BrainHostError | None = None
        self._event_sink: EventSink | None = None
        self._messages: list[dict[str, Any]] = []
        self._state_lock = threading.RLock()
        self._watchers: set[threading.Thread] = set()
        self._backchannel_audio_requests: dict[str, dict[str, Any]] = {}
        self._manual_observations: dict[str, tuple[Any, str]] = {}
        self._capture_session_id: str | None = None
        self._pending_capture_request_id: str | None = None
        self._background_event_kind: str | None = None
        self._screen_awareness_enabled = False
        self._screen_context_resolution = "fullscreen"
        self._screen_contexts: list[dict[str, Any]] = []
        self._screen_batch_started_at: float | None = None
        self._screen_context_dropped_count = 0
        self._last_user_activity_at = time.monotonic()

    def set_event_sink(self, sink: EventSink | None) -> None:
        with self._state_lock:
            self._event_sink = sink
        if sink is not None and self.state == "ready" and self.scheduler is not None:
            self.scheduler.start()

    def shutdown(self) -> dict[str, Any]:
        return self._shutdown()

    def initialize(self) -> dict[str, Any] | None:
        if self.state == "ready":
            return self.startup
        try:
            self.context = self._context_builder(self.config.base_dir)
            self.tts_service = _build_tts_service(self.context, self.config.base_dir)
            self.backchannel_service = _build_backchannel_service(
                self.context,
                self.config.base_dir,
                self._handle_backchannel_choice,
            )
            self.startup = startup_state_dto(self.context)
            runtime = self.startup.setdefault("runtime", {})
            runtime["tts_ready"] = bool(getattr(self.tts_service, "service_ready", False))
            runtime["tts_enabled"] = type(self.tts_service).__name__ != "NullTTSSynthesisService"
            if hasattr(self.context, "agent_runtime"):
                from app.brain_host.scheduler import PeriodicScheduler
                from app.core.assistant_service import AssistantApplication
                from app.core.chat_pipeline import ChatPipeline

                self.assistant = AssistantApplication(
                    ChatPipeline(
                        self.context.agent_runtime,
                        visual_observation_store=getattr(
                            self.context,
                            "visual_observation_store",
                            None,
                        ),
                    ),
                    session_id=self.config.session_id,
                )
                self.scheduler = PeriodicScheduler()
        except Exception as exc:  # noqa: BLE001
            self.state = "failed"
            self.initialization_error = BrainHostError(
                "BACKEND_INITIALIZATION_FAILED",
                "Brain Host initialization failed",
                retryable=True,
                details={"error_type": type(exc).__name__},
            )
            return None
        self.state = "ready"
        self._configure_screen_observation_runtime()
        self.sync_scheduler_jobs(start=False)
        return self.startup

    def handle_request(
        self,
        method: str,
        payload: Mapping[str, Any],
        *,
        request_id: str | None = None,
    ) -> dict[str, Any]:
        if method == "system.hello":
            return self._hello(payload)
        if method == "system.health":
            return self._health()
        if method == "system.shutdown":
            return self._shutdown()
        if method == "chat.send":
            return self._chat_send(payload, request_id=request_id)
        if method == "chat.cancel":
            return self._chat_cancel(payload)
        if method == "chat.confirm_action":
            return self._chat_confirm_action(payload, request_id=request_id)
        if method == "chat.reject_action":
            return self._chat_reject_action(payload, request_id=request_id)
        if method == "observation.capture_started":
            return self._observation_capture_started()
        if method == "observation.capture_cancelled":
            return self._observation_capture_cancelled(payload)
        if method == "observation.capture_failed":
            return self._observation_capture_failed(payload)
        if method == "observation.push":
            return self._observation_push(payload)
        if method == "observation.configure":
            return self._observation_configure(payload)
        if method == "tts.synthesize":
            return self._tts_synthesize(payload, request_id=request_id)
        if method == "tts.cancel":
            return self._tts_cancel(payload)
        raise BrainHostError("METHOD_NOT_FOUND", f"Unknown Brain Host method: {method}")

    def _tts_synthesize(
        self,
        payload: Mapping[str, Any],
        *,
        request_id: str | None,
    ) -> dict[str, Any]:
        text = payload.get("text")
        if not isinstance(text, str) or not text.strip():
            raise BrainHostError("INVALID_REQUEST", "TTS 文本不能为空。")
        service = self.tts_service
        if service is None:
            raise BrainHostError(
                "TTS_UNAVAILABLE",
                "TTS 合成服务尚未准备好。",
                retryable=True,
            )
        tone = payload.get("tone")
        if tone is not None and not isinstance(tone, str):
            raise BrainHostError("INVALID_REQUEST", "TTS tone 必须是字符串。")
        segment_id = payload.get("segment_id", payload.get("segmentId", ""))
        if not isinstance(segment_id, str):
            raise BrainHostError("INVALID_REQUEST", "segmentId 必须是字符串。")
        audio_key = payload.get("audio_key", payload.get("audioKey", ""))
        if not isinstance(audio_key, str):
            raise BrainHostError("INVALID_REQUEST", "audioKey 必须是字符串。")
        with self._state_lock:
            audio_context = self._backchannel_audio_requests.pop(audio_key.strip(), None)
        try:
            source = audio_context.get("source") if audio_context is not None else None
            if isinstance(source, Path) and source.is_file():
                handle = service.adopt_audio(
                    source,
                    text=text.strip(),
                    tone=tone,
                    request_id=None,
                )
            else:
                handle = service.synthesize(text.strip(), tone, request_id=None)
        except Exception as exc:  # noqa: BLE001
            raise BrainHostError(
                "TTS_SYNTHESIS_REJECTED",
                "TTS 合成请求未能提交。",
                retryable=True,
                details={"error_type": type(exc).__name__},
            ) from exc
        self._watch_tts(
            handle,
            ipc_request_id=request_id or handle.request_id,
            segment_id=segment_id.strip(),
            audio_context=audio_context,
        )
        return {"version": 1, "synthesisId": handle.request_id}

    def _tts_cancel(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        synthesis_id = _required_id(payload, "synthesis_id", "synthesisId")
        service = self.tts_service
        cancel = getattr(service, "cancel", None)
        if not callable(cancel) or not cancel(synthesis_id):
            raise BrainHostError(
                "TTS_REQUEST_NOT_FOUND",
                "当前会话中没有可取消的 TTS 请求。",
            )
        return {"version": 1, "synthesisId": synthesis_id, "cancelled": True}

    def _watch_tts(
        self,
        handle: Any,
        *,
        ipc_request_id: str,
        segment_id: str,
        audio_context: dict[str, Any] | None,
    ) -> None:
        watcher = threading.Thread(
            target=self._wait_for_tts,
            args=(handle, ipc_request_id, segment_id, audio_context),
            name=f"sakura-tts-watch-{handle.request_id[-8:]}",
            daemon=True,
        )
        with self._state_lock:
            self._watchers.add(watcher)
        watcher.start()

    def _wait_for_tts(
        self,
        handle: Any,
        ipc_request_id: str,
        segment_id: str,
        audio_context: dict[str, Any] | None,
    ) -> None:
        from app.voice.tts_synthesis_service import (
            TTSSynthesisCancelled,
            TTSSynthesisClosed,
        )

        try:
            result = handle.result()
        except (TTSSynthesisCancelled, TTSSynthesisClosed):
            self._publish(
                "tts.cancelled",
                {
                    "version": 1,
                    "synthesisId": handle.request_id,
                    "requestId": ipc_request_id,
                    "segmentId": segment_id,
                },
            )
        except Exception as exc:  # noqa: BLE001
            self._publish(
                "tts.error",
                {
                    "version": 1,
                    "synthesisId": handle.request_id,
                    "requestId": ipc_request_id,
                    "segmentId": segment_id,
                    "error": {
                        "code": "TTS_SYNTHESIS_FAILED",
                        "message": "语音合成失败，字幕仍会继续显示。",
                        "retryable": True,
                        "details": {"errorType": type(exc).__name__},
                    },
                },
            )
        else:
            if result.resource is not None and audio_context is not None:
                cache = audio_context.get("cache")
                store = getattr(cache, "store", None)
                if callable(store):
                    store(
                        str(audio_context.get("tone", "")),
                        str(audio_context.get("text", "")),
                        result.resource.path,
                    )
            resource = result.resource.to_private_dto() if result.resource is not None else None
            published = self._publish(
                "tts.audio_ready",
                {
                    "version": 1,
                    "synthesisId": handle.request_id,
                    "requestId": ipc_request_id,
                    "segmentId": segment_id,
                    "resource": resource,
                    "skippedReason": result.skipped_reason,
                },
            )
            if not published and result.resource is not None:
                try:
                    result.resource.path.unlink(missing_ok=True)
                except OSError:
                    pass
        finally:
            with self._state_lock:
                self._watchers.discard(threading.current_thread())

    def _chat_send(
        self,
        payload: Mapping[str, Any],
        *,
        request_id: str | None,
    ) -> dict[str, Any]:
        text = payload.get("text")
        if not isinstance(text, str):
            raise BrainHostError("INVALID_REQUEST", "聊天消息必须是字符串。")
        observation_id = payload.get("observation_id", payload.get("observationId"))
        if observation_id is not None and not isinstance(observation_id, str):
            raise BrainHostError("INVALID_REQUEST", "observationId 必须是字符串。")
        observation_id = str(observation_id or "").strip()
        if not text.strip() and not observation_id:
            raise BrainHostError("INVALID_REQUEST", "聊天消息不能为空。")
        assistant = self._require_assistant()
        user_text = text.strip() or "请看看这张截图。"
        with self._state_lock:
            if self._capture_session_id or self._pending_capture_request_id or self._background_event_kind:
                raise BrainHostError(
                    "ASSISTANT_BUSY",
                    "截图或主动事件正在处理中，请稍后再发送消息。",
                    retryable=True,
                )
            from app.llm.context_trimming import trim_messages_for_model
            from app.storage.visual_observation import VisualObservationJob

            request_user_message: dict[str, Any] = {"role": "user", "content": user_text}
            recorded_user_text = user_text
            visual_jobs: list[VisualObservationJob] = []
            if observation_id:
                stored = self._manual_observations.get(observation_id)
                if stored is None:
                    raise BrainHostError(
                        "OBSERVATION_NOT_FOUND",
                        "该截图已使用、已过期或不属于当前会话。",
                    )
                observation, visual_id = stored
                from app.agent.screen_observation import (
                    append_manual_observation_marker,
                    build_screen_observation_user_message,
                )

                request_user_message = build_screen_observation_user_message(user_text, observation)
                recorded_user_text = append_manual_observation_marker(
                    user_text,
                    observation,
                    visual_id,
                )
                visual_jobs.append(
                    VisualObservationJob(
                        id=visual_id,
                        source="manual_selection",
                        user_text=user_text,
                        observation=observation,
                    )
                )

            request_messages = trim_messages_for_model(
                [*self._messages, request_user_message]
            )
            handle = self._submit_with_progress(
                lambda progress_callback: assistant.send_message(
                    request_messages,
                    visual_observation_jobs=visual_jobs,
                    progress_callback=progress_callback,
                    request_id=request_id,
                )
            )
            if observation_id:
                self._manual_observations.pop(observation_id, None)
            self._clear_screen_context_batch_locked()
            self._last_user_activity_at = time.monotonic()
            self._messages.append({"role": "user", "content": recorded_user_text})
            self._record_history("user", recorded_user_text)
            self._watch_interaction(handle, source="chat")
            if self.backchannel_service is not None:
                self.backchannel_service.schedule(user_text)
        return self._accepted_interaction(handle)

    def _observation_capture_started(self) -> dict[str, Any]:
        self._require_assistant()
        with self._state_lock:
            if self._assistant_busy_locked():
                raise BrainHostError(
                    "ASSISTANT_BUSY",
                    "聊天、截图或主动事件正在处理中。",
                    retryable=True,
                )
            self._manual_observations.clear()
            self._last_user_activity_at = time.monotonic()
            self._capture_session_id = f"capture-{secrets.token_hex(16)}"
            capture_session_id = self._capture_session_id
        self._publish_busy(True, "capture")
        return {"version": 1, "captureSessionId": capture_session_id}

    def _observation_capture_cancelled(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        capture_session_id = _required_id(payload, "capture_session_id", "captureSessionId")
        with self._state_lock:
            if capture_session_id != self._capture_session_id:
                raise BrainHostError("CAPTURE_SESSION_NOT_FOUND", "截图会话已失效。")
            self._capture_session_id = None
        self._publish_busy(False, "capture")
        return {"version": 1, "captureSessionId": capture_session_id, "cancelled": True}

    def _observation_capture_failed(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        capture_request_id = _required_id(
            payload,
            "capture_request_id",
            "captureRequestId",
        )
        with self._state_lock:
            if capture_request_id != self._pending_capture_request_id:
                raise BrainHostError("CAPTURE_REQUEST_NOT_FOUND", "主动截图请求已失效。")
            self._pending_capture_request_id = None
        self._publish_busy(False, "screen_awareness")
        return {"version": 1, "captureRequestId": capture_request_id, "failed": True}

    def _observation_push(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        source = payload.get("source")
        resource = payload.get("resource")
        if source not in {"manual", "screen_awareness"} or not isinstance(resource, Mapping):
            raise BrainHostError("INVALID_REQUEST", "截图来源或资源描述无效。")
        from app.agent.screen_observation import build_screen_observation_from_private_resource

        try:
            observation = build_screen_observation_from_private_resource(
                resource,
                base_dir=self.config.base_dir,
            )
        except (OSError, ValueError) as exc:
            raise BrainHostError(
                "CAPTURE_RESOURCE_REJECTED",
                "截图资源未通过校验。",
                details={"errorType": type(exc).__name__},
            ) from exc
        if source == "manual":
            capture_session_id = _required_id(
                payload,
                "capture_session_id",
                "captureSessionId",
            )
            with self._state_lock:
                if capture_session_id != self._capture_session_id:
                    raise BrainHostError("CAPTURE_SESSION_NOT_FOUND", "截图会话已失效。")
                from app.storage.visual_observation import generate_visual_observation_id

                observation_id = f"observation-{secrets.token_hex(16)}"
                visual_id = generate_visual_observation_id()
                self._manual_observations[observation_id] = (observation, visual_id)
                self._capture_session_id = None
            self._publish_busy(False, "capture")
            return {
                "version": 1,
                "observationId": observation_id,
                "width": observation.width,
                "height": observation.height,
                "screenName": observation.screen_name,
            }

        capture_request_id = _required_id(
            payload,
            "capture_request_id",
            "captureRequestId",
        )
        with self._state_lock:
            if capture_request_id != self._pending_capture_request_id:
                raise BrainHostError("CAPTURE_REQUEST_NOT_FOUND", "主动截图请求已失效。")
            self._pending_capture_request_id = None
            now = time.monotonic()
            context = {
                "data_url": observation.data_url,
                "width": observation.width,
                "height": observation.height,
                "captured_at": observation.captured_at,
                "screen_name": observation.screen_name,
                "detail": "high",
            }
            if self._screen_batch_started_at is None:
                self._screen_batch_started_at = now
            self._screen_contexts.append(context)
            settings = self._screen_awareness_settings()
            while len(self._screen_contexts) > settings.screen_context_batch_limit:
                self._screen_contexts.pop(0)
                self._screen_context_dropped_count += 1
            should_dispatch = (
                now - self._screen_batch_started_at >= settings.cooldown_minutes * 60
            )
            contexts = [dict(item) for item in self._screen_contexts] if should_dispatch else []
            dropped_count = self._screen_context_dropped_count
            if should_dispatch:
                self._clear_screen_context_batch_locked()
        if should_dispatch:
            dispatched = self._dispatch_screen_awareness(
                contexts,
                capture_request_id,
                dropped_count=dropped_count,
            )
        else:
            dispatched = False
            self._publish_busy(False, "screen_awareness")
        return {
            "version": 1,
            "captureRequestId": capture_request_id,
            "accepted": True,
            "dispatched": dispatched,
        }

    def _observation_configure(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        enabled = payload.get("enabled")
        if not isinstance(enabled, bool):
            raise BrainHostError("INVALID_REQUEST", "enabled 必须是布尔值。")
        with self._state_lock:
            self._screen_awareness_enabled = enabled
            if not enabled:
                self._pending_capture_request_id = None
                self._clear_screen_context_batch_locked()
        self.sync_scheduler_jobs(start=False)
        if not enabled:
            self._publish_busy(False, "screen_awareness")
        return {"version": 1, "screenAwarenessEnabled": enabled}

    def sync_scheduler_jobs(self, *, start: bool) -> None:
        scheduler = self.scheduler
        if scheduler is None:
            return
        scheduler.add_job("reminders", 1.0, self.check_due_reminders)
        if self._screen_awareness_enabled:
            settings = self._screen_awareness_settings()
            interval = settings.check_interval_minutes * 60
            scheduler.add_job(
                "screen-awareness",
                interval,
                self.request_screen_awareness_capture,
            )
        else:
            scheduler.remove_job("screen-awareness")
        if start:
            scheduler.start()

    def check_due_reminders(self) -> bool:
        if self.state != "ready" or self.context is None:
            return False
        with self._state_lock:
            if self._assistant_busy_locked():
                return False
        store = getattr(self.context, "reminder_store", None)
        due_reminders = getattr(store, "due_reminders", None)
        if not callable(due_reminders):
            return False
        try:
            reminders = due_reminders()
        except ValueError:
            return False
        if not reminders:
            return False
        reminder = reminders[0]
        reminder_id = str(reminder.get("id", "")).strip()
        if not reminder_id:
            return False
        from app.agent.actions import AgentEvent

        event = AgentEvent(
            "reminder_due",
            {
                "id": reminder_id,
                "text": str(reminder.get("text", "")),
                "trigger_at": str(reminder.get("trigger_at", "")),
            },
        )
        return self._dispatch_proactive_event(
            event,
            kind="reminder",
            event_id=reminder_id,
        )

    def request_screen_awareness_capture(self) -> bool:
        if self.state != "ready":
            return False
        with self._state_lock:
            if not self._screen_awareness_enabled or self._assistant_busy_locked():
                return False
            if (
                time.monotonic() - self._last_user_activity_at
                < self._screen_awareness_settings().check_interval_minutes * 60
            ):
                return False
            capture_request_id = f"capture-request-{secrets.token_hex(16)}"
            self._pending_capture_request_id = capture_request_id
            resolution = self._screen_context_resolution
        self._publish_busy(True, "screen_awareness")
        published = self._publish(
            "observation.capture_requested",
            {
                "version": 1,
                "captureRequestId": capture_request_id,
                "target": {"kind": "fullscreen"},
                "resolution": resolution,
            },
        )
        if not published:
            with self._state_lock:
                self._pending_capture_request_id = None
            self._publish_busy(False, "screen_awareness")
            return False
        return True

    def _dispatch_screen_awareness(
        self,
        contexts: list[dict[str, Any]],
        capture_request_id: str,
        *,
        dropped_count: int,
    ) -> bool:
        from app.agent.actions import AgentEvent
        from app.storage.visual_observation import (
            VisualObservationJob,
            generate_visual_observation_id,
        )

        recent_conversation = [
            dict(message)
            for message in self._messages[-12:]
            if message.get("role") in {"user", "assistant"}
            and isinstance(message.get("content"), str)
        ]
        event = AgentEvent(
            "screen_awareness_check",
            {
                "triggered_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                "screen_context_allowed": True,
                "screen_context_count": len(contexts),
                "screen_context_dropped_count": dropped_count,
                "screen_contexts": contexts,
                "recent_conversation": recent_conversation,
                "capture_request_id": capture_request_id,
            },
        )
        visual_id = generate_visual_observation_id()
        jobs = [
            VisualObservationJob(
                id=visual_id,
                source="screen_awareness_context",
                user_text="主动屏幕感知上下文",
                screen_contexts=contexts,
            )
        ]
        self._record_history("system", "[已抓取屏幕上下文]")
        return self._dispatch_proactive_event(
            event,
            kind="screen_awareness",
            event_id=capture_request_id,
            visual_observation_jobs=jobs,
            busy_already_published=True,
        )

    def _dispatch_proactive_event(
        self,
        event: Any,
        *,
        kind: str,
        event_id: str,
        visual_observation_jobs: list[Any] | None = None,
        busy_already_published: bool = False,
    ) -> bool:
        assistant = self._require_assistant()
        handle = assistant.dispatch_event(
            event,
            visual_observation_jobs=visual_observation_jobs,
        )
        if handle is None:
            if busy_already_published:
                self._publish_busy(False, kind)
            return False
        with self._state_lock:
            self._background_event_kind = kind
        if not busy_already_published:
            self._publish_busy(True, kind)
        watcher = threading.Thread(
            target=self._wait_for_proactive_event,
            args=(handle, kind, event_id),
            name=f"sakura-proactive-{handle.interaction_id[-8:]}",
            daemon=True,
        )
        with self._state_lock:
            self._watchers.add(watcher)
        watcher.start()
        return True

    def _wait_for_proactive_event(self, handle: Any, kind: str, event_id: str) -> None:
        try:
            result = handle.result()
        except Exception:  # noqa: BLE001
            if kind == "reminder":
                from app.llm.chat_reply import ChatReply, ChatSegment

                reminder = self._reminder_by_id(event_id)
                text = str(reminder.get("text", "")) if reminder else ""
                fallback = ChatReply(
                    [
                        ChatSegment(
                            ja=f"時間だよ。{text}",
                            zh=f"到时间了：{text}",
                            tone="请求",
                            portrait="伸手命令",
                        )
                    ]
                )
                self._record_assistant_reply(fallback)
                self._publish(
                    "assistant.proactive_message",
                    {
                        "version": 1,
                        "kind": kind,
                        "eventId": event_id,
                        "reply": chat_reply_dto(fallback),
                        "fallback": True,
                    },
                )
        else:
            self._record_completed_result(result)
            if result.reply.text.strip() or result.reply.translation.strip() or result.actions:
                self._publish(
                    "assistant.proactive_message",
                    {
                        "version": 1,
                        "kind": kind,
                        "eventId": event_id,
                        "reply": chat_reply_dto(result.reply),
                        "fallback": False,
                    },
                )
        finally:
            if kind == "reminder":
                self._complete_reminder(event_id)
            with self._state_lock:
                self._background_event_kind = None
                self._watchers.discard(threading.current_thread())
            self._publish_busy(False, kind)

    def _reminder_by_id(self, reminder_id: str) -> dict[str, Any] | None:
        store = getattr(self.context, "reminder_store", None)
        due_reminders = getattr(store, "due_reminders", None)
        if not callable(due_reminders):
            return None
        try:
            return next(
                (item for item in due_reminders() if str(item.get("id", "")) == reminder_id),
                None,
            )
        except ValueError:
            return None

    def _complete_reminder(self, reminder_id: str) -> None:
        store = getattr(self.context, "reminder_store", None)
        mark_completed = getattr(store, "mark_completed", None)
        if callable(mark_completed):
            try:
                mark_completed(reminder_id)
            except ValueError:
                pass

    def _assistant_busy_locked(self) -> bool:
        return bool(
            self._capture_session_id
            or self._pending_capture_request_id
            or self._background_event_kind
            or (self.assistant is not None and bool(getattr(self.assistant, "busy", False)))
        )

    def _clear_screen_context_batch_locked(self) -> None:
        self._screen_contexts.clear()
        self._screen_batch_started_at = None
        self._screen_context_dropped_count = 0

    def _publish_busy(self, busy: bool, kind: str) -> None:
        self._publish(
            "assistant.busy_changed",
            {"version": 1, "busy": bool(busy), "kind": kind},
        )

    def _screen_awareness_settings(self) -> Any:
        from app.agent.screen_awareness import ScreenAwarenessSettings

        settings = getattr(self.context, "screen_awareness_settings", None)
        return settings.normalized() if hasattr(settings, "normalized") else ScreenAwarenessSettings()

    def _configure_screen_observation_runtime(self) -> None:
        settings = self._screen_awareness_settings()
        settings_service = getattr(self.context, "settings_service", None)
        load_values = getattr(settings_service, "load_system_values", None)
        try:
            values = load_values("screen_observation") if callable(load_values) else {}
        except (OSError, ValueError):
            values = {}
        screen_enabled = bool(values.get("enabled", True))
        autonomous_enabled = bool(values.get("autonomous_enabled", True)) and screen_enabled
        runtime = getattr(self.context, "agent_runtime", None)
        set_vision = getattr(runtime, "set_model_vision_enabled", None)
        if callable(set_vision):
            set_vision(screen_enabled)
        set_autonomous = getattr(runtime, "set_autonomous_screen_observation_enabled", None)
        if callable(set_autonomous):
            set_autonomous(autonomous_enabled)
        self._screen_awareness_enabled = (
            settings.allows_screen_context() and screen_enabled and autonomous_enabled
        )
        self._screen_context_resolution = settings.screen_context_resolution

    def _chat_cancel(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        interaction_id = _required_id(payload, "interaction_id", "interactionId")
        assistant = self._require_assistant()
        if not assistant.cancel(interaction_id):
            raise BrainHostError(
                "INTERACTION_NOT_FOUND",
                "当前会话中没有可取消的聊天请求。",
            )
        return {"version": 1, "interactionId": interaction_id, "cancelled": True}

    def _chat_confirm_action(
        self,
        payload: Mapping[str, Any],
        *,
        request_id: str | None,
    ) -> dict[str, Any]:
        action_id = _required_id(payload, "action_id", "actionId")
        assistant = self._require_assistant()
        handle = self._submit_with_progress(
            lambda progress_callback: assistant.confirm_action(
                action_id,
                progress_callback=progress_callback,
                request_id=request_id,
            )
        )
        self._watch_interaction(handle, source="confirm_action")
        return self._accepted_interaction(handle)

    def _chat_reject_action(
        self,
        payload: Mapping[str, Any],
        *,
        request_id: str | None,
    ) -> dict[str, Any]:
        action_id = _required_id(payload, "action_id", "actionId")
        assistant = self._require_assistant()
        handle = self._submit_assistant(
            lambda: assistant.reject_action(action_id, request_id=request_id)
        )
        self._watch_interaction(handle, source="reject_action")
        return self._accepted_interaction(handle)

    def _require_assistant(self) -> Any:
        if self.state != "ready" or self.assistant is None:
            raise BrainHostError(
                "BACKEND_UNAVAILABLE",
                "Brain Host 尚未准备好，请稍后重试。",
                retryable=True,
                details={"state": self.state},
            )
        return self.assistant

    def _submit_assistant(self, submit: Callable[[], Any]) -> Any:
        from app.core.assistant_service import (
            AssistantBusyError,
            AssistantClosedError,
            PendingActionNotFound,
        )

        try:
            return submit()
        except AssistantBusyError as exc:
            raise BrainHostError(
                "ASSISTANT_BUSY",
                "上一轮对话尚未结束，请先等待或取消。",
                retryable=True,
            ) from exc
        except AssistantClosedError as exc:
            raise BrainHostError(
                "BACKEND_UNAVAILABLE",
                "Brain Host 已停止，请等待桌面端恢复。",
                retryable=True,
            ) from exc
        except PendingActionNotFound as exc:
            raise BrainHostError(
                "ACTION_NOT_FOUND",
                "该操作确认已失效、已处理或不属于当前会话。",
            ) from exc

    def _submit_with_progress(self, submit: Callable[[Callable[[Any], None]], Any]) -> Any:
        gate = threading.Lock()
        handle_ref: list[Any] = []
        buffered: list[Any] = []

        def progress_callback(progress: Any) -> None:
            with gate:
                if not handle_ref:
                    buffered.append(progress)
                    return
                handle = handle_ref[0]
            self._handle_progress(handle, progress)

        handle = self._submit_assistant(lambda: submit(progress_callback))
        with gate:
            handle_ref.append(handle)
            pending = tuple(buffered)
            buffered.clear()
        for progress in pending:
            self._handle_progress(handle, progress)
        return handle

    def _accepted_interaction(self, handle: Any) -> dict[str, Any]:
        return {
            "version": 1,
            "interactionId": handle.interaction_id,
            "requestId": handle.request_id,
        }

    def _handle_progress(self, handle: Any, progress: Any) -> None:
        self._cancel_backchannel()
        self._record_assistant_reply(progress.reply)
        self._publish(
            "chat.progress",
            agent_progress_dto(
                progress,
                interaction_id=handle.interaction_id,
                request_id=handle.request_id,
            ),
        )

    def _watch_interaction(self, handle: Any, *, source: str) -> None:
        watcher = threading.Thread(
            target=self._wait_for_interaction,
            args=(handle, source),
            name=f"sakura-brain-watch-{handle.interaction_id[-8:]}",
            daemon=True,
        )
        with self._state_lock:
            self._watchers.add(watcher)
        watcher.start()

    def _wait_for_interaction(self, handle: Any, source: str) -> None:
        from app.core.assistant_service import InteractionCancelledError

        try:
            result = handle.result()
        except InteractionCancelledError:
            self._cancel_backchannel()
            self._publish(
                "chat.cancelled",
                {
                    "version": 1,
                    "interactionId": handle.interaction_id,
                    "requestId": handle.request_id,
                },
            )
        except Exception as exc:  # noqa: BLE001
            self._cancel_backchannel()
            self._record_history("error", str(exc))
            self._publish(
                "chat.error",
                {
                    "version": 1,
                    "interactionId": handle.interaction_id,
                    "requestId": handle.request_id,
                    "error": {
                        "code": "CHAT_REQUEST_FAILED",
                        "message": "聊天请求没有成功完成，请检查网络、代理和模型配置后重试。",
                        "retryable": True,
                        "details": {"errorType": type(exc).__name__},
                    },
                },
            )
        else:
            self._cancel_backchannel()
            self._record_completed_result(result)
            payload = {
                "version": 1,
                "interactionId": handle.interaction_id,
                "requestId": handle.request_id,
                "source": source,
                "reply": chat_reply_dto(result.reply),
                "pendingActions": [
                    pending_action_dto(action)
                    for action in self._pending_actions_for(handle.snapshot().pending_action_ids)
                ],
            }
            self._publish("chat.reply", payload)
            for action in payload["pendingActions"]:
                self._publish(
                    "chat.confirmation_requested",
                    {
                        "version": 1,
                        "interactionId": handle.interaction_id,
                        "requestId": handle.request_id,
                        "action": action,
                    },
                )
        finally:
            with self._state_lock:
                self._watchers.discard(threading.current_thread())

    def _handle_backchannel_choice(self, choice: Any) -> None:
        if self.assistant is None or not bool(getattr(self.assistant, "busy", False)):
            return
        settings = getattr(self.backchannel_service, "settings", None)
        tts_enabled = bool(getattr(settings, "tts_enabled", False))
        audio_key = ""
        if tts_enabled:
            audio_key = f"backchannel-{secrets.token_hex(12)}"
            cache = getattr(self.backchannel_service, "audio_cache", None)
            source = _backchannel_audio_source(self.backchannel_service, choice, cache)
            with self._state_lock:
                self._backchannel_audio_requests[audio_key] = {
                    "source": source,
                    "cache": cache,
                    "tone": choice.template.tone,
                    "text": choice.variant.ja,
                }
        self._publish(
            "assistant.backchannel",
            {
                "version": 1,
                "temporary": True,
                "segment": {
                    "ja": choice.variant.ja,
                    "zh": choice.variant.zh,
                    "tone": choice.template.tone,
                    "portrait": choice.template.portrait,
                    "suppressTts": not tts_enabled,
                    "audioKey": audio_key,
                },
            },
        )

    def _cancel_backchannel(self) -> None:
        cancel = getattr(self.backchannel_service, "cancel", None)
        if callable(cancel):
            cancel()
        with self._state_lock:
            self._backchannel_audio_requests.clear()

    def _pending_actions_for(self, action_ids: tuple[str, ...]) -> tuple[Any, ...]:
        assistant = self.assistant
        if assistant is None:
            return ()
        by_id = {str(action["id"]): action for action in assistant.pending_actions}
        actions = []
        for action_id in action_ids:
            item = by_id.get(action_id)
            if item is not None:
                from app.agent.actions import PendingToolAction

                actions.append(PendingToolAction.from_dict(item))
        return tuple(actions)

    def _record_completed_result(self, result: Any) -> None:
        reply = result.reply
        with self._state_lock:
            if reply.text.strip():
                self._messages.append({"role": "assistant", "content": reply.text})
        self._record_assistant_reply(reply, _debug=result._debug)

    def _record_assistant_reply(self, reply: Any, _debug: dict[str, Any] | None = None) -> None:
        clean_segments = [segment for segment in reply.segments if segment.text.strip()]
        for index, segment in enumerate(clean_segments):
            self._record_history(
                "assistant",
                segment.text,
                segment.translation,
                segment.tone,
                segment.portrait,
                _debug=_debug if index == 0 else None,
            )

    def _record_history(
        self,
        role: str,
        content: str,
        translation: str = "",
        tone: str = "",
        portrait: str = "",
        *,
        _debug: dict[str, Any] | None = None,
    ) -> None:
        history = getattr(self.context, "history_store", None)
        append = getattr(history, "append", None)
        if not callable(append):
            return
        try:
            with self._state_lock:
                append(role, content, translation, tone, portrait, _debug=_debug)
        except OSError:
            pass

    def _publish(self, name: str, payload: dict[str, Any]) -> bool:
        with self._state_lock:
            sink = self._event_sink
        if sink is None:
            return False
        try:
            sink(name, payload)
        except Exception:  # noqa: BLE001
            return False
        return True

    def _hello(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        if payload.get("protocol") != self.config.protocol_version:
            raise BrainHostError("PROTOCOL_VERSION_UNSUPPORTED", "protocol version mismatch")
        credential = payload.get("session_credential")
        if not isinstance(credential, str) or not secrets.compare_digest(
            credential,
            self.config.session_credential,
        ):
            raise BrainHostError("AUTHENTICATION_FAILED", "session credential is invalid")
        return {
            "protocol": self.config.protocol_version,
            "session_id": self.config.session_id,
            "backend_state": self.state,
            "startup": self.startup,
        }

    def _health(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "state": self.state,
            "ready": self.state == "ready",
        }
        if self.context is not None:
            result["character_id"] = self.context.character_profile.id
        if self.initialization_error is not None:
            result["error"] = self.initialization_error.to_dict()
        return result

    def _shutdown(self) -> dict[str, Any]:
        if self.state == "stopped":
            return {"state": "stopped"}
        self.state = "stopping"
        context = self.context
        if self.scheduler is not None:
            self.scheduler.stop(timeout=1)
        if self.assistant is not None:
            self.assistant.close(wait=True)
        if self.tts_service is not None:
            _close_quietly(self.tts_service, "close")
        if self.backchannel_service is not None:
            _close_quietly(self.backchannel_service, "close")
        with self._state_lock:
            watchers = tuple(self._watchers)
            self._manual_observations.clear()
            self._capture_session_id = None
            self._pending_capture_request_id = None
            self._background_event_kind = None
            self._clear_screen_context_batch_locked()
        for watcher in watchers:
            if watcher is not threading.current_thread():
                watcher.join(timeout=1)
        if context is not None:
            _close_quietly(getattr(context, "mcp_tool_provider", None), "close")
            _close_quietly(getattr(context, "plugin_manager", None), "shutdown_all")
            _close_quietly(getattr(context, "tts_provider", None), "close")
            registry = getattr(context, "resource_registry", None)
            if registry is not None:
                registry.stop_all(1_000)
        self.state = "stopped"
        return {"state": "stopped"}


def _build_context(base_dir: Path) -> Any:
    from app.core.bootstrap import build_initial_app_context

    return build_initial_app_context(base_dir)


def _build_tts_service(context: Any, base_dir: Path) -> Any:
    from app.voice.tts_synthesis_service import (
        NullTTSSynthesisService,
        create_tts_synthesis_service,
    )

    load_settings = getattr(getattr(context, "settings_service", None), "load_tts_settings", None)
    if not callable(load_settings):
        return NullTTSSynthesisService()
    try:
        settings = load_settings(character_profile=getattr(context, "character_profile", None))
        return create_tts_synthesis_service(settings, base_dir=base_dir)
    except Exception:  # noqa: BLE001
        return NullTTSSynthesisService()


def _build_backchannel_service(
    context: Any,
    base_dir: Path,
    on_choice: Callable[[Any], None],
) -> Any | None:
    settings_service = getattr(context, "settings_service", None)
    load_settings = getattr(settings_service, "load_backchannel_settings", None)
    profile = getattr(context, "character_profile", None)
    manifest_path = getattr(profile, "backchannel_manifest_path", None)
    if not callable(load_settings) or manifest_path is None:
        return None
    try:
        settings = load_settings()
        if not settings.active:
            return None
        from app.backchannel.classifier import RuleClassifier
        from app.backchannel.headless_service import HeadlessBackchannelService
        from app.backchannel.manifest import load_backchannel_manifest

        classifier: Any = RuleClassifier()
        if settings.mode == "hybrid":
            from app.backchannel.hybrid_classifier import HybridBackchannelClassifier

            classifier = HybridBackchannelClassifier.from_model_cache(
                base_dir,
                process_isolated=False,
            )
        manifest = load_backchannel_manifest(Path(manifest_path), profile=profile)
        if not manifest:
            return None
        service = HeadlessBackchannelService(
            classifier,
            manifest,
            settings=settings,
            on_choice=on_choice,
        )
        from app.backchannel.audio_cache import BackchannelAudioCache, voice_fingerprint

        service.audio_cache = BackchannelAudioCache(
            base_dir / "data" / "backchannels" / str(getattr(profile, "id", "default")) / "audio",
            voice_fingerprint(getattr(profile, "voice", None)),
        )
        return service
    except Exception:  # noqa: BLE001
        return None


def _backchannel_audio_source(service: Any, choice: Any, cache: Any) -> Path | None:
    audio = str(getattr(choice.variant, "audio", "") or "").strip()
    manifest_path = getattr(getattr(service, "manifest", None), "source_path", None)
    if audio and manifest_path is not None:
        root = Path(manifest_path).resolve().parent
        candidate = Path(audio)
        if not candidate.is_absolute():
            candidate = root / candidate
        try:
            resolved = candidate.resolve(strict=True)
            resolved.relative_to(root)
            if resolved.is_file():
                return resolved
        except (OSError, ValueError):
            pass
    lookup = getattr(cache, "lookup", None)
    if callable(lookup):
        return lookup(choice.template.tone, choice.variant.ja)
    return None


def _close_quietly(target: object | None, method: str) -> None:
    callback = getattr(target, method, None)
    if callable(callback):
        try:
            callback()
        except Exception:  # noqa: BLE001
            pass


def _required_id(payload: Mapping[str, Any], snake_case: str, camel_case: str) -> str:
    value = payload.get(snake_case, payload.get(camel_case))
    if not isinstance(value, str) or not value.strip():
        raise BrainHostError("INVALID_REQUEST", f"{camel_case} is required")
    return value.strip()
