from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse

from PySide6.QtCore import QObject, Qt, QThread, Signal, Slot
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHeaderView,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSlider,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from app.agent.memory import MemoryStore
from app.agent.mcp import MCPRuntimeSettings, WINDOWS_MCP_UNAVAILABLE_TEXT
from app.config.character_archive import (
    CharacterArchiveError,
    export_character_archive,
    import_character_archive,
)
from app.config.settings_service import DebugLogSettings
from app.llm.api_client import ApiSettings, OpenAICompatibleClient
from app.config.character_loader import CharacterProfile, CharacterRegistry
from app.ui.portrait_controller import (
    PORTRAIT_SCALE_DEFAULT_PERCENT,
    PORTRAIT_SCALE_MAX_PERCENT,
    PORTRAIT_SCALE_MIN_PERCENT,
    normalize_portrait_scale_percent,
)
from app.ui.subtitle_controller import (
    REPLY_SEGMENT_PAUSE_MAX_MS,
    REPLY_SEGMENT_PAUSE_MIN_MS,
    REPLY_SEGMENT_PAUSE_MS,
    SPEECH_TYPING_INTERVAL_MS,
    SUBTITLE_TYPING_INTERVAL_MAX_MS,
    SUBTITLE_TYPING_INTERVAL_MIN_MS,
    normalize_subtitle_display_speed,
)
from app.agent.proactive_care import (
    PROACTIVE_MAX_COOLDOWN_MINUTES,
    PROACTIVE_MAX_CHECK_INTERVAL_MINUTES,
    PROACTIVE_MAX_SCREEN_CONTEXT_BATCH_LIMIT,
    PROACTIVE_MIN_COOLDOWN_MINUTES,
    PROACTIVE_MIN_CHECK_INTERVAL_MINUTES,
    PROACTIVE_MIN_SCREEN_CONTEXT_BATCH_LIMIT,
    ProactiveCareSettings,
)
from app.voice.tts import (
    DEFAULT_GENIE_TTS_API_URL,
    DEFAULT_GPT_SOVITS_API_URL,
    GenieTTSProvider,
    GPTSoVITSTTSProvider,
    TTS_PROVIDER_CUSTOM_GPT_SOVITS,
    TTS_PROVIDER_GENIE,
    TTS_PROVIDER_GPT_SOVITS,
    GPTSoVITSTTSSettings,
    TTSConfigError,
)
from app.ui.tts_bundle_dialog import TTSBundleDownloadDialog
from app.voice.tts_bundle import default_provider_bundle_work_dir, is_provider_bundle_work_dir
from sdk.types import ToolsTabContribution


MEMORY_READING_TEXT = "正在读取长期记忆..."
MEMORY_DEPENDENCY_LOADING_TEXT = "长期记忆系统正在初始化，首次启动可能需要下载本地嵌入模型，请稍等。"


class ApiConnectionTestWorker(QObject):
    succeeded = Signal(str)
    failed = Signal(str)
    finished = Signal()

    def __init__(self, settings: ApiSettings) -> None:
        super().__init__()
        self.settings = settings

    @Slot()
    def run(self) -> None:
        try:
            message = OpenAICompatibleClient(self.settings).test_connection()
        except Exception as exc:  # UI 边界统一转成可读错误。
            self.failed.emit(str(exc))
        else:
            self.succeeded.emit(message)
        finally:
            self.finished.emit()


class TTSTestWorker(QObject):
    succeeded = Signal(object, str)
    failed = Signal(str)
    finished = Signal()

    def __init__(self, settings: GPTSoVITSTTSSettings) -> None:
        super().__init__()
        self.settings = settings

    @Slot()
    def run(self) -> None:
        provider = None
        try:
            provider = (
                GenieTTSProvider(self.settings)
                if self.settings.provider == TTS_PROVIDER_GENIE
                else GPTSoVITSTTSProvider(self.settings)
            )
            ok, message = provider.ensure_ready()
            if ok:
                self.succeeded.emit(provider.settings, message)
            else:
                self.failed.emit(message)
        except Exception as exc:  # UI 边界统一转成可读错误。
            self.failed.emit(str(exc))
        finally:
            if provider is not None:
                close = getattr(provider, "close", None)
                if callable(close):
                    close()
            self.finished.emit()


class MemoryListWorker(QObject):
    succeeded = Signal(list)
    failed = Signal(str)
    finished = Signal()

    def __init__(self, memory_store: MemoryStore, limit: int = 200) -> None:
        super().__init__()
        self.memory_store = memory_store
        self.limit = limit

    @Slot()
    def run(self) -> None:
        try:
            memories = self.memory_store.list_memories(limit=self.limit)
        except Exception as exc:  # UI 边界统一转成可读错误。
            self.failed.emit(str(exc))
        else:
            self.succeeded.emit(memories)
        finally:
            self.finished.emit()


class CharacterArchiveExportWorker(QObject):
    succeeded = Signal(str)
    failed = Signal(str)
    finished = Signal()

    def __init__(self, profile: CharacterProfile, output_path: Path) -> None:
        super().__init__()
        self.profile = profile
        self.output_path = output_path

    @Slot()
    def run(self) -> None:
        try:
            export_character_archive(self.profile, self.output_path)
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(str(exc))
        else:
            self.succeeded.emit(str(self.output_path))
        finally:
            self.finished.emit()


