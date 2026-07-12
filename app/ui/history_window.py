from __future__ import annotations

import re
from dataclasses import dataclass

from PySide6.QtCore import QTimer, Qt

try:
    import shiboken6
except ImportError:  # pragma: no cover - 仅供无真实 PySide6 的最小测试桩环境
    shiboken6 = None  # type: ignore[assignment]

from PySide6.QtWidgets import (
    QLabel,
    QDialog,
    QFrame,
    QHBoxLayout,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from app.agent.screen_awareness import SCREEN_AWARENESS_CONTEXT_HISTORY_MARKER
from app.agent.screen_observation import (
    MANUAL_SCREEN_OBSERVATION_HISTORY_MARKER,
    SCREEN_OBSERVATION_HISTORY_MARKER,
)
from app.storage.chat_history import ChatHistoryEntry, ChatHistoryStore
from app.llm.chat_reply import parse_chat_reply_result
from app.ui.theme import DEFAULT_THEME_SETTINGS, ThemeSettings, build_history_window_stylesheet


_VISUAL_ID_SUFFIX_RE = re.compile(r"，视觉记录\s+visual_id=[^\]\s]+")
_HISTORY_MARKER_DISPLAY_TEXT = {
    MANUAL_SCREEN_OBSERVATION_HISTORY_MARKER: "（已附上你框选的画面）",
    SCREEN_OBSERVATION_HISTORY_MARKER: "（已看过当前屏幕）",
    SCREEN_AWARENESS_CONTEXT_HISTORY_MARKER: "刚才留意了一下屏幕状态。",
}
_RENDER_BATCH_SIZE = 40


@dataclass(frozen=True)
class HistoryEntryView:
    role_name: str
    align: str
    bubble_object_name: str
    meta_text: str
    content: str


class HistoryWindow(QDialog):
    def __init__(
        self,
        history_store: ChatHistoryStore,
        subtitle_language: str = "ja",
        theme_settings: ThemeSettings | None = None,
        parent=None,  # type: ignore[no-untyped-def]
    ) -> None:
        super().__init__(parent)
        self.history_store = history_store
        self.subtitle_language = subtitle_language
        self.theme_settings = (theme_settings or DEFAULT_THEME_SETTINGS).normalized()
        self._bubble_frames: list[QFrame] = []
        self._pending_entries: list[ChatHistoryEntry] = []
        self._render_index = 0
        self._render_generation = 0
        self._refresh_scheduled = False
        self._staged_history_content: QWidget | None = None
        self._owned_timers: list[QTimer] = []

        self.setWindowTitle("历史记录")
        self.resize(620, 680)

        self.title_label = QLabel("历史记录", self)
        self.title_label.setObjectName("historyTitle")

        self.count_label = QLabel("0 条记录", self)
        self.count_label.setObjectName("historyCount")

        self.history_view = QScrollArea(self)
        self.history_view.setObjectName("historyScroll")
        self.history_view.setWidgetResizable(True)
        self.history_view.setFrameShape(QFrame.Shape.NoFrame)

        self.history_content, self.history_layout = self._create_history_content()
        self.history_view.setWidget(self.history_content)

        self.refresh_button = QPushButton("刷新", self)
        self.refresh_button.setObjectName("secondaryButton")
        self.refresh_button.clicked.connect(self.refresh)

        self.clear_button = QPushButton("清空历史", self)
        self.clear_button.setObjectName("dangerButton")
        self.clear_button.clicked.connect(self.clear_history)

        self.close_button = QPushButton("关闭", self)
        self.close_button.setObjectName("secondaryButton")
        self.close_button.clicked.connect(self.close)

        header_layout = QHBoxLayout()
        header_layout.addWidget(self.title_label)
        header_layout.addStretch(1)
        header_layout.addWidget(self.count_label)

        button_layout = QHBoxLayout()
        button_layout.addWidget(self.refresh_button)
        button_layout.addStretch(1)
        button_layout.addWidget(self.clear_button)
        button_layout.addWidget(self.close_button)

        layout = QVBoxLayout()
        layout.setContentsMargins(18, 18, 18, 16)
        layout.setSpacing(12)
        layout.addLayout(header_layout)
        layout.addWidget(self.history_view, 1)
        layout.addLayout(button_layout)
        self.setLayout(layout)

        self.set_theme_settings(self.theme_settings)
        self._show_loading_state()
        self.request_refresh()

    def resizeEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        super().resizeEvent(event)
        if not hasattr(self, "history_view"):
            return
        self._update_bubble_widths()

    def showEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        super().showEvent(event)
        self._show_loading_state()
        self.request_refresh()
        self._schedule_layout_update()

    def set_subtitle_language(self, subtitle_language: str) -> None:
        if subtitle_language == self.subtitle_language:
            return
        self.subtitle_language = subtitle_language
        self.request_refresh()

    def set_history_store(self, history_store: ChatHistoryStore, assistant_name: str) -> None:
        self.history_store = history_store
        self.history_store.assistant_name = assistant_name
        self.request_refresh()

    def set_theme_settings(self, settings: ThemeSettings) -> None:
        self.theme_settings = settings.normalized()
        self.setStyleSheet(build_history_window_stylesheet(self.theme_settings))

    def request_refresh(self) -> None:
        """把历史刷新推迟到事件循环，避免打开窗口前同步渲染全部消息。"""
        if self._refresh_scheduled:
            return
        self._refresh_scheduled = True
        self._single_shot(0, self._run_scheduled_refresh)

    def _run_scheduled_refresh(self) -> None:
        if not self._is_alive():
            return
        self._refresh_scheduled = False
        self.refresh()

    def refresh(self) -> None:
        self._refresh_scheduled = False
        entries = self.history_store.load()
        self.count_label.setText(f"{len(entries)} 条记录")

        if not entries:
            self._clear_entries()
            content, layout = self._create_history_content()
            self._install_history_content(content, layout)
            self._add_empty_state()
            return

        self._stage_entries_for_render()
        self._pending_entries = entries
        self._render_index = 0
        self._render_next_batch(self._render_generation)

    def clear_history(self) -> None:
        result = QMessageBox.question(
            self,
            "清空历史",
            "确定要清空全部历史记录吗？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if result != QMessageBox.StandardButton.Yes:
            return
        self.history_store.clear()
        self.refresh()

    def _clear_entries(self) -> None:
        self._render_generation += 1
        self._pending_entries = []
        self._render_index = 0
        self._bubble_frames.clear()
        if self._staged_history_content is not None:
            self._staged_history_content.deleteLater()
            self._staged_history_content = None
        while self.history_layout.count():
            item = self.history_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                # deleteLater 会等事件循环空闲后才真正销毁；
                # 先隐藏并脱离父控件，避免刷新后旧内容短暂叠在空状态上。
                widget.hide()
                widget.setParent(None)
                widget.deleteLater()

    def _create_history_content(self) -> tuple[QWidget, QVBoxLayout]:
        content = QWidget()
        content.setObjectName("historyContent")
        layout = QVBoxLayout(content)
        layout.setContentsMargins(20, 14, 20, 14)
        layout.setSpacing(12)
        return content, layout

    def _install_history_content(
        self,
        content: QWidget,
        layout: QVBoxLayout,
        bubble_frames: list[QFrame] | None = None,
    ) -> None:
        previous = self.history_view.takeWidget()
        if previous is not None and previous is not content:
            previous.deleteLater()
        self.history_content = content
        self.history_layout = layout
        self._bubble_frames = bubble_frames or []
        self.history_view.setWidget(content)

    def _stage_entries_for_render(self) -> None:
        self._render_generation += 1
        self._pending_entries = []
        self._render_index = 0
        if self._staged_history_content is not None:
            self._staged_history_content.deleteLater()
        content, layout = self._create_history_content()
        self._staged_history_content = content
        self.history_content = content
        self.history_layout = layout
        self._bubble_frames = []

    def _show_loading_state(self) -> None:
        self.count_label.setText("读取中...")
        self._clear_entries()
        content, layout = self._create_history_content()
        self._install_history_content(content, layout)
        loading_label = QLabel("正在读取历史记录...", self.history_content)
        loading_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        loading_label.setObjectName("systemText")
        self.history_layout.addStretch(1)
        self.history_layout.addWidget(loading_label)
        self.history_layout.addStretch(1)

    def _add_empty_state(self) -> None:
        empty_label = QLabel("还没有历史记录\n等和桜聊过之后，这里会安静地收好对话。", self.history_content)
        empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty_label.setObjectName("systemText")
        empty_label.setWordWrap(True)
        self.history_layout.addStretch(1)
        self.history_layout.addWidget(empty_label)
        self.history_layout.addStretch(1)

    def _render_next_batch(self, generation: int) -> None:
        if not self._is_alive():
            return
        if generation != self._render_generation:
            return
        if self._staged_history_content is None:
            return
        start = self._render_index
        if start >= len(self._pending_entries):
            return

        end = min(start + _RENDER_BATCH_SIZE, len(self._pending_entries))
        previous_role = self._pending_entries[start - 1].role if start > 0 else None
        self.history_content.setUpdatesEnabled(False)
        try:
            for entry in self._pending_entries[start:end]:
                self._add_entry(entry, show_meta=entry.role != previous_role)
                previous_role = entry.role
            self._update_bubble_widths()
        finally:
            self.history_content.setUpdatesEnabled(True)
        self._render_index = end

        if self._render_index >= len(self._pending_entries):
            self.history_layout.addStretch(1)
            self.history_layout.activate()
            self._install_history_content(
                self._staged_history_content,
                self.history_layout,
                list(self._bubble_frames),
            )
            self._staged_history_content = None
            self._schedule_layout_update()
            return
        self._single_shot(0, lambda generation=generation: self._render_next_batch(generation))

    def _add_entry(self, entry: ChatHistoryEntry, *, show_meta: bool = True) -> None:
        view = _entry_view_model(entry, self.subtitle_language, self.history_store.assistant_name)

        row = QWidget(self.history_content)
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(0, 0, 0, 0)
        row_layout.setSpacing(0)

        entry_column = QWidget(row)
        entry_column_layout = QVBoxLayout(entry_column)
        entry_column_layout.setContentsMargins(0, 0, 0, 0)
        entry_column_layout.setSpacing(4)

        bubble = QFrame(entry_column)
        bubble.setObjectName(view.bubble_object_name)
        bubble_layout = QVBoxLayout(bubble)
        bubble_layout.setContentsMargins(14, 12, 14, 12)
        bubble_layout.setSpacing(0)

        content_label = QLabel(view.content, bubble)
        content_label.setObjectName(_content_object_name(view.bubble_object_name))
        content_label.setWordWrap(True)
        content_label.setTextFormat(Qt.TextFormat.PlainText)
        content_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse | Qt.TextInteractionFlag.LinksAccessibleByMouse
        )

        if show_meta:
            meta_label = QLabel(view.meta_text, entry_column)
            meta_label.setObjectName("entryMeta")
            meta_label.setAlignment(_label_alignment(view.align))
            entry_column_layout.addWidget(meta_label)

        bubble_layout.addWidget(content_label)
        entry_column_layout.addWidget(bubble)

        if view.align == "right":
            row_layout.addStretch(1)
            row_layout.addWidget(entry_column)
        elif view.align == "center":
            row_layout.addStretch(1)
            row_layout.addWidget(entry_column)
            row_layout.addStretch(1)
        else:
            row_layout.addWidget(entry_column)
            row_layout.addStretch(1)

        self._bubble_frames.append(bubble)
        self.history_layout.addWidget(row)

    def _update_bubble_widths(self) -> None:
        width = self.history_view.viewport().width()
        if width < 320:
            width = self.history_view.width() - 2
        if width < 320:
            width = self.width() - 36
        if width <= 0:
            return

        available_width = max(1, width - 40)
        target_width = int(width * 0.82)
        max_width = min(max(260, target_width), available_width)
        for bubble in self._bubble_frames:
            bubble.setFixedWidth(max_width)
            bubble.updateGeometry()

    def _schedule_layout_update(self) -> None:
        for delay_ms in (0, 60, 160, 320):
            self._single_shot(delay_ms, self._sync_history_layout)

    def _sync_history_layout(self) -> None:
        # 延迟触发的 singleShot 可能在窗口被销毁后才执行（典型场景是 pytest-qt
        # 拆除时的 processEvents），此时底层 C++ QObject 已失效，直接访问会抛
        # ``RuntimeError: Internal C++ object already deleted``。先确认存活再继续。
        if shiboken6 is not None:
            try:
                if not shiboken6.isValid(self):
                    return
            except RuntimeError:
                return
        self._update_bubble_widths()
        self.history_layout.activate()
        self.history_content.adjustSize()
        self._scroll_to_bottom()

    def _single_shot(self, delay_ms: int, callback) -> None:  # type: ignore[no-untyped-def]
        timer = QTimer(self)
        timer.setSingleShot(True)
        self._owned_timers.append(timer)

        def run() -> None:
            if timer in self._owned_timers:
                self._owned_timers.remove(timer)
            timer.deleteLater()
            if self._is_alive():
                callback()

        timer.timeout.connect(run)
        timer.start(max(0, int(delay_ms)))

    def _is_alive(self) -> bool:
        if shiboken6 is None:
            return True
        try:
            return bool(shiboken6.isValid(self))
        except RuntimeError:
            return False

    def _scroll_to_bottom(self) -> None:
        scrollbar = self.history_view.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())


