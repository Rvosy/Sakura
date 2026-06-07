from __future__ import annotations

import sys
import ctypes
from dataclasses import replace
from pathlib import Path

from PySide6.QtCore import QObject, QThread, QTimer, Qt, Signal, Slot
from PySide6.QtGui import QGuiApplication, QPalette, QColor
from PySide6.QtWidgets import QApplication, QDialog, QLabel, QMessageBox, QProgressBar, QPushButton, QVBoxLayout, QStyleFactory

from app.core.app_context import AppContext
from app.core.bootstrap import build_deferred_services, build_initial_app_context
from app.core.debug_log import debug_log
from app.config.character_loader import CharacterConfigError
from app.config.settings_service import AppSettingsService
from app.agent.mcp import MCPRuntimeSettings
from app.agent.proactive_care import ProactiveCareSettings
from app.ui.pet_window import PetWindow
from app.ui.settings_dialog import SettingsDialog
from app.ui.portrait_controller import PORTRAIT_SCALE_DEFAULT_PERCENT
from app.ui.subtitle_controller import (
    REPLY_SEGMENT_PAUSE_MS,
    SPEECH_TYPING_INTERVAL_MS,
    normalize_subtitle_display_speed,
)
from app.voice.tts import TTSConfigError
from app.voice.tts_bundle import (
    TTSBundleMigration,
    TTSBundleMigrationProgress,
    find_pending_bundle_migrations,
    migrate_bundle_to_short_path,
    normalize_bundle_work_dir,
)


BASE_DIR = Path(__file__).resolve().parent


def _force_light_palette(app: QApplication) -> None:
    """强制使用 Fusion 风格 + 亮色 palette，避免 Windows 暗色模式下系统控件文字与浅色背景冲突。"""
    app.setStyle(QStyleFactory.create("Fusion"))
    palette = QPalette()
    palette.setColor(QPalette.ColorRole.Window, QColor("#fff6fa"))
    palette.setColor(QPalette.ColorRole.WindowText, QColor("#3d2b35"))
    palette.setColor(QPalette.ColorRole.Base, QColor("#ffffff"))
    palette.setColor(QPalette.ColorRole.AlternateBase, QColor("#fff6fa"))
    palette.setColor(QPalette.ColorRole.Text, QColor("#3d2b35"))
    palette.setColor(QPalette.ColorRole.ButtonText, QColor("#3d2b35"))
    palette.setColor(QPalette.ColorRole.Button, QColor("#ffe8f1"))
    palette.setColor(QPalette.ColorRole.ToolTipBase, QColor("#fff6fa"))
    palette.setColor(QPalette.ColorRole.ToolTipText, QColor("#3d2b35"))
    palette.setColor(QPalette.ColorRole.PlaceholderText, QColor("#9b4f72"))
    app.setPalette(palette)


def _configure_windows_high_dpi() -> None:
    """在 QApplication 创建前配置 Windows 混合 DPI 行为。"""

    if sys.platform != "win32":
        return

    awareness = _set_windows_process_dpi_awareness()
    try:
        QGuiApplication.setHighDpiScaleFactorRoundingPolicy(
            Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
        )
    except Exception as exc:  # noqa: BLE001
        debug_log("Startup", "配置 Qt HighDPI 舍入策略失败", {"error": str(exc)})
    debug_log("Startup", "Windows HighDPI 配置完成", {"awareness": awareness})


def _set_windows_process_dpi_awareness() -> str:
    """优先启用 Per-Monitor V2，失败时降级到旧版 DPI 感知模式。"""

    errors: list[str] = []
    try:
        set_context = ctypes.windll.user32.SetProcessDpiAwarenessContext
        set_context.argtypes = [ctypes.c_void_p]
        set_context.restype = ctypes.c_bool
        if set_context(ctypes.c_void_p(-4)):
            return "per_monitor_v2"
        errors.append("SetProcessDpiAwarenessContext")
    except Exception as exc:  # noqa: BLE001
        errors.append(f"SetProcessDpiAwarenessContext: {exc}")

    try:
        set_awareness = ctypes.windll.shcore.SetProcessDpiAwareness
        set_awareness.argtypes = [ctypes.c_int]
        set_awareness.restype = ctypes.c_long
        if set_awareness(2) == 0:
            return "per_monitor"
        errors.append("SetProcessDpiAwareness")
    except Exception as exc:  # noqa: BLE001
        errors.append(f"SetProcessDpiAwareness: {exc}")

    try:
        set_system_aware = ctypes.windll.user32.SetProcessDPIAware
        set_system_aware.restype = ctypes.c_bool
        if set_system_aware():
            return "system"
        errors.append("SetProcessDPIAware")
    except Exception as exc:  # noqa: BLE001
        errors.append(f"SetProcessDPIAware: {exc}")

    debug_log("Startup", "Windows DPI 感知配置未生效", {"errors": errors})
    return "unchanged"


