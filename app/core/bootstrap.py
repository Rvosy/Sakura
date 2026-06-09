from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from app.agent import AgentRuntime, MemoryStore, ReminderStore, ToolRegistry, create_builtin_tool_registry
from app.agent.mcp import MCPToolProvider, register_mcp_tools_from_config
from app.agent.mcp.settings import MCPRuntimeSettings
from app.agent.memory_curator import MemoryCurator, MemoryCurationState
from app.config.settings_service import AppSettingsService
from app.llm.api_client import ApiSettings, OpenAICompatibleClient
from app.core.app_context import AppContext, CoreServices, FeatureServices, StorageServices
from app.core.extensions import ExtensionRegistry
from app.config.character_loader import (
    DEFAULT_CHARACTER_ID,
    CharacterProfile,
    CharacterRegistry,
    load_character_system_prompt,
)
from app.storage.chat_history import ChatHistoryStore
from app.agent.runtime_events import RuntimeEventLog
from app.core.debug_log import debug_log
from app.voice.tts import (
    TTS_PROVIDER_GENIE,
    GenieTTSProvider,
    GPTSoVITSTTSProvider,
    NullTTSProvider,
    TTSConfigError,
    TTSProvider,
)
from app.storage.visual_observation import VisualObservationStore
from app.core.plugin_manager import SakuraPluginManager


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
    plugin_manager: SakuraPluginManager
    mcp_settings: MCPRuntimeSettings
    mcp_tool_provider: MCPToolProvider | None
    errors: tuple[str, ...] = ()


def load_startup_state(base_dir: Path) -> StartupState:
    """加载可立即显示立绘所需的轻量启动状态。"""

    settings_service = AppSettingsService(base_dir=base_dir)
    settings = settings_service.load_api_settings()
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
    memory_store = MemoryStore(
        base_dir=base_dir,
        api_settings=settings,
        scope_id=character_profile.id,
    )
    memory_store.preload(wait=False)
    reminder_store = ReminderStore(base_dir / "data" / "reminders.json")
    tool_registry = create_builtin_tool_registry(
        base_dir,
        memory_store,
        reminder_store,
    )
    extension_registry = ExtensionRegistry()
    extension_registry.apply_tools(tool_registry)
    plugin_manager = SakuraPluginManager(base_dir=base_dir)
    mcp_settings = settings_service.load_mcp_runtime_settings()
    agent_runtime = AgentRuntime(
        api_client=api_client,
        system_prompt=system_prompt,
        reply_tones=character_profile.reply_tones,
        reply_portraits=character_profile.portrait_choices,
        tools=tool_registry,
        memory=memory_store,
    )
    history_store = _create_history_store(base_dir, character_profile)
    runtime_event_log = _create_runtime_event_log(base_dir, character_profile)
    visual_observation_store = _create_visual_observation_store(base_dir, character_profile)
    debug_log_settings = settings_service.load_debug_log_settings()
    startup_settings = settings_service.load_startup_settings()
    memory_curation_settings = settings_service.load_memory_curation_settings()
    memory_curation_state = MemoryCurationState(
        base_dir / "data" / "memory_curation_state.json"
    )
    memory_curator = MemoryCurator(api_client, memory_store)
    proactive_care_settings = settings_service.load_proactive_care_settings()

    debug_log(
        "Startup",
        "初始主窗口服务已创建",
        {
            "tool_count": len(tool_registry.all()),
            "mcp_deferred": True,
            "plugins_deferred": True,
            "tts_deferred": True,
            "auto_memory": memory_curation_settings.enabled,
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
            proactive_care_settings=proactive_care_settings,
        ),
        startup_initializing=True,
    )


def build_deferred_services(base_dir: Path, context: AppContext) -> DeferredStartupServices:
    """后台创建启动首帧之后才需要的耗时服务。"""

    errors: list[str] = []
    settings_service = context.settings_service
    character_profile = context.character_profile

    try:
        tts_settings = settings_service.load_tts_settings(
            character_profile=character_profile,
        )
        if not tts_settings.enabled:
            tts_provider = NullTTSProvider()
        elif tts_settings.provider == TTS_PROVIDER_GENIE:
            tts_provider = GenieTTSProvider(tts_settings)
        else:
            tts_provider = GPTSoVITSTTSProvider(tts_settings)
    except TTSConfigError as exc:
        print(f"[TTS] 配置无效，已禁用 TTS：{exc}")
        debug_log("TTS", "配置无效，已禁用 TTS", {"error": str(exc)})
        errors.append(f"TTS 配置无效，已禁用：{exc}")
        tts_provider = NullTTSProvider()
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
    plugin_manager = SakuraPluginManager(base_dir=base_dir)
    try:
        plugin_manager.load_from_config(tool_registry)
    except Exception as exc:  # noqa: BLE001
        print(f"[Plugin] 启动加载失败，已跳过插件：{exc}")
        debug_log("PluginManager", "启动加载失败，已跳过插件", {"error": str(exc)})
        errors.append(f"插件加载失败，已跳过：{exc}")
    for result in plugin_manager.results:
        if result.error:
            errors.append(f"插件 {result.spec.plugin_id or result.spec.entry} 加载失败：{result.error}")
    mcp_settings = settings_service.load_mcp_runtime_settings()
    mcp_tool_provider = register_mcp_tools_from_config(
        base_dir,
        tool_registry,
        runtime_settings=mcp_settings,
    )

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


def build_app_context(base_dir: Path, startup_state: StartupState | None = None) -> AppContext:
    """兼容旧调用：同步创建完整依赖。"""

    context = build_initial_app_context(base_dir, startup_state=startup_state)
    deferred = build_deferred_services(base_dir, context)
    context.agent_runtime.tools = deferred.tool_registry
    context.agent_runtime.set_prompt_patches(deferred.plugin_manager.prompt_patches)
    return AppContext(
        base_dir=context.base_dir,
        settings_service=context.settings_service,
        settings=context.settings,
        character_registry=context.character_registry,
        character_profile=context.character_profile,
        system_prompt=context.system_prompt,
        tts_provider=deferred.tts_provider,
        core=CoreServices(
            api_client=context.api_client,
            tool_registry=deferred.tool_registry,
            agent_runtime=context.agent_runtime,
        ),
        storage=context.storage,
        features=FeatureServices(
            settings_service=context.settings_service,
            extension_registry=deferred.extension_registry,
            mcp_tool_provider=deferred.mcp_tool_provider,
            plugin_manager=deferred.plugin_manager,
            mcp_settings=deferred.mcp_settings,
            debug_log_settings=context.debug_log_settings,
            startup_settings=context.startup_settings,
            memory_curation_settings=context.memory_curation_settings,
            memory_curation_state=context.memory_curation_state,
            memory_curator=context.memory_curator,
            proactive_care_settings=context.proactive_care_settings,
        ),
        startup_initializing=False,
    )


def _normalize_portrait_scale_percent(value: object) -> int:
    try:
        percent = int(str(value).strip())
    except (TypeError, ValueError):
        return PORTRAIT_SCALE_DEFAULT_PERCENT
    return max(PORTRAIT_SCALE_MIN_PERCENT, min(PORTRAIT_SCALE_MAX_PERCENT, percent))


def _create_history_store(base_dir: Path, profile: CharacterProfile) -> ChatHistoryStore:
    history_path = base_dir / "data" / "chat_history" / f"{profile.id}.jsonl"
    _migrate_legacy_history(base_dir, profile, history_path)
    return ChatHistoryStore(history_path, profile.display_name)


def _create_runtime_event_log(base_dir: Path, profile: CharacterProfile) -> RuntimeEventLog:
    """按角色创建运行时事件日志（与聊天历史路径风格一致，但完全独立）。"""
    event_path = base_dir / "data" / "runtime_events" / f"{profile.id}.jsonl"
    return RuntimeEventLog(event_path)


def _create_visual_observation_store(
    base_dir: Path,
    profile: CharacterProfile,
) -> VisualObservationStore:
    visual_path = base_dir / "data" / "visual_observations" / f"{profile.id}.jsonl"
    return VisualObservationStore(visual_path)


def _migrate_legacy_history(base_dir: Path, profile: CharacterProfile, history_path: Path) -> None:
    if profile.id != DEFAULT_CHARACTER_ID or history_path.exists():
        return
    legacy_path = base_dir / "data" / "chat_history.jsonl"
    if not legacy_path.exists():
        return
    try:
        history_path.parent.mkdir(parents=True, exist_ok=True)
        history_path.write_text(legacy_path.read_text(encoding="utf-8"), encoding="utf-8")
    except OSError as exc:
        print(f"[History] 旧历史迁移失败：{exc}")
