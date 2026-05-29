from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QEvent, QPoint, QRect, Qt, QThread, QTimer, Slot
from PySide6.QtGui import QAction, QCursor, QFont, QFontDatabase, QIcon, QMouseEvent, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QPushButton,
    QSystemTrayIcon,
    QVBoxLayout,
    QWidget,
)

from app.api_client import OpenAICompatibleClient
from app.chat_history import ChatHistoryStore
from app.chat_reply import ChatReply, ChatSegment
from app.chat_worker import ChatWorker
from app.history_window import HistoryWindow
from app.settings_dialog import SettingsDialog
from app.tts import (
    GPTSoVITSTTSProvider,
    GPTSoVITSTTSSettings,
    NullTTSProvider,
    TTSConfigError,
    TTSProvider,
)


SPEECH_TYPING_INTERVAL_MS = 35
SPEECH_START_DELAY_MS = 500
REPLY_SEGMENT_PAUSE_MS = 900


class PetWindow(QWidget):
    def __init__(
        self,
        base_dir: Path,
        portrait_path: Path,
        api_client: OpenAICompatibleClient,
        system_prompt: str,
        tts_provider: TTSProvider,
    ) -> None:
        super().__init__()
        self.base_dir = base_dir
        self.env_path = base_dir / ".env"
        self.portrait_path = portrait_path
        self.api_client = api_client
        self.system_prompt = system_prompt
        self.tts_provider = tts_provider
        self.retired_tts_providers: list[TTSProvider] = []
        self.history_store = ChatHistoryStore(base_dir / "data" / "chat_history.jsonl")
        self.history_window: HistoryWindow | None = None
        self.messages: list[dict[str, str]] = []
        self.thread: QThread | None = None
        self.worker: ChatWorker | None = None
        self.drag_offset: QPoint | None = None
        self.stage_size = (860, 640)
        self.speech_text = ""
        self.speech_index = 0
        self.pending_reply_segments: list[ChatSegment] = []
        self.reply_sequence_id = 0
        self.current_segment_sequence_id: int | None = None
        self.current_segment_speech_done = False
        self.current_segment_tts_done = True
        self.reply_advance_scheduled = False
        self.speech_timer = QTimer(self)
        self.speech_timer.setInterval(SPEECH_TYPING_INTERVAL_MS)
        self.speech_timer.timeout.connect(self._show_next_speech_char)

        self.setWindowTitle("夜乃桜")
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

        self.label = QLabel(self)
        self.label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.label.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.label.customContextMenuRequested.connect(self._show_context_menu)

        self.bubble = QFrame(self)
        self.bubble.setObjectName("speechBubble")
        self.bubble.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.bubble.customContextMenuRequested.connect(self._show_context_menu)

        self.name_label = QLabel("夜乃桜", self.bubble)
        self.name_label.setObjectName("speakerName")

        self.speech_label = QLabel("……起動した。用事があるなら、呼んで。", self.bubble)
        self.speech_label.setObjectName("speechText")
        self.speech_label.setWordWrap(True)
        self.speech_label.setAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)

        bubble_header = QHBoxLayout()
        bubble_header.setContentsMargins(0, 0, 0, 0)
        bubble_header.addWidget(self.name_label)
        bubble_header.addStretch(1)

        bubble_layout = QVBoxLayout()
        bubble_layout.setContentsMargins(22, 12, 22, 14)
        bubble_layout.setSpacing(6)
        bubble_layout.addLayout(bubble_header)
        bubble_layout.addWidget(self.speech_label, 1)
        self.bubble.setLayout(bubble_layout)

        self.input_bar = QFrame(self)
        self.input_bar.setObjectName("inputBar")

        self.input_edit = QLineEdit(self.input_bar)
        self.input_edit.setObjectName("petInput")
        self.input_edit.setPlaceholderText("桜に話しかける...")
        self.input_edit.setFixedHeight(34)
        self.input_edit.returnPressed.connect(self.send_message)

        self.send_button = QPushButton("发送", self.input_bar)
        self.send_button.setObjectName("sendButton")
        self.send_button.setFixedHeight(34)
        self.send_button.clicked.connect(self.send_message)

        input_layout = QHBoxLayout()
        input_layout.setContentsMargins(0, 5, 0, 5)
        input_layout.setSpacing(8)
        input_layout.addWidget(self.input_edit, 1)
        input_layout.addWidget(self.send_button)
        self.input_bar.setLayout(input_layout)

        self.setStyleSheet(
            """
            #speechBubble {
                background: rgba(255, 232, 241, 188);
                border: 1px solid rgba(238, 172, 200, 132);
                border-radius: 26px;
            }
            #speakerName {
                color: #d55b91;
                font-size: 13px;
                font-weight: 700;
            }
            #speechText {
                color: #4b3440;
                font-size: 19px;
                line-height: 1.35;
            }
            #inputBar {
                background: transparent;
                border: none;
            }
            #petInput {
                background: rgba(255, 255, 255, 132);
                border: 1px solid rgba(255, 255, 255, 1);
                border-radius: 17px;
                color: #4b3440;
                font-size: 13px;
                padding: 2px 14px;
            }
            #petInput:disabled {
                color: rgba(75, 52, 64, 130);
            }
            #sendButton {
                background: rgba(74, 170, 214, 225);
                border: none;
                border-radius: 16px;
                color: white;
                font-size: 15px;
                font-weight: 800;
                min-width: 68px;
                padding: 4px 14px;
            }
            #sendButton:hover {
                background: rgba(48, 145, 195, 235);
            }
            #sendButton:disabled {
                background: rgba(126, 171, 193, 190);
            }
            """
        )
        self._apply_fonts()
        for drag_widget in (self.label, self.bubble, self.name_label, self.speech_label):
            drag_widget.installEventFilter(self)

        self.pixmap = self._load_portrait()
        self._apply_portrait()
        self._create_tray_icon()
        self._move_to_default_position()

    def resizeEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        super().resizeEvent(event)
        self._layout_stage()

    def eventFilter(self, watched, event) -> bool:  # type: ignore[no-untyped-def]
        if isinstance(event, QMouseEvent):
            if event.type() == QEvent.Type.MouseButtonPress:
                return self._handle_mouse_press(event)
            if event.type() == QEvent.Type.MouseMove:
                return self._handle_mouse_move(event)
            if event.type() == QEvent.Type.MouseButtonRelease:
                return self._handle_mouse_release(event)
        return super().eventFilter(watched, event)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        self._handle_mouse_press(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        self._handle_mouse_move(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        self._handle_mouse_release(event)

    def _handle_mouse_press(self, event: QMouseEvent) -> bool:
        if event.button() == Qt.MouseButton.LeftButton:
            self.drag_offset = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()
            return True
        if event.button() == Qt.MouseButton.RightButton:
            self._show_context_menu(event.position().toPoint())
            event.accept()
            return True
        return False

    def _handle_mouse_move(self, event: QMouseEvent) -> bool:
        if event.buttons() & Qt.MouseButton.LeftButton and self.drag_offset is not None:
            self.move(event.globalPosition().toPoint() - self.drag_offset)
            event.accept()
            return True
        return False

    def _handle_mouse_release(self, event: QMouseEvent) -> bool:
        if event.button() == Qt.MouseButton.LeftButton:
            self.drag_offset = None
            event.accept()
            return True
        return False

    def _load_portrait(self) -> QPixmap:
        pixmap = QPixmap(str(self.portrait_path))
        if pixmap.isNull():
            QMessageBox.critical(
                self,
                "立绘加载失败",
                f"无法加载立绘：{self.portrait_path}",
            )
        return pixmap

    def _apply_portrait(self) -> None:
        if self.pixmap.isNull():
            self.resize(*self.stage_size)
            return

        scaled = self.pixmap.scaled(
            560,
            570,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self.label.setPixmap(scaled)
        self.label.resize(scaled.size())
        self.resize(*self.stage_size)
        self._layout_stage()

    def _apply_fonts(self) -> None:
        text_font = _rounded_japanese_font(11, QFont.Weight.Normal)
        speech_font = _rounded_japanese_font(15, QFont.Weight.Medium)
        name_font = _rounded_japanese_font(10, QFont.Weight.Bold)
        button_font = _rounded_japanese_font(11, QFont.Weight.ExtraBold)

        self.name_label.setFont(name_font)
        self.speech_label.setFont(speech_font)
        self.input_edit.setFont(text_font)
        self.send_button.setFont(button_font)

    def _layout_stage(self) -> None:
        width = self.width()
        height = self.height()
        portrait_width = self.label.width()
        portrait_height = self.label.height()
        self.label.move((width - portrait_width) // 2, max(0, height - portrait_height - 62))

        bubble_width = min(640, width - 96)
        bubble_height = 128
        input_height = 44
        input_gap = 10
        bubble_x = (width - bubble_width) // 2
        bubble_y = height - bubble_height - input_height - input_gap - 108
        self.bubble.setGeometry(QRect(bubble_x, bubble_y, bubble_width, bubble_height))
        self.bubble.raise_()

        input_y = bubble_y + bubble_height + input_gap
        self.input_bar.setGeometry(QRect(bubble_x, input_y, bubble_width, input_height))
        self.input_bar.raise_()

    def _create_tray_icon(self) -> None:
        icon = QIcon(self.pixmap) if not self.pixmap.isNull() else QIcon()
        self.tray_icon = QSystemTrayIcon(icon, self)
        self.tray_icon.setToolTip("夜乃桜")
        self.tray_icon.setContextMenu(self._build_menu())
        self.tray_icon.activated.connect(self._handle_tray_activated)
        self.tray_icon.show()

    def _build_menu(self) -> QMenu:
        menu = QMenu(self)

        toggle_action = QAction("隐藏/显示立绘", self)
        toggle_action.triggered.connect(self.toggle_visible)
        menu.addAction(toggle_action)

        menu.addSeparator()

        history_action = QAction("历史记录", self)
        history_action.triggered.connect(self.show_history)
        menu.addAction(history_action)

        settings_action = QAction("设置", self)
        settings_action.triggered.connect(self.show_settings)
        menu.addAction(settings_action)

        menu.addSeparator()

        quit_action = QAction("退出", self)
        quit_action.triggered.connect(QApplication.quit)
        menu.addAction(quit_action)

        return menu

    def _show_context_menu(self, position: QPoint) -> None:
        _ = position
        self._build_menu().exec(QCursor.pos())

    def _handle_tray_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self.toggle_visible()

    def _move_to_default_position(self) -> None:
        screen = QApplication.primaryScreen()
        if screen is None:
            return
        geometry = screen.availableGeometry()
        x = geometry.right() - self.width() - 40
        y = geometry.bottom() - self.height() - 20
        self.move(max(geometry.left(), x), max(geometry.top(), y))

    @Slot()
    def send_message(self) -> None:
        text = self.input_edit.text().strip()
        if not text or self.thread is not None:
            return

        self.input_edit.clear()
        self.reply_sequence_id += 1
        self.pending_reply_segments = []
        self._reset_current_segment_progress()
        self.set_speech("......")
        next_messages = [*self.messages, {"role": "user", "content": text}]
        self.messages = next_messages
        self._record_history("user", text)
        self._set_busy(True)

        self.thread = QThread(self)
        self.worker = ChatWorker(self.api_client, self.system_prompt, next_messages)
        self.worker.moveToThread(self.thread)
        self.thread.started.connect(self.worker.run)
        self.worker.finished.connect(self._handle_reply)
        self.worker.failed.connect(self._handle_error)
        self.worker.finished.connect(self.thread.quit)
        self.worker.failed.connect(self.thread.quit)
        self.thread.finished.connect(self._cleanup_worker)
        self.thread.start()

    @Slot(object)
    def _handle_reply(self, reply: ChatReply) -> None:
        self.messages.append({"role": "assistant", "content": reply.text})
        self._record_history("assistant", reply.text)
        self._show_reply_segments(reply.segments)

    @Slot(str)
    def _handle_error(self, message: str) -> None:
        if self.messages and self.messages[-1]["role"] == "user":
            self.messages.pop()
        self._record_history("error", message)
        self._reset_current_segment_progress()
        self.set_speech("……通信に失敗した。設定を確認して。")
        QMessageBox.warning(self, "请求失败", message)

    @Slot()
    def _cleanup_worker(self) -> None:
        if self.worker is not None:
            self.worker.deleteLater()
        if self.thread is not None:
            self.thread.deleteLater()
        self.worker = None
        self.thread = None
        self._set_busy(False)

    def _set_busy(self, busy: bool) -> None:
        self.input_edit.setEnabled(not busy)
        self.send_button.setEnabled(not busy)
        self.send_button.setText("等待" if busy else "发送")

    @Slot(str)
    def set_speech(self, text: str) -> None:
        cleaned = " ".join(text.split())
        self.speech_timer.stop()
        self.speech_text = cleaned
        self.speech_index = 0
        self.speech_label.clear()
        if self.speech_text:
            self.speech_timer.start()

    @Slot()
    def _show_next_speech_char(self) -> None:
        if self.speech_index >= len(self.speech_text):
            self.speech_timer.stop()
            return

        self.speech_index += 1
        self.speech_label.setText(self.speech_text[: self.speech_index])
        if self.speech_index >= len(self.speech_text):
            self.speech_timer.stop()
            if self.current_segment_sequence_id is not None:
                self._mark_segment_speech_done(self.current_segment_sequence_id)

    def toggle_visible(self) -> None:
        if self.isVisible():
            self.hide()
        else:
            self.show()
            self.raise_()

    @Slot()
    def show_history(self) -> None:
        if self.history_window is None:
            self.history_window = HistoryWindow(self.history_store, self)
        self.history_window.refresh()
        self.history_window.show()
        self.history_window.raise_()
        self.history_window.activateWindow()

    @Slot()
    def show_settings(self) -> None:
        try:
            tts_settings = GPTSoVITSTTSSettings.load(self.env_path, self.base_dir, validate_enabled=False)
        except (OSError, TTSConfigError) as exc:
            QMessageBox.warning(self, "配置读取失败", f"TTS 配置读取失败，将使用默认值打开设置：{exc}")
            tts_settings = self._default_tts_settings()

        dialog = SettingsDialog(self.api_client.settings, tts_settings, self.base_dir, self)
        if (
            dialog.exec() != QDialog.DialogCode.Accepted
            or dialog.result_api_settings is None
            or dialog.result_tts_settings is None
        ):
            return

        try:
            dialog.result_api_settings.save(self.env_path)
            dialog.result_tts_settings.save(self.env_path, self.base_dir)
        except OSError as exc:
            QMessageBox.critical(self, "保存失败", f"无法保存设置：{exc}")
            return

        new_tts_provider = self._create_tts_provider_from_settings(dialog.result_tts_settings)
        if new_tts_provider is None:
            return

        self.api_client.update_settings(dialog.result_api_settings)
        self.retired_tts_providers.append(self.tts_provider)
        self.tts_provider = new_tts_provider
        self.reply_sequence_id += 1
        self.pending_reply_segments = []
        self._reset_current_segment_progress()
        QMessageBox.information(self, "保存成功", "设置已保存，后续聊天和朗读将使用新配置。")

    def _create_tts_provider_from_settings(
        self,
        settings: GPTSoVITSTTSSettings,
    ) -> TTSProvider | None:
        if not settings.enabled:
            return NullTTSProvider()
        try:
            return GPTSoVITSTTSProvider(settings)
        except TTSConfigError as exc:
            QMessageBox.critical(self, "TTS 配置无效", f"无法启用 TTS，当前语音配置保持不变：{exc}")
            return None

    def _default_tts_settings(self) -> GPTSoVITSTTSSettings:
        return GPTSoVITSTTSSettings(
            enabled=False,
            api_url="http://127.0.0.1:9880/tts",
            ref_audio_path=self.base_dir / "ref" / "VO01_2210.ogg",
            ref_text_path=self.base_dir / "ref" / "text.txt",
            ref_text="",
            ref_lang="ja",
            text_lang="ja",
            timeout_seconds=60,
        )

    def _record_history(self, role: str, content: str) -> None:
        try:
            self.history_store.append(role, content)
        except OSError as exc:
            print(f"[History] 写入失败：{exc}")

    def _show_reply_segments(self, segments: list[ChatSegment]) -> None:
        self.reply_sequence_id += 1
        self.pending_reply_segments = [segment for segment in segments if segment.text.strip()]
        self._reset_current_segment_progress()
        self._show_next_reply_segment(self.reply_sequence_id)

    def _show_next_reply_segment(self, sequence_id: int) -> None:
        if sequence_id != self.reply_sequence_id or not self.pending_reply_segments:
            return

        segment = self.pending_reply_segments.pop(0)
        self.current_segment_sequence_id = sequence_id
        self.current_segment_speech_done = False
        self.current_segment_tts_done = False
        self.reply_advance_scheduled = False
        self.tts_provider.speak(
            segment.text,
            segment.tone,
            on_finished=lambda: self._mark_segment_tts_done(sequence_id),
        )
        QTimer.singleShot(
            SPEECH_START_DELAY_MS,
            lambda: self._start_segment_speech(sequence_id, segment.text),
        )

    def _start_segment_speech(self, sequence_id: int, text: str) -> None:
        if sequence_id != self.reply_sequence_id or sequence_id != self.current_segment_sequence_id:
            return
        self.set_speech(text)

    def _mark_segment_speech_done(self, sequence_id: int) -> None:
        if sequence_id != self.reply_sequence_id or sequence_id != self.current_segment_sequence_id:
            return
        self.current_segment_speech_done = True
        self._schedule_next_reply_segment_if_ready(sequence_id)

    def _mark_segment_tts_done(self, sequence_id: int) -> None:
        if sequence_id != self.reply_sequence_id or sequence_id != self.current_segment_sequence_id:
            return
        self.current_segment_tts_done = True
        self._schedule_next_reply_segment_if_ready(sequence_id)

    def _schedule_next_reply_segment_if_ready(self, sequence_id: int) -> None:
        if (
            sequence_id != self.reply_sequence_id
            or sequence_id != self.current_segment_sequence_id
            or self.reply_advance_scheduled
            or not self.current_segment_speech_done
            or not self.current_segment_tts_done
            or not self.pending_reply_segments
        ):
            return

        self.reply_advance_scheduled = True
        QTimer.singleShot(
            REPLY_SEGMENT_PAUSE_MS,
            lambda: self._show_next_reply_segment(sequence_id),
        )

    def _reset_current_segment_progress(self) -> None:
        self.current_segment_sequence_id = None
        self.current_segment_speech_done = False
        self.current_segment_tts_done = True
        self.reply_advance_scheduled = False


def _rounded_japanese_font(point_size: int, weight: QFont.Weight) -> QFont:
    family = _choose_font_family([
        "BIZ UDPGothic",
        "Meiryo",
        "Yu Gothic UI",
        "Yu Gothic",
        "MS PGothic",
        "Microsoft YaHei UI",
        "Segoe UI",
    ])
    font = QFont(family)
    font.setPointSize(point_size)
    font.setWeight(weight)
    font.setStyleStrategy(QFont.StyleStrategy.PreferAntialias)
    return font


def _choose_font_family(candidates: list[str]) -> str:
    available = set(QFontDatabase.families())
    for candidate in candidates:
        if candidate in available:
            return candidate
    return candidates[-1]
