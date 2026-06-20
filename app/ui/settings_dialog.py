from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Callable, Literal
from urllib.parse import urlparse

from PySide6.QtCore import Qt, QThread, QTimer, Slot
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QColorDialog,
    QFileDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.agent.memory import (
    DEFAULT_MEMORY_CONFIDENCE,
    DEFAULT_MEMORY_IMPORTANCE,
    DEFAULT_MEMORY_LAYER,
    DEFAULT_MEMORY_SOURCE,
    MEMORY_LAYER_LABELS,
    MEMORY_LAYERS,
    EmbeddingModelImportResult,
    MemoryStore,
)
from app.agent.mcp import MCPRuntimeSettings
from app.agent.runtime_limits import RuntimeLoopSettings, normalize_runtime_loop_settings
from app.backchannel.model_cache import (
    DEFAULT_BACKCHANNEL_EMBEDDING_MODEL,
    BackchannelModelImportResult,
    backchannel_model_cached,
    download_backchannel_model,
    import_backchannel_model_archive,
)
from app.core.debug_log import debug_log
from app.storage.paths import StoragePaths
from app.config.character_archive import (
    CharacterArchiveError,
    import_character_archive,
    import_character_voice_archive,
)
from app.config.settings_service import (
    BackchannelSettings,
    BubbleSettings,
    DebugLogSettings,
    StartupSettings,
)
from app.platforms.launch_at_login import is_launch_at_login_supported
from app.llm.api_client import ApiSettings
from app.plugins.discovery import PluginDiscovery, save_plugin_enabled_overrides
from app.plugins.models import PluginSpec
from app.config.character_loader import (
    CharacterProfile,
    CharacterRegistry,
    THEME_SOURCE_COMPAT_DEFAULT,
    THEME_SOURCE_PACKAGE,
)
from app.ui.portrait_controller import (
    PORTRAIT_SCALE_DEFAULT_PERCENT,
    normalize_portrait_scale_percent,
)
from app.ui.error_messages import format_failure_message
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
from app.ui.subtitle_controller import (
    REPLY_SEGMENT_PAUSE_MS,
    SPEECH_TYPING_INTERVAL_MS,
    normalize_subtitle_display_speed,
)
from app.agent.screen_awareness import (
    ScreenAwarenessSettings,
    estimate_screen_context_batch_tokens_for_size,
    estimate_screen_context_image_tokens_for_size,
)
from app.voice.tts_settings import (
    DEFAULT_GENIE_TTS_API_URL,
    DEFAULT_GPT_SOVITS_API_URL,
    TTS_PROVIDER_CUSTOM_GPT_SOVITS,
    TTS_PROVIDER_GENIE,
    TTS_PROVIDER_GPT_SOVITS,
    GPTSoVITSTTSSettings,
    TTSConfigError,
)
from app.ui.tts_bundle_dialog import TTSBundleDownloadDialog
from app.ui.theme import (
    DEFAULT_THEME_SETTINGS,
    THEME_COLOR_FIELDS,
    ThemeSettings,
    build_color_button_stylesheet,
    build_settings_dialog_stylesheet,
    merge_theme_with_character,
    normalize_hex_color,
    mix,
)
from app.ui.window_backdrop import VisualEffectMode
from app.voice.tts_bundle import default_provider_bundle_work_dir, is_provider_bundle_work_dir
from app.plugins.models import SettingsPanelContribution, ToolsTabContribution


MEMORY_READING_TEXT = "正在读取长期记忆..."
MEMORY_DEPENDENCY_LOADING_TEXT = "长期记忆系统正在初始化，首次启动可能需要下载本地嵌入模型，请稍等。"


from app.ui.settings import workers as settings_workers
from app.ui.settings import widgets as settings_widgets
from app.ui.settings.pages import (
    ApiSettingsPage,
    CharacterSettingsPage,
    MemorySettingsPage,
    PluginSettingsPage,
    PrivacySettingsPage,
    SystemSettingsPage,
    ThemeSettingsPage,
    ToolsSettingsPage,
    TtsSettingsPage,
)


