"""旧 SDK 能力注册表兼容导出。"""

from app.plugins.capabilities import PluginCapabilityRegistry
from app.plugins.models import (
    ChatUIWidgetContribution,
    PromptPatchContribution,
    SettingsPanelContribution,
    ToolContribution,
    ToolsTabContribution,
)

__all__ = [
    "ChatUIWidgetContribution",
    "PluginCapabilityRegistry",
    "PromptPatchContribution",
    "SettingsPanelContribution",
    "ToolContribution",
    "ToolsTabContribution",
]
