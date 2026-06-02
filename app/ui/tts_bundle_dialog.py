from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QThread, Signal, Slot
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
)

from app.voice.tts_bundle import (
    TTS_BUNDLES,
    TTSBundleEntry,
    cleanup_stale_download_archives,
    download_and_extract_bundle,
    format_bundle_label,
    format_gpu_summary,
    format_platform_summary,
    list_nvidia_gpus,
    recommend_tts_bundle,
)


class TTSBundleDownloadThread(QThread):
    progress = Signal(int)
    status = Signal(str)
    succeeded = Signal(str)
    failed = Signal(str)

    def __init__(self, entry: TTSBundleEntry, base_dir: Path) -> None:
        super().__init__()
        self.entry = entry
        self.base_dir = base_dir

    def run(self) -> None:
        try:
            work_dir = download_and_extract_bundle(
                self.entry,
                self.base_dir,
                on_progress=self.progress.emit,
                on_status=self.status.emit,
            )
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(str(exc))
            return
        self.succeeded.emit(str(work_dir))


class TTSBundleDownloadDialog(QDialog):
    def __init__(self, base_dir: Path, parent=None) -> None:  # type: ignore[no-untyped-def]
        super().__init__(parent)
        self.base_dir = base_dir
        self.downloaded_work_dir: Path | None = None
        self.downloaded_provider: str | None = None
        self._thread: TTSBundleDownloadThread | None = None
        self.setWindowTitle("下载 TTS 整合包")
        self.setMinimumWidth(520)
        self._cleanup_legacy_archives()

        gpus = list_nvidia_gpus()
        recommended = recommend_tts_bundle(gpus)

        self.platform_label = QLabel(f"当前平台：\n{format_platform_summary()}", self)
        self.platform_label.setWordWrap(True)
        self.gpu_label = QLabel(f"显卡检测：\n{format_gpu_summary(gpus)}", self)
        self.gpu_label.setWordWrap(True)
        self.recommend_label = QLabel(f"推荐下载：{format_bundle_label(recommended)}", self)
        self.recommend_label.setWordWrap(True)

        self.bundle_combo = QComboBox(self)
        for entry in TTS_BUNDLES:
            self.bundle_combo.addItem(format_bundle_label(entry), entry.key)
            if entry.key == recommended.key:
                self.bundle_combo.setCurrentIndex(self.bundle_combo.count() - 1)

        self.status_label = QLabel("", self)
        self.status_label.setVisible(False)
        self.progress_bar = QProgressBar(self)
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setVisible(False)

        self.start_button = QPushButton("开始下载", self)
        self.start_button.clicked.connect(self._start_download)
        self.cancel_button = QPushButton("取消", self)
        self.cancel_button.clicked.connect(self.reject)

        form = QFormLayout()
        form.addRow("整合包", self.bundle_combo)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        buttons.addWidget(self.start_button)
        buttons.addWidget(self.cancel_button)

        layout = QVBoxLayout()
        layout.addWidget(self.platform_label)
        layout.addWidget(self.gpu_label)
        layout.addWidget(self.recommend_label)
        layout.addLayout(form)
        layout.addWidget(self.status_label)
        layout.addWidget(self.progress_bar)
        layout.addLayout(buttons)
        self.setLayout(layout)

    @Slot()
    def _start_download(self) -> None:
        if self._thread is not None:
            return
        entry = self._selected_entry()
        self.downloaded_work_dir = None
        self.downloaded_provider = None
        self.bundle_combo.setEnabled(False)
        self.start_button.setEnabled(False)
        self.cancel_button.setEnabled(False)
        self.status_label.setVisible(True)
        self.progress_bar.setVisible(True)
        self.progress_bar.setValue(0)
        self._handle_status("download")

        thread = TTSBundleDownloadThread(entry, self.base_dir)
        self._thread = thread
        thread.progress.connect(self.progress_bar.setValue)
        thread.status.connect(self._handle_status)
        thread.succeeded.connect(self._handle_success)
        thread.failed.connect(self._handle_failure)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(self._clear_thread)
        thread.start()

    @Slot(str)
    def _handle_status(self, status: str) -> None:
        text = {
            "verify": "正在校验本地压缩包...",
            "download": "正在下载整合包...",
            "extract": "正在解压整合包...",
            "cleanup": "正在清理下载压缩包...",
        }.get(status, status)
        self.status_label.setText(text)

    @Slot(str)
    def _handle_success(self, work_dir: str) -> None:
        self.downloaded_work_dir = Path(work_dir)
        self.downloaded_provider = self._selected_entry().provider
        QMessageBox.information(self, "下载完成", f"TTS 整合包已就绪：\n{work_dir}")
        self.accept()

    @Slot(str)
    def _handle_failure(self, message: str) -> None:
        QMessageBox.warning(self, "下载失败", message)
        self.bundle_combo.setEnabled(True)
        self.start_button.setEnabled(True)
        self.cancel_button.setEnabled(True)
        self._thread = None

    @Slot()
    def _clear_thread(self) -> None:
        self._thread = None

    def _cleanup_legacy_archives(self) -> None:
        try:
            cleanup_stale_download_archives(self.base_dir)
        except RuntimeError as exc:
            QMessageBox.warning(self, "清理旧压缩包失败", str(exc))

    def reject(self) -> None:
        if self._thread is not None and self._thread.isRunning():
            QMessageBox.information(self, "下载中", "整合包下载或解压正在进行，完成后才能关闭。")
            return
        super().reject()

    def _selected_entry(self) -> TTSBundleEntry:
        key = str(self.bundle_combo.currentData() or "")
        for entry in TTS_BUNDLES:
            if entry.key == key:
                return entry
        return TTS_BUNDLES[0]
