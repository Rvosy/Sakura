from __future__ import annotations

import os
import sys
import ctypes
import faulthandler
import traceback
from datetime import datetime
from dataclasses import replace
from pathlib import Path

from PySide6.QtCore import (
    QEventLoop,
    QObject,
    QTimer,
    Qt,
    Signal,
    Slot,
    QtMsgType,
    qInstallMessageHandler,
)
from PySide6.QtGui import QGuiApplication, QPalette, QColor
from PySide6.QtWidgets import QApplication, QDialog, QLabel, QMessageBox, QProgressBar, QPushButton, QVBoxLayout, QStyleFactory

from app.config.app_version import record_app_version
from app.config.default_configs import ensure_default_configs
from app.config.migration_runner import MigrationReport, MigrationRunner
from app.core.app_context import AppContext
from app.core.bootstrap import build_deferred_services, build_initial_app_context
from app.core.cancellation import CancellationToken, OperationCancelled
from app.core.runtime_log import log_event
from app.core.instance import SingleInstanceGuard
from app.core.selfcheck import run_startup_self_check
from app.storage.paths import StoragePaths
from app.config.character_loader import CharacterConfigError, CharacterRegistry
from app.config.model_slots import resolve_model_slot
from app.config.models import MODEL_SLOT_CHAT
from app.config.settings_service import AppSettingsService, StartupSettings
from app.platforms.launch_at_login import (
    LaunchAtLoginError,
    ensure_launch_at_login_state,
    is_launch_at_login_supported,
    set_launch_at_login_enabled,
)
from app.ui.pet_window import PetWindow
from app.ui.control_panel_layout import (
    DEFAULT_BUBBLE_HEIGHT,
    DEFAULT_CONTROL_PANEL_VERTICAL_OFFSET,
    DEFAULT_CONTROL_PANEL_WIDTH,
    DEFAULT_INPUT_BAR_OFFSET,
    normalize_bubble_height,
    normalize_control_panel_vertical_offset,
    normalize_control_panel_width,
    normalize_input_bar_offset,
)
from app.ui.error_messages import format_failure_message
from app.ui.tauri_settings import (
    TauriSettingsProcess,
    TauriSettingsResult,
    resolve_tauri_settings_binary,
    tts_settings_from_tauri_result,
)
from app.ui.tauri_studio import TauriStudioProcess, resolve_tauri_studio_binary
from app.ui.portrait_controller import (
    PORTRAIT_SCALE_DEFAULT_PERCENT,
    normalize_portrait_scale_percent,
)
from app.ui.subtitle_controller import (
    REPLY_SEGMENT_PAUSE_MS,
    SPEECH_TYPING_INTERVAL_MS,
    normalize_subtitle_display_speed,
)
from app.voice.tts_settings import TTSConfigError
from app.voice.tts_bundle import (
    TTSBundleMigration,
    TTSBundleMigrationProgress,
    find_pending_bundle_migrations,
    migrate_bundle_to_short_path,
    normalize_bundle_work_dir,
)


BASE_DIR = Path(__file__).resolve().parent

# 保活 faulthandler 的写入句柄,避免被 GC 关闭后崩溃时写向失效 fd。
_CRASH_LOG_HANDLE = None


