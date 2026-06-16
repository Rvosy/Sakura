from __future__ import annotations

import json
from datetime import datetime

from PySide6.QtCore import QRectF, QSize, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QPainter
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from app.core.gui_log import (
    GUI_LOG_LEVEL_ERROR,
    GUI_LOG_LEVEL_INFO,
    GUI_LOG_LEVEL_WARNING,
    GUI_LOG_SCOPE_PROGRAM,
    GUI_LOG_SCOPE_TTS,
    GuiLogBuffer,
    GuiLogRecord,
    get_gui_log_buffer,
)
from app.ui.theme import (
    DEFAULT_THEME_SETTINGS,
    ThemeSettings,
    build_runtime_log_window_stylesheet,
)


_POLL_INTERVAL_MS = 700
_LEVEL_LABELS = {
    GUI_LOG_LEVEL_INFO: "信息",
    GUI_LOG_LEVEL_WARNING: "提醒",
    GUI_LOG_LEVEL_ERROR: "错误",
}
_COPY_TEXT_ROLE = int(Qt.ItemDataRole.UserRole)
_COLLAPSE_KEY_ROLE = _COPY_TEXT_ROLE + 1
_REPEAT_COUNT_ROLE = _COPY_TEXT_ROLE + 2
_BASE_COPY_TEXT_ROLE = _COPY_TEXT_ROLE + 3
_BASE_TOOLTIP_ROLE = _COPY_TEXT_ROLE + 4
_SEGMENTS_ROLE = _COPY_TEXT_ROLE + 5
_LEVEL_ROLE = _COPY_TEXT_ROLE + 6
_MERGE_KEY_ROLE = _COPY_TEXT_ROLE + 7

# 行内分段类型：时间 / 分类标签 / 级别 / 消息 / 详情摘要 / 重复次数
_SEG_TIME = "time"
_SEG_CATEGORY = "category"
_SEG_LEVEL = "level"
_SEG_MESSAGE = "message"
_SEG_DETAIL = "detail"
_SEG_REPEAT = "repeat"

_LEVEL_TEXT_COLORS = {
    GUI_LOG_LEVEL_ERROR: "#b13e5a",
    GUI_LOG_LEVEL_WARNING: "#9c6a1b",
}
# warning/error 行的淡色背景，让异常行一眼可辨
_LEVEL_ROW_BG_ALPHA = {
    GUI_LOG_LEVEL_ERROR: 24,
    GUI_LOG_LEVEL_WARNING: 22,
}

# 详情摘要优先展示的字段；其余标量字段按出现顺序补足
_DETAIL_PRIORITY_KEYS = (
    "model",
    "provider",
    "voice",
    "character",
    "status",
    "status_code",
    "elapsed_ms",
    "duration_ms",
    "chars",
    "count",
    "error",
)
_MAX_DETAIL_SUMMARY_ITEMS = 3
_MAX_DETAIL_SUMMARY_VALUE_CHARS = 36


def _with_alpha(color_text: str, alpha: int) -> QColor:
    color = QColor(color_text)
    color.setAlpha(max(0, min(255, alpha)))
    return color