class DeferredStartupWorker(QObject):
    finished = Signal(object)
    failed = Signal(str)

    def __init__(self, base_dir: Path, context: AppContext) -> None:
        super().__init__()
        self.base_dir = base_dir
        self.context = context

    @Slot()
    def run(self) -> None:
        try:
            services = build_deferred_services(self.base_dir, self.context)
            self._move_service_objects_to_ui_thread(services)
            self.finished.emit(services)
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(str(exc))

    def _move_service_objects_to_ui_thread(self, services: object) -> None:
        application = QApplication.instance()
        if application is None:
            return
        tts_provider = getattr(services, "tts_provider", None)
        if isinstance(tts_provider, QObject):
            tts_provider.moveToThread(application.thread())


class TTSBundleMigrationWorker(QObject):
    current_item = Signal(str)
    progress = Signal(object)
    finished = Signal(object)

    def __init__(self, migrations: list[TTSBundleMigration]) -> None:
        super().__init__()
        self.migrations = migrations

    @Slot()
    def run(self) -> None:
        errors: list[str] = []
        for migration in self.migrations:
            self.current_item.emit(f"正在迁移：{migration.entry.label}")
            try:
                migrate_bundle_to_short_path(migration, on_progress=self.progress.emit)
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{migration.entry.label}：{exc}")
        self.finished.emit(errors)


class TTSBundleMigrationDialog(QDialog):
    """启动阶段 TTS 整合包迁移进度窗口。"""

    def __init__(self, base_dir: Path, parent: PetWindow) -> None:
        super().__init__(parent)
        self.base_dir = base_dir
        self.pet_window = parent
        self._finish_pending = False
        self._finish_errors: list[str] = []
        self.setWindowTitle("正在迁移 TTS 整合包")
        self.setModal(True)
        self.setMinimumWidth(520)

        description = QLabel(
            "新版本修复了 Windows 下可能出现的路径过长问题。\n\n"
            "现在需要迁移旧版本的 TTS 数据，Sakura 正在努力搬运中，"
            "可能需要一些时间，请耐心等待喵 ฅ•ω•ฅ",
            self,
        )
        description.setWordWrap(True)
        self.current_label = QLabel("正在准备迁移...", self)
        self.current_label.setWordWrap(True)
        self.progress_label = QLabel("0%（0/0 个文件）", self)
        self.progress_bar = QProgressBar(self)
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.confirm_button = QPushButton("迁移中...", self)
        self.confirm_button.setEnabled(False)
        self.confirm_button.clicked.connect(self._confirm_migration_finished)

        layout = QVBoxLayout()
        layout.addWidget(description)
        layout.addWidget(self.current_label)
        layout.addWidget(self.progress_label)
        layout.addWidget(self.progress_bar)
        layout.addWidget(self.confirm_button)
        self.setLayout(layout)

    @Slot(str)
    def set_current_item(self, text: str) -> None:
        self.current_label.setText(text)

    @Slot(object)
    def set_progress(self, progress: TTSBundleMigrationProgress) -> None:
        total_files = max(0, int(progress.total_files))
        completed_files = max(0, int(progress.completed_files))
        percent = int(completed_files * 100 / total_files) if total_files else 0
        self.progress_bar.setValue(max(0, min(100, percent)))
        self.progress_label.setText(f"{percent}%（{completed_files}/{total_files} 个文件）")

    @Slot(object)
    def finish_migration(self, errors: list[str]) -> None:
        if self._finish_pending:
            return
        self._finish_pending = True
        self._finish_errors = list(errors)
        if errors:
            self.current_label.setText("迁移失败，点击继续启动。")
            self.confirm_button.setText("继续启动")
        else:
            self.current_label.setText("迁移完成，点击确定继续启动。")
            self.progress_bar.setValue(100)
            if self.progress_label.text().startswith("0%"):
                self.progress_label.setText("100%（迁移完成）")
            self.confirm_button.setText("确定")
        self.confirm_button.setEnabled(True)
        self.confirm_button.setDefault(True)
        self.confirm_button.setFocus()

    def closeEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        event.ignore()

    @Slot()
    def _confirm_migration_finished(self) -> None:
        if not self._finish_pending:
            return
        _finish_tts_migration(self.base_dir, self.pet_window, self, self._finish_errors)