def _enable_crash_diagnostics(base_dir: Path) -> None:
    """启用原生崩溃与未捕获异常的留痕（失败不阻断启动）。

    - faulthandler：段错误时把**所有线程**的原生栈写入 data/logs/sakura-crash.log。
      原生崩溃（如 TTS provider 与后台预热线程并发拆解服务进程）不会进 runtime
      日志，这是定位「保存设置闪退」一类问题的唯一手段。
    - sys.excepthook：未捕获的 Python 异常同时落 crash 日志与 runtime 日志，
      避免在 PySide6 槽函数里被静默吞掉。
    """
    global _CRASH_LOG_HANDLE
    try:
        crash_log_path = StoragePaths(base_dir).crash_log_file()
        crash_log_path.parent.mkdir(parents=True, exist_ok=True)
        handle = crash_log_path.open("a", encoding="utf-8", buffering=1)
        handle.write(
            f"\n===== Sakura 启动 "
            f"{datetime.now().astimezone().isoformat(timespec='seconds')} =====\n"
        )
        handle.flush()
        _CRASH_LOG_HANDLE = handle
        faulthandler.enable(file=handle, all_threads=True)
    except Exception as exc:  # noqa: BLE001
        log_event("Startup", "启用 faulthandler 失败", {"error": str(exc)})

    previous_hook = sys.excepthook

    def _log_uncaught(exc_type, exc_value, exc_tb):  # type: ignore[no-untyped-def]
        if issubclass(exc_type, KeyboardInterrupt):
            previous_hook(exc_type, exc_value, exc_tb)
            return
        text = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
        log_event("Crash", "未捕获异常", {"error": text})
        handle = _CRASH_LOG_HANDLE
        if handle is not None:
            try:
                handle.write(
                    f"\n[{datetime.now().astimezone().isoformat(timespec='seconds')}] "
                    f"未捕获异常\n{text}\n"
                )
                handle.flush()
            except Exception:  # noqa: BLE001
                pass
        previous_hook(exc_type, exc_value, exc_tb)

    sys.excepthook = _log_uncaught


def _qt_message_handler(msg_type: QtMsgType, context: object, msg: str) -> None:
    # Windows 无边框透明窗口触发的无害 DWM 边框设置警告，直接丢弃
    if "setDarkBorderToWindow" in msg:
        return
    sys.stderr.write(f"{msg}\n")
    if msg_type == QtMsgType.QtFatalMsg:
        sys.exit(1)


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
        log_event("Startup", "配置 Qt HighDPI 舍入策略失败", {"error": str(exc)})
    log_event("Startup", "Windows HighDPI 配置完成", {"awareness": awareness})


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

    log_event("Startup", "Windows DPI 感知配置未生效", {"errors": errors})
    return "unchanged"


class DeferredStartupWorker(QObject):
    finished = Signal(object)
    failed = Signal(str)
    cancelled = Signal()

    def __init__(self, base_dir: Path, context: AppContext) -> None:
        super().__init__()
        self.base_dir = base_dir
        self.context = context
        self._cancel_token = CancellationToken()

    @Slot()
    def cancel(self) -> None:
        self._cancel_token.cancel()

    @Slot()
    def run(self) -> None:
        services: object | None = None
        try:
            self._cancel_token.throw_if_cancelled()
            services = build_deferred_services(
                self.base_dir,
                self.context,
                cancel_checker=self._cancel_token.throw_if_cancelled,
            )
            if self._cancel_token.is_cancelled():
                self._close_services(services)
                self.cancelled.emit()
                return
            self._move_service_objects_to_ui_thread(services)
            self._cancel_token.throw_if_cancelled()
            self.finished.emit(services)
            services = None
        except OperationCancelled:
            if services is not None:
                self._close_services(services)
            self.cancelled.emit()
        except Exception as exc:  # noqa: BLE001
            if self._cancel_token.is_cancelled():
                if services is not None:
                    self._close_services(services)
                self.cancelled.emit()
                return
            self.failed.emit(str(exc))

    def _move_service_objects_to_ui_thread(self, services: object) -> None:
        application = QApplication.instance()
        if application is None:
            return
        tts_provider = getattr(services, "tts_provider", None)
        if isinstance(tts_provider, QObject):
            tts_provider.moveToThread(application.thread())

    def _close_services(self, services: object) -> None:
        for provider in (getattr(services, "tts_provider", None),):
            close = getattr(provider, "close", None)
            if callable(close):
                try:
                    close()
                except Exception as exc:  # noqa: BLE001
                    log_event("TTS", "取消后台启动时关闭 TTS Provider 失败", {"error": str(exc)})
        mcp_tool_provider = getattr(services, "mcp_tool_provider", None)
        close_mcp = getattr(mcp_tool_provider, "close", None)
        if callable(close_mcp):
            try:
                close_mcp()
            except Exception as exc:  # noqa: BLE001
                log_event("MCP", "取消后台启动时关闭 MCP Provider 失败", {"error": str(exc)})
        plugin_manager = getattr(services, "plugin_manager", None)
        shutdown_all = getattr(plugin_manager, "shutdown_all", None)
        if callable(shutdown_all):
            try:
                shutdown_all()
            except Exception as exc:  # noqa: BLE001
                log_event("PluginManager", "取消后台启动时关闭插件失败", {"error": str(exc)})


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


