from __future__ import annotations

from typing import Any

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QCheckBox, QFormLayout, QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPushButton, QSpinBox, QVBoxLayout, QWidget
import shiboken6

from plugins.sakura_mobile.server import DEFAULT_HOST


class SakuraMobileSettingsPanel(QWidget):
    def __init__(self, plugin: Any, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.plugin = plugin
        config = plugin.config()

        self.enabled = QCheckBox("启用手机网页端", self)
        self.enabled.setChecked(bool(config["enabled"]))
        self.host = QLineEdit(str(config["host"]), self)
        self.port = QSpinBox(self)
        self.port.setRange(1, 65535)
        self.port.setValue(int(config["port"]))
        self.token = QLineEdit(str(config["token"]), self)
        self.token.setEchoMode(QLineEdit.EchoMode.Normal)
        self.status_label = QLabel("", self)
        self.local_url = QLineEdit(self)
        self.local_url.setReadOnly(True)
        self.lan_url = QLineEdit(self)
        self.lan_url.setReadOnly(True)
        self.copy_local_button = QPushButton("复制本机链接", self)
        self.copy_lan_button = QPushButton("复制内网链接", self)
        self.save_button = QPushButton("保存并重启手机端", self)

        form = QFormLayout()
        form.addRow("", self.enabled)
        form.addRow("监听地址", self.host)
        form.addRow("端口", self.port)
        form.addRow("访问 token", self.token)
        form.addRow("运行状态", self.status_label)
        form.addRow("本机链接", self._link_row(self.local_url, self.copy_local_button))
        form.addRow("内网链接", self._link_row(self.lan_url, self.copy_lan_button))

        layout = QVBoxLayout()
        layout.addLayout(form)
        layout.addWidget(self.save_button)
        layout.addStretch(1)
        self.setLayout(layout)

        self.enabled.toggled.connect(self._sync_controls)
        self.copy_local_button.clicked.connect(lambda: self._copy(self.local_url.text(), self.copy_local_button))
        self.copy_lan_button.clicked.connect(lambda: self._copy(self.lan_url.text(), self.copy_lan_button))
        self.save_button.clicked.connect(self._save)
        self._sync_controls(self.enabled.isChecked())
        self._refresh_status()

    def _link_row(self, edit: QLineEdit, button: QPushButton) -> QWidget:
        row = QWidget(self)
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(edit, 1)
        layout.addWidget(button)
        return row

    def _sync_controls(self, enabled: bool) -> None:
        self.host.setEnabled(enabled)
        self.port.setEnabled(enabled)
        self.token.setEnabled(enabled)
        self._refresh_status()

    def _save(self) -> None:
        token = self.token.text().strip()
        if self.enabled.isChecked() and not token:
            QMessageBox.warning(self, "配置无效", "启用手机网页端时访问 token 不能为空。")
            self.token.setFocus()
            return
        self.plugin.save_config(
            {
                "enabled": self.enabled.isChecked(),
                "host": self.host.text().strip() or DEFAULT_HOST,
                "port": self.port.value(),
                "token": token or "sakura",
            }
        )
        status = self._refresh_status()
        if not self.enabled.isChecked():
            QMessageBox.information(self, "手机端已关闭", "手机网页端已关闭。")
        elif status.get("running"):
            QMessageBox.information(self, "手机端已启动", self._status_links_message(status))
        else:
            QMessageBox.warning(self, "手机端启动失败", str(status.get("error") or "服务未启动。"))

    def _refresh_status(self) -> dict[str, Any]:
        status = self.plugin.status()
        if not status["enabled"]:
            self.status_label.setText("未启动")
        elif status["running"]:
            self.status_label.setText("运行中")
        else:
            error = str(status.get("error") or "未启动")
            self.status_label.setText(f"启动失败：{error}" if status.get("error") else error)
        self.local_url.setText(str(status.get("local_url") or ""))
        lan_urls = status.get("lan_urls") or []
        self.lan_url.setText(" ; ".join(lan_urls) if lan_urls else "未发现内网地址")
        self.local_url.setCursorPosition(0)
        self.lan_url.setCursorPosition(0)
        return status

    def _status_links_message(self, status: dict[str, Any]) -> str:
        lan_urls = status.get("lan_urls") or []
        lan_text = "\n".join(lan_urls) if lan_urls else "未发现内网地址"
        return f"本机链接：\n{status.get('local_url', '')}\n\n内网链接：\n{lan_text}"

    def _copy(self, text: str, button: QPushButton) -> None:
        clean = text.strip()
        original_text = str(button.property("originalText") or button.text())
        button.setProperty("originalText", original_text)
        if not clean or clean == "未发现内网地址":
            button.setText("无链接")
            QTimer.singleShot(1200, lambda: _restore_button_text(button, original_text))
            return
        QApplication.clipboard().setText(clean)
        button.setText("已复制")
        QTimer.singleShot(1200, lambda: _restore_button_text(button, original_text))


def _restore_button_text(button: QPushButton, text: str) -> None:
    if shiboken6.isValid(button):
        button.setText(text)
