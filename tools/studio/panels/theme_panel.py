"""主题配色编辑面板。

复用 app.ui.theme 的 THEME_COLOR_FIELDS（字段/中文名/默认色）、ThemeSettings、
normalize_hex_color 与 build_settings_dialog_stylesheet，提供取色编辑与实时预览。
导出时主题 source 固定为 package（角色包自带配色）。
"""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QColorDialog,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from app.ui.theme import (
    DEFAULT_THEME_SETTINGS,
    THEME_COLOR_FIELDS,
    ThemeSettings,
    build_settings_dialog_stylesheet,
    rgba,
    normalize_hex_color,
)

from tools.studio.character_doc import CharacterDoc
from tools.studio.panels.base import StudioPanel
from tools.studio.styles import build_studio_stylesheet


class ThemePanel(StudioPanel):
    theme_changed = Signal(object)

    def __init__(self) -> None:
        super().__init__()
        # 去重保护：按字段名收集 (label, default)，避免常量重复项生成重复行
        self._fields: list[tuple[str, str, str]] = []
        seen: set[str] = set()
        for field, label, default in THEME_COLOR_FIELDS:
            if field not in seen:
                seen.add(field)
                self._fields.append((field, label, default))

        self._edits: dict[str, QLineEdit] = {}
        self._swatches: dict[str, QPushButton] = {}

        root = QHBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 12)
        root.setSpacing(16)
        root.addLayout(self._build_editor(), 1)
        root.addLayout(self._build_preview(), 1)
        self._set_theme(DEFAULT_THEME_SETTINGS.normalized())

    # ---- 编辑区 -----------------------------------------------------------

    def _build_editor(self) -> QVBoxLayout:
        col = QVBoxLayout()
        col.addWidget(QLabel("主题配色（角色包 theme，导出时 source = package）"))

        grid_host = QWidget()
        grid_host.setObjectName("themeEditorGrid")
        grid = QGridLayout(grid_host)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(6)
        for row, (field, label, default) in enumerate(self._fields):
            grid.addWidget(QLabel(label), row, 0)
            swatch = QPushButton()
            swatch.setObjectName("themeSwatchButton")
            swatch.setFixedSize(52, 24)
            swatch.clicked.connect(lambda _checked, f=field: self._pick_color(f))
            edit = QLineEdit()
            edit.setPlaceholderText(default)
            edit.textChanged.connect(lambda _text, f=field: self._on_color_changed(f))
            grid.addWidget(swatch, row, 1)
            grid.addWidget(edit, row, 2)
            self._edits[field] = edit
            self._swatches[field] = swatch

        scroll = QScrollArea()
        scroll.setObjectName("themeEditorScroll")
        scroll.viewport().setObjectName("themeEditorViewport")
        scroll.setWidgetResizable(True)
        scroll.setWidget(grid_host)
        col.addWidget(scroll, 1)

        reset_btn = QPushButton("恢复默认配色")
        reset_btn.clicked.connect(self._reset_defaults)
        col.addWidget(reset_btn)
        return col

    def _build_preview(self) -> QVBoxLayout:
        col = QVBoxLayout()
        col.addWidget(QLabel("预览"))
        self.preview_box = QWidget()
        self.preview_box.setObjectName("themePreviewBox")
        inner = QVBoxLayout(self.preview_box)
        inner.setContentsMargins(16, 16, 16, 16)
        inner.setSpacing(10)

        title = QLabel("示例标题")
        title.setObjectName("themePreviewTitle")
        inner.addWidget(title)
        secondary = QLabel("次级说明文字")
        secondary.setObjectName("themePreviewSecondary")
        inner.addWidget(secondary)
        muted = QLabel("弱提示文字")
        muted.setObjectName("themePreviewMuted")
        inner.addWidget(muted)

        inner.addWidget(QPushButton("主色按钮"))
        sample_edit = QLineEdit()
        sample_edit.setPlaceholderText("输入框示例")
        inner.addWidget(sample_edit)

        bubble = QWidget()
        bubble.setObjectName("themePreviewBubble")
        bubble_layout = QVBoxLayout(bubble)
        bubble_layout.setContentsMargins(10, 8, 10, 8)
        bubble_layout.addWidget(QLabel("气泡背景示例"))
        inner.addWidget(bubble)

        sample_list = QListWidget()
        sample_list.addItems(["列表项 A", "列表项 B"])
        inner.addWidget(sample_list, 1)
        col.addWidget(self.preview_box, 1)
        return col

    # ---- 取色与刷新 -------------------------------------------------------

    def _default_for(self, field: str) -> str:
        for f, _label, default in self._fields:
            if f == field:
                return default
        return "#000000"

    def _pick_color(self, field: str) -> None:
        current = normalize_hex_color(self._edits[field].text(), self._default_for(field))
        color = QColorDialog.getColor(QColor(current), self, "选择颜色")
        if color.isValid():
            self._edits[field].setText(color.name())

    def _on_color_changed(self, field: str) -> None:
        value = normalize_hex_color(self._edits[field].text(), self._default_for(field))
        self._update_swatch(field, value)
        self._refresh_preview()

    def _reset_defaults(self) -> None:
        self._set_theme(DEFAULT_THEME_SETTINGS.normalized())

    def _set_theme(self, theme: ThemeSettings) -> None:
        normalized = theme.normalized()
        for field, _label, default in self._fields:
            value = normalize_hex_color(getattr(normalized, field), default)
            edit = self._edits[field]
            edit.blockSignals(True)
            edit.setText(value)
            edit.blockSignals(False)
            self._update_swatch(field, value)
        self._refresh_preview()

    def _update_swatch(self, field: str, value: str) -> None:
        self._swatches[field].setStyleSheet(self._swatch_stylesheet(value))

    def _current_theme(self) -> ThemeSettings:
        values = {
            field: normalize_hex_color(self._edits[field].text(), default)
            for field, _label, default in self._fields
        }
        return ThemeSettings(**values).normalized()

    def _refresh_preview(self) -> None:
        theme = self._current_theme()
        self.preview_box.setStyleSheet(
            build_settings_dialog_stylesheet(theme) + build_studio_stylesheet(theme)
        )
        self.theme_changed.emit(theme)

    def _swatch_stylesheet(self, color: str) -> str:
        safe_color = normalize_hex_color(color, "#000000")
        return f"""
QPushButton#themeSwatchButton {{
    background: {safe_color};
    border: 1px solid {rgba("#000000", 46)};
    border-radius: 8px;
    min-width: 52px;
    max-width: 52px;
    min-height: 24px;
    max-height: 24px;
    padding: 0;
}}
QPushButton#themeSwatchButton:hover {{
    border: 1px solid {rgba("#000000", 92)};
}}
"""

    # ---- 面板接口 ---------------------------------------------------------

    def load_from(self, doc: CharacterDoc) -> None:
        self._set_theme(doc.theme.normalized())

    def write_to(self, doc: CharacterDoc) -> None:
        doc.theme = self._current_theme()