def _format_data_migration_failure(report: MigrationReport) -> str:
    errors = "\n".join(
        f"{result.name}: {result.error}"
        for result in report.results
        if result.status == "failed"
    )
    return format_failure_message(
        "部分旧数据迁移失败，原数据没有被覆盖。",
        "受影响功能本次可能使用默认值或暂不可用；请保留原数据并查看 "
        "data/logs/sakura-runtime.log，下次启动会继续重试迁移。",
        errors,
    )


def _normalize_proxy_env() -> None:
    """将 ``socks://`` 规范化为 ``socks5://``，兼容 httpx 仅识别 socks5:// 的限制。"""
    for key in ("ALL_PROXY", "all_proxy"):
        value = os.environ.get(key)
        if value and value.startswith("socks://"):
            os.environ[key] = "socks5://" + value[len("socks://"):]


def main() -> int:
    _enable_crash_diagnostics(BASE_DIR)
    _normalize_proxy_env()
    qInstallMessageHandler(_qt_message_handler)
    _configure_windows_high_dpi()
    app = QApplication(sys.argv)
    app.setApplicationName("Sakura Desktop Pet")
    app.setQuitOnLastWindowClosed(False)
    _force_light_palette(app)

    # 启动自检必须先于单实例锁创建：data/ 不可写或被文件占位时，
    # 应给出明确 fatal，而不是在锁文件目录创建阶段提前失败。
    self_check = run_startup_self_check(BASE_DIR)
    if self_check.fatal_issues:
        QMessageBox.critical(
            None,
            "启动检查未通过",
            format_failure_message(
                "Sakura 的运行环境未通过启动检查。",
                "请按诊断信息修复缺失文件或目录权限后重新启动。",
                self_check.fatal_message(),
            ),
        )
        return 1

    # 单实例锁：防止双开并发写历史/配置、争抢记忆库锁。
    # guard 需存活到进程结束（main 栈帧持有），崩溃残留锁由 QLockFile stale 检测接管。
    instance_guard = SingleInstanceGuard(BASE_DIR)
    if not instance_guard.acquire():
        QMessageBox.warning(
            None,
            "Sakura 已在运行",
            f"{instance_guard.holder_description()}正在运行中。\n"
            "请先退出已有实例（可在系统托盘中找到它）。",
        )
        return 0
    app.aboutToQuit.connect(instance_guard.release)

    # 发布包不携带 mcp.yaml/plugins.yaml（避免覆盖升级冲掉用户配置），缺失时生成默认
    ensure_default_configs(BASE_DIR)
    # 记录/比对 app_version，覆盖升级后第一时间在日志中留痕
    record_app_version(BASE_DIR)

    # 版本化数据迁移：失败不阻断启动（原文件保持原位，按旧形态继续运行，下次启动重试）
    migration_report = MigrationRunner(BASE_DIR).run()
    if migration_report.failed:
        QMessageBox.warning(
            None,
            "数据迁移未完成",
            _format_data_migration_failure(migration_report),
        )

    initial_setup = False
    try:
        initial_setup = _initial_setup_required(BASE_DIR)
        if initial_setup:
            context = _open_first_run_settings(BASE_DIR)
        else:
            context = build_initial_app_context(BASE_DIR)
    except (CharacterConfigError, OSError, RuntimeError, TTSConfigError, ValueError) as exc:
        if initial_setup:
            QMessageBox.critical(
                None,
                "启动失败",
                format_failure_message(
                    "首次启动配置没有完成，Sakura 无法继续启动。",
                    "请检查角色包、TTS 配置和 data 目录权限后重试。",
                    exc,
                ),
            )
        _write_startup_error("Character", f"配置无效：{exc}")
        return 1
    if context is None:
        return 0

    character_issues = getattr(context.character_registry, "load_errors", ())
    if character_issues:
        details = "\n".join(
            f"- {issue.manifest_path}: {issue.error}" for issue in character_issues[:5]
        )
        if len(character_issues) > 5:
            details += f"\n- 另有 {len(character_issues) - 5} 个角色包已跳过"
        QMessageBox.warning(
            None,
            "部分角色包未加载",
            "Sakura 已跳过损坏或不安全的角色包，其他角色仍可正常使用。\n\n" + details,
        )

    _ensure_launch_at_login_state(BASE_DIR, context.settings_service)
    pet_window = PetWindow(context)
    app.aboutToQuit.connect(pet_window.close_external_tools)
    pet_window.show()
    QTimer.singleShot(0, lambda: _start_tts_migration_or_deferred(BASE_DIR, pet_window))

    return app.exec()


