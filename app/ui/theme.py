from __future__ import annotations

import json
import re
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.config.character_loader import CharacterProfile


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
        from app.ui.window_backdrop import VisualEffectMode

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
_SETTINGS_ARROW_DOWN_URL = (
    Path(__file__).with_name("assets").joinpath("chevron-down.svg").resolve().as_posix()
)
_SETTINGS_ARROW_UP_URL = (
    Path(__file__).with_name("assets").joinpath("chevron-up.svg").resolve().as_posix()
)
_MENU_CHECK_URL = Path(__file__).with_name("assets").joinpath("menu-check.svg").resolve().as_posix()


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
    from app.ui.window_backdrop import VisualEffectMode

    if not isinstance(data, dict):
        return DEFAULT_THEME_SETTINGS
    values = {
        field: normalize_hex_color(data.get(field), default)
        for field, _label, default in THEME_COLOR_FIELDS
    }
    return ThemeSettings(
        **values,
        ai_enabled=_bool_value(data.get("ai_enabled"), False),
        visual_effect_mode=VisualEffectMode.validate(str(data.get("visual_effect_mode", VisualEffectMode.DEFAULT))),
    )


def theme_to_mapping(settings: ThemeSettings) -> dict[str, object]:
    data = theme_colors_to_mapping(settings)
    normalized = settings.normalized()
    data["ai_enabled"] = bool(normalized.ai_enabled)
    data["visual_effect_mode"] = normalized.visual_effect_mode
    return data


def merge_theme_with_character(
    saved_settings: ThemeSettings,
    profile: CharacterProfile | None,
) -> ThemeSettings:
    """合并已保存主题与角色包主题，保留用户级偏好字段。

    角色包主题只贡献配色；visual_effect_mode 和 ai_enabled 是用户级偏好
    （character.json 不序列化这两个字段），始终沿用已保存的值。
    """
    from app.config.character_loader import THEME_SOURCE_PACKAGE

    saved = saved_settings.normalized()
    if profile is not None and profile.theme_source == THEME_SOURCE_PACKAGE:
        theme = (profile.theme_settings or DEFAULT_THEME_SETTINGS).normalized()
        return replace(
            theme,
            visual_effect_mode=saved.visual_effect_mode,
            ai_enabled=saved.ai_enabled,
        )
    return saved


def theme_colors_to_mapping(settings: ThemeSettings) -> dict[str, object]:
    normalized = settings.normalized()
    return {
        field: getattr(normalized, field)
        for field, _label, _default in THEME_COLOR_FIELDS
    }


def _extract_json_text(raw_text: str) -> str:
    """从 AI 返回的原始文本中提取 JSON，兼容纯 JSON 和 Markdown 代码块。"""
    text = raw_text.strip()
    if not text:
        return ""
    # 尝试从 ```json ... ``` 代码块中提取
    block_match = re.search(r"```(?:json)?\s*(.*?)\s*```", text, re.DOTALL)
    if block_match:
        text = block_match.group(1).strip()
        if text:
            return text
    # 提取第一个 { 到最后一个 } 之间的内容（兼容混有解释文字的情况）
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        return text[start : end + 1]
    return text




def parse_ai_theme_response(raw_text: str, *, ai_enabled: bool) -> ThemeSettings:
    """解析 AI 返回的主题 JSON；失败时抛出可展示给用户的 ValueError。"""

    try:
        data = json.loads(_extract_json_text(raw_text))
    except json.JSONDecodeError as exc:
        raise ValueError("AI 返回内容不是有效 JSON。") from exc
    if not isinstance(data, dict):
        raise ValueError("AI 返回 JSON 必须是对象。")

    missing = [field for field, _label, _default in THEME_COLOR_FIELDS if field not in data]
    if missing:
        raise ValueError(f"AI 返回缺少字段：{', '.join(missing)}。")

    values: dict[str, str] = {}
    for field, _label, _default in THEME_COLOR_FIELDS:
        raw_color = str(data[field]).strip()
        normalized = normalize_hex_color(raw_color, "")
        if not normalized or normalized != raw_color.lower():
            raise ValueError("AI 返回的颜色必须是 #RRGGBB 格式。")
        values[field] = normalized
    return ThemeSettings(**values, ai_enabled=ai_enabled).normalized()


