from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from app.agent import AgentRuntime, MemoryStore, ReminderStore, ToolRegistry, create_builtin_tool_registry
from app.agent.mcp import MCPToolProvider, register_mcp_tools_from_config
from app.agent.mcp.settings import MCPRuntimeSettings
from app.agent.memory_curator import MemoryCurator, MemoryCurationState
from app.config.settings_service import AppSettingsService
from app.llm.api_client import ApiSettings, OpenAICompatibleClient
from app.config.model_slots import ResolvedModelSlot, resolve_model_slot
from app.config.models import (
    MODEL_SLOT_CHAT,
    MODEL_SLOT_MEMORY_CURATION,
    MODEL_SLOT_VISION_CHAT,
    MODEL_SLOT_VISUAL_CONTEXT,
)
from app.core.app_context import AppContext, CoreServices, FeatureServices, StorageServices
from app.core.cancellation import CancelChecker, OperationCancelled, check_cancelled
from app.core.extensions import ExtensionRegistry
from app.config.character_loader import (
    CharacterProfile,
    CharacterRegistry,
    load_character_system_prompt,
)
from app.storage.chat_history import ChatHistoryStore
from app.agent.runtime_events import RuntimeEventLog
from app.core.debug_log import debug_log
from app.core.resource_manager import ResourceRegistry
from app.voice.factory import create_tts_provider
from app.voice.tts import (
    NullTTSProvider,
    TTSProvider,
    purge_tts_cache,
)
from app.voice.tts_settings import TTSConfigError
from app.storage.paths import StoragePaths
from app.storage.visual_observation import VisualObservationStore
from app.plugins.manager import PluginManager


PORTRAIT_SCALE_MIN_PERCENT = 50
PORTRAIT_SCALE_MAX_PERCENT = 150
PORTRAIT_SCALE_DEFAULT_PERCENT = 100


@dataclass(frozen=True)
class StartupState:
    """真实主窗口首帧需要的轻量启动状态。"""

    base_dir: Path
    settings_service: AppSettingsService
    settings: ApiSettings
    character_registry: CharacterRegistry
    character_profile: CharacterProfile
    system_prompt: str
    portrait_scale_percent: int


@dataclass(frozen=True)
class DeferredStartupServices:
    """后台初始化完成后注入主窗口的耗时服务。"""

    tts_provider: TTSProvider
    tool_registry: ToolRegistry
    extension_registry: ExtensionRegistry
    plugin_manager: PluginManager
    mcp_settings: MCPRuntimeSettings
    mcp_tool_provider: MCPToolProvider | None
    errors: tuple[str, ...] = ()


def load_startup_state(base_dir: Path) -> StartupState:
    """加载可立即显示立绘所需的轻量启动状态。"""

    settings_service = AppSettingsService(base_dir=base_dir)
    settings = settings_service.load_api_settings()
    # 使用新格式配置覆盖单条 settings
    api_profiles = settings_service.load_api_profiles()
    model_selection = settings_service.load_model_selection()
    chat_slot = resolve_model_slot(api_profiles, model_selection, MODEL_SLOT_CHAT, settings)
    if chat_slot is not None:
        settings = chat_slot.settings
    debug_log(
        "Startup",
        "API 配置已加载",
        {
            "base_url": settings.base_url,
            "model": settings.model,
            "timeout_seconds": settings.timeout_seconds,
            "api_key": settings.api_key,
        },
    )

    character_registry = CharacterRegistry(base_dir)
    character_profile = character_registry.get(
        settings_service.load_current_character_id(character_registry)
    )
    system_prompt = load_character_system_prompt(character_profile)
    debug_log(
        "Startup",
        "角色配置已加载",
        {
            "character_id": character_profile.id,
            "display_name": character_profile.display_name,
            "reply_tones": character_profile.reply_tones,
        },
    )
    portrait_scale_percent = _normalize_portrait_scale_percent(
        settings_service.load_system_values("ui").get(
            "portrait_scale_percent",
            PORTRAIT_SCALE_DEFAULT_PERCENT,
        )
    )

    return StartupState(
        base_dir=base_dir,
        settings_service=settings_service,
        settings=settings,
        character_registry=character_registry,
        character_profile=character_profile,
        system_prompt=system_prompt,
        portrait_scale_percent=portrait_scale_percent,
    )


