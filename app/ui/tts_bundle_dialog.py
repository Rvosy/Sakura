from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QThread, Signal, Slot
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
    TTSBundleDownloadProgress,
)
from app.ui.error_messages import format_failure_message


_BACKGROUND_DOWNLOAD_DIALOGS: set["TTSBundleDownloadDialog"] = set()


def has_active_tts_bundle_download() -> bool:
    return any(dialog.is_download_running() for dialog in tuple(_BACKGROUND_DOWNLOAD_DIALOGS))


def active_tts_bundle_download_dialog() -> "TTSBundleDownloadDialog" | None:
    for dialog in tuple(_BACKGROUND_DOWNLOAD_DIALOGS):
        if dialog.is_download_running() or dialog.isVisible():
            return dialog
    return None


def cancel_active_tts_bundle_downloads_for_shutdown(timeout_ms: int = 3000) -> bool:
    active_dialogs = [dialog for dialog in tuple(_BACKGROUND_DOWNLOAD_DIALOGS) if dialog.is_download_running()]
    if not active_dialogs:
        return True
    timeout_per_dialog = max(1, int(timeout_ms / max(1, len(active_dialogs))))
    return all(dialog.cancel_for_shutdown(timeout_per_dialog) for dialog in active_dialogs)


class TTSBundleDownloadThread(QThread):
    progress = Signal(int)
    download_progress = Signal(object)
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
                on_download_progress=self.download_progress.emit,
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
    succeeded = Signal(object)

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
        self.setWindowModality(Qt.WindowModality.NonModal)
        self.setWindowFlag(Qt.WindowType.WindowTitleHint, True)
        self.setWindowFlag(Qt.WindowType.WindowSystemMenuHint, True)
        self.setWindowFlag(Qt.WindowType.WindowMinimizeButtonHint, True)
        self.setWindowFlag(Qt.WindowType.WindowCloseButtonHint, True)
        self.setMinimumWidth(520)
        _BACKGROUND_DOWNLOAD_DIALOGS.add(self)
        self.finished.connect(lambda _result, dialog=self: _BACKGROUND_DOWNLOAD_DIALOGS.discard(dialog))
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
        self.detail_label = QLabel("", self)
        self.detail_label.setWordWrap(True)
        self.detail_label.setVisible(False)
        self.progress_bar = QProgressBar(self)
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setVisible(False)

        self.start_button = QPushButton("开始下载", self)
        self.start_button.setEnabled(bool(self._entries))
        self.start_button.clicked.connect(self._start_download)
        self.pause_button = QPushButton("暂停下载", self)
        self.pause_button.setVisible(False)
        self.pause_button.clicked.connect(self._cancel_download)
        self.close_button = QPushButton("关闭", self)
        self.close_button.clicked.connect(self._on_close_clicked)

        form = QFormLayout()
        form.addRow("整合包", self.bundle_combo)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        buttons.addWidget(self.start_button)
        buttons.addWidget(self.pause_button)
        buttons.addWidget(self.close_button)

        layout = QVBoxLayout()
        layout.addWidget(self.platform_label)
        layout.addWidget(self.gpu_label)
        layout.addWidget(self.recommend_label)
        layout.addLayout(form)
        layout.addWidget(self.status_label)
        layout.addWidget(self.detail_label)
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
        self.start_button.setText("开始下载")
        self.pause_button.setText("暂停下载")
        self.pause_button.setEnabled(True)
        self.pause_button.setVisible(True)
        self.close_button.setText("隐藏到后台")
        self.close_button.setEnabled(True)
        self.status_label.setVisible(True)
        self.detail_label.setVisible(True)
        self.detail_label.setText("")
        self.progress_bar.setVisible(True)
        self.progress_bar.setValue(0)
        self._handle_status("download")

        thread = TTSBundleDownloadThread(entry, self.base_dir)
        self._thread = thread
        thread.progress.connect(self.progress_bar.setValue)
        thread.download_progress.connect(self._handle_download_progress)
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
    def _handle_download_progress(self, progress: TTSBundleDownloadProgress) -> None:
        self.progress_bar.setValue(progress.percent)
        downloaded = _format_bytes(progress.downloaded_bytes)
        total = _format_bytes(progress.total_bytes)
        speed = _format_speed(progress.bytes_per_second)
        prefix = "正在续传" if progress.resumed else "正在下载"
        self.detail_label.setText(f"{prefix}：{downloaded} / {total}，{speed}")

    @Slot(object)
    def _handle_success(self, result: TTSBundleInstallResult) -> None:
        self.downloaded_work_dir = result.work_dir
        self.downloaded_provider = result.provider
        self.downloaded_python_path = result.python_path
        self.downloaded_tts_config_path = result.tts_config_path
        self.status_label.setVisible(True)
        self.status_label.setText("TTS 整合包已就绪")
        self.detail_label.setVisible(True)
        self.detail_label.setText(str(result.work_dir))
        self.succeeded.emit(result)
        self.accept()

    @Slot(str)
    def _handle_failure(self, message: str) -> None:
        if self.isVisible():
            QMessageBox.warning(
                self,
                "下载失败",
                format_failure_message(
                    "TTS 整合包没有下载或安装成功。",
                    "请检查网络、代理、磁盘空间和目录权限后重试，已下载部分会尽量保留供下次继续。",
                    message,
                ),
            )
        self.status_label.setVisible(True)
        self.status_label.setText(f"下载失败：{message}")
        self.bundle_combo.setEnabled(bool(self._entries))
        self.start_button.setEnabled(bool(self._entries))
        self.start_button.setText("继续下载")
        self.pause_button.setVisible(False)
        self.close_button.setText("关闭")
        self.close_button.setEnabled(True)
        self._thread = None

    @Slot()
    def _clear_thread(self) -> None:
        self._thread = None

    @Slot()
    def _handle_cancelled(self) -> None:
        """下载被用户取消后，恢复界面状态。"""
        self.bundle_combo.setEnabled(bool(self._entries))
        self.start_button.setEnabled(bool(self._entries))
        self.start_button.setText("继续下载")
        self.pause_button.setVisible(False)
        self.close_button.setText("关闭")
        self.close_button.setEnabled(True)
        self.status_label.setText("下载已暂停")
        self.detail_label.setVisible(True)
        self.detail_label.setText("已下载部分会保留，下次可继续。")
        self._thread = None

    def _on_close_clicked(self) -> None:
        """下载中隐藏到后台，空闲时关闭窗口。"""
        if self.is_download_running():
            self.hide()
        else:
            self.reject()

    def _cancel_download(self) -> None:
        """请求暂停下载线程，保留 .part 供下次续传。"""
        thread = self._thread
        if thread is not None:
            self.status_label.setText("正在暂停下载...")
            self.detail_label.setVisible(True)
            self.detail_label.setText("已下载部分会保留，下次可继续。")
            self.pause_button.setEnabled(False)
            thread.cancel()

    def is_download_running(self) -> bool:
        thread = self._thread
        return bool(thread is not None and thread.isRunning())

    def cancel_for_shutdown(self, timeout_ms: int = 3000) -> bool:
        """应用退出时请求暂停下载，返回线程是否已停止。"""
        thread = self._thread
        if thread is None or not thread.isRunning():
            return True
        self._cancel_download()
        return bool(thread.wait(timeout_ms))

    def _cleanup_legacy_archives(self) -> None:
        try:
            cleanup_stale_download_archives(self.base_dir)
        except RuntimeError as exc:
            QMessageBox.warning(
                self,
                "清理旧压缩包失败",
                format_failure_message(
                    "旧的 TTS 下载压缩包没有清理成功。",
                    "请关闭占用该文件的程序，检查目录权限后重新打开下载窗口。",
                    exc,
                ),
            )

    def reject(self) -> None:
        if self.is_download_running():
            self.hide()
            return
        super().reject()

    def closeEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        if self.is_download_running():
            event.ignore()
            self.hide()
            return
        super().closeEvent(event)

    def _selected_entry(self) -> TTSBundleEntry:
        key = str(self.bundle_combo.currentData() or "")
        for entry in self._entries:
            if entry.key == key:
                return entry
        raise RuntimeError("当前平台没有可一键下载的 TTS 整合包。")


def _format_bytes(value: int) -> str:
    size = float(max(0, value))
    units = ("B", "KB", "MB", "GB")
    for unit in units:
        if size < 1024 or unit == units[-1]:
            return f"{size:.1f} {unit}" if unit != "B" else f"{int(size)} B"
        size /= 1024
    return f"{size:.1f} GB"


def _format_speed(bytes_per_second: float) -> str:
    if bytes_per_second <= 0:
        return "正在计算速度"
    return f"{_format_bytes(int(bytes_per_second))}/s"