def build_color_button_stylesheet(color: str) -> str:
    safe_color = normalize_hex_color(color, DEFAULT_PRIMARY_COLOR)
    return (
        f"background: {safe_color};"
        "border: 1px solid rgba(0, 0, 0, 0.18);"
        "border-radius: 8px;"
        "min-width: 38px;"
    )


def build_pet_window_stylesheet(settings: ThemeSettings) -> str:
    theme = settings.normalized()
    return f"""
#speechBubble {{
    background: {rgba(theme.bubble_background_color, 238)};
    border: 1px solid {rgba(theme.border_color, 170)};
    border-radius: 20px;
}}
#speakerName {{
    color: {theme.primary_color};
    font-size: 13px;
    font-weight: 700;
}}
#speechText {{
    color: {theme.text_color};
    font-size: 19px;
    line-height: 1.35;
}}
#ttsErrorText {{
    color: #9f314e;
    font-size: 12px;
    font-weight: 700;
    line-height: 1.25;
}}
#replyHistoryPanel {{
    background: rgba(255, 255, 255, 92);
    border: 1px solid {rgba(theme.border_color, 154)};
    border-radius: 17px;
}}
#replyHistoryButton {{
    background: transparent;
    border: none;
    border-radius: 13px;
    color: {theme.secondary_text_color};
    font-size: 15px;
    font-weight: 900;
}}
#replyHistoryButton:hover {{
    background: transparent;
    border: none;
    color: {theme.accent_color};
}}
#replyHistoryButton:disabled {{
    background: transparent;
    color: {rgba(theme.secondary_text_color, 92)};
}}
#inputBar {{
    background: transparent;
    border: none;
}}
#inputBar[visualEffectMode="solid"] {{
    background: {rgba(theme.bubble_background_color, 238)};
    border: 1px solid {rgba(theme.border_color, 170)};
    border-radius: 22px;
}}
#petInput {{
    background: {rgba(theme.input_background_color, 55)};
    border: 1px solid rgba(255, 255, 255, 218);
    border-radius: 19px;
    color: {mix(theme.text_color, "#000000", 0.08)};
    font-size: 15px;
    font-weight: 700;
    padding: 3px 16px;
    selection-background-color: {rgba(theme.primary_color, 92)};
}}
#petInput[visualEffectMode="solid"] {{
    background: {rgba(theme.input_background_color, 235)};
    border: 1px solid {rgba(theme.border_color, 148)};
}}
#petInput:focus {{
    background: {rgba(theme.input_background_color, 90)};
    border: 1px solid {rgba(theme.primary_color, 210)};
}}
#petInput[visualEffectMode="solid"]:focus {{
    background: {theme.input_background_color};
    border: 1px solid {rgba(theme.primary_color, 210)};
}}
#petInput:disabled {{
    color: {rgba(mix(theme.text_color, "#000000", 0.08), 150)};
}}
#sendButton, #screenshotButton {{
    background: {rgba(theme.primary_color, 232)};
    border: 1px solid rgba(255, 255, 255, 150);
    border-radius: 19px;
    color: white;
    font-size: 15px;
    font-weight: 800;
    padding: 4px 12px;
}}
#sendButton {{
    border-radius: 16px;
    min-width: 50px;
    padding: 4px 10px;
}}
#screenshotButton {{
    width: 36px;
    height: 36px;
    min-width: 36px;
    max-width: 36px;
    min-height: 36px;
    max-height: 36px;
    padding: 0;
}}
#sendButton:hover, #screenshotButton:hover {{
    background: {rgba(theme.primary_hover_color, 242)};
    border: 1px solid {rgba(mix(theme.panel_background_color, "#ffffff", 0.35), 190)};
}}
#screenshotButton[screenshotAttached="true"] {{
    background: {rgba(theme.accent_color, 242)};
    border: 1px solid {rgba(mix(theme.panel_background_color, "#ffffff", 0.25), 220)};
    color: white;
}}
#sendButton:disabled, #screenshotButton:disabled {{
    background: {rgba(theme.primary_color, 118)};
    border: 1px solid {rgba(theme.border_color, 92)};
    color: rgba(255, 255, 255, 178);
}}
#sendButton[replyWaiting="true"] {{
    background: {rgba(theme.primary_color, 146)};
    border: 1px solid {rgba(theme.primary_color, 174)};
    color: rgba(255, 255, 255, 218);
}}
#sendButton[replyWaiting="true"]:disabled {{
    background: {rgba(theme.primary_color, 146)};
    border: 1px solid {rgba(theme.primary_color, 174)};
    color: rgba(255, 255, 255, 218);
}}
#confirmActionButton {{
    background: rgba(93, 181, 130, 225);
    border: none;
    border-radius: 16px;
    color: white;
    font-size: 15px;
    font-weight: 800;
    min-width: 58px;
    padding: 4px 12px;
}}
#cancelActionButton {{
    background: rgba(180, 130, 146, 210);
    border: none;
    border-radius: 16px;
    color: white;
    font-size: 15px;
    font-weight: 800;
    min-width: 58px;
    padding: 4px 12px;
}}
QMenu {{
    background: {rgba(theme.input_background_color, 246)};
    border: 1px solid {rgba(theme.border_color, 164)};
    border-radius: 14px;
    color: {theme.text_color};
    font-size: 14px;
    padding: 6px;
}}
QMenu::item {{
    background: transparent;
    border-radius: 8px;
    padding: 5px 20px 5px 24px;
}}
QMenu::item:selected {{
    background: {rgba(theme.panel_background_color, 220)};
    color: {theme.text_color};
}}
QMenu::item:disabled {{
    color: {rgba(theme.muted_text_color, 145)};
}}
QMenu::separator {{
    height: 1px;
    background: {rgba(theme.border_color, 105)};
    margin: 3px 7px;
}}
QMenu::indicator {{
    width: 14px;
    height: 14px;
    left: 6px;
}}
QMenu::indicator:checked {{
    image: url("{_MENU_CHECK_URL}");
}}
QMenu::indicator:unchecked {{
    image: none;
}}
"""


