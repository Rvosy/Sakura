"""AppBuilder — 声明式应用装配器。

将 bootstrap.py 中的启动装配流程提取为 Builder 模式，
每个 with_* 方法装配一个子系统，build() 产出 AppContext。

使用方式:
    context = (AppBuilder(base_dir)
        .with_settings()
        .with_character()
        .with_api_client()
        .with_tools()
        .with_mcp()
        .with_tts()
        .with_storage()
        .with_features()
        .build())
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from app.agent import AgentRuntime, MemoryStore, ReminderStore, ToolRegistry, create_builtin_tool_registry
from app.agent.mcp import MCPToolProvider, register_mcp_tools_from_config
from app.agent.mcp.settings import MCPRuntimeSettings
from app.agent.memory_curator import MemoryCurator, MemoryCurationState
from app.config.character_loader import (
    CharacterProfile,
    CharacterRegistry,
    load_character_system_prompt,
)
from app.config.settings_service import AppSettingsService, StartupSettings
from app.core.app_context import AppContext, CoreServices, FeatureServices, StorageServices
from app.core.debug_log import debug_log
from app.core.extensions import ExtensionRegistry
from app.core.plugin_manager import SakuraPluginManager
from app.llm.api_client import ApiSettings, OpenAICompatibleClient
from app.storage.chat_history import ChatHistoryStore
from app.agent.runtime_events import RuntimeEventLog
from app.storage.visual_observation import VisualObservationStore
from app.voice.tts import TTSProvider


class AppBuilder:
    """声明式应用装配器。

    负责将 Sakura 的各子系统按顺序装配为可运行的 AppContext。
    """

    def __init__(self, base_dir: Path) -> None:
        self.base_dir = base_dir
        self._settings_service: AppSettingsService | None = None
        self._api_settings: ApiSettings | None = None
        self._character_registry: CharacterRegistry | None = None
        self._character_profile: CharacterProfile | None = None
        self._system_prompt: str = ""
        self._api_client: OpenAICompatibleClient | None = None
        self._tool_registry: ToolRegistry | None = None
        self._tts_provider: TTSProvider | None = None
        self._memory_store: MemoryStore | None = None
        self._reminder_store: ReminderStore | None = None
        self._history_store: ChatHistoryStore | None = None
        self._runtime_event_log: RuntimeEventLog | None = None
        self._visual_store: VisualObservationStore | None = None
        self._extension_registry: ExtensionRegistry | None = None
        self._mcp_provider: MCPToolProvider | None = None
        self._mcp_settings: MCPRuntimeSettings | None = None
        self._plugin_manager: SakuraPluginManager | None = None
        self._memory_curator: MemoryCurator | None = None
        self._memory_curation_state: MemoryCurationState | None = None
        self._errors: list[str] = []

    # ---- 装配步骤 ----

    def with_settings(self) -> "AppBuilder":
        """加载应用配置 (data/config/*.yaml)。"""
        self._settings_service = AppSettingsService(base_dir=self.base_dir)
        self._api_settings = self._settings_service.load_api_settings()
        return self

    def with_character(self) -> "AppBuilder":
        """加载角色配置 (characters/*/character.json)。"""
        if self._settings_service is None:
            self.with_settings()
        self._character_registry = CharacterRegistry(self.base_dir)
        self._character_profile = self._character_registry.get(
            self._settings_service.load_current_character_id(self._character_registry)
        )
        self._system_prompt = load_character_system_prompt(self._character_profile)
        return self

    def with_api_client(self) -> "AppBuilder":
        """创建 API 客户端。"""
        if self._api_settings is None:
            self.with_settings()
        self._api_client = OpenAICompatibleClient(self._api_settings)
        return self

    def with_tools(self, tool_registry: ToolRegistry | None = None) -> "AppBuilder":
        """装配工具注册表（内置工具 + 外部注册）。"""
        self._tool_registry = tool_registry or create_builtin_tool_registry(self.base_dir)
        return self

    def with_mcp(self) -> "AppBuilder":
        """装配 MCP 工具 Provider。"""
        if self._tool_registry is None:
            self.with_tools()
        if self._settings_service is None:
            self.with_settings()
        self._mcp_settings = self._settings_service.load_mcp_settings()
        self._mcp_provider = MCPToolProvider(self._mcp_settings, base_dir=self.base_dir)
        try:
            register_mcp_tools_from_config(self._mcp_provider, self._tool_registry)
        except Exception as exc:
            debug_log("AppBuilder", "MCP 工具注册失败", {"error": str(exc)})
            self._errors.append(f"MCP: {exc}")
        return self

    def with_tts(self, tts_provider: TTSProvider | None = None) -> "AppBuilder":
        """装配 TTS Provider。"""
        if self._settings_service is None:
            self.with_settings()
        # TTS 创建由 bootstrap 中的 _create_tts_provider 处理
        # 此处只保存外部传入的 provider
        self._tts_provider = tts_provider
        return self

    def with_extensions(self) -> "AppBuilder":
        """装配扩展注册表。"""
        self._extension_registry = ExtensionRegistry()
        if self._tool_registry is not None:
            self._extension_registry.apply_tools(self._tool_registry)
        return self

    def with_plugins(self) -> "AppBuilder":
        """加载本地插件。"""
        if self._tool_registry is None:
            self.with_tools()
        self._plugin_manager = SakuraPluginManager(base_dir=self.base_dir)
        try:
            self._plugin_manager.load_from_config(self._tool_registry)
        except Exception as exc:
            debug_log("AppBuilder", "插件加载失败", {"error": str(exc)})
            self._errors.append(f"Plugin: {exc}")
        return self

    def with_storage(self) -> "AppBuilder":
        """装配存储层。"""
        if self._character_profile is None:
            self.with_character()
        self._memory_store = MemoryStore(base_dir=self.base_dir)
        self._reminder_store = ReminderStore(base_dir=self.base_dir)
        self._history_store = _create_history_store(self.base_dir, self._character_profile)
        self._runtime_event_log = _create_runtime_event_log(self.base_dir, self._character_profile)
        self._visual_store = _create_visual_observation_store(self.base_dir, self._character_profile)
        return self

    def with_features(self) -> "AppBuilder":
        """装配可选功能（记忆整理、主动关怀等）。"""
        if self._settings_service is None:
            self.with_settings()
        self._memory_curation_state = MemoryCurationState()
        self._memory_curator = MemoryCurator(
            base_dir=self.base_dir,
            curation_settings=self._settings_service.load_memory_curation_settings(),
            curation_state=self._memory_curation_state,
        )
        return self

    # ---- 构建 ----

    def build(self) -> AppContext:
        """组装最终的 AppContext。"""
        if self._api_settings is None:
            self.with_settings()
        if self._character_registry is None:
            self.with_character()
        if self._api_client is None:
            self.with_api_client()
        if self._tool_registry is None:
            self.with_tools()
        if self._extension_registry is None:
            self.with_extensions()
        if self._plugin_manager is None:
            self.with_plugins()
        if self._memory_store is None:
            self.with_storage()

        # 创建 AgentRuntime
        agent_runtime = AgentRuntime(
            self._api_client,
            self._system_prompt,
            reply_tones=self._character_profile.reply_tones,
            reply_portraits=list(self._character_profile.portrait_map.keys()),
            tools=self._tool_registry,
            memory=self._memory_store,
            prompt_patches=self._plugin_manager.prompt_patches if self._plugin_manager else [],
        )
        core = CoreServices(
            api_client=self._api_client,
            tool_registry=self._tool_registry,
            agent_runtime=agent_runtime,
        )

        storage = StorageServices(
            memory_store=self._memory_store,
            reminder_store=self._reminder_store,
            history_store=self._history_store,
            visual_observation_store=self._visual_store,
            runtime_event_log=self._runtime_event_log,
        )

        debug_log_settings = (
            self._settings_service.load_debug_log_settings()
            if self._settings_service else None
        )
        startup_settings = (
            self._settings_service.load_startup_settings()
            if self._settings_service else StartupSettings()
        )

        features = FeatureServices(
            settings_service=self._settings_service,
            extension_registry=self._extension_registry,
            mcp_tool_provider=self._mcp_provider,
            plugin_manager=self._plugin_manager,
            mcp_settings=self._mcp_settings or MCPRuntimeSettings(),
            debug_log_settings=debug_log_settings,
            startup_settings=startup_settings,
            memory_curation_settings=(self._memory_curator.curation_settings if self._memory_curator else None),
            memory_curation_state=self._memory_curation_state or MemoryCurationState(),
            memory_curator=self._memory_curator,
            proactive_care_settings=(self._settings_service.load_proactive_care_settings() if self._settings_service else None),
        )

        return AppContext(
            base_dir=self.base_dir,
            settings_service=self._settings_service,
            settings=self._api_settings,
            character_registry=self._character_registry,
            character_profile=self._character_profile,
            system_prompt=self._system_prompt,
            tts_provider=self._tts_provider,
            core=core,
            storage=storage,
            features=features,
        )

    @property
    def errors(self) -> list[str]:
        """装配过程中收集的错误。"""
        return list(self._errors)


# ---- 辅助函数 (从 bootstrap.py 迁移) ----

def _create_history_store(base_dir: Path, profile: CharacterProfile) -> ChatHistoryStore:
    """为角色创建聊天历史存储。"""
    history_path = base_dir / "data" / "chat_history" / f"{profile.id}.jsonl"
    return ChatHistoryStore(history_path, profile.display_name)


def _create_runtime_event_log(base_dir: Path, profile: CharacterProfile) -> RuntimeEventLog:
    """为角色创建运行时事件日志（与聊天历史路径风格一致，但完全独立）。"""
    event_path = base_dir / "data" / "runtime_events" / f"{profile.id}.jsonl"
    return RuntimeEventLog(event_path)


def _create_visual_observation_store(
    base_dir: Path, profile: CharacterProfile,
) -> VisualObservationStore:
    """为角色创建视觉观察存储。"""
    visual_path = base_dir / "data" / "visual_observations" / f"{profile.id}.jsonl"
    return VisualObservationStore(visual_path)