class SettingsDialog(QDialog):
    def __init__(
        self,
        api_settings: ApiSettings,
        tts_settings: GPTSoVITSTTSSettings,
        base_dir: Path,
        character_registry: CharacterRegistry | None = None,
        current_character: CharacterProfile | None = None,
        proactive_care_settings: ProactiveCareSettings | None = None,
        mcp_settings: MCPRuntimeSettings | None = None,
        debug_log_settings: DebugLogSettings | None = None,
        memory_store: MemoryStore | None = None,
        tools_tab_contributions: list[ToolsTabContribution] | None = None,
        parent=None,  # type: ignore[no-untyped-def]
        portrait_scale_percent: int = PORTRAIT_SCALE_DEFAULT_PERCENT,
        subtitle_typing_interval_ms: int = SPEECH_TYPING_INTERVAL_MS,
        reply_segment_pause_ms: int = REPLY_SEGMENT_PAUSE_MS,
    ) -> None:
        super().__init__(parent)
        self.base_dir = base_dir
        self.tts_settings = tts_settings
        self.character_registry = character_registry
        self.current_character = current_character
        self.portrait_scale_percent = normalize_portrait_scale_percent(portrait_scale_percent)
        (
            self.subtitle_typing_interval_ms,
            self.reply_segment_pause_ms,
        ) = normalize_subtitle_display_speed(
            subtitle_typing_interval_ms,
            reply_segment_pause_ms,
        )
        self.memory_store = memory_store
        self._all_memories: list[dict[str, object]] = []
        self._visible_memories: list[dict[str, object]] = []
        self._selected_memory_ids: set[str] = set()
        self._memory_editor_mode: Literal["new", "edit"] | None = None
        self._editing_memory_id: str | None = None
        self._active_memory_id: str | None = None
        self.result_api_settings: ApiSettings | None = None
        self.result_tts_settings: GPTSoVITSTTSSettings | None = None
        self.result_character_id: str | None = None
        self.result_portrait_scale_percent: int | None = None
        self.result_subtitle_typing_interval_ms: int | None = None
        self.result_reply_segment_pause_ms: int | None = None
        self.result_proactive_care_settings: ProactiveCareSettings | None = None
        self.result_mcp_settings: MCPRuntimeSettings | None = None
        self.result_debug_log_settings: DebugLogSettings | None = None
        self._api_test_thread: QThread | None = None
        self._api_test_worker: ApiConnectionTestWorker | None = None
        self._tts_test_thread: QThread | None = None
        self._tts_test_worker: TTSTestWorker | None = None
        self._pending_accept_values: dict[str, object] | None = None
        self._save_button_text: str | None = None
        self._memory_list_thread: QThread | None = None
        self._memory_list_worker: MemoryListWorker | None = None
        self._character_export_thread: QThread | None = None
        self._character_export_worker: CharacterArchiveExportWorker | None = None
        self._memory_reload_pending = False
        self._syncing_memory_selection = False

        self.setWindowTitle("设置")
        self.resize(560, 400)

        tabs = QTabWidget(self)
        tabs.addTab(self._build_character_tab(character_registry, current_character), "角色")
        tabs.addTab(self._build_api_tab(api_settings), "API")
        tabs.addTab(self._build_tts_tab(tts_settings), "TTS")
        tabs.addTab(
            self._build_privacy_tab(
                proactive_care_settings or ProactiveCareSettings(),
            ),
            "隐私",
        )
        tabs.addTab(
            self._build_mcp_tab(
                mcp_settings or MCPRuntimeSettings(),
                tools_tab_contributions or [],
            ),
            "工具",
        )
        tabs.addTab(self._build_system_tab(debug_log_settings or DebugLogSettings()), "系统")
        if memory_store is not None:
            tabs.addTab(self._build_memory_tab(memory_store), "记忆")

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel,
            self,
        )
        self.button_box = buttons
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout()
        layout.addWidget(tabs, 1)
        layout.addWidget(buttons)
        self.setLayout(layout)
        self.setStyleSheet(
            """
            QDialog {
                background: #fff6fa;
                color: #3d2b35;
                font-family: "Microsoft YaHei", "Yu Gothic UI", sans-serif;
                font-size: 14px;
            }
            QTabWidget::pane {
                border: 1px solid rgba(238, 172, 200, 0.54);
                border-radius: 8px;
                background: rgba(255, 232, 241, 0.70);
            }
            QTabBar::tab {
                background: rgba(255, 232, 241, 0.75);
                border: 1px solid rgba(238, 172, 200, 0.48);
                border-bottom: none;
                border-top-left-radius: 8px;
                border-top-right-radius: 8px;
                padding: 7px 18px;
                margin-right: 4px;
                color: #7a3656;
            }
            QTabBar::tab:selected {
                background: #ffffff;
                color: #b13e73;
                font-weight: 700;
            }
            QLineEdit, QSpinBox, QDoubleSpinBox, QTextEdit, QTableWidget, QComboBox {
                background: rgba(255, 255, 255, 0.92);
                border: 1px solid rgba(238, 172, 200, 0.58);
                border-radius: 7px;
                padding: 6px 8px;
                color: #3d2b35;
                selection-background-color: rgba(213, 91, 145, 0.28);
            }
            QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QTextEdit:focus, QComboBox:focus {
                border: 1px solid rgba(213, 91, 145, 0.76);
                background: #ffffff;
            }
            QTableWidget {
                gridline-color: rgba(238, 172, 200, 0.42);
                alternate-background-color: rgba(255, 244, 249, 0.86);
            }
            QHeaderView::section {
                background: #ffe8f1;
                border: 1px solid rgba(238, 172, 200, 0.52);
                color: #7a3656;
                padding: 6px;
                font-weight: 700;
            }
            QCheckBox {
                color: #4b3440;
                spacing: 8px;
            }
            QCheckBox::indicator {
                width: 16px;
                height: 16px;
                border-radius: 4px;
                border: 1px solid rgba(213, 91, 145, 0.68);
                background: #ffffff;
            }
            QCheckBox::indicator:checked {
                background: #d55b91;
                border: 1px solid #b13e73;
            }
            QPushButton {
                background: #d55b91;
                border: 1px solid rgba(177, 62, 115, 0.55);
                border-radius: 8px;
                color: white;
                min-width: 72px;
                padding: 8px 12px;
                font-weight: 600;
            }
            QPushButton:hover {
                background: #bf3f7a;
            }
            QPushButton:disabled {
                background: rgba(213, 91, 145, 0.42);
                border: 1px solid rgba(238, 172, 200, 0.45);
                color: rgba(255, 255, 255, 0.76);
            }
            """
        )

    def _build_character_tab(
        self,
        character_registry: CharacterRegistry | None,
        current_character: CharacterProfile | None,
    ) -> QWidget:
        tab = QWidget(self)
        self.character_combo = QComboBox(tab)
        self.character_empty_label = QLabel("尚未导入角色", tab)
        self._refresh_character_combo(
            current_character.id if current_character is not None else None
        )

        form_layout = QFormLayout()
        form_layout.setContentsMargins(16, 18, 16, 16)
        form_layout.setSpacing(12)
        form_layout.addRow("状态", self.character_empty_label)
        form_layout.addRow("当前角色", self.character_combo)
        form_layout.addRow("立绘大小", self._build_portrait_scale_control(tab))
        form_layout.addRow("角色包", self._build_character_archive_controls(tab))
        tab.setLayout(form_layout)
        self._sync_character_archive_controls()
        return tab

    def _build_character_archive_controls(self, parent: QWidget) -> QWidget:
        container = QWidget(parent)
        self.character_import_button = QPushButton("导入 .char", container)
        self.character_export_button = QPushButton("导出当前角色", container)
        self.character_import_button.clicked.connect(self._import_character_archive)
        self.character_export_button.clicked.connect(self._export_current_character_archive)
        self._sync_character_archive_controls()

        layout = QHBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        layout.addWidget(self.character_import_button)
        layout.addWidget(self.character_export_button)
        layout.addStretch(1)
        container.setLayout(layout)
        return container

    def _build_portrait_scale_control(self, parent: QWidget) -> QWidget:
        container = QWidget(parent)
        self.portrait_scale_slider = QSlider(Qt.Orientation.Horizontal, container)
        self.portrait_scale_slider.setRange(
            PORTRAIT_SCALE_MIN_PERCENT,
            PORTRAIT_SCALE_MAX_PERCENT,
        )
        self.portrait_scale_slider.setSingleStep(5)
        self.portrait_scale_slider.setPageStep(10)
        self.portrait_scale_slider.setTickInterval(25)
        self.portrait_scale_slider.setTickPosition(QSlider.TickPosition.TicksBelow)
        self.portrait_scale_slider.setValue(self.portrait_scale_percent)

        self.portrait_scale_spin = QSpinBox(container)
        self.portrait_scale_spin.setRange(
            PORTRAIT_SCALE_MIN_PERCENT,
            PORTRAIT_SCALE_MAX_PERCENT,
        )
        self.portrait_scale_spin.setSingleStep(5)
        self.portrait_scale_spin.setSuffix("%")
        self.portrait_scale_spin.setValue(self.portrait_scale_percent)

        self.portrait_scale_slider.valueChanged.connect(self.portrait_scale_spin.setValue)
        self.portrait_scale_spin.valueChanged.connect(self.portrait_scale_slider.setValue)

        layout = QHBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        layout.addWidget(self.portrait_scale_slider, 1)
        layout.addWidget(self.portrait_scale_spin)
        container.setLayout(layout)
        return container

    def _build_api_tab(self, settings: ApiSettings) -> QWidget:
        tab = QWidget(self)
        self.base_url_edit = QLineEdit(settings.base_url, tab)
        self.base_url_edit.setPlaceholderText("https://api.openai.com/v1")

        self.api_key_edit = QLineEdit(settings.api_key, tab)
        self.api_key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.api_key_edit.setPlaceholderText("请输入 API Key")

        self.model_edit = QLineEdit(settings.model, tab)
        self.model_edit.setPlaceholderText("gpt-4.1-mini")

        self.api_timeout_spin = QSpinBox(tab)
        self.api_timeout_spin.setRange(1, 600)
        self.api_timeout_spin.setSuffix(" 秒")
        self.api_timeout_spin.setValue(settings.timeout_seconds)

        self.api_test_button = QPushButton("测试 API", tab)
        self.api_test_button.clicked.connect(self._test_api_settings)

        form_layout = QFormLayout()
        form_layout.setContentsMargins(16, 18, 16, 16)
        form_layout.setSpacing(12)
        form_layout.addRow("Base URL", self.base_url_edit)
        form_layout.addRow("API Key", self.api_key_edit)
        form_layout.addRow("模型", self.model_edit)
        form_layout.addRow("超时", self.api_timeout_spin)
        form_layout.addRow("", self.api_test_button)
        tab.setLayout(form_layout)
        return tab

    def _build_tts_tab(self, settings: GPTSoVITSTTSSettings) -> QWidget:
        tab = QWidget(self)
        self.tts_enabled_check = QCheckBox("启用 TTS 语音", tab)
        self.tts_enabled_check.setChecked(settings.enabled)

        self.tts_provider_combo = QComboBox(tab)
        self.tts_provider_combo.addItem("GPT-SoVITS 整合包（GPU）", TTS_PROVIDER_GPT_SOVITS)
        self.tts_provider_combo.addItem("Genie TTS 整合包（CPU）", TTS_PROVIDER_GENIE)
        self.tts_provider_combo.addItem("自定义外部 GPT-SoVITS", TTS_PROVIDER_CUSTOM_GPT_SOVITS)
        provider_index = self.tts_provider_combo.findData(settings.provider)
        self.tts_provider_combo.setCurrentIndex(provider_index if provider_index >= 0 else 0)

        self.tts_api_url_edit = QLineEdit(settings.api_url, tab)
        self.tts_api_url_edit.setPlaceholderText(_default_tts_api_url(settings.provider))
        self.tts_work_dir_edit = QLineEdit(str(settings.work_dir or ""), tab)
        self.tts_work_dir_edit.setPlaceholderText("data/tts_bundles/installed/gpt_sovits_nvidia50/GPT-SoVITS-v2pro-20250604-nvidia50")
        self.tts_bundle_download_button = QPushButton("一键下载 TTS 整合包", tab)
        self.tts_bundle_download_button.clicked.connect(self._download_gpt_sovits_bundle)
        self.tts_provider_combo.currentIndexChanged.connect(lambda _index: self._sync_tts_provider_controls(apply_defaults=True))

        self.ref_lang_edit = QLineEdit(settings.ref_lang, tab)
        self.text_lang_edit = QLineEdit(settings.text_lang, tab)

        self.tts_timeout_spin = QSpinBox(tab)
        self.tts_timeout_spin.setRange(1, 600)
        self.tts_timeout_spin.setSuffix(" 秒")
        self.tts_timeout_spin.setValue(settings.timeout_seconds)

        form_layout = QFormLayout()
        form_layout.setContentsMargins(16, 18, 16, 16)
        form_layout.setSpacing(12)
        form_layout.addRow("", self.tts_enabled_check)
        form_layout.addRow("TTS 提供器", self.tts_provider_combo)
        form_layout.addRow("API URL", self.tts_api_url_edit)
        form_layout.addRow("TTS 工作目录", self.tts_work_dir_edit)
        form_layout.addRow("", self.tts_bundle_download_button)
        form_layout.addRow("参考语言", self.ref_lang_edit)
        form_layout.addRow("文本语言", self.text_lang_edit)
        form_layout.addRow("超时", self.tts_timeout_spin)
        tab.setLayout(form_layout)
        self._sync_tts_provider_controls(apply_defaults=_is_bundled_tts_provider(settings.provider))
        return tab

    def _build_privacy_tab(
        self,
        proactive_care_settings: ProactiveCareSettings,
    ) -> QWidget:
        tab = QWidget(self)
        self.proactive_screen_context_enabled_check = QCheckBox("允许模型主动获取屏幕信息", tab)
        self.proactive_screen_context_enabled_check.setChecked(
            proactive_care_settings.screen_context_enabled
        )

        self.proactive_check_interval_spin = QSpinBox(tab)
        self.proactive_check_interval_spin.setRange(
            PROACTIVE_MIN_CHECK_INTERVAL_MINUTES,
            PROACTIVE_MAX_CHECK_INTERVAL_MINUTES,
        )
        self.proactive_check_interval_spin.setSuffix(" 分钟")
        self.proactive_check_interval_spin.setValue(
            proactive_care_settings.normalized().check_interval_minutes
        )

        self.proactive_cooldown_spin = QSpinBox(tab)
        self.proactive_cooldown_spin.setRange(
            PROACTIVE_MIN_COOLDOWN_MINUTES,
            PROACTIVE_MAX_COOLDOWN_MINUTES,
        )
        self.proactive_cooldown_spin.setSuffix(" 分钟")
        self.proactive_cooldown_spin.setValue(
            proactive_care_settings.normalized().cooldown_minutes
        )

        self.proactive_batch_limit_spin = QSpinBox(tab)
        self.proactive_batch_limit_spin.setRange(
            PROACTIVE_MIN_SCREEN_CONTEXT_BATCH_LIMIT,
            PROACTIVE_MAX_SCREEN_CONTEXT_BATCH_LIMIT,
        )
        self.proactive_batch_limit_spin.setSuffix(" 张")
        self.proactive_batch_limit_spin.setValue(
            proactive_care_settings.normalized().screen_context_batch_limit
        )
        self.proactive_screen_context_enabled_check.toggled.connect(
            self._sync_proactive_interval_controls
        )
        self._sync_proactive_interval_controls(
            self.proactive_screen_context_enabled_check.isChecked()
        )

        form_layout = QFormLayout()
        form_layout.setContentsMargins(16, 18, 16, 16)
        form_layout.setSpacing(12)
        form_layout.addRow("", self.proactive_screen_context_enabled_check)
        form_layout.addRow("主动检查间隔", self.proactive_check_interval_spin)
        form_layout.addRow("主动打扰冷却", self.proactive_cooldown_spin)
        form_layout.addRow("单次最多发送截图", self.proactive_batch_limit_spin)
        tab.setLayout(form_layout)
        return tab

    def _build_mcp_tab(
        self,
        settings: MCPRuntimeSettings,
        tools_tab_contributions: list[ToolsTabContribution],
    ) -> QWidget:
        tab = QWidget(self)
        self.windows_mcp_enabled_check = QCheckBox("启用 Windows MCP 桌面控制（高级）", tab)
        self.windows_mcp_enabled_check.setChecked(False)
        self.windows_mcp_enabled_check.setEnabled(False)
        self.windows_mcp_enabled_check.setToolTip(WINDOWS_MCP_UNAVAILABLE_TEXT)

        restart_hint = QLabel(
            f"{WINDOWS_MCP_UNAVAILABLE_TEXT}。保存后需要重启 Sakura 才会加载或卸载 Windows MCP 工具。",
            tab,
        )
        restart_hint.setWordWrap(True)
        restart_hint.setStyleSheet("color: #9b4f72;")

        form_layout = QFormLayout()
        form_layout.setContentsMargins(16, 18, 16, 16)
        form_layout.setSpacing(12)
        form_layout.addRow("", self.windows_mcp_enabled_check)
        form_layout.addRow("生效方式", restart_hint)
        for contribution in sorted(tools_tab_contributions, key=lambda item: item.order):
            try:
                widget = contribution.build(None)
            except Exception as exc:
                widget = QLabel(f"{contribution.title} 设置加载失败：{exc}", tab)
                widget.setWordWrap(True)
            form_layout.addRow(contribution.title, widget)
        tab.setLayout(form_layout)
        return tab

    def _build_system_tab(self, debug_settings: DebugLogSettings) -> QWidget:
        tab = QWidget(self)
        self.debug_log_enabled_check = QCheckBox("输出终端调试日志", tab)
        self.debug_log_enabled_check.setChecked(debug_settings.enabled)
        self.debug_body_enabled_check = QCheckBox("输出完整请求/回复正文", tab)
        self.debug_body_enabled_check.setChecked(debug_settings.body_enabled)
        self.debug_log_enabled_check.toggled.connect(self.debug_body_enabled_check.setEnabled)
        self.debug_body_enabled_check.setEnabled(self.debug_log_enabled_check.isChecked())
        self.debug_file_enabled_check = QCheckBox("输出文件运行日志", tab)
        self.debug_file_enabled_check.setChecked(debug_settings.file_enabled)

        self.subtitle_typing_interval_spin = QSpinBox(tab)
        self.subtitle_typing_interval_spin.setRange(
            SUBTITLE_TYPING_INTERVAL_MIN_MS,
            SUBTITLE_TYPING_INTERVAL_MAX_MS,
        )
        self.subtitle_typing_interval_spin.setSuffix(" 毫秒")
        self.subtitle_typing_interval_spin.setValue(self.subtitle_typing_interval_ms)

        self.reply_segment_pause_spin = QSpinBox(tab)
        self.reply_segment_pause_spin.setRange(
            REPLY_SEGMENT_PAUSE_MIN_MS,
            REPLY_SEGMENT_PAUSE_MAX_MS,
        )
        self.reply_segment_pause_spin.setSuffix(" 毫秒")
        self.reply_segment_pause_spin.setValue(self.reply_segment_pause_ms)

        form_layout = QFormLayout()
        form_layout.setContentsMargins(16, 18, 16, 16)
        form_layout.setSpacing(12)
        form_layout.addRow("", self.debug_log_enabled_check)
        form_layout.addRow("", self.debug_body_enabled_check)
        form_layout.addRow("", self.debug_file_enabled_check)
        form_layout.addRow("字幕逐字间隔", self.subtitle_typing_interval_spin)
        form_layout.addRow("回复分段停顿", self.reply_segment_pause_spin)
        tab.setLayout(form_layout)
        return tab

    @Slot(bool)
    def _sync_proactive_interval_controls(self, enabled: bool) -> None:
        """主动屏幕获取关闭时，不允许调整主动关怀时间参数。"""
        self.proactive_check_interval_spin.setEnabled(enabled)
        self.proactive_cooldown_spin.setEnabled(enabled)
        self.proactive_batch_limit_spin.setEnabled(enabled)

    def _build_memory_tab(self, memory_store: MemoryStore) -> QWidget:
        tab = QWidget(self)
        _ = memory_store

        self.memory_search_edit = QLineEdit(tab)
        self.memory_search_edit.setPlaceholderText("搜索记忆内容或 ID")
        self.memory_search_edit.textChanged.connect(self._refresh_memory_table)

        self.memory_refresh_button = QPushButton("刷新", tab)
        self.memory_refresh_button.clicked.connect(self._load_memory_entries)
        self.memory_status_label = QLabel(MEMORY_READING_TEXT, tab)
        self.memory_status_label.setStyleSheet("color: #9b4f72;")

        self.memory_table = QTableWidget(0, 4, tab)
        self.memory_table.setHorizontalHeaderLabels(["", "内容", "更新时间", "ID"])
        self.memory_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.memory_table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.memory_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.memory_table.verticalHeader().setVisible(False)
        self.memory_table.setAlternatingRowColors(True)
        self.memory_table.setWordWrap(True)
        self.memory_table.itemClicked.connect(self._handle_memory_item_clicked)
        header = self.memory_table.horizontalHeader()
        header.setStretchLastSection(False)
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Fixed)
        self.memory_table.setColumnWidth(0, 56)
        self.memory_table.setColumnWidth(3, 82)
        self.memory_select_all_check = QCheckBox(header)
        self.memory_select_all_check.setToolTip("全选当前结果")
        self.memory_select_all_check.stateChanged.connect(
            self._handle_memory_select_all_check_changed
        )
        header.sectionResized.connect(
            lambda *_args: self._sync_memory_select_all_check_geometry()
        )
        self._sync_memory_select_all_check_geometry()

        self.memory_selection_label = QLabel("已选择 0 条", tab)
        self.memory_selection_label.setStyleSheet("color: #7a3656;")
        self.memory_delete_button = QPushButton("删除选中", tab)
        self.memory_delete_button.setEnabled(False)
        self.memory_delete_button.clicked.connect(self._delete_memory_entry)
        self.memory_clear_selection_button = QPushButton("清空选择", tab)
        self.memory_clear_selection_button.setEnabled(False)
        self.memory_clear_selection_button.clicked.connect(self._clear_memory_selection)
        self.memory_preview_label = QLabel("未选择记忆", tab)
        self.memory_preview_label.setWordWrap(True)
        self.memory_preview_label.setStyleSheet("color: #6d4a5b;")

        self.memory_new_button = QPushButton("新增记忆", tab)
        self.memory_new_button.setCheckable(True)
        self.memory_new_button.toggled.connect(self._toggle_memory_new_editor)
        self.memory_content_edit = QTextEdit(tab)
        self.memory_content_edit.setPlaceholderText("新增长期记忆内容")
        self.memory_content_edit.setFixedHeight(84)
        self.memory_save_button = QPushButton("保存", tab)
        self.memory_save_button.clicked.connect(self._save_memory_entry)

        filter_layout = QHBoxLayout()
        filter_layout.addWidget(self.memory_search_edit, 1)
        filter_layout.addWidget(self.memory_refresh_button)

        status_layout = QHBoxLayout()
        status_layout.addWidget(self.memory_status_label, 1)
        status_layout.addWidget(self.memory_new_button)

        selection_layout = QHBoxLayout()
        selection_layout.addWidget(self.memory_selection_label)
        selection_layout.addStretch(1)
        selection_layout.addWidget(self.memory_clear_selection_button)
        selection_layout.addWidget(self.memory_delete_button)

        self.memory_editor_container = QWidget(tab)
        editor_layout = QFormLayout()
        editor_layout.setContentsMargins(0, 0, 0, 0)
        editor_layout.setSpacing(8)
        editor_layout.addRow("内容", self.memory_content_edit)
        editor_layout.addRow("", self.memory_save_button)
        self.memory_editor_container.setLayout(editor_layout)
        self.memory_editor_container.setVisible(False)

        layout = QVBoxLayout()
        layout.setContentsMargins(16, 18, 16, 16)
        layout.setSpacing(10)
        layout.addLayout(filter_layout)
        layout.addLayout(status_layout)
        layout.addWidget(self.memory_table, 1)
        layout.addLayout(selection_layout)
        layout.addWidget(self.memory_editor_container)
        tab.setLayout(layout)

        self._show_memory_placeholder(MEMORY_READING_TEXT)
        self._clear_memory_editor()
        self._load_memory_entries()
        return tab

    def _load_memory_entries(self) -> None:
        if self.memory_store is None or not hasattr(self, "memory_table"):
            return
        if self._memory_list_thread is not None:
            self._memory_reload_pending = True
            return

        loading_text = self._memory_loading_text()
        self.memory_status_label.setText(loading_text)
        self.memory_refresh_button.setEnabled(False)
        self._show_memory_placeholder(loading_text)

        thread = QThread(self)
        worker = MemoryListWorker(self.memory_store, limit=200)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.succeeded.connect(self._handle_memory_load_success)
        worker.failed.connect(self._handle_memory_load_failed)
        worker.finished.connect(thread.quit)
        worker.finished.connect(self._reset_memory_list_worker)  # 在 worker 结束时立即重置，避免依赖 thread.finished 的多轮事件链
        worker.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)

        self._memory_list_thread = thread
        self._memory_list_worker = worker
        thread.start()

    def _memory_loading_text(self) -> str:
        if self.memory_store is None:
            return MEMORY_READING_TEXT
        needs_download = getattr(self.memory_store, "needs_embedding_model_download", None)
        if not callable(needs_download):
            return MEMORY_READING_TEXT
        try:
            return MEMORY_DEPENDENCY_LOADING_TEXT if bool(needs_download()) else MEMORY_READING_TEXT
        except Exception:  # UI 状态提示不能阻断记忆列表加载。
            return MEMORY_READING_TEXT

    @Slot(list)
    def _handle_memory_load_success(self, memories: list[dict[str, object]]) -> None:
        self._all_memories = list(memories)
        all_ids = {str(memory.get("id", "")) for memory in self._all_memories}
        self._selected_memory_ids &= all_ids
        if self._editing_memory_id and self._editing_memory_id not in all_ids:
            self._memory_editor_mode = None
            self._editing_memory_id = None
            self._active_memory_id = None
            self._clear_memory_editor()
            self.memory_editor_container.setVisible(False)
        self.memory_status_label.setText(f"已加载 {len(self._all_memories)} 条记忆")
        self._refresh_memory_table()

    @Slot(str)
    def _handle_memory_load_failed(self, message: str) -> None:
        self._all_memories = []
        self.memory_status_label.setText(f"读取失败：{message}")
        self._show_memory_placeholder("记忆读取失败，请稍后重试。")
        QMessageBox.warning(self, "读取失败", message)

    @Slot()
    def _reset_memory_list_worker(self) -> None:
        self.memory_refresh_button.setEnabled(True)
        self._memory_list_thread = None
        self._memory_list_worker = None
        if self._memory_reload_pending:
            self._memory_reload_pending = False
            self._load_memory_entries()

    def _refresh_memory_table(self) -> None:
        if not hasattr(self, "memory_table"):
            return
        keyword = self.memory_search_edit.text().strip()
        keyword_lower = keyword.lower()
        if keyword_lower:
            self._visible_memories = [
                memory
                for memory in self._all_memories
                if keyword_lower in str(memory.get("content", "")).lower()
                or keyword_lower in str(memory.get("id", "")).lower()
            ]
        else:
            self._visible_memories = list(self._all_memories)
        if not self._visible_memories:
            self._show_memory_placeholder("没有匹配的记忆。" if keyword else "暂无长期记忆。")
            return

        self._syncing_memory_selection = True
        self.memory_table.blockSignals(True)
        self.memory_table.clearContents()
        self.memory_table.setRowCount(len(self._visible_memories))
        for row, memory in enumerate(self._visible_memories):
            memory_id = str(memory.get("id", ""))
            content = str(memory.get("content", ""))
            updated_at = str(memory.get("updated_at") or memory.get("created_at") or "")
            is_checked = memory_id in self._selected_memory_ids

            select_item = QTableWidgetItem("")
            select_item.setFlags(Qt.ItemFlag.ItemIsEnabled)
            select_item.setData(Qt.ItemDataRole.UserRole, memory_id)

            values = [
                content,
                _format_memory_time(updated_at),
                _compact_memory_id(memory_id),
            ]
            self.memory_table.setItem(row, 0, select_item)
            self._set_memory_checkbox_widget(row, memory_id, is_checked)
            for column, value in enumerate(values, start=1):
                item = QTableWidgetItem(value)
                item.setFlags(Qt.ItemFlag.ItemIsEnabled)
                if column == 1:
                    item.setToolTip(content)
                elif column == 3:
                    item.setToolTip(memory_id)
                    item.setData(Qt.ItemDataRole.UserRole, memory_id)
                self.memory_table.setItem(row, column, item)
            self._apply_memory_row_checked_style(row, is_checked)
        self.memory_table.blockSignals(False)
        self._syncing_memory_selection = False
        self._sync_memory_select_all_check_geometry()
        self._sync_memory_bulk_actions()

    def _show_memory_placeholder(self, text: str) -> None:
        if not hasattr(self, "memory_table"):
            return
        self._visible_memories = []
        self._syncing_memory_selection = True
        self.memory_table.blockSignals(True)
        self.memory_table.clearContents()
        self.memory_table.setRowCount(1)
        item = QTableWidgetItem(text)
        item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsSelectable)
        self.memory_table.setItem(0, 1, item)
        self.memory_table.setItem(0, 0, QTableWidgetItem(""))
        self.memory_table.setItem(0, 2, QTableWidgetItem(""))
        self.memory_table.setItem(0, 3, QTableWidgetItem(""))
        self.memory_table.blockSignals(False)
        self._syncing_memory_selection = False
        self._sync_memory_bulk_actions()

    def _handle_memory_item_clicked(self, item: QTableWidgetItem) -> None:
        if self._syncing_memory_selection:
            return
        if self._memory_editor_mode == "new" and self.memory_new_button.isChecked():
            self.memory_new_button.setChecked(False)
        row = item.row()
        if row < 0 or row >= len(self._visible_memories):
            return
        memory_id = str(self._visible_memories[row].get("id", ""))
        if not memory_id:
            return
        if item.column() == 0:
            self._set_memory_checked(row, memory_id not in self._selected_memory_ids)
            return
        self._switch_memory_single_selection(row)

    def _handle_memory_checkbox_state_changed(self, memory_id: str, checked: bool) -> None:
        if self._syncing_memory_selection:
            return
        if self._memory_editor_mode == "new" and self.memory_new_button.isChecked():
            self.memory_new_button.setChecked(False)
        row = self._visible_memory_row_by_id(memory_id)
        if row is None:
            return
        self._set_memory_checked(row, checked)

    def _switch_memory_single_selection(self, row: int) -> None:
        if row < 0 or row >= len(self._visible_memories):
            return
        memory_id = str(self._visible_memories[row].get("id", ""))
        if not memory_id:
            return
        self._selected_memory_ids = {memory_id}
        self._refresh_memory_table()
        self._open_memory_editor(row)

    def _handle_memory_select_all_check_changed(self, state: int) -> None:
        if self._syncing_memory_selection:
            return
        checked = state == Qt.CheckState.Checked.value
        self._set_all_visible_memories_checked(checked)

    def _set_memory_checked(self, row: int, checked: bool) -> None:
        if row < 0 or row >= len(self._visible_memories):
            return
        memory_id = str(self._visible_memories[row].get("id", ""))
        if not memory_id:
            return
        if checked:
            self._selected_memory_ids.add(memory_id)
        else:
            self._selected_memory_ids.discard(memory_id)

        item = self.memory_table.item(row, 0)
        if item is not None:
            self.memory_table.blockSignals(True)
            self.memory_table.blockSignals(False)
        self._sync_memory_checkbox_widget(row, checked)
        self._apply_memory_row_checked_style(row, checked)
        self._sync_memory_bulk_actions()

    def _open_memory_editor(self, row: int) -> None:
        if row < 0 or row >= len(self._visible_memories):
            return
        if self._memory_editor_mode == "new" and self.memory_new_button.isChecked():
            self.memory_new_button.setChecked(False)
        memory = self._visible_memories[row]
        memory_id = str(memory.get("id", ""))
        if not memory_id:
            return
        self._memory_editor_mode = "edit"
        self._editing_memory_id = memory_id
        self._active_memory_id = memory_id
        self.memory_content_edit.setPlainText(str(memory.get("content", "")))
        self.memory_content_edit.setPlaceholderText("编辑长期记忆内容")
        self.memory_save_button.setText("保存修改")
        self.memory_editor_container.setVisible(True)
        self.memory_preview_label.setText("")

    def _set_all_visible_memories_checked(self, checked: bool) -> None:
        visible_ids = {
            str(memory.get("id", ""))
            for memory in self._visible_memories
            if str(memory.get("id", ""))
        }
        if not visible_ids:
            return
        if checked:
            self._selected_memory_ids |= visible_ids
        else:
            self._selected_memory_ids -= visible_ids
        self._refresh_memory_table()

    def _toggle_select_all_visible_memories(self) -> None:
        visible_ids = {
            str(memory.get("id", ""))
            for memory in self._visible_memories
            if str(memory.get("id", ""))
        }
        if not visible_ids:
            return
        self._set_all_visible_memories_checked(
            not visible_ids.issubset(self._selected_memory_ids)
        )

    def _visible_memory_row_by_id(self, memory_id: str) -> int | None:
        for row, memory in enumerate(self._visible_memories):
            if str(memory.get("id", "")) == memory_id:
                return row
        return None

    def _set_memory_checkbox_widget(self, row: int, memory_id: str, checked: bool) -> None:
        container = QWidget(self.memory_table)
        layout = QHBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        checkbox = QCheckBox(container)
        checkbox.setChecked(checked)
        checkbox.setToolTip("选择这条记忆")
        checkbox.stateChanged.connect(
            lambda state, current_id=memory_id: self._handle_memory_checkbox_state_changed(
                current_id,
                state == Qt.CheckState.Checked.value,
            )
        )
        layout.addWidget(checkbox, 0, Qt.AlignmentFlag.AlignCenter)
        container.setLayout(layout)
        self.memory_table.setCellWidget(row, 0, container)
        self._style_memory_checkbox_container(container, row, checked)

    def _sync_memory_checkbox_widget(self, row: int, checked: bool) -> None:
        container = self.memory_table.cellWidget(row, 0)
        if container is None:
            return
        checkbox = container.findChild(QCheckBox)
        if checkbox is not None:
            checkbox.blockSignals(True)
            checkbox.setChecked(checked)
            checkbox.blockSignals(False)
        self._style_memory_checkbox_container(container, row, checked)

    def _style_memory_checkbox_container(self, container: QWidget, row: int, checked: bool) -> None:
        color = _memory_row_background_color(row, checked)
        container.setStyleSheet(f"background: {color};")

    def _sync_memory_select_all_check_geometry(self) -> None:
        if not hasattr(self, "memory_select_all_check"):
            return
        header = self.memory_table.horizontalHeader()
        checkbox_size = self.memory_select_all_check.sizeHint()
        section_x = header.sectionViewportPosition(0)
        section_width = header.sectionSize(0)
        x = section_x + max(0, (section_width - checkbox_size.width()) // 2)
        y = max(0, (header.height() - checkbox_size.height()) // 2)
        self.memory_select_all_check.setGeometry(
            x,
            y,
            checkbox_size.width(),
            checkbox_size.height(),
        )
        self.memory_select_all_check.raise_()

    def _toggle_memory_new_editor(self, checked: bool) -> None:
        if not hasattr(self, "memory_editor_container"):
            return
        if checked:
            self._clear_memory_selection()
            self._memory_editor_mode = "new"
            self._editing_memory_id = None
            self._active_memory_id = None
            self.memory_content_edit.clear()
            self.memory_content_edit.setPlaceholderText("新增长期记忆内容")
            self.memory_save_button.setText("保存")
            self.memory_preview_label.setText("正在新增记忆")
            self.memory_editor_container.setVisible(True)
        elif self._memory_editor_mode == "new":
            self._memory_editor_mode = None
            self._editing_memory_id = None
            self._active_memory_id = None
            self._clear_memory_editor()
            self.memory_editor_container.setVisible(False)
            self._sync_memory_bulk_actions()
        self.memory_new_button.setText("收起新增" if checked else "新增记忆")

    def _clear_memory_selection(self) -> None:
        if not hasattr(self, "memory_table"):
            return
        self._selected_memory_ids.clear()
        self._refresh_memory_table()

    def _sync_memory_bulk_actions(self) -> None:
        if not hasattr(self, "memory_table"):
            return
        selected_memories = self._selected_memories()
        selected_count = len(selected_memories)
        visible_ids = {
            str(memory.get("id", ""))
            for memory in self._visible_memories
            if str(memory.get("id", ""))
        }
        all_visible_selected = bool(visible_ids) and visible_ids.issubset(self._selected_memory_ids)

        self.memory_selection_label.setText(f"已选择 {selected_count} 条")
        self.memory_select_all_check.setEnabled(bool(visible_ids))
        self.memory_select_all_check.blockSignals(True)
        self.memory_select_all_check.setChecked(all_visible_selected)
        self.memory_select_all_check.blockSignals(False)
        self.memory_delete_button.setEnabled(selected_count > 0)
        self.memory_clear_selection_button.setEnabled(selected_count > 0)

        if self._memory_editor_mode != "new":
            self.memory_preview_label.setText("")

    def _apply_memory_row_checked_style(self, row: int, checked: bool) -> None:
        brush = _memory_row_background(row, checked)
        for column in range(self.memory_table.columnCount()):
            item = self.memory_table.item(row, column)
            if item is not None:
                item.setBackground(brush)
        container = self.memory_table.cellWidget(row, 0)
        if container is not None:
            self._style_memory_checkbox_container(container, row, checked)

    def _clear_memory_editor(self) -> None:
        if not hasattr(self, "memory_content_edit"):
            return
        self.memory_content_edit.clear()

    def _save_memory_entry(self) -> None:
        if self.memory_store is None:
            return
        content = self.memory_content_edit.toPlainText().strip()
        if not content:
            QMessageBox.warning(self, "内容为空", "记忆内容不能为空。")
            return
        try:
            if self._memory_editor_mode == "edit" and self._editing_memory_id:
                editing_id = self._editing_memory_id
                self.memory_store.update_memory(
                    {"id": editing_id, "content": content, "source": "manual"},
                    allow_sensitive=True,
                )
                self._selected_memory_ids = {editing_id}
                self._active_memory_id = editing_id
                success_message = "记忆已更新。"
            else:
                self.memory_store.create_memory(
                    {"content": content, "source": "manual"},
                    allow_sensitive=True,
                )
                self._memory_editor_mode = None
                self._editing_memory_id = None
                self._active_memory_id = None
                self._clear_memory_editor()
                self.memory_new_button.setChecked(False)
                success_message = "记忆已保存。"
        except (RuntimeError, ValueError) as exc:
            QMessageBox.warning(self, "保存失败", str(exc))
            return
        self._load_memory_entries()
        QMessageBox.information(self, "保存成功", success_message)

    def _delete_memory_entry(self) -> None:
        if self.memory_store is None:
            return
        memories = self._selected_memories()
        if not memories:
            QMessageBox.information(self, "未选择", "请先选择要删除的记忆。")
            return
        result = QMessageBox.question(
            self,
            "删除记忆",
            f"确定要删除选中的 {len(memories)} 条长期记忆吗？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if result != QMessageBox.StandardButton.Yes:
            return
        failed: list[str] = []
        deleted = 0
        for memory in memories:
            memory_id = str(memory.get("id", "")).strip()
            if not memory_id:
                failed.append("缺少记忆 ID")
                continue
            try:
                self.memory_store.forget_memory({"id": memory_id})
            except (RuntimeError, ValueError) as exc:
                failed.append(f"{_compact_memory_id(memory_id)}：{exc}")
            else:
                deleted += 1
        if self._editing_memory_id in self._selected_memory_ids:
            self._memory_editor_mode = None
            self._editing_memory_id = None
            self._active_memory_id = None
            self._clear_memory_editor()
            self.memory_editor_container.setVisible(False)
        self._clear_memory_selection()
        self._load_memory_entries()
        if failed:
            QMessageBox.warning(
                self,
                "删除完成",
                f"已删除 {deleted} 条，失败 {len(failed)} 条。\n" + "\n".join(failed),
            )

    def _selected_memory_rows(self) -> list[int]:
        if not hasattr(self, "memory_table"):
            return []
        return [
            row
            for row, memory in enumerate(self._visible_memories)
            if str(memory.get("id", "")) in self._selected_memory_ids
        ]

    def _selected_memories(self) -> list[dict[str, object]]:
        return [
            memory
            for memory in self._all_memories
            if str(memory.get("id", "")) in self._selected_memory_ids
        ]

    def _selected_memory(self) -> dict[str, object] | None:
        memories = self._selected_memories()
        if not memories:
            return None
        return memories[0]

    def accept(self) -> None:
        if self._api_test_thread is not None:
            QMessageBox.information(self, "测试中", "API 测试仍在进行，请等待完成后再保存设置。")
            return
        if self._tts_test_thread is not None:
            QMessageBox.information(self, "检测中", "TTS 服务检测仍在进行，请等待完成后再保存设置。")
            return
        if self._character_export_thread is not None:
            QMessageBox.information(self, "导出中", "角色包导出仍在进行，请等待完成后再保存设置。")
            return

        accept_values = self._collect_accept_values()
        if accept_values is None:
            return
        tts_settings = accept_values["tts_settings"]
        if isinstance(tts_settings, GPTSoVITSTTSSettings) and tts_settings.enabled:
            self._start_tts_settings_test(tts_settings, accept_values)
            return

        self._complete_accept(accept_values)

    def _collect_accept_values(self) -> dict[str, object] | None:
        api_settings = self._validated_api_settings()
        if api_settings is None:
            return None
        tts_settings = self._validated_tts_settings()
        if tts_settings is None:
            return None
        character_id = self._selected_character_id()
        if character_id is None:
            QMessageBox.warning(self, "配置无效", "请先导入并选择一个角色包。")
            return None

        subtitle_typing_interval_ms, reply_segment_pause_ms = normalize_subtitle_display_speed(
            self.subtitle_typing_interval_spin.value(),
            self.reply_segment_pause_spin.value(),
        )
        return {
            "api_settings": api_settings,
            "tts_settings": tts_settings,
            "character_id": character_id,
            "portrait_scale_percent": self._selected_portrait_scale_percent(),
            "subtitle_typing_interval_ms": subtitle_typing_interval_ms,
            "reply_segment_pause_ms": reply_segment_pause_ms,
            "proactive_care_settings": ProactiveCareSettings(
                enabled=self.proactive_screen_context_enabled_check.isChecked(),
                screen_context_enabled=self.proactive_screen_context_enabled_check.isChecked(),
                check_interval_minutes=self.proactive_check_interval_spin.value(),
                cooldown_minutes=self.proactive_cooldown_spin.value(),
                screen_context_batch_limit=self.proactive_batch_limit_spin.value(),
            ),
            "mcp_settings": MCPRuntimeSettings(windows_enabled=False),
            "debug_log_settings": DebugLogSettings(
                enabled=self.debug_log_enabled_check.isChecked(),
                body_enabled=(
                    self.debug_log_enabled_check.isChecked()
                    and self.debug_body_enabled_check.isChecked()
                ),
                file_enabled=self.debug_file_enabled_check.isChecked(),
            ),
        }

    def _complete_accept(self, values: dict[str, object]) -> None:
        api_settings = values["api_settings"]
        tts_settings = values["tts_settings"]
        character_id = values["character_id"]
        portrait_scale_percent = values["portrait_scale_percent"]
        subtitle_typing_interval_ms = values["subtitle_typing_interval_ms"]
        reply_segment_pause_ms = values["reply_segment_pause_ms"]
        proactive_care_settings = values["proactive_care_settings"]
        mcp_settings = values["mcp_settings"]
        debug_log_settings = values["debug_log_settings"]

        if not isinstance(api_settings, ApiSettings):
            return
        if not isinstance(tts_settings, GPTSoVITSTTSSettings):
            return
        if not isinstance(character_id, str):
            return
        if not isinstance(portrait_scale_percent, int):
            return
        if not isinstance(subtitle_typing_interval_ms, int):
            return
        if not isinstance(reply_segment_pause_ms, int):
            return
        if not isinstance(proactive_care_settings, ProactiveCareSettings):
            return
        if not isinstance(mcp_settings, MCPRuntimeSettings):
            return
        if not isinstance(debug_log_settings, DebugLogSettings):
            return

        self.result_api_settings = api_settings
        self.result_tts_settings = tts_settings
        self.result_character_id = character_id
        self.result_portrait_scale_percent = portrait_scale_percent
        self.result_subtitle_typing_interval_ms = subtitle_typing_interval_ms
        self.result_reply_segment_pause_ms = reply_segment_pause_ms
        self.result_proactive_care_settings = proactive_care_settings
        self.result_mcp_settings = mcp_settings
        self.result_debug_log_settings = debug_log_settings
        super().accept()

    def reject(self) -> None:
        if self._api_test_thread is not None:
            QMessageBox.information(self, "测试中", "API 测试仍在进行，请等待完成后再关闭设置。")
            return
        if self._tts_test_thread is not None:
            QMessageBox.information(self, "检测中", "TTS 服务检测仍在进行，请等待完成后再关闭设置。")
            return
        if self._character_export_thread is not None:
            QMessageBox.information(self, "导出中", "角色包导出仍在进行，请等待完成后再关闭设置。")
            return
        super().reject()

    def closeEvent(self, event):  # type: ignore[no-untyped-def]
        if self._tts_test_thread is not None:
            QMessageBox.information(self, "检测中", "TTS 服务检测仍在进行，请等待完成后再关闭设置。")
            event.ignore()
            return
        if self._character_export_thread is not None:
            QMessageBox.information(self, "导出中", "角色包导出仍在进行，请等待完成后再关闭设置。")
            event.ignore()
            return
        super().closeEvent(event)

    def _test_api_settings(self) -> None:
        settings = self._validated_api_settings()
        if settings is None or self._api_test_thread is not None:
            return

        self.api_test_button.setEnabled(False)
        self.api_test_button.setText("测试中...")

        thread = QThread(self)
        worker = ApiConnectionTestWorker(settings)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.succeeded.connect(self._handle_api_test_success)
        worker.failed.connect(self._handle_api_test_failed)
        worker.finished.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(self._reset_api_test_state)

        self._api_test_thread = thread
        self._api_test_worker = worker
        thread.start()

    @Slot(str)
    def _handle_api_test_success(self, message: str) -> None:
        QMessageBox.information(self, "测试成功", f"API 连接成功，模型返回：{message}")

    @Slot(str)
    def _handle_api_test_failed(self, message: str) -> None:
        QMessageBox.warning(self, "测试失败", message)

    @Slot()
    def _reset_api_test_state(self) -> None:
        self.api_test_button.setEnabled(True)
        self.api_test_button.setText("测试 API")
        self._api_test_thread = None
        self._api_test_worker = None

    def _start_tts_settings_test(
        self,
        settings: GPTSoVITSTTSSettings,
        accept_values: dict[str, object],
    ) -> None:
        if self._tts_test_thread is not None:
            return

        self._pending_accept_values = dict(accept_values)
        self._set_tts_test_busy(True)

        thread = QThread(self)
        worker = TTSTestWorker(settings)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.succeeded.connect(self._handle_tts_test_success)
        worker.failed.connect(self._handle_tts_test_failed)
        worker.finished.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(self._reset_tts_test_state)

        self._tts_test_thread = thread
        self._tts_test_worker = worker
        thread.start()

    @Slot(object, str)
    def _handle_tts_test_success(
        self,
        settings: object,
        _message: str,
    ) -> None:
        accept_values = self._pending_accept_values
        if accept_values is None:
            return
        if isinstance(settings, GPTSoVITSTTSSettings):
            accept_values["tts_settings"] = settings
        self._complete_accept(accept_values)

    @Slot(str)
    def _handle_tts_test_failed(self, message: str) -> None:
        accept_values = self._pending_accept_values
        if accept_values is None:
            return
        original_settings = accept_values.get("tts_settings")
        if not isinstance(original_settings, GPTSoVITSTTSSettings):
            return

        QMessageBox.warning(
            self,
            "TTS 检测失败",
            f"{message}\n\n已自动关闭 TTS，并继续保存其他设置。",
        )
        self.tts_enabled_check.setChecked(False)
        accept_values["tts_settings"] = replace(original_settings, enabled=False)
        self._complete_accept(accept_values)

    @Slot()
    def _reset_tts_test_state(self) -> None:
        self._tts_test_thread = None
        self._tts_test_worker = None
        self._pending_accept_values = None
        self._set_tts_test_busy(False)

    def _set_tts_test_busy(self, busy: bool) -> None:
        if not hasattr(self, "button_box"):
            return
        save_button = self.button_box.button(QDialogButtonBox.StandardButton.Save)
        cancel_button = self.button_box.button(QDialogButtonBox.StandardButton.Cancel)
        if save_button is not None:
            if busy:
                self._save_button_text = save_button.text()
                save_button.setText("检测 TTS...")
            elif self._save_button_text is not None:
                save_button.setText(self._save_button_text)
                self._save_button_text = None
            save_button.setEnabled(not busy)
        if cancel_button is not None:
            cancel_button.setEnabled(not busy)

    def _download_gpt_sovits_bundle(self) -> None:
        dialog = TTSBundleDownloadDialog(self.base_dir, self)
        if dialog.exec() != QDialog.DialogCode.Accepted or dialog.downloaded_work_dir is None:
            return
        provider = getattr(dialog, "downloaded_provider", None) or TTS_PROVIDER_GPT_SOVITS
        provider_index = self.tts_provider_combo.findData(provider)
        if provider_index >= 0:
            self.tts_provider_combo.setCurrentIndex(provider_index)
        self.tts_work_dir_edit.setText(str(dialog.downloaded_work_dir))
        self.tts_api_url_edit.setText(_default_tts_api_url(provider))
        self.tts_enabled_check.setChecked(True)
        self._sync_tts_provider_controls()

    @Slot()
    def _sync_tts_provider_controls(self, *, apply_defaults: bool = False) -> None:
        provider = str(self.tts_provider_combo.currentData() or TTS_PROVIDER_GPT_SOVITS)
        self.tts_api_url_edit.setPlaceholderText(_default_tts_api_url(provider))
        if provider == TTS_PROVIDER_GENIE:
            self.tts_work_dir_edit.setPlaceholderText("data/tts_bundles/installed/genie_tts_server/Genie-TTS Server")
        elif provider == TTS_PROVIDER_CUSTOM_GPT_SOVITS:
            self.tts_work_dir_edit.setPlaceholderText("外部 GPT-SoVITS 工作目录，可留空")
        else:
            self.tts_work_dir_edit.setPlaceholderText("data/tts_bundles/installed/gpt_sovits_nvidia50/GPT-SoVITS-v2pro-20250604-nvidia50")
        bundled = _is_bundled_tts_provider(provider)
        self.tts_api_url_edit.setReadOnly(bundled)
        self.tts_work_dir_edit.setReadOnly(bundled)
        if bundled and apply_defaults:
            self.tts_api_url_edit.setText(_default_tts_api_url(provider))
            work_dir = default_provider_bundle_work_dir(provider, self.base_dir)
            self.tts_work_dir_edit.setText(str(work_dir or ""))
        elif provider == TTS_PROVIDER_CUSTOM_GPT_SOVITS and apply_defaults:
            work_dir = _optional_path(self.tts_work_dir_edit.text(), self.base_dir)
            if work_dir is not None and is_provider_bundle_work_dir(work_dir, self.base_dir):
                self.tts_work_dir_edit.clear()

    def _import_character_archive(self) -> None:
        if self._character_export_thread is not None:
            QMessageBox.information(self, "导出中", "角色包导出仍在进行，请等待完成后再导入。")
            return
        path_text, _ = QFileDialog.getOpenFileName(
            self,
            "导入 Sakura 角色包",
            str(self.base_dir),
            "Sakura 角色包 (*.char)",
        )
        if not path_text:
            return
        try:
            result = import_character_archive(Path(path_text), self.base_dir)
            self.character_registry = CharacterRegistry(self.base_dir)
            self._refresh_character_combo(result.character_id)
            self._sync_character_archive_controls()
        except (CharacterArchiveError, OSError, ValueError) as exc:
            QMessageBox.warning(self, "导入失败", str(exc))
            return
        QMessageBox.information(
            self,
            "导入成功",
            f"已导入角色「{result.display_name}」。点击保存后会切换到该角色。",
        )

    def _export_current_character_archive(self) -> None:
        if self._character_export_thread is not None:
            return
        profile = self._selected_character_profile()
        if profile is None:
            QMessageBox.warning(self, "导出失败", "当前没有可导出的角色。")
            return
        output_text, _ = QFileDialog.getSaveFileName(
            self,
            "导出 Sakura 角色包",
            str(self.base_dir / f"{profile.id}.char"),
            "Sakura 角色包 (*.char)",
        )
        if not output_text:
            return
        output_path = Path(output_text)
        if output_path.suffix.lower() != ".char":
            output_path = output_path.with_suffix(".char")
        self._start_character_archive_export(profile, output_path)

    def _start_character_archive_export(
        self,
        profile: CharacterProfile,
        output_path: Path,
    ) -> None:
        self._set_character_export_busy(True)
        thread = QThread(self)
        worker = CharacterArchiveExportWorker(profile, output_path)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.succeeded.connect(self._handle_character_export_success)
        worker.failed.connect(self._handle_character_export_failed)
        worker.finished.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(self._reset_character_export_state)

        self._character_export_thread = thread
        self._character_export_worker = worker
        thread.start()

    @Slot(str)
    def _handle_character_export_success(self, output_path: str) -> None:
        QMessageBox.information(self, "导出成功", f"角色包已导出到：{output_path}")

    @Slot(str)
    def _handle_character_export_failed(self, message: str) -> None:
        QMessageBox.warning(self, "导出失败", message)

    @Slot()
    def _reset_character_export_state(self) -> None:
        self._character_export_thread = None
        self._character_export_worker = None
        self._set_character_export_busy(False)

    def _set_character_export_busy(self, busy: bool) -> None:
        if hasattr(self, "button_box"):
            save_button = self.button_box.button(QDialogButtonBox.StandardButton.Save)
            cancel_button = self.button_box.button(QDialogButtonBox.StandardButton.Cancel)
            if save_button is not None:
                save_button.setEnabled(not busy)
            if cancel_button is not None:
                cancel_button.setEnabled(not busy)
        if hasattr(self, "character_import_button"):
            self.character_import_button.setEnabled(not busy)
        if hasattr(self, "character_export_button"):
            self.character_export_button.setEnabled(
                not busy and self._selected_character_profile() is not None
            )

    def _sync_character_archive_controls(self) -> None:
        self._set_character_export_busy(self._character_export_thread is not None)

    def _validated_api_settings(self) -> ApiSettings | None:
        base_url = self.base_url_edit.text().strip().rstrip("/")
        api_key = self.api_key_edit.text().strip()
        model = self.model_edit.text().strip()

        if not _is_http_url(base_url):
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
            timeout_seconds=self.api_timeout_spin.value(),
        )

    def _validated_tts_settings(self) -> GPTSoVITSTTSSettings | None:
        enabled = self.tts_enabled_check.isChecked()
        provider = str(self.tts_provider_combo.currentData() or TTS_PROVIDER_GPT_SOVITS)
        api_url = self.tts_api_url_edit.text().strip()
        work_dir = _optional_path(self.tts_work_dir_edit.text(), self.base_dir)
        ref_lang = self.ref_lang_edit.text().strip()
        text_lang = self.text_lang_edit.text().strip()

        if enabled and not _is_http_url(api_url):
            QMessageBox.warning(self, "配置无效", "TTS API URL 必须是有效的 http 或 https 地址。")
            return None

        selected_profile = self._selected_character_profile()
        if selected_profile is not None:
            settings = GPTSoVITSTTSSettings.from_character_profile(
                character_profile=selected_profile,
                enabled=enabled,
                api_url=api_url,
                ref_lang=ref_lang,
                text_lang=text_lang,
                timeout_seconds=self.tts_timeout_spin.value(),
                provider=provider,
                work_dir=work_dir,
                onnx_model_dir=_default_genie_onnx_dir(self.base_dir, selected_profile) if provider == TTS_PROVIDER_GENIE else None,
                validate_enabled=False,
            )
        else:
            settings = GPTSoVITSTTSSettings(
                enabled=enabled,
                api_url=api_url,
                ref_audio_path=self.tts_settings.ref_audio_path,
                ref_text_path=self.tts_settings.ref_text_path,
                ref_text=self.tts_settings.ref_text,
                provider=provider,
                gpt_model_path=self.tts_settings.gpt_model_path,
                sovits_model_path=self.tts_settings.sovits_model_path,
                work_dir=work_dir,
                character_name=self.tts_settings.character_name or "sakura",
                onnx_model_dir=(
                    self.tts_settings.onnx_model_dir or _default_genie_onnx_dir(self.base_dir, selected_profile)
                    if provider == TTS_PROVIDER_GENIE
                    else None
                ),
                ref_lang=ref_lang,
                text_lang=text_lang,
                timeout_seconds=self.tts_timeout_spin.value(),
                tone_references=self.tts_settings.tone_references,
            )
        if enabled:
            try:
                settings.validate()
            except TTSConfigError as exc:
                QMessageBox.warning(self, "配置无效", str(exc))
                return None
        return settings

    def _selected_character_id(self) -> str | None:
        if self.character_registry is None or not hasattr(self, "character_combo"):
            return self.current_character.id if self.current_character is not None else None
        character_id = self.character_combo.currentData()
        if isinstance(character_id, str) and character_id.strip():
            return character_id.strip()
        return self.current_character.id if self.current_character is not None else None

    def _selected_character_profile(self) -> CharacterProfile | None:
        character_id = self._selected_character_id()
        if character_id is None or self.character_registry is None:
            return self.current_character
        return self.character_registry.get(character_id)

    def _selected_portrait_scale_percent(self) -> int:
        if hasattr(self, "portrait_scale_spin"):
            return normalize_portrait_scale_percent(self.portrait_scale_spin.value())
        return self.portrait_scale_percent

    def _refresh_character_combo(self, selected_character_id: str | None = None) -> None:
        if not hasattr(self, "character_combo"):
            return
        selected_id = selected_character_id or self._selected_character_id()
        self.character_combo.blockSignals(True)
        self.character_combo.clear()
        selected_index = -1
        profiles = list(self.character_registry.all()) if self.character_registry is not None else []
        for profile in profiles:
            self.character_combo.addItem(profile.display_name, profile.id)
            if profile.id == selected_id:
                selected_index = self.character_combo.count() - 1
        if selected_index >= 0:
            self.character_combo.setCurrentIndex(selected_index)
        elif self.character_combo.count() > 0:
            self.character_combo.setCurrentIndex(0)
        else:
            self.character_combo.addItem("尚未导入角色", None)
        has_character = bool(profiles)
        self.character_combo.setEnabled(has_character)
        if hasattr(self, "character_empty_label"):
            self.character_empty_label.setVisible(not has_character)
        self.character_combo.blockSignals(False)
        self._sync_character_archive_controls()


def _is_http_url(url: str) -> bool:
    parsed_url = urlparse(url)
    return parsed_url.scheme in {"http", "https"} and bool(parsed_url.netloc)


def _default_tts_api_url(provider: str) -> str:
    return DEFAULT_GENIE_TTS_API_URL if provider == TTS_PROVIDER_GENIE else DEFAULT_GPT_SOVITS_API_URL


def _is_bundled_tts_provider(provider: str) -> bool:
    return provider in {TTS_PROVIDER_GPT_SOVITS, TTS_PROVIDER_GENIE}


def _default_genie_onnx_dir(base_dir: Path, profile: CharacterProfile | None) -> Path:
    character_id = profile.id if profile is not None else "default"
    return base_dir / "data" / "tts_bundles" / "onnx" / character_id


def _optional_path(value: str, base_dir: Path) -> Path | None:
    text = value.strip().strip('"').strip("'")
    if not text:
        return None
    path = Path(text)
    if path.is_absolute():
        return path
    return base_dir / path


def _compact_memory_id(memory_id: str) -> str:
    if len(memory_id) <= 16:
        return memory_id
    return f"{memory_id[:8]}...{memory_id[-4:]}"


def _memory_row_background(row: int, checked: bool) -> QBrush:
    return QBrush(QColor(_memory_row_background_color(row, checked)))


def _memory_row_background_color(row: int, checked: bool) -> str:
    if checked:
        return "#f4c4da"
    if row % 2:
        return "#fff4f9"
    return "#fffafd"


def _format_memory_time(value: str) -> str:
    text = value.strip()
    if not text:
        return ""
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        legacy_text = text.replace("T", " ").replace("Z", "")
        for separator in ("+", "."):
            legacy_text = legacy_text.split(separator, 1)[0]
        return legacy_text
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone()
    return parsed.strftime("%Y-%m-%d %H:%M:%S")