def main() -> int:
    _configure_windows_high_dpi()
    app = QApplication(sys.argv)
    app.setApplicationName("Sakura Desktop Pet")
    app.setQuitOnLastWindowClosed(False)
    _force_light_palette(app)

    try:
        context = build_initial_app_context(BASE_DIR)
    except CharacterConfigError as exc:
        if not _character_packages_missing(BASE_DIR):
            print(f"[Character] 配置无效：{exc}")
            return 1
        try:
            context = _open_first_run_settings(BASE_DIR)
        except (CharacterConfigError, OSError, TTSConfigError, ValueError) as first_run_exc:
            QMessageBox.critical(None, "启动失败", str(first_run_exc))
            print(f"[Character] 配置无效：{first_run_exc}")
            return 1
        if context is None:
            return 0
    except (OSError, ValueError) as exc:
        print(f"[Character] 配置无效：{exc}")
        return 1

    pet_window = PetWindow(context)
    app.aboutToQuit.connect(pet_window.close_external_tools)
    pet_window.show()
    QTimer.singleShot(0, lambda: _start_tts_migration_or_deferred(BASE_DIR, pet_window))

    return app.exec()


def _character_packages_missing(base_dir: Path) -> bool:
    characters_dir = base_dir / "characters"
    if not characters_dir.is_dir():
        return True
    try:
        return not any(characters_dir.glob("*/character.json"))
    except OSError:
        return False


def _open_first_run_settings(base_dir: Path) -> AppContext | None:
    settings_service = AppSettingsService(base_dir=base_dir)
    api_settings = settings_service.load_api_settings()
    tts_settings = settings_service.load_tts_settings(
        validate_enabled=False,
        character_profile=None,
    )
    dialog = SettingsDialog(
        api_settings=api_settings,
        tts_settings=tts_settings,
        base_dir=base_dir,
        character_registry=None,
        current_character=None,
        proactive_care_settings=settings_service.load_proactive_care_settings(),
        mcp_settings=settings_service.load_mcp_runtime_settings(),
        debug_log_settings=settings_service.load_debug_log_settings(),
        portrait_scale_percent=PORTRAIT_SCALE_DEFAULT_PERCENT,
        subtitle_typing_interval_ms=SPEECH_TYPING_INTERVAL_MS,
        reply_segment_pause_ms=REPLY_SEGMENT_PAUSE_MS,
        theme_settings=settings_service.load_theme_settings(),
    )
    if dialog.exec() != QDialog.DialogCode.Accepted:
        return None
    (
        subtitle_typing_interval_ms,
        reply_segment_pause_ms,
    ) = normalize_subtitle_display_speed(
        getattr(dialog, "result_subtitle_typing_interval_ms", SPEECH_TYPING_INTERVAL_MS),
        getattr(dialog, "result_reply_segment_pause_ms", REPLY_SEGMENT_PAUSE_MS),
    )
    result_theme_settings = getattr(
        dialog,
        "result_theme_settings",
        settings_service.load_theme_settings(),
    )
    if (
        dialog.result_api_settings is None
        or dialog.result_tts_settings is None
        or dialog.result_character_id is None
        or dialog.result_proactive_care_settings is None
        or dialog.result_mcp_settings is None
        or dialog.result_debug_log_settings is None
        or dialog.result_portrait_scale_percent is None
        or result_theme_settings is None
        or dialog.character_registry is None
    ):
        QMessageBox.warning(None, "配置无效", "请先导入并选择一个角色包。")
        return None

    settings_service.save_api_settings(dialog.result_api_settings)
    settings_service.save_tts_settings(dialog.result_tts_settings)
    settings_service.save_current_character_id(
        dialog.character_registry,
        dialog.result_character_id,
    )
    settings_service.save_proactive_care_settings(
        dialog.result_proactive_care_settings or ProactiveCareSettings()
    )
    settings_service.save_mcp_runtime_settings(dialog.result_mcp_settings or MCPRuntimeSettings())
    settings_service.save_debug_log_settings(dialog.result_debug_log_settings)
    settings_service.save_theme_settings(result_theme_settings)
    settings_service.save_system_values(
        "ui",
        {
            "portrait_scale_percent": int(dialog.result_portrait_scale_percent),
            "subtitle_typing_interval_ms": subtitle_typing_interval_ms,
            "reply_segment_pause_ms": reply_segment_pause_ms,
        },
    )
    return build_initial_app_context(base_dir)