def build_initial_app_context(base_dir: Path, startup_state: StartupState | None = None) -> AppContext:
    """创建真实主窗口首帧可用的基础依赖，不连接耗时外部服务。"""

    startup_state = startup_state or load_startup_state(base_dir)
    settings_service = startup_state.settings_service
    settings = startup_state.settings
    character_registry = startup_state.character_registry
    character_profile = startup_state.character_profile
    system_prompt = startup_state.system_prompt
    api_client = OpenAICompatibleClient(settings)

    # 加载 API 配置集和模型选择，构建按功能分流的 API client。
    api_profiles = settings_service.load_api_profiles()
    model_selection = settings_service.load_model_selection()
    vision_slot = resolve_model_slot(api_profiles, model_selection, MODEL_SLOT_VISION_CHAT, settings)
    vision_api_client = _client_for_explicit_slot(vision_slot, MODEL_SLOT_VISION_CHAT)
    visual_context_slot = resolve_model_slot(api_profiles, model_selection, MODEL_SLOT_VISUAL_CONTEXT, settings)
    visual_context_api_client = _client_for_explicit_slot(
        visual_context_slot,
        MODEL_SLOT_VISUAL_CONTEXT,
    )

    resource_registry = ResourceRegistry()
    memory_store = MemoryStore(
        base_dir=base_dir,
        api_settings=settings,
        scope_id=character_profile.id,
        resource_registry=resource_registry,
    )
    memory_store.preload(wait=False)
    reminder_store = ReminderStore(StoragePaths(base_dir).reminders_store())
    tool_registry = create_builtin_tool_registry(
        base_dir,
        memory_store,
        reminder_store,
    )
    extension_registry = ExtensionRegistry()
    extension_registry.apply_tools(tool_registry)
    plugin_manager = PluginManager(base_dir=base_dir, resource_registry=resource_registry)
    mcp_settings = settings_service.load_mcp_runtime_settings()
    runtime_loop_settings = settings_service.load_runtime_loop_settings()
    history_store = create_history_store(base_dir, character_profile)
    agent_runtime = AgentRuntime(
        api_client=api_client,
        vision_api_client=vision_api_client,
        visual_context_api_client=visual_context_api_client,
        system_prompt=system_prompt,
        reply_tones=character_profile.reply_tones,
        reply_portraits=character_profile.portrait_choices,
        tools=tool_registry,
        memory=memory_store,
        history_store=history_store,
        runtime_loop_settings=runtime_loop_settings,
        character_id=character_profile.id,
        character_name=character_profile.display_name,
    )
    runtime_event_log = create_runtime_event_log(base_dir, character_profile)
    visual_observation_store = create_visual_observation_store(base_dir, character_profile)
    debug_log_settings = settings_service.load_debug_log_settings()
    startup_settings = settings_service.load_startup_settings()
    memory_curation_settings = settings_service.load_memory_curation_settings()
    memory_curation_state = MemoryCurationState(
        StoragePaths(base_dir).memory_curation_state()
    )
    memory_curation_slot = resolve_model_slot(
        api_profiles,
        model_selection,
        MODEL_SLOT_MEMORY_CURATION,
        settings,
    )
    memory_curator_client = (
        OpenAICompatibleClient(memory_curation_slot.settings)
        if memory_curation_slot is not None
        else api_client
    )
    memory_curator = MemoryCurator(memory_curator_client, memory_store, system_prompt=system_prompt)
    screen_awareness_settings = settings_service.load_screen_awareness_settings()

    debug_log(
        "Startup",
        "初始主窗口服务已创建",
        {
            "tool_count": len(tool_registry.all()),
            "mcp_deferred": True,
            "plugins_deferred": True,
            "tts_deferred": True,
            "auto_memory": memory_curation_settings.enabled,
            "tool_loop": {
                "max_agent_steps_per_turn": runtime_loop_settings.max_agent_steps_per_turn,
                "max_tool_calls_per_step": runtime_loop_settings.max_tool_calls_per_step,
                "max_tool_calls_per_turn": runtime_loop_settings.max_tool_calls_per_turn,
            },
        },
    )

    return AppContext(
        base_dir=base_dir,
        settings_service=settings_service,
        settings=settings,
        character_registry=character_registry,
        character_profile=character_profile,
        system_prompt=system_prompt,
        tts_provider=NullTTSProvider(),
        core=CoreServices(
            api_client=api_client,
            tool_registry=tool_registry,
            agent_runtime=agent_runtime,
        ),
        storage=StorageServices(
            memory_store=memory_store,
            reminder_store=reminder_store,
            history_store=history_store,
            visual_observation_store=visual_observation_store,
            runtime_event_log=runtime_event_log,
        ),
        resource_registry=resource_registry,
        features=FeatureServices(
            settings_service=settings_service,
            extension_registry=extension_registry,
            mcp_tool_provider=None,
            plugin_manager=plugin_manager,
            mcp_settings=mcp_settings,
            debug_log_settings=debug_log_settings,
            startup_settings=startup_settings,
            memory_curation_settings=memory_curation_settings,
            memory_curation_state=memory_curation_state,
            memory_curator=memory_curator,
            screen_awareness_settings=screen_awareness_settings,
        ),
        startup_initializing=True,
    )