def _write_startup_error(category: str, message: str) -> None:
    log_event(category, "启动失败", {"error": message})
    sys.stderr.write(f"[{category}] {message}\n")


def _character_packages_missing(base_dir: Path) -> bool:
    characters_dir = base_dir / "characters"
    if not characters_dir.is_dir():
        return True
    try:
        return not any(characters_dir.glob("*/character.json"))
    except OSError:
        return False


def _initial_setup_required(base_dir: Path) -> bool:
    if _character_packages_missing(base_dir):
        return True
    settings_service = AppSettingsService(base_dir=base_dir)
    settings = settings_service.load_api_settings()
    chat = resolve_model_slot(
        settings_service.load_api_profiles(),
        settings_service.load_model_selection(),
        MODEL_SLOT_CHAT,
        settings,
    )
    return chat is None or not all(
        (chat.settings.base_url, chat.settings.api_key, chat.settings.model)
    )


def _ensure_launch_at_login_state(
    base_dir: Path,
    settings_service: AppSettingsService,
) -> None:
    try:
        settings = settings_service.load_startup_settings()
        ensure_launch_at_login_state(base_dir, settings.launch_at_login)
    except (LaunchAtLoginError, OSError) as exc:
        log_event("Startup", "同步登录自启动状态失败", {"error": str(exc)})


def _open_first_run_studio(
    base_dir: Path,
    character_id: str | None = None,
) -> dict[str, object] | bool:
    if resolve_tauri_studio_binary(base_dir) is None:
        return False

    loop = QEventLoop()
    state: dict[str, object] = {"finished": False, "closed": False}
    process = TauriStudioProcess(
        base_dir,
        initial_character_id=str(character_id or ""),
    )

    def _on_closed() -> None:
        state["finished"] = True
        state["closed"] = True
        loop.quit()

    def _on_failed(message: object) -> None:
        state["finished"] = True
        state["error"] = str(message) or "角色工作室启动失败。"
        loop.quit()

    process.closed.connect(_on_closed)
    process.failed.connect(_on_failed)
    started = process.start()
    try:
        if started and not bool(state["finished"]):
            loop.exec()
    finally:
        shutdown = getattr(process, "shutdown", None)
        if callable(shutdown):
            shutdown()

    if "error" in state:
        raise RuntimeError(str(state["error"]))
    if not bool(state["closed"]):
        return False
    return {
        "refresh_characters": True,
        "current_character_id": str(character_id or ""),
    }