def build_settings_dialog_stylesheet(settings: ThemeSettings) -> str:
    theme = settings.normalized()
    return f"""
QDialog {{
    background: {theme.page_background_color};
    color: {theme.text_color};
    font-family: "Microsoft YaHei", "Yu Gothic UI", sans-serif;
    font-size: 14px;
}}
QTabWidget::pane {{
    border: 1px solid {rgba(theme.border_color, 138)};
    border-radius: 8px;
    background: {rgba(theme.panel_background_color, 179)};
}}
QTabBar::tab {{
    background: {rgba(theme.panel_background_color, 191)};
    border: 1px solid {rgba(theme.border_color, 122)};
    border-bottom: none;
    border-top-left-radius: 8px;
    border-top-right-radius: 8px;
    padding: 7px 18px;
    margin-right: 4px;
    color: {theme.secondary_text_color};
}}
QTabBar::tab:selected {{
    background: {theme.input_background_color};
    color: {theme.accent_color};
    font-weight: 700;
}}
QListWidget#settingsNavList {{
    background: {rgba(theme.panel_background_color, 179)};
    border: 1px solid {rgba(theme.border_color, 138)};
    border-radius: 8px;
    padding: 6px;
    outline: 0;
    color: {theme.secondary_text_color};
    font-size: 14px;
}}
QListWidget#settingsNavList::item {{
    padding: 8px 12px;
    margin: 2px 0;
    border-radius: 6px;
}}
QListWidget#settingsNavList::item:hover {{
    background: {rgba(theme.panel_background_color, 205)};
}}
QListWidget#settingsNavList::item:selected,
QListWidget#settingsNavList::item:selected:active,
QListWidget#settingsNavList::item:selected:!active {{
    background: {theme.input_background_color};
    color: {theme.accent_color};
    font-weight: 700;
}}
QStackedWidget#settingsNavStack {{
    background: transparent;
    border: none;
}}
QWidget#settingsNavPage {{
    background: {rgba(theme.panel_background_color, 179)};
    border: 1px solid {rgba(theme.border_color, 138)};
    border-radius: 8px;
}}
QScrollArea#memoryEditorPanel {{
    background: {rgba(theme.input_background_color, 126)};
    border: 1px solid {rgba(theme.border_color, 126)};
    border-radius: 8px;
}}
QScrollArea#memoryEditorPanel > QWidget,
QWidget#memoryEditorContent {{
    background: transparent;
}}
QScrollArea#settingsScrollArea {{
    background: transparent;
    border: none;
}}
QScrollArea#settingsScrollArea > QWidget {{
    background: transparent;
}}
QWidget#settingsScrollContent, QWidget#settingsSectionContent, QWidget#settingsPluginTab, QWidget#settingsScrollViewport {{
    background: transparent;
}}
QGroupBox {{
    background: {rgba(theme.panel_background_color, 116)};
    border: 1px solid {rgba(theme.border_color, 130)};
    border-radius: 8px;
    color: {theme.secondary_text_color};
    font-weight: 700;
    margin-top: 18px;
    padding-top: 10px;
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    subcontrol-position: top left;
    left: 12px;
    padding: 0 6px;
}}
QGroupBox#advancedParamsGroup {{
    margin-top: 22px;
    padding-top: 12px;
}}
QGroupBox#advancedParamsGroup::title {{
    padding: 2px 6px 3px 6px;
}}
QLineEdit, QSpinBox, QDoubleSpinBox, QTextEdit, QPlainTextEdit, QTableWidget, QComboBox {{
    background: {rgba(theme.input_background_color, 235)};
    border: 1px solid {rgba(theme.border_color, 148)};
    border-radius: 7px;
    padding: 6px 8px;
    color: {theme.text_color};
    selection-background-color: {rgba(theme.primary_color, 71)};
}}
QSlider {{
    min-height: 22px;
}}
QSlider::groove:horizontal {{
    height: 4px;
    background: {rgba(theme.border_color, 130)};
    border-radius: 2px;
}}
QSlider::sub-page:horizontal {{
    background: {theme.accent_color};
    border-radius: 2px;
}}
QSlider::add-page:horizontal {{
    background: {rgba(theme.border_color, 92)};
    border-radius: 2px;
}}
QSlider::handle:horizontal {{
    width: 14px;
    height: 14px;
    margin: -6px 0;
    border-radius: 7px;
    background: {theme.accent_color};
    border: 2px solid {rgba(theme.input_background_color, 235)};
}}
QSlider::handle:horizontal:hover {{
    background: {theme.primary_color};
}}
QSlider::handle:horizontal:disabled {{
    background: {rgba(theme.muted_text_color, 130)};
    border: 2px solid {rgba(theme.border_color, 92)};
}}
QProgressBar {{
    background: {rgba(theme.input_background_color, 214)};
    border: 1px solid {rgba(theme.border_color, 132)};
    border-radius: 7px;
    color: {theme.secondary_text_color};
    min-height: 18px;
    text-align: center;
}}
QProgressBar::chunk {{
    background: {theme.primary_color};
    border-radius: 6px;
    margin: 1px;
}}
QLineEdit:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled, QTextEdit:disabled, QPlainTextEdit:disabled, QComboBox:disabled {{
    background: {rgba(mix(theme.panel_background_color, "#808080", 0.16), 172)};
    border: 1px solid {rgba(mix(theme.border_color, "#808080", 0.28), 102)};
    color: {rgba(theme.muted_text_color, 138)};
    selection-background-color: transparent;
}}
QLineEdit[readOnly="true"] {{
    background: {rgba(mix(theme.panel_background_color, "#808080", 0.14), 188)};
    border: 1px solid {rgba(mix(theme.border_color, "#808080", 0.22), 118)};
    color: {rgba(theme.muted_text_color, 172)};
    selection-background-color: {rgba(theme.primary_color, 71)};
    selection-color: {theme.text_color};
}}
QComboBox {{
    combobox-popup: 0;
    padding: 6px 30px 6px 8px;
}}
QComboBox::drop-down {{
    subcontrol-origin: border;
    subcontrol-position: top right;
    width: 26px;
    border-left: 1px solid {rgba(theme.border_color, 105)};
    border-top-right-radius: 7px;
    border-bottom-right-radius: 7px;
    background: {rgba(theme.panel_background_color, 138)};
}}
QComboBox::drop-down:hover {{
    background: {rgba(theme.panel_background_color, 205)};
}}
QComboBox::drop-down:disabled {{
    background: {rgba(mix(theme.panel_background_color, "#808080", 0.20), 126)};
    border-left: 1px solid {rgba(mix(theme.border_color, "#808080", 0.28), 86)};
}}
QComboBox::down-arrow {{
    image: url("{_SETTINGS_ARROW_DOWN_URL}");
    width: 12px;
    height: 12px;
}}
QComboBox QAbstractItemView {{
    background: {rgba(theme.input_background_color, 246)};
    border: 1px solid {rgba(theme.border_color, 158)};
    border-radius: 7px;
    color: {theme.text_color};
    font-size: 14px;
    outline: 0;
    padding: 2px;
    selection-background-color: {rgba(theme.panel_background_color, 220)};
    selection-color: {theme.text_color};
}}
QComboBox QAbstractItemView::item {{
    min-height: 22px;
    padding: 3px 8px;
    border-radius: 5px;
}}
QComboBox QAbstractItemView::item:hover {{
    background: {rgba(theme.panel_background_color, 185)};
}}
QComboBox QAbstractItemView::item:selected,
QComboBox QAbstractItemView::item:selected:active,
QComboBox QAbstractItemView::item:selected:!active {{
    background: {rgba(theme.primary_color, 43)};
    color: {theme.text_color};
}}
QSpinBox, QDoubleSpinBox {{
    padding: 6px 28px 6px 8px;
}}
QSpinBox::up-button, QDoubleSpinBox::up-button {{
    subcontrol-origin: border;
    subcontrol-position: top right;
    width: 22px;
    border-left: 1px solid {rgba(theme.border_color, 105)};
    border-top-right-radius: 7px;
    background: {rgba(theme.panel_background_color, 120)};
}}
QSpinBox::down-button, QDoubleSpinBox::down-button {{
    subcontrol-origin: border;
    subcontrol-position: bottom right;
    width: 22px;
    border-left: 1px solid {rgba(theme.border_color, 105)};
    border-bottom-right-radius: 7px;
    background: {rgba(theme.panel_background_color, 120)};
}}
QSpinBox::up-button:hover, QDoubleSpinBox::up-button:hover,
QSpinBox::down-button:hover, QDoubleSpinBox::down-button:hover {{
    background: {rgba(theme.panel_background_color, 205)};
}}
QSpinBox::up-button:disabled, QDoubleSpinBox::up-button:disabled,
QSpinBox::down-button:disabled, QDoubleSpinBox::down-button:disabled {{
    background: {rgba(mix(theme.panel_background_color, "#808080", 0.20), 126)};
    border-left: 1px solid {rgba(mix(theme.border_color, "#808080", 0.28), 86)};
}}
QSpinBox::up-arrow, QDoubleSpinBox::up-arrow {{
    image: url("{_SETTINGS_ARROW_UP_URL}");
    width: 12px;
    height: 12px;
}}
QSpinBox::down-arrow, QDoubleSpinBox::down-arrow {{
    image: url("{_SETTINGS_ARROW_DOWN_URL}");
    width: 12px;
    height: 12px;
}}
QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QTextEdit:focus, QPlainTextEdit:focus, QComboBox:focus {{
    border: 1px solid {rgba(theme.primary_color, 194)};
    background: {theme.input_background_color};
}}
QTableWidget {{
    gridline-color: {rgba(theme.border_color, 107)};
    alternate-background-color: {rgba(mix(theme.page_background_color, "#ffffff", 0.15), 219)};
}}
QHeaderView::section {{
    background: {theme.panel_background_color};
    border: 1px solid {rgba(theme.border_color, 133)};
    color: {theme.secondary_text_color};
    padding: 6px;
    font-weight: 700;
}}
QCheckBox {{
    color: {mix(theme.text_color, "#ffffff", 0.08)};
    spacing: 8px;
}}
QCheckBox::indicator, QGroupBox::indicator {{
    width: 16px;
    height: 16px;
    border-radius: 4px;
    border: 1px solid {rgba(theme.primary_color, 173)};
    background: {theme.input_background_color};
}}
QGroupBox#advancedParamsGroup::indicator {{
    margin-top: 2px;
    margin-bottom: 2px;
}}
QCheckBox::indicator:checked, QGroupBox::indicator:checked {{
    background: {theme.primary_color};
    border: 1px solid {theme.accent_color};
}}
QPushButton {{
    background: {theme.primary_color};
    border: 1px solid {rgba(theme.accent_color, 140)};
    border-radius: 8px;
    color: white;
    min-width: 72px;
    padding: 8px 12px;
    font-weight: 600;
}}
QPushButton:hover {{
    background: {theme.primary_hover_color};
}}
QPushButton:disabled {{
    background: {rgba(theme.primary_color, 107)};
    border: 1px solid {rgba(theme.border_color, 115)};
    color: rgba(255, 255, 255, 0.76);
}}
QToolTip {{
    background: {theme.panel_background_color};
    color: {theme.text_color};
    border: 1px solid {rgba(theme.border_color, 190)};
    border-radius: 8px;
    padding: 6px 10px;
    font-family: "Microsoft YaHei", "Yu Gothic UI", sans-serif;
    font-size: 13px;
    font-weight: normal;
}}
"""


