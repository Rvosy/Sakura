"""app/plugins/models.py — Sakura 原生插件数据模型。"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable


PLUGIN_API_VERSION = 1

PERMISSION_TOOL = "tool"
PERMISSION_TOOLS_TAB = "tools_tab"
PERMISSION_SETTINGS_PANEL = "settings_panel"
PERMISSION_CHAT_UI = "chat_ui"
PERMISSION_PROMPT_PATCH = "prompt_patch"
PERMISSION_CONTEXT_PROVIDER = "context_provider"
PERMISSION_EVENT_APP = "event.app"
PERMISSION_EVENT_MESSAGE = "event.message"
PERMISSION_EVENT_TTS = "event.tts"
PERMISSION_EVENT_CHARACTER = "event.character"

KNOWN_PLUGIN_PERMISSIONS = frozenset(
    {
        PERMISSION_TOOL,
        PERMISSION_TOOLS_TAB,
        PERMISSION_SETTINGS_PANEL,
        PERMISSION_CHAT_UI,
        PERMISSION_PROMPT_PATCH,
        PERMISSION_CONTEXT_PROVIDER,
        PERMISSION_EVENT_APP,
        PERMISSION_EVENT_MESSAGE,
        PERMISSION_EVENT_TTS,
        PERMISSION_EVENT_CHARACTER,
    }
)


@dataclass(frozen=True)
class ToolContribution:
    """插件提供的 Agent 工具贡献。"""

    name: str
    description: str
    parameters: dict[str, Any]
    handler: Callable[[dict[str, Any]], Any] | None = None
    group: str = "default"
    risk: str = "low"
    requires_confirmation: bool = False
    capability: str | None = None


@dataclass(frozen=True)
class ToolsTabContribution:
    """插件贡献到设置窗口“工具”页的面板。"""

    tab_id: str
    title: str
    build: Callable[[Any], Any]
    order: float = 100.0


@dataclass(frozen=True)
class SettingsPanelContribution:
    """插件贡献到设置窗口“插件”页的设置面板。"""

    section_id: str
    title: str
    build: Callable[[Any], Any]
    order: float = 100.0
    plugin_id: str = ""


@dataclass(frozen=True)
class ChatUIWidgetContribution:
    """插件贡献到聊天输入区域的 UI 组件。"""

    widget_id: str
    build: Callable[[Any], Any]
    order: float = 100.0


@dataclass(frozen=True)
class PromptPatchContribution:
    """插件贡献的提示词补丁。"""

    patch_id: str
    system_prompt_append: str = ""
    reply_protocol_append: str = ""


@dataclass(frozen=True)
class ContextProviderContribution:
    """插件贡献的动态上下文提供者。

    与 ``PromptPatchContribution`` 区分职责：
    - PromptPatch 用于修改系统提示词、回复协议（相对静态）。
    - ContextProvider 在每次构建 prompt 时动态生成一段局部上下文（如情绪、
      屏幕摘要），由宿主统一组装，插件不拼完整 prompt。

    ``build_context`` 接收宿主传入的上下文字典（本轮预留为空字典），返回字符串。
    """

    provider_id: str
    description: str
    build_context: Callable[[dict[str, Any]], str]
    order: float = 100.0
    enabled: bool = True


@dataclass(frozen=True)
class PluginManifestView:
    """暴露给插件的只读清单视图。"""

    plugin_id: str
    name: str
    version: str
    description: str = ""
    api_version: int = PLUGIN_API_VERSION
    priority: int = 100
    enabled: bool = True
    required: bool = False
    permissions: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class PluginEvent:
    """宿主派发给插件 hook 的统一事件。"""

    event_type: str
    payload: dict[str, Any] = field(default_factory=dict)
    source: str = "host"


@dataclass(frozen=True)
class PluginManifest:
    """插件的完整清单信息。"""

    plugin_id: str
    name: str = ""
    description: str = ""
    version: str = "0.0.0"
    api_version: int = PLUGIN_API_VERSION
    priority: int = 100
    enabled: bool = True
    required: bool = False
    entry: str = ""
    permissions: tuple[str, ...] = field(default_factory=tuple)
    plugin_root: Path | None = None


@dataclass(frozen=True)
class PluginSpec:
    """插件发现规格。"""

    entry: str
    enabled: bool = True
    priority: int = 100
    plugin_id: str = ""
    name: str = ""
    description: str = ""
    version: str = "0.0.0"
    api_version: int = PLUGIN_API_VERSION
    required: bool = False
    permissions: tuple[str, ...] = field(default_factory=tuple)
    plugin_root: Path | None = None
    source: str = "manifest"
    priority_override: bool = False
