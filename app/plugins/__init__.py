"""app/plugins/ — Sakura 原生插件系统。"""

from app.plugins.base import PluginBase, PluginContext
from app.plugins.capabilities import PluginCapabilities, PluginCapabilityRegistry
from app.plugins.discovery import PluginDiscovery
from app.plugins.manager import PluginLoadResult, PluginManager
from app.plugins.models import (
    KNOWN_PLUGIN_PERMISSIONS,
    PERMISSION_CHAT_UI,
    PERMISSION_EVENT_APP,
    PERMISSION_EVENT_CHARACTER,
    PERMISSION_EVENT_MESSAGE,
    PERMISSION_EVENT_TTS,
    PERMISSION_PROMPT_PATCH,
    PERMISSION_SETTINGS_PANEL,
    PERMISSION_TOOL,
    PERMISSION_TOOLS_TAB,
    PLUGIN_API_VERSION,
    ChatUIWidgetContribution,
    PluginEvent,
    PluginManifest,
    PluginManifestView,
    PluginSpec,
    PromptPatchContribution,
    SettingsPanelContribution,
    ToolContribution,
    ToolsTabContribution,
)

__all__ = [
    "ChatUIWidgetContribution",
    "KNOWN_PLUGIN_PERMISSIONS",
    "PERMISSION_CHAT_UI",
    "PERMISSION_EVENT_APP",
    "PERMISSION_EVENT_CHARACTER",
    "PERMISSION_EVENT_MESSAGE",
    "PERMISSION_EVENT_TTS",
    "PERMISSION_PROMPT_PATCH",
    "PERMISSION_SETTINGS_PANEL",
    "PERMISSION_TOOL",
    "PERMISSION_TOOLS_TAB",
    "PLUGIN_API_VERSION",
    "PluginBase",
    "PluginCapabilities",
    "PluginCapabilityRegistry",
    "PluginContext",
    "PluginDiscovery",
    "PluginEvent",
    "PluginLoadResult",
    "PluginManager",
    "PluginManifest",
    "PluginManifestView",
    "PluginSpec",
    "PromptPatchContribution",
    "SettingsPanelContribution",
    "ToolContribution",
    "ToolsTabContribution",
]