class SettingsDialog(QDialog):
    def __init__(
        self,
        api_settings: ApiSettings,
        tts_settings: GPTSoVITSTTSSettings,
        base_dir: Path,
        character_registry: CharacterRegistry | None = None,
        current_character: CharacterProfile | None = None,
        screen_awareness_settings: ScreenAwarenessSettings | None = None,
        mcp_settings: MCPRuntimeSettings | None = None,
        debug_log_settings: DebugLogSettings | None = None,
        memory_store: MemoryStore | None = None,
        tools_tab_contributions: list[ToolsTabContribution] | None = None,
        settings_panel_contributions: list[SettingsPanelContribution] | None = None,
        parent=None,  # type: ignore[no-untyped-def]
        portrait_scale_percent: int = PORTRAIT_SCALE_DEFAULT_PERCENT,
        control_panel_width: int = DEFAULT_CONTROL_PANEL_WIDTH,
        bubble_height: int = DEFAULT_BUBBLE_HEIGHT,
        control_panel_vertical_offset: int = DEFAULT_CONTROL_PANEL_VERTICAL_OFFSET,
        input_bar_offset: int = DEFAULT_INPUT_BAR_OFFSET,
        subtitle_typing_interval_ms: int = SPEECH_TYPING_INTERVAL_MS,
        reply_segment_pause_ms: int = REPLY_SEGMENT_PAUSE_MS,
        theme_settings: ThemeSettings | None = None,
        startup_settings: StartupSettings | None = None,
        bubble_settings: BubbleSettings | None = None,
        backchannel_settings: BackchannelSettings | None = None,
        runtime_loop_settings: RuntimeLoopSettings | None = None,
        on_layout_preview: Callable[[int, int, int, int, int], None] | None = None,
        proactive_care_settings: ScreenAwarenessSettings | None = None,
        memory_curation_settings=None,
    ) -> None:
        super().__init__(parent)
        if screen_awareness_settings is None:
            screen_awareness_settings = proactive_care_settings
        self.base_dir = base_dir
        self.tts_settings = tts_settings
        self.startup_settings = startup_settings or StartupSettings()
        self.bubble_settings = bubble_settings or BubbleSettings()
        self.backchannel_settings = (backchannel_settings or BackchannelSettings()).normalized()
        self.runtime_loop_settings = normalize_runtime_loop_settings(runtime_loop_settings)
        # 延迟导入避免与 app.agent 形成导入环（与 settings_service 一致）。
        from app.agent.memory_curator import MemoryCurationSettings as _MemoryCurationSettings

        self.memory_curation_settings = memory_curation_settings or _MemoryCurationSettings()
        self._initial_api_settings = api_settings
        self._initial_tts_settings = tts_settings
        self._initial_character_id = current_character.id if current_character is not None else None
        self.theme_settings = merge_theme_with_character(
            theme_settings or DEFAULT_THEME_SETTINGS,
            current_character,
        )
        self.plugin_specs: list[PluginSpec] = PluginDiscovery(self.base_dir).discover()
        self._plugin_specs_by_id = {
            spec.plugin_id: spec
            for spec in self.plugin_specs
            if spec.plugin_id
        }
        self.character_registry = character_registry
        self.current_character = current_character
        self.portrait_scale_percent = normalize_portrait_scale_percent(portrait_scale_percent)
        self.control_panel_width = normalize_control_panel_width(control_panel_width)
        self.bubble_height = normalize_bubble_height(bubble_height)
        self.control_panel_vertical_offset = normalize_control_panel_vertical_offset(
            control_panel_vertical_offset
        )
        self.input_bar_offset = normalize_input_bar_offset(input_bar_offset)
        # 立绘/控制组滑块拖动时的实时预览回调（由宿主窗口注入，不持久化）。
        self._on_layout_preview = on_layout_preview
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
        self.result_control_panel_width: int | None = None
        self.result_bubble_height: int | None = None
        self.result_control_panel_vertical_offset: int | None = None
        self.result_input_bar_offset: int | None = None
        self.result_subtitle_typing_interval_ms: int | None = None
        self.result_reply_segment_pause_ms: int | None = None
        self.result_screen_awareness_settings: ScreenAwarenessSettings | None = None
        self.result_proactive_care_settings: ScreenAwarenessSettings | None = None
        self.result_mcp_settings: MCPRuntimeSettings | None = None
        self.result_runtime_loop_settings: RuntimeLoopSettings | None = None
        self.result_debug_log_settings: DebugLogSettings | None = None
        self.result_startup_settings: StartupSettings | None = None
        self.result_bubble_settings: BubbleSettings | None = None
        self.result_backchannel_settings: BackchannelSettings | None = None
        self.result_memory_curation_settings = None
        self.result_theme_settings: ThemeSettings | None = None
        self.result_theme_write_mode: Literal["unchanged", "manual", "ai", "reset", "character"] = "unchanged"
        self.result_plugin_config_changed = False
        self._api_test_thread: QThread | None = None
        self._api_test_worker: settings_workers.ApiConnectionTestWorker | None = None
        self._api_model_probe_thread: QThread | None = None
        self._api_model_probe_worker: settings_workers.ApiModelListProbeWorker | None = None
        self._tts_test_thread: QThread | None = None
        self._tts_test_worker: settings_workers.TTSTestWorker | None = None
        self._pending_api_accept_values: dict[str, object] | None = None
        self._pending_accept_values: dict[str, object] | None = None
        self._save_button_text: str | None = None
        self._memory_list_thread: QThread | None = None
        self._memory_list_worker: settings_workers.MemoryListWorker | None = None
        self._memory_model_import_thread: QThread | None = None
        self._memory_model_import_worker: settings_workers.MemoryModelImportWorker | None = None
        self._memory_model_download_thread: QThread | None = None
        self._memory_model_download_worker: settings_workers.MemoryModelDownloadWorker | None = None
        self._backchannel_model_import_thread: QThread | None = None
        self._backchannel_model_import_worker: settings_workers.BackchannelModelImportWorker | None = None
        self._backchannel_model_download_thread: QThread | None = None
        self._backchannel_model_download_worker: settings_workers.BackchannelModelDownloadWorker | None = None
        self._theme_ai_thread: QThread | None = None
        self._theme_ai_worker: settings_workers.ThemeAiWorker | None = None
        self._theme_ai_enabled = self.theme_settings.ai_enabled
        self._theme_write_mode: Literal["unchanged", "manual", "ai", "reset", "character"] = "unchanged"
        self._syncing_theme_controls = False
        # 上次实际应用到对话框的 QSS,用于跳过内容未变的 setStyleSheet(避免无谓 re-polish)。
        self._applied_dialog_stylesheet: str | None = None
        # 颜色输入防抖:连续键入只在停顿后重建一次整张对话框 QSS,避免逐字符卡顿。
        self._theme_stylesheet_debounce = QTimer(self)
        self._theme_stylesheet_debounce.setSingleShot(True)
        self._theme_stylesheet_debounce.setInterval(150)
        self._theme_stylesheet_debounce.timeout.connect(self._apply_pending_theme_stylesheet)
        self._character_export_thread: QThread | None = None
        self._character_export_worker: settings_workers.CharacterArchiveExportWorker | None = None
        self._memory_reload_pending = False
        self._syncing_memory_selection = False
        self._memory_entries_loaded_once = False

        self.setWindowTitle("设置")
        self.setMinimumSize(680, 500)
        self.resize(820, 640)

        # 左侧分类导航：一个分类对应一个内容面板，纵向列表便于后续扩展更多设置分类。
        nav_items: list[tuple[str, QWidget]] = [
            (
                "角色",
                self._build_scrollable_tab(
                    CharacterSettingsPage(self).build(character_registry, current_character)
                ),
            ),
            ("外观", self._build_scrollable_tab(ThemeSettingsPage(self).build())),
            ("模型", self._build_scrollable_tab(ApiSettingsPage(self).build(api_settings))),
            ("语音", self._build_scrollable_tab(TtsSettingsPage(self).build(tts_settings))),
            (
                "隐私",
                self._build_scrollable_tab(
                    PrivacySettingsPage(self).build(screen_awareness_settings or ScreenAwarenessSettings())
                ),
            ),
            (
                "工具",
                self._build_scrollable_tab(
                    ToolsSettingsPage(self).build(
                        mcp_settings or MCPRuntimeSettings(),
                        self.runtime_loop_settings,
                        tools_tab_contributions or [],
                    )
                ),
            ),
            (
                "插件",
                self._build_scrollable_tab(
                    PluginSettingsPage(self).build(settings_panel_contributions or [])
                ),
            ),
            (
                "系统",
                self._build_scrollable_tab(
                    SystemSettingsPage(self).build(
                        debug_log_settings or DebugLogSettings(),
                        self.startup_settings,
                        self.bubble_settings,
                        self.backchannel_settings,
                    )
                ),
            ),
        ]
        if memory_store is not None:
            # 记忆页自带列表滚动，沿用原行为不再额外包滚动区，避免双重滚动条。
            nav_items.append(("记忆", MemorySettingsPage(self).build(memory_store)))

        navigation = self._build_navigation(nav_items)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel,
            self,
        )
        self.button_box = buttons
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout()
        layout.addWidget(navigation, 1)
        layout.addWidget(buttons)
        self.setLayout(layout)
        self._capture_initial_tts_settings_from_controls()
        self._apply_theme_stylesheet(self.theme_settings)
        # 初始化外观效果下拉框等控件为当前主题值
        self._set_theme_controls(self.theme_settings, sync_visual_effect=True)

    def _capture_initial_tts_settings_from_controls(self) -> None:
        settings = self._validated_tts_settings(
            show_warnings=False,
            validate_enabled=False,
        )
        if settings is not None:
            self._initial_tts_settings = settings

    def _build_navigation(self, items: list[tuple[str, QWidget]]) -> QWidget:
        """左侧分类列表 + 右侧内容堆叠，替代原顶部横向 tab，便于纵向扩展分类。"""
        container = QWidget(self)
        nav_list = settings_widgets._ClickOnlyListWidget(container)
        nav_list.setObjectName("settingsNavList")
        nav_list.setFixedWidth(140)
        nav_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        stack = QStackedWidget(container)
        stack.setObjectName("settingsNavStack")
        self._settings_nav_titles = [title for title, _panel in items]
        for title, panel in items:
            nav_list.addItem(QListWidgetItem(title))
            stack.addWidget(panel)
        nav_list.currentRowChanged.connect(stack.setCurrentIndex)
        nav_list.currentRowChanged.connect(self._handle_settings_nav_changed)
        nav_list.setCurrentRow(0)

        layout = QHBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        layout.addWidget(nav_list)
        layout.addWidget(stack, 1)
        container.setLayout(layout)
        return container

    @Slot(int)
    def _handle_settings_nav_changed(self, row: int) -> None:
        titles = getattr(self, "_settings_nav_titles", [])
        if row < 0 or row >= len(titles):
            return
        if titles[row] == "记忆":
            self._ensure_memory_entries_loaded()

    def _ensure_memory_entries_loaded(self) -> None:
        if self._memory_entries_loaded_once:
            return
        if self.memory_store is None or not hasattr(self, "memory_table"):
            return
        self._memory_entries_loaded_once = True
        self._load_memory_entries()

    def _build_scrollable_tab(self, content: QWidget) -> QWidget:
        tab = QWidget(self)
        # 内容页自身承载面板背景：QStackedWidget 不绘制 QSS 背景，内容又透明，
        # 不给页容器上色时空白处会一路透到粉色的 QDialog 底色。
        tab.setObjectName("settingsNavPage")
        # 滚动内容容器必须显式透明，否则会被样式表填上默认灰背景，
        # 盖住 settingsNavPage 的面板色，导致右侧内容区“没融入主题”。
        # settingsScrollContent 已在主题样式表中声明为透明；保留 content 已有的
        # objectName（如插件页的 settingsPluginTab，同样是透明规则）。
        if not content.objectName():
            content.setObjectName("settingsScrollContent")
        scroll_area = QScrollArea(tab)
        scroll_area.setObjectName("settingsScrollArea")
        scroll_area.viewport().setObjectName("settingsScrollViewport")
        scroll_area.setWidgetResizable(True)
        scroll_area.setFrameShape(QFrame.Shape.NoFrame)
        scroll_area.setWidget(content)

        layout = QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(scroll_area)
        tab.setLayout(layout)
        return tab

    def _populate_plugin_table_row(self, row: int, spec: PluginSpec) -> None:
        enabled_item = QTableWidgetItem("")
        enabled_item.setData(Qt.ItemDataRole.UserRole, spec.plugin_id)
        enabled_item.setFlags(Qt.ItemFlag.ItemIsEnabled)
        self.plugin_table.setItem(row, 0, enabled_item)
        self._set_plugin_checkbox_widget(row, spec)

        display_name = spec.name or spec.plugin_id or spec.entry
        name_item = QTableWidgetItem(display_name)
        name_item.setFlags(Qt.ItemFlag.ItemIsEnabled)
        name_item.setToolTip(display_name)
        self.plugin_table.setItem(row, 1, name_item)
        self._apply_plugin_row_style(row)

    def _set_plugin_checkbox_widget(self, row: int, spec: PluginSpec) -> None:
        container = QWidget(self.plugin_table)
        layout = QHBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        checkbox = QCheckBox(container)
        checkbox.setChecked(spec.enabled or spec.required)
        checkbox.setEnabled(not spec.required)
        checkbox.setToolTip("启用此插件" if not spec.required else "必需插件不可禁用。")
        checkbox.stateChanged.connect(
            lambda _state, current_row=row: self._handle_plugin_enabled_check_changed(current_row)
        )
        layout.addWidget(checkbox, 0, Qt.AlignmentFlag.AlignCenter)
        container.setLayout(layout)
        self.plugin_table.setCellWidget(row, 0, container)
        self._style_plugin_checkbox_container(container, row)

    def _handle_plugin_enabled_check_changed(self, row: int) -> None:
        self._apply_plugin_row_style(row)
        if getattr(self, "plugin_table", None) is not None and self.plugin_table.currentRow() == row:
            self._refresh_plugin_detail_panel(row)

    def _apply_plugin_row_style(self, row: int) -> None:
        brush = _memory_row_background(row, False, self.theme_settings)
        for column in range(self.plugin_table.columnCount()):
            item = self.plugin_table.item(row, column)
            if item is not None:
                item.setBackground(brush)
        container = self.plugin_table.cellWidget(row, 0)
        if container is not None:
            self._style_plugin_checkbox_container(container, row)

    def _style_plugin_checkbox_container(self, container: QWidget, row: int) -> None:
        color = _memory_row_background_color(row, False, self.theme_settings)
        container.setStyleSheet(f"background: {color};")

    def _selected_plugin_enabled_overrides(self) -> dict[str, bool]:
        if not hasattr(self, "plugin_table"):
            return {}
        selected: dict[str, bool] = {}
        for row in range(self.plugin_table.rowCount()):
            item = self.plugin_table.item(row, 0)
            if item is None:
                continue
            plugin_id = item.data(Qt.ItemDataRole.UserRole)
            if not isinstance(plugin_id, str) or not plugin_id:
                continue
            spec = self._plugin_specs_by_id.get(plugin_id)
            container = self.plugin_table.cellWidget(row, 0)
            checkbox = container.findChild(QCheckBox) if container is not None else None
            selected[plugin_id] = bool(
                spec.required if spec is not None and spec.required else checkbox is not None and checkbox.isChecked()
            )
        return selected

    def _plugin_row_enabled(self, row: int) -> bool:
        if not hasattr(self, "plugin_table"):
            return False
        item = self.plugin_table.item(row, 0)
        if item is None:
            return False
        plugin_id = item.data(Qt.ItemDataRole.UserRole)
        spec = self._plugin_specs_by_id.get(plugin_id) if isinstance(plugin_id, str) else None
        if spec is not None and spec.required:
            return True
        container = self.plugin_table.cellWidget(row, 0)
        checkbox = container.findChild(QCheckBox) if container is not None else None
        return bool(checkbox is not None and checkbox.isChecked())

    def _refresh_plugin_detail_panel(self, row: int | None = None) -> None:
        if not hasattr(self, "plugin_detail_title_label"):
            return
        if not self.plugin_specs:
            self.plugin_detail_title_label.setText("暂无插件")
            self.plugin_detail_meta_label.setText("当前没有发现可管理的插件。")
            self.plugin_detail_permissions_label.setText("无")
            self.plugin_detail_description_label.setText("插件目录为空，或尚未配置插件清单。")
            self._current_plugin_id = ""
            self._update_plugin_settings_button(enabled=False, tooltip="暂无可管理插件。")
            return

        if row is None or row < 0 or row >= len(self.plugin_specs):
            row = 0
        spec = self.plugin_specs[row]
        selected_enabled = self._plugin_row_enabled(row)
        persisted_enabled = bool(spec.enabled or spec.required)
        status = "已启用" if selected_enabled else "已禁用"
        if selected_enabled != persisted_enabled:
            status += "（保存并重启后生效）"
        if spec.required:
            status += "，必需插件"

        source = "内置清单" if spec.source == "manifest" else "配置"
        self.plugin_detail_title_label.setText(spec.name or spec.plugin_id or spec.entry)
        self.plugin_detail_meta_label.setText(
            "\n".join(
                [
                    f"版本：{spec.version}",
                    f"优先级：{spec.priority}",
                    f"来源：{source}",
                    f"状态：{status}",
                ]
            )
        )
        self.plugin_detail_permissions_label.setText(
            "、".join(spec.permissions) if spec.permissions else "未声明"
        )
        self.plugin_detail_description_label.setText(spec.description or "暂无介绍。")

        self._current_plugin_id = spec.plugin_id
        if self._plugin_settings_for(spec.plugin_id):
            self._update_plugin_settings_button(enabled=True, tooltip="")
        elif not spec.enabled:
            self._update_plugin_settings_button(
                enabled=False,
                tooltip="此插件未启用；启用并保存重启 Sakura 后才会加载内置详细设置。",
            )
        else:
            self._update_plugin_settings_button(enabled=False, tooltip="此插件没有内置详细设置。")

    def _plugin_settings_for(self, plugin_id: str) -> list:
        """取选中插件的设置贡献；仅有唯一未归属分组时回退到 __unscoped__。"""
        grouped = getattr(self, "_plugin_settings_contributions_by_id", {})
        contributions = grouped.get(plugin_id)
        if not contributions and len(grouped) == 1:
            contributions = grouped.get("__unscoped__")
        return list(contributions or [])

    def _update_plugin_settings_button(self, *, enabled: bool, tooltip: str) -> None:
        button = getattr(self, "plugin_open_settings_button", None)
        if button is not None:
            button.setEnabled(enabled)
            button.setToolTip(tooltip)

    def _open_plugin_settings_dialog(self) -> None:
        """弹出独立对话框，整宽、可滚动地展示选中插件的设置面板。"""
        dialog = self._build_plugin_settings_dialog(getattr(self, "_current_plugin_id", ""))
        if dialog is not None:
            dialog.exec()

    def _build_plugin_settings_dialog(self, plugin_id: str) -> QDialog | None:
        """构建（但不弹出）选中插件的设置对话框；无设置时返回 None。"""
        contributions = self._plugin_settings_for(plugin_id)
        if not contributions:
            return None
        title = self.plugin_detail_title_label.text() if hasattr(self, "plugin_detail_title_label") else "插件"

        dialog = QDialog(self)
        dialog.setObjectName("pluginSettingsDialog")
        dialog.setWindowTitle(f"{title} · 设置")
        # 继承主设置窗口的主题样式，避免子对话框显示为默认 Qt 灰白。
        dialog.setStyleSheet(self.styleSheet())
        dialog_layout = QVBoxLayout(dialog)
        dialog_layout.setContentsMargins(0, 0, 0, 0)
        dialog_layout.setSpacing(0)

        scroll = QScrollArea(dialog)
        # 复用主题中“透明滚动区/内容”选择器，让插件 GroupBox 落在主题背景上。
        scroll.setObjectName("settingsScrollArea")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        content = QWidget()
        content.setObjectName("settingsScrollContent")
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(16, 16, 16, 16)
        content_layout.setSpacing(12)
        for contribution in sorted(contributions, key=lambda item: item.order):
            try:
                widget = contribution.build(content)
            except Exception as exc:  # noqa: BLE001 — 单个设置面板构建失败降级为提示，不阻断
                widget = QLabel(f"{contribution.title} 设置加载失败：{exc}", content)
                widget.setWordWrap(True)
            content_layout.addWidget(widget)
        content_layout.addStretch(1)
        scroll.setWidget(content)
        dialog_layout.addWidget(scroll, 1)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close, dialog)
        buttons.setContentsMargins(12, 8, 12, 12)
        buttons.rejected.connect(dialog.reject)
        buttons.accepted.connect(dialog.accept)
        dialog_layout.addWidget(buttons)

        # 尺寸贴合内容：内容少则对话框矮（关闭按钮紧随其下），多则封顶滚动。
        hint = content.sizeHint()
        dialog.resize(
            min(max(hint.width() + 40, 480), 640),
            min(hint.height() + 72, 600),
        )
        return dialog

    @Slot(bool)
    def _sync_proactive_screen_context_controls(self, enabled: bool) -> None:
        """主动屏幕感知关闭时，不允许调整从属参数。"""
        self._set_form_widgets_enabled(
            getattr(self, "_proactive_form_layout", None),
            (
                self.proactive_check_interval_spin,
                self.proactive_cooldown_spin,
                self.proactive_batch_limit_spin,
                self.proactive_token_estimate_label,
            ),
            enabled,
        )

    def _sync_proactive_token_estimate(self, *_args: object) -> None:
        """根据当前屏幕原始尺寸和批量张数刷新主动感知图像 token 粗估。"""
        if not hasattr(self, "proactive_token_estimate_label"):
            return
        screen_width, screen_height = self._screen_awareness_estimate_size()
        image_count = max(1, int(self.proactive_batch_limit_spin.value()))
        model = self._initial_api_settings.model
        per_image = estimate_screen_context_image_tokens_for_size(
            screen_width,
            screen_height,
            model=model,
        )
        total = estimate_screen_context_batch_tokens_for_size(
            screen_width,
            screen_height,
            image_count,
            model=model,
        )
        self.proactive_token_estimate_label.setText(
            f"按原始屏幕 {screen_width}x{screen_height}、高细节估算："
            f"约 {per_image:,} tokens/张；{image_count} 张约 {total:,} tokens。"
        )

    def _screen_awareness_estimate_size(self) -> tuple[int, int]:
        screen = self.screen()
        if screen is None:
            app = QApplication.instance()
            screen = app.primaryScreen() if app is not None else None
        if screen is not None:
            geometry = screen.geometry()
            # geometry() 是逻辑尺寸；实际截图按物理像素采样，故乘 devicePixelRatio
            # 还原真实「原始屏幕」分辨率（如 200% 缩放下 1600x1000 → 3200x2000）。
            dpr = screen.devicePixelRatio() or 1.0
            return (
                max(1, round(geometry.width() * dpr)),
                max(1, round(geometry.height() * dpr)),
            )

        return 1280, 720

    @Slot(bool)
    def _sync_bubble_auto_hide_controls(self, enabled: bool) -> None:
        """气泡自动隐藏关闭时，不允许调整无操作时长。"""
        self._set_form_widgets_enabled(
            getattr(self, "_system_form_layout", None),
            (self.bubble_auto_hide_delay_spin,),
            enabled,
        )

    @Slot(bool)
    def _sync_backchannel_controls(self, enabled: bool) -> None:
        """接话层关闭时禁用从属参数；接话语音还依赖全局 TTS 开关。"""
        self._set_form_widgets_enabled(
            getattr(self, "_backchannel_form_layout", None),
            (
                self.backchannel_mode_combo,
                self.backchannel_delay_spin,
                self.backchannel_probability_spin,
            ),
            enabled,
        )
        tts_check = getattr(self, "tts_enabled_check", None)
        tts_on = tts_check.isChecked() if tts_check is not None else True
        self._set_form_widgets_enabled(
            getattr(self, "_backchannel_form_layout", None),
            (self.backchannel_tts_enabled_check,),
            enabled and tts_on,
        )
        self._refresh_backchannel_setup_status()

    def _sync_tts_enabled_controls(self, enabled: bool) -> None:
        """同步 TTS 总开关和整合包模式下的从属控件可交互状态。"""
        provider = str(self.tts_provider_combo.currentData() or TTS_PROVIDER_GPT_SOVITS)
        bundled = _is_bundled_tts_provider(provider)
        bundled_fields = (
            self.tts_api_url_edit,
            self.tts_work_dir_edit,
            self.tts_python_path_edit,
            self.tts_config_path_edit,
        )
        self._set_form_widgets_enabled(
            getattr(self, "_tts_form_layout", None),
            (self.tts_provider_combo,),
            enabled,
        )
        self._set_form_widgets_enabled(
            getattr(self, "_tts_form_layout", None),
            bundled_fields,
            enabled and not bundled,
            labels_enabled=enabled,
        )
        self._set_form_widgets_enabled(
            getattr(self, "_tts_form_layout", None),
            (self.tts_timeout_spin,),
            enabled,
        )
        self.tts_bundle_download_button.setEnabled(True)
        self._sync_voice_import_controls()

    def _sync_voice_import_controls(self) -> None:
        if hasattr(self, "tts_voice_import_button"):
            self.tts_voice_import_button.setEnabled(
                self._character_export_thread is None and self._selected_character_profile() is not None
            )

    def _set_form_widgets_enabled(
        self,
        form_layout: QFormLayout | None,
        widgets: tuple[QWidget, ...],
        enabled: bool,
        *,
        labels_enabled: bool | None = None,
    ) -> None:
        for widget in widgets:
            widget.setEnabled(enabled)
            if form_layout is None:
                continue
            label = form_layout.labelForField(widget)
            if label is not None:
                label.setEnabled(enabled if labels_enabled is None else labels_enabled)

    def _load_memory_entries(self) -> None:
        if self.memory_store is None or not hasattr(self, "memory_table"):
            return
        self._memory_entries_loaded_once = True
        if self._memory_list_thread is not None:
            self._memory_reload_pending = True
            return

        loading_text = self._memory_loading_text()
        self.memory_status_label.setText(loading_text)
        self.memory_refresh_button.setEnabled(False)
        self._show_memory_placeholder(loading_text)

        thread = QThread()
        worker = settings_workers.MemoryListWorker(self.memory_store)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.succeeded.connect(self._handle_memory_load_success)
        worker.failed.connect(self._handle_memory_load_failed)
        worker.finished.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(self._reset_memory_list_worker)

        self._memory_list_thread = thread
        self._memory_list_worker = worker
        thread.start()

    def _import_memory_model_archive(self) -> None:
        if self.memory_store is None:
            return
        if self._memory_model_import_thread is not None or self._memory_model_download_thread is not None:
            QMessageBox.information(self, "处理中", "记忆模型正在安装或导入，请等待完成。")
            return
        path_text, _ = QFileDialog.getOpenFileName(
            self,
            "导入记忆模型 ZIP",
            str(self.base_dir),
            "记忆模型 ZIP (*.zip)",
        )
        if not path_text:
            return
        self._start_memory_model_import(Path(path_text))

    def _download_memory_model(self) -> None:
        if self.memory_store is None:
            return
        if self._memory_model_import_thread is not None or self._memory_model_download_thread is not None:
            QMessageBox.information(self, "处理中", "记忆模型正在安装或导入，请等待完成。")
            return
        if not callable(getattr(self.memory_store, "download_embedding_model", None)):
            QMessageBox.warning(
                self,
                "安装失败",
                format_failure_message(
                    "当前记忆模块不支持在线安装模型。",
                    "请下载记忆模型 ZIP，并使用设置页的手动导入功能。",
                    "当前记忆模块没有 download_embedding_model 接口。",
                ),
            )
            return
        if not self._memory_entries_loaded_once:
            self._ensure_memory_entries_loaded()
        self._start_memory_model_download()

    def _import_backchannel_model_archive(self) -> None:
        if self._backchannel_model_import_thread is not None or self._backchannel_model_download_thread is not None:
            QMessageBox.information(self, "处理中", "接话模型正在安装或导入，请等待完成。")
            return
        path_text, _ = QFileDialog.getOpenFileName(
            self,
            "导入接话模型 ZIP",
            str(self.base_dir),
            "接话模型 ZIP (*.zip)",
        )
        if not path_text:
            return
        self._start_backchannel_model_import(Path(path_text))

    def _download_backchannel_model(self) -> None:
        if self._backchannel_model_import_thread is not None or self._backchannel_model_download_thread is not None:
            QMessageBox.information(self, "处理中", "接话模型正在安装或导入，请等待完成。")
            return
        self._start_backchannel_model_download()

    def _refresh_backchannel_setup_status(self) -> None:
        if hasattr(self, "backchannel_setup_hint_label"):
            self.backchannel_setup_hint_label.setText(self._backchannel_setup_hint_text())
        if hasattr(self, "backchannel_model_status_label"):
            self.backchannel_model_status_label.setText(self._backchannel_model_status_text())

    def _start_backchannel_model_import(self, archive_path: Path) -> None:
        self._set_backchannel_model_import_busy(True)
        if hasattr(self, "backchannel_model_status_label"):
            self.backchannel_model_status_label.setText("正在导入接话模型...")

        thread = QThread()
        worker = settings_workers.BackchannelModelImportWorker(
            self.base_dir,
            archive_path,
            import_backchannel_model_archive,
        )
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.succeeded.connect(self._handle_backchannel_model_import_success)
        worker.failed.connect(self._handle_backchannel_model_import_failed)
        worker.finished.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(self._reset_backchannel_model_import_worker)

        self._backchannel_model_import_thread = thread
        self._backchannel_model_import_worker = worker
        thread.start()

    def _start_backchannel_model_download(self) -> None:
        self._set_backchannel_model_download_busy(True)
        if hasattr(self, "backchannel_model_status_label"):
            self.backchannel_model_status_label.setText("正在在线安装接话模型...")

        thread = QThread()
        worker = settings_workers.BackchannelModelDownloadWorker(
            self.base_dir,
            download_backchannel_model,
        )
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.succeeded.connect(self._handle_backchannel_model_download_success)
        worker.failed.connect(self._handle_backchannel_model_download_failed)
        worker.finished.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(self._reset_backchannel_model_download_worker)

        self._backchannel_model_download_thread = thread
        self._backchannel_model_download_worker = worker
        thread.start()

    def _start_memory_model_import(self, archive_path: Path) -> None:
        if self.memory_store is None:
            return
        self._set_memory_model_import_busy(True)
        self.memory_status_label.setText("正在导入记忆模型...")

        thread = QThread()
        worker = settings_workers.MemoryModelImportWorker(self.memory_store, archive_path)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.succeeded.connect(self._handle_memory_model_import_success)
        worker.failed.connect(self._handle_memory_model_import_failed)
        worker.finished.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(self._reset_memory_model_import_worker)

        self._memory_model_import_thread = thread
        self._memory_model_import_worker = worker
        thread.start()

    def _start_memory_model_download(self) -> None:
        if self.memory_store is None:
            return
        self._set_memory_model_download_busy(True)
        self.memory_status_label.setText("正在在线安装记忆模型...")

        thread = QThread()
        worker = settings_workers.MemoryModelDownloadWorker(self.memory_store)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.succeeded.connect(self._handle_memory_model_download_success)
        worker.failed.connect(self._handle_memory_model_download_failed)
        worker.finished.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(self._reset_memory_model_download_worker)

        self._memory_model_download_thread = thread
        self._memory_model_download_worker = worker
        thread.start()

    @Slot(object)
    def _handle_memory_model_import_success(self, result: EmbeddingModelImportResult) -> None:
        self.memory_status_label.setText("记忆模型已导入，正在重新读取长期记忆...")
        QMessageBox.information(
            self,
            "导入成功",
            (
                f"记忆模型已导入：{result.model_name}\n"
                f"缓存目录：{result.cache_folder}\n"
                f"快照数量：{result.snapshot_count}"
            ),
        )
        self._load_memory_entries()

    @Slot(str)
    def _handle_memory_model_import_failed(self, message: str) -> None:
        self.memory_status_label.setText(f"导入失败：{message}")
        QMessageBox.warning(
            self,
            "导入失败",
            format_failure_message(
                "记忆模型 ZIP 没有成功导入。",
                "请确认 ZIP 来自 Sakura Release、没有手动改名或解压，然后重新导入。",
                message,
            ),
        )

    @Slot(object)
    def _handle_memory_model_download_success(self, result: EmbeddingModelImportResult) -> None:
        self.memory_status_label.setText("记忆模型已安装，正在重新读取长期记忆...")
        QMessageBox.information(
            self,
            "安装成功",
            (
                f"记忆模型已安装：{result.model_name}\n"
                f"缓存目录：{result.cache_folder}\n"
                f"快照数量：{result.snapshot_count}"
            ),
        )
        self._load_memory_entries()

    @Slot(str)
    def _handle_memory_model_download_failed(self, message: str) -> None:
        self.memory_status_label.setText(f"安装失败：{message}")
        QMessageBox.warning(
            self,
            "安装失败",
            format_failure_message(
                "记忆模型没有在线安装成功。",
                "请开启代理后重试，或下载下面的 ZIP 并在设置页手动导入：\n"
                "https://github.com/Rvosy/Sakura/releases/download/v0.9.7/"
                "models--sentence-transformers--all-MiniLM-L6-v2.zip",
                message,
            ),
        )

    @Slot(object)
    def _handle_backchannel_model_import_success(self, result: BackchannelModelImportResult) -> None:
        self._refresh_backchannel_setup_status()
        QMessageBox.information(
            self,
            "导入成功",
            (
                f"接话模型已导入：{result.model_name}\n"
                f"缓存目录：{result.cache_folder}\n"
                f"快照数量：{result.snapshot_count}"
            ),
        )

    @Slot(str)
    def _handle_backchannel_model_import_failed(self, message: str) -> None:
        if hasattr(self, "backchannel_model_status_label"):
            self.backchannel_model_status_label.setText(f"导入失败：{message}")
        QMessageBox.warning(
            self,
            "导入失败",
            format_failure_message(
                "接话模型 ZIP 没有成功导入。",
                "请确认选择了完整、未解压的接话模型 ZIP 后重试。",
                message,
            ),
        )

    @Slot(object)
    def _handle_backchannel_model_download_success(self, result: BackchannelModelImportResult) -> None:
        self._refresh_backchannel_setup_status()
        QMessageBox.information(
            self,
            "安装成功",
            (
                f"接话模型已安装：{result.model_name}\n"
                f"缓存目录：{result.cache_folder}\n"
                f"快照数量：{result.snapshot_count}"
            ),
        )

    @Slot(str)
    def _handle_backchannel_model_download_failed(self, message: str) -> None:
        if hasattr(self, "backchannel_model_status_label"):
            self.backchannel_model_status_label.setText(f"安装失败：{message}")
        QMessageBox.warning(
            self,
            "安装失败",
            format_failure_message(
                "接话模型没有在线安装成功。",
                "请检查 Hugging Face 访问、网络或代理后重试，也可以在设置页手动导入模型 ZIP。",
                message,
            ),
        )

    @Slot()
    def _reset_memory_model_import_worker(self) -> None:
        self._memory_model_import_thread = None
        self._memory_model_import_worker = None
        self._set_memory_model_import_busy(False)

    @Slot()
    def _reset_memory_model_download_worker(self) -> None:
        self._memory_model_download_thread = None
        self._memory_model_download_worker = None
        self._set_memory_model_download_busy(False)

    @Slot()
    def _reset_backchannel_model_import_worker(self) -> None:
        self._backchannel_model_import_thread = None
        self._backchannel_model_import_worker = None
        self._set_backchannel_model_import_busy(False)
        self._refresh_backchannel_setup_status()

    @Slot()
    def _reset_backchannel_model_download_worker(self) -> None:
        self._backchannel_model_download_thread = None
        self._backchannel_model_download_worker = None
        self._set_backchannel_model_download_busy(False)
        self._refresh_backchannel_setup_status()

    def _set_memory_model_import_busy(self, busy: bool) -> None:
        if hasattr(self, "memory_import_model_button"):
            self.memory_import_model_button.setEnabled(not busy)
        if hasattr(self, "memory_download_model_button"):
            self.memory_download_model_button.setEnabled(
                not busy and self._memory_model_download_thread is None
            )
        if hasattr(self, "memory_refresh_button"):
            self.memory_refresh_button.setEnabled(
                not busy and self._memory_list_thread is None and self._memory_model_download_thread is None
            )

    def _set_memory_model_download_busy(self, busy: bool) -> None:
        if hasattr(self, "memory_download_model_button"):
            self.memory_download_model_button.setEnabled(not busy)
        if hasattr(self, "memory_import_model_button"):
            self.memory_import_model_button.setEnabled(
                not busy and self._memory_model_import_thread is None
            )
        if hasattr(self, "memory_refresh_button"):
            self.memory_refresh_button.setEnabled(
                not busy and self._memory_list_thread is None and self._memory_model_import_thread is None
            )

    def _set_backchannel_model_import_busy(self, busy: bool) -> None:
        if hasattr(self, "backchannel_import_model_button"):
            self.backchannel_import_model_button.setEnabled(not busy)
        if hasattr(self, "backchannel_download_model_button"):
            self.backchannel_download_model_button.setEnabled(
                not busy and self._backchannel_model_download_thread is None
            )
        if hasattr(self, "backchannel_refresh_status_button"):
            self.backchannel_refresh_status_button.setEnabled(
                not busy and self._backchannel_model_download_thread is None
            )

    def _set_backchannel_model_download_busy(self, busy: bool) -> None:
        if hasattr(self, "backchannel_download_model_button"):
            self.backchannel_download_model_button.setEnabled(not busy)
        if hasattr(self, "backchannel_import_model_button"):
            self.backchannel_import_model_button.setEnabled(
                not busy and self._backchannel_model_import_thread is None
            )
        if hasattr(self, "backchannel_refresh_status_button"):
            self.backchannel_refresh_status_button.setEnabled(
                not busy and self._backchannel_model_import_thread is None
            )

    def _backchannel_model_status_text(self) -> str:
        if backchannel_model_cached(self.base_dir):
            if self._selected_backchannel_mode() == "hybrid":
                return f"已导入 {DEFAULT_BACKCHANNEL_EMBEDDING_MODEL}，模型增强可用。"
            return f"已导入 {DEFAULT_BACKCHANNEL_EMBEDDING_MODEL}；切换到模型增强后启用。"
        return "未导入模型；模型增强会自动使用规则模式降级。"

    def _backchannel_setup_hint_text(self) -> str:
        enabled = self._selected_backchannel_enabled()
        mode = self._selected_backchannel_mode()
        model_ready = backchannel_model_cached(self.base_dir)

        if not enabled:
            return "接话当前关闭；仍可先导入句向量模型备用。"
        if mode == "rules":
            return "规则模式不依赖模型；保存后会用高精度规则触发接话。"
        if model_ready:
            return "模型增强已就绪；保存后规则优先，规则无命中时由 probe 分类头补足泛化。"
        return "已选择模型增强；缺句向量模型或低置信时会自动降级到规则，不会强行接话。"

    def _selected_backchannel_mode(self) -> str:
        combo = getattr(self, "backchannel_mode_combo", None)
        if combo is not None:
            return str(combo.currentData() or self.backchannel_settings.mode)
        return self.backchannel_settings.mode

    def _selected_backchannel_enabled(self) -> bool:
        check = getattr(self, "backchannel_enabled_check", None)
        if check is not None:
            return check.isChecked()
        return self.backchannel_settings.enabled

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
        self._all_memories = _sort_memories_by_latest_time(memories)
        all_ids = {str(memory.get("id", "")) for memory in self._all_memories}
        self._selected_memory_ids &= all_ids
        if self._editing_memory_id and self._editing_memory_id not in all_ids:
            self._memory_editor_mode = None
            self._editing_memory_id = None
            self._active_memory_id = None
            self._clear_memory_editor()
            self._set_memory_editor_visible(False)
        self.memory_status_label.setText(f"已加载 {len(self._all_memories)} 条记忆")
        self._refresh_memory_table()

    @Slot(str)
    def _handle_memory_load_failed(self, message: str) -> None:
        self._all_memories = []
        self._memory_entries_loaded_once = False
        self.memory_status_label.setText(f"读取失败：{message}")
        self._show_memory_placeholder("记忆读取失败，请稍后重试。")
        QMessageBox.warning(
            self,
            "读取失败",
            format_failure_message(
                "长期记忆列表没有读取成功。",
                "请确认记忆模型已经安装，稍后重新打开记忆页或重启 Sakura。",
                message,
            ),
        )

    @Slot()
    def _reset_memory_list_worker(self) -> None:
        self.memory_refresh_button.setEnabled(self._memory_model_import_thread is None)
        self._memory_list_thread = None
        self._memory_list_worker = None
        if self._memory_reload_pending:
            self._memory_reload_pending = False
            self._load_memory_entries()

    def _pin_active_memory_to_top(self) -> None:
        """编辑某条记忆时把它挪到列表首行,避免被底部详情面板遮住、取消还要下拉找回。"""
        if self._memory_editor_mode != "edit" or not self._active_memory_id:
            return
        for index, memory in enumerate(self._visible_memories):
            if str(memory.get("id", "")) == self._active_memory_id:
                if index > 0:
                    self._visible_memories.insert(0, self._visible_memories.pop(index))
                return

    def _refresh_memory_table(self) -> None:
        if not hasattr(self, "memory_table"):
            return
        keyword = self.memory_search_edit.text().strip()
        keyword_lower = keyword.lower()
        layer_filter = ""
        layer_combo = getattr(self, "memory_layer_filter_combo", None)
        if layer_combo is not None:
            layer_filter = str(layer_combo.currentData() or "")
        if keyword_lower:
            self._visible_memories = [
                memory
                for memory in self._all_memories
                if keyword_lower in str(memory.get("content", "")).lower()
                or keyword_lower in str(memory.get("id", "")).lower()
                or keyword_lower in str(memory.get("category", "")).lower()
                or keyword_lower in str(memory.get("source", "")).lower()
            ]
        else:
            self._visible_memories = list(self._all_memories)
        if layer_filter:
            self._visible_memories = [
                memory
                for memory in self._visible_memories
                if str(memory.get("layer") or DEFAULT_MEMORY_LAYER) == layer_filter
            ]
        self._pin_active_memory_to_top()
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
            layer = str(memory.get("layer") or DEFAULT_MEMORY_LAYER)
            updated_at = str(memory.get("updated_at") or memory.get("created_at") or "")
            is_checked = memory_id in self._selected_memory_ids

            select_item = QTableWidgetItem("")
            select_item.setFlags(Qt.ItemFlag.ItemIsEnabled)
            select_item.setData(Qt.ItemDataRole.UserRole, memory_id)

            values = [
                content,
                MEMORY_LAYER_LABELS.get(layer, layer),
                _format_memory_time(updated_at),
            ]
            self.memory_table.setItem(row, 0, select_item)
            self._set_memory_checkbox_widget(row, memory_id, is_checked)
            for column, value in enumerate(values, start=1):
                item = QTableWidgetItem(value)
                item.setFlags(Qt.ItemFlag.ItemIsEnabled)
                if column == 1:
                    item.setToolTip(f"{content}\n\nID: {memory_id}")
                elif column == 2:
                    item.setData(Qt.ItemDataRole.UserRole, layer)
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
        for column in range(self.memory_table.columnCount()):
            if self.memory_table.item(0, column) is None:
                self.memory_table.setItem(0, column, QTableWidgetItem(""))
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
        # 先进入编辑态(置 _active_memory_id),再刷新表格,refresh 会把该项钉到首行,
        # 最后滚动到顶部让被编辑项与详情面板同屏可见。
        self._open_memory_editor(row)
        self._refresh_memory_table()
        self.memory_table.scrollToTop()

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
        if not self._selected_memory_ids and self._memory_editor_mode == "edit":
            self._memory_editor_mode = None
            self._editing_memory_id = None
            self._active_memory_id = None
            self._clear_memory_editor()
            self._set_memory_editor_visible(False)
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
        _set_combo_current_data(self.memory_layer_combo, str(memory.get("layer") or DEFAULT_MEMORY_LAYER))
        self.memory_category_edit.setText(str(memory.get("category") or ""))
        self.memory_source_edit.setText(str(memory.get("source") or DEFAULT_MEMORY_SOURCE))
        self.memory_importance_spin.setValue(_float_value(memory.get("importance"), DEFAULT_MEMORY_IMPORTANCE))
        self.memory_confidence_spin.setValue(_float_value(memory.get("confidence"), DEFAULT_MEMORY_CONFIDENCE))
        self.memory_content_edit.setPlaceholderText("编辑长期记忆内容")
        self.memory_save_button.setText("保存修改")
        self._set_memory_editor_visible(True)
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
        color = _memory_row_background_color(row, checked, self.theme_settings)
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
            _set_combo_current_data(self.memory_layer_combo, DEFAULT_MEMORY_LAYER)
            self.memory_category_edit.clear()
            self.memory_source_edit.setText(DEFAULT_MEMORY_SOURCE)
            self.memory_importance_spin.setValue(DEFAULT_MEMORY_IMPORTANCE)
            self.memory_confidence_spin.setValue(DEFAULT_MEMORY_CONFIDENCE)
            self.memory_content_edit.setPlaceholderText("新增长期记忆内容")
            self.memory_save_button.setText("保存")
            self.memory_preview_label.setText("正在新增记忆")
            self._set_memory_editor_visible(True)
        elif self._memory_editor_mode == "new":
            self._memory_editor_mode = None
            self._editing_memory_id = None
            self._active_memory_id = None
            self._clear_memory_editor()
            self._set_memory_editor_visible(False)
            self._sync_memory_bulk_actions()
        self.memory_new_button.setText("收起新增" if checked else "新增记忆")

    def _clear_memory_selection(self) -> None:
        if not hasattr(self, "memory_table"):
            return
        self._selected_memory_ids.clear()
        if self._memory_editor_mode == "edit":
            self._memory_editor_mode = None
            self._editing_memory_id = None
            self._active_memory_id = None
            self._clear_memory_editor()
            self._set_memory_editor_visible(False)
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
        brush = _memory_row_background(row, checked, self.theme_settings)
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
        if hasattr(self, "memory_category_edit"):
            self.memory_category_edit.clear()
        if hasattr(self, "memory_source_edit"):
            self.memory_source_edit.setText(DEFAULT_MEMORY_SOURCE)
        if hasattr(self, "memory_importance_spin"):
            self.memory_importance_spin.setValue(DEFAULT_MEMORY_IMPORTANCE)
        if hasattr(self, "memory_confidence_spin"):
            self.memory_confidence_spin.setValue(DEFAULT_MEMORY_CONFIDENCE)
        if hasattr(self, "memory_layer_combo"):
            _set_combo_current_data(self.memory_layer_combo, DEFAULT_MEMORY_LAYER)

    def _set_memory_editor_visible(self, visible: bool) -> None:
        if not hasattr(self, "memory_editor_container"):
            return
        self.memory_editor_container.setVisible(visible)
        pane = getattr(self, "memory_editor_pane", None)
        splitter = getattr(self, "memory_list_splitter", None)
        if pane is None or splitter is None:
            return
        # 下窗格(选择行 + 编辑区)在 QSplitter 中:把内容/窗格最大高度都钉到自身 sizeHint,
        # 这样无论拖手柄还是初始分配都不会被撑出空白;多余纵向空间一律归上方的记忆列表。
        if visible:
            content_height = self.memory_editor_content.sizeHint().height()
            self.memory_editor_container.setMaximumHeight(content_height)
        pane.setMaximumHeight(16777215)
        pane_hint = pane.sizeHint().height()
        pane.setMaximumHeight(pane_hint)
        if not visible:
            return
        total = splitter.height()
        if total <= 0:
            return
        # 默认给下窗格刚好贴合的高度,其余留给列表;用户可再拖手柄进一步加长列表。
        bottom_height = min(pane_hint, max(80, total - 120))
        splitter.setSizes([total - bottom_height, bottom_height])

    def _save_memory_entry(self) -> None:
        if self.memory_store is None:
            return
        content = self.memory_content_edit.toPlainText().strip()
        if not content:
            QMessageBox.warning(self, "内容为空", "记忆内容不能为空。")
            return
        metadata = self._collect_memory_editor_metadata()
        try:
            if self._memory_editor_mode == "edit" and self._editing_memory_id:
                editing_id = self._editing_memory_id
                self.memory_store.update_memory(
                    {"id": editing_id, "content": content, **metadata},
                    allow_sensitive=True,
                )
                self._selected_memory_ids = {editing_id}
                self._active_memory_id = editing_id
                success_message = "记忆已更新。"
            else:
                self.memory_store.create_memory(
                    {"content": content, **metadata},
                    allow_sensitive=True,
                )
                self._memory_editor_mode = None
                self._editing_memory_id = None
                self._active_memory_id = None
                self._clear_memory_editor()
                self.memory_new_button.setChecked(False)
                success_message = "记忆已保存。"
        except (RuntimeError, ValueError) as exc:
            QMessageBox.warning(
                self,
                "保存失败",
                format_failure_message(
                    "这条长期记忆没有保存成功。",
                    "请确认长期记忆系统已经就绪，并检查内容后重试。",
                    exc,
                ),
            )
            return
        self._load_memory_entries()
        QMessageBox.information(self, "保存成功", success_message)

    def _collect_memory_editor_metadata(self) -> dict[str, object]:
        layer = DEFAULT_MEMORY_LAYER
        layer_combo = getattr(self, "memory_layer_combo", None)
        if layer_combo is not None:
            layer = str(layer_combo.currentData() or DEFAULT_MEMORY_LAYER)
        if layer not in MEMORY_LAYERS:
            layer = DEFAULT_MEMORY_LAYER
        source = DEFAULT_MEMORY_SOURCE
        source_edit = getattr(self, "memory_source_edit", None)
        if source_edit is not None:
            source = source_edit.text().strip() or DEFAULT_MEMORY_SOURCE
        category = ""
        category_edit = getattr(self, "memory_category_edit", None)
        if category_edit is not None:
            category = category_edit.text().strip()
        importance = DEFAULT_MEMORY_IMPORTANCE
        importance_spin = getattr(self, "memory_importance_spin", None)
        if importance_spin is not None:
            importance = float(importance_spin.value())
        confidence = DEFAULT_MEMORY_CONFIDENCE
        confidence_spin = getattr(self, "memory_confidence_spin", None)
        if confidence_spin is not None:
            confidence = float(confidence_spin.value())
        return {
            "layer": layer,
            "category": category,
            "importance": importance,
            "confidence": confidence,
            "source": source,
        }

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
            self._set_memory_editor_visible(False)
        self._clear_memory_selection()
        self._load_memory_entries()
        if failed:
            QMessageBox.warning(
                self,
                "删除完成",
                format_failure_message(
                    f"已删除 {deleted} 条记忆，另有 {len(failed)} 条删除失败。",
                    "请刷新记忆列表后重试失败项。",
                    "\n".join(failed),
                ),
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

    def _apply_theme_stylesheet(self, settings: ThemeSettings) -> None:
        theme = settings.normalized()
        self.theme_settings = theme
        stylesheet = build_settings_dialog_stylesheet(theme)
        # QSS 内容未变则跳过:setStyleSheet 会 re-polish 对话框内所有控件(含下拉弹层),
        # 切角色/只改视觉效果等「配色实际没变」的场景无需重绘。内联 label 颜色同样源自
        # theme,QSS 相同即这些颜色也相同,一并跳过安全。
        if stylesheet == self._applied_dialog_stylesheet:
            return
        self._applied_dialog_stylesheet = stylesheet
        self.setStyleSheet(stylesheet)
        inline_styles = {
            "theme_status_label": f"color: {theme.muted_text_color};",
            "memory_status_label": f"color: {theme.muted_text_color};",
            "memory_selection_label": f"color: {theme.secondary_text_color};",
            "memory_preview_label": f"color: {theme.text_color};",
            "system_restart_hint_label": f"color: {theme.muted_text_color};",
            "advanced_params_hint": f"color: {theme.secondary_text_color};",
        }
        for attr, style in inline_styles.items():
            widget = getattr(self, attr, None)
            if isinstance(widget, QLabel):
                widget.setStyleSheet(style)
        splitter = getattr(self, "memory_list_splitter", None)
        if splitter is not None and hasattr(splitter, "set_grip_colors"):
            grip = QColor(theme.border_color)
            grip.setAlpha(150)
            grip_hover = QColor(theme.primary_color)
            grip_hover.setAlpha(190)
            splitter.set_grip_colors(grip, grip_hover)

    def _choose_theme_color(self, edit: QLineEdit) -> None:
        current_color = QColor(normalize_hex_color(edit.text(), DEFAULT_THEME_SETTINGS.primary_color))
        color = QColorDialog.getColor(current_color, self, "选择主题颜色")
        if not color.isValid():
            return
        edit.setText(color.name())

    def _handle_visual_effect_changed(self, _index: int) -> None:
        """外观效果下拉框切换时标记主题为手动修改。"""
        if not self._syncing_theme_controls:
            self._theme_ai_enabled = False
            self._theme_write_mode = "manual"

    def _handle_theme_color_changed(self, edit: QLineEdit) -> None:
        if not self._syncing_theme_controls:
            self._theme_ai_enabled = False
            self._theme_write_mode = "manual"
        button = self._theme_button_for_edit(edit)
        normalized = normalize_hex_color(edit.text(), "")
        if button is not None and normalized:
            button.setStyleSheet(build_color_button_stylesheet(normalized))
        # 颜色按钮预览即时更新(便宜);整张对话框 QSS 的重建走防抖,避免逐字符 re-polish
        # 所有控件造成卡顿。程序化同步(_set_theme_controls)期间不调度,由其末尾统一应用。
        if not self._syncing_theme_controls:
            self._theme_stylesheet_debounce.start()

    @Slot()
    def _apply_pending_theme_stylesheet(self) -> None:
        # 防抖到点:按当前颜色框的最新值重建并应用一次对话框 QSS。
        theme = self._selected_theme_settings(show_error=False)
        if theme is not None:
            self._apply_theme_stylesheet(theme)

    def _theme_button_for_edit(self, edit: QLineEdit) -> QPushButton | None:
        for field, color_edit in getattr(self, "theme_color_edits", {}).items():
            button = getattr(self, "theme_color_buttons", {}).get(field)
            if color_edit is edit and isinstance(button, QPushButton):
                return button
        return None

    def _selected_theme_settings(self, *, show_error: bool = True) -> ThemeSettings | None:
        if not hasattr(self, "theme_color_edits"):
            return self.theme_settings
        normalized_values: dict[str, str] = {}
        for field, label, _default in THEME_COLOR_FIELDS:
            value = self.theme_color_edits[field].text()
            normalized = normalize_hex_color(value, "")
            if not normalized:
                if show_error:
                    QMessageBox.warning(self, "主题颜色无效", f"{label}必须是 #RRGGBB 格式。")
                return None
            normalized_values[field] = normalized
        visual_effect_mode = VisualEffectMode.DEFAULT
        combo = getattr(self, "theme_visual_effect_combo", None)
        if combo is not None and combo.currentData() is not None:
            visual_effect_mode = str(combo.currentData())
        return ThemeSettings(
            **normalized_values,
            ai_enabled=self._theme_ai_enabled,
            visual_effect_mode=visual_effect_mode,
        ).normalized()

    def _set_theme_controls(
        self, settings: ThemeSettings, *, sync_visual_effect: bool = False
    ) -> None:
        """将主题控件的颜色值同步到界面，可选择性同步视觉效果下拉框。

        sync_visual_effect 默认为 False：视觉效果是用户级偏好（角色主题只贡献配色），
        切换角色/AI配色/恢复默认配色均不覆盖用户手动选择的视觉效果。
        仅在对话框初始化（__init__）时传 True。
        """
        theme = settings.normalized()
        self._syncing_theme_controls = True
        try:
            for field, _label, _default in THEME_COLOR_FIELDS:
                self.theme_color_edits[field].setText(getattr(theme, field))
                self.theme_color_buttons[field].setStyleSheet(
                    build_color_button_stylesheet(getattr(theme, field))
                )
            if sync_visual_effect:
                combo = getattr(self, "theme_visual_effect_combo", None)
                if combo is not None:
                    idx = combo.findData(theme.visual_effect_mode)
                    if idx < 0:
                        idx = combo.findData(VisualEffectMode.GAUSSIAN_BLUR)
                    if idx >= 0:
                        combo.setCurrentIndex(idx)
        finally:
            self._syncing_theme_controls = False
        self._theme_ai_enabled = theme.ai_enabled
        self._apply_theme_stylesheet(theme)
        self._sync_theme_ai_controls()

    @Slot()
    def _reset_theme_colors(self) -> None:
        profile = self._selected_character_profile()
        if profile is None:
            self._set_theme_controls(ThemeSettings())
            self.theme_status_label.setText("已恢复默认 Sakura 粉色配色。")
        else:
            self._set_theme_controls(profile.theme_settings or DEFAULT_THEME_SETTINGS)
            if profile.theme_source == THEME_SOURCE_COMPAT_DEFAULT:
                self.theme_status_label.setText("已恢复默认 Sakura 粉色配色。")
            else:
                self.theme_status_label.setText(f"已恢复角色「{profile.display_name}」的默认主题。")
        self._theme_write_mode = "reset"

    @Slot()
    def _generate_ai_theme(self) -> None:
        if self._theme_ai_thread is not None:
            return
        api_settings = self._validated_api_settings()
        if api_settings is None:
            return
        profile = self._selected_character_profile()
        if profile is None:
            QMessageBox.warning(self, "角色无效", "请先选择一个角色。")
            return
        if not profile.default_portrait_path.exists():
            QMessageBox.warning(self, "立绘缺失", f"默认立绘不存在：{profile.default_portrait_path}")
            return

        self.theme_status_label.setText("正在根据默认立绘生成配色...")
        self._set_theme_ai_busy(True)
        thread = QThread(self)
        worker = settings_workers.ThemeAiWorker(
            api_settings,
            profile,
            ai_enabled=True,
        )
        worker.moveToThread(thread)
        self._theme_ai_thread = thread
        self._theme_ai_worker = worker
        thread.started.connect(worker.run)
        worker.succeeded.connect(self._handle_theme_ai_success)
        worker.failed.connect(self._handle_theme_ai_failed)
        worker.finished.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(self._reset_theme_ai_state)
        thread.start()

    @Slot(object)
    def _handle_theme_ai_success(self, settings: object) -> None:
        if not isinstance(settings, ThemeSettings):
            self._handle_theme_ai_failed("AI 返回的主题格式无效。")
            return
        self._set_theme_controls(settings)
        self._theme_write_mode = "ai"
        self.theme_status_label.setText("AI 配色已生成并应用预览。")

    @Slot(str)
    def _handle_theme_ai_failed(self, message: str) -> None:
        self.theme_status_label.setText(f"AI 配色失败，已保留当前配色：{message}")

    def _set_theme_ai_busy(self, busy: bool) -> None:
        if hasattr(self, "theme_ai_generate_button"):
            self.theme_ai_generate_button.setEnabled(
                not busy and self._theme_ai_generation_available()
            )
        if hasattr(self, "theme_reset_button"):
            self.theme_reset_button.setEnabled(not busy)
        save_button = self.button_box.button(QDialogButtonBox.StandardButton.Save)
        if save_button is not None:
            save_button.setEnabled(not busy)

    def _reset_theme_ai_state(self) -> None:
        self._theme_ai_thread = None
        self._theme_ai_worker = None
        self._set_theme_ai_busy(False)

    @Slot()
    def _sync_theme_ai_controls(self) -> None:
        if hasattr(self, "theme_ai_generate_button"):
            self.theme_ai_generate_button.setEnabled(
                self._theme_ai_thread is None and self._theme_ai_generation_available()
            )

    def _handle_character_selection_changed(self) -> None:
        profile = self._selected_character_profile()
        if profile is not None and hasattr(self, "theme_color_edits"):
            self._set_theme_controls(profile.theme_settings or DEFAULT_THEME_SETTINGS)
            self._theme_write_mode = "character"
            if hasattr(self, "theme_status_label"):
                self.theme_status_label.setText(f"已载入角色「{profile.display_name}」的主题。")
        self._sync_theme_ai_controls()
        self._sync_character_archive_controls()
        self._sync_voice_import_controls()

    def _theme_ai_generation_available(self) -> bool:
        profile = self._selected_character_profile()
        return profile is not None and profile.default_portrait_path.exists()

    def accept(self) -> None:
        if self._api_test_thread is not None:
            QMessageBox.information(self, "测试中", "API 测试仍在进行，请等待完成后再保存设置。")
            return
        if self._api_model_probe_thread is not None:
            QMessageBox.information(self, "检测中", "模型列表仍在检测，请等待完成后再保存设置。")
            return
        if self._tts_test_thread is not None:
            QMessageBox.information(self, "检测中", "TTS 服务检测仍在进行，请等待完成后再保存设置。")
            return
        if self._character_export_thread is not None:
            QMessageBox.information(self, "导出中", "角色包导出仍在进行，请等待完成后再保存设置。")
            return
        if self._theme_ai_thread is not None:
            QMessageBox.information(self, "AI 配色中", "AI 配色仍在生成，请等待完成后再保存设置。")
            return
        if self._memory_model_import_thread is not None:
            QMessageBox.information(self, "导入中", "记忆模型正在导入，请等待完成后再保存设置。")
            return
        if self._memory_model_download_thread is not None:
            QMessageBox.information(self, "安装中", "记忆模型正在在线安装，请等待完成后再保存设置。")
            return
        if self._backchannel_model_import_thread is not None:
            QMessageBox.information(self, "导入中", "接话模型正在导入，请等待完成后再保存设置。")
            return
        if self._backchannel_model_download_thread is not None:
            QMessageBox.information(self, "安装中", "接话模型正在在线安装，请等待完成后再保存设置。")
            return

        accept_values = self._collect_accept_values()
        if accept_values is None:
            return
        api_settings = accept_values["api_settings"]
        if isinstance(api_settings, ApiSettings) and self._should_test_api_on_accept(api_settings):
            self._start_api_settings_test(api_settings, accept_values)
            return

        self._continue_accept_after_api_test(accept_values)

    def _continue_accept_after_api_test(self, accept_values: dict[str, object]) -> None:
        tts_settings = accept_values["tts_settings"]
        if self._should_test_tts_on_accept(tts_settings, accept_values["character_id"]):
            self._start_tts_settings_test(tts_settings, accept_values)
            return
        self._complete_accept(accept_values)

    def _should_test_api_on_accept(self, api_settings: ApiSettings) -> bool:
        return api_settings != self._initial_api_settings

    def _should_test_tts_on_accept(
        self,
        tts_settings: object,
        character_id: object,
    ) -> bool:
        return (
            isinstance(tts_settings, GPTSoVITSTTSSettings)
            and tts_settings.enabled
            and isinstance(character_id, str)
            and (
                character_id != self._initial_character_id
                or tts_settings != self._initial_tts_settings
            )
        )

    def _collect_memory_curation_settings(self):
        from dataclasses import replace

        spin = getattr(self, "memory_trigger_turns_spin", None)
        if spin is None:
            # 记忆页未构建时回退到初始值，保留 backfill_limit 等未暴露字段。
            return self.memory_curation_settings
        # 自动整理始终开启，设置页只调整触发轮数。
        return replace(
            self.memory_curation_settings,
            enabled=True,
            trigger_turns=int(spin.value()),
        )

    def _collect_accept_values(self) -> dict[str, object] | None:
        api_settings = self._validated_api_settings()
        if api_settings is None:
            return None
        tts_settings = self._validated_tts_settings()
        if tts_settings is None:
            return None
        theme_settings = self._selected_theme_settings()
        if theme_settings is None:
            return None
        character_id = self._selected_character_id()
        if character_id is None:
            QMessageBox.warning(self, "配置无效", "请先导入并选择一个角色包。")
            return None

        subtitle_typing_interval_ms, reply_segment_pause_ms = normalize_subtitle_display_speed(
            self.subtitle_typing_interval_spin.value(),
            self.reply_segment_pause_spin.value(),
        )
        launch_at_login_supported = is_launch_at_login_supported()
        return {
            "api_settings": api_settings,
            "tts_settings": tts_settings,
            "character_id": character_id,
            "portrait_scale_percent": self._selected_portrait_scale_percent(),
            "control_panel_width": self._selected_control_panel_width(),
            "bubble_height": self._selected_bubble_height(),
            "control_panel_vertical_offset": self._selected_control_panel_vertical_offset(),
            "input_bar_offset": self._selected_input_bar_offset(),
            "subtitle_typing_interval_ms": subtitle_typing_interval_ms,
            "reply_segment_pause_ms": reply_segment_pause_ms,
            "theme_settings": theme_settings,
            "screen_awareness_settings": ScreenAwarenessSettings(
                enabled=self.proactive_screen_context_enabled_check.isChecked(),
                screen_context_enabled=self.proactive_screen_context_enabled_check.isChecked(),
                check_interval_minutes=self.proactive_check_interval_spin.value(),
                cooldown_minutes=self.proactive_cooldown_spin.value(),
                screen_context_batch_limit=self.proactive_batch_limit_spin.value(),
            ),
            "mcp_settings": MCPRuntimeSettings(
                windows_enabled=self.windows_mcp_enabled_check.isChecked(),
            ),
            "runtime_loop_settings": RuntimeLoopSettings(
                max_agent_steps_per_turn=self.agent_steps_per_turn_spin.value(),
                max_tool_calls_per_step=self.tool_calls_per_step_spin.value(),
                max_tool_calls_per_turn=self.tool_calls_per_turn_spin.value(),
            ).normalized(),
            "debug_log_settings": DebugLogSettings(
                enabled=self.debug_log_enabled_check.isChecked(),
                body_enabled=(
                    self.debug_log_enabled_check.isChecked()
                    and self.debug_body_enabled_check.isChecked()
                ),
                file_enabled=self.debug_file_enabled_check.isChecked(),
                stage_debug_overlay=self.stage_debug_overlay_check.isChecked(),
                stage_collision_mask=self.stage_collision_mask_check.isChecked(),
            ),
            "startup_settings": StartupSettings(
                launch_at_login=(
                    self.launch_at_login_check.isChecked()
                    if launch_at_login_supported
                    else self.startup_settings.launch_at_login
                ),
            ),
            "bubble_settings": BubbleSettings(
                auto_hide_enabled=self.bubble_auto_hide_check.isChecked(),
                auto_hide_delay_seconds=self.bubble_auto_hide_delay_spin.value(),
            ),
            "backchannel_settings": BackchannelSettings(
                enabled=self.backchannel_enabled_check.isChecked(),
                mode=str(self.backchannel_mode_combo.currentData() or self.backchannel_settings.mode),
                delay_ms=self.backchannel_delay_spin.value(),
                probability=self.backchannel_probability_spin.value(),
                tts_enabled=self.backchannel_tts_enabled_check.isChecked(),
                # timeout_ms 设置页不暴露，保存时保留 YAML 已配置值，避免覆盖回默认。
                timeout_ms=self.backchannel_settings.timeout_ms,
            ),
            "memory_curation_settings": self._collect_memory_curation_settings(),
        }

    def _complete_accept(self, values: dict[str, object]) -> None:
        api_settings = values["api_settings"]
        tts_settings = values["tts_settings"]
        character_id = values["character_id"]
        portrait_scale_percent = values["portrait_scale_percent"]
        control_panel_width = values["control_panel_width"]
        bubble_height = values["bubble_height"]
        control_panel_vertical_offset = values["control_panel_vertical_offset"]
        input_bar_offset = values["input_bar_offset"]
        subtitle_typing_interval_ms = values["subtitle_typing_interval_ms"]
        reply_segment_pause_ms = values["reply_segment_pause_ms"]
        theme_settings = values["theme_settings"]
        screen_awareness_settings = values["screen_awareness_settings"]
        mcp_settings = values["mcp_settings"]
        runtime_loop_settings = values["runtime_loop_settings"]
        debug_log_settings = values["debug_log_settings"]
        startup_settings = values["startup_settings"]
        bubble_settings = values["bubble_settings"]
        backchannel_settings = values["backchannel_settings"]
        memory_curation_settings = values["memory_curation_settings"]

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
        if not isinstance(theme_settings, ThemeSettings):
            return
        if not isinstance(screen_awareness_settings, ScreenAwarenessSettings):
            return
        if not isinstance(mcp_settings, MCPRuntimeSettings):
            return
        if not isinstance(runtime_loop_settings, RuntimeLoopSettings):
            return
        if not isinstance(debug_log_settings, DebugLogSettings):
            return
        if not isinstance(startup_settings, StartupSettings):
            return
        if not isinstance(bubble_settings, BubbleSettings):
            return
        if not isinstance(backchannel_settings, BackchannelSettings):
            return
        from app.agent.memory_curator import MemoryCurationSettings as _MemoryCurationSettings
        if not isinstance(memory_curation_settings, _MemoryCurationSettings):
            return

        try:
            plugin_config_changed = self._save_plugin_settings_if_needed()
        except OSError as exc:
            QMessageBox.critical(
                self,
                "保存失败",
                format_failure_message(
                    "插件启用配置没有保存成功。",
                    "请检查插件配置文件的写入权限和占用情况后重试。",
                    exc,
                ),
            )
            return

        self.result_api_settings = api_settings
        self.result_tts_settings = tts_settings
        self.result_character_id = character_id
        self.result_portrait_scale_percent = portrait_scale_percent
        self.result_control_panel_width = (
            control_panel_width
            if isinstance(control_panel_width, int)
            else self.control_panel_width
        )
        self.result_bubble_height = (
            bubble_height if isinstance(bubble_height, int) else self.bubble_height
        )
        self.result_control_panel_vertical_offset = (
            control_panel_vertical_offset
            if isinstance(control_panel_vertical_offset, int)
            else self.control_panel_vertical_offset
        )
        self.result_input_bar_offset = (
            input_bar_offset if isinstance(input_bar_offset, int) else self.input_bar_offset
        )
        self.result_subtitle_typing_interval_ms = subtitle_typing_interval_ms
        self.result_reply_segment_pause_ms = reply_segment_pause_ms
        self.result_theme_settings = theme_settings
        self.result_theme_write_mode = self._theme_write_mode
        self.result_screen_awareness_settings = screen_awareness_settings
        self.result_proactive_care_settings = screen_awareness_settings
        self.result_mcp_settings = mcp_settings
        self.result_runtime_loop_settings = runtime_loop_settings
        self.result_debug_log_settings = debug_log_settings
        self.result_startup_settings = startup_settings
        self.result_bubble_settings = bubble_settings
        self.result_backchannel_settings = backchannel_settings.normalized()
        self.result_memory_curation_settings = memory_curation_settings
        self.result_plugin_config_changed = plugin_config_changed
        super().accept()

    def _save_plugin_settings_if_needed(self) -> bool:
        enabled_by_id = self._selected_plugin_enabled_overrides()
        if not enabled_by_id:
            return False
        return save_plugin_enabled_overrides(self.base_dir, enabled_by_id)

    def reject(self) -> None:
        if self._api_test_thread is not None:
            QMessageBox.information(self, "测试中", "API 测试仍在进行，请等待完成后再关闭设置。")
            return
        if self._api_model_probe_thread is not None:
            QMessageBox.information(self, "检测中", "模型列表仍在检测，请等待完成后再关闭设置。")
            return
        if self._tts_test_thread is not None:
            QMessageBox.information(self, "检测中", "TTS 服务检测仍在进行，请等待完成后再关闭设置。")
            return
        if self._character_export_thread is not None:
            QMessageBox.information(self, "导出中", "角色包导出仍在进行，请等待完成后再关闭设置。")
            return
        if self._theme_ai_thread is not None:
            QMessageBox.information(self, "AI 配色中", "AI 配色仍在生成，请等待完成后再关闭设置。")
            return
        if self._memory_model_import_thread is not None:
            QMessageBox.information(self, "导入中", "记忆模型正在导入，请等待完成后再关闭设置。")
            return
        if self._memory_model_download_thread is not None:
            QMessageBox.information(self, "安装中", "记忆模型正在在线安装，请等待完成后再关闭设置。")
            return
        if self._backchannel_model_import_thread is not None:
            QMessageBox.information(self, "导入中", "接话模型正在导入，请等待完成后再关闭设置。")
            return
        if self._backchannel_model_download_thread is not None:
            QMessageBox.information(self, "安装中", "接话模型正在在线安装，请等待完成后再关闭设置。")
            return
        super().reject()

    def closeEvent(self, event):  # type: ignore[no-untyped-def]
        if self._api_test_thread is not None:
            QMessageBox.information(self, "测试中", "API 测试仍在进行，请等待完成后再关闭设置。")
            event.ignore()
            return
        if self._api_model_probe_thread is not None:
            QMessageBox.information(self, "检测中", "模型列表仍在检测，请等待完成后再关闭设置。")
            event.ignore()
            return
        if self._tts_test_thread is not None:
            QMessageBox.information(self, "检测中", "TTS 服务检测仍在进行，请等待完成后再关闭设置。")
            event.ignore()
            return
        if self._character_export_thread is not None:
            QMessageBox.information(self, "导出中", "角色包导出仍在进行，请等待完成后再关闭设置。")
            event.ignore()
            return
        if self._theme_ai_thread is not None:
            QMessageBox.information(self, "AI 配色中", "AI 配色仍在生成，请等待完成后再关闭设置。")
            event.ignore()
            return
        if self._memory_model_import_thread is not None:
            QMessageBox.information(self, "导入中", "记忆模型正在导入，请等待完成后再关闭设置。")
            event.ignore()
            return
        if self._memory_model_download_thread is not None:
            QMessageBox.information(self, "安装中", "记忆模型正在在线安装，请等待完成后再关闭设置。")
            event.ignore()
            return
        if self._backchannel_model_import_thread is not None:
            QMessageBox.information(self, "导入中", "接话模型正在导入，请等待完成后再关闭设置。")
            event.ignore()
            return
        if self._backchannel_model_download_thread is not None:
            QMessageBox.information(self, "安装中", "接话模型正在在线安装，请等待完成后再关闭设置。")
            event.ignore()
            return
        super().closeEvent(event)

    def _test_api_settings(self) -> None:
        settings = self._validated_api_settings()
        if (
            settings is None
            or self._api_test_thread is not None
            or self._api_model_probe_thread is not None
            or self._tts_test_thread is not None
        ):
            return

        self._start_api_settings_test(settings)

    def _start_api_settings_test(
        self,
        settings: ApiSettings,
        accept_values: dict[str, object] | None = None,
    ) -> None:
        if self._api_test_thread is not None or self._api_model_probe_thread is not None:
            return

        self._pending_api_accept_values = dict(accept_values) if accept_values is not None else None
        self._set_api_test_busy(True)
        thread = QThread()
        worker = settings_workers.ApiConnectionTestWorker(settings)
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
        accept_values = self._pending_api_accept_values
        if accept_values is not None:
            self._continue_accept_after_api_test(accept_values)
            return
        QMessageBox.information(self, "测试成功", f"API 连接成功，模型返回：{message}")

    @Slot(str)
    def _handle_api_test_failed(self, message: str) -> None:
        if self._pending_api_accept_values is not None:
            QMessageBox.warning(
                self,
                "API 检测失败",
                format_failure_message(
                    "API 连接检测失败，设置尚未保存。",
                    "请检查网络或代理，以及 Base URL、API Key 和模型名称后再保存。",
                    message,
                ),
            )
            return
        QMessageBox.warning(
            self,
            "测试失败",
            format_failure_message(
                "API 连接测试没有成功。",
                "请检查网络或代理，以及 Base URL、API Key 和模型名称后重试。",
                message,
            ),
        )

    @Slot()
    def _reset_api_test_state(self) -> None:
        self._api_test_thread = None
        self._api_test_worker = None
        self._pending_api_accept_values = None
        self._set_api_test_busy(False)

    def _set_api_test_busy(self, busy: bool) -> None:
        self.api_test_button.setEnabled(not busy)
        self.api_test_button.setText("测试中..." if busy else "测试 API")
        self.api_model_probe_button.setEnabled(not busy)
        if not hasattr(self, "button_box"):
            return
        save_button = self.button_box.button(QDialogButtonBox.StandardButton.Save)
        if save_button is None:
            return
        if busy:
            if self._save_button_text is None:
                self._save_button_text = save_button.text()
            save_button.setText("测试 API...")
        elif self._tts_test_thread is not None:
            return
        elif self._save_button_text is not None:
            save_button.setText(self._save_button_text)
            self._save_button_text = None

    def _probe_api_models(self) -> None:
        settings = self._validated_api_model_probe_settings()
        if (
            settings is None
            or self._api_model_probe_thread is not None
            or self._api_test_thread is not None
            or self._tts_test_thread is not None
        ):
            return
        self._set_api_model_probe_busy(True)
        thread = QThread()
        worker = settings_workers.ApiModelListProbeWorker(settings)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.succeeded.connect(self._handle_api_model_probe_success)
        worker.failed.connect(self._handle_api_model_probe_failed)
        worker.finished.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(self._reset_api_model_probe_state)

        self._api_model_probe_thread = thread
        self._api_model_probe_worker = worker
        thread.start()

    @Slot(list)
    def _handle_api_model_probe_success(self, model_names: list[str]) -> None:
        if not model_names:
            QMessageBox.warning(
                self,
                "探测失败",
                format_failure_message(
                    "API 服务返回了空的模型列表。",
                    "请确认服务提供 /models 接口，并检查当前账号是否有可用模型。",
                    "模型列表为空。",
                ),
            )
            return
        self.model_edit.set_model_names(model_names)
        QMessageBox.information(self, "探测成功", f"已发现 {len(model_names)} 个模型。")

    @Slot(str)
    def _handle_api_model_probe_failed(self, message: str) -> None:
        QMessageBox.warning(
            self,
            "探测失败",
            format_failure_message(
                "无法从 API 服务读取模型列表。",
                "请检查网络、代理、Base URL 和 API Key，并确认服务提供 /models 接口。",
                message,
            ),
        )

    @Slot()
    def _reset_api_model_probe_state(self) -> None:
        self._api_model_probe_thread = None
        self._api_model_probe_worker = None
        self._set_api_model_probe_busy(False)

    def _set_api_model_probe_busy(self, busy: bool) -> None:
        self.api_model_probe_button.setEnabled(not busy)
        self.api_model_probe_button.setText("检测中..." if busy else "检测模型")
        self.api_test_button.setEnabled(not busy)
        if not hasattr(self, "button_box"):
            return
        save_button = self.button_box.button(QDialogButtonBox.StandardButton.Save)
        if save_button is None:
            return
        if busy:
            if self._save_button_text is None:
                self._save_button_text = save_button.text()
            save_button.setText("检测模型...")
            save_button.setEnabled(False)
        elif self._api_test_thread is not None or self._tts_test_thread is not None:
            return
        elif self._save_button_text is not None:
            save_button.setText(self._save_button_text)
            self._save_button_text = None
        save_button.setEnabled(not busy)

    def _start_tts_settings_test(
        self,
        settings: GPTSoVITSTTSSettings,
        accept_values: dict[str, object],
    ) -> None:
        if self._tts_test_thread is not None:
            return

        self._pending_accept_values = dict(accept_values)
        self._set_tts_test_busy(True)

        thread = QThread()
        worker = settings_workers.TTSTestWorker(settings, base_dir=self.base_dir)
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
            format_failure_message(
                "TTS 服务检测失败，但 TTS 设置会保留并继续保存。",
                "请重启本地 TTS 服务，并检查服务地址、工作目录、Python 和模型路径。",
                message,
            ),
        )
        accept_values["tts_settings"] = original_settings
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
        python_path = getattr(dialog, "downloaded_python_path", None)
        tts_config_path = getattr(dialog, "downloaded_tts_config_path", None)
        provider_index = self.tts_provider_combo.findData(provider)
        if provider_index >= 0:
            self.tts_provider_combo.setCurrentIndex(provider_index)
        self.tts_work_dir_edit.setText(str(dialog.downloaded_work_dir))
        if python_path is not None:
            self.tts_python_path_edit.setText(str(python_path))
        else:
            self.tts_python_path_edit.setText(_bundle_python_path_display(provider, dialog.downloaded_work_dir))
        if tts_config_path is not None:
            self.tts_config_path_edit.setText(str(tts_config_path))
        else:
            self.tts_config_path_edit.setText(_bundle_tts_config_display(provider, dialog.downloaded_work_dir))
        self.tts_api_url_edit.setText(_default_tts_api_url(provider))
        self.tts_enabled_check.setChecked(True)
        self._sync_tts_provider_controls()

    @Slot()
    def _sync_tts_provider_controls(self, *, apply_defaults: bool = False) -> None:
        provider = str(self.tts_provider_combo.currentData() or TTS_PROVIDER_GPT_SOVITS)
        self.tts_api_url_edit.setPlaceholderText(_default_tts_api_url(provider))
        if provider == TTS_PROVIDER_GENIE:
            self.tts_work_dir_edit.setPlaceholderText("tts/cpu")
        elif provider == TTS_PROVIDER_CUSTOM_GPT_SOVITS:
            self.tts_work_dir_edit.setPlaceholderText("外部 GPT-SoVITS 源码目录，可留空")
        else:
            self.tts_work_dir_edit.setPlaceholderText("tts/g50")
        bundled = _is_bundled_tts_provider(provider)
        self.tts_api_url_edit.setReadOnly(bundled)
        self.tts_work_dir_edit.setReadOnly(bundled)
        self.tts_python_path_edit.setReadOnly(bundled or provider == TTS_PROVIDER_GENIE)
        self.tts_config_path_edit.setReadOnly(bundled or provider == TTS_PROVIDER_GENIE)
        if bundled and apply_defaults:
            self.tts_api_url_edit.setText(_default_tts_api_url(provider))
            work_dir = default_provider_bundle_work_dir(provider, self.base_dir)
            self.tts_work_dir_edit.setText(str(work_dir or ""))
            self.tts_python_path_edit.setText(_bundle_python_path_display(provider, work_dir))
            self.tts_config_path_edit.setText(_bundle_tts_config_display(provider, work_dir))
        elif provider == TTS_PROVIDER_CUSTOM_GPT_SOVITS and apply_defaults:
            work_dir = _optional_path(self.tts_work_dir_edit.text(), self.base_dir)
            if work_dir is not None and is_provider_bundle_work_dir(work_dir, self.base_dir):
                self.tts_work_dir_edit.clear()
            self.tts_python_path_edit.clear()
            self.tts_config_path_edit.clear()
        self._sync_tts_enabled_controls(self.tts_enabled_check.isChecked())

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
            self._handle_character_selection_changed()
            self._sync_character_archive_controls()
            imported_profile = self._selected_character_profile()
        except (CharacterArchiveError, OSError, ValueError) as exc:
            QMessageBox.warning(
                self,
                "导入失败",
                format_failure_message(
                    "角色包没有成功导入。",
                    "请确认角色包完整、格式正确，并检查 characters 目录的写入权限。",
                    exc,
                ),
            )
            return
        if imported_profile is not None and imported_profile.voice is None:
            self.tts_enabled_check.setChecked(False)
            QMessageBox.information(
                self,
                "导入成功",
                (
                    f"已导入角色「{result.display_name}」。该角色没有语音包，TTS 已自动关闭。"
                    "可稍后导入 .voice 语音包。点击保存后会切换到该角色。"
                ),
            )
        else:
            QMessageBox.information(
                self,
                "导入成功",
                f"已导入角色「{result.display_name}」。点击保存后会切换到该角色。",
            )

    def _import_character_voice_archive(self) -> None:
        if self._character_export_thread is not None:
            QMessageBox.information(self, "导出中", "角色包导出仍在进行，请等待完成后再导入语音包。")
            return
        profile = self._selected_character_profile()
        if profile is None:
            QMessageBox.warning(self, "导入失败", "请先导入并选择一个角色。")
            return
        path_text, _ = QFileDialog.getOpenFileName(
            self,
            "导入 Sakura TTS 模型包",
            str(self.base_dir),
            "Sakura TTS 模型包 (*.voice)",
        )
        if not path_text:
            return
        try:
            result = import_character_voice_archive(Path(path_text), self.base_dir, profile.id)
            self.character_registry = CharacterRegistry(self.base_dir)
            self._refresh_character_combo(result.character_id)
            imported_profile = self._selected_character_profile()
            self._sync_voice_import_controls()
        except (CharacterArchiveError, OSError, ValueError) as exc:
            QMessageBox.warning(
                self,
                "导入失败",
                format_failure_message(
                    "角色语音包没有成功导入。",
                    "请确认语音包与当前角色匹配、文件完整，并检查写入权限。",
                    exc,
                ),
            )
            return
        QMessageBox.information(
            self,
            "导入成功",
            f"已为角色「{result.display_name}」导入 TTS 模型包。",
        )

    def _export_current_character_archive(self, export_kind: Literal["full", "card", "voice"] = "full") -> None:
        if self._character_export_thread is not None:
            return
        profile = self._selected_character_profile()
        if profile is None:
            QMessageBox.warning(self, "导出失败", "当前没有可导出的角色。")
            return
        if export_kind in ("full", "voice") and not settings_workers._has_exportable_voice_model(profile):
            if export_kind == "full":
                QMessageBox.warning(
                    self,
                    "导出失败",
                    "当前角色没有完整语音模型，请使用“导出单角色包 (.char)”导出角色人格和立绘。",
                )
            else:
                QMessageBox.warning(self, "导出失败", "当前角色没有可导出的语音模型。")
            return
        if export_kind == "voice":
            title = "导出 Sakura TTS 模型包"
            default_name = f"{profile.id}.voice"
            file_filter = "Sakura TTS 模型包 (*.voice)"
            suffix = ".voice"
        elif export_kind == "card":
            title = "导出 Sakura 单角色包"
            default_name = f"{profile.id}.card.char"
            file_filter = "Sakura 角色包 (*.char)"
            suffix = ".char"
        else:
            title = "导出 Sakura 完整角色包"
            default_name = f"{profile.id}.char"
            file_filter = "Sakura 角色包 (*.char)"
            suffix = ".char"
        output_text, _ = QFileDialog.getSaveFileName(
            self,
            title,
            str(self.base_dir / default_name),
            file_filter,
        )
        if not output_text:
            return
        output_path = Path(output_text)
        if output_path.suffix.lower() != suffix:
            output_path = output_path.with_suffix(suffix)
        self._start_character_archive_export(profile, output_path, export_kind)

    def _start_character_archive_export(
        self,
        profile: CharacterProfile,
        output_path: Path,
        export_kind: Literal["full", "card", "voice"] = "full",
    ) -> None:
        self._set_character_export_busy(True)
        thread = QThread()
        worker = settings_workers.CharacterArchiveExportWorker(profile, output_path, export_kind)
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
        QMessageBox.warning(
            self,
            "导出失败",
            format_failure_message(
                "角色包没有成功导出。",
                "请检查目标目录的空间、写入权限和文件占用情况后重试。",
                message,
            ),
        )

    @Slot()
    def _reset_character_export_state(self) -> None:
        self._character_export_thread = None
        self._character_export_worker = None
        self._set_character_export_busy(False)

    def _set_character_export_busy(self, busy: bool) -> None:
        profile = self._selected_character_profile()
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
            self.character_export_button.setEnabled(not busy and profile is not None)
        self._sync_character_export_actions(profile=profile, busy=busy)
        if hasattr(self, "tts_voice_import_button"):
            self._sync_voice_import_controls()

    def _sync_character_archive_controls(self) -> None:
        self._set_character_export_busy(self._character_export_thread is not None)

    def _sync_character_export_actions(
        self,
        *,
        profile: CharacterProfile | None = None,
        busy: bool | None = None,
    ) -> None:
        if not hasattr(self, "character_export_full_action"):
            return
        if profile is None:
            profile = self._selected_character_profile()
        if busy is None:
            busy = self._character_export_thread is not None
        has_profile = profile is not None
        has_voice_model = settings_workers._has_exportable_voice_model(profile)
        self.character_export_full_action.setEnabled(not busy and has_voice_model)
        self.character_export_card_action.setEnabled(not busy and has_profile)
        self.character_export_voice_action.setEnabled(not busy and has_voice_model)
        if not has_profile:
            self.character_export_full_action.setToolTip("当前没有可导出的角色。")
            self.character_export_card_action.setToolTip("当前没有可导出的角色。")
            self.character_export_voice_action.setToolTip("当前没有可导出的角色。")
        elif has_voice_model:
            self.character_export_full_action.setToolTip("导出当前角色的人格、立绘与语音模型。")
            self.character_export_card_action.setToolTip("导出当前角色的人格与立绘，不包含语音模型。")
            self.character_export_voice_action.setToolTip("导出当前角色的 .voice TTS 模型包。")
        else:
            self.character_export_full_action.setToolTip("当前角色没有完整语音模型，只能导出单角色包。")
            self.character_export_card_action.setToolTip("导出当前角色的人格与立绘，不包含语音模型。")
            self.character_export_voice_action.setToolTip("当前角色没有可导出的语音模型。")

    def _validated_api_settings(self) -> ApiSettings | None:
        base_url = self.base_url_edit.text().strip().rstrip("/")
        api_key = self.api_key_edit.text().strip()
        model = self.model_edit.text().strip()
        temperature = self.llm_temperature_spin.value()
        if (
            self._initial_api_settings.temperature is None
            and abs(temperature - 0.8) < 0.005
        ):
            temperature = None

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
            temperature=temperature,
            top_p=(
                self.llm_top_p_spin.value()
                if self.llm_top_p_enabled_check.isChecked()
                else None
            ),
            max_tokens=(
                self.llm_max_tokens_spin.value()
                if self.llm_max_tokens_enabled_check.isChecked()
                else None
            ),
        )

    def _validated_api_model_probe_settings(self) -> ApiSettings | None:
        base_url = self.base_url_edit.text().strip().rstrip("/")
        api_key = self.api_key_edit.text().strip()

        if not _is_http_url(base_url):
            QMessageBox.warning(self, "配置无效", "Base URL 必须是有效的 http 或 https 地址。")
            return None
        if not api_key:
            QMessageBox.warning(self, "配置无效", "API Key 不能为空。")
            return None

        return ApiSettings(
            base_url=base_url,
            api_key=api_key,
            model=self.model_edit.text().strip(),
            timeout_seconds=self.api_timeout_spin.value(),
        )

    def _validated_tts_settings(
        self,
        *,
        show_warnings: bool = True,
        validate_enabled: bool = True,
    ) -> GPTSoVITSTTSSettings | None:
        enabled = self.tts_enabled_check.isChecked()
        provider = str(self.tts_provider_combo.currentData() or TTS_PROVIDER_GPT_SOVITS)
        bundled = _is_bundled_tts_provider(provider)
        api_url = self.tts_api_url_edit.text().strip()
        work_dir = _optional_path(self.tts_work_dir_edit.text(), self.base_dir)
        python_path = None if bundled else _optional_path(self.tts_python_path_edit.text(), self.base_dir)
        tts_config_path = None if bundled else _optional_path(self.tts_config_path_edit.text(), self.base_dir)
        selected_profile = self._selected_character_profile()
        selected_voice = selected_profile.voice if selected_profile is not None else None
        ref_lang = (selected_voice.ref_lang if selected_voice is not None else self.tts_settings.ref_lang) or "ja"
        text_lang = (selected_voice.text_lang if selected_voice is not None else self.tts_settings.text_lang) or "ja"

        if enabled and selected_profile is not None and selected_profile.voice is None:
            enabled = False
            if show_warnings:
                self.tts_enabled_check.setChecked(False)
                QMessageBox.warning(
                    self,
                    "TTS 已关闭",
                    "当前角色没有语音包，TTS 已自动关闭。请先导入 .voice 语音包后再启用 TTS。",
                )

        if enabled and not _is_http_url(api_url):
            if show_warnings:
                QMessageBox.warning(self, "配置无效", "TTS API URL 必须是有效的 http 或 https 地址。")
            return None

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
                python_path=python_path,
                tts_config_path=tts_config_path,
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
                python_path=python_path,
                tts_config_path=tts_config_path,
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
        if enabled and validate_enabled:
            try:
                settings.validate()
            except TTSConfigError as exc:
                if show_warnings:
                    QMessageBox.warning(
                        self,
                        "配置无效",
                        format_failure_message(
                            "TTS 配置无法通过检查。",
                            "请检查 Python、工作目录、模型、推理配置和参考音频路径。",
                            exc,
                        ),
                    )
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

    def _selected_control_panel_width(self) -> int:
        if hasattr(self, "control_panel_width_spin"):
            return normalize_control_panel_width(self.control_panel_width_spin.value())
        return self.control_panel_width

    def _selected_bubble_height(self) -> int:
        if hasattr(self, "bubble_height_spin"):
            return normalize_bubble_height(self.bubble_height_spin.value())
        return self.bubble_height

    def _selected_control_panel_vertical_offset(self) -> int:
        if hasattr(self, "control_panel_offset_spin"):
            return normalize_control_panel_vertical_offset(
                self.control_panel_offset_spin.value()
            )
        return self.control_panel_vertical_offset

    def _selected_input_bar_offset(self) -> int:
        if hasattr(self, "input_bar_offset_spin"):
            return normalize_input_bar_offset(self.input_bar_offset_spin.value())
        return self.input_bar_offset

    def _emit_layout_preview(self, *_args) -> None:  # type: ignore[no-untyped-def]
        """立绘/控制组滑块变化时，实时把当前取值回调给宿主窗口预览（不持久化）。"""
        callback = getattr(self, "_on_layout_preview", None)
        if callback is None:
            return
        callback(
            self._selected_portrait_scale_percent(),
            self._selected_control_panel_width(),
            self._selected_bubble_height(),
            self._selected_control_panel_vertical_offset(),
            self._selected_input_bar_offset(),
        )

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
        self._sync_theme_ai_controls()
        self._sync_voice_import_controls()


