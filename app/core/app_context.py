from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from app.agent import AgentRuntime, MemoryStore, ReminderStore, ToolRegistry
from app.agent.mcp import MCPRuntimeSettings, MCPToolProvider
from app.agent.memory_curator import MemoryCurator, MemoryCurationSettings, MemoryCurationState
from app.config.settings_service import AppSettingsService, DebugLogSettings, StartupSettings
from app.llm.api_client import ApiSettings, OpenAICompatibleClient
from app.config.character_loader import CharacterProfile, CharacterRegistry
from app.storage.chat_history import ChatHistoryStore
from app.agent.runtime_events import RuntimeEventLog
from app.core.extensions import ExtensionRegistry
from app.agent.proactive_care import ProactiveCareSettings
from app.voice.tts import TTSProvider
from app.storage.visual_observation import VisualObservationStore
from app.plugins.manager import PluginManager


@dataclass(frozen=True)
class CoreServices:
    """聊天运行时和工具注册等核心服务。"""

    api_client: OpenAICompatibleClient
    tool_registry: ToolRegistry
    agent_runtime: AgentRuntime


@dataclass(frozen=True)
class StorageServices:
    """本地持久化存储服务。"""

    memory_store: MemoryStore
    reminder_store: ReminderStore
    history_store: ChatHistoryStore
    visual_observation_store: VisualObservationStore
    runtime_event_log: RuntimeEventLog


@dataclass(frozen=True)
class FeatureServices:
    """可选功能和后台维护服务。"""

    settings_service: AppSettingsService
    extension_registry: ExtensionRegistry
    mcp_tool_provider: MCPToolProvider | None
    plugin_manager: PluginManager
    mcp_settings: MCPRuntimeSettings
    debug_log_settings: DebugLogSettings
    startup_settings: StartupSettings
    memory_curation_settings: MemoryCurationSettings
    memory_curation_state: MemoryCurationState
    memory_curator: MemoryCurator
    proactive_care_settings: ProactiveCareSettings


@dataclass(frozen=True)
class AppContext:
    """应用启动阶段组装出的核心依赖。"""

    base_dir: Path
    settings_service: AppSettingsService
    settings: ApiSettings
    character_registry: CharacterRegistry
    character_profile: CharacterProfile
    system_prompt: str
    tts_provider: TTSProvider
    core: CoreServices
    storage: StorageServices
    features: FeatureServices
    startup_initializing: bool = False

    @property
    def api_client(self) -> OpenAICompatibleClient:
        return self.core.api_client

    @property
    def tool_registry(self) -> ToolRegistry:
        return self.core.tool_registry

    @property
    def agent_runtime(self) -> AgentRuntime:
        return self.core.agent_runtime

    @property
    def memory_store(self) -> MemoryStore:
        return self.storage.memory_store

    @property
    def reminder_store(self) -> ReminderStore:
        return self.storage.reminder_store

    @property
    def history_store(self) -> ChatHistoryStore:
        return self.storage.history_store

    @property
    def visual_observation_store(self) -> VisualObservationStore:
        return self.storage.visual_observation_store

    @property
    def runtime_event_log(self) -> RuntimeEventLog:
        return self.storage.runtime_event_log

    @property
    def extension_registry(self) -> ExtensionRegistry:
        return self.features.extension_registry

    @property
    def mcp_tool_provider(self) -> MCPToolProvider | None:
        return self.features.mcp_tool_provider

    @property
    def plugin_manager(self) -> PluginManager:
        return self.features.plugin_manager

    @property
    def mcp_settings(self) -> MCPRuntimeSettings:
        return self.features.mcp_settings

    @property
    def debug_log_settings(self) -> DebugLogSettings:
        return self.features.debug_log_settings

    @property
    def startup_settings(self) -> StartupSettings:
        return self.features.startup_settings

    @property
    def memory_curation_settings(self) -> MemoryCurationSettings:
        return self.features.memory_curation_settings

    @property
    def memory_curation_state(self) -> MemoryCurationState:
        return self.features.memory_curation_state

    @property
    def memory_curator(self) -> MemoryCurator:
        return self.features.memory_curator

    @property
    def proactive_care_settings(self) -> ProactiveCareSettings:
        return self.features.proactive_care_settings