def _entry_view_model(
    entry: ChatHistoryEntry,
    subtitle_language: str,
    assistant_name: str,
) -> HistoryEntryView:
    role_name, align, bubble_object_name = _role_style(entry.role, assistant_name)
    time_text = _format_time(entry.created_at)
    return HistoryEntryView(
        role_name=role_name,
        align=align,
        bubble_object_name=bubble_object_name,
        meta_text=f"{role_name} · {time_text}",
        content=_humanize_history_content(_entry_display_content(entry, subtitle_language)),
    )


def _entry_display_content(entry: ChatHistoryEntry, subtitle_language: str) -> str:
    if entry.role == "assistant":
        parsed = parse_chat_reply_result(entry.content.strip())
        if not parsed.needs_retry and parsed.reply.text != entry.content.strip():
            return parsed.reply.display_text(subtitle_language)
    return entry.display_content(subtitle_language)


def _role_style(role: str, assistant_name: str) -> tuple[str, str, str]:
    if role == "user":
        return ("你", "right", "userBubble")
    if role == "assistant":
        return (assistant_name, "left", "assistantBubble")
    if role == "error":
        return ("错误", "left", "errorBubble")
    return ("系统记录", "center", "systemBubble")


def _label_alignment(align: str) -> Qt.AlignmentFlag:
    if align == "right":
        return Qt.AlignmentFlag.AlignRight
    if align == "center":
        return Qt.AlignmentFlag.AlignCenter
    return Qt.AlignmentFlag.AlignLeft


def _content_object_name(bubble_object_name: str) -> str:
    if bubble_object_name == "errorBubble":
        return "errorText"
    if bubble_object_name == "systemBubble":
        return "systemText"
    return "entryText"


def _humanize_history_content(content: str) -> str:
    """把内部屏幕记录标记转换成适合历史窗口展示的提示。"""

    lines = content.splitlines()
    if not lines:
        return content
    return "\n".join(_humanize_history_line(line) for line in lines)


def _humanize_history_line(line: str) -> str:
    stripped = line.strip()
    normalized = _VISUAL_ID_SUFFIX_RE.sub("", stripped)
    if normalized in _HISTORY_MARKER_DISPLAY_TEXT:
        return _HISTORY_MARKER_DISPLAY_TEXT[normalized]
    return line


def _format_time(created_at: str) -> str:
    time_text = created_at.replace("T", " ").replace("Z", "")
    for separator in ("+", "."):
        time_text = time_text.split(separator, 1)[0]
    return time_text