def _is_http_url(url: str) -> bool:
    parsed_url = urlparse(url)
    return parsed_url.scheme in {"http", "https"} and bool(parsed_url.netloc)


def _combo_int_data(combo: object, *, default: int) -> int:
    current_data = getattr(combo, "currentData", lambda: None)()
    try:
        return int(current_data)
    except (TypeError, ValueError):
        return default


def _default_tts_api_url(provider: str) -> str:
    return DEFAULT_GENIE_TTS_API_URL if provider == TTS_PROVIDER_GENIE else DEFAULT_GPT_SOVITS_API_URL


def _is_bundled_tts_provider(provider: str) -> bool:
    return provider in {TTS_PROVIDER_GPT_SOVITS, TTS_PROVIDER_GENIE}


def _bundle_python_path_display(provider: str, work_dir: Path | None) -> str:
    if not _is_bundled_tts_provider(provider) or work_dir is None:
        return ""
    return str(work_dir / "runtime" / "python.exe")


def _bundle_tts_config_display(provider: str, work_dir: Path | None) -> str:
    if provider == TTS_PROVIDER_GPT_SOVITS and work_dir is not None:
        return str(work_dir / "GPT_SoVITS" / "configs" / "tts_infer.yaml")
    if provider == TTS_PROVIDER_GENIE:
        return "Genie TTS 整合包内置，无需单独配置"
    return ""


