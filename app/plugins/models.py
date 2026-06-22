"""app/plugins/models.py — Sakura 原生插件数据模型。"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Sequence

from app.llm.prompts.types import ContextFragment, ContextRequest


PLUGIN_API_VERSION = 1

# 宿主当前支持的插件 API 版本集合。
# 引入不兼容的新版本时，把新版本号一并加入此集合（如 {1, 2}），即可让宿主
# 同时加载已发布的旧版插件与新版插件，避免一次性破坏全部存量插件。
SUPPORTED_API_VERSIONS = frozenset({PLUGIN_API_VERSION})

PERMISSION_TOOL = "tool"
PERMISSION_TOOLS_TAB = "tools_tab"
PERMISSION_SETTINGS_PANEL = "settings_panel"
PERMISSION_CHAT_UI = "chat_ui"
PERMISSION_PROMPT_PATCH = "prompt_patch"
PERMISSION_CONTEXT_PROVIDER = "context_provider"
PERMISSION_MOBILE_CHAT = "mobile_chat"
PERMISSION_RENDERER = "renderer"
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
        PERMISSION_MOBILE_CHAT,
        PERMISSION_RENDERER,
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
    - ContextProvider 在每次构建 prompt 时，根据本轮 ``ContextRequest`` 动态生成
      若干 ``ContextFragment``（如情绪、屏幕摘要），由宿主统一做信任分级、预算与
      组装，插件不拼完整 prompt。

    ``build_context`` 接收本轮受限事实 ``ContextRequest``，返回 ``ContextFragment``
    序列。宿主会强制覆盖每个片段的 id / source / trust / cache_scope 等元数据，
    插件通常只需提供 ``content``（可选 ``priority`` / ``freshness`` /
    ``token_budget`` / ``sensitivity`` 等建议值）。返回空序列表示本轮不注入。
    """

    provider_id: str
    description: str
    build_context: Callable[[ContextRequest], Sequence[ContextFragment]]
    order: float = 100.0
    enabled: bool = True


@dataclass(frozen=True)
class RendererCreateContext:
    """插件创建角色渲染器时收到的宿主上下文。

    宿主只传递稳定、受限的信息；具体模型路径、动作表等仍由插件根据
    ``renderer_config`` 和 ``package_dir`` 自行解析。
    """

    character_id: str
    character_name: str
    package_dir: Path
    renderer_config: dict[str, Any]
    owner_window: Any | None = None
    event_bus: Any | None = None


@dataclass(frozen=True)
class RendererContribution:
    """插件贡献的角色渲染后端工厂。"""

    renderer_type: str
    display_name: str
    create: Callable[[RendererCreateContext], Any]
    priority: float = 100.0
    plugin_id: str = ""


@dataclass(frozen=True)
class PluginManifestView:
    """暴露给插件的只读清单视图。"""

    plugin_id: str
    name: str
    version: str
    author: str = ""
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
    author: str = ""
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
    author: str = ""
    description: str = ""
    version: str = "0.0.0"
    api_version: int = PLUGIN_API_VERSION
    required: bool = False
    permissions: tuple[str, ...] = field(default_factory=tuple)
    plugin_root: Path | None = None
    source: str = "manifest"
    priority_override: bool = False