def build_app_chrome_stylesheet(settings: ThemeSettings) -> str:
    """全局应用级样式：美化滚动条与菜单等独立顶层控件。"""
    theme = settings.normalized()
    return f"""
QScrollBar:vertical {{
    background: transparent;
    width: 12px;
    margin: 2px 2px 2px 0;
}}
QScrollBar::handle:vertical {{
    background: {rgba(theme.primary_color, 120)};
    border-radius: 5px;
    min-height: 28px;
}}
QScrollBar::handle:vertical:hover {{
    background: {rgba(theme.primary_color, 185)};
}}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
    height: 0;
    background: transparent;
}}
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{
    background: transparent;
}}
QScrollBar:horizontal {{
    background: transparent;
    height: 12px;
    margin: 0 2px 2px 2px;
}}
QScrollBar::handle:horizontal {{
    background: {rgba(theme.primary_color, 120)};
    border-radius: 5px;
    min-width: 28px;
}}
QScrollBar::handle:horizontal:hover {{
    background: {rgba(theme.primary_color, 185)};
}}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{
    width: 0;
    background: transparent;
}}
QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {{
    background: transparent;
}}
QMenu {{
    background: {rgba(theme.input_background_color, 246)};
    border: none;
    border-radius: 8px;
    color: {theme.text_color};
    font-size: 14px;
    padding: 6px;
}}
QMenu::item {{
    background: transparent;
    border-radius: 6px;
    padding: 5px 20px 5px 20px;
}}
QMenu::item:selected {{
    background: {rgba(theme.panel_background_color, 220)};
    color: {theme.text_color};
}}
QMenu::item:disabled {{
    color: {rgba(theme.muted_text_color, 145)};
}}
QMenu::separator {{
    height: 1px;
    background: {rgba(theme.border_color, 105)};
    margin: 3px 7px;
}}
"""


