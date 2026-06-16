from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from app.agent.tools import Tool, ToolRegistry
from app.voice.tts import TTSProvider


class ToolContributor(Protocol):
    """向 ToolRegistry 注册工具的最小扩展协议。"""

    def contribute_tools(self) -> list[Tool]:
        """返回需要挂入当前应用的工具。"""


class TTSProviderContributor(Protocol):
    """预留给后续语音 Provider 扩展的协议。"""

    def create_tts_provider(self) -> TTSProvider | None:
        """返回一个 TTS Provider；无可用实现时返回 None。"""


class SettingsContributor(Protocol):
    """预留给后续设置页扩展的协议。"""

    @property
    def settings_section_id(self) -> str:
        """设置页贡献的稳定标识。"""


@dataclass
class ExtensionRegistry:
    """内部扩展注册表；先提供协议边界，不负责发现、安装或依赖管理。"""

    tool_contributors: list[ToolContributor] = field(default_factory=list)
    tts_contributors: list[TTSProviderContributor] = field(default_factory=list)
    settings_contributors: list[SettingsContributor] = field(default_factory=list)

    def register_tool_contributor(self, contributor: ToolContributor) -> None:
        self.tool_contributors.append(contributor)

    def register_tts_contributor(self, contributor: TTSProviderContributor) -> None:
        self.tts_contributors.append(contributor)

    def register_settings_contributor(self, contributor: SettingsContributor) -> None:
        self.settings_contributors.append(contributor)

    def apply_tools(self, registry: ToolRegistry) -> None:
        for contributor in self.tool_contributors:
            for tool in contributor.contribute_tools():
                registry.register(tool)