class RuntimeLogItemDelegate(QStyledItemDelegate):
    """按分段绘制日志行：时间淡色、分类做成标签、消息按级别着色、详情摘要弱化。"""

    _ROW_VPAD = 5
    _ROW_HPAD = 10
    _SEGMENT_SPACING = 8

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._theme = DEFAULT_THEME_SETTINGS

    def set_theme_settings(self, settings: ThemeSettings) -> None:
        self._theme = (settings or DEFAULT_THEME_SETTINGS).normalized()

    def sizeHint(self, option: QStyleOptionViewItem, index) -> QSize:  # type: ignore[no-untyped-def]
        base = super().sizeHint(option, index)
        height = int(QFontMetricsF(option.font).height()) + self._ROW_VPAD * 2 + 4
        return QSize(base.width(), max(base.height(), height))

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index) -> None:  # type: ignore[no-untyped-def]
        segments = index.data(_SEGMENTS_ROLE)
        if not segments:
            super().paint(painter, option, index)
            return
        level = str(index.data(_LEVEL_ROLE) or GUI_LOG_LEVEL_INFO)

        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        row_rect = QRectF(option.rect).adjusted(0, 2, 0, -2)
        background = self._row_background(level, option.state)
        if background is not None:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(background)
            painter.drawRoundedRect(row_rect, 8, 8)

        x = row_rect.left() + self._ROW_HPAD
        right_limit = row_rect.right() - self._ROW_HPAD
        for kind, text in segments:
            if not text or x >= right_limit:
                break
            font = self._segment_font(option.font, kind)
            metrics = QFontMetricsF(font)
            available = right_limit - x
            if kind == _SEG_CATEGORY:
                x = self._draw_category_chip(painter, row_rect, x, available, font, metrics, text)
                continue
            elided = metrics.elidedText(text, Qt.TextElideMode.ElideRight, int(available))
            painter.setFont(font)
            painter.setPen(self._segment_color(kind, level))
            text_rect = QRectF(x, row_rect.top(), available, row_rect.height())
            painter.drawText(
                text_rect,
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                elided,
            )
            x += metrics.horizontalAdvance(elided) + self._SEGMENT_SPACING
        painter.restore()

    def _row_background(self, level: str, state) -> QColor | None:  # type: ignore[no-untyped-def]
        if state & QStyle.StateFlag.State_Selected:
            return _with_alpha(self._theme.primary_color, 58)
        level_color = _LEVEL_TEXT_COLORS.get(level)
        if level_color is not None:
            return _with_alpha(level_color, _LEVEL_ROW_BG_ALPHA.get(level, 22))
        return None

    def _draw_category_chip(
        self,
        painter: QPainter,
        row_rect: QRectF,
        x: float,
        available: float,
        font: QFont,
        metrics: QFontMetricsF,
        text: str,
    ) -> float:
        chip_hpad = 6.0
        elided = metrics.elidedText(text, Qt.TextElideMode.ElideRight, int(max(0.0, available - chip_hpad * 2)))
        text_width = metrics.horizontalAdvance(elided)
        chip_height = metrics.height() + 2
        chip_rect = QRectF(
            x,
            row_rect.center().y() - chip_height / 2,
            text_width + chip_hpad * 2,
            chip_height,
        )
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(_with_alpha(self._theme.primary_color, 34))
        painter.drawRoundedRect(chip_rect, 5, 5)
        painter.setFont(font)
        painter.setPen(QColor(self._theme.secondary_text_color))
        painter.drawText(chip_rect, Qt.AlignmentFlag.AlignCenter, elided)
        return chip_rect.right() + self._SEGMENT_SPACING

    def _segment_font(self, base_font: QFont, kind: str) -> QFont:
        font = QFont(base_font)
        if kind == _SEG_CATEGORY:
            # QSS 用 font-size 像素指定字体时 pointSizeF() 为 -1，需按实际单位缩小
            if font.pixelSize() > 0:
                font.setPixelSize(max(8, font.pixelSize() - 1))
            elif font.pointSizeF() > 0:
                font.setPointSizeF(max(6.0, font.pointSizeF() - 1))
            font.setBold(True)
        elif kind == _SEG_LEVEL:
            font.setBold(True)
        elif kind == _SEG_REPEAT:
            font.setBold(True)
        return font

    def _segment_color(self, kind: str, level: str) -> QColor:
        if kind in (_SEG_TIME, _SEG_DETAIL):
            return QColor(self._theme.muted_text_color)
        if kind == _SEG_REPEAT:
            return QColor(self._theme.secondary_text_color)
        level_color = _LEVEL_TEXT_COLORS.get(level)
        if level_color is not None:
            return QColor(level_color)
        return QColor(self._theme.text_color)


