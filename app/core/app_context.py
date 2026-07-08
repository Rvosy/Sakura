from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from app.agent import AgentRuntime, MemoryStore, ReminderStore, ToolRegistry
from app.agent.mcp import MCPToolProvider
from app.agent.mcp.settings import MCPRuntimeSettings
from app.agent.memory_curator import MemoryCurator, MemoryCurationSettings, MemoryCurationState
from app.config.character_loader import CharacterProfile, CharacterRegistry
from app.config.settings_service import AppSettingsService
from app.config.models import DebugLogSettings
from app.core.extensions import ExtensionRegistry
from app.core.resource_manager import ResourceRegistry
from app.llm.api_client import ApiSettings
from app.plugins.manager import PluginManager
from app.storage.chat_history import ChatHistoryStore
from app.agent.runtime_events import RuntimeEventLog
from app.storage.visual_observation import VisualObservationStore
from app.voice.tts import TTSProvider


@dataclass(frozen=True)
class CoreServices:
    """首帧和后台启动都需要的基础核心服务。"""

    api_client: object
    tool_registry: ToolRegistry
    agent_runtime: AgentRuntime


@dataclass(frozen=True)
class StorageServices:
    """数据持久化与状态存储。"""

    memory_store: MemoryStore | None = None
    history_store: ChatHistoryStore | None = None
    visual_observation_store: VisualObservationStore | None = None
    runtime_event_log: RuntimeEventLog | None = None
    reminder_store: ReminderStore | None = None


@dataclass(frozen=True)
class FeatureServices:
    """可选/后台初始化的功能扩展。"""

    settings_service: AppSettingsService | None = None
    extension_registry: ExtensionRegistry | None = None
    mcp_tool_provider: MCPToolProvider | None = None
    plugin_manager: PluginManager | None = None
    mcp_settings: MCPRuntimeSettings | None = None
    debug_log_settings: DebugLogSettings | None = None
    startup_settings: object | None = None
    memory_curation_settings: MemoryCurationSettings | None = None
    memory_curation_state: MemoryCurationState | None = None
    memory_curator: MemoryCurator | None = None
    screen_awareness_settings: object | None = None


@dataclass(frozen=True)
class AppContext:
    """按业务边界分组的运行时上下文；调用方应通过 core / storage / features 显式声明依赖范围。

    废弃：不再提供 20+ 个 @property 快捷方式。若旧代码仍使用 ctx.api_client，
    请改为 ctx.core.api_client；ctx.history_store 改为 ctx.storage.history_store；
    ctx.mcp_settings 改为 ctx.features.mcp_settings，以此类推。
    """

    base_dir: Path
    settings_service: AppSettingsService
    settings: ApiSettings
    character_registry: CharacterRegistry
    character_profile: CharacterProfile
    system_prompt: str
    tts_provider: TTSProvider
    core: CoreServices
    storage: StorageServices
    resource_registry: ResourceRegistry
    features: FeatureServices
    startup_initializing: bool = False

    # 保留向后兼容的迁移别名（将在后续版本中移除）
    @property
    def proactive_care_settings(self) -> object | None:
        return self.features.screen_awareness_settings
