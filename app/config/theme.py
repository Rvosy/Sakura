"""不依赖 Qt 的 Sakura 主题数据模型与序列化函数。"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from typing import Any


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
DEFAULT_VISUAL_EFFECT_MODE = "gaussian_blur"
VISUAL_EFFECT_MODES = frozenset(
    {"solid", "gaussian_blur", "windows_acrylic", "macos_visual_effect"}
)

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
    visual_effect_mode: str = DEFAULT_VISUAL_EFFECT_MODE

    def normalized(self) -> "ThemeSettings":
        values = {
            field: normalize_hex_color(getattr(self, field), default)
            for field, _label, default in THEME_COLOR_FIELDS
        }
        return ThemeSettings(
            **values,
            ai_enabled=bool(self.ai_enabled),
            visual_effect_mode=normalize_visual_effect_mode(self.visual_effect_mode),
        )


DEFAULT_THEME_SETTINGS = ThemeSettings()


def normalize_visual_effect_mode(value: object) -> str:
    text = str(value or "").strip()
    return text if text in VISUAL_EFFECT_MODES else DEFAULT_VISUAL_EFFECT_MODE


def normalize_hex_color(value: object, default: str) -> str:
    text = str(value or "").strip()
    if not text:
        return default
    if not text.startswith("#"):
        text = f"#{text}"
    return text.lower() if _HEX_COLOR_RE.match(text) else default


def theme_from_mapping(data: Any) -> ThemeSettings:
    if not isinstance(data, dict):
        return DEFAULT_THEME_SETTINGS
    values = {
        field: normalize_hex_color(data.get(field), default)
        for field, _label, default in THEME_COLOR_FIELDS
    }
    return ThemeSettings(
        **values,
        ai_enabled=_bool_value(data.get("ai_enabled"), False),
        visual_effect_mode=normalize_visual_effect_mode(data.get("visual_effect_mode")),
    )


def theme_colors_to_mapping(settings: ThemeSettings) -> dict[str, object]:
    normalized = settings.normalized()
    return {
        field: getattr(normalized, field)
        for field, _label, _default in THEME_COLOR_FIELDS
    }


def theme_to_mapping(settings: ThemeSettings) -> dict[str, object]:
    normalized = settings.normalized()
    data = theme_colors_to_mapping(normalized)
    data["ai_enabled"] = normalized.ai_enabled
    data["visual_effect_mode"] = normalized.visual_effect_mode
    return data


def resolve_effective_theme(
    profile: Any | None,
    override: ThemeSettings | None = None,
    user_ui_settings: ThemeSettings | None = None,
) -> ThemeSettings:
    """解析当前角色主题，不依赖 Qt UI 模块。"""
    from app.config.character_loader import THEME_SOURCE_PACKAGE

    user = (user_ui_settings or DEFAULT_THEME_SETTINGS).normalized()
    if override is not None:
        colors = override.normalized()
    elif profile is not None and getattr(profile, "theme_source", None) == THEME_SOURCE_PACKAGE:
        colors = (getattr(profile, "theme_settings", None) or DEFAULT_THEME_SETTINGS).normalized()
    else:
        colors = DEFAULT_THEME_SETTINGS
    return replace(
        colors,
        visual_effect_mode=user.visual_effect_mode,
        ai_enabled=user.ai_enabled,
    )


def _bool_value(value: object, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
    return default