def _start_tts_migration_or_deferred(base_dir: Path, pet_window: PetWindow) -> None:
    migrations = _pending_startup_tts_migrations(base_dir)
    if not migrations:
        _start_deferred_startup(base_dir, pet_window)
        return

    dialog = TTSBundleMigrationDialog(base_dir, pet_window)
    thread = QThread(pet_window)
    worker = TTSBundleMigrationWorker(migrations)
    worker.moveToThread(thread)
    pet_window.tts_migration_dialog = dialog
    pet_window.tts_migration_thread = thread
    pet_window.tts_migration_worker = worker

    thread.started.connect(worker.run)
    worker.current_item.connect(dialog.set_current_item)
    worker.progress.connect(dialog.set_progress)
    worker.finished.connect(dialog.finish_migration)
    worker.finished.connect(thread.quit)
    thread.finished.connect(worker.deleteLater)
    thread.finished.connect(thread.deleteLater)
    thread.finished.connect(lambda: setattr(pet_window, "tts_migration_thread", None))
    thread.finished.connect(lambda: setattr(pet_window, "tts_migration_worker", None))

    dialog.show()
    thread.start()


def _pending_startup_tts_migrations(base_dir: Path) -> list[TTSBundleMigration]:
    settings_service = AppSettingsService(base_dir=base_dir)
    settings = settings_service.load_tts_settings(validate_enabled=False)
    provider_migrations = find_pending_bundle_migrations(base_dir, settings.provider)
    all_migrations = find_pending_bundle_migrations(base_dir)
    migrations = _dedupe_tts_migrations([*provider_migrations, *all_migrations])
    debug_log(
        "TTS",
        "启动检测 TTS 整合包迁移",
        {
            "provider": settings.provider,
            "enabled": settings.enabled,
            "pending": [migration.entry.key for migration in migrations],
        },
    )
    return migrations


def _dedupe_tts_migrations(migrations: list[TTSBundleMigration]) -> list[TTSBundleMigration]:
    deduped: list[TTSBundleMigration] = []
    seen: set[str] = set()
    for migration in migrations:
        if migration.entry.key in seen:
            continue
        seen.add(migration.entry.key)
        deduped.append(migration)
    return deduped


def _finish_tts_migration(base_dir: Path, pet_window: PetWindow, dialog: QDialog, errors: list[str]) -> None:
    dialog.accept()
    setattr(pet_window, "tts_migration_dialog", None)
    _normalize_migrated_tts_config(base_dir)
    if errors:
        QMessageBox.warning(
            pet_window,
            "TTS 整合包迁移失败",
            "迁移失败，Sakura 会继续使用旧目录启动。旧模型文件不会被删除，"
            "下次启动会继续迁移。\n\n"
            + "\n".join(errors),
        )
    _start_deferred_startup(base_dir, pet_window)


def _normalize_migrated_tts_config(base_dir: Path) -> None:
    settings_service = AppSettingsService(base_dir=base_dir)
    settings = settings_service.load_tts_settings(validate_enabled=False)
    normalized_work_dir = normalize_bundle_work_dir(settings.work_dir, base_dir)
    if normalized_work_dir == settings.work_dir:
        return
    settings_service.save_tts_settings(replace(settings, work_dir=normalized_work_dir))


def _start_deferred_startup(base_dir: Path, pet_window: PetWindow) -> None:
    thread = QThread(pet_window)
    worker = DeferredStartupWorker(base_dir, pet_window.context)
    worker.moveToThread(thread)
    pet_window.deferred_startup_thread = thread
    pet_window.deferred_startup_worker = worker
    thread.started.connect(worker.run)
    worker.finished.connect(pet_window.apply_deferred_services)
    worker.failed.connect(pet_window.handle_deferred_startup_failed)
    worker.finished.connect(thread.quit)
    worker.failed.connect(thread.quit)
    thread.finished.connect(worker.deleteLater)
    thread.finished.connect(thread.deleteLater)
    thread.finished.connect(lambda: setattr(pet_window, "deferred_startup_thread", None))
    thread.finished.connect(lambda: setattr(pet_window, "deferred_startup_worker", None))
    thread.start()

if __name__ == "__main__":
    raise SystemExit(main())
