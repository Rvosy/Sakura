from __future__ import annotations

from urllib.parse import urlparse

from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLineEdit,
    QMessageBox,
    QSpinBox,
    QVBoxLayout,
)

from app.api_client import ApiSettings


class ApiSettingsDialog(QDialog):
    def __init__(self, settings: ApiSettings, parent=None) -> None:  # type: ignore[no-untyped-def]
        super().__init__(parent)
        self.result_settings: ApiSettings | None = None

        self.setWindowTitle("API 设置")
        self.resize(460, 220)

        self.base_url_edit = QLineEdit(settings.base_url, self)
        self.base_url_edit.setPlaceholderText("https://api.openai.com/v1")

        self.api_key_edit = QLineEdit(settings.api_key, self)
        self.api_key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.api_key_edit.setPlaceholderText("请输入 API Key")

        self.model_edit = QLineEdit(settings.model, self)
        self.model_edit.setPlaceholderText("gpt-4.1-mini")

        self.timeout_spin = QSpinBox(self)
        self.timeout_spin.setRange(1, 600)
        self.timeout_spin.setSuffix(" 秒")
        self.timeout_spin.setValue(settings.timeout_seconds)

        form_layout = QFormLayout()
        form_layout.addRow("Base URL", self.base_url_edit)
        form_layout.addRow("API Key", self.api_key_edit)
        form_layout.addRow("模型", self.model_edit)
        form_layout.addRow("超时", self.timeout_spin)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel,
            self,
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout()
        layout.addLayout(form_layout)
        layout.addWidget(buttons)
        self.setLayout(layout)

    def accept(self) -> None:
        settings = self._validated_settings()
        if settings is None:
            return
        self.result_settings = settings
        super().accept()

    def _validated_settings(self) -> ApiSettings | None:
        base_url = self.base_url_edit.text().strip().rstrip("/")
        api_key = self.api_key_edit.text().strip()
        model = self.model_edit.text().strip()
        timeout_seconds = self.timeout_spin.value()

        parsed_url = urlparse(base_url)
        if parsed_url.scheme not in {"http", "https"} or not parsed_url.netloc:
            QMessageBox.warning(self, "配置无效", "Base URL 必须是有效的 http 或 https 地址。")
            return None
        if not api_key:
            QMessageBox.warning(self, "配置无效", "API Key 不能为空。")
            return None
        if not model:
            QMessageBox.warning(self, "配置无效", "模型不能为空。")
            return None

        return ApiSettings(
            base_url=base_url,
            api_key=api_key,
            model=model,
            timeout_seconds=timeout_seconds,
        )
