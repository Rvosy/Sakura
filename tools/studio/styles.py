"""SakuraCharacterStudio 专用样式。"""

from __future__ import annotations

from app.ui.theme import ThemeSettings, mix, rgba

STUDIO_FONT_STACK = (
    '"Microsoft YaHei UI", "Segoe UI Variable Text", "Segoe UI", '
    '"Noto Sans CJK SC", "Source Han Sans SC", sans-serif'
)


def build_studio_stylesheet(settings: ThemeSettings) -> str:
    """构建 Studio 独立样式，补齐设置页通用 QSS 覆盖不到的控件。"""

    theme = settings.normalized()
    soft_panel = mix(theme.panel_background_color, "#ffffff", 0.38)
    subtle_panel = mix(theme.panel_background_color, theme.page_background_color, 0.42)
    focus_bg = mix(theme.input_background_color, theme.page_background_color, 0.12)
    return f"""
QMainWindow {{
    background: {theme.page_background_color};
    color: {theme.text_color};
    font-family: {STUDIO_FONT_STACK};
    font-size: 14px;
}}
QWidget#studioCentral {{
    background: {theme.page_background_color};
    color: {theme.text_color};
    font-family: {STUDIO_FONT_STACK};
    font-size: 14px;
}}
QWidget#studioContent,
QWidget#studioPanel,
QWidget#themeEditorGrid,
QWidget#themeEditorViewport,
QWidget#studioInlineField,
QWidget#voiceModelBody {{
    background: transparent;
    color: {theme.text_color};
}}
QLabel {{
    color: {theme.text_color};
}}
QLabel#studioTitle {{
    color: {theme.text_color};
    font-size: 22px;
    font-weight: 800;
}}
QLabel#studioStatus {{
    color: {theme.muted_text_color};
    font-size: 14px;
    padding: 4px 2px;
}}
QLabel#studioPanelTitle {{
    color: {theme.text_color};
    font-size: 22px;
    font-weight: 800;
}}
QLabel#studioSectionLabel {{
    color: {theme.secondary_text_color};
    font-size: 14px;
}}
QWidget#studioStepper {{
    background: {rgba(soft_panel, 218)};
    border: 1px solid {rgba(theme.border_color, 145)};
    border-radius: 8px;
}}
QLabel#studioStepperTitle {{
    color: {theme.secondary_text_color};
    font-size: 14px;
    font-weight: 800;
    padding: 0 4px 12px 4px;
}}
QPushButton#studioStepButton {{
    background: transparent;
    border: none;
    border-radius: 8px;
    color: {theme.secondary_text_color};
    min-width: 0;
    padding: 9px 10px;
    text-align: left;
    font-size: 14px;
    font-weight: 700;
}}
QPushButton#studioStepButton:hover {{
    background: {rgba(theme.input_background_color, 180)};
    color: {theme.text_color};
}}
QPushButton#studioStepButton[stepState="current"] {{
    background: {theme.input_background_color};
    border: 1px solid {rgba(theme.primary_color, 165)};
    color: {theme.accent_color};
}}
QPushButton#studioStepButton[stepState="done"] {{
    color: {theme.primary_color};
}}
QFrame#studioStepLine {{
    background: {rgba(theme.border_color, 130)};
    border: none;
    min-height: 16px;
    max-height: 16px;
    margin-left: 22px;
    margin-top: 2px;
    margin-bottom: 2px;
    max-width: 2px;
}}
QFrame#studioStepLine[lineState="done"] {{
    background: {rgba(theme.primary_color, 190)};
}}
QStackedWidget#studioStack {{
    background: {rgba(soft_panel, 196)};
    border: 1px solid {rgba(theme.border_color, 138)};
    border-radius: 8px;
}}
QWidget#studioWizardFooter {{
    background: {rgba(subtle_panel, 206)};
    border: 1px solid {rgba(theme.border_color, 120)};
    border-radius: 8px;
}}
QFrame#studioActionCard {{
    background: {rgba(theme.input_background_color, 198)};
    border: 1px solid {rgba(theme.border_color, 118)};
    border-radius: 8px;
}}
QLabel#studioStepIndicator {{
    color: {theme.secondary_text_color};
    font-size: 14px;
    font-weight: 700;
}}
QPushButton#studioPrimaryButton {{
    background: {theme.primary_color};
    border: 1px solid {rgba(theme.accent_color, 155)};
    border-radius: 8px;
    color: white;
    min-width: 78px;
    padding: 8px 14px;
    font-size: 14px;
    font-weight: 700;
}}
QPushButton#studioPrimaryButton:hover {{
    background: {theme.primary_hover_color};
}}
QPushButton#studioPrimaryButton:disabled {{
    background: {rgba(theme.primary_color, 95)};
    border: 1px solid {rgba(theme.border_color, 100)};
    color: rgba(255, 255, 255, 178);
}}
QPushButton#studioSecondaryButton {{
    background: {rgba(theme.input_background_color, 228)};
    border: 1px solid {rgba(theme.border_color, 145)};
    border-radius: 8px;
    color: {theme.secondary_text_color};
    min-width: 78px;
    padding: 8px 14px;
    font-size: 14px;
    font-weight: 700;
}}
QPushButton#studioSecondaryButton:hover {{
    background: {rgba(theme.panel_background_color, 235)};
    border: 1px solid {rgba(theme.primary_color, 142)};
    color: {theme.text_color};
}}
QPushButton#studioSecondaryButton:disabled {{
    background: {rgba(theme.input_background_color, 118)};
    border: 1px solid {rgba(theme.border_color, 88)};
    color: {rgba(theme.muted_text_color, 128)};
}}
QPushButton#themeSwatchButton {{
    border: 1px solid {rgba(theme.border_color, 190)};
    border-radius: 8px;
    min-width: 52px;
    max-width: 52px;
    min-height: 24px;
    max-height: 24px;
    padding: 0;
}}
QLineEdit, QPlainTextEdit, QTextEdit, QTableWidget, QComboBox, QListWidget {{
    background: {rgba(theme.input_background_color, 238)};
    border: 1px solid {rgba(theme.border_color, 148)};
    border-radius: 7px;
    color: {theme.text_color};
    font-size: 14px;
    selection-background-color: {rgba(theme.primary_color, 72)};
}}
QLineEdit {{
    padding: 6px 8px;
}}
QPlainTextEdit, QTextEdit {{
    padding: 8px;
    line-height: 145%;
}}
QPushButton {{
    font-size: 14px;
}}
QLineEdit:focus, QPlainTextEdit:focus, QTextEdit:focus, QComboBox:focus {{
    background: {focus_bg};
    border: 1px solid {rgba(theme.primary_color, 194)};
}}
QTableWidget {{
    background: {rgba(theme.input_background_color, 238)};
    gridline-color: {rgba(theme.border_color, 96)};
    alternate-background-color: {rgba(theme.page_background_color, 210)};
    outline: 0;
}}
QTableWidget::item {{
    padding: 6px 8px;
    border: none;
}}
QTableWidget::item:focus {{
    border: 1px solid {rgba(theme.primary_color, 148)};
}}
QTableWidget QLineEdit {{
    background: {theme.input_background_color};
    border: 1px solid {rgba(theme.primary_color, 145)};
    border-radius: 6px;
    color: {theme.text_color};
    min-height: 24px;
    padding: 2px 8px;
    selection-background-color: {rgba(theme.primary_color, 72)};
}}
QTableWidget QLineEdit:focus {{
    background: {theme.input_background_color};
    border: 1px solid {rgba(theme.primary_color, 205)};
}}
QLabel#portraitPreview {{
    background: {rgba(theme.input_background_color, 150)};
    border: 1px solid {rgba(theme.border_color, 116)};
    border-radius: 8px;
    color: {theme.muted_text_color};
}}
QTableWidget::item:selected, QListWidget::item:selected {{
    background: {rgba(theme.primary_color, 52)};
    color: {theme.text_color};
}}
QHeaderView {{
    background: {rgba(theme.input_background_color, 238)};
    border: none;
}}
QHeaderView::section {{
    background: {rgba(theme.panel_background_color, 225)};
    border: 1px solid {rgba(theme.border_color, 130)};
    color: {theme.secondary_text_color};
    padding: 7px;
    font-size: 14px;
    font-weight: 800;
}}
QHeaderView::section:vertical {{
    background: {rgba(theme.panel_background_color, 210)};
    color: {theme.secondary_text_color};
}}
QTableCornerButton::section {{
    background: {rgba(theme.panel_background_color, 225)};
    border: 1px solid {rgba(theme.border_color, 130)};
}}
QListWidget {{
    padding: 6px;
    outline: 0;
}}
QListWidget::item {{
    border-radius: 7px;
    padding: 6px 8px;
}}
QListWidget::item:hover {{
    background: {rgba(theme.panel_background_color, 202)};
}}
QCheckBox {{
    color: {theme.text_color};
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
    border: 1px solid {theme.primary_color};
}}
QCheckBox::indicator:checked {{
    background: {theme.primary_color};
    border: 1px solid {theme.accent_color};
}}
QWidget#themePreviewBox {{
    background: {theme.page_background_color};
    border: 1px solid {rgba(theme.border_color, 145)};
    border-radius: 8px;
}}
QLabel#themePreviewTitle {{
    color: {theme.text_color};
    font-size: 16px;
    font-weight: 800;
}}
QLabel#themePreviewSecondary {{
    color: {theme.secondary_text_color};
}}
QLabel#themePreviewMuted {{
    color: {theme.muted_text_color};
}}
QWidget#themePreviewBubble {{
    background: {theme.bubble_background_color};
    border: 1px solid {rgba(theme.border_color, 160)};
    border-radius: 8px;
}}
QScrollArea {{
    background: transparent;
    border: none;
}}
QScrollArea > QWidget,
QScrollArea QWidget#themeEditorGrid,
QScrollArea QWidget#themeEditorViewport {{
    background: transparent;
}}
QAbstractScrollArea::corner {{
    background: {rgba(theme.panel_background_color, 225)};
}}
QScrollBar:vertical {{
    background: transparent;
    width: 12px;
    margin: 2px 2px 2px 0;
}}
QScrollBar::handle:vertical {{
    background: {rgba(theme.primary_color, 118)};
    border-radius: 5px;
    min-height: 28px;
}}
QScrollBar::handle:vertical:hover {{
    background: {rgba(theme.primary_color, 184)};
}}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
    height: 0;
    background: transparent;
}}
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{
    background: transparent;
}}
"""
