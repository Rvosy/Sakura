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
    TTSBundleEntry,
    TTSBundleInstallResult,
    cleanup_stale_download_archives,
    compatible_tts_bundles,
    DownloadCancelledError,
    format_bundle_label,
    format_gpu_summary,
    format_platform_summary,
    install_tts_bundle,
    list_nvidia_gpus,
    recommend_tts_bundle,
)


class TTSBundleDownloadThread(QThread):
    progress = Signal(int)
    status = Signal(str)
    succeeded = Signal(object)
    failed = Signal(str)
    cancelled = Signal()

    def __init__(self, entry: TTSBundleEntry, base_dir: Path) -> None:
        super().__init__()
        self.entry = entry
        self.base_dir = base_dir
        self._cancel_flag = False

    def cancel(self) -> None:
        """请求取消正在进行的下载。"""
        self._cancel_flag = True

    def run(self) -> None:
        def _check_cancel() -> None:
            if self._cancel_flag:
                raise DownloadCancelledError("用户取消了下载")
        try:
            result = install_tts_bundle(
                self.entry,
                self.base_dir,
                check_cancel=_check_cancel,
                on_progress=self.progress.emit,
                on_status=self.status.emit,
            )
        except DownloadCancelledError:
            self.cancelled.emit()
            return
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(str(exc))
            return
        self.succeeded.emit(result)


class TTSBundleDownloadDialog(QDialog):
    def __init__(self, base_dir: Path, parent=None) -> None:  # type: ignore[no-untyped-def]
        super().__init__(parent)
        self.base_dir = base_dir
        self.downloaded_work_dir: Path | None = None
        self.downloaded_provider: str | None = None
        self.downloaded_python_path: Path | None = None
        self.downloaded_tts_config_path: Path | None = None
        self._thread: TTSBundleDownloadThread | None = None
        self._entries = compatible_tts_bundles()
        self.setWindowTitle("下载 TTS 整合包")
        self.setMinimumWidth(520)
        self._cleanup_legacy_archives()

        gpus = list_nvidia_gpus()
        recommended = recommend_tts_bundle(gpus)

        self.platform_label = QLabel(f"当前平台：\n{format_platform_summary()}", self)
        self.platform_label.setWordWrap(True)
        self.gpu_label = QLabel(f"显卡检测：\n{format_gpu_summary(gpus)}", self)
        self.gpu_label.setWordWrap(True)
        recommend_text = (
            f"推荐下载：{format_bundle_label(recommended)}"
            if recommended is not None
            else "推荐下载：当前平台暂无可一键下载的整合包，请在 TTS 提供器中选择“自定义 GPT-SoVITS（macOS/Linux）”。"
        )
        self.recommend_label = QLabel(recommend_text, self)
        self.recommend_label.setWordWrap(True)

        self.bundle_combo = QComboBox(self)
        for entry in self._entries:
            self.bundle_combo.addItem(format_bundle_label(entry), entry.key)
            if recommended is not None and entry.key == recommended.key:
                self.bundle_combo.setCurrentIndex(self.bundle_combo.count() - 1)
        if not self._entries:
            self.bundle_combo.addItem("当前平台暂无可用整合包", "")
            self.bundle_combo.setEnabled(False)

        self.status_label = QLabel("", self)
        self.status_label.setVisible(False)
        self.progress_bar = QProgressBar(self)
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setVisible(False)

        self.start_button = QPushButton("开始下载", self)
        self.start_button.setEnabled(bool(self._entries))
        self.start_button.clicked.connect(self._start_download)
        self.cancel_button = QPushButton("关闭", self)
        self.cancel_button.clicked.connect(self._on_cancel_clicked)

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
        try:
            entry = self._selected_entry()
        except RuntimeError as exc:
            QMessageBox.warning(self, "暂无可用整合包", str(exc))
            return
        self.downloaded_work_dir = None
        self.downloaded_provider = None
        self.downloaded_python_path = None
        self.downloaded_tts_config_path = None
        self.bundle_combo.setEnabled(False)
        self.start_button.setEnabled(False)
        self.cancel_button.setText("取消下载")
        self.cancel_button.setEnabled(True)
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
        thread.cancelled.connect(self._handle_cancelled)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(self._clear_thread)
        thread.start()

    @Slot(str)
    def _handle_status(self, status: str) -> None:
        text = {
            "verify": "正在校验本地压缩包...",
            "download": "正在下载整合包...",
            "extract": "正在解压整合包...",
            "prepare": "正在准备安装环境...",
            "install": "正在安装 TTS 运行环境...",
            "configure": "正在生成 TTS 配置...",
            "cleanup": "正在清理下载压缩包...",
        }.get(status, status)
        self.status_label.setText(text)

    @Slot(object)
    def _handle_success(self, result: TTSBundleInstallResult) -> None:
        self.downloaded_work_dir = result.work_dir
        self.downloaded_provider = result.provider
        self.downloaded_python_path = result.python_path
        self.downloaded_tts_config_path = result.tts_config_path
        QMessageBox.information(self, "下载完成", f"TTS 整合包已就绪：\n{result.work_dir}")
        self.accept()

    @Slot(str)
    def _handle_failure(self, message: str) -> None:
        QMessageBox.warning(self, "下载失败", message)
        self.bundle_combo.setEnabled(bool(self._entries))
        self.start_button.setEnabled(bool(self._entries))
        self.cancel_button.setText("关闭")
        self.cancel_button.setEnabled(True)
        self._thread = None

    @Slot()
    def _clear_thread(self) -> None:
        self._thread = None

    @Slot()
    def _handle_cancelled(self) -> None:
        """下载被用户取消后，恢复界面状态。"""
        self.bundle_combo.setEnabled(bool(self._entries))
        self.start_button.setEnabled(bool(self._entries))
        self.cancel_button.setText("关闭")
        self.cancel_button.setEnabled(True)
        self.status_label.setText("下载已取消")
        self._thread = None

    def _on_cancel_clicked(self) -> None:
        """取消按钮点击：下载中则取消下载，否则关闭对话框。"""
        if self._thread is not None and self._thread.isRunning():
            self._cancel_download()
        else:
            self.reject()

    def _cancel_download(self) -> None:
        """请求取消下载线程。"""
        thread = self._thread
        if thread is not None:
            self.status_label.setText("正在取消下载...")
            self.cancel_button.setEnabled(False)
            thread.cancel()

    def _cleanup_legacy_archives(self) -> None:
        try:
            cleanup_stale_download_archives(self.base_dir)
        except RuntimeError as exc:
            QMessageBox.warning(self, "清理旧压缩包失败", str(exc))

    def reject(self) -> None:
        if self._thread is not None and self._thread.isRunning():
            reply = QMessageBox.question(
                self,
                "取消下载",
                "下载正在进行中，确定要取消下载吗？\n已下载的进度将会丢失。",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if reply == QMessageBox.Yes:
                self._cancel_download()
            return
        super().reject()

    def _selected_entry(self) -> TTSBundleEntry:
        key = str(self.bundle_combo.currentData() or "")
        for entry in self._entries:
            if entry.key == key:
                return entry
        raise RuntimeError("当前平台没有可一键下载的 TTS 整合包。")