def _open_first_run_settings(base_dir: Path) -> AppContext | None:
    """首次启动（缺角色包）时用 Tauri 设置页完成初始配置，落库后返回 AppContext。

    Tauri-only 迁移后不再有 Qt 设置弹窗回退：缺少已构建的 settings-tauri 二进制时
    直接报错退出，提示先构建或用 SAKURA_TAURI_SETTINGS_BIN 指定可执行文件。
    """
    settings_service = AppSettingsService(base_dir=base_dir)
    if resolve_tauri_settings_binary(base_dir) is None:
        raise RuntimeError(
            "未找到设置程序（sakura-settings）。请先构建 tools/settings-tauri，"
            "或用环境变量 SAKURA_TAURI_SETTINGS_BIN 指定可执行文件路径。"
        )

    api_settings = settings_service.load_api_settings()
    tts_settings = settings_service.load_tts_settings(
        validate_enabled=False,
        character_profile=None,
    )
    startup_settings = settings_service.load_startup_settings()
    ui_settings = settings_service.load_system_values("ui")
    subtitle_typing_interval_ms, reply_segment_pause_ms = normalize_subtitle_display_speed(
        ui_settings.get("subtitle_typing_interval_ms", SPEECH_TYPING_INTERVAL_MS),
        ui_settings.get("reply_segment_pause_ms", REPLY_SEGMENT_PAUSE_MS),
    )
    character_registry = None
    current_character = None
    if not _character_packages_missing(base_dir):
        character_registry = CharacterRegistry(base_dir)
        current_character = character_registry.get(
            settings_service.load_current_character_id(character_registry)
        )

    process = TauriSettingsProcess(
        base_dir=base_dir,
        settings=settings_service.load_screen_awareness_settings(),
        mcp_settings=settings_service.load_mcp_runtime_settings(),
        runtime_loop_settings=settings_service.load_runtime_loop_settings(),
        debug_log_settings=settings_service.load_debug_log_settings(),
        theme_settings=settings_service.load_theme_settings(),
        character_registry=character_registry,
        current_character=current_character,
        portrait_scale_percent=normalize_portrait_scale_percent(
            ui_settings.get("portrait_scale_percent", PORTRAIT_SCALE_DEFAULT_PERCENT)
        ),
        control_panel_width=normalize_control_panel_width(
            ui_settings.get("control_panel_width", DEFAULT_CONTROL_PANEL_WIDTH)
        ),
        bubble_height=normalize_bubble_height(
            ui_settings.get("bubble_height", DEFAULT_BUBBLE_HEIGHT)
        ),
        control_panel_vertical_offset=normalize_control_panel_vertical_offset(
            ui_settings.get(
                "control_panel_vertical_offset",
                DEFAULT_CONTROL_PANEL_VERTICAL_OFFSET,
            )
        ),
        input_bar_offset=normalize_input_bar_offset(
            ui_settings.get("input_bar_offset", DEFAULT_INPUT_BAR_OFFSET)
        ),
        subtitle_typing_interval_ms=subtitle_typing_interval_ms,
        reply_segment_pause_ms=reply_segment_pause_ms,
        api_settings=api_settings,
        api_profiles=settings_service.load_api_profiles(),
        model_selection=settings_service.load_model_selection(),
        tts_settings=tts_settings,
        startup_settings=startup_settings,
        launch_at_login_supported=is_launch_at_login_supported(),
        studio_launcher=lambda character_id: _open_first_run_studio(base_dir, character_id),
        model=getattr(api_settings, "model", None),
        onboarding=True,
    )

    loop = QEventLoop()
    state: dict[str, object] = {}

    def _on_completed(result: object) -> None:
        state["result"] = result
        loop.quit()

    def _on_cancelled() -> None:
        loop.quit()

    def _on_failed(message: object) -> None:
        state["error"] = str(message) or "设置程序启动失败。"
        loop.quit()

    def _on_apply_requested(request_id: str, _result: object) -> None:
        # 首次引导没有运行中的桌宠可即时生效，「应用」仅回应以免设置页卡住等待；
        # 真正落库发生在用户点「保存/完成」触发的 completed。
        resolve = getattr(process, "resolve_apply_request", None)
        if callable(resolve):
            resolve(request_id, ok=True, error="")

    process.completed.connect(_on_completed)
    process.cancelled.connect(_on_cancelled)
    process.failed.connect(_on_failed)
    process.apply_requested.connect(_on_apply_requested)

    if not process.start():
        raise RuntimeError("无法启动设置程序，请确认已构建 tools/settings-tauri。")
    loop.exec()
    shutdown = getattr(process, "shutdown", None)
    if callable(shutdown):
        shutdown()

    if "error" in state:
        raise RuntimeError(str(state["error"]))
    result = state.get("result")
    if not isinstance(result, TauriSettingsResult):
        return None

    registry = CharacterRegistry(base_dir)
    try:
        selected_profile = registry.get(result.character.character_id)
    except CharacterConfigError:
        QMessageBox.warning(None, "配置无效", "请先导入并选择一个角色包。")
        return None

    result_tts_settings = tts_settings_from_tauri_result(
        result.tts,
        selected_profile,
        base_dir,
        previous=tts_settings,
    )
    subtitle_typing_interval_ms, reply_segment_pause_ms = normalize_subtitle_display_speed(
        result.system_basic.subtitle_typing_interval_ms,
        result.system_basic.reply_segment_pause_ms,
    )
    result_startup_settings = (
        result.system_extra.startup
        if result.system_extra.launch_at_login_supported
        else startup_settings
    )

    settings_service.save_api_settings(result.api.settings)
    settings_service.save_api_profiles(result.api.profiles)
    settings_service.save_model_selection(result.api.model_selection)
    settings_service.save_tts_settings(result_tts_settings)
    settings_service.save_current_character_id(registry, selected_profile.id)
    settings_service.save_screen_awareness_settings(result.screen_awareness)
    settings_service.save_mcp_runtime_settings(result.mcp)
    settings_service.save_runtime_loop_settings(result.runtime_loop)
    settings_service.save_debug_log_settings(result.system_basic.debug_log)
    if result.theme_changed:
        settings_service.save_theme_settings(result.theme)
    if result_startup_settings != startup_settings:
        _apply_launch_at_login_settings(base_dir, result_startup_settings)
        settings_service.save_startup_settings(result_startup_settings)
    settings_service.save_system_values(
        "ui",
        {
            "portrait_scale_percent": int(result.character.portrait_scale_percent),
            "control_panel_width": int(result.character.control_panel_width),
            "bubble_height": int(result.character.bubble_height),
            "control_panel_vertical_offset": int(
                result.character.control_panel_vertical_offset
            ),
            "input_bar_offset": int(result.character.input_bar_offset),
            "subtitle_typing_interval_ms": subtitle_typing_interval_ms,
            "reply_segment_pause_ms": reply_segment_pause_ms,
        },
    )
    return build_initial_app_context(base_dir)