def build_message_box_stylesheet(settings: ThemeSettings) -> str:
    theme = settings.normalized()
    return f"""
QMessageBox {{
    background: {theme.page_background_color};
    color: {theme.text_color};
    font-family: "Microsoft YaHei", "Yu Gothic UI", sans-serif;
    font-size: 14px;
}}
QMessageBox QLabel {{
    color: {theme.text_color};
    font-size: 14px;
    line-height: 1.35;
}}
QMessageBox QPushButton {{
    background: {theme.primary_color};
    border: 1px solid {rgba(theme.accent_color, 140)};
    border-radius: 8px;
    color: white;
    min-width: 76px;
    padding: 7px 14px;
    font-weight: 600;
}}
QMessageBox QPushButton:hover {{
    background: {theme.primary_hover_color};
}}
QMessageBox QPushButton:pressed {{
    background: {theme.accent_color};
}}
QMessageBox QPushButton:disabled {{
    background: {rgba(theme.primary_color, 107)};
    border: 1px solid {rgba(theme.border_color, 115)};
    color: rgba(255, 255, 255, 0.76);
}}
"""


def build_history_window_stylesheet(settings: ThemeSettings) -> str:
    theme = settings.normalized()
    return f"""
QDialog {{
    background: {theme.page_background_color};
    color: {theme.text_color};
    font-family: "Microsoft YaHei", "Yu Gothic UI", sans-serif;
    font-size: 16px;
}}
QLabel#historyTitle {{
    color: {theme.secondary_text_color};
    font-size: 22px;
    font-weight: 700;
}}
QLabel#historyCount {{
    color: {theme.muted_text_color};
    background: {rgba(theme.panel_background_color, 199)};
    border: 1px solid {rgba(theme.border_color, 122)};
    border-radius: 12px;
    padding: 5px 10px;
    font-size: 13px;
}}
QScrollArea#historyScroll {{
    background: {rgba(mix(theme.page_background_color, "#ffffff", 0.15), 240)};
    border: 1px solid {rgba(theme.border_color, 138)};
    border-radius: 14px;
}}
QWidget#historyContent {{
    background: transparent;
}}
QFrame#assistantBubble {{
    background: {mix(theme.bubble_background_color, "#ffffff", 0.72)};
    border: 1px solid {mix(theme.border_color, "#ffffff", 0.18)};
    border-radius: 14px;
}}
QFrame#userBubble {{
    background: {mix(theme.bubble_background_color, theme.primary_color, 0.13)};
    border: 1px solid {mix(theme.border_color, theme.primary_color, 0.18)};
    border-radius: 14px;
}}
QFrame#errorBubble {{
    background: #ffe9e7;
    border: 1px solid #efc2bd;
    border-radius: 14px;
}}
QFrame#systemBubble {{
    background: {mix(theme.bubble_background_color, "#ffffff", 0.35)};
    border: 1px solid {mix(theme.border_color, "#ffffff", 0.12)};
    border-radius: 12px;
}}
QLabel#entryMeta {{
    color: {theme.muted_text_color};
    font-size: 13px;
}}
QLabel#entryText {{
    color: {theme.text_color};
    font-size: 16px;
    line-height: 155%;
}}
QLabel#errorText {{
    color: #9f393a;
    font-size: 16px;
    line-height: 155%;
}}
QLabel#systemText {{
    color: {mix(theme.text_color, "#ffffff", 0.25)};
    font-size: 15px;
    line-height: 155%;
}}
QPushButton {{
    background: {rgba(theme.input_background_color, 230)};
    border: 1px solid {rgba(theme.border_color, 148)};
    border-radius: 8px;
    color: {theme.secondary_text_color};
    min-width: 72px;
    padding: 8px 12px;
    font-size: 15px;
    font-weight: 600;
}}
QPushButton:hover {{
    background: {rgba(theme.panel_background_color, 245)};
    border: 1px solid {rgba(theme.primary_color, 158)};
}}
QPushButton#dangerButton {{
    background: #fff1f5;
    border: 1px solid rgba(199, 88, 122, 0.52);
    color: #b13e5a;
}}
QPushButton#dangerButton:hover {{
    background: #ffe1ea;
}}
QPushButton#primaryButton {{
    background: {theme.primary_color};
    border: 1px solid {rgba(theme.accent_color, 140)};
    color: white;
}}
QPushButton#primaryButton:hover {{
    background: {theme.primary_hover_color};
}}
QPushButton#secondaryButton:default {{
    background: {theme.primary_color};
    color: white;
}}
"""


