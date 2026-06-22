"""设置窗口页面构建类。

这些类只负责构造 QWidget 与把控件挂回 SettingsDialog；保存、校验、
异步 Worker 生命周期仍由 SettingsDialog 统一管理。
"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QDoubleSpinBox,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHeaderView,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QTableWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from app.agent.mcp import MCPRuntimeSettings, WINDOWS_MCP_EXPERIMENTAL_TEXT
from app.agent.runtime_limits import (
    MAX_CONFIGURABLE_AGENT_STEPS_PER_TURN,
    MAX_CONFIGURABLE_TOOL_CALLS_PER_STEP,
    MAX_CONFIGURABLE_TOOL_CALLS_PER_TURN,
    MIN_AGENT_STEPS_PER_TURN,
    MIN_TOOL_CALLS_PER_STEP,
    RuntimeLoopSettings,
)
from app.agent.memory import MEMORY_LAYER_LABELS, MEMORY_LAYERS, MemoryStore
from app.backchannel.model_cache import (
    BACKCHANNEL_MODEL_CACHE_NAME,
    DEFAULT_BACKCHANNEL_EMBEDDING_MODEL,
    backchannel_model_endpoint,
)
from app.agent.screen_awareness import (
    SCREEN_AWARENESS_MAX_COOLDOWN_MINUTES,
    SCREEN_AWARENESS_MAX_CHECK_INTERVAL_MINUTES,
    SCREEN_AWARENESS_MAX_SCREEN_CONTEXT_BATCH_LIMIT,
    SCREEN_AWARENESS_MIN_COOLDOWN_MINUTES,
    SCREEN_AWARENESS_MIN_CHECK_INTERVAL_MINUTES,
    SCREEN_AWARENESS_MIN_SCREEN_CONTEXT_BATCH_LIMIT,
    ScreenAwarenessSettings,
)
from app.config.character_loader import CharacterProfile, CharacterRegistry
from app.config.models import (
    MODEL_SLOT_CHAT,
    MODEL_SLOT_DESCRIPTIONS,
    MODEL_SLOT_LABELS,
    MODEL_SLOT_UI_ORDER,
    MODEL_SLOT_VISION_CHAT,
    ApiConfigProfile,
    ModelSelectionSettings,
    ModelSlotSelection,
)
from app.config.settings_service import (
    BACKCHANNEL_MAX_DELAY_MS,
    BACKCHANNEL_MIN_DELAY_MS,
    BackchannelSettings,
    BUBBLE_AUTO_HIDE_MAX_DELAY_SECONDS,
    BUBBLE_AUTO_HIDE_MIN_DELAY_SECONDS,
    BubbleSettings,
    DebugLogSettings,
    SCREEN_OBSERVATION_DELIVERY_IMAGE,
    SCREEN_OBSERVATION_DELIVERY_SENSORY_SUMMARY,
    ScreenObservationSettings,
    StartupSettings,
)
from app.llm.api_client import ApiSettings
from app.sensory.models import SensorySource
from app.sensory.providers import (
    DEFAULT_LLAMA_CPP_ENDPOINT,
    DEFAULT_LMSTUDIO_ENDPOINT,
    DEFAULT_OLLAMA_ENDPOINT,
)
from app.sensory.settings import (
    SENSORY_DEFAULT_CONTEXT_BUDGET_CHARS,
    SENSORY_MAX_CONTEXT_BUDGET_CHARS,
    SENSORY_MIN_CONTEXT_BUDGET_CHARS,
    SensorySettings,
)
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
    _FitContentScrollArea,
    _GripSplitter,
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

    def build(
        self,
        settings: ApiSettings,
        api_profiles: list | None = None,
        model_names: list[str] | None = None,
        model_selection: Any = None,
        sensory_settings: SensorySettings | None = None,
    ) -> QWidget:
        owner = self.dialog
        tab = QWidget(owner)

        owner._api_profiles = list(api_profiles or [])
        owner._model_selection = model_selection or ModelSelectionSettings()
        if not owner._model_selection.chat.configured and owner._api_profiles:
            first_profile = owner._api_profiles[0]
            first_model = first_profile.models[0] if first_profile.models else settings.model
            owner._model_selection = ModelSelectionSettings(
                chat=ModelSlotSelection(profile_id=first_profile.id, model=first_model)
            )

        provider_group = QGroupBox("供应商配置", tab)
        owner.api_profile_edit_button = QPushButton("编辑供应商和模型", provider_group)
        owner.api_profile_edit_button.clicked.connect(owner._open_api_profile_editor)
        owner.provider_summary_label = QLabel(self._provider_summary(owner._api_profiles), provider_group)
        owner.provider_summary_label.setWordWrap(True)
        provider_layout = QVBoxLayout()
        provider_layout.setContentsMargins(12, 8, 12, 8)
        provider_layout.setSpacing(8)
        provider_layout.addWidget(owner.provider_summary_label)
        provider_layout.addWidget(owner.api_profile_edit_button, 0, Qt.AlignmentFlag.AlignLeft)
        provider_group.setLayout(provider_layout)

        slot_group = QGroupBox("实际使用的模型配置", tab)
        slot_form = QFormLayout()
        slot_form.setContentsMargins(12, 8, 12, 8)
        slot_form.setSpacing(8)
        owner._slot_profile_combos = {}
        owner._slot_model_combos = {}
        owner._slot_inherit_checks = {}
        owner._slot_test_buttons = {}
        owner._slot_probe_buttons = {}
        for slot in MODEL_SLOT_UI_ORDER:
            row = self._build_slot_row(slot, slot_group)
            slot_form.addRow(MODEL_SLOT_LABELS[slot], row)
        slot_group.setLayout(slot_form)

        # ── 基础设置 ──
        basic_group = QGroupBox("基础设置", tab)
        owner.api_timeout_spin = _NoWheelSpinBox(basic_group)
        owner.api_timeout_spin.setRange(1, 600)
        owner.api_timeout_spin.setSuffix(" 秒")
        owner.api_timeout_spin.setValue(settings.timeout_seconds)

        basic_form = QFormLayout()
        basic_form.setContentsMargins(12, 8, 12, 8)
        basic_form.setSpacing(8)
        basic_form.addRow("超时", owner.api_timeout_spin)
        basic_group.setLayout(basic_form)

        # ── 组装 ──
        outer_layout = QVBoxLayout()
        outer_layout.setContentsMargins(16, 18, 16, 16)
        outer_layout.setSpacing(12)
        outer_layout.addWidget(provider_group)
        outer_layout.addWidget(slot_group)
        outer_layout.addWidget(basic_group)
        outer_layout.addWidget(self._build_advanced_llm_params_group(settings, tab))
        if sensory_settings is not None:
            outer_layout.addWidget(self._build_sensory_group(sensory_settings, tab))
        outer_layout.addStretch(1)
        tab.setLayout(outer_layout)

        # 兼容旧测试/调用点中直接访问的控件名。
        owner.vision_profile_combo = owner._slot_profile_combos[MODEL_SLOT_CHAT]
        owner.vision_model_combo = owner._slot_model_combos[MODEL_SLOT_CHAT]
        owner.vision_probe_btn = owner._slot_probe_buttons[MODEL_SLOT_CHAT]
        owner.vision_test_btn = owner._slot_test_buttons[MODEL_SLOT_CHAT]
        owner.text_enabled_check = owner._slot_inherit_checks[MODEL_SLOT_VISION_CHAT]
        owner.text_profile_combo = owner._slot_profile_combos[MODEL_SLOT_CHAT]
        owner.text_model_combo = owner._slot_model_combos[MODEL_SLOT_CHAT]
        owner.text_probe_btn = owner._slot_probe_buttons[MODEL_SLOT_CHAT]
        owner.text_test_btn = owner._slot_test_buttons[MODEL_SLOT_CHAT]
        return tab

    def _build_slot_row(self, slot: str, parent: QWidget) -> QWidget:
        owner = self.dialog
        container = QWidget(parent)
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        desc = QLabel(MODEL_SLOT_DESCRIPTIONS[slot], container)
        desc.setWordWrap(True)
        desc.setStyleSheet("color: #888; font-size: 11px;")
        layout.addWidget(desc)

        controls = QWidget(container)
        controls_layout = QHBoxLayout(controls)
        controls_layout.setContentsMargins(0, 0, 0, 0)
        controls_layout.setSpacing(8)

        inherit_check = QCheckBox("继承", controls)
        inherit_check.setVisible(slot != MODEL_SLOT_CHAT)
        profile_combo = _NoWheelComboBox(controls)
        model_combo = ModelComboBox(controls)
        probe_btn = QPushButton("检测模型", controls)
        test_btn = QPushButton("测试", controls)

        self._populate_profile_combo(profile_combo, owner._api_profiles)
        selection = owner._model_selection.get(slot)
        inherited = slot != MODEL_SLOT_CHAT and (selection is None or not selection.configured)
        inherit_check.setChecked(inherited)
        if selection is not None and selection.configured:
            self._set_profile_combo_value(profile_combo, selection.profile_id)
        self._populate_model_combo_for_profile(model_combo, owner._api_profiles, str(profile_combo.currentData() or ""))
        if selection is not None and selection.model:
            model_combo.setText(selection.model)

        owner._slot_profile_combos[slot] = profile_combo
        owner._slot_model_combos[slot] = model_combo
        owner._slot_inherit_checks[slot] = inherit_check
        owner._slot_probe_buttons[slot] = probe_btn
        owner._slot_test_buttons[slot] = test_btn

        profile_combo.currentIndexChanged.connect(
            lambda _index, slot_name=slot: owner._sync_slot_model_combo(slot_name, keep_current=False)
        )
        inherit_check.toggled.connect(
            lambda _checked, slot_name=slot: owner._sync_slot_inherit_state(slot_name)
        )
        probe_btn.clicked.connect(lambda _checked=False, slot_name=slot: owner._probe_api_models(slot_name))
        test_btn.clicked.connect(lambda _checked=False, slot_name=slot: owner._test_api_settings(slot_name))

        controls_layout.addWidget(inherit_check)
        controls_layout.addWidget(profile_combo, 2)
        controls_layout.addWidget(model_combo, 3)
        controls_layout.addWidget(probe_btn)
        controls_layout.addWidget(test_btn)
        layout.addWidget(controls)
        owner._sync_slot_inherit_state(slot)
        return container

    def _populate_profile_combo(self, combo: QComboBox, profiles: list) -> None:
        combo.clear()
        for p in profiles:
            alias = p.alias if p.alias else p.id
            combo.addItem(alias, p.id)
        if combo.count() == 0:
            combo.addItem("（无配置）", "")

    def _populate_model_combo_for_profile(
        self,
        combo: ModelComboBox,
        profiles: list,
        profile_id: str,
    ) -> None:
        model_names: list[str] = []
        for profile in profiles:
            if getattr(profile, "id", "") == profile_id:
                model_names = list(getattr(profile, "models", ()))
                break
        combo.blockSignals(True)
        combo.set_model_names(model_names)
        combo.blockSignals(False)

    def _set_profile_combo_value(self, combo: QComboBox, profile_id: str) -> None:
        index = combo.findData(profile_id)
        if index >= 0:
            combo.setCurrentIndex(index)

    def _provider_summary(self, profiles: list) -> str:
        if not profiles:
            return "暂无供应商。"
        return "；".join(
            f"{getattr(p, 'alias', '') or getattr(p, 'id', '')}：{len(getattr(p, 'models', ()))} 个模型"
            for p in profiles
        )

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

    def _build_sensory_group(self, settings: SensorySettings, parent: QWidget) -> QGroupBox:
        owner = self.dialog
        normalized = settings.normalized()
        owner._initialize_sensory_ui_state(normalized)

        group = QGroupBox("增强感知", parent)
        group.setObjectName("sensorySettingsGroup")

        owner.sensory_enabled_check = QCheckBox("启用增强感知工具", group)
        owner.sensory_enabled_check.setChecked(normalized.enabled)
        owner.sensory_context_enabled_check = QCheckBox("将感官摘要动态注入主模型上下文", group)
        owner.sensory_context_enabled_check.setChecked(normalized.context_enabled)

        owner.sensory_context_budget_spin = _NoWheelSpinBox(group)
        owner.sensory_context_budget_spin.setRange(
            SENSORY_MIN_CONTEXT_BUDGET_CHARS,
            SENSORY_MAX_CONTEXT_BUDGET_CHARS,
        )
        owner.sensory_context_budget_spin.setSuffix(" 字符")
        owner.sensory_context_budget_spin.setValue(
            normalized.context_budget_chars or SENSORY_DEFAULT_CONTEXT_BUDGET_CHARS
        )

        owner.sensory_source_combo = _NoWheelComboBox(group)
        owner.sensory_source_combo.addItem("视觉", SensorySource.VISION.value)
        owner.sensory_source_combo.addItem("语音", SensorySource.SPEECH.value)
        owner.sensory_source_combo.addItem("环境声音", SensorySource.SOUND.value)

        owner.sensory_mode_combo = _NoWheelComboBox(group)
        owner.sensory_mode_combo.addItem("关闭该感官模型", "off")
        owner.sensory_mode_combo.addItem("本机运行框架", "local")
        owner.sensory_mode_combo.addItem("局域网连接分布式计算", "lan")
        owner.sensory_mode_combo.addItem("远端 API", "api")

        owner.sensory_backend_combo = _NoWheelComboBox(group)
        owner.sensory_backend_combo.addItem("LM Studio", "lmstudio")
        owner.sensory_backend_combo.addItem("Ollama", "ollama")
        owner.sensory_backend_combo.addItem("llama.cpp", "llama")
        owner.sensory_backend_combo.addItem("OpenAI 兼容 API", "openai_compatible")

        owner.sensory_endpoint_edit = QLineEdit(group)
        owner.sensory_model_edit = ModelComboBox(group)
        owner.sensory_model_edit.setPlaceholderText("例如 qwen3-vl-4b-instruct-mlx")
        owner.sensory_api_key_edit = QLineEdit(group)
        owner.sensory_api_key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        owner.sensory_api_key_edit.setPlaceholderText("本机服务通常可留空")

        owner.sensory_timeout_spin = _NoWheelSpinBox(group)
        owner.sensory_timeout_spin.setRange(1, 300)
        owner.sensory_timeout_spin.setSuffix(" 秒")

        owner.sensory_confidence_spin = _NoWheelDoubleSpinBox(group)
        owner.sensory_confidence_spin.setRange(0.0, 1.0)
        owner.sensory_confidence_spin.setSingleStep(0.05)
        owner.sensory_confidence_spin.setDecimals(2)

        owner.sensory_probe_button = QPushButton("检测模型", group)
        owner.sensory_probe_button.clicked.connect(owner._probe_sensory_models)
        owner.sensory_test_button = QPushButton("测试模型", group)
        owner.sensory_test_button.clicked.connect(owner._test_sensory_model)
        owner.sensory_hf_download_button = QPushButton("从 Hugging Face 下载", group)
        owner.sensory_hf_download_button.clicked.connect(owner._download_sensory_model_from_huggingface)
        owner.sensory_status_label = QLabel("未测试", group)
        owner.sensory_status_label.setObjectName("secondaryText")
        owner.sensory_status_label.setWordWrap(True)
        owner.sensory_privacy_label = QLabel(
            "语音/环境声音可在确认后采集电脑系统声音；本机模式不出网，局域网/远端 API 会发送到配置的 Endpoint。",
            group,
        )
        owner.sensory_privacy_label.setObjectName("secondaryText")
        owner.sensory_privacy_label.setWordWrap(True)

        action_row = QWidget(group)
        action_layout = QHBoxLayout(action_row)
        action_layout.setContentsMargins(0, 0, 0, 0)
        action_layout.setSpacing(8)
        action_layout.addWidget(owner.sensory_probe_button)
        action_layout.addWidget(owner.sensory_test_button)
        action_layout.addWidget(owner.sensory_hf_download_button)
        action_layout.addStretch(1)

        toggles = QWidget(group)
        toggles_layout = QHBoxLayout(toggles)
        toggles_layout.setContentsMargins(0, 0, 0, 0)
        toggles_layout.setSpacing(10)
        toggles_layout.addWidget(owner.sensory_enabled_check)
        toggles_layout.addWidget(owner.sensory_context_enabled_check)
        toggles_layout.addStretch(1)

        form = QFormLayout()
        form.setContentsMargins(16, 10, 16, 12)
        form.setSpacing(12)
        form.addRow("", toggles)
        form.addRow("", owner.sensory_privacy_label)
        form.addRow("上下文预算", owner.sensory_context_budget_spin)
        form.addRow("感官源", owner.sensory_source_combo)
        form.addRow("模式", owner.sensory_mode_combo)
        form.addRow("后端", owner.sensory_backend_combo)
        form.addRow("Endpoint", owner.sensory_endpoint_edit)
        form.addRow("模型", owner.sensory_model_edit)
        form.addRow("API Key", owner.sensory_api_key_edit)
        form.addRow("超时", owner.sensory_timeout_spin)
        form.addRow("最低置信度", owner.sensory_confidence_spin)
        form.addRow("", action_row)
        form.addRow("状态", owner.sensory_status_label)
        group.setLayout(form)

        owner.sensory_enabled_check.toggled.connect(owner._sync_sensory_controls)
        owner.sensory_context_enabled_check.toggled.connect(owner._sync_sensory_controls)
        owner.sensory_source_combo.currentIndexChanged.connect(owner._handle_sensory_source_changed)
        owner.sensory_mode_combo.currentIndexChanged.connect(owner._handle_sensory_control_changed)
        owner.sensory_backend_combo.currentIndexChanged.connect(owner._handle_sensory_backend_changed)
        owner.sensory_endpoint_edit.textChanged.connect(owner._handle_sensory_control_changed)
        owner.sensory_model_edit.currentTextChanged.connect(owner._handle_sensory_control_changed)
        owner.sensory_api_key_edit.textChanged.connect(owner._handle_sensory_control_changed)
        owner.sensory_timeout_spin.valueChanged.connect(owner._handle_sensory_control_changed)
        owner.sensory_confidence_spin.valueChanged.connect(owner._handle_sensory_control_changed)

        owner._load_sensory_source_controls(SensorySource.VISION.value, mark_dirty=False)
        owner._sync_sensory_controls()
        return group


def default_sensory_endpoint_for_backend(backend: str) -> str:
    normalized = backend.strip().lower()
    if normalized in {"lmstudio", "lm_studio"}:
        return DEFAULT_LMSTUDIO_ENDPOINT
    if normalized == "ollama":
        return DEFAULT_OLLAMA_ENDPOINT
    if normalized in {"llama", "llama.cpp", "llama_cpp", "llamacpp"}:
        return DEFAULT_LLAMA_CPP_ENDPOINT
    return "https://api.openai.com/v1"


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
        owner.tts_bundle_status_label = QLabel("", tab)
        owner.tts_bundle_status_label.setWordWrap(True)
        owner.tts_provider_combo.currentIndexChanged.connect(
            lambda _index: owner._sync_tts_provider_controls(apply_defaults=True)
        )
        owner.tts_enabled_check.toggled.connect(owner._sync_tts_enabled_controls)

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
        form_layout.addRow("超时", owner.tts_timeout_spin)
        form_layout.addRow("整合包下载", owner.tts_bundle_status_label)
        owner._tts_form_layout = form_layout
        tab.setLayout(form_layout)
        owner._sync_tts_provider_controls(apply_defaults=_is_bundled_tts_provider(settings.provider))
        owner._sync_tts_enabled_controls(settings.enabled)
        return tab


class PrivacySettingsPage:
    def __init__(self, dialog: Any) -> None:
        self.dialog = dialog

    def build(
        self,
        screen_awareness_settings: ScreenAwarenessSettings,
        screen_observation_settings: ScreenObservationSettings | None = None,
    ) -> QWidget:
        owner = self.dialog
        tab = QWidget(owner)
        owner.proactive_screen_context_enabled_check = QCheckBox("启用主动屏幕感知（会定期获取屏幕信息）", tab)
        normalized_screen_awareness_settings = screen_awareness_settings.normalized()
        normalized_screen_observation_settings = (
            screen_observation_settings or ScreenObservationSettings()
        ).normalized()
        owner.proactive_screen_context_enabled_check.setChecked(
            normalized_screen_awareness_settings.allows_screen_context()
        )
        owner.screen_observation_delivery_combo = _NoWheelComboBox(tab)
        owner.screen_observation_delivery_combo.addItem(
            "直接发送图片给主模型（需要视觉能力）",
            SCREEN_OBSERVATION_DELIVERY_IMAGE,
        )
        owner.screen_observation_delivery_combo.addItem(
            "先用增强视觉模型转成文字摘要",
            SCREEN_OBSERVATION_DELIVERY_SENSORY_SUMMARY,
        )
        delivery_index = owner.screen_observation_delivery_combo.findData(
            normalized_screen_observation_settings.delivery_mode
        )
        owner.screen_observation_delivery_combo.setCurrentIndex(max(0, delivery_index))
        owner.screen_observation_delivery_combo.setToolTip(
            "文字摘要模式会把截图交给增强视觉模型处理，再把结构化文本交给主模型；主模型不会收到原图。"
        )
        owner.proactive_check_interval_spin = _NoWheelSpinBox(tab)
        owner.proactive_check_interval_spin.setRange(
            SCREEN_AWARENESS_MIN_CHECK_INTERVAL_MINUTES,
            SCREEN_AWARENESS_MAX_CHECK_INTERVAL_MINUTES,
        )
        owner.proactive_check_interval_spin.setSuffix(" 分钟")
        owner.proactive_check_interval_spin.setValue(
            normalized_screen_awareness_settings.check_interval_minutes
        )
        owner.proactive_cooldown_spin = _NoWheelSpinBox(tab)
        owner.proactive_cooldown_spin.setRange(
            SCREEN_AWARENESS_MIN_COOLDOWN_MINUTES,
            SCREEN_AWARENESS_MAX_COOLDOWN_MINUTES,
        )
        owner.proactive_cooldown_spin.setSuffix(" 分钟")
        owner.proactive_cooldown_spin.setValue(
            normalized_screen_awareness_settings.cooldown_minutes
        )
        owner.proactive_batch_limit_spin = _NoWheelSpinBox(tab)
        owner.proactive_batch_limit_spin.setRange(
            SCREEN_AWARENESS_MIN_SCREEN_CONTEXT_BATCH_LIMIT,
            SCREEN_AWARENESS_MAX_SCREEN_CONTEXT_BATCH_LIMIT,
        )
        owner.proactive_batch_limit_spin.setSuffix(" 张")
        owner.proactive_batch_limit_spin.setValue(
            normalized_screen_awareness_settings.screen_context_batch_limit
        )
        owner.proactive_token_estimate_label = QLabel(tab)
        owner.proactive_token_estimate_label.setWordWrap(True)
        owner.proactive_token_estimate_label.setObjectName("secondaryText")
        owner.proactive_token_estimate_label.setToolTip(
            "按当前屏幕原始尺寸和高细节图像规则估算，不包含文字、工具协议和非 OpenAI 兼容方差异。"
        )
        owner.proactive_screen_context_enabled_check.toggled.connect(
            owner._sync_proactive_screen_context_controls
        )
        owner.proactive_batch_limit_spin.valueChanged.connect(
            owner._sync_proactive_token_estimate
        )

        form_layout = QFormLayout()
        form_layout.setContentsMargins(16, 18, 16, 16)
        form_layout.setSpacing(12)
        form_layout.addRow("", owner.proactive_screen_context_enabled_check)
        form_layout.addRow("截图交付方式", owner.screen_observation_delivery_combo)
        form_layout.addRow("主动检查间隔", owner.proactive_check_interval_spin)
        form_layout.addRow("主动发言冷却", owner.proactive_cooldown_spin)
        form_layout.addRow("单次最多发送截图", owner.proactive_batch_limit_spin)
        form_layout.addRow("预计图像 token", owner.proactive_token_estimate_label)
        owner._proactive_form_layout = form_layout
        tab.setLayout(form_layout)
        owner._sync_proactive_token_estimate()
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
        runtime_loop_settings: RuntimeLoopSettings,
        tools_tab_contributions: list[ToolsTabContribution],
    ) -> QWidget:
        owner = self.dialog
        tab = QWidget(owner)
        runtime_loop_settings = runtime_loop_settings.normalized()
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
        form_layout.addRow("", self._build_runtime_loop_group(runtime_loop_settings, tab))
        for contribution in sorted(tools_tab_contributions, key=lambda item: item.order):
            try:
                widget = contribution.build(None)
            except Exception as exc:  # noqa: BLE001
                widget = QLabel(f"{contribution.title} 设置加载失败：{exc}", tab)
                widget.setWordWrap(True)
            form_layout.addRow(contribution.title, widget)
        tab.setLayout(form_layout)
        return tab

    def _build_runtime_loop_group(
        self,
        settings: RuntimeLoopSettings,
        parent: QWidget,
    ) -> QGroupBox:
        owner = self.dialog
        group = QGroupBox("工具循环", parent)

        owner.agent_steps_per_turn_spin = _NoWheelSpinBox(group)
        owner.agent_steps_per_turn_spin.setRange(
            MIN_AGENT_STEPS_PER_TURN,
            MAX_CONFIGURABLE_AGENT_STEPS_PER_TURN,
        )
        owner.agent_steps_per_turn_spin.setSuffix(" 步")
        owner.agent_steps_per_turn_spin.setValue(settings.max_agent_steps_per_turn)
        owner.agent_steps_per_turn_spin.setToolTip(
            "每轮对话中，模型最多连续规划和执行工具的轮数。"
        )

        owner.tool_calls_per_step_spin = _NoWheelSpinBox(group)
        owner.tool_calls_per_step_spin.setRange(
            MIN_TOOL_CALLS_PER_STEP,
            MAX_CONFIGURABLE_TOOL_CALLS_PER_STEP,
        )
        owner.tool_calls_per_step_spin.setSuffix(" 个")
        owner.tool_calls_per_step_spin.setValue(settings.max_tool_calls_per_step)
        owner.tool_calls_per_step_spin.setToolTip(
            "单次规划返回多个工具调用时，本步骤最多执行的数量。"
        )

        owner.tool_calls_per_turn_spin = _NoWheelSpinBox(group)
        owner.tool_calls_per_turn_spin.setRange(
            settings.max_tool_calls_per_step,
            MAX_CONFIGURABLE_TOOL_CALLS_PER_TURN,
        )
        owner.tool_calls_per_turn_spin.setSuffix(" 个")
        owner.tool_calls_per_turn_spin.setValue(settings.max_tool_calls_per_turn)
        owner.tool_calls_per_turn_spin.setToolTip(
            "一轮用户消息内最多执行的工具调用总数。"
        )
        owner.tool_calls_per_step_spin.valueChanged.connect(
            owner.tool_calls_per_turn_spin.setMinimum
        )

        hint = QLabel(
            "数值越大，复杂任务可连续推进得更久，但也会增加响应时间和接口消耗。",
            group,
        )
        hint.setWordWrap(True)

        layout = QFormLayout()
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(12)
        layout.addRow("循环步数", owner.agent_steps_per_turn_spin)
        layout.addRow("每步工具数", owner.tool_calls_per_step_spin)
        layout.addRow("整轮工具数", owner.tool_calls_per_turn_spin)
        layout.addRow("说明", hint)
        group.setLayout(layout)
        return group


class PluginSettingsPage:
    def __init__(self, dialog: Any) -> None:
        self.dialog = dialog

    def build(self, settings_panel_contributions: list[SettingsPanelContribution]) -> QWidget:
        owner = self.dialog
        tab = QWidget(owner)
        tab.setObjectName("settingsPluginTab")
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(16, 18, 16, 16)
        layout.setSpacing(12)

        hint = QLabel("插件启用状态保存后需要重启 Sakura 才会生效。", tab)
        hint.setObjectName("pluginRestartHintLabel")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        owner.plugin_table = QTableWidget(tab)
        owner.plugin_table.setObjectName("pluginManagerTable")
        owner.plugin_table.setColumnCount(2)
        owner.plugin_table.setHorizontalHeaderLabels(["启用", "插件"])
        owner.plugin_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        owner.plugin_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        owner.plugin_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        owner.plugin_table.setAlternatingRowColors(True)
        owner.plugin_table.setWordWrap(True)
        owner.plugin_table.verticalHeader().setVisible(False)
        owner.plugin_table.setMinimumWidth(190)
        owner.plugin_table.setMaximumWidth(320)
        owner.plugin_table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        owner.plugin_table.setRowCount(len(owner.plugin_specs))
        header = owner.plugin_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        for row, spec in enumerate(owner.plugin_specs):
            owner._populate_plugin_table_row(row, spec)
        owner.plugin_table.resizeRowsToContents()

        detail_panel = self._build_detail_panel(tab, settings_panel_contributions)
        detail_panel.setMinimumWidth(260)
        splitter = QSplitter(Qt.Orientation.Horizontal, tab)
        splitter.setObjectName("pluginManagerSplitter")
        splitter.setChildrenCollapsible(False)
        splitter.addWidget(owner.plugin_table)
        splitter.addWidget(detail_panel)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([240, 460])
        layout.addWidget(splitter, 1)

        owner.plugin_table.currentCellChanged.connect(
            lambda current_row, _current_column, _previous_row, _previous_column:
            owner._refresh_plugin_detail_panel(current_row)
        )
        if owner.plugin_specs:
            owner.plugin_table.setCurrentCell(0, 1)
        owner._refresh_plugin_detail_panel(owner.plugin_table.currentRow())
        return tab

    def _build_detail_panel(
        self,
        parent: QWidget,
        settings_panel_contributions: list[SettingsPanelContribution],
    ) -> QWidget:
        owner = self.dialog
        panel = QWidget(parent)
        panel.setObjectName("pluginDetailPanel")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        detail_group = QGroupBox("插件详情", panel)
        detail_layout = QFormLayout(detail_group)
        detail_layout.setContentsMargins(14, 14, 14, 14)
        detail_layout.setSpacing(10)
        owner.plugin_detail_title_label = QLabel("请选择插件", detail_group)
        owner.plugin_detail_title_label.setObjectName("pluginDetailTitleLabel")
        owner.plugin_detail_title_label.setWordWrap(True)
        owner.plugin_detail_meta_label = QLabel("", detail_group)
        owner.plugin_detail_meta_label.setObjectName("pluginDetailMetaLabel")
        owner.plugin_detail_meta_label.setWordWrap(True)
        owner.plugin_detail_permissions_label = QLabel("", detail_group)
        owner.plugin_detail_permissions_label.setObjectName("pluginDetailPermissionsLabel")
        owner.plugin_detail_permissions_label.setWordWrap(True)
        owner.plugin_detail_description_label = QLabel("", detail_group)
        owner.plugin_detail_description_label.setObjectName("pluginDetailDescriptionLabel")
        owner.plugin_detail_description_label.setWordWrap(True)
        detail_layout.addRow("名称", owner.plugin_detail_title_label)
        detail_layout.addRow("信息", owner.plugin_detail_meta_label)
        detail_layout.addRow("权限", owner.plugin_detail_permissions_label)
        detail_layout.addRow("介绍", owner.plugin_detail_description_label)
        layout.addWidget(detail_group)

        # 详细设置改为按钮触发的独立对话框：启停页保持聚焦，插件设置控件在独立窗口
        # 里整宽展示并可滚动，避免嵌套挤压。这里只按 plugin_id 暂存贡献，按需构建。
        owner._plugin_settings_contributions_by_id = self._group_settings_panels(
            settings_panel_contributions
        )
        owner.plugin_open_settings_button = QPushButton("打开设置…", panel)
        owner.plugin_open_settings_button.setObjectName("pluginOpenSettingsButton")
        owner.plugin_open_settings_button.clicked.connect(owner._open_plugin_settings_dialog)
        button_row = QHBoxLayout()
        button_row.setContentsMargins(0, 0, 0, 0)
        button_row.addWidget(owner.plugin_open_settings_button)
        button_row.addStretch(1)
        layout.addLayout(button_row)
        layout.addStretch(1)
        return panel

    @staticmethod
    def _group_settings_panels(
        contributions: list[SettingsPanelContribution],
    ) -> dict[str, list[SettingsPanelContribution]]:
        grouped: dict[str, list[SettingsPanelContribution]] = {}
        for contribution in contributions:
            plugin_id = contribution.plugin_id.strip()
            if not plugin_id:
                plugin_id = "__unscoped__"
            grouped.setdefault(plugin_id, []).append(contribution)
        return grouped


class SystemSettingsPage:
    def __init__(self, dialog: Any) -> None:
        self.dialog = dialog

    def build(
        self,
        debug_settings: DebugLogSettings,
        startup_settings: StartupSettings,
        bubble_settings: BubbleSettings,
        backchannel_settings: BackchannelSettings,
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
        owner.stage_debug_overlay_check = QCheckBox("舞台调试框（开发者，画窗口/布局/立绘边界 + DPR）", tab)
        owner.stage_debug_overlay_check.setChecked(debug_settings.stage_debug_overlay)
        owner.stage_debug_overlay_check.setToolTip(
            "在桌宠上叠加可视化调试层:红=窗口/碰撞区,绿=布局算出的立绘框,蓝=实际立绘控件,"
            "并显示逻辑尺寸与 devicePixelRatio,用于排查舞台尺寸/碰撞与 mac HiDPI 问题。"
        )
        owner.stage_collision_mask_check = QCheckBox("舞台碰撞贴合（立绘四周空白可穿透点击，默认开）", tab)
        owner.stage_collision_mask_check.setChecked(debug_settings.stage_collision_mask)
        owner.stage_collision_mask_check.setToolTip(
            "用 setMask 把窗口命中区裁到「立绘+气泡+输入栏」矩形并集,立绘两侧/角落的透明空白"
            "不再拦截点击(可点到下层窗口),也不会再误拖桌宠。"
        )

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

        normalized_backchannel = backchannel_settings.normalized()
        owner.backchannel_enabled_check = QCheckBox("启用本地快速接话", tab)
        owner.backchannel_enabled_check.setChecked(normalized_backchannel.enabled)
        owner.backchannel_enabled_check.setToolTip(
            "用户发消息后，主回复返回前先显示一句角色化过渡反应。"
        )
        owner.backchannel_mode_combo = _NoWheelComboBox(tab)
        owner.backchannel_mode_combo.addItem("规则模式", "rules")
        owner.backchannel_mode_combo.addItem("模型增强", "hybrid")
        mode_index = owner.backchannel_mode_combo.findData(normalized_backchannel.mode)
        owner.backchannel_mode_combo.setCurrentIndex(max(0, mode_index))
        owner.backchannel_mode_combo.setToolTip(
            "模型增强会优先使用规则命中；模型缺失或低置信时自动降级。"
        )
        owner.backchannel_tts_enabled_check = QCheckBox("接话语音（缺失时用当前 TTS 合成）", tab)
        owner.backchannel_tts_enabled_check.setChecked(normalized_backchannel.tts_enabled)
        owner.backchannel_tts_enabled_check.setToolTip(
            "需要同时启用全局 TTS；保存后会预生成当前角色缺失的接话语音。"
        )
        owner.backchannel_delay_spin = _NoWheelSpinBox(tab)
        owner.backchannel_delay_spin.setRange(BACKCHANNEL_MIN_DELAY_MS, BACKCHANNEL_MAX_DELAY_MS)
        owner.backchannel_delay_spin.setSuffix(" 毫秒")
        owner.backchannel_delay_spin.setValue(normalized_backchannel.delay_ms)
        owner.backchannel_probability_spin = QDoubleSpinBox(tab)
        owner.backchannel_probability_spin.setRange(0.0, 1.0)
        owner.backchannel_probability_spin.setSingleStep(0.05)
        owner.backchannel_probability_spin.setDecimals(2)
        owner.backchannel_probability_spin.setValue(normalized_backchannel.probability)
        owner.backchannel_setup_hint_label = QLabel(owner._backchannel_setup_hint_text(), tab)
        owner.backchannel_setup_hint_label.setWordWrap(True)
        owner.backchannel_model_status_label = QLabel(owner._backchannel_model_status_text(), tab)
        owner.backchannel_model_status_label.setWordWrap(True)
        owner.backchannel_download_model_button = QPushButton("在线安装", tab)
        owner.backchannel_download_model_button.setToolTip(
            f"从 {backchannel_model_endpoint()} 安装 {DEFAULT_BACKCHANNEL_EMBEDDING_MODEL} 到本地缓存。"
        )
        owner.backchannel_download_model_button.clicked.connect(owner._download_backchannel_model)
        owner.backchannel_import_model_button = QPushButton("导入接话模型", tab)
        owner.backchannel_import_model_button.setToolTip(
            f"导入 {BACKCHANNEL_MODEL_CACHE_NAME}.zip，供 hybrid 模式离线使用。"
        )
        owner.backchannel_import_model_button.clicked.connect(owner._import_backchannel_model_archive)
        owner.backchannel_refresh_status_button = QPushButton("重新检测", tab)
        owner.backchannel_refresh_status_button.setToolTip("重新检测接话模型状态。")
        owner.backchannel_refresh_status_button.clicked.connect(owner._refresh_backchannel_setup_status)
        owner.backchannel_enabled_check.toggled.connect(owner._sync_backchannel_controls)
        owner.backchannel_mode_combo.currentIndexChanged.connect(
            lambda _index: owner._refresh_backchannel_setup_status()
        )
        tts_enabled_check = getattr(owner, "tts_enabled_check", None)
        if tts_enabled_check is not None:
            tts_enabled_check.toggled.connect(
                lambda _checked: owner._sync_backchannel_controls(
                    owner.backchannel_enabled_check.isChecked()
                )
            )

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
        debug_form.addRow("", owner.stage_debug_overlay_check)
        debug_form.addRow("", owner.stage_collision_mask_check)
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
        backchannel_form = QFormLayout()
        backchannel_form.setContentsMargins(16, 12, 16, 12)
        backchannel_form.setSpacing(12)
        backchannel_form.addRow("", owner.backchannel_enabled_check)
        backchannel_form.addRow("接话模式", owner.backchannel_mode_combo)
        backchannel_form.addRow("配置状态", owner.backchannel_setup_hint_label)
        backchannel_form.addRow("", owner.backchannel_tts_enabled_check)
        backchannel_form.addRow("接话延迟", owner.backchannel_delay_spin)
        backchannel_form.addRow("接话触发概率", owner.backchannel_probability_spin)
        backchannel_model_layout = QHBoxLayout()
        backchannel_model_layout.addWidget(owner.backchannel_model_status_label, 1)
        backchannel_model_layout.addWidget(owner.backchannel_download_model_button)
        backchannel_model_layout.addWidget(owner.backchannel_import_model_button)
        backchannel_model_layout.addWidget(owner.backchannel_refresh_status_button)
        backchannel_form.addRow("接话模型", backchannel_model_layout)
        owner._backchannel_form_layout = backchannel_form

        layout = QVBoxLayout()
        layout.setContentsMargins(16, 18, 16, 16)
        layout.setSpacing(12)
        for title, group_form in (
            ("启动", startup_form),
            ("调试日志", debug_form),
            ("字幕与回复", subtitle_form),
            ("气泡", bubble_form),
            ("接话", backchannel_form),
        ):
            group = QGroupBox(title, tab)
            group.setLayout(group_form)
            layout.addWidget(group)
        layout.addStretch(1)
        owner._sync_bubble_auto_hide_controls(owner.bubble_auto_hide_check.isChecked())
        owner._sync_backchannel_controls(owner.backchannel_enabled_check.isChecked())
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

        # 自动整理设置（需求3）：自动整理始终开启，这里只暴露触发频率（轮数）。
        from app.agent.memory_curator import MemoryCurationSettings

        curation_settings = (
            getattr(owner, "memory_curation_settings", None) or MemoryCurationSettings()
        )
        owner.memory_trigger_turns_spin = _NoWheelSpinBox(tab)
        owner.memory_trigger_turns_spin.setRange(1, 50)
        owner.memory_trigger_turns_spin.setSuffix(" 轮对话")
        owner.memory_trigger_turns_spin.setValue(int(curation_settings.trigger_turns))
        curation_form = QFormLayout()
        curation_form.setContentsMargins(16, 12, 16, 12)
        curation_form.setSpacing(12)
        curation_form.addRow("自动整理频率", owner.memory_trigger_turns_spin)
        owner.memory_curation_group = QGroupBox("自动整理", tab)
        owner.memory_curation_group.setLayout(curation_form)

        owner.memory_search_edit = QLineEdit(tab)
        owner.memory_search_edit.setPlaceholderText("搜索记忆内容或 ID")
        owner.memory_search_edit.textChanged.connect(owner._refresh_memory_table)
        owner.memory_layer_filter_combo = _NoWheelComboBox(tab)
        owner.memory_layer_filter_combo.addItem("全部层级", "")
        for layer in MEMORY_LAYERS:
            owner.memory_layer_filter_combo.addItem(MEMORY_LAYER_LABELS.get(layer, layer), layer)
        owner.memory_layer_filter_combo.currentIndexChanged.connect(owner._refresh_memory_table)
        owner.memory_refresh_button = QPushButton("刷新", tab)
        owner.memory_refresh_button.clicked.connect(owner._load_memory_entries)
        owner.memory_download_model_button = QPushButton("在线安装记忆模型", tab)
        owner.memory_download_model_button.setToolTip(
            "从 Hugging Face 安装 sentence-transformers/all-MiniLM-L6-v2 到本地缓存。"
        )
        owner.memory_download_model_button.clicked.connect(owner._download_memory_model)
        owner.memory_import_model_button = QPushButton("导入记忆模型", tab)
        owner.memory_import_model_button.setToolTip(
            "导入 models--sentence-transformers--all-MiniLM-L6-v2.zip，供无法自动下载时使用。"
        )
        owner.memory_import_model_button.clicked.connect(owner._import_memory_model_archive)
        owner.memory_status_label = QLabel(MEMORY_READING_TEXT, tab)

        owner.memory_table = QTableWidget(0, 4, tab)
        owner.memory_table.setHorizontalHeaderLabels(
            ["", "内容", "层级", "更新时间"]
        )
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
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        owner.memory_table.setColumnWidth(0, 56)
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
        owner.memory_content_edit.setFixedHeight(88)
        owner.memory_content_edit.setMinimumHeight(88)
        owner.memory_content_edit.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Fixed,
        )
        owner.memory_content_edit.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        owner.memory_layer_combo = _NoWheelComboBox(tab)
        for layer in MEMORY_LAYERS:
            owner.memory_layer_combo.addItem(MEMORY_LAYER_LABELS.get(layer, layer), layer)
        owner.memory_category_edit = QLineEdit(tab)
        owner.memory_category_edit.setPlaceholderText("如 preference / project / workflow")
        owner.memory_source_edit = QLineEdit(tab)
        owner.memory_source_edit.setPlaceholderText("manual")
        owner.memory_importance_spin = _NoWheelDoubleSpinBox(tab)
        owner.memory_importance_spin.setRange(0.0, 1.0)
        owner.memory_importance_spin.setSingleStep(0.05)
        owner.memory_importance_spin.setDecimals(2)
        owner.memory_confidence_spin = _NoWheelDoubleSpinBox(tab)
        owner.memory_confidence_spin.setRange(0.0, 1.0)
        owner.memory_confidence_spin.setSingleStep(0.05)
        owner.memory_confidence_spin.setDecimals(2)
        owner.memory_save_button = QPushButton("保存", tab)
        owner.memory_save_button.clicked.connect(owner._save_memory_entry)

        filter_layout = QHBoxLayout()
        filter_layout.addWidget(owner.memory_search_edit, 1)
        filter_layout.addWidget(owner.memory_layer_filter_combo)
        filter_layout.addWidget(owner.memory_download_model_button)
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

        # 编辑区滚动面板：sizeHint 贴合表单实际高度（见 _FitContentScrollArea），配合
        # Maximum 纵向策略 —— 空间充足时正好占满表单高度、不留空白，多余纵向空间留给上方
        # 记忆表格（stretch=1）；窗口压矮时面板收缩并内部滚动，而不是把各行压成重叠。
        # 不再用 setFixedHeight 锁死高度，避免 QSS padding 注入后行高被压缩导致输入框重叠。
        owner.memory_editor_container = _FitContentScrollArea(tab)
        owner.memory_editor_container.setObjectName("memoryEditorPanel")
        owner.memory_editor_container.setWidgetResizable(True)
        owner.memory_editor_container.setFrameShape(QFrame.Shape.NoFrame)
        owner.memory_editor_container.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        owner.memory_editor_container.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAsNeeded
        )
        owner.memory_editor_container.setSizePolicy(
            QSizePolicy.Policy.Preferred,
            QSizePolicy.Policy.Maximum,
        )
        owner.memory_editor_content = QWidget(owner.memory_editor_container)
        owner.memory_editor_content.setObjectName("memoryEditorContent")
        owner.memory_editor_form = QWidget(owner.memory_editor_content)
        owner.memory_editor_form.setObjectName("memoryEditorForm")
        editor_layout = QFormLayout(owner.memory_editor_form)
        editor_layout.setContentsMargins(10, 10, 10, 10)
        editor_layout.setHorizontalSpacing(12)
        editor_layout.setVerticalSpacing(9)
        editor_layout.addRow("内容", owner.memory_content_edit)
        editor_layout.addRow("层级", owner.memory_layer_combo)
        editor_layout.addRow("分类", owner.memory_category_edit)
        editor_layout.addRow("重要性", owner.memory_importance_spin)
        editor_layout.addRow("置信度", owner.memory_confidence_spin)
        editor_layout.addRow("来源", owner.memory_source_edit)
        editor_layout.addRow("", owner.memory_save_button)
        editor_content_layout = QVBoxLayout(owner.memory_editor_content)
        editor_content_layout.setContentsMargins(0, 0, 0, 0)
        editor_content_layout.setSpacing(0)
        editor_content_layout.addWidget(owner.memory_editor_form)
        owner.memory_editor_container.setWidget(owner.memory_editor_content)
        owner.memory_editor_container.setVisible(False)

        # 上窗格只放表格,下窗格放「选择行 + 编辑区」,用竖直 QSplitter 隔开:
        # 手柄正好落在表格底部、选择行上方,拖动它即可手动加长记忆列表
        # (编辑区随之收缩并内部滚动),不必只靠拉伸整窗。
        owner.memory_editor_pane = QWidget(tab)
        owner.memory_editor_pane.setObjectName("memoryEditorPane")
        editor_pane_layout = QVBoxLayout(owner.memory_editor_pane)
        editor_pane_layout.setContentsMargins(0, 0, 0, 0)
        editor_pane_layout.setSpacing(10)
        editor_pane_layout.addLayout(selection_layout)
        editor_pane_layout.addWidget(owner.memory_editor_container)

        owner.memory_list_splitter = _GripSplitter(Qt.Orientation.Vertical, tab)
        owner.memory_list_splitter.setObjectName("memoryListSplitter")
        owner.memory_list_splitter.setChildrenCollapsible(False)
        owner.memory_list_splitter.setHandleWidth(8)
        owner.memory_list_splitter.addWidget(owner.memory_table)
        owner.memory_list_splitter.addWidget(owner.memory_editor_pane)
        owner.memory_list_splitter.setStretchFactor(0, 1)
        owner.memory_list_splitter.setStretchFactor(1, 0)

        layout = QVBoxLayout()
        layout.setContentsMargins(16, 18, 16, 16)
        layout.setSpacing(10)
        layout.addWidget(owner.memory_curation_group)
        layout.addLayout(filter_layout)
        layout.addLayout(status_layout)
        layout.addWidget(owner.memory_list_splitter, 1)
        tab.setLayout(layout)

        owner.memory_status_label.setText("打开记忆页时读取长期记忆。")
        owner._show_memory_placeholder("切换到记忆页后读取长期记忆。")
        owner._clear_memory_editor()
        # 初始收起编辑区:把下窗格高度钉到选择行,空间全归列表、手柄不留可拖出的空白。
        owner._set_memory_editor_visible(False)
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