def _apply_launch_at_login_settings(base_dir: Path, settings: StartupSettings) -> None:
    try:
        set_launch_at_login_enabled(base_dir, settings.launch_at_login)
    except (LaunchAtLoginError, OSError) as exc:
        raise OSError(f"无法更新登录自启动：{exc}") from exc


def _start_tts_migration_or_deferred(base_dir: Path, pet_window: PetWindow) -> None:
    migrations = _pending_startup_tts_migrations(base_dir)
    if not migrations:
        _start_deferred_startup(base_dir, pet_window)
        return

    dialog = TTSBundleMigrationDialog(base_dir, pet_window)
    worker = TTSBundleMigrationWorker(migrations)
    pet_window.tts_migration_dialog = dialog
    dialog.show()
    # register=False：迁移不被 stop_all 打断；PetWindow 在运行期间拒绝关闭。
    pet_window.resource_manager.spawn_qt_worker(
        worker,
        parent=pet_window,
        owner=pet_window,
        thread_attr="tts_migration_thread",
        worker_attr="tts_migration_worker",
        signal_bindings=[
            (worker.current_item, dialog.set_current_item),
            (worker.progress, dialog.set_progress),
            (worker.finished, dialog.finish_migration),
        ],
        quit_on=[worker.finished],
        register=False,
    )


def _pending_startup_tts_migrations(base_dir: Path) -> list[TTSBundleMigration]:
    settings_service = AppSettingsService(base_dir=base_dir)
    settings = settings_service.load_tts_settings(validate_enabled=False)
    provider_migrations = find_pending_bundle_migrations(base_dir, settings.provider)
    all_migrations = find_pending_bundle_migrations(base_dir)
    migrations = _dedupe_tts_migrations([*provider_migrations, *all_migrations])
    log_event(
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
            format_failure_message(
                "TTS 整合包迁移失败，Sakura 会继续使用旧目录启动，旧模型不会被删除。",
                "请检查目标目录的空间、权限和文件占用；下次启动会继续迁移。",
                "\n".join(errors),
            ),
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
    worker = DeferredStartupWorker(base_dir, pet_window.context)
    pet_window.resource_manager.spawn_qt_worker(
        worker,
        parent=pet_window,
        owner=pet_window,
        thread_attr="deferred_startup_thread",
        worker_attr="deferred_startup_worker",
        signal_bindings=[
            (worker.finished, pet_window.apply_deferred_services),
            (worker.failed, pet_window.handle_deferred_startup_failed),
        ],
        quit_on=[worker.finished, worker.failed, worker.cancelled],
    )

if __name__ == "__main__":
    raise SystemExit(main())