def build_runtime_log_window_stylesheet(settings: ThemeSettings) -> str:
    theme = settings.normalized()
    return f"""
QDialog {{
    background: {theme.page_background_color};
    color: {theme.text_color};
    font-family: "Microsoft YaHei", "Yu Gothic UI", sans-serif;
    font-size: 14px;
}}
QLabel#runtimeLogTitle {{
    color: {theme.secondary_text_color};
    font-size: 22px;
    font-weight: 700;
}}
QLabel#runtimeLogSummary {{
    color: {theme.muted_text_color};
    background: {rgba(theme.panel_background_color, 205)};
    border: 1px solid {rgba(theme.border_color, 122)};
    border-radius: 11px;
    padding: 5px 10px;
    font-size: 13px;
}}
QTabWidget#runtimeLogTabs::pane {{
    background: transparent;
    border: none;
}}
QTabBar::tab {{
    background: {rgba(theme.input_background_color, 214)};
    border: 1px solid {rgba(theme.border_color, 132)};
    border-bottom: none;
    border-top-left-radius: 8px;
    border-top-right-radius: 8px;
    color: {theme.muted_text_color};
    padding: 8px 20px;
    margin-right: 4px;
    font-size: 14px;
    font-weight: 700;
}}
QTabBar::tab:selected {{
    background: {rgba(theme.panel_background_color, 245)};
    color: {theme.secondary_text_color};
    border: 1px solid {rgba(theme.primary_color, 145)};
    border-bottom: none;
}}
QTabBar::tab:hover {{
    color: {theme.secondary_text_color};
    background: {rgba(theme.panel_background_color, 230)};
}}
QFrame#runtimeLogPage {{
    background: {rgba(mix(theme.page_background_color, "#ffffff", 0.12), 238)};
    border: 1px solid {rgba(theme.border_color, 138)};
    border-radius: 12px;
}}
QListWidget#runtimeLogList {{
    background: transparent;
    border: none;
    outline: 0;
    color: {theme.text_color};
    font-size: 13px;
}}
QPushButton {{
    background: {rgba(theme.input_background_color, 230)};
    border: 1px solid {rgba(theme.border_color, 148)};
    border-radius: 8px;
    color: {theme.secondary_text_color};
    min-width: 72px;
    padding: 7px 11px;
    font-size: 14px;
    font-weight: 600;
}}
QPushButton:hover {{
    background: {rgba(theme.panel_background_color, 245)};
    border: 1px solid {rgba(theme.primary_color, 158)};
}}
QPushButton#dangerButton {{
    background: #fff1f5;
    border: 1px solid rgba(199, 88, 122, 0.52);
    color: #b13e5a;
}}
QPushButton#dangerButton:hover {{
    background: #ffe1ea;
}}
QCheckBox {{
    color: {theme.secondary_text_color};
    font-size: 13px;
    spacing: 8px;
}}
QCheckBox::indicator {{
    width: 16px;
    height: 16px;
    border-radius: 4px;
    border: 1px solid {rgba(theme.primary_color, 173)};
    background: {theme.input_background_color};
}}
QCheckBox::indicator:hover {{
    border: 1px solid {rgba(theme.primary_color, 210)};
    background: {rgba(theme.panel_background_color, 210)};
}}
QCheckBox::indicator:checked {{
    image: url("{_MENU_CHECK_URL}");
    background: {theme.primary_color};
    border: 1px solid {theme.accent_color};
}}
QToolTip {{
    background: {theme.panel_background_color};
    color: {theme.text_color};
    border: 1px solid {rgba(theme.border_color, 190)};
    border-radius: 8px;
    padding: 6px 10px;
    font-family: "Microsoft YaHei", "Yu Gothic UI", sans-serif;
    font-size: 13px;
    font-weight: normal;
}}
"""


