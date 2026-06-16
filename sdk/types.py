"""旧 SDK 类型兼容导出。

这些名称映射到当前 ``app.plugins`` 的公开类型，保证旧插件贡献对象能被
当前插件管理器直接消费。
"""

from app.plugins.models import (
    ChatUIWidgetContribution,
    PluginManifestView,
    PromptPatchContribution,
    SettingsPanelContribution,
    ToolContribution,
    ToolsTabContribution,
)

__all__ = [
    "ChatUIWidgetContribution",
    "PluginManifestView",
    "PromptPatchContribution",
    "SettingsPanelContribution",
    "ToolContribution",
    "ToolsTabContribution",
]
