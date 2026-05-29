from __future__ import annotations

from html import escape

from PySide6.QtWidgets import QDialog, QHBoxLayout, QMessageBox, QPushButton, QTextBrowser, QVBoxLayout

from app.chat_history import ChatHistoryEntry, ChatHistoryStore


class HistoryWindow(QDialog):
    def __init__(self, history_store: ChatHistoryStore, parent=None) -> None:  # type: ignore[no-untyped-def]
        super().__init__(parent)
        self.history_store = history_store

        self.setWindowTitle("历史记录")
        self.resize(560, 640)

        self.history_view = QTextBrowser(self)
        self.history_view.setOpenExternalLinks(True)

        self.refresh_button = QPushButton("刷新", self)
        self.refresh_button.clicked.connect(self.refresh)

        self.clear_button = QPushButton("清空历史", self)
        self.clear_button.clicked.connect(self.clear_history)

        self.close_button = QPushButton("关闭", self)
        self.close_button.clicked.connect(self.close)

        button_layout = QHBoxLayout()
        button_layout.addWidget(self.refresh_button)
        button_layout.addStretch(1)
        button_layout.addWidget(self.clear_button)
        button_layout.addWidget(self.close_button)

        layout = QVBoxLayout()
        layout.addWidget(self.history_view, 1)
        layout.addLayout(button_layout)
        self.setLayout(layout)

        self.setStyleSheet(
            """
            QDialog {
                background: #f4fbfd;
                color: #24343a;
                font-family: "Microsoft YaHei", "Yu Gothic UI", sans-serif;
                font-size: 14px;
            }
            QTextBrowser {
                background: rgba(226, 246, 250, 0.86);
                border: 1px solid rgba(120, 176, 188, 0.55);
                border-radius: 10px;
                padding: 12px;
            }
            QPushButton {
                background: #72c7d6;
                border: none;
                border-radius: 8px;
                color: white;
                min-width: 72px;
                padding: 8px 12px;
                font-weight: 600;
            }
            QPushButton:hover {
                background: #5eb7c8;
            }
            """
        )
        self.refresh()

    def refresh(self) -> None:
        entries = self.history_store.load()
        if not entries:
            self.history_view.setHtml("<p>暂无历史记录。</p>")
            return
        self.history_view.setHtml("".join(_format_entry(entry) for entry in entries))
        scrollbar = self.history_view.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

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


def _format_entry(entry: ChatHistoryEntry) -> str:
    role_name = {
        "user": "你",
        "assistant": "桜",
        "error": "错误",
    }.get(entry.role, entry.role)
    time_text = entry.created_at.replace("T", " ").split("+", 1)[0]
    content = escape(entry.content).replace("\n", "<br>")
    return (
        "<div style='margin: 0 0 14px 0;'>"
        f"<div style='color: #5f8790; font-size: 12px;'>{escape(time_text)}</div>"
        f"<b>{escape(role_name)}：</b><br>{content}"
        "</div>"
    )
