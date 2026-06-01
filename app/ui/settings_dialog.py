from __future__ import annotations

from pathlib import Path
from urllib.parse import urlparse

from PySide6.QtCore import QObject, Qt, QThread, Signal, Slot
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
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
from app.agent.mcp import MCPRuntimeSettings
from app.config.settings_service import DebugLogSettings
from app.llm.api_client import ApiSettings, OpenAICompatibleClient
from app.config.character_loader import CharacterProfile, CharacterRegistry
from app.ui.portrait_controller import (
    PORTRAIT_SCALE_DEFAULT_PERCENT,
    PORTRAIT_SCALE_MAX_PERCENT,
    PORTRAIT_SCALE_MIN_PERCENT,
    normalize_portrait_scale_percent,
)
from app.proactive_care import (
    PROACTIVE_MAX_COOLDOWN_MINUTES,
    PROACTIVE_MAX_CHECK_INTERVAL_MINUTES,
    PROACTIVE_MAX_SCREEN_CONTEXT_BATCH_LIMIT,
    PROACTIVE_MIN_COOLDOWN_MINUTES,
    PROACTIVE_MIN_CHECK_INTERVAL_MINUTES,
    PROACTIVE_MIN_SCREEN_CONTEXT_BATCH_LIMIT,
    ProactiveCareSettings,
)
from app.voice.tts import GPTSoVITSTTSSettings, TTSConfigError
from sdk.types import ToolsTabContribution


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
    ) -> None:
        super().__init__(parent)
        self.base_dir = base_dir
        self.tts_settings = tts_settings
        self.character_registry = character_registry
        self.current_character = current_character
        self.portrait_scale_percent = normalize_portrait_scale_percent(portrait_scale_percent)
        self.memory_store = memory_store
        self._visible_memories: list[dict[str, object]] = []
        self.result_api_settings: ApiSettings | None = None
        self.result_tts_settings: GPTSoVITSTTSSettings | None = None
        self.result_character_id: str | None = None
        self.result_portrait_scale_percent: int | None = None
        self.result_proactive_care_settings: ProactiveCareSettings | None = None
        self.result_mcp_settings: MCPRuntimeSettings | None = None
        self.result_debug_log_settings: DebugLogSettings | None = None
        self._api_test_thread: QThread | None = None
        self._api_test_worker: ApiConnectionTestWorker | None = None

        self.setWindowTitle("设置")
        self.resize(560, 400)

        tabs = QTabWidget(self)
        if character_registry is not None and current_character is not None:
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
        character_registry: CharacterRegistry,
        current_character: CharacterProfile,
    ) -> QWidget:
        tab = QWidget(self)
        self.character_combo = QComboBox(tab)
        for profile in character_registry.all():
            self.character_combo.addItem(profile.display_name, profile.id)
            if profile.id == current_character.id:
                self.character_combo.setCurrentIndex(self.character_combo.count() - 1)

        form_layout = QFormLayout()
        form_layout.setContentsMargins(16, 18, 16, 16)
        form_layout.setSpacing(12)
        form_layout.addRow("当前角色", self.character_combo)
        form_layout.addRow("立绘大小", self._build_portrait_scale_control(tab))
        tab.setLayout(form_layout)
        return tab

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
        self.tts_enabled_check = QCheckBox("启用 GPT-SoVITS 语音", tab)
        self.tts_enabled_check.setChecked(settings.enabled)

        self.tts_api_url_edit = QLineEdit(settings.api_url, tab)
        self.tts_api_url_edit.setPlaceholderText("http://127.0.0.1:9880/tts")

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
        form_layout.addRow("API URL", self.tts_api_url_edit)
        form_layout.addRow("参考语言", self.ref_lang_edit)
        form_layout.addRow("文本语言", self.text_lang_edit)
        form_layout.addRow("超时", self.tts_timeout_spin)
        tab.setLayout(form_layout)
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
        self.windows_mcp_enabled_check.setChecked(settings.windows_enabled)

        restart_hint = QLabel(
            "保存后需要重启 Sakura 才会加载或卸载 Windows MCP 工具。",
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

        form_layout = QFormLayout()
        form_layout.setContentsMargins(16, 18, 16, 16)
        form_layout.setSpacing(12)
        form_layout.addRow("", self.debug_log_enabled_check)
        form_layout.addRow("", self.debug_body_enabled_check)
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

        self.memory_table = QTableWidget(0, 3, tab)
        self.memory_table.setHorizontalHeaderLabels(["内容", "ID", "更新时间"])
        self.memory_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.memory_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.memory_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.memory_table.verticalHeader().setVisible(False)

        self.memory_content_edit = QTextEdit(tab)
        self.memory_content_edit.setPlaceholderText("新增长期记忆内容")
        self.memory_content_edit.setFixedHeight(92)

        self.memory_new_button = QPushButton("新增", tab)
        self.memory_new_button.clicked.connect(self._clear_memory_editor)
        self.memory_save_button = QPushButton("添加记忆", tab)
        self.memory_save_button.clicked.connect(self._save_memory_entry)
        self.memory_delete_button = QPushButton("删除", tab)
        self.memory_delete_button.clicked.connect(self._delete_memory_entry)
        self.memory_refresh_button = QPushButton("刷新", tab)
        self.memory_refresh_button.clicked.connect(self._refresh_memory_table)

        filter_layout = QHBoxLayout()
        filter_layout.addWidget(self.memory_search_edit, 1)

        editor_layout = QFormLayout()
        editor_layout.setSpacing(8)
        editor_layout.addRow("内容", self.memory_content_edit)

        button_layout = QHBoxLayout()
        button_layout.addWidget(self.memory_new_button)
        button_layout.addWidget(self.memory_save_button)
        button_layout.addWidget(self.memory_delete_button)
        button_layout.addStretch(1)
        button_layout.addWidget(self.memory_refresh_button)

        layout = QVBoxLayout()
        layout.setContentsMargins(16, 18, 16, 16)
        layout.setSpacing(10)
        layout.addLayout(filter_layout)
        layout.addWidget(self.memory_table, 1)
        layout.addWidget(QLabel("编辑记忆", tab))
        layout.addLayout(editor_layout)
        layout.addLayout(button_layout)
        tab.setLayout(layout)

        self._show_memory_placeholder("点击“刷新”加载长期记忆。")
        self._clear_memory_editor()
        return tab

    def _refresh_memory_table(self) -> None:
        if self.memory_store is None or not hasattr(self, "memory_table"):
            return
        is_ready = getattr(self.memory_store, "is_ready", None)
        if callable(is_ready) and not is_ready():
            preload = getattr(self.memory_store, "preload", None)
            if callable(preload):
                preload(wait=False)
            self._show_memory_placeholder("长期记忆系统正在初始化。")
            return
        keyword = self.memory_search_edit.text().strip()
        try:
            if keyword:
                self._visible_memories = self.memory_store.search_memory(
                    {"query": keyword, "limit": 200}
                )["memories"]
            else:
                self._visible_memories = self.memory_store.list_memories(limit=200)
        except (RuntimeError, ValueError) as exc:
            QMessageBox.warning(self, "读取失败", str(exc))
            self._visible_memories = []
        self.memory_table.setRowCount(len(self._visible_memories))
        for row, memory in enumerate(self._visible_memories):
            values = [
                str(memory.get("content", "")),
                str(memory.get("id", "")),
                _format_memory_time(
                    str(memory.get("updated_at") or memory.get("created_at") or "")
                ),
            ]
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                if column == 1:
                    item.setData(Qt.ItemDataRole.UserRole, str(memory.get("id", "")))
                self.memory_table.setItem(row, column, item)
        self.memory_table.resizeColumnsToContents()

    def _show_memory_placeholder(self, text: str) -> None:
        if not hasattr(self, "memory_table"):
            return
        self._visible_memories = []
        self.memory_table.setRowCount(1)
        item = QTableWidgetItem(text)
        item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsSelectable)
        self.memory_table.setItem(0, 0, item)
        self.memory_table.setItem(0, 1, QTableWidgetItem(""))
        self.memory_table.setItem(0, 2, QTableWidgetItem(""))
        self.memory_table.resizeColumnsToContents()

    def _handle_memory_selection(self) -> None:
        return

    def _clear_memory_editor(self) -> None:
        if not hasattr(self, "memory_content_edit"):
            return
        self.memory_table.clearSelection()
        self.memory_content_edit.clear()

    def _save_memory_entry(self) -> None:
        if self.memory_store is None:
            return
        content = self.memory_content_edit.toPlainText().strip()
        if not content:
            QMessageBox.warning(self, "内容为空", "记忆内容不能为空。")
            return
        try:
            self.memory_store.create_memory(
                {"content": content, "source": "manual"},
                allow_sensitive=True,
            )
        except (RuntimeError, ValueError) as exc:
            QMessageBox.warning(self, "保存失败", str(exc))
            return
        self._clear_memory_editor()
        self._refresh_memory_table()
        QMessageBox.information(self, "保存成功", "记忆已保存。")

    def _delete_memory_entry(self) -> None:
        if self.memory_store is None:
            return
        memory = self._selected_memory()
        if memory is None:
            QMessageBox.information(self, "未选择", "请先选择一条记忆。")
            return
        result = QMessageBox.question(
            self,
            "删除记忆",
            "确定要删除选中的长期记忆吗？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if result != QMessageBox.StandardButton.Yes:
            return
        try:
            self.memory_store.forget_memory({"id": str(memory.get("id", ""))})
        except (RuntimeError, ValueError) as exc:
            QMessageBox.warning(self, "删除失败", str(exc))
            return
        self._refresh_memory_table()
        self._clear_memory_editor()

    def _selected_memory(self) -> dict[str, object] | None:
        if not hasattr(self, "memory_table"):
            return None
        selected_rows = self.memory_table.selectionModel().selectedRows()
        if not selected_rows:
            return None
        row = selected_rows[0].row()
        if row < 0 or row >= len(self._visible_memories):
            return None
        return self._visible_memories[row]

    def accept(self) -> None:
        if self._api_test_thread is not None:
            QMessageBox.information(self, "测试中", "API 测试仍在进行，请等待完成后再保存设置。")
            return

        api_settings = self._validated_api_settings()
        if api_settings is None:
            return
        tts_settings = self._validated_tts_settings()
        if tts_settings is None:
            return

        self.result_api_settings = api_settings
        self.result_tts_settings = tts_settings
        self.result_character_id = self._selected_character_id()
        self.result_portrait_scale_percent = self._selected_portrait_scale_percent()
        self.result_proactive_care_settings = ProactiveCareSettings(
            enabled=self.proactive_screen_context_enabled_check.isChecked(),
            screen_context_enabled=self.proactive_screen_context_enabled_check.isChecked(),
            check_interval_minutes=self.proactive_check_interval_spin.value(),
            cooldown_minutes=self.proactive_cooldown_spin.value(),
            screen_context_batch_limit=self.proactive_batch_limit_spin.value(),
        )
        self.result_mcp_settings = MCPRuntimeSettings(
            windows_enabled=self.windows_mcp_enabled_check.isChecked(),
        )
        self.result_debug_log_settings = DebugLogSettings(
            enabled=self.debug_log_enabled_check.isChecked(),
            body_enabled=(
                self.debug_log_enabled_check.isChecked()
                and self.debug_body_enabled_check.isChecked()
            ),
        )
        super().accept()

    def reject(self) -> None:
        if self._api_test_thread is not None:
            QMessageBox.information(self, "测试中", "API 测试仍在进行，请等待完成后再关闭设置。")
            return
        super().reject()

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
        api_url = self.tts_api_url_edit.text().strip()
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
                validate_enabled=False,
            )
        else:
            settings = GPTSoVITSTTSSettings(
                enabled=enabled,
                api_url=api_url,
                ref_audio_path=self.tts_settings.ref_audio_path,
                ref_text_path=self.tts_settings.ref_text_path,
                ref_text=self.tts_settings.ref_text,
                gpt_model_path=self.tts_settings.gpt_model_path,
                sovits_model_path=self.tts_settings.sovits_model_path,
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


def _is_http_url(url: str) -> bool:
    parsed_url = urlparse(url)
    return parsed_url.scheme in {"http", "https"} and bool(parsed_url.netloc)


def _format_memory_time(value: str) -> str:
    text = value.replace("T", " ").replace("Z", "")
    for separator in ("+", "."):
        text = text.split(separator, 1)[0]
    return text
