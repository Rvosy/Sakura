"""设置窗口页面构建类。

这些类只负责构造 QWidget 与把控件挂回 SettingsDialog；保存、校验、
异步 Worker 生命周期仍由 SettingsDialog 统一管理。
"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QFormLayout,
    QGroupBox,
    QHeaderView,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QPushButton,
    QTableWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from app.agent.mcp import MCPRuntimeSettings, WINDOWS_MCP_EXPERIMENTAL_TEXT
from app.agent.memory import MemoryStore
from app.agent.proactive_care import (
    PROACTIVE_MAX_COOLDOWN_MINUTES,
    PROACTIVE_MAX_CHECK_INTERVAL_MINUTES,
    PROACTIVE_MAX_SCREEN_CONTEXT_BATCH_LIMIT,
    PROACTIVE_MIN_COOLDOWN_MINUTES,
    PROACTIVE_MIN_CHECK_INTERVAL_MINUTES,
    PROACTIVE_MIN_SCREEN_CONTEXT_BATCH_LIMIT,
    ProactiveCareSettings,
)
from app.config.character_loader import CharacterProfile, CharacterRegistry
from app.config.settings_service import (
    BUBBLE_AUTO_HIDE_MAX_DELAY_SECONDS,
    BUBBLE_AUTO_HIDE_MIN_DELAY_SECONDS,
    BubbleSettings,
    DebugLogSettings,
    StartupSettings,
)
from app.llm.api_client import ApiSettings
from app.platforms.launch_at_login import (
    is_launch_at_login_supported,
    launch_at_login_platform_text,
)
from app.plugins.models import SettingsPanelContribution, ToolsTabContribution
from app.ui.control_panel_layout import (
    DEFAULT_BUBBLE_HEIGHT,
    DEFAULT_CONTROL_PANEL_VERTICAL_OFFSET,
    DEFAULT_CONTROL_PANEL_WIDTH,
    DEFAULT_INPUT_BAR_OFFSET,
    MAX_BUBBLE_HEIGHT,
    MAX_CONTROL_PANEL_VERTICAL_OFFSET,
    MAX_CONTROL_PANEL_WIDTH,
    MAX_INPUT_BAR_OFFSET,
    MIN_BUBBLE_HEIGHT,
    MIN_CONTROL_PANEL_VERTICAL_OFFSET,
    MIN_CONTROL_PANEL_WIDTH,
    MIN_INPUT_BAR_OFFSET,
)
from app.ui.portrait_controller import (
    PORTRAIT_SCALE_MAX_PERCENT,
    PORTRAIT_SCALE_MIN_PERCENT,
)
from app.ui.settings.widgets import (
    ModelComboBox,
    _NoWheelComboBox,
    _NoWheelDoubleSpinBox,
    _NoWheelSlider,
    _NoWheelSpinBox,
)
from app.ui.subtitle_controller import (
    REPLY_SEGMENT_PAUSE_MAX_MS,
    REPLY_SEGMENT_PAUSE_MIN_MS,
    SUBTITLE_TYPING_INTERVAL_MAX_MS,
    SUBTITLE_TYPING_INTERVAL_MIN_MS,
)
from app.ui.theme import THEME_COLOR_FIELDS, build_color_button_stylesheet
from app.ui.window_backdrop import VisualEffectMode
from app.voice.tts_settings import (
    DEFAULT_GENIE_TTS_API_URL,
    DEFAULT_GPT_SOVITS_API_URL,
    GPTSoVITSTTSSettings,
    TTS_PROVIDER_CUSTOM_GPT_SOVITS,
    TTS_PROVIDER_GENIE,
    TTS_PROVIDER_GPT_SOVITS,
)

MEMORY_READING_TEXT = "正在读取长期记忆..."


class CharacterSettingsPage:
    def __init__(self, dialog: Any) -> None:
        self.dialog = dialog

    def build(
        self,
        character_registry: CharacterRegistry | None,
        current_character: CharacterProfile | None,
    ) -> QWidget:
        owner = self.dialog
        tab = QWidget(owner)
        owner.character_combo = _NoWheelComboBox(tab)
        owner.character_empty_label = QLabel("尚未导入角色", tab)
        owner._refresh_character_combo(
            current_character.id if current_character is not None else None
        )
        owner.character_combo.currentIndexChanged.connect(
            lambda _index: owner._handle_character_selection_changed()
        )
        _ = character_registry

        form_layout = QFormLayout()
        form_layout.setContentsMargins(16, 18, 16, 16)
        form_layout.setSpacing(12)
        form_layout.addRow("状态", owner.character_empty_label)
        form_layout.addRow("当前角色", owner.character_combo)
        form_layout.addRow("立绘大小", self._build_portrait_scale_control(tab))
        form_layout.addRow("对话框宽度", self._build_control_panel_width_control(tab))
        form_layout.addRow("气泡高度", self._build_bubble_height_control(tab))
        form_layout.addRow("气泡上下位置", self._build_control_panel_offset_control(tab))
        form_layout.addRow("输入框下移", self._build_input_bar_offset_control(tab))
        form_layout.addRow("角色包", self._build_character_archive_controls(tab))
        tab.setLayout(form_layout)
        owner._sync_character_archive_controls()
        return tab

    def _build_character_archive_controls(self, parent: QWidget) -> QWidget:
        owner = self.dialog
        container = QWidget(parent)
        owner.character_import_button = QPushButton("导入 .char", container)
        owner.tts_voice_import_button = QPushButton("导入 .voice", container)
        owner.tts_voice_import_button.setToolTip("为当前选中的角色导入单独的 TTS 模型包。")
        owner.character_export_button = QPushButton("导出", container)
        owner.character_export_menu = QMenu(owner.character_export_button)
        _prepare_popup_menu(owner.character_export_menu)
        owner.character_export_full_action = QAction("导出完整包 (.char)", owner)
        owner.character_export_card_action = QAction("导出单角色包 (.char)", owner)
        owner.character_export_voice_action = QAction("导出语音包 (.voice)", owner)
        owner.character_export_full_action.triggered.connect(
            lambda _checked=False: owner._export_current_character_archive("full")
        )
        owner.character_export_card_action.triggered.connect(
            lambda _checked=False: owner._export_current_character_archive("card")
        )
        owner.character_export_voice_action.triggered.connect(
            lambda _checked=False: owner._export_current_character_archive("voice")
        )
        owner.character_export_menu.addAction(owner.character_export_full_action)
        owner.character_export_menu.addAction(owner.character_export_card_action)
        owner.character_export_menu.addAction(owner.character_export_voice_action)
        owner.character_export_button.setMenu(owner.character_export_menu)
        owner.character_import_button.clicked.connect(owner._import_character_archive)
        owner.tts_voice_import_button.clicked.connect(owner._import_character_voice_archive)
        owner._sync_character_archive_controls()

        layout = QHBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        layout.addWidget(owner.character_import_button)
        layout.addWidget(owner.tts_voice_import_button)
        layout.addWidget(owner.character_export_button)
        layout.addStretch(1)
        container.setLayout(layout)
        return container

    def _build_portrait_scale_control(self, parent: QWidget) -> QWidget:
        owner = self.dialog
        container = QWidget(parent)
        owner.portrait_scale_slider = _NoWheelSlider(Qt.Orientation.Horizontal, container)
        owner.portrait_scale_slider.setRange(
            PORTRAIT_SCALE_MIN_PERCENT,
            PORTRAIT_SCALE_MAX_PERCENT,
        )
        owner.portrait_scale_slider.setSingleStep(5)
        owner.portrait_scale_slider.setPageStep(10)
        owner.portrait_scale_slider.setTickInterval(25)
        owner.portrait_scale_slider.setTickPosition(_NoWheelSlider.TickPosition.TicksBelow)
        owner.portrait_scale_slider.setValue(owner.portrait_scale_percent)

        owner.portrait_scale_spin = _NoWheelSpinBox(container)
        owner.portrait_scale_spin.setRange(
            PORTRAIT_SCALE_MIN_PERCENT,
            PORTRAIT_SCALE_MAX_PERCENT,
        )
        owner.portrait_scale_spin.setSingleStep(5)
        owner.portrait_scale_spin.setSuffix("%")
        owner.portrait_scale_spin.setValue(owner.portrait_scale_percent)

        owner.portrait_scale_slider.valueChanged.connect(owner.portrait_scale_spin.setValue)
        owner.portrait_scale_spin.valueChanged.connect(owner.portrait_scale_slider.setValue)
        owner.portrait_scale_spin.valueChanged.connect(owner._emit_layout_preview)

        layout = QHBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        layout.addWidget(owner.portrait_scale_slider, 1)
        layout.addWidget(owner.portrait_scale_spin)
        container.setLayout(layout)
        return container

    def _build_range_control(
        self,
        parent: QWidget,
        *,
        slider_attr: str,
        spin_attr: str,
        minimum: int,
        maximum: int,
        value: int,
        single_step: int,
        suffix: str = "",
    ) -> QWidget:
        owner = self.dialog
        container = QWidget(parent)
        slider = _NoWheelSlider(Qt.Orientation.Horizontal, container)
        slider.setRange(minimum, maximum)
        slider.setSingleStep(single_step)
        slider.setPageStep(single_step * 2)
        slider.setValue(value)

        spin = _NoWheelSpinBox(container)
        spin.setRange(minimum, maximum)
        spin.setSingleStep(single_step)
        if suffix:
            spin.setSuffix(suffix)
        spin.setValue(value)

        slider.valueChanged.connect(spin.setValue)
        spin.valueChanged.connect(slider.setValue)
        spin.valueChanged.connect(owner._emit_layout_preview)

        setattr(owner, slider_attr, slider)
        setattr(owner, spin_attr, spin)

        layout = QHBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        layout.addWidget(slider, 1)
        layout.addWidget(spin)
        container.setLayout(layout)
        return container

    def _build_control_panel_width_control(self, parent: QWidget) -> QWidget:
        return self._build_range_control(
            parent,
            slider_attr="control_panel_width_slider",
            spin_attr="control_panel_width_spin",
            minimum=MIN_CONTROL_PANEL_WIDTH,
            maximum=MAX_CONTROL_PANEL_WIDTH,
            value=self.dialog.control_panel_width,
            single_step=10,
            suffix=" px",
        )

    def _build_bubble_height_control(self, parent: QWidget) -> QWidget:
        return self._build_range_control(
            parent,
            slider_attr="bubble_height_slider",
            spin_attr="bubble_height_spin",
            minimum=MIN_BUBBLE_HEIGHT,
            maximum=MAX_BUBBLE_HEIGHT,
            value=self.dialog.bubble_height,
            single_step=4,
            suffix=" px",
        )

    def _build_control_panel_offset_control(self, parent: QWidget) -> QWidget:
        return self._build_range_control(
            parent,
            slider_attr="control_panel_offset_slider",
            spin_attr="control_panel_offset_spin",
            minimum=MIN_CONTROL_PANEL_VERTICAL_OFFSET,
            maximum=MAX_CONTROL_PANEL_VERTICAL_OFFSET,
            value=self.dialog.control_panel_vertical_offset,
            single_step=10,
            suffix=" px",
        )

    def _build_input_bar_offset_control(self, parent: QWidget) -> QWidget:
        return self._build_range_control(
            parent,
            slider_attr="input_bar_offset_slider",
            spin_attr="input_bar_offset_spin",
            minimum=MIN_INPUT_BAR_OFFSET,
            maximum=MAX_INPUT_BAR_OFFSET,
            value=self.dialog.input_bar_offset,
            single_step=10,
            suffix=" px",
        )


class ThemeSettingsPage:
    def __init__(self, dialog: Any) -> None:
        self.dialog = dialog

    def build(self) -> QWidget:
        owner = self.dialog
        tab = QWidget(owner)
        owner.theme_color_edits: dict[str, QLineEdit] = {}
        owner.theme_color_buttons: dict[str, QPushButton] = {}

        owner.theme_ai_generate_button = QPushButton("AI 生成配色", tab)
        owner.theme_ai_generate_button.clicked.connect(owner._generate_ai_theme)
        owner.theme_reset_button = QPushButton("恢复默认配色", tab)
        owner.theme_reset_button.clicked.connect(owner._reset_theme_colors)
        owner.theme_status_label = QLabel("", tab)
        owner.theme_status_label.setWordWrap(True)

        button_row = QWidget(tab)
        button_layout = QHBoxLayout()
        button_layout.setContentsMargins(0, 0, 0, 0)
        button_layout.setSpacing(10)
        button_layout.addWidget(owner.theme_ai_generate_button)
        button_layout.addWidget(owner.theme_reset_button)
        button_layout.addStretch(1)
        button_row.setLayout(button_layout)

        form_layout = QFormLayout()
        form_layout.setContentsMargins(16, 18, 16, 16)
        form_layout.setSpacing(12)
        for field, label, _default in THEME_COLOR_FIELDS:
            edit, button = self._build_theme_color_control(tab, getattr(owner.theme_settings, field))
            owner.theme_color_edits[field] = edit
            owner.theme_color_buttons[field] = button
            form_layout.addRow(label, self._theme_color_row(edit, button))
        owner.theme_primary_edit = owner.theme_color_edits["primary_color"]
        owner.theme_primary_button = owner.theme_color_buttons["primary_color"]
        owner.theme_accent_edit = owner.theme_color_edits["accent_color"]
        owner.theme_accent_button = owner.theme_color_buttons["accent_color"]
        owner.theme_text_edit = owner.theme_color_edits["text_color"]
        owner.theme_text_button = owner.theme_color_buttons["text_color"]

        owner.theme_visual_effect_combo = _NoWheelComboBox(tab)
        for mode_id in VisualEffectMode.available_modes():
            label = {
                VisualEffectMode.SOLID: "纯色块",
                VisualEffectMode.GAUSSIAN_BLUR: "高斯模糊",
                VisualEffectMode.MACOS_VISUAL_EFFECT: "macOS 原生毛玻璃",
            }.get(mode_id, mode_id)
            owner.theme_visual_effect_combo.addItem(label, mode_id)
        owner.theme_visual_effect_combo.currentIndexChanged.connect(
            owner._handle_visual_effect_changed
        )
        form_layout.addRow("输入栏外观效果", owner.theme_visual_effect_combo)
        form_layout.addRow("", button_row)
        form_layout.addRow("状态", owner.theme_status_label)
        tab.setLayout(form_layout)
        owner._sync_theme_ai_controls()
        return tab

    def _build_theme_color_control(
        self,
        parent: QWidget,
        color: str,
    ) -> tuple[QLineEdit, QPushButton]:
        owner = self.dialog
        edit = QLineEdit(color, parent)
        edit.setMaxLength(7)
        edit.setPlaceholderText("#RRGGBB")
        button = QPushButton("", parent)
        button.setFixedWidth(42)
        button.setToolTip("选择颜色")
        button.setStyleSheet(build_color_button_stylesheet(color))
        button.clicked.connect(lambda _checked=False, color_edit=edit: owner._choose_theme_color(color_edit))
        edit.textChanged.connect(lambda _text, color_edit=edit: owner._handle_theme_color_changed(color_edit))
        return edit, button

    def _theme_color_row(self, edit: QLineEdit, button: QPushButton) -> QWidget:
        container = QWidget(self.dialog)
        layout = QHBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        layout.addWidget(button)
        layout.addWidget(edit, 1)
        container.setLayout(layout)
        return container


class ApiSettingsPage:
    def __init__(self, dialog: Any) -> None:
        self.dialog = dialog

    def build(self, settings: ApiSettings) -> QWidget:
        owner = self.dialog
        tab = QWidget(owner)
        owner.base_url_edit = QLineEdit(settings.base_url, tab)
        owner.base_url_edit.setPlaceholderText("https://api.openai.com/v1")
        owner.api_key_edit = QLineEdit(settings.api_key, tab)
        owner.api_key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        owner.api_key_edit.setPlaceholderText("请输入 API Key")

        owner.model_edit = ModelComboBox(tab)
        owner.model_edit.setText(settings.model)
        owner.model_edit.setPlaceholderText("gpt-4.1-mini")

        owner.api_timeout_spin = _NoWheelSpinBox(tab)
        owner.api_timeout_spin.setRange(1, 600)
        owner.api_timeout_spin.setSuffix(" 秒")
        owner.api_timeout_spin.setValue(settings.timeout_seconds)

        owner.api_model_probe_button = QPushButton("检测模型", tab)
        owner.api_model_probe_button.clicked.connect(owner._probe_api_models)
        owner.api_test_button = QPushButton("测试 API", tab)
        owner.api_test_button.clicked.connect(owner._test_api_settings)

        api_actions = QWidget(tab)
        api_actions_layout = QHBoxLayout(api_actions)
        api_actions_layout.setContentsMargins(0, 0, 0, 0)
        api_actions_layout.setSpacing(8)
        api_actions_layout.addWidget(owner.api_model_probe_button)
        api_actions_layout.addWidget(owner.api_test_button)

        form_layout = QFormLayout()
        form_layout.setContentsMargins(0, 0, 0, 0)
        form_layout.setSpacing(12)
        form_layout.addRow("Base URL", owner.base_url_edit)
        form_layout.addRow("API Key", owner.api_key_edit)
        form_layout.addRow("模型", owner.model_edit)
        form_layout.addRow("超时", owner.api_timeout_spin)
        form_layout.addRow("", api_actions)
        form_container = QWidget(tab)
        form_container.setLayout(form_layout)

        outer_layout = QVBoxLayout()
        outer_layout.setContentsMargins(16, 18, 16, 16)
        outer_layout.setSpacing(12)
        outer_layout.addWidget(form_container)
        outer_layout.addWidget(self._build_advanced_llm_params_group(settings, tab))
        outer_layout.addStretch(1)
        tab.setLayout(outer_layout)
        return tab

    def _build_advanced_llm_params_group(self, settings: ApiSettings, parent: QWidget) -> QGroupBox:
        owner = self.dialog
        group = QGroupBox("高级参数", parent)
        group.setObjectName("advancedParamsGroup")
        group.setCheckable(True)
        owner.advanced_params_hint = QLabel(
            "⚠ 如果你不清楚这些参数的作用，请保持默认、不要随意修改。", group
        )
        owner.advanced_params_hint.setObjectName("advancedParamsHint")
        owner.advanced_params_hint.setWordWrap(True)

        owner.llm_temperature_spin = _NoWheelDoubleSpinBox(group)
        owner.llm_temperature_spin.setRange(0.0, 2.0)
        owner.llm_temperature_spin.setSingleStep(0.1)
        owner.llm_temperature_spin.setDecimals(2)
        owner.llm_temperature_spin.setValue(
            settings.temperature if settings.temperature is not None else 0.8
        )

        owner.llm_top_p_enabled_check = QCheckBox("覆盖 top_p", group)
        owner.llm_top_p_spin = _NoWheelDoubleSpinBox(group)
        owner.llm_top_p_spin.setRange(0.0, 1.0)
        owner.llm_top_p_spin.setSingleStep(0.05)
        owner.llm_top_p_spin.setDecimals(2)
        owner.llm_top_p_spin.setValue(settings.top_p if settings.top_p is not None else 1.0)
        owner.llm_top_p_enabled_check.setChecked(settings.top_p is not None)
        owner.llm_top_p_spin.setEnabled(settings.top_p is not None)
        owner.llm_top_p_enabled_check.toggled.connect(owner.llm_top_p_spin.setEnabled)

        owner.llm_max_tokens_enabled_check = QCheckBox("限制最大输出", group)
        owner.llm_max_tokens_spin = _NoWheelSpinBox(group)
        owner.llm_max_tokens_spin.setRange(1, 32768)
        owner.llm_max_tokens_spin.setSuffix(" tokens")
        owner.llm_max_tokens_spin.setValue(
            settings.max_tokens if settings.max_tokens is not None else 2048
        )
        owner.llm_max_tokens_enabled_check.setChecked(settings.max_tokens is not None)
        owner.llm_max_tokens_spin.setEnabled(settings.max_tokens is not None)
        owner.llm_max_tokens_enabled_check.toggled.connect(owner.llm_max_tokens_spin.setEnabled)

        form = QFormLayout()
        form.setContentsMargins(0, 0, 0, 0)
        form.setSpacing(12)
        form.addRow("温度", owner.llm_temperature_spin)
        form.addRow(owner.llm_top_p_enabled_check, owner.llm_top_p_spin)
        form.addRow(owner.llm_max_tokens_enabled_check, owner.llm_max_tokens_spin)
        body = QWidget(group)
        body.setLayout(form)

        group_layout = QVBoxLayout()
        group_layout.setContentsMargins(16, 10, 16, 12)
        group_layout.setSpacing(10)
        group_layout.addWidget(owner.advanced_params_hint)
        group_layout.addWidget(body)
        group.setLayout(group_layout)
        group.toggled.connect(body.setVisible)
        group.toggled.connect(lambda _checked: owner.advanced_params_hint.setEnabled(True))
        has_custom = (
            settings.temperature is not None
            or settings.top_p is not None
            or settings.max_tokens is not None
        )
        group.setChecked(has_custom)
        body.setVisible(has_custom)
        owner.advanced_params_hint.setEnabled(True)
        return group


class TtsSettingsPage:
    def __init__(self, dialog: Any) -> None:
        self.dialog = dialog

    def build(self, settings: GPTSoVITSTTSSettings) -> QWidget:
        owner = self.dialog
        tab = QWidget(owner)
        owner.tts_enabled_check = QCheckBox("启用 TTS 语音", tab)
        owner.tts_enabled_check.setChecked(settings.enabled)

        owner.tts_provider_combo = _NoWheelComboBox(tab)
        owner.tts_provider_combo.addItem("GPT-SoVITS 整合包（GPU）", TTS_PROVIDER_GPT_SOVITS)
        owner.tts_provider_combo.addItem("Genie TTS 整合包（CPU）", TTS_PROVIDER_GENIE)
        owner.tts_provider_combo.addItem("自定义 GPT-SoVITS（macOS/Linux）", TTS_PROVIDER_CUSTOM_GPT_SOVITS)
        provider_index = owner.tts_provider_combo.findData(settings.provider)
        owner.tts_provider_combo.setCurrentIndex(provider_index if provider_index >= 0 else 0)

        owner.tts_api_url_edit = QLineEdit(settings.api_url, tab)
        owner.tts_api_url_edit.setPlaceholderText(_default_tts_api_url(settings.provider))
        owner.tts_work_dir_edit = QLineEdit(str(settings.work_dir or ""), tab)
        owner.tts_work_dir_edit.setPlaceholderText("tts/g50")
        owner.tts_python_path_edit = QLineEdit(str(settings.python_path or ""), tab)
        owner.tts_python_path_edit.setPlaceholderText(
            "macOS/Linux Python，例如 /path/to/miniforge3/envs/gpt-sovits/bin/python"
        )
        owner.tts_config_path_edit = QLineEdit(str(settings.tts_config_path or ""), tab)
        owner.tts_config_path_edit.setPlaceholderText("可选：GPT-SoVITS tts_infer.yaml")
        owner.tts_bundle_download_button = QPushButton("一键下载 TTS 整合包", tab)
        owner.tts_bundle_download_button.setToolTip(
            "Windows 可一键下载内置整合包；macOS/Linux 请使用自定义 GPT-SoVITS 接入源码版运行环境。"
        )
        owner.tts_bundle_download_button.clicked.connect(owner._download_gpt_sovits_bundle)
        owner.tts_provider_combo.currentIndexChanged.connect(
            lambda _index: owner._sync_tts_provider_controls(apply_defaults=True)
        )
        owner.tts_enabled_check.toggled.connect(owner._sync_tts_enabled_controls)

        owner.ref_lang_edit = QLineEdit(settings.ref_lang, tab)
        owner.text_lang_edit = QLineEdit(settings.text_lang, tab)
        owner.tts_timeout_spin = _NoWheelSpinBox(tab)
        owner.tts_timeout_spin.setRange(1, 600)
        owner.tts_timeout_spin.setSuffix(" 秒")
        owner.tts_timeout_spin.setValue(settings.timeout_seconds)

        enabled_row = QWidget(tab)
        enabled_layout = QHBoxLayout()
        enabled_layout.setContentsMargins(0, 0, 0, 0)
        enabled_layout.setSpacing(10)
        enabled_layout.addWidget(owner.tts_enabled_check)
        enabled_layout.addWidget(owner.tts_bundle_download_button)
        enabled_layout.addStretch(1)
        enabled_row.setLayout(enabled_layout)

        form_layout = QFormLayout()
        form_layout.setContentsMargins(16, 18, 16, 16)
        form_layout.setSpacing(12)
        form_layout.addRow("", enabled_row)
        form_layout.addRow("TTS 提供器", owner.tts_provider_combo)
        form_layout.addRow("API URL", owner.tts_api_url_edit)
        form_layout.addRow("TTS 工作目录", owner.tts_work_dir_edit)
        form_layout.addRow("TTS Python", owner.tts_python_path_edit)
        form_layout.addRow("推理配置", owner.tts_config_path_edit)
        form_layout.addRow("参考语言", owner.ref_lang_edit)
        form_layout.addRow("文本语言", owner.text_lang_edit)
        form_layout.addRow("超时", owner.tts_timeout_spin)
        owner._tts_form_layout = form_layout
        tab.setLayout(form_layout)
        owner._sync_tts_provider_controls(apply_defaults=_is_bundled_tts_provider(settings.provider))
        owner._sync_tts_enabled_controls(settings.enabled)
        return tab


class PrivacySettingsPage:
    def __init__(self, dialog: Any) -> None:
        self.dialog = dialog

    def build(self, proactive_care_settings: ProactiveCareSettings) -> QWidget:
        owner = self.dialog
        tab = QWidget(owner)
        owner.proactive_screen_context_enabled_check = QCheckBox("允许模型主动获取屏幕信息", tab)
        owner.proactive_screen_context_enabled_check.setChecked(
            proactive_care_settings.screen_context_enabled
        )
        owner.proactive_check_interval_spin = _NoWheelSpinBox(tab)
        owner.proactive_check_interval_spin.setRange(
            PROACTIVE_MIN_CHECK_INTERVAL_MINUTES,
            PROACTIVE_MAX_CHECK_INTERVAL_MINUTES,
        )
        owner.proactive_check_interval_spin.setSuffix(" 分钟")
        owner.proactive_check_interval_spin.setValue(
            proactive_care_settings.normalized().check_interval_minutes
        )
        owner.proactive_cooldown_spin = _NoWheelSpinBox(tab)
        owner.proactive_cooldown_spin.setRange(
            PROACTIVE_MIN_COOLDOWN_MINUTES,
            PROACTIVE_MAX_COOLDOWN_MINUTES,
        )
        owner.proactive_cooldown_spin.setSuffix(" 分钟")
        owner.proactive_cooldown_spin.setValue(
            proactive_care_settings.normalized().cooldown_minutes
        )
        owner.proactive_batch_limit_spin = _NoWheelSpinBox(tab)
        owner.proactive_batch_limit_spin.setRange(
            PROACTIVE_MIN_SCREEN_CONTEXT_BATCH_LIMIT,
            PROACTIVE_MAX_SCREEN_CONTEXT_BATCH_LIMIT,
        )
        owner.proactive_batch_limit_spin.setSuffix(" 张")
        owner.proactive_batch_limit_spin.setValue(
            proactive_care_settings.normalized().screen_context_batch_limit
        )
        owner.proactive_screen_context_enabled_check.toggled.connect(
            owner._sync_proactive_screen_context_controls
        )

        form_layout = QFormLayout()
        form_layout.setContentsMargins(16, 18, 16, 16)
        form_layout.setSpacing(12)
        form_layout.addRow("", owner.proactive_screen_context_enabled_check)
        form_layout.addRow("主动检查间隔", owner.proactive_check_interval_spin)
        form_layout.addRow("主动打扰冷却", owner.proactive_cooldown_spin)
        form_layout.addRow("单次最多发送截图", owner.proactive_batch_limit_spin)
        owner._proactive_form_layout = form_layout
        tab.setLayout(form_layout)
        owner._sync_proactive_screen_context_controls(
            owner.proactive_screen_context_enabled_check.isChecked()
        )
        return tab


class ToolsSettingsPage:
    def __init__(self, dialog: Any) -> None:
        self.dialog = dialog

    def build(
        self,
        settings: MCPRuntimeSettings,
        tools_tab_contributions: list[ToolsTabContribution],
    ) -> QWidget:
        owner = self.dialog
        tab = QWidget(owner)
        owner.windows_mcp_enabled_check = QCheckBox("启用 Windows MCP 桌面控制（实验性）", tab)
        owner.windows_mcp_enabled_check.setChecked(settings.windows_enabled)
        owner.windows_mcp_enabled_check.setToolTip(WINDOWS_MCP_EXPERIMENTAL_TEXT)

        restart_hint = QLabel(
            f"{WINDOWS_MCP_EXPERIMENTAL_TEXT}。保存后需要重启 Sakura 才会加载或卸载 Windows MCP 工具。",
            tab,
        )
        restart_hint.setWordWrap(True)
        owner.system_restart_hint_label = restart_hint

        form_layout = QFormLayout()
        form_layout.setContentsMargins(16, 18, 16, 16)
        form_layout.setSpacing(12)
        form_layout.addRow("", owner.windows_mcp_enabled_check)
        form_layout.addRow("生效方式", restart_hint)
        for contribution in sorted(tools_tab_contributions, key=lambda item: item.order):
            try:
                widget = contribution.build(None)
            except Exception as exc:  # noqa: BLE001
                widget = QLabel(f"{contribution.title} 设置加载失败：{exc}", tab)
                widget.setWordWrap(True)
            form_layout.addRow(contribution.title, widget)
        tab.setLayout(form_layout)
        return tab


class PluginSettingsPage:
    def __init__(self, dialog: Any) -> None:
        self.dialog = dialog

    def build(self, settings_panel_contributions: list[SettingsPanelContribution]) -> QWidget:
        owner = self.dialog
        tab = QWidget(owner)
        tab.setObjectName("settingsPluginTab")
        layout = QVBoxLayout()
        layout.setContentsMargins(16, 18, 16, 16)
        layout.setSpacing(12)

        hint = QLabel("插件启用状态保存后需要重启 Sakura 才会生效。", tab)
        hint.setObjectName("pluginRestartHintLabel")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        owner.plugin_table = QTableWidget(tab)
        owner.plugin_table.setObjectName("pluginManagerTable")
        owner.plugin_table.setColumnCount(6)
        owner.plugin_table.setHorizontalHeaderLabels(["启用", "名称", "版本", "优先级", "来源", "介绍"])
        owner.plugin_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        owner.plugin_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        owner.plugin_table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        owner.plugin_table.setAlternatingRowColors(True)
        owner.plugin_table.setWordWrap(True)
        owner.plugin_table.verticalHeader().setVisible(False)
        owner.plugin_table.setRowCount(len(owner.plugin_specs))
        header = owner.plugin_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(5, QHeaderView.ResizeMode.Stretch)
        for row, spec in enumerate(owner.plugin_specs):
            owner._populate_plugin_table_row(row, spec)
        owner.plugin_table.resizeRowsToContents()
        layout.addWidget(owner.plugin_table, 1)

        panel_title = QLabel("插件自定义设置", tab)
        panel_title.setObjectName("pluginSettingsTitleLabel")
        layout.addWidget(panel_title)
        panel_container = QWidget(tab)
        form_layout = QFormLayout()
        form_layout.setContentsMargins(0, 0, 0, 0)
        form_layout.setSpacing(10)
        for contribution in sorted(settings_panel_contributions, key=lambda item: item.order):
            try:
                widget = contribution.build(tab)
            except Exception as exc:  # noqa: BLE001
                widget = QLabel(f"{contribution.title} 设置加载失败：{exc}", tab)
                widget.setWordWrap(True)
            form_layout.addRow(contribution.title, widget)
        if not settings_panel_contributions:
            empty_label = QLabel("暂无插件自定义设置。", tab)
            empty_label.setWordWrap(True)
            form_layout.addRow("", empty_label)
        panel_container.setLayout(form_layout)
        layout.addWidget(panel_container)
        tab.setLayout(layout)
        return tab


class SystemSettingsPage:
    def __init__(self, dialog: Any) -> None:
        self.dialog = dialog

    def build(
        self,
        debug_settings: DebugLogSettings,
        startup_settings: StartupSettings,
        bubble_settings: BubbleSettings,
    ) -> QWidget:
        owner = self.dialog
        tab = QWidget(owner)
        owner.launch_at_login_check = QCheckBox("登录时自动启动 Sakura", tab)
        owner.launch_at_login_check.setChecked(
            startup_settings.launch_at_login and is_launch_at_login_supported()
        )
        if is_launch_at_login_supported():
            owner.launch_at_login_check.setToolTip(
                f"保存后将更新 {launch_at_login_platform_text()} 登录启动项。"
            )
        else:
            owner.launch_at_login_check.setEnabled(False)
            owner.launch_at_login_check.setToolTip("当前平台暂不支持自动配置登录启动项。")

        owner.debug_log_enabled_check = QCheckBox("输出终端调试日志", tab)
        owner.debug_log_enabled_check.setChecked(debug_settings.enabled)
        owner.debug_body_enabled_check = QCheckBox("输出完整请求/回复正文", tab)
        owner.debug_body_enabled_check.setChecked(debug_settings.body_enabled)
        owner.debug_log_enabled_check.toggled.connect(owner.debug_body_enabled_check.setEnabled)
        owner.debug_body_enabled_check.setEnabled(owner.debug_log_enabled_check.isChecked())
        owner.debug_file_enabled_check = QCheckBox("输出文件运行日志", tab)
        owner.debug_file_enabled_check.setChecked(debug_settings.file_enabled)

        owner.subtitle_typing_interval_spin = _NoWheelSpinBox(tab)
        owner.subtitle_typing_interval_spin.setRange(
            SUBTITLE_TYPING_INTERVAL_MIN_MS,
            SUBTITLE_TYPING_INTERVAL_MAX_MS,
        )
        owner.subtitle_typing_interval_spin.setSuffix(" 毫秒")
        owner.subtitle_typing_interval_spin.setValue(owner.subtitle_typing_interval_ms)
        owner.reply_segment_pause_spin = _NoWheelSpinBox(tab)
        owner.reply_segment_pause_spin.setRange(
            REPLY_SEGMENT_PAUSE_MIN_MS,
            REPLY_SEGMENT_PAUSE_MAX_MS,
        )
        owner.reply_segment_pause_spin.setSuffix(" 毫秒")
        owner.reply_segment_pause_spin.setValue(owner.reply_segment_pause_ms)

        owner.bubble_auto_hide_check = QCheckBox("气泡无操作后自动隐藏", tab)
        owner.bubble_auto_hide_check.setChecked(bubble_settings.auto_hide_enabled)
        owner.bubble_auto_hide_delay_spin = _NoWheelSpinBox(tab)
        owner.bubble_auto_hide_delay_spin.setRange(
            BUBBLE_AUTO_HIDE_MIN_DELAY_SECONDS,
            BUBBLE_AUTO_HIDE_MAX_DELAY_SECONDS,
        )
        owner.bubble_auto_hide_delay_spin.setSuffix(" 秒")
        owner.bubble_auto_hide_delay_spin.setValue(
            bubble_settings.normalized().auto_hide_delay_seconds
        )
        owner.bubble_auto_hide_check.toggled.connect(owner._sync_bubble_auto_hide_controls)

        startup_form = QFormLayout()
        startup_form.setContentsMargins(16, 12, 16, 12)
        startup_form.setSpacing(12)
        startup_form.addRow("", owner.launch_at_login_check)
        debug_form = QFormLayout()
        debug_form.setContentsMargins(16, 12, 16, 12)
        debug_form.setSpacing(12)
        debug_form.addRow("", owner.debug_log_enabled_check)
        debug_form.addRow("", owner.debug_body_enabled_check)
        debug_form.addRow("", owner.debug_file_enabled_check)
        subtitle_form = QFormLayout()
        subtitle_form.setContentsMargins(16, 12, 16, 12)
        subtitle_form.setSpacing(12)
        subtitle_form.addRow("字幕逐字间隔", owner.subtitle_typing_interval_spin)
        subtitle_form.addRow("回复分段停顿", owner.reply_segment_pause_spin)
        bubble_form = QFormLayout()
        bubble_form.setContentsMargins(16, 12, 16, 12)
        bubble_form.setSpacing(12)
        bubble_form.addRow("", owner.bubble_auto_hide_check)
        bubble_form.addRow("气泡无操作时长", owner.bubble_auto_hide_delay_spin)
        owner._system_form_layout = bubble_form

        layout = QVBoxLayout()
        layout.setContentsMargins(16, 18, 16, 16)
        layout.setSpacing(12)
        for title, group_form in (
            ("启动", startup_form),
            ("调试日志", debug_form),
            ("字幕与回复", subtitle_form),
            ("气泡", bubble_form),
        ):
            group = QGroupBox(title, tab)
            group.setLayout(group_form)
            layout.addWidget(group)
        layout.addStretch(1)
        owner._sync_bubble_auto_hide_controls(owner.bubble_auto_hide_check.isChecked())
        tab.setLayout(layout)
        return tab


class MemorySettingsPage:
    def __init__(self, dialog: Any) -> None:
        self.dialog = dialog

    def build(self, memory_store: MemoryStore) -> QWidget:
        owner = self.dialog
        tab = QWidget(owner)
        tab.setObjectName("settingsNavPage")
        _ = memory_store

        owner.memory_search_edit = QLineEdit(tab)
        owner.memory_search_edit.setPlaceholderText("搜索记忆内容或 ID")
        owner.memory_search_edit.textChanged.connect(owner._refresh_memory_table)
        owner.memory_refresh_button = QPushButton("刷新", tab)
        owner.memory_refresh_button.clicked.connect(owner._load_memory_entries)
        owner.memory_import_model_button = QPushButton("导入记忆模型", tab)
        owner.memory_import_model_button.setToolTip(
            "导入 models--sentence-transformers--all-MiniLM-L6-v2.zip，供无法自动下载时使用。"
        )
        owner.memory_import_model_button.clicked.connect(owner._import_memory_model_archive)
        owner.memory_status_label = QLabel(MEMORY_READING_TEXT, tab)

        owner.memory_table = QTableWidget(0, 4, tab)
        owner.memory_table.setHorizontalHeaderLabels(["", "内容", "更新时间", "ID"])
        owner.memory_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        owner.memory_table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        owner.memory_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        owner.memory_table.verticalHeader().setVisible(False)
        owner.memory_table.setAlternatingRowColors(True)
        owner.memory_table.setWordWrap(True)
        owner.memory_table.itemClicked.connect(owner._handle_memory_item_clicked)
        header = owner.memory_table.horizontalHeader()
        header.setStretchLastSection(False)
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Fixed)
        owner.memory_table.setColumnWidth(0, 56)
        owner.memory_table.setColumnWidth(3, 82)
        owner.memory_select_all_check = QCheckBox(header)
        owner.memory_select_all_check.setToolTip("全选当前结果")
        owner.memory_select_all_check.stateChanged.connect(
            owner._handle_memory_select_all_check_changed
        )
        header.sectionResized.connect(
            lambda *_args: owner._sync_memory_select_all_check_geometry()
        )
        owner._sync_memory_select_all_check_geometry()

        owner.memory_selection_label = QLabel("已选择 0 条", tab)
        owner.memory_delete_button = QPushButton("删除选中", tab)
        owner.memory_delete_button.setEnabled(False)
        owner.memory_delete_button.clicked.connect(owner._delete_memory_entry)
        owner.memory_clear_selection_button = QPushButton("清空选择", tab)
        owner.memory_clear_selection_button.setEnabled(False)
        owner.memory_clear_selection_button.clicked.connect(owner._clear_memory_selection)
        owner.memory_preview_label = QLabel("未选择记忆", tab)
        owner.memory_preview_label.setWordWrap(True)

        owner.memory_new_button = QPushButton("新增记忆", tab)
        owner.memory_new_button.setCheckable(True)
        owner.memory_new_button.toggled.connect(owner._toggle_memory_new_editor)
        owner.memory_content_edit = QTextEdit(tab)
        owner.memory_content_edit.setPlaceholderText("新增长期记忆内容")
        owner.memory_content_edit.setFixedHeight(84)
        owner.memory_save_button = QPushButton("保存", tab)
        owner.memory_save_button.clicked.connect(owner._save_memory_entry)

        filter_layout = QHBoxLayout()
        filter_layout.addWidget(owner.memory_search_edit, 1)
        filter_layout.addWidget(owner.memory_import_model_button)
        filter_layout.addWidget(owner.memory_refresh_button)
        status_layout = QHBoxLayout()
        status_layout.addWidget(owner.memory_status_label, 1)
        status_layout.addWidget(owner.memory_new_button)
        selection_layout = QHBoxLayout()
        selection_layout.addWidget(owner.memory_selection_label)
        selection_layout.addStretch(1)
        selection_layout.addWidget(owner.memory_clear_selection_button)
        selection_layout.addWidget(owner.memory_delete_button)

        owner.memory_editor_container = QWidget(tab)
        editor_layout = QFormLayout()
        editor_layout.setContentsMargins(0, 0, 0, 0)
        editor_layout.setSpacing(8)
        editor_layout.addRow("内容", owner.memory_content_edit)
        editor_layout.addRow("", owner.memory_save_button)
        owner.memory_editor_container.setLayout(editor_layout)
        owner.memory_editor_container.setVisible(False)

        layout = QVBoxLayout()
        layout.setContentsMargins(16, 18, 16, 16)
        layout.setSpacing(10)
        layout.addLayout(filter_layout)
        layout.addLayout(status_layout)
        layout.addWidget(owner.memory_table, 1)
        layout.addLayout(selection_layout)
        layout.addWidget(owner.memory_editor_container)
        tab.setLayout(layout)

        loading_text = owner._memory_loading_text()
        owner.memory_status_label.setText(loading_text)
        owner._show_memory_placeholder(loading_text)
        owner._clear_memory_editor()
        QTimer.singleShot(0, owner._load_memory_entries)
        return tab


def _prepare_popup_menu(menu: QMenu) -> None:
    menu.setWindowFlags(
        menu.windowFlags()
        | Qt.WindowType.FramelessWindowHint
        | Qt.WindowType.NoDropShadowWindowHint
    )
    menu.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)


def _default_tts_api_url(provider: str) -> str:
    if provider == TTS_PROVIDER_GENIE:
        return DEFAULT_GENIE_TTS_API_URL
    return DEFAULT_GPT_SOVITS_API_URL


def _is_bundled_tts_provider(provider: str) -> bool:
    return provider in {TTS_PROVIDER_GPT_SOVITS, TTS_PROVIDER_GENIE}
