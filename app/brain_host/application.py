"""无 Qt Brain Host 应用装配与系统方法。"""

from __future__ import annotations

import secrets
import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass
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
        self.scheduler: Any | None = None
        self.state = "starting"
        self.startup: dict[str, Any] | None = None
        self.initialization_error: BrainHostError | None = None
        self._event_sink: EventSink | None = None
        self._messages: list[dict[str, Any]] = []
        self._state_lock = threading.RLock()
        self._watchers: set[threading.Thread] = set()

    def set_event_sink(self, sink: EventSink | None) -> None:
        with self._state_lock:
            self._event_sink = sink

    def shutdown(self) -> dict[str, Any]:
        return self._shutdown()

    def initialize(self) -> dict[str, Any] | None:
        if self.state == "ready":
            return self.startup
        try:
            self.context = self._context_builder(self.config.base_dir)
            self.startup = startup_state_dto(self.context)
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
        raise BrainHostError("METHOD_NOT_FOUND", f"Unknown Brain Host method: {method}")

    def _chat_send(
        self,
        payload: Mapping[str, Any],
        *,
        request_id: str | None,
    ) -> dict[str, Any]:
        text = payload.get("text")
        if not isinstance(text, str) or not text.strip():
            raise BrainHostError("INVALID_REQUEST", "聊天消息不能为空。")
        assistant = self._require_assistant()
        user_text = text.strip()
        with self._state_lock:
            from app.llm.context_trimming import trim_messages_for_model

            request_messages = trim_messages_for_model(
                [*self._messages, {"role": "user", "content": user_text}]
            )
            handle = self._submit_with_progress(
                lambda progress_callback: assistant.send_message(
                    request_messages,
                    progress_callback=progress_callback,
                    request_id=request_id,
                )
            )
            self._messages.append({"role": "user", "content": user_text})
            self._record_history("user", user_text)
            self._watch_interaction(handle, source="chat")
        return self._accepted_interaction(handle)

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
            self._publish(
                "chat.cancelled",
                {
                    "version": 1,
                    "interactionId": handle.interaction_id,
                    "requestId": handle.request_id,
                },
            )
        except Exception as exc:  # noqa: BLE001
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

    def _publish(self, name: str, payload: dict[str, Any]) -> None:
        with self._state_lock:
            sink = self._event_sink
        if sink is None:
            return
        try:
            sink(name, payload)
        except Exception:  # noqa: BLE001
            pass

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
        with self._state_lock:
            watchers = tuple(self._watchers)
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
