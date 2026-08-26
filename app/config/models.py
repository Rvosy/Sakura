"""app/config/models.py — 集中管理的配置数据模型。

将所有配置 dataclass 集中到此模块，便于：
- 统一管理默认值
- 配置迁移
- 测试验证
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from app.config.visual_effect import VisualEffectMode


MODEL_SLOT_CHAT = "chat"
MODEL_SLOT_VISION_CHAT = "vision_chat"
MODEL_SLOT_MEMORY_CURATION = "memory_curation"

MODEL_SLOT_ORDER = (
    MODEL_SLOT_CHAT,
    MODEL_SLOT_VISION_CHAT,
    MODEL_SLOT_MEMORY_CURATION,
)

MODEL_SLOT_UI_ORDER = (
    MODEL_SLOT_CHAT,
    MODEL_SLOT_VISION_CHAT,
    MODEL_SLOT_MEMORY_CURATION,
)

MODEL_SLOT_LABELS = {
    MODEL_SLOT_CHAT: "聊天模型",
    MODEL_SLOT_VISION_CHAT: "视觉模型",
    MODEL_SLOT_MEMORY_CURATION: "记忆整理模型",
}

MODEL_SLOT_DESCRIPTIONS = {
    MODEL_SLOT_CHAT: "全局默认的角色聊天模型，必填。",
    MODEL_SLOT_VISION_CHAT: "当聊天模型不支持图片，或想要自定义视觉模型时使用；留空则由聊天模型直接看原图。",
    MODEL_SLOT_MEMORY_CURATION: "用于自动整理长期记忆；留空则继承聊天模型。",
}

MODEL_SLOT_FALLBACKS = {
    MODEL_SLOT_VISION_CHAT: (MODEL_SLOT_CHAT,),
    MODEL_SLOT_MEMORY_CURATION: (MODEL_SLOT_CHAT,),
}


# ---- 角色主题配置 ----

DEFAULT_PRIMARY_COLOR = "#d55b91"
DEFAULT_PRIMARY_HOVER_COLOR = "#bf3f7a"
DEFAULT_ACCENT_COLOR = "#b13e73"
DEFAULT_TEXT_COLOR = "#3d2b35"
DEFAULT_SECONDARY_TEXT_COLOR = "#7a3656"
DEFAULT_MUTED_TEXT_COLOR = "#9b4f72"
DEFAULT_PAGE_BACKGROUND_COLOR = "#fff6fa"
DEFAULT_PANEL_BACKGROUND_COLOR = "#ffe8f1"
DEFAULT_INPUT_BACKGROUND_COLOR = "#ffffff"
DEFAULT_BUBBLE_BACKGROUND_COLOR = "#ffe8f1"
DEFAULT_BORDER_COLOR = "#eeacc8"

THEME_COLOR_FIELDS: tuple[tuple[str, str, str], ...] = (
    ("primary_color", "主题色", DEFAULT_PRIMARY_COLOR),
    ("primary_hover_color", "按钮悬停色", DEFAULT_PRIMARY_HOVER_COLOR),
    ("accent_color", "强调色", DEFAULT_ACCENT_COLOR),
    ("text_color", "主文字色", DEFAULT_TEXT_COLOR),
    ("secondary_text_color", "次级文字色", DEFAULT_SECONDARY_TEXT_COLOR),
    ("muted_text_color", "弱提示文字色", DEFAULT_MUTED_TEXT_COLOR),
    ("page_background_color", "页面背景色", DEFAULT_PAGE_BACKGROUND_COLOR),
    ("panel_background_color", "面板背景色", DEFAULT_PANEL_BACKGROUND_COLOR),
    ("input_background_color", "输入框背景色", DEFAULT_INPUT_BACKGROUND_COLOR),
    ("bubble_background_color", "气泡背景色", DEFAULT_BUBBLE_BACKGROUND_COLOR),
    ("border_color", "边框色", DEFAULT_BORDER_COLOR),
)
_HEX_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")


@dataclass(frozen=True)
class ThemeSettings:
    """桌宠 UI 主题配置。"""

    primary_color: str = DEFAULT_PRIMARY_COLOR
    primary_hover_color: str = DEFAULT_PRIMARY_HOVER_COLOR
    accent_color: str = DEFAULT_ACCENT_COLOR
    text_color: str = DEFAULT_TEXT_COLOR
    secondary_text_color: str = DEFAULT_SECONDARY_TEXT_COLOR
    muted_text_color: str = DEFAULT_MUTED_TEXT_COLOR
    page_background_color: str = DEFAULT_PAGE_BACKGROUND_COLOR
    panel_background_color: str = DEFAULT_PANEL_BACKGROUND_COLOR
    input_background_color: str = DEFAULT_INPUT_BACKGROUND_COLOR
    bubble_background_color: str = DEFAULT_BUBBLE_BACKGROUND_COLOR
    border_color: str = DEFAULT_BORDER_COLOR
    ai_enabled: bool = False
    visual_effect_mode: str = "gaussian_blur"

    def normalized(self) -> "ThemeSettings":
        return ThemeSettings(
            primary_color=normalize_hex_color(self.primary_color, DEFAULT_PRIMARY_COLOR),
            primary_hover_color=normalize_hex_color(self.primary_hover_color, DEFAULT_PRIMARY_HOVER_COLOR),
            accent_color=normalize_hex_color(self.accent_color, DEFAULT_ACCENT_COLOR),
            text_color=normalize_hex_color(self.text_color, DEFAULT_TEXT_COLOR),
            secondary_text_color=normalize_hex_color(self.secondary_text_color, DEFAULT_SECONDARY_TEXT_COLOR),
            muted_text_color=normalize_hex_color(self.muted_text_color, DEFAULT_MUTED_TEXT_COLOR),
            page_background_color=normalize_hex_color(self.page_background_color, DEFAULT_PAGE_BACKGROUND_COLOR),
            panel_background_color=normalize_hex_color(self.panel_background_color, DEFAULT_PANEL_BACKGROUND_COLOR),
            input_background_color=normalize_hex_color(self.input_background_color, DEFAULT_INPUT_BACKGROUND_COLOR),
            bubble_background_color=normalize_hex_color(self.bubble_background_color, DEFAULT_BUBBLE_BACKGROUND_COLOR),
            border_color=normalize_hex_color(self.border_color, DEFAULT_BORDER_COLOR),
            ai_enabled=bool(self.ai_enabled),
            visual_effect_mode=VisualEffectMode.validate(self.visual_effect_mode),
        )


DEFAULT_THEME_SETTINGS = ThemeSettings()


def normalize_hex_color(value: object, default: str) -> str:
    text = str(value or "").strip()
    if not text:
        return default
    if not text.startswith("#"):
        text = f"#{text}"
    if not _HEX_COLOR_RE.match(text):
        return default
    return text.lower()


def mix_theme_color(color: str, other: str, weight: float) -> str:
    """Blend two theme colors and return a normalized ``#rrggbb`` value."""

    def rgb(value: str) -> tuple[int, int, int]:
        normalized = normalize_hex_color(value, DEFAULT_PRIMARY_COLOR).lstrip("#")
        return int(normalized[0:2], 16), int(normalized[2:4], 16), int(normalized[4:6], 16)

    red, green, blue = rgb(color)
    other_red, other_green, other_blue = rgb(other)
    clamped = max(0.0, min(1.0, weight))
    mixed = (
        round(red * (1 - clamped) + other_red * clamped),
        round(green * (1 - clamped) + other_green * clamped),
        round(blue * (1 - clamped) + other_blue * clamped),
    )
    return "#{:02x}{:02x}{:02x}".format(*mixed)


