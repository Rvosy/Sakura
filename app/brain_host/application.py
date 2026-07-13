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
            if self.backchannel_service is not None:
                self.backchannel_service.schedule(user_text)
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
