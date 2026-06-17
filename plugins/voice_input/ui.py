from __future__ import annotations

from typing import Any

from PySide6.QtCore import Signal, Slot
from PySide6.QtWidgets import QMessageBox, QToolButton

from .controller import VoiceInputController, VoiceInputControllerCallbacks


class VoiceInputButton(QToolButton):
    recording_started = Signal()
    recognizing_started = Signal()
    finished = Signal(str)
    failed = Signal(str)
    status_changed = Signal(str)

    def __init__(self, context: Any, parent: Any = None) -> None:
        super().__init__(parent)
        self.setObjectName("voiceInputButton")
        self.setFixedSize(38, 38)
        self.setText("麦")
        self.setToolTip("语音输入")
        self._controller = VoiceInputController(
            context,
            callbacks=VoiceInputControllerCallbacks(
                recording_started=self.recording_started.emit,
                recognizing_started=self.recognizing_started.emit,
                finished=self.finished.emit,
                error=self.failed.emit,
                status=self.status_changed.emit,
            ),
        )
        self.clicked.connect(self._handle_clicked)
        self.recording_started.connect(self._show_recording)
        self.recognizing_started.connect(self._show_recognizing)
        self.finished.connect(self._show_finished)
        self.failed.connect(self._show_error)
        self.status_changed.connect(self._update_tooltip)

    @Slot()
    def _handle_clicked(self) -> None:
        self._controller.toggle_recording()

    @Slot()
    def _show_recording(self) -> None:
        self.setEnabled(True)
        self.setText("停")
        self.setToolTip("正在录音，再次点击停止")

    @Slot()
    def _show_recognizing(self) -> None:
        self.setEnabled(False)
        self.setText("识")
        self.setToolTip("正在识别语音")

    @Slot(str)
    def _show_finished(self, _text: str) -> None:
        self._show_idle("识别完成，文本已填入输入框")

    @Slot(str)
    def _show_error(self, message: str) -> None:
        self._show_idle(message)
        QMessageBox.warning(self, "Voice Input", message)

    @Slot(str)
    def _update_tooltip(self, message: str) -> None:
        if message:
            self.setToolTip(message)

    def _show_idle(self, tooltip: str = "语音输入") -> None:
        self.setEnabled(True)
        self.setText("麦")
        self.setToolTip(tooltip)

    def shutdown(self) -> None:
        self._controller.shutdown()

    def closeEvent(self, event: Any) -> None:  # type: ignore[override]
        self.shutdown()
        super().closeEvent(event)
