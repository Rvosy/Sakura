"""角色主题设置与配置映射。"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from app.config.visual_effect import VisualEffectMode


# ---- 角色主题配置 ----

DEFAULT_PRIMARY_COLOR = "#4b9ac4"
DEFAULT_PRIMARY_HOVER_COLOR = "#3b83aa"
DEFAULT_ACCENT_COLOR = "#e36c96"
DEFAULT_TEXT_COLOR = "#27445a"
DEFAULT_SECONDARY_TEXT_COLOR = "#54768b"
DEFAULT_MUTED_TEXT_COLOR = "#7d99a9"
DEFAULT_PAGE_BACKGROUND_COLOR = "#f8fcfe"
DEFAULT_PANEL_BACKGROUND_COLOR = "#eaf5fa"
DEFAULT_INPUT_BACKGROUND_COLOR = "#ffffff"
DEFAULT_BUBBLE_BACKGROUND_COLOR = "#e3f1f7"
DEFAULT_BORDER_COLOR = "#accfde"

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