def build_deferred_services(
    base_dir: Path,
    context: AppContext,
    *,
    cancel_checker: CancelChecker | None = None,
) -> DeferredStartupServices:
    """后台创建启动首帧之后才需要的耗时服务。"""

    errors: list[str] = []
    settings_service = context.settings_service
    character_profile = context.character_profile
    tts_provider: TTSProvider | None = None
    plugin_manager: PluginManager | None = None
    mcp_tool_provider: MCPToolProvider | None = None

    try:
        check_cancelled(cancel_checker)
        # 启动时清空 data/cache/tts 残留（崩溃/强退遗留的临时 wav），失败不影响启动
        try:
            purge_tts_cache(base_dir)
        except OSError as exc:
            debug_log("Startup", "TTS 缓存启动清理失败，已忽略", {"error": str(exc)})
        check_cancelled(cancel_checker)

        try:
            tts_settings = settings_service.load_tts_settings(
                character_profile=character_profile,
            )
            tts_provider = create_tts_provider(tts_settings, base_dir=base_dir)
        except TTSConfigError as exc:
            debug_log("TTS", "配置无效，已禁用 TTS", {"error": str(exc)})
            errors.append(f"TTS 配置无效，已禁用：{exc}")
            tts_provider = NullTTSProvider()
        check_cancelled(cancel_checker)
        debug_log(
            "Startup",
            "TTS Provider 已创建",
            {"provider": type(tts_provider).__name__},
        )

        tool_registry = create_builtin_tool_registry(
            base_dir,
            context.memory_store,
            context.reminder_store,
        )
        tool_registry.set_free_access_enabled(context.tool_registry.free_access_enabled)
        extension_registry = ExtensionRegistry()
        extension_registry.apply_tools(tool_registry)
        plugin_manager = PluginManager(base_dir=base_dir, resource_registry=context.resource_registry)
        try:
            check_cancelled(cancel_checker)
            plugin_manager.load_from_config(tool_registry)
        except OperationCancelled:
            raise
        except Exception as exc:  # noqa: BLE001
            debug_log("Plugin", "启动加载失败，已跳过插件", {"error": str(exc)})
            debug_log("PluginManager", "启动加载失败，已跳过插件", {"error": str(exc)})
            errors.append(f"插件加载失败，已跳过：{exc}")
        check_cancelled(cancel_checker)
        for result in plugin_manager.results:
            if result.error:
                errors.append(f"插件 {result.spec.plugin_id or result.spec.entry} 加载失败：{result.error}")
        mcp_settings = settings_service.load_mcp_runtime_settings()
        mcp_tool_provider = register_mcp_tools_from_config(
            base_dir,
            tool_registry,
            runtime_settings=mcp_settings,
            resource_registry=context.resource_registry,
        )
        check_cancelled(cancel_checker)
    except OperationCancelled:
        _close_deferred_service_objects(tts_provider, mcp_tool_provider, plugin_manager)
        raise

    debug_log(
        "Startup",
        "后台启动服务已创建",
        {
            "tool_count": len(tool_registry.all()),
            "mcp_enabled": mcp_tool_provider is not None,
            "windows_mcp_enabled": mcp_settings.windows_enabled,
            "error_count": len(errors),
        },
    )

    return DeferredStartupServices(
        tts_provider=tts_provider,
        tool_registry=tool_registry,
        extension_registry=extension_registry,
        plugin_manager=plugin_manager,
        mcp_settings=mcp_settings,
        mcp_tool_provider=mcp_tool_provider,
        errors=tuple(errors),
    )