class RuntimeLogWindow(QDialog):
    """非模态运行日志窗口，按软件/TTS 两页显示精简日志。"""

    def __init__(
        self,
        log_buffer: GuiLogBuffer | None = None,
        theme_settings: ThemeSettings | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.log_buffer = log_buffer or get_gui_log_buffer()
        self.theme_settings = (theme_settings or DEFAULT_THEME_SETTINGS).normalized()
        self._last_record_id = 0
        self._lists_by_scope: dict[str, QListWidget] = {}
        self._last_selected_list: QListWidget | None = None
        self._item_delegate = RuntimeLogItemDelegate(self)
        self._bulk_refreshing = False

        self.setWindowTitle("运行日志")
        self.setWindowModality(Qt.WindowModality.NonModal)
        self.resize(900, 560)

        self.title_label = QLabel("运行日志", self)
        self.title_label.setObjectName("runtimeLogTitle")
        self.summary_label = QLabel("软件与 TTS 服务的本次会话精简日志", self)
        self.summary_label.setObjectName("runtimeLogSummary")

        header_layout = QHBoxLayout()
        header_layout.addWidget(self.title_label)
        header_layout.addStretch(1)
        header_layout.addWidget(self.summary_label)

        self.program_list, program_page = self._build_log_page(
            GUI_LOG_SCOPE_PROGRAM,
            "软件",
        )
        self.tts_list, tts_page = self._build_log_page(
            GUI_LOG_SCOPE_TTS,
            "TTS 服务",
        )

        self.tabs = QTabWidget(self)
        self.tabs.setObjectName("runtimeLogTabs")
        self.tabs.addTab(program_page, "软件")
        self.tabs.addTab(tts_page, "TTS")

        self.auto_scroll_check = QCheckBox("自动滚动", self)
        self.auto_scroll_check.setChecked(True)

        refresh_button = QPushButton("刷新", self)
        refresh_button.clicked.connect(lambda: self.refresh(reset=True))

        copy_button = QPushButton("复制选中", self)
        copy_button.clicked.connect(self._copy_selected)

        clear_button = QPushButton("清空", self)
        clear_button.setObjectName("dangerButton")
        clear_button.clicked.connect(self._clear_logs)

        close_button = QPushButton("关闭", self)
        close_button.clicked.connect(self.close)

        button_layout = QHBoxLayout()
        button_layout.addWidget(self.auto_scroll_check)
        button_layout.addStretch(1)
        button_layout.addWidget(refresh_button)
        button_layout.addWidget(copy_button)
        button_layout.addWidget(clear_button)
        button_layout.addWidget(close_button)

        layout = QVBoxLayout()
        layout.setContentsMargins(18, 18, 18, 16)
        layout.setSpacing(12)
        layout.addLayout(header_layout)
        layout.addWidget(self.tabs, 1)
        layout.addLayout(button_layout)
        self.setLayout(layout)

        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(_POLL_INTERVAL_MS)
        self._poll_timer.timeout.connect(self.refresh)

        self.set_theme_settings(self.theme_settings)
        self._refresh_summary()

    def showEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        super().showEvent(event)
        self.refresh(reset=True)
        self._poll_timer.start()

    def hideEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        super().hideEvent(event)
        self._poll_timer.stop()

    def set_theme_settings(self, settings: ThemeSettings) -> None:
        self.theme_settings = (settings or DEFAULT_THEME_SETTINGS).normalized()
        self.setStyleSheet(build_runtime_log_window_stylesheet(self.theme_settings))
        self._item_delegate.set_theme_settings(self.theme_settings)
        for log_list in self._lists_by_scope.values():
            log_list.viewport().update()

    def refresh(self, *, reset: bool = False) -> None:
        bulk_lists = list(self._lists_by_scope.values()) if reset else []
        for log_list in bulk_lists:
            log_list.setUpdatesEnabled(False)
        self._bulk_refreshing = bool(bulk_lists)
        if reset:
            self._last_record_id = 0
            for log_list in self._lists_by_scope.values():
                log_list.clear()

        try:
            records = self.log_buffer.snapshot(after_id=self._last_record_id)
            if not records:
                self._refresh_summary()
                return

            for record in records:
                self._append_record(record)
                self._last_record_id = max(self._last_record_id, record.record_id)
            self._refresh_summary()
        finally:
            self._bulk_refreshing = False
            for log_list in bulk_lists:
                log_list.setUpdatesEnabled(True)
            if bulk_lists and self.auto_scroll_check.isChecked():
                for log_list in bulk_lists:
                    log_list.scrollToBottom()

    def _build_log_page(self, scope: str, _title: str) -> tuple[QListWidget, QFrame]:
        page = QFrame(self)
        page.setObjectName("runtimeLogPage")

        log_list = QListWidget(page)
        log_list.setObjectName("runtimeLogList")
        log_list.setSelectionMode(QListWidget.SelectionMode.SingleSelection)
        log_list.setItemDelegate(self._item_delegate)
        log_list.itemDoubleClicked.connect(lambda _item: self._copy_selected())
        log_list.itemSelectionChanged.connect(
            lambda scope=scope: self._handle_selection_changed(scope)
        )

        page_layout = QVBoxLayout(page)
        page_layout.setContentsMargins(12, 12, 12, 12)
        page_layout.setSpacing(0)
        page_layout.addWidget(log_list, 1)

        self._lists_by_scope[scope] = log_list
        return log_list, page

    def _append_record(self, record: GuiLogRecord) -> None:
        log_list = self._lists_by_scope.get(record.scope)
        if log_list is None:
            return

        collapse_key = _record_collapse_key(record)
        last_item = log_list.item(log_list.count() - 1) if log_list.count() else None

        # 进度类记录：原地替换上一条同 merge_key 的行，而不是追加新行
        if (
            record.merge_key
            and last_item is not None
            and last_item.data(_MERGE_KEY_ROLE) == record.merge_key
        ):
            self._apply_record_to_item(last_item, record)
            if not self._bulk_refreshing and self.auto_scroll_check.isChecked():
                log_list.scrollToBottom()
            return

        if last_item is not None and last_item.data(_COLLAPSE_KEY_ROLE) == collapse_key:
            repeat_count = int(last_item.data(_REPEAT_COUNT_ROLE) or 1) + 1
            base_segments = list(last_item.data(_SEGMENTS_ROLE) or [])
            base_segments = [seg for seg in base_segments if seg[0] != _SEG_REPEAT]
            segments = _segments_with_repeat(base_segments, repeat_count)
            base_copy_text = str(
                last_item.data(_BASE_COPY_TEXT_ROLE)
                or last_item.data(_COPY_TEXT_ROLE)
                or last_item.text()
            )
            base_tooltip = str(last_item.data(_BASE_TOOLTIP_ROLE) or last_item.toolTip())
            last_item.setData(_REPEAT_COUNT_ROLE, repeat_count)
            last_item.setData(_SEGMENTS_ROLE, segments)
            last_item.setText(_segments_text(segments))
            last_item.setToolTip(_repeat_tooltip_text(base_tooltip, repeat_count))
            last_item.setData(_COPY_TEXT_ROLE, _repeat_copy_text(base_copy_text, repeat_count))
            if not self._bulk_refreshing and self.auto_scroll_check.isChecked():
                log_list.scrollToBottom()
            return

        item = QListWidgetItem()
        self._apply_record_to_item(item, record)
        log_list.addItem(item)
        if not self._bulk_refreshing and self.auto_scroll_check.isChecked():
            log_list.scrollToBottom()

    def _apply_record_to_item(self, item: QListWidgetItem, record: GuiLogRecord) -> None:
        """把日志记录的全部展示状态写入列表项（新建与原地替换共用）。"""
        segments = _record_segments(record)
        tooltip_text = _record_tooltip(record)
        copy_text = _record_copy_text(record)
        item.setText(_segments_text(segments))
        item.setToolTip(tooltip_text)
        item.setData(_COPY_TEXT_ROLE, copy_text)
        item.setData(_COLLAPSE_KEY_ROLE, _record_collapse_key(record))
        item.setData(_REPEAT_COUNT_ROLE, 1)
        item.setData(_SEGMENTS_ROLE, segments)
        item.setData(_LEVEL_ROLE, record.level)
        item.setData(_MERGE_KEY_ROLE, record.merge_key)
        item.setData(_BASE_COPY_TEXT_ROLE, copy_text)
        item.setData(_BASE_TOOLTIP_ROLE, tooltip_text)

    def _refresh_summary(self) -> None:
        parts: list[str] = []
        for scope, title in (
            (GUI_LOG_SCOPE_PROGRAM, "软件"),
            (GUI_LOG_SCOPE_TTS, "TTS 服务"),
        ):
            log_list = self._lists_by_scope[scope]
            count = log_list.count()
            parts.append(f"{title}：{count} 条")
        self.summary_label.setText("  |  ".join(parts))

    def _handle_selection_changed(self, scope: str) -> None:
        selected_list = self._lists_by_scope.get(scope)
        if selected_list is None or not selected_list.selectedItems():
            return
        self._last_selected_list = selected_list
        for other_scope, other_list in self._lists_by_scope.items():
            if other_scope == scope:
                continue
            other_list.blockSignals(True)
            other_list.clearSelection()
            other_list.blockSignals(False)

    def _copy_selected(self) -> None:
        active_list = self._last_selected_list or self.program_list
        item = active_list.currentItem()
        if item is None:
            return
        clipboard = QApplication.clipboard()
        clipboard.setText(str(item.data(_COPY_TEXT_ROLE) or item.text()))

    def _clear_logs(self) -> None:
        self.log_buffer.clear()
        self.refresh(reset=True)


def _format_time(timestamp: str) -> str:
    try:
        return datetime.fromisoformat(timestamp).strftime("%H:%M:%S")
    except ValueError:
        return timestamp[-8:] if len(timestamp) >= 8 else timestamp


def _format_detail_for_display(detail: str) -> str:
    """格式化 detail JSON 用于展示，将 bytes 字段转换为 KB。"""
    if not detail:
        return detail
    try:
        data = json.loads(detail)
    except ValueError:
        return detail
    if not isinstance(data, dict):
        return detail
    formatted: dict = {}
    for key, value in data.items():
        if key == "bytes" and isinstance(value, (int, float)):
            formatted[key] = f"{value / 1024:.1f} KB"
        else:
            formatted[key] = value
    return json.dumps(formatted, ensure_ascii=False)


def _record_tooltip(record: GuiLogRecord) -> str:
    lines = [
        f"时间：{record.timestamp}",
        f"分类：{record.category}",
        f"级别：{_LEVEL_LABELS.get(record.level, record.level)}",
        f"消息：{record.message}",
    ]
    if record.text_preview:
        lines.append(f"文本：{record.text_preview}")
    if record.detail:
        lines.append(f"详情：{_format_detail_for_display(record.detail)}")
    return "\n".join(lines)


def _record_segments(record: GuiLogRecord) -> list[tuple[str, str]]:
    """把日志记录拆为带类型的行内分段，供 delegate 分层着色绘制。"""
    segments: list[tuple[str, str]] = [
        (_SEG_TIME, _format_time(record.timestamp)),
        (_SEG_CATEGORY, record.category),
    ]
    if record.level != GUI_LOG_LEVEL_INFO:
        segments.append((_SEG_LEVEL, _LEVEL_LABELS.get(record.level, "信息")))
    segments.append((_SEG_MESSAGE, record.message))
    # 合成/播放类记录优先把文本内容作为灰字摘要，其余记录回退到关键字段摘要
    if record.text_preview:
        segments.append((_SEG_DETAIL, f"「{record.text_preview}」"))
    else:
        summary = _detail_summary(record.detail)
        if summary:
            segments.append((_SEG_DETAIL, summary))
    return segments


def _segments_text(segments: list[tuple[str, str]]) -> str:
    """分段拼出纯文本行，用于无障碍/兜底显示与测试断言。"""
    parts: list[str] = []
    for kind, text in segments:
        if not text:
            continue
        if kind == _SEG_CATEGORY:
            parts.append(f"[{text}]")
        else:
            parts.append(text)
    return "  ".join(parts)


def _segments_with_repeat(
    base_segments: list[tuple[str, str]],
    repeat_count: int,
) -> list[tuple[str, str]]:
    if repeat_count <= 1:
        return list(base_segments)
    return [*base_segments, (_SEG_REPEAT, f"×{repeat_count}")]


def _detail_summary(detail: str) -> str:
    """从 detail JSON 提取少量关键标量字段作为行尾摘要。"""
    if not detail:
        return ""
    try:
        data = json.loads(detail)
    except ValueError:
        return ""
    if not isinstance(data, dict):
        return ""

    pairs: list[str] = []
    used_keys: set[str] = set()
    ordered_keys = [key for key in _DETAIL_PRIORITY_KEYS if key in data]
    ordered_keys.extend(key for key in data if key not in _DETAIL_PRIORITY_KEYS)
    for key in ordered_keys:
        if len(pairs) >= _MAX_DETAIL_SUMMARY_ITEMS:
            break
        if key in used_keys or key == "omitted_keys":
            continue
        value = data[key]
        if not isinstance(value, (str, int, float, bool)):
            continue
        if value == "" or value == "<redacted>":
            continue
        used_keys.add(key)
        if key == "bytes" and isinstance(value, (int, float)):
            value_text = f"{value / 1024:.1f} KB"
        else:
            value_text = str(value)
            if len(value_text) > _MAX_DETAIL_SUMMARY_VALUE_CHARS:
                value_text = f"{value_text[:_MAX_DETAIL_SUMMARY_VALUE_CHARS]}…"
        pairs.append(f"{key}={value_text}")
    return " · ".join(pairs)


def _record_collapse_key(record: GuiLogRecord) -> tuple:
    # 含 text_preview：文本不同的"开始播放"等记录不应折叠成一条
    # 合成文本记录内容各异，用 record_id 保证每条独立展示，避免同字数文本被误折叠为 ×N
    if record.message.startswith("收到合成文本"):
        return (record.scope, record.level, record.category, record.message, record.text_preview, record.record_id)
    return (record.scope, record.level, record.category, record.message, record.text_preview)


def _repeat_tooltip_text(base_tooltip: str, repeat_count: int) -> str:
    if repeat_count <= 1:
        return base_tooltip
    return f"{base_tooltip}\n连续重复：{repeat_count} 次"


def _repeat_copy_text(base_copy_text: str, repeat_count: int) -> str:
    if repeat_count <= 1:
        return base_copy_text
    return f"{base_copy_text} 连续重复：{repeat_count} 次"


def _record_copy_text(record: GuiLogRecord) -> str:
    parts = [
        f"[{record.timestamp}]",
        f"[{record.category}]",
        f"[{_LEVEL_LABELS.get(record.level, record.level)}]",
        record.message,
    ]
    if record.text_preview:
        parts.append(f"「{record.text_preview}」")
    if record.detail:
        parts.append(record.detail)
    return " ".join(parts)