def rgba(hex_color: str, alpha: int) -> str:
    red, green, blue = _rgb(hex_color)
    return f"rgba({red}, {green}, {blue}, {max(0, min(255, alpha))})"


def mix(color: str, other: str, weight: float) -> str:
    red, green, blue = _rgb(color)
    other_red, other_green, other_blue = _rgb(other)
    clamped = max(0.0, min(1.0, weight))
    mixed = (
        round(red * (1 - clamped) + other_red * clamped),
        round(green * (1 - clamped) + other_green * clamped),
        round(blue * (1 - clamped) + other_blue * clamped),
    )
    return "#{:02x}{:02x}{:02x}".format(*mixed)


def _rgb(hex_color: str) -> tuple[int, int, int]:
    color = normalize_hex_color(hex_color, DEFAULT_PRIMARY_COLOR).lstrip("#")
    return int(color[0:2], 16), int(color[2:4], 16), int(color[4:6], 16)


def _extract_json_text(raw_text: str) -> str:
    text = _strip_json_code_block(raw_text)
    try:
        json.loads(text)
        return text
    except json.JSONDecodeError:
        pass
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        return text[start : end + 1].strip()
    return text


def _strip_json_code_block(raw_text: str) -> str:
    text = raw_text.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        return "\n".join(lines).strip()
    return text


def _bool_value(value: object, default: bool) -> bool:
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


DEFAULT_PET_WINDOW_STYLESHEET = build_pet_window_stylesheet(DEFAULT_THEME_SETTINGS)
DEFAULT_SETTINGS_DIALOG_STYLESHEET = build_settings_dialog_stylesheet(DEFAULT_THEME_SETTINGS)
DEFAULT_HISTORY_WINDOW_STYLESHEET = build_history_window_stylesheet(DEFAULT_THEME_SETTINGS)
DEFAULT_RUNTIME_LOG_WINDOW_STYLESHEET = build_runtime_log_window_stylesheet(DEFAULT_THEME_SETTINGS)