def _close_deferred_service_objects(
    tts_provider: TTSProvider | None,
    mcp_tool_provider: MCPToolProvider | None,
    plugin_manager: PluginManager | None,
) -> None:
    close_tts = getattr(tts_provider, "close", None)
    if callable(close_tts):
        try:
            close_tts()
        except Exception as exc:  # noqa: BLE001
            debug_log("TTS", "取消后台启动时关闭 TTS Provider 失败", {"error": str(exc)})
    close_mcp = getattr(mcp_tool_provider, "close", None)
    if callable(close_mcp):
        try:
            close_mcp()
        except Exception as exc:  # noqa: BLE001
            debug_log("MCP", "取消后台启动时关闭 MCP Provider 失败", {"error": str(exc)})
    shutdown_all = getattr(plugin_manager, "shutdown_all", None)
    if callable(shutdown_all):
        try:
            shutdown_all()
        except Exception as exc:  # noqa: BLE001
            debug_log("PluginManager", "取消后台启动时关闭插件失败", {"error": str(exc)})


def _normalize_portrait_scale_percent(value: object) -> int:
    try:
        percent = int(str(value).strip())
    except (TypeError, ValueError):
        return PORTRAIT_SCALE_DEFAULT_PERCENT
    return max(PORTRAIT_SCALE_MIN_PERCENT, min(PORTRAIT_SCALE_MAX_PERCENT, percent))


def create_history_store(base_dir: Path, profile: CharacterProfile) -> ChatHistoryStore:
    """按角色创建聊天历史存储；路径统一来自 StoragePaths（pet_window 复用）。"""
    history_path = StoragePaths(base_dir).chat_history_for(profile.id)
    return ChatHistoryStore(history_path, profile.display_name)


def create_runtime_event_log(base_dir: Path, profile: CharacterProfile) -> RuntimeEventLog:
    """按角色创建运行时事件日志（与聊天历史路径风格一致，但完全独立）。"""
    event_path = StoragePaths(base_dir).runtime_events_for(profile.id)
    return RuntimeEventLog(event_path)


def create_visual_observation_store(
    base_dir: Path,
    profile: CharacterProfile,
) -> VisualObservationStore:
    visual_path = StoragePaths(base_dir).visual_observations_for(profile.id)
    return VisualObservationStore(visual_path)


def _client_for_explicit_slot(
    resolved: ResolvedModelSlot | None,
    slot: str,
) -> OpenAICompatibleClient | None:
    if resolved is None or resolved.source_slot != slot:
        return None
    return OpenAICompatibleClient(resolved.settings)
