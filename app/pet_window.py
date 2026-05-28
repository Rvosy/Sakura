from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QEvent, QPoint, QRect, Qt, QThread, Slot
from PySide6.QtGui import QAction, QCursor, QFont, QFontDatabase, QIcon, QMouseEvent, QPixmap
from PySide6.QtWidgets import (
    QApplication,
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
from app.chat_reply import ChatReply
from app.chat_worker import ChatWorker
from app.tts import TTSProvider


class PetWindow(QWidget):
    def __init__(
        self,
        portrait_path: Path,
        api_client: OpenAICompatibleClient,
        system_prompt: str,
        tts_provider: TTSProvider,
    ) -> None:
        super().__init__()
        self.portrait_path = portrait_path
        self.api_client = api_client
        self.system_prompt = system_prompt
        self.tts_provider = tts_provider
        self.messages: list[dict[str, str]] = []
        self.thread: QThread | None = None
        self.worker: ChatWorker | None = None
        self.drag_offset: QPoint | None = None
        self.stage_size = (860, 640)

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
        self.set_speech("......")
        next_messages = [*self.messages, {"role": "user", "content": text}]
        self.messages = next_messages
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
        self.set_speech(reply.text)
        self.tts_provider.speak(reply.text, reply.tone)

    @Slot(str)
    def _handle_error(self, message: str) -> None:
        if self.messages and self.messages[-1]["role"] == "user":
            self.messages.pop()
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
        self.speech_label.setText(cleaned)

    def toggle_visible(self) -> None:
        if self.isVisible():
            self.hide()
        else:
            self.show()
            self.raise_()


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
