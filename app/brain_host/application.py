"""无 Qt Brain Host 应用装配与系统方法。"""

from __future__ import annotations

import secrets
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from app.brain_host.dto import (
    agent_progress_dto,
    chat_reply_dto,
    pending_action_dto,
    startup_state_dto,
)
from app.brain_host.errors import BrainHostError
from app.brain_host.protocol import PROTOCOL_VERSION
from app.brain_host.secondary_windows import secondary_host_call, secondary_window_request


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
        self.settings_resource_tasks: Any | None = None
        self.mobile_bridge: Any | None = None
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
        self._plugin_runtime_started = False
        self._plugin_runtime_closing = False

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
        from app.config.character_loader import CharacterConfigError

        try:
            self.context = self._context_builder(self.config.base_dir)
            self._install_headless_runtime_services()
            self.tts_service = _build_tts_service(self.context, self.config.base_dir)
            self.backchannel_service = _build_backchannel_service(
                self.context,
                self.config.base_dir,
                self._handle_backchannel_choice,
            )
            self.startup = self._current_startup_state()
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
        except CharacterConfigError:
            self.context = _build_characterless_context(self.config.base_dir)
            self.assistant = None
            self.tts_service = None
            self.backchannel_service = None
            self.scheduler = None
            self.mobile_bridge = None
            self.initialization_error = None
            self.state = "ready"
            self.startup = self._current_startup_state()
            return self.startup
        except Exception as exc:  # noqa: BLE001
            self.state = "failed"
            self.initialization_error = BrainHostError(
                "BACKEND_INITIALIZATION_FAILED",
                "Brain Host initialization failed",
                retryable=True,
                details={"error_type": type(exc).__name__},
            )
            return None
        self.initialization_error = None
        self.state = "ready"
        self._configure_screen_observation_runtime()
        self.sync_scheduler_jobs(start=False)
        self._emit_plugin_runtime_started()
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
        if method == "pet.bootstrap":
            if self.context is None or self.state != "ready":
                raise BrainHostError("BACKEND_UNAVAILABLE", "Brain Host 尚未就绪。", retryable=True)
            self.startup = self._current_startup_state()
            return self.startup
        if method == "bootstrap.status":
            if self.context is None or self.state != "ready":
                raise BrainHostError("BACKEND_UNAVAILABLE", "Brain Host 尚未就绪。", retryable=True)
            self.startup = self._current_startup_state()
            return self.startup
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
        if method == "window.request":
            kind = payload.get("kind")
            if not isinstance(kind, str) or not kind.strip():
                raise BrainHostError("INVALID_REQUEST", "window.request 缺少 kind。")
            try:
                return secondary_window_request(self, kind.strip(), payload)
            except (OSError, ValueError, RuntimeError) as exc:
                raise BrainHostError(
                    "SECONDARY_WINDOW_REQUEST_FAILED",
                    str(exc),
                    details={"errorType": type(exc).__name__},
                ) from exc
        if method == "window.host_call":
            host_method = payload.get("method")
            params = payload.get("params", {})
            if not isinstance(host_method, str) or not isinstance(params, Mapping):
                raise BrainHostError("INVALID_REQUEST", "window.host_call 参数无效。")
            try:
                return secondary_host_call(self, host_method, params)
            except (OSError, ValueError, RuntimeError) as exc:
                raise BrainHostError(
                    "SECONDARY_WINDOW_CALL_FAILED",
                    str(exc),
                    details={"errorType": type(exc).__name__},
                ) from exc
        if method == "tts.synthesize":
            return self._tts_synthesize(payload, request_id=request_id)
        if method == "tts.cancel":
            return self._tts_cancel(payload)
        raise BrainHostError("METHOD_NOT_FOUND", f"Unknown Brain Host method: {method}")

    def refresh_character(self, character_id: str) -> None:
        context = self.context
        if context is None:
            raise ValueError("Brain Host 上下文尚未初始化。")
        with self._state_lock:
            if self._assistant_busy_locked():
                raise ValueError("助手正在处理请求，暂时不能切换角色。")
        from app.config.character_loader import CharacterRegistry, load_character_system_prompt
        from app.core.bootstrap import (
            create_history_store,
            create_runtime_event_log,
            create_visual_observation_store,
        )

        registry = CharacterRegistry(self.config.base_dir)
        profile = registry.get(character_id)
        if getattr(context, "character_profile", None) is None or not hasattr(
            context, "agent_runtime"
        ):
            save_current = getattr(context.settings_service, "save_current_character_id", None)
            if callable(save_current):
                save_current(registry, profile.id)
            self.context = None
            self.startup = None
            self.initialization_error = None
            self.settings_resource_tasks = None
            self.state = "starting"
            startup = self.initialize()
            active_profile = getattr(self.context, "character_profile", None)
            if (
                self.state != "ready"
                or startup is None
                or getattr(active_profile, "id", None) != profile.id
            ):
                raise ValueError("首个角色创建成功，但 Brain Host 重新初始化失败。")
            return
        previous_id = str(getattr(getattr(context, "character_profile", None), "id", ""))
        system_prompt = load_character_system_prompt(profile)
        memory_store = context.memory_store
        memory_store.set_scope(profile.id)
        history_store = create_history_store(self.config.base_dir, profile)
        runtime_event_log = create_runtime_event_log(self.config.base_dir, profile)
        visual_observation_store = create_visual_observation_store(self.config.base_dir, profile)
        runtime = context.agent_runtime
        runtime.update_character(
            system_prompt,
            profile.reply_tones,
            profile.portrait_choices,
            character_id=profile.id,
            character_name=profile.display_name,
        )
        runtime.set_history_store(history_store)
        curator = getattr(context, "memory_curator", None)
        set_system_prompt = getattr(curator, "set_system_prompt", None)
        if callable(set_system_prompt):
            set_system_prompt(system_prompt)
        new_storage = replace(
            context.storage,
            history_store=history_store,
            visual_observation_store=visual_observation_store,
            runtime_event_log=runtime_event_log,
        )
        self.context = replace(
            context,
            character_registry=registry,
            character_profile=profile,
            system_prompt=system_prompt,
            storage=new_storage,
        )
        if self.assistant is not None:
            self.assistant.pipeline.visual_observation_store = visual_observation_store
        if profile.id != previous_id:
            with self._state_lock:
                self._messages.clear()
                self._manual_observations.clear()
                self._clear_screen_context_batch_locked()
            self._emit_plugin_event(
                "character.loaded",
                {
                    "character_id": profile.id,
                    "character_name": profile.display_name,
                    "previous_character_id": previous_id,
                },
                source="character",
            )
        self._cancel_backchannel()
        if self.backchannel_service is not None:
            _close_quietly(self.backchannel_service, "close")
        self.backchannel_service = _build_backchannel_service(
            self.context,
            self.config.base_dir,
            self._handle_backchannel_choice,
        )
        self.startup = self._current_startup_state()

    def refresh_runtime_settings(
        self,
        *,
        screen_awareness: Any,
        mcp: Any,
        debug: Any,
        startup: Any,
        memory_curation: Any,
    ) -> None:
        context = self.context
        if context is None:
            return
        self.context = replace(
            context,
            features=replace(
                context.features,
                screen_awareness_settings=screen_awareness,
                mcp_settings=mcp,
                debug_log_settings=debug,
                startup_settings=startup,
                memory_curation_settings=memory_curation,
            ),
        )
        self._configure_screen_observation_runtime()

    def refresh_api_settings(self) -> None:
        context = self.context
        if context is None:
            return
        from app.config.model_slots import resolve_model_slot
        from app.config.models import (
            MODEL_SLOT_CHAT,
            MODEL_SLOT_MEMORY_CURATION,
            MODEL_SLOT_VISION_CHAT,
        )
        from app.llm.api_client import OpenAICompatibleClient

        service = context.settings_service
        settings = service.load_api_settings()
        profiles = service.load_api_profiles()
        selection = service.load_model_selection()
        chat_slot = resolve_model_slot(profiles, selection, MODEL_SLOT_CHAT, settings)
        if chat_slot is not None:
            settings = chat_slot.settings
        context.api_client.update_settings(settings)
        context.memory_store.set_api_settings(settings)
        vision_slot = resolve_model_slot(profiles, selection, MODEL_SLOT_VISION_CHAT, settings)
        context.agent_runtime.vision_api_client = (
            OpenAICompatibleClient(vision_slot.settings)
            if vision_slot is not None and vision_slot.source_slot == MODEL_SLOT_VISION_CHAT
            else None
        )
        memory_slot = resolve_model_slot(profiles, selection, MODEL_SLOT_MEMORY_CURATION, settings)
        curator = context.memory_curator
        curator.set_api_client(
            OpenAICompatibleClient(memory_slot.settings)
            if memory_slot is not None
            else context.api_client
        )
        self.context = replace(context, settings=settings)
        self.startup = self._current_startup_state()

    def refresh_tts(self) -> None:
        previous = self.tts_service
        self.tts_service = _build_tts_service(self.context, self.config.base_dir)
        if previous is not None:
            _close_quietly(previous, "close")
        self.startup = self._current_startup_state()

    def _install_headless_runtime_services(self) -> None:
        """在 Brain Host 内装配插件与 MCP，不创建任何 Qt 服务。"""

        context = self.context
        if context is None or not all(
            hasattr(context, name)
            for name in ("core", "features", "resource_registry", "agent_runtime")
        ):
            return

        from app.agent import create_builtin_tool_registry
        from app.agent.mcp import register_mcp_tools_from_config
        from app.core.extensions import ExtensionRegistry
        from app.core.mobile_chat_bridge import MobileChatBridge
        from app.plugins.manager import PluginManager

        tool_registry = create_builtin_tool_registry(
            self.config.base_dir,
            context.memory_store,
            context.reminder_store,
        )
        tool_registry.set_free_access_enabled(context.tool_registry.free_access_enabled)
        extension_registry = ExtensionRegistry()
        extension_registry.apply_tools(tool_registry)
        plugin_manager = PluginManager(
            base_dir=self.config.base_dir,
            resource_registry=context.resource_registry,
            allow_native_ui=False,
        )
        mobile_bridge = MobileChatBridge(self)
        plugin_manager.services.set_backends(
            mobile_characters_sink=mobile_bridge.characters,
            mobile_history_sink=mobile_bridge.history,
            mobile_chat_sink=mobile_bridge.chat,
            mobile_theme_sink=self._mobile_theme_mapping,
        )
        try:
            plugin_manager.load_from_config(tool_registry)
            mcp_settings = context.settings_service.load_mcp_runtime_settings()
            mcp_tool_provider = register_mcp_tools_from_config(
                self.config.base_dir,
                tool_registry,
                runtime_settings=mcp_settings,
                resource_registry=context.resource_registry,
            )
        except Exception:
            plugin_manager.shutdown_all()
            raise

        emitter = plugin_manager.emit_bus_event
        tool_registry.set_event_emitter(emitter)
        runtime = context.agent_runtime
        runtime.tools = tool_registry
        runtime.set_prompt_patches(plugin_manager.prompt_patches)
        runtime.set_context_providers(plugin_manager.context_providers)
        for client in (
            getattr(runtime, "api_client", None),
            getattr(runtime, "vision_api_client", None),
            getattr(getattr(context, "memory_curator", None), "api_client", None),
        ):
            set_event_emitter = getattr(client, "set_event_emitter", None)
            if callable(set_event_emitter):
                set_event_emitter(emitter)

        previous_plugin_manager = getattr(context, "plugin_manager", None)
        self.context = replace(
            context,
            core=replace(context.core, tool_registry=tool_registry),
            features=replace(
                context.features,
                extension_registry=extension_registry,
                plugin_manager=plugin_manager,
                mcp_settings=mcp_settings,
                mcp_tool_provider=mcp_tool_provider,
            ),
            startup_initializing=False,
        )
        self.mobile_bridge = mobile_bridge
        if previous_plugin_manager is not plugin_manager:
            _close_quietly(previous_plugin_manager, "shutdown_all")

    def _mobile_theme_mapping(self) -> dict[str, object]:
        from app.config.theme import (
            DEFAULT_THEME_SETTINGS,
            resolve_effective_theme,
            theme_colors_to_mapping,
        )

        context = self.context
        if context is None:
            return theme_colors_to_mapping(DEFAULT_THEME_SETTINGS)
        service = getattr(context, "settings_service", None)
        profile = getattr(context, "character_profile", None)
        try:
            user_theme = service.load_theme_settings()
            override = service.load_character_theme_override(profile.id) if profile is not None else None
            return theme_colors_to_mapping(resolve_effective_theme(profile, override, user_theme))
        except (AttributeError, OSError, ValueError):
            return theme_colors_to_mapping(DEFAULT_THEME_SETTINGS)

    @property
    def character_profile(self) -> Any:
        return getattr(self.context, "character_profile", None)

    @property
    def character_registry(self) -> Any:
        return getattr(self.context, "character_registry", None)

    @property
    def api_client(self) -> Any:
        return getattr(self.context, "api_client", None)

    @property
    def agent_runtime(self) -> Any:
        return getattr(self.context, "agent_runtime", None)

    @property
    def memory_store(self) -> Any:
        return getattr(self.context, "memory_store", None)

    def _create_history_store(self, profile: Any) -> Any:
        from app.core.bootstrap import create_history_store

        return create_history_store(self.config.base_dir, profile)

    def submit_mobile_chat(
        self,
        bridge: Any,
        character_id: str,
        text: str,
        image_data_url: str = "",
    ) -> dict[str, Any]:
        from app.core.mobile_chat_bridge import MobileChatBusyError

        with self._state_lock:
            if self.state != "ready" or self._assistant_busy_locked():
                raise MobileChatBusyError("Sakura 正忙，请稍后再试。")
            self._background_event_kind = "mobile_chat"
        self._publish_busy(True, "mobile_chat")
        try:
            result = bridge.execute_chat(character_id, text, image_data_url)
            clean_text = text.strip() or "请看这张图片。"
            reply_text = str(result.get("reply_raw") or result.get("reply") or "").strip()
            with self._state_lock:
                self._messages.append({"role": "user", "content": clean_text})
                if reply_text:
                    self._messages.append({"role": "assistant", "content": reply_text})
                self._last_user_activity_at = time.monotonic()
            self._emit_user_plugin_events(clean_text, source="mobile")
            if reply_text:
                self._emit_plugin_event(
                    "message.ai",
                    {
                        "text": reply_text,
                        "segments": list(result.get("segments") or []),
                        "character_id": character_id,
                    },
                    source="mobile",
                )
                self._emit_plugin_bus_event(
                    "chat.message.sent",
                    {"text": reply_text, "character_id": character_id},
                )
            return result
        finally:
            with self._state_lock:
                self._background_event_kind = None
            self._publish_busy(False, "mobile_chat")

    def _current_startup_state(self) -> dict[str, Any]:
        if self.context is None:
            return {}
        startup = startup_state_dto(self.context)
        runtime = startup.setdefault("runtime", {})
        runtime["tts_ready"] = bool(getattr(self.tts_service, "service_ready", False))
        runtime["tts_enabled"] = type(self.tts_service).__name__ != "NullTTSSynthesisService"
        return startup

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
        plugin_payload = {
            "synthesis_id": handle.request_id,
            "segment_id": segment_id.strip(),
            "text": text.strip(),
            "tone": str(tone or ""),
            "character_id": str(getattr(self.character_profile, "id", "")),
        }
        self._emit_tts_plugin_started(plugin_payload)
        self._watch_tts(
            handle,
            ipc_request_id=request_id or handle.request_id,
            segment_id=segment_id.strip(),
            audio_context=audio_context,
            plugin_payload=plugin_payload,
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
        plugin_payload: dict[str, Any],
    ) -> None:
        watcher = threading.Thread(
            target=self._wait_for_tts,
            args=(handle, ipc_request_id, segment_id, audio_context, plugin_payload),
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
        plugin_payload: dict[str, Any],
    ) -> None:
        from app.voice.tts_synthesis_service import (
            TTSSynthesisCancelled,
            TTSSynthesisClosed,
        )

        completion_status = "error"
        try:
            result = handle.result()
        except (TTSSynthesisCancelled, TTSSynthesisClosed):
            completion_status = "cancelled"
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
            completion_status = "ready" if result.resource is not None else "skipped"
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
            self._emit_tts_plugin_finished(
                {**plugin_payload, "status": completion_status}
            )
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
            self._emit_user_plugin_events(recorded_user_text, source="user")
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
        self._emit_assistant_plugin_events(reply, source="agent")

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

    def _emit_plugin_event(
        self,
        event_type: str,
        payload: dict[str, Any] | None = None,
        *,
        source: str,
    ) -> None:
        manager = getattr(self.context, "plugin_manager", None)
        emit_event = getattr(manager, "emit_event", None)
        if not callable(emit_event):
            return
        try:
            emit_event(event_type, payload or {}, source=source)
        except (RuntimeError, ValueError):
            return

    def _emit_plugin_bus_event(
        self,
        event_name: str,
        payload: dict[str, Any] | None = None,
    ) -> None:
        manager = getattr(self.context, "plugin_manager", None)
        emit_event = getattr(manager, "emit_bus_event", None)
        if not callable(emit_event):
            return
        try:
            emit_event(event_name, payload or {})
        except RuntimeError:
            return

    def _emit_plugin_runtime_started(self) -> None:
        if self._plugin_runtime_started:
            return
        profile = getattr(self.context, "character_profile", None)
        if profile is None:
            return
        self._plugin_runtime_started = True
        payload = {
            "character_id": str(getattr(profile, "id", "")),
            "character_name": str(getattr(profile, "display_name", "")),
        }
        self._emit_plugin_event("app.start", {**payload, "carryover": {}}, source="startup")
        self._emit_plugin_event(
            "character.loaded",
            {**payload, "previous_character_id": ""},
            source="startup",
        )
        self._emit_plugin_bus_event("app.started", payload)

    def _emit_plugin_runtime_closing(self) -> None:
        if self._plugin_runtime_closing or not self._plugin_runtime_started:
            return
        self._plugin_runtime_closing = True
        self._emit_plugin_bus_event(
            "app.closing",
            {"interrupted_reply": bool(getattr(self.assistant, "busy", False))},
        )

    def _emit_user_plugin_events(self, text: str, *, source: str) -> None:
        profile = getattr(self.context, "character_profile", None)
        character_id = str(getattr(profile, "id", ""))
        payload = {"text": text, "character_id": character_id}
        self._emit_plugin_event("message.user", payload, source=source)
        self._emit_plugin_bus_event("chat.message.received", payload)

    def _emit_assistant_plugin_events(self, reply: Any, *, source: str) -> None:
        profile = getattr(self.context, "character_profile", None)
        character_id = str(getattr(profile, "id", ""))
        segments = [
            {
                "text": segment.text,
                "translation": segment.translation,
                "tone": segment.tone,
                "portrait": segment.portrait,
            }
            for segment in reply.segments
            if segment.text.strip()
        ]
        payload = {"text": reply.text, "segments": segments, "character_id": character_id}
        self._emit_plugin_event("message.ai", payload, source=source)
        self._emit_plugin_bus_event(
            "chat.message.sent",
            {"text": reply.text, "character_id": character_id},
        )

    def _emit_tts_plugin_started(self, payload: dict[str, Any]) -> None:
        self._emit_plugin_event("tts.start", payload, source="tts")
        self._emit_plugin_bus_event("tts.started", payload)

    def _emit_tts_plugin_finished(self, payload: dict[str, Any]) -> None:
        self._emit_plugin_event("tts.end", payload, source="tts")
        self._emit_plugin_bus_event("tts.finished", payload)

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
        profile = getattr(self.context, "character_profile", None)
        if profile is not None:
            result["character_id"] = profile.id
        if self.initialization_error is not None:
            result["error"] = self.initialization_error.to_dict()
        return result

    def _shutdown(self) -> dict[str, Any]:
        if self.state == "stopped":
            return {"state": "stopped"}
        self.state = "stopping"
        context = self.context
        self._emit_plugin_runtime_closing()
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


def _build_characterless_context(base_dir: Path) -> Any:
    from app.config.settings_service import AppSettingsService

    settings_service = AppSettingsService(base_dir=base_dir)
    return SimpleNamespace(
        base_dir=base_dir.resolve(),
        settings_service=settings_service,
        settings=settings_service.load_api_settings(),
        character_profile=None,
        character_registry=SimpleNamespace(profiles={}),
    )


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