def theme_from_mapping(data: Any) -> ThemeSettings:
    if not isinstance(data, dict):
        return DEFAULT_THEME_SETTINGS
    values = {
        name: normalize_hex_color(data.get(name), default)
        for name, _label, default in THEME_COLOR_FIELDS
    }
    return ThemeSettings(
        **values,
        ai_enabled=_theme_bool_value(data.get("ai_enabled"), False),
        visual_effect_mode=VisualEffectMode.validate(
            str(data.get("visual_effect_mode", VisualEffectMode.DEFAULT))
        ),
    )


def theme_colors_to_mapping(settings: ThemeSettings) -> dict[str, object]:
    normalized = settings.normalized()
    return {
        name: getattr(normalized, name)
        for name, _label, _default in THEME_COLOR_FIELDS
    }


def theme_to_mapping(settings: ThemeSettings) -> dict[str, object]:
    data = theme_colors_to_mapping(settings)
    normalized = settings.normalized()
    data["ai_enabled"] = bool(normalized.ai_enabled)
    data["visual_effect_mode"] = normalized.visual_effect_mode
    return data


def _theme_bool_value(value: object, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    normalized = str(value).strip().lower()
    if normalized in {"1", "true", "yes", "on", "enabled"}:
        return True
    if normalized in {"0", "false", "no", "off", "disabled"}:
        return False
    return default


# ---- API 配置 ----

@dataclass(frozen=True)
class ApiSettings:
    """LLM API 连接配置。"""

    base_url: str = ""
    api_key: str = field(default="", repr=False)
    model: str = ""
    timeout_seconds: int = 60
    # 角色对话生成参数；None 表示沿用内置默认/不发送该参数，保持历史行为。
    temperature: float | None = None
    top_p: float | None = None
    max_tokens: int | None = None
    context_window_tokens: int = 32_768
    context_window_source: str = "fallback"


@dataclass(frozen=True)
class ApiConfigProfile:
    """单条 API 供应商配置，包含 base_url、api_key、别名和该供应商模型列表。

    模型列表属于供应商，功能槽位只能选择某个供应商下已添加的模型。
    """

    id: str
    alias: str
    base_url: str
    api_key: str = field(default="", repr=False)
    models: tuple[str, ...] = ()


@dataclass(frozen=True)
class ModelSlotSelection:
    """某个功能槽位选中的供应商和模型；空值表示继承。"""

    profile_id: str = ""
    model: str = ""
    context_window_tokens: int | None = None

    @property
    def configured(self) -> bool:
        return bool(self.profile_id.strip() and self.model.strip())


@dataclass(frozen=True)
class ModelSelectionSettings:
    """各功能实际使用的模型配置。"""

    chat: ModelSlotSelection = field(default_factory=ModelSlotSelection)
    vision_chat: ModelSlotSelection | None = None
    memory_curation: ModelSlotSelection | None = None

    def get(self, slot: str) -> ModelSlotSelection | None:
        if slot == MODEL_SLOT_CHAT:
            return self.chat
        if slot == MODEL_SLOT_VISION_CHAT:
            return self.vision_chat
        if slot == MODEL_SLOT_MEMORY_CURATION:
            return self.memory_curation
        return None

    @property
    def vision_profile_id(self) -> str:
        selection = self.vision_chat or self.chat
        return selection.profile_id

    @property
    def vision_model(self) -> str:
        selection = self.vision_chat or self.chat
        return selection.model

    @property
    def text_enabled(self) -> bool:
        return self.vision_chat is not None

    @property
    def text_profile_id(self) -> str:
        return self.chat.profile_id

    @property
    def text_model(self) -> str:
        return self.chat.model


# ---- 运行日志 ----

@dataclass(frozen=True)
class DebugLogSettings:
    """运行日志配置。"""

    enabled: bool = True
    body_enabled: bool = False
    file_enabled: bool = True
    profile: str = "info"
    # 开发者选项:舞台调试框(画窗口/布局/实际立绘三框 + DPR 数值,排查布局/HiDPI)。
    stage_debug_overlay: bool = False
    # 舞台碰撞遮罩(默认开):setMask 到内容矩形并集,立绘四周空白点击穿透,避免误拖/挡点击。
    stage_collision_mask: bool = True


# ---- TTS 配置 (存根，实际实现在 app/voice/tts_settings.py) ----
# GPTSoVITSTTSSettings 在 app/voice/tts_settings.py 中定义，
# 因其包含 validate() 等逻辑方法，不适合纯数据容器。


# ---- MCP 运行时 ----
# MCPRuntimeSettings 在 app/agent/mcp/settings.py 中定义


# ---- 主动屏幕感知 ----
# ScreenAwarenessSettings 在 app/agent/screen_awareness.py 中定义


# ---- 记忆整理 ----
# MemoryCurationSettings 在 app/agent/memory_curator.py 中定义