def _default_genie_onnx_dir(base_dir: Path, profile: CharacterProfile | None) -> Path:
    character_id = profile.id if profile is not None else "default"
    return StoragePaths(base_dir).tts_bundle_onnx_for(character_id)


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


def _memory_row_background(row: int, checked: bool, theme: ThemeSettings) -> QBrush:
    return QBrush(QColor(_memory_row_background_color(row, checked, theme)))


def _memory_row_background_color(row: int, checked: bool, theme: ThemeSettings) -> str:
    """根据主题配色计算记忆表格行的背景色。"""
    if checked:
        return mix(theme.panel_background_color, theme.primary_color, 0.22)
    if row % 2:
        return mix(theme.page_background_color, "#ffffff", 0.35)
    return mix(theme.page_background_color, "#ffffff", 0.70)


def _sort_memories_by_latest_time(
    memories: list[dict[str, object]],
) -> list[dict[str, object]]:
    """按更新时间倒序排列记忆，缺少更新时间时使用创建时间。"""
    return sorted(memories, key=_memory_latest_time_sort_key, reverse=True)


def _memory_latest_time_sort_key(memory: dict[str, object]) -> float:
    for field in ("updated_at", "created_at"):
        parsed = _parse_memory_time(str(memory.get(field) or ""))
        if parsed is not None:
            return parsed
    return float("-inf")


def _parse_memory_time(value: str) -> float | None:
    text = value.strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()
    except (OSError, ValueError):
        return None


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


def _format_memory_score(value: object, default: float) -> str:
    return f"{_float_value(value, default):.2f}"


def _float_value(value: object, default: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return min(1.0, max(0.0, number))


def _set_combo_current_data(combo: object, value: str) -> None:
    finder = getattr(combo, "findData", None)
    setter = getattr(combo, "setCurrentIndex", None)
    if not callable(finder) or not callable(setter):
        return
    index = finder(value)
    setter(index if index >= 0 else 0)
