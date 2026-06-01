from __future__ import annotations

from pathlib import Path

from app.agent import AgentRuntime, MemoryStore, ReminderStore, create_builtin_tool_registry
from app.agent.mcp import register_mcp_tools_from_config
from app.agent.memory_curator import MemoryCurator, MemoryCurationState
from app.config.settings_service import AppSettingsService
from app.llm.api_client import OpenAICompatibleClient
from app.core.app_context import AppContext, CoreServices, FeatureServices, StorageServices
from app.core.extensions import ExtensionRegistry
from app.config.character_loader import (
    DEFAULT_CHARACTER_ID,
    CharacterProfile,
    CharacterRegistry,
    load_character_system_prompt,
)
from app.storage.chat_history import ChatHistoryStore
from app.debug_log import debug_log
from app.voice.tts import GPTSoVITSTTSProvider, NullTTSProvider, TTSConfigError
from app.storage.visual_observation import VisualObservationStore
from app.core.plugin_manager import SakuraPluginManager


def build_app_context(base_dir: Path) -> AppContext:
    """加载启动配置并创建主窗口所需的核心依赖。"""

    settings_service = AppSettingsService(base_dir=base_dir)
    settings = settings_service.load_api_settings()
    api_client = OpenAICompatibleClient(settings)
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

    try:
        tts_settings = settings_service.load_tts_settings(
            character_profile=character_profile,
        )
        tts_provider = (
            GPTSoVITSTTSProvider(tts_settings)
            if tts_settings.enabled
            else NullTTSProvider()
        )
    except TTSConfigError as exc:
        print(f"[TTS] 配置无效，已禁用 TTS：{exc}")
        debug_log("TTS", "配置无效，已禁用 TTS", {"error": str(exc)})
        tts_provider = NullTTSProvider()
    debug_log(
        "Startup",
        "TTS Provider 已创建",
        {"provider": type(tts_provider).__name__},
    )

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
    plugin_manager.load_from_config(tool_registry)
    mcp_settings = settings_service.load_mcp_runtime_settings()
    mcp_tool_provider = register_mcp_tools_from_config(
        base_dir,
        tool_registry,
        runtime_settings=mcp_settings,
    )
    agent_runtime = AgentRuntime(
        api_client=api_client,
        system_prompt=system_prompt,
        reply_tones=character_profile.reply_tones,
        reply_portraits=character_profile.portrait_choices,
        tools=tool_registry,
        memory=memory_store,
    )
    history_store = _create_history_store(base_dir, character_profile)
    visual_observation_store = _create_visual_observation_store(base_dir, character_profile)
    debug_log_settings = settings_service.load_debug_log_settings()
    memory_curation_settings = settings_service.load_memory_curation_settings()
    memory_curation_state = MemoryCurationState(
        base_dir / "data" / "memory_curation_state.json"
    )
    memory_curator = MemoryCurator(api_client, memory_store)
    proactive_care_settings = settings_service.load_proactive_care_settings()

    debug_log(
        "Startup",
        "核心服务已创建",
        {
            "tool_count": len(tool_registry.all()),
            "mcp_enabled": mcp_tool_provider is not None,
            "windows_mcp_enabled": mcp_settings.windows_enabled,
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
        tts_provider=tts_provider,
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
        ),
        features=FeatureServices(
            settings_service=settings_service,
            extension_registry=extension_registry,
            mcp_tool_provider=mcp_tool_provider,
            plugin_manager=plugin_manager,
            mcp_settings=mcp_settings,
            debug_log_settings=debug_log_settings,
            memory_curation_settings=memory_curation_settings,
            memory_curation_state=memory_curation_state,
            memory_curator=memory_curator,
            proactive_care_settings=proactive_care_settings,
        ),
    )


def _create_history_store(base_dir: Path, profile: CharacterProfile) -> ChatHistoryStore:
    history_path = base_dir / "data" / "chat_history" / f"{profile.id}.jsonl"
    _migrate_legacy_history(base_dir, profile, history_path)
    return ChatHistoryStore(history_path, profile.display_name)


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
