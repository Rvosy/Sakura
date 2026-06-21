from __future__ import annotations

from typing import Any

from PySide6.QtWidgets import QCheckBox, QFormLayout, QLineEdit, QPushButton, QSpinBox, QVBoxLayout, QWidget


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
        self.token.setEchoMode(QLineEdit.EchoMode.Password)
        self.save_button = QPushButton("保存并重启手机端", self)

        form = QFormLayout()
        form.addRow("", self.enabled)
        form.addRow("监听地址", self.host)
        form.addRow("端口", self.port)
        form.addRow("访问 token", self.token)

        layout = QVBoxLayout()
        layout.addLayout(form)
        layout.addWidget(self.save_button)
        layout.addStretch(1)
        self.setLayout(layout)

        self.enabled.toggled.connect(self._sync_controls)
        self.save_button.clicked.connect(self._save)
        self._sync_controls(self.enabled.isChecked())

    def _sync_controls(self, enabled: bool) -> None:
        self.host.setEnabled(enabled)
        self.port.setEnabled(enabled)
        self.token.setEnabled(enabled)

    def _save(self) -> None:
        token = self.token.text().strip()
        if self.enabled.isChecked() and not token:
            return
        self.plugin.save_config(
            {
                "enabled": self.enabled.isChecked(),
                "host": self.host.text().strip() or "127.0.0.1",
                "port": self.port.value(),
                "token": token or "sakura",
            }
        )
