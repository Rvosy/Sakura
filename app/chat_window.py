from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QThread, Signal, Slot
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from app.agent import AgentResult, AgentRuntime, create_builtin_tool_registry
from app.api_client import OpenAICompatibleClient
from app.chat_worker import ChatWorker
from app.tts import TTSProvider


class ChatWindow(QWidget):
    assistant_replied = Signal(str)

    def __init__(
        self,
        api_client: OpenAICompatibleClient,
        system_prompt: str,
        tts_provider: TTSProvider,
        base_dir: Path | None = None,
    ) -> None:
        super().__init__()
        base_dir = base_dir or Path(__file__).resolve().parents[1]
        self.api_client = api_client
        self.system_prompt = system_prompt
        self.agent_runtime = AgentRuntime(
            api_client=api_client,
            system_prompt=system_prompt,
            tools=create_builtin_tool_registry(base_dir),
        )
        self.tts_provider = tts_provider
        self.messages: list[dict[str, str]] = []
        self.thread: QThread | None = None
        self.worker: ChatWorker | None = None

        self.setWindowTitle("夜乃桜")
        self.resize(520, 640)
        self.setStyleSheet(
            """
            QWidget {
                background: #f4fbfd;
                color: #24343a;
                font-family: "Microsoft YaHei", "Yu Gothic UI", sans-serif;
                font-size: 14px;
            }
            QTextBrowser {
                background: rgba(226, 246, 250, 0.86);
                border: 1px solid rgba(120, 176, 188, 0.55);
                border-radius: 12px;
                padding: 14px;
                selection-background-color: #7cc8d7;
            }
            QLineEdit {
                background: rgba(255, 255, 255, 0.92);
                border: 1px solid rgba(120, 176, 188, 0.65);
                border-radius: 18px;
                padding: 9px 14px;
            }
            QPushButton {
                background: #72c7d6;
                border: none;
                border-radius: 18px;
                color: white;
                min-width: 72px;
                padding: 9px 14px;
                font-weight: 600;
            }
            QPushButton:hover {
                background: #5eb7c8;
            }
            QPushButton:disabled {
                background: #a9c7ce;
            }
            """
        )

        self.history_view = QTextBrowser()
        self.history_view.setOpenExternalLinks(True)

        self.input_edit = QLineEdit()
        self.input_edit.setPlaceholderText("输入要对桜说的话...")
        self.input_edit.returnPressed.connect(self.send_message)

        self.send_button = QPushButton("发送")
        self.send_button.clicked.connect(self.send_message)

        input_layout = QHBoxLayout()
        input_layout.addWidget(self.input_edit, 1)
        input_layout.addWidget(self.send_button)

        layout = QVBoxLayout()
        layout.addWidget(self.history_view, 1)
        layout.addLayout(input_layout)
        self.setLayout(layout)

    @Slot()
    def send_message(self) -> None:
        text = self.input_edit.text().strip()
        if not text or self.thread is not None:
            return

        self.input_edit.clear()
        self._append_message("你", text)
        next_messages = [*self.messages, {"role": "user", "content": text}]
        self.messages = next_messages
        self._set_busy(True)

        self.thread = QThread(self)
        self.worker = ChatWorker(self.agent_runtime, next_messages)
        self.worker.moveToThread(self.thread)
        self.thread.started.connect(self.worker.run)
        self.worker.finished.connect(self._handle_reply)
        self.worker.failed.connect(self._handle_error)
        self.worker.finished.connect(self.thread.quit)
        self.worker.failed.connect(self.thread.quit)
        self.thread.finished.connect(self._cleanup_worker)
        self.thread.start()

    @Slot(object)
    def _handle_reply(self, result: AgentResult) -> None:
        reply = result.reply
        reply_text = reply.text
        self.messages.append({"role": "assistant", "content": reply_text})
        self._append_message("桜", reply_text)
        self.assistant_replied.emit(reply_text)
        self.tts_provider.speak(reply_text, reply.tone)

    @Slot(str)
    def _handle_error(self, message: str) -> None:
        if self.messages and self.messages[-1]["role"] == "user":
            self.messages.pop()
        self._append_message("错误", message)
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
        self.send_button.setText("等待中..." if busy else "发送")

    def _append_message(self, sender: str, message: str) -> None:
        safe_sender = _escape_html(sender)
        safe_message = _escape_html(message).replace("\n", "<br>")
        self.history_view.append(f"<b>{safe_sender}：</b><br>{safe_message}<br>")


def _escape_html(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )
