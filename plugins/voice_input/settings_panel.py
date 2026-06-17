from __future__ import annotations

import threading
from typing import Any

from PySide6.QtCore import Signal, Slot
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from .config import VoiceInputConfig, load_voice_input_config, save_voice_input_config
from .model_manager import MODEL_SPECS, download_model, model_status_text


class VoiceInputSettingsPanel(QWidget):
    download_status = Signal(str)
    download_succeeded = Signal(str)
    download_failed = Signal(str)
    download_finished = Signal()

    def __init__(self, context: Any, parent: Any = None) -> None:
        super().__init__(parent)
        self.context = context
        self._download_thread: threading.Thread | None = None
        self._download_cancel_event: threading.Event | None = None
        self._syncing = False
        self._build_ui()
        self._load_to_controls(load_voice_input_config(context))
        self._connect_signals()
        self.destroyed.connect(lambda *_args: self.shutdown())
        self._refresh_model_status()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        form = QFormLayout()
        form.setSpacing(8)

        self.model_combo = QComboBox(self)
        for name, spec in MODEL_SPECS.items():
            self.model_combo.addItem(f"{spec.label} - {spec.description}", name)
        form.addRow("ASR 模型", self.model_combo)

        self.language_combo = QComboBox(self)
        for label, value in (
            ("自动检测", "auto"),
            ("中文", "zh"),
            ("English", "en"),
            ("日本語", "ja"),
            ("한국어", "ko"),
        ):
            self.language_combo.addItem(label, value)
        form.addRow("语言", self.language_combo)

        self.audio_device_combo = QComboBox(self)
        self.audio_device_combo.setEditable(True)
        self._populate_audio_devices()
        form.addRow("麦克风", self.audio_device_combo)

        self.compute_device_combo = QComboBox(self)
        for label, value in (("自动", "auto"), ("CPU", "cpu"), ("CUDA", "cuda")):
            self.compute_device_combo.addItem(label, value)
        form.addRow("识别设备", self.compute_device_combo)

        self.vad_check = QCheckBox("启用静音检测", self)
        form.addRow("VAD", self.vad_check)

        self.max_record_spin = QSpinBox(self)
        self.max_record_spin.setRange(3, 300)
        self.max_record_spin.setSuffix(" 秒")
        form.addRow("最长录音", self.max_record_spin)

        self.silence_timeout_spin = QSpinBox(self)
        self.silence_timeout_spin.setRange(300, 10000)
        self.silence_timeout_spin.setSingleStep(100)
        self.silence_timeout_spin.setSuffix(" ms")
        form.addRow("静音超时", self.silence_timeout_spin)

        self.vad_threshold_spin = QSpinBox(self)
        self.vad_threshold_spin.setRange(50, 10000)
        self.vad_threshold_spin.setSingleStep(50)
        form.addRow("VAD 阈值", self.vad_threshold_spin)

        layout.addLayout(form)

        button_row = QHBoxLayout()
        button_row.setContentsMargins(0, 0, 0, 0)
        self.download_button = QPushButton("下载模型", self)
        self.download_button.clicked.connect(self._start_download)
        button_row.addWidget(self.download_button)
        button_row.addStretch(1)
        layout.addLayout(button_row)

        self.status_label = QLabel("", self)
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

    def _connect_signals(self) -> None:
        self.model_combo.currentIndexChanged.connect(self._save_from_controls)
        self.language_combo.currentIndexChanged.connect(self._save_from_controls)
        self.audio_device_combo.currentTextChanged.connect(self._save_from_controls)
        self.compute_device_combo.currentIndexChanged.connect(self._save_from_controls)
        self.vad_check.toggled.connect(self._save_from_controls)
        self.max_record_spin.valueChanged.connect(self._save_from_controls)
        self.silence_timeout_spin.valueChanged.connect(self._save_from_controls)
        self.vad_threshold_spin.valueChanged.connect(self._save_from_controls)
        self.download_status.connect(self.status_label.setText)
        self.download_succeeded.connect(self._handle_download_succeeded)
        self.download_failed.connect(self._handle_download_failed)
        self.download_finished.connect(self._handle_download_finished)

    def _populate_audio_devices(self) -> None:
        self.audio_device_combo.addItem("默认设备", "")
        try:
            import sounddevice as sd

            devices = sd.query_devices()
        except Exception:
            return
        for index, device in enumerate(devices):
            try:
                if int(device.get("max_input_channels", 0)) <= 0:
                    continue
                name = str(device.get("name", f"设备 {index}"))
            except AttributeError:
                continue
            self.audio_device_combo.addItem(f"{index}: {name}", str(index))

    def _load_to_controls(self, config: VoiceInputConfig) -> None:
        self._syncing = True
        try:
            _set_combo_data(self.model_combo, config.model_name)
            _set_combo_data(self.language_combo, config.language)
            _set_combo_text_or_data(self.audio_device_combo, config.audio_device)
            _set_combo_data(self.compute_device_combo, config.compute_device)
            self.vad_check.setChecked(config.vad_enabled)
            self.max_record_spin.setValue(config.max_record_seconds)
            self.silence_timeout_spin.setValue(config.silence_timeout_ms)
            self.vad_threshold_spin.setValue(config.vad_threshold)
        finally:
            self._syncing = False

    @Slot()
    def _save_from_controls(self, *_args: object) -> None:
        if self._syncing:
            return
        config = self._config_from_controls()
        save_voice_input_config(self.context, config)
        self._refresh_model_status()

    def _config_from_controls(self) -> VoiceInputConfig:
        return VoiceInputConfig(
            model_name=str(self.model_combo.currentData() or "tiny"),
            language=str(self.language_combo.currentData() or "auto"),
            audio_device=_current_audio_device_value(self.audio_device_combo),
            compute_device=str(self.compute_device_combo.currentData() or "auto"),
            vad_enabled=self.vad_check.isChecked(),
            max_record_seconds=self.max_record_spin.value(),
            silence_timeout_ms=self.silence_timeout_spin.value(),
            vad_threshold=self.vad_threshold_spin.value(),
        )

    def _refresh_model_status(self) -> None:
        config = self._config_from_controls()
        self.status_label.setText(model_status_text(self.context.data_dir, config.model_name))

    def _start_download(self) -> None:
        if self._download_thread is not None:
            QMessageBox.information(self, "下载中", "ASR 模型仍在下载，请等待完成。")
            return
        config = self._config_from_controls()
        save_voice_input_config(self.context, config)
        cancel_event = threading.Event()
        self._download_cancel_event = cancel_event
        self._set_download_busy(True)

        def run() -> None:
            try:
                path = download_model(
                    self.context.data_dir,
                    config.model_name,
                    cancel_event=cancel_event,
                    status_callback=self.download_status.emit,
                )
            except Exception as exc:  # noqa: BLE001
                if not cancel_event.is_set():
                    self.download_failed.emit(str(exc))
            else:
                if not cancel_event.is_set():
                    self.download_succeeded.emit(str(path))
            finally:
                self.download_finished.emit()

        thread = threading.Thread(target=run, name="voice-input-model-download", daemon=True)
        self._download_thread = thread
        thread.start()

    @Slot(str)
    def _handle_download_succeeded(self, path: str) -> None:
        self.status_label.setText(f"模型已下载：{path}")
        QMessageBox.information(self, "下载完成", "ASR 模型已下载完成。")

    @Slot(str)
    def _handle_download_failed(self, message: str) -> None:
        self.status_label.setText(f"模型下载失败：{message}")
        QMessageBox.warning(self, "下载失败", message)

    @Slot()
    def _handle_download_finished(self) -> None:
        self._download_thread = None
        self._download_cancel_event = None
        self._set_download_busy(False)
        self._refresh_model_status()

    def _set_download_busy(self, busy: bool) -> None:
        self.download_button.setEnabled(not busy)
        self.model_combo.setEnabled(not busy)

    def shutdown(self) -> None:
        cancel_event = self._download_cancel_event
        if cancel_event is not None:
            cancel_event.set()
        thread = self._download_thread
        if thread is not None and thread.is_alive():
            thread.join(0.2)


def _set_combo_data(combo: QComboBox, value: str) -> None:
    index = combo.findData(value)
    if index >= 0:
        combo.setCurrentIndex(index)


def _set_combo_text_or_data(combo: QComboBox, value: str) -> None:
    index = combo.findData(value)
    if index >= 0:
        combo.setCurrentIndex(index)
        return
    if value:
        combo.setCurrentText(value)


def _current_audio_device_value(combo: QComboBox) -> str:
    data = combo.currentData()
    if data is not None and str(data):
        return str(data)
    text = combo.currentText().strip()
    if text in {"默认设备", "auto", "default"}:
        return ""
    return text
