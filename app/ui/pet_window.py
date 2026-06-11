from __future__ import annotations

import sys
import time
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

from PySide6.QtCore import (
    QEvent,
    QObject,
    QPoint,
    QRect,
    QSize,
    Qt,
    QThread,
    QTimer,
    Signal,
    Slot,
)
from PySide6.QtGui import (
    QColor,
    QCursor,
    QFont,
    QIcon,
    QKeyEvent,
    QMouseEvent,
    QPainter,
    QPixmap,
)
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QFrame,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QPushButton,
    QSystemTrayIcon,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from app.agent import (
    AgentEvent,
    AgentProgress,
    AgentResult,
    PendingToolAction,
)
from app.agent.memory_curator import (
    MemoryCurationResult,
)
from app.agent.memory_curation_worker import MemoryCurationWorker
from app.agent.screen_tools import SCREEN_OBSERVATION_REQUEST_ACTION
from app.core.app_context import AppContext
from app.config.character_loader import (
    DEFAULT_CHARACTER_ID,
    THEME_SOURCE_PACKAGE,
    CharacterConfigError,
    CharacterProfile,
    CharacterRegistry,
    load_character_system_prompt,
    save_character_theme,
)
from app.storage.chat_history import ChatHistoryEntry, ChatHistoryStore
from app.agent.runtime_events import (
    APP_CLOSED,
    APP_STARTED,
    LONG_HIDDEN_SECONDS,
    PET_HIDDEN,
    PET_REOPENED,
    RuntimeEvent,
    RuntimeEventLog,
    RuntimeEventQueue,
    build_runtime_event_context_message,
)
from app.llm.chat_reply import ChatReply, ChatSegment, parse_chat_reply_result
from app.llm.context_trimming import trim_messages_for_model
from app.core.chat_worker import ChatWorker, EventWorker
from app.core.debug_log import debug_log, summarize_messages
from app.config.settings_service import BubbleSettings, StartupSettings
from app.platforms.launch_at_login import (
    LaunchAtLoginError,
    set_launch_at_login_enabled,
)
from app.ui.history_window import HistoryWindow
from app.ui.log_window import RuntimeLogWindow
from app.agent.proactive_care import (
    PROACTIVE_SCREEN_CONTEXT_HISTORY_MARKER,
    PROACTIVE_TIMER_DUE_GRACE_SECONDS,
    PROACTIVE_TIMER_POLL_INTERVAL_MS,
)
from app.agent.screen_observation import (
    SCREEN_OBSERVATION_HISTORY_MARKER,
    ScreenObservation,
    append_manual_observation_marker,
    append_observation_marker,
    build_screen_observation_from_pixmap,
    build_screen_observation_user_message,
    capture_screen_observation,
)
from app.ui.settings_dialog import SettingsDialog
from app.ui.portrait_controller import (
    PORTRAIT_BASE_MAX_HEIGHT,
    PORTRAIT_BASE_MAX_WIDTH,
    PORTRAIT_SCALE_DEFAULT_PERCENT,
    normalize_portrait_scale_percent,
)
from app.ui.control_panel_layout import (
    CONTROL_PANEL_BOTTOM_MARGIN,
    CONTROL_PANEL_GAP,
    DEFAULT_BUBBLE_HEIGHT,
    DEFAULT_CONTROL_PANEL_VERTICAL_OFFSET,
    DEFAULT_CONTROL_PANEL_WIDTH,
    DEFAULT_INPUT_BAR_OFFSET,
    INPUT_BAR_HEIGHT,
    MAX_BUBBLE_HEIGHT,
    MIN_BUBBLE_HEIGHT,
    MIN_CONTROL_PANEL_WIDTH,
    PetLayout,
    compute_pet_layout,
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
from app.voice.tts import (
    TTS_PROVIDER_GENIE,
    GenieTTSProvider,
    GPTSoVITSTTSProvider,
    GPTSoVITSTTSSettings,
    NullTTSProvider,
    TTSConfigError,
    TTSProvider,
)
from app.storage.visual_observation import (
    VISUAL_OBSERVATION_RECENT_MINUTES,
    VisualObservationJob,
    VisualObservationStore,
    build_visual_context_message,
    generate_visual_observation_id,
    should_inject_visual_context,
)
from app.ui.fonts import _rounded_chinese_font, _rounded_japanese_font
from app.ui.input_bar_animator import InputBarAnimator
from app.ui.card_container import CardContainer
from app.ui.window_backdrop import MacOSVisualEffectBackdrop, VisualEffectMode
from app.ui.input_blur_background import InputBlurBackground, make_blurred_pixmap
from app.ui.bubble_auto_hide import BubbleAutoHideController
from app.ui import (
    ManualScreenshotOverlay,
    PortraitController,
    SubtitleController,
    ToolConfirmationPanel,
    build_pet_tray_menu,
    capture_virtual_desktop_pixmap,
)
from app.ui.styles import pet_window_stylesheet
from app.ui.theme import (
    DEFAULT_THEME_SETTINGS,
    ThemeSettings,
    build_app_chrome_stylesheet,
    build_message_box_stylesheet,
    merge_theme_with_character,
)
from app.voice import VoicePlaybackController

if TYPE_CHECKING:
    from app.core.bootstrap import DeferredStartupServices


REMINDER_CHECK_INTERVAL_MS = 30_000
STARTUP_INITIALIZING_TEXT = "初始化中……"
TTS_ERROR_DISPLAY_MS = 8_000
MEMORY_STATUS_DISPLAY_MS = 7_000
MEMORY_STATUS_STARTUP_DELAY_MS = 1_000
SUBTITLE_LANGUAGE_JA = "ja"
SUBTITLE_LANGUAGE_ZH = "zh"
MANUAL_SCREENSHOT_DEFAULT_TEXT = "请根据我框选的截图继续对话。"
_UI_ASSETS_DIR = Path(__file__).with_name("assets")
_SCREENSHOT_ICON_PATH = _UI_ASSETS_DIR / "screenshot-select.svg"
_SCREENSHOT_ATTACHED_ICON_PATH = _UI_ASSETS_DIR / "screenshot-attached.svg"
PROACTIVE_RECENT_CONVERSATION_LIMIT = 12
PROACTIVE_RECENT_CONVERSATION_CONTENT_LIMIT = 800
PROACTIVE_RECENT_CONVERSATION_SUMMARY_HINT = (
    "这些 recent_conversation 消息用于理解这段时间发生了什么、用户当前阶段和 Sakura "
    "刚刚说过什么；不要逐字复述，应结合屏幕变化自然回应，并避免连续重复同一种休息提醒。"
)
REPLY_HISTORY_PANEL_WIDTH = 34
REPLY_HISTORY_PANEL_HEIGHT = 70
REPLY_HISTORY_BUTTON_SIZE = 30
REPLY_HISTORY_PREVIOUS_SYMBOL = "▲"
REPLY_HISTORY_NEXT_SYMBOL = "▼"


def _message_box_theme(parent: QWidget | None, theme_settings: ThemeSettings | None) -> ThemeSettings:
    theme = theme_settings or getattr(parent, "theme_settings", DEFAULT_THEME_SETTINGS)
    if not isinstance(theme, ThemeSettings):
        theme = DEFAULT_THEME_SETTINGS
    return theme.normalized()


def show_themed_message_box(
    parent: QWidget | None,
    icon: QMessageBox.Icon,
    title: str,
    text: str,
    *,
    theme_settings: ThemeSettings | None = None,
    buttons: QMessageBox.StandardButton = QMessageBox.StandardButton.Ok,
    default_button: QMessageBox.StandardButton = QMessageBox.StandardButton.Ok,
) -> QMessageBox.StandardButton:
    """使用当前 Sakura 主题显示 QMessageBox。"""

    box = QMessageBox(parent)
    box.setIcon(icon)
    box.setWindowTitle(title)
    box.setText(text)
    box.setStandardButtons(buttons)
    if default_button != QMessageBox.StandardButton.NoButton:
        box.setDefaultButton(default_button)
    box.setStyleSheet(build_message_box_stylesheet(_message_box_theme(parent, theme_settings)))
    return QMessageBox.StandardButton(box.exec())


def show_themed_information(
    parent: QWidget | None,
    title: str,
    text: str,
    *,
    theme_settings: ThemeSettings | None = None,
) -> QMessageBox.StandardButton:
    return show_themed_message_box(
        parent,
        QMessageBox.Icon.Information,
        title,
        text,
        theme_settings=theme_settings,
    )


def show_themed_warning(
    parent: QWidget | None,
    title: str,
    text: str,
    *,
    theme_settings: ThemeSettings | None = None,
) -> QMessageBox.StandardButton:
    return show_themed_message_box(
        parent,
        QMessageBox.Icon.Warning,
        title,
        text,
        theme_settings=theme_settings,
    )


def show_themed_critical(
    parent: QWidget | None,
    title: str,
    text: str,
    *,
    theme_settings: ThemeSettings | None = None,
) -> QMessageBox.StandardButton:
    return show_themed_message_box(
        parent,
        QMessageBox.Icon.Critical,
        title,
        text,
        theme_settings=theme_settings,
    )


class TTSReadyWarmupWorker(QObject):
    """后台启动并检测 TTS 服务，避免首次朗读承担冷启动。"""

    succeeded = Signal(str)
    failed = Signal(str)
    finished = Signal()

    def __init__(self, provider: TTSProvider) -> None:
        super().__init__()
        self.provider = provider

    @Slot()
    def run(self) -> None:
        try:
            ensure_ready = getattr(self.provider, "ensure_ready", None)
            if not callable(ensure_ready):
                return
            debug_log("TTS", "开始后台预热 TTS 服务", {"provider": type(self.provider).__name__})
            ok, message = ensure_ready()
            if ok:
                debug_log(
                    "TTS",
                    "后台预热 TTS 服务完成",
                    {"provider": type(self.provider).__name__, "message": message},
                )
                self.succeeded.emit(message)
            else:
                debug_log(
                    "TTS",
                    "后台预热 TTS 服务失败",
                    {"provider": type(self.provider).__name__, "message": message},
                )
                self.failed.emit(message)
        except Exception as exc:  # noqa: BLE001
            message = f"TTS 服务预热失败：{exc}"
            debug_log(
                "TTS",
                "后台预热 TTS 服务异常",
                {"provider": type(self.provider).__name__, "error": str(exc)},
            )
            self.failed.emit(message)
        finally:
            self.finished.emit()


class PetWindow(QWidget):
    memory_status_changed = Signal(str, str)

    def __init__(
        self,
        context: AppContext,
    ) -> None:
        super().__init__()
        self.context = context
        self.base_dir = context.base_dir
        self.startup_initializing = context.startup_initializing
        self.deferred_startup_thread: QThread | None = None
        self.deferred_startup_worker: QObject | None = None
        self.tts_ready_warmup_thread: QThread | None = None
        self.tts_ready_warmup_worker: QObject | None = None
        self.settings_service = context.settings_service
        self.character_registry = context.character_registry
        self.character_profile = context.character_profile
        self.api_client = context.api_client
        self.system_prompt = context.system_prompt
        self.memory_store = context.memory_store
        self.reminder_store = context.reminder_store
        self.tool_registry = context.tool_registry
        self.mcp_tool_provider = context.mcp_tool_provider
        self.plugin_manager = context.plugin_manager
        self.agent_runtime = context.agent_runtime
        self.tts_provider = context.tts_provider
        self.retired_tts_providers: list[TTSProvider] = []
        self.history_store = context.history_store
        self.runtime_event_log = context.runtime_event_log
        self.visual_observation_store = context.visual_observation_store
        self.mcp_settings = context.mcp_settings
        self.debug_log_settings = context.debug_log_settings
        self.startup_settings = context.startup_settings
        self.theme_settings = merge_theme_with_character(
            self.settings_service.load_theme_settings(),
            self.character_profile,
        )
        self.memory_curation_settings = context.memory_curation_settings
        self.memory_curation_state = context.memory_curation_state
        self.memory_curator = context.memory_curator
        self.subtitle_language = self._load_subtitle_language()
        self.screen_observation_enabled = self._load_screen_observation_enabled()
        self.autonomous_screen_observation_enabled = self._load_autonomous_screen_observation_enabled()
        self.proactive_care_settings = context.proactive_care_settings
        self.model_vision_enabled = self.screen_observation_enabled
        self.agent_runtime.set_model_vision_enabled(self.model_vision_enabled)
        self.agent_runtime.set_autonomous_screen_observation_enabled(
            self.autonomous_screen_observation_enabled
        )
        self.free_access_enabled = self._load_free_access_enabled()
        self.tool_registry.set_free_access_enabled(self.free_access_enabled)
        self.always_on_top_enabled = self._load_always_on_top_enabled()
        self.history_window: HistoryWindow | None = None
        self.runtime_log_window: RuntimeLogWindow | None = None
        self.settings_dialog: SettingsDialog | None = None
        self.messages: list[dict[str, Any]] = []
        self.worker_thread: QThread | None = None
        self.worker: ChatWorker | EventWorker | None = None
        self.memory_curation_thread: QThread | None = None
        self.memory_curation_worker: MemoryCurationWorker | None = None
        self.memory_curation_mode = ""
        self.memory_curation_target_history_count = 0
        self.memory_curation_consumed_turns = 0
        self.pending_history_clear_after_curation = False
        self.drag_anchor: QPoint | None = None
        # 是否正在拖动窗口：首次 move 置位，用于拖动时收起输入栏、区分单击与拖动（单击桌宠唤回气泡）。
        self._dragging = False
        self.portrait_scale_percent = self._load_portrait_scale_percent()
        self.control_panel_width = self._load_control_panel_width()
        self.bubble_height = self._load_bubble_height()
        self.control_panel_vertical_offset = self._load_control_panel_vertical_offset()
        self.input_bar_offset = self._load_input_bar_offset()
        # 自适应文本气泡高度（None = 使用用户设置的 bubble_height）
        self._auto_fit_bubble_height: int | None = None
        (
            self.subtitle_typing_interval_ms,
            self.reply_segment_pause_ms,
        ) = self._load_subtitle_display_speed()
        # 初始窗口尺寸：立绘尚未建立，用按缩放的名义立绘尺寸算包围盒；首帧布局后会以实际立绘尺寸校正。
        _init_scale = self.portrait_scale_percent / 100
        self.stage_size = compute_pet_layout(
            portrait_width=round(PORTRAIT_BASE_MAX_WIDTH * _init_scale),
            portrait_height=round(PORTRAIT_BASE_MAX_HEIGHT * _init_scale),
            control_panel_width=self.control_panel_width,
            bubble_height=self.bubble_height,
            vertical_offset=self.control_panel_vertical_offset,
            input_bar_offset=self.input_bar_offset,
        ).window_size
        self.pending_tool_action: PendingToolAction | None = None
        self.pending_manual_screen_observation: ScreenObservation | None = None
        self.manual_screenshot_overlay: ManualScreenshotOverlay | None = None
        self.pending_screen_observation_messages: list[dict[str, Any]] | None = None
        self.pending_screen_observation_event: AgentEvent | None = None
        self.pending_screen_observation_event_reminder_id: str | None = None
        self.pending_visual_observation_jobs: list[VisualObservationJob] = []
        self.pending_event_visual_observation_jobs: list[VisualObservationJob] = []
        self.plugin_chat_ui_widget_instances: list[QWidget] = []
        self.hidden_to_tray = False
        # 运行时事件系统：队列负责注入下次请求，pet_hidden_at 记录隐藏起点用于计算重开时长。
        self.runtime_event_queue = RuntimeEventQueue()
        self.pet_hidden_at: float | None = None
        self._runtime_app_closed_logged = False
        self.screen_observation_followup_in_progress = False
        self.active_reminder_id: str | None = None
        self.active_reminder_text = ""
        self.active_event_type = ""
        self.active_event: AgentEvent | None = None
        self.memory_status_message_active = False
        self.memory_status_last_status = ""
        self.memory_status_last_message = ""
        self.memory_failure_dialog_last_message = ""
        self.memory_failure_dialog_pending_message = ""
        self.last_user_activity_at = time.perf_counter()
        self.last_proactive_care_at: float | None = None
        self.last_proactive_screen_context_at: float | None = None
        self.proactive_screen_context_batch_started_at: float | None = None
        self.proactive_screen_contexts: list[dict[str, Any]] = []
        self.proactive_screen_context_dropped_count = 0
        self.interaction_sequence = 0
        self.active_interaction_id = ""
        self.active_interaction_started_at: float | None = None
        self.active_interaction_last_at: float | None = None
        self.reply_history_segments: list[ChatSegment] = []
        self.reply_history_index: int | None = None
        self.reply_history_review_active = False
        self.reminder_timer = QTimer(self)
        self.reminder_timer.setInterval(REMINDER_CHECK_INTERVAL_MS)
        self.reminder_timer.timeout.connect(self._check_due_reminders)
        self.proactive_care_timer = QTimer(self)
        self.proactive_care_timer.setInterval(PROACTIVE_TIMER_POLL_INTERVAL_MS)
        self.proactive_care_timer.timeout.connect(self._check_proactive_care)
        if not self.startup_initializing:
            self.reminder_timer.start()
            self._sync_proactive_care_timer()
            QTimer.singleShot(0, self._maybe_start_memory_backfill)
        debug_log(
            "PetWindow",
            "窗口运行状态初始化",
            {
                "character_id": self.character_profile.id,
                "character_name": self.character_profile.display_name,
                "tool_count": len(self.tool_registry.all()),
                "mcp_enabled": self.mcp_tool_provider is not None,
                "windows_mcp_enabled": self.mcp_settings.windows_enabled,
                "tts_provider": type(self.tts_provider).__name__,
                "subtitle_language": self.subtitle_language,
                "screen_observation_enabled": self.screen_observation_enabled,
                "autonomous_screen_observation_enabled": self.autonomous_screen_observation_enabled,
                "subtitle_typing_interval_ms": self.subtitle_typing_interval_ms,
                "reply_segment_pause_ms": self.reply_segment_pause_ms,
                "proactive_care": self.proactive_care_settings,
                "auto_memory": self.memory_curation_settings,
                "always_on_top_enabled": self.always_on_top_enabled,
            },
        )

        self.setWindowTitle(self.character_profile.display_name)
        self._apply_window_flags()
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

        self.label = QLabel(self)
        self.label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.portrait_opacity_effect = QGraphicsOpacityEffect(self.label)
        self.portrait_opacity_effect.setOpacity(1.0)
        self.label.setGraphicsEffect(self.portrait_opacity_effect)

        self.portrait_transition_label = QLabel(self)
        self.portrait_transition_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.portrait_transition_label.hide()
        self.portrait_transition_opacity_effect = QGraphicsOpacityEffect(self.portrait_transition_label)
        self.portrait_transition_opacity_effect.setOpacity(0.0)
        self.portrait_transition_label.setGraphicsEffect(self.portrait_transition_opacity_effect)
        self.portrait_controller = PortraitController(
            profile=self.character_profile,
            parent_widget=self,
            main_label=self.label,
            transition_label=self.portrait_transition_label,
            main_opacity_effect=self.portrait_opacity_effect,
            transition_opacity_effect=self.portrait_transition_opacity_effect,
            stage_size=self.stage_size,
            relayout=self._layout_stage,
            raise_foreground=self._raise_foreground_controls,
            on_portrait_changed=self._update_tray_icon_pixmap,
            portrait_scale_percent=self.portrait_scale_percent,
            parent=self,
        )

        self.bubble = QFrame(self)
        self.bubble.setObjectName("speechBubble")
        # 气泡整体透明度效果：驱动每段台词的浮现脉冲（透明窗口不能用 setWindowOpacity）。
        self.bubble_opacity_effect = QGraphicsOpacityEffect(self.bubble)
        self.bubble_opacity_effect.setOpacity(1.0)
        self.bubble.setGraphicsEffect(self.bubble_opacity_effect)

        self.name_label = QLabel(self.character_profile.display_name, self.bubble)
        self.name_label.setObjectName("speakerName")

        initial_speech = (
            STARTUP_INITIALIZING_TEXT
            if self.startup_initializing
            else self.character_profile.initial_message
        )
        self.speech_label = QLabel(initial_speech, self.bubble)
        self.speech_label.setObjectName("speechText")
        self.speech_label.setWordWrap(True)
        self.speech_label.setAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)

        self.tts_error_label = QLabel("", self.bubble)
        self.tts_error_label.setObjectName("ttsErrorText")
        self.tts_error_label.setWordWrap(True)
        self.tts_error_label.setVisible(False)
        self.tts_error_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.tts_error_timer = QTimer(self)
        self.tts_error_timer.setSingleShot(True)
        self.tts_error_timer.timeout.connect(self._hide_tts_error)

        self.reply_history_panel = QFrame(self.bubble)
        _configure_reply_history_panel(self.reply_history_panel)

        self.reply_history_previous_button = QToolButton(self.reply_history_panel)
        _configure_reply_history_button(
            self.reply_history_previous_button,
            text=REPLY_HISTORY_PREVIOUS_SYMBOL,
            tooltip="上一条历史消息",
        )
        self.reply_history_previous_button.clicked.connect(self._show_previous_reply_history)

        self.reply_history_next_button = QToolButton(self.reply_history_panel)
        _configure_reply_history_button(
            self.reply_history_next_button,
            text=REPLY_HISTORY_NEXT_SYMBOL,
            tooltip="下一条历史消息",
        )
        self.reply_history_next_button.clicked.connect(self._show_next_reply_history)

        self.voice_playback_controller = VoicePlaybackController(
            self.tts_provider,
            self._log_interaction_stage,
            lambda: str(getattr(getattr(self.tts_provider, "settings", None), "text_lang", "ja")),
            self._show_tts_error,
        )
        self._connect_tts_error_signal(self.tts_provider)
        self.subtitle_controller = SubtitleController(
            self.speech_label,
            self.voice_playback_controller,
            self.subtitle_language,
            self._log_interaction_stage,
            self._apply_reply_segment,
            lambda: self._end_interaction("reply_completed"),
            lambda: bool(self.active_interaction_id),
            self,
            preload_segment=self.portrait_controller.preload_for_segment,
            typing_interval_ms=self.subtitle_typing_interval_ms,
            segment_pause_ms=self.reply_segment_pause_ms,
            bubble_opacity_effect=self.bubble_opacity_effect,
            on_typing_overflow=self._fit_bubble_for_label_height,
        )
        self.speech_timer = self.subtitle_controller.speech_timer
        if not self.startup_initializing:
            QTimer.singleShot(0, self._warm_up_current_tts_playback)
            QTimer.singleShot(0, self._start_current_tts_ready_warmup)

        bubble_header = QHBoxLayout()
        bubble_header.setContentsMargins(0, 0, 0, 0)
        bubble_header.addWidget(self.name_label)
        bubble_header.addStretch(1)

        bubble_text_layout = QVBoxLayout()
        bubble_text_layout.setContentsMargins(0, 0, 0, 0)
        bubble_text_layout.setSpacing(6)
        bubble_text_layout.addLayout(bubble_header)
        bubble_text_layout.addWidget(self.speech_label, 1)
        bubble_text_layout.addWidget(self.tts_error_label)

        history_button_layout = QVBoxLayout()
        history_button_layout.setContentsMargins(2, 3, 2, 3)
        history_button_layout.setSpacing(4)
        history_button_layout.addWidget(self.reply_history_previous_button)
        history_button_layout.addWidget(self.reply_history_next_button)
        self.reply_history_panel.setLayout(history_button_layout)

        bubble_body_layout = QHBoxLayout()
        bubble_body_layout.setContentsMargins(0, 0, 0, 0)
        bubble_body_layout.setSpacing(10)
        bubble_body_layout.addLayout(bubble_text_layout, 1)
        bubble_body_layout.addWidget(self.reply_history_panel, 0, Qt.AlignmentFlag.AlignVCenter)

        bubble_layout = QVBoxLayout()
        bubble_layout.setContentsMargins(22, 12, 18, 14)
        bubble_layout.setSpacing(0)
        bubble_layout.addLayout(bubble_body_layout, 1)
        self.bubble.setLayout(bubble_layout)
        # 气泡为主窗口直接子控件（单窗口重构）：随主窗口单帧合成，不再是独立 HWND。
        # 不额外包容器——浮现脉冲与自动隐藏淡入淡出共用同一个 bubble_opacity_effect，
        # 避免「容器 effect + 内容 effect」嵌套触发 QPainter 冲突（破帧/元素消失）。
        # 圆角与底色由 #speechBubble 的 QSS 负责（主窗口样式表级联）。

        self.input_bar = QFrame(self)
        self.input_bar.setObjectName("inputBar")

        self.input_edit = QLineEdit(self.input_bar)
        self.input_edit.setObjectName("petInput")
        self.input_edit.setPlaceholderText(self._normal_input_placeholder_text())
        self.input_edit.setFixedHeight(38)
        self.input_edit.installEventFilter(self)
        self.input_edit.returnPressed.connect(self._handle_return_pressed)

        self.send_button = QPushButton("发送", self.input_bar)
        self.send_button.setObjectName("sendButton")
        self.send_button.setFixedHeight(38)
        self.send_button.clicked.connect(self._handle_send_button_clicked)
        self.reply_waiting_ui_active = False

        self.screenshot_button = QToolButton(self.input_bar)
        self.screenshot_button.setObjectName("screenshotButton")
        self.screenshot_button.setFixedSize(38, 38)
        self.screenshot_button.setIcon(QIcon(str(_SCREENSHOT_ICON_PATH)))
        self.screenshot_button.setIconSize(QSize(18, 18))
        self.screenshot_button.setProperty("screenshotAttached", False)
        self.screenshot_button.setToolTip("框选截图并附加到下一条消息；右键清除")
        self.screenshot_button.installEventFilter(self)
        self.screenshot_button.clicked.connect(self._handle_screenshot_button_clicked)

        self.tool_confirmation_panel = ToolConfirmationPanel(
            self.confirm_pending_action,
            self.cancel_pending_action,
            self.input_bar,
        )
        self.confirm_action_button = self.tool_confirmation_panel.confirm_button
        self.cancel_action_button = self.tool_confirmation_panel.cancel_button

        input_layout = QHBoxLayout()
        input_layout.setContentsMargins(10, 7, 10, 7)
        input_layout.setSpacing(8)
        input_layout.addWidget(self.input_edit, 1)
        input_layout.addWidget(self.tool_confirmation_panel)
        input_layout.addWidget(self.screenshot_button)
        input_layout.addWidget(self.send_button)
        self.input_bar.setLayout(input_layout)
        # 输入栏为「窗口内」卡片容器（单窗口重构）：Windows 亚克力不再暴露为可选项；
        # macOS 原生毛玻璃用 NSVisualEffectView 挂在输入栏子视图背后，其余非纯色模式走软件高斯模糊。
        self.input_blur_background = InputBlurBackground(corner_radius=22.0)
        self.input_native_backdrop = MacOSVisualEffectBackdrop()
        needs_bg, input_before_show, input_after_show, input_before_hide = self._input_bar_blur_pipeline()
        self.input_card = CardContainer(
            self.input_bar,
            background_layer=self.input_blur_background if needs_bg else None,
            parent=self,
        )
        self.input_bar_animator = InputBarAnimator(
            self.input_bar,
            self.input_card,
            self.input_card.fade_effect,
            self._input_bar_pinned,
            self._cursor_in_pet_region,
            parent=self,
            before_show=input_before_show,
            after_show=input_after_show,
            before_hide=input_before_hide,
        )
        # 气泡无操作自动隐藏控制器：说完话后倒计时，悬停桌宠暂停，超时淡出，点击桌宠唤回。
        self.bubble_settings = self.settings_service.load_bubble_settings()
        # 自动隐藏复用气泡自身的 opacity effect（与浮现脉冲同一个，二者时间互斥），不再嵌套容器 effect。
        self.bubble_auto_hide = BubbleAutoHideController(
            self.bubble,
            self.bubble_opacity_effect,
            self._cursor_in_pet_region,
            enabled=self.bubble_settings.auto_hide_enabled,
            delay_seconds=self.bubble_settings.auto_hide_delay_seconds,
            parent=self,
        )
        self._sync_plugin_chat_ui_widgets()

        self._apply_theme_settings(self.theme_settings)
        self._apply_fonts()
        self._load_reply_history_from_store()
        self._update_reply_history_buttons()
        for drag_widget in (
            self.label,
            self.portrait_transition_label,
            self.bubble,
            self.name_label,
            self.speech_label,
        ):
            drag_widget.installEventFilter(self)

        # 初始：先按当前立绘贴图，再用统一布局模型把窗口尺寸校正到实际立绘并摆放子控件。
        # 位置稍后由 _move_to_default_position 处理，故此处不做底边锚点（anchor=None 走平铺 resize）。
        self.portrait_controller.apply_current()
        self._apply_pet_layout()
        self._create_tray_icon()
        self.memory_status_changed.connect(self._handle_memory_status_changed)
        self._connect_memory_status_listener()
        self._move_to_default_position()
        if getattr(self, "startup_initializing", False):
            self._apply_startup_initializing_state()

        application = QApplication.instance()
        if application is not None:
            application.aboutToQuit.connect(self.close_external_tools)
            if sys.platform == "darwin":
                application.installEventFilter(self)

    def resizeEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        super().resizeEvent(event)
        self._layout_stage()

    def moveEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        super().moveEvent(event)
        # 单窗口重构后气泡/输入栏为子控件，随主窗口一起移动，无需在此重定位。

    def showEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        super().showEvent(event)
        # 子控件随主窗口显示；此处只需把它们摆到位并启动动画/自动隐藏。
        self._layout_stage()
        if hasattr(self, "bubble"):
            self.bubble.show()
        if hasattr(self, "input_bar_animator"):
            self.input_bar_animator.start()
        if hasattr(self, "bubble_auto_hide"):
            self.bubble_auto_hide.start()
        # macOS 上子控件 z 序在窗口刚提交时可能未稳定，补两发 raise 确保气泡/输入栏在立绘前端。
        if sys.platform == "darwin":
            QTimer.singleShot(0, self._raise_foreground_controls)
            QTimer.singleShot(100, self._raise_foreground_controls)
        self._refresh_tray_menu()
        self._schedule_native_topmost_sync()
        if getattr(self, "memory_failure_dialog_pending_message", ""):
            QTimer.singleShot(0, self._show_pending_memory_failure_dialog)

    def hideEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        super().hideEvent(event)
        # 子控件随主窗口隐藏，无需单独 hide。
        self._refresh_tray_menu()

    def changeEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        super().changeEvent(event)
        if event.type() in {
            QEvent.Type.ActivationChange,
            QEvent.Type.WindowStateChange,
        }:
            self._schedule_native_topmost_sync()

    def event(self, event) -> bool:  # type: ignore[override]
        if _is_screen_change_event(event):
            self._schedule_screen_change_relayout()
        return super().event(event)

    def eventFilter(self, watched, event) -> bool:  # type: ignore[no-untyped-def]
        application = QApplication.instance()
        if application is not None and watched is application:
            if event.type() == QEvent.Type.ApplicationActivate:
                self._handle_application_activated()
            return super().eventFilter(watched, event)
        if watched is self.input_edit:
            if event.type() == QEvent.Type.KeyPress:
                self._log_input_key_event(event)
            return super().eventFilter(watched, event)
        if watched is self.screenshot_button and isinstance(event, QMouseEvent):
            if (
                event.type() == QEvent.Type.MouseButtonPress
                and event.button() == Qt.MouseButton.RightButton
            ):
                self._clear_manual_screen_observation()
                return True
            return super().eventFilter(watched, event)
        if watched in {
            self.label,
            self.portrait_transition_label,
            self.bubble,
            self.name_label,
            self.speech_label,
        } and isinstance(event, QMouseEvent):
            if event.type() == QEvent.Type.MouseButtonPress:
                return self._handle_mouse_press(event, watched)
            if event.type() == QEvent.Type.MouseMove:
                return self._handle_mouse_move(event)
            if event.type() == QEvent.Type.MouseButtonRelease:
                return self._handle_mouse_release(event)
        return super().eventFilter(watched, event)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        self._handle_mouse_press(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        self._handle_mouse_move(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        self._handle_mouse_release(event)

    def closeEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        self.close_external_tools()
        super().closeEvent(event)

    @Slot()
    def close_external_tools(self) -> None:
        self._emit_app_closed_event()
        self.close_tts_tools()
        self.close_mcp_tools()
        self.close_plugins()

    def _emit_app_started_event(self) -> None:
        """启动就绪后落盘 app.started；若存在上次关闭记录则附带跨会话信息并注入首条消息。"""
        log = getattr(self, "runtime_event_log", None)
        carryover = log.load_startup_carryover() if log is not None else None
        away = carryover.get("away_seconds") if carryover else None
        priority = 1 if isinstance(away, (int, float)) and away >= LONG_HIDDEN_SECONDS else 0
        self.emit_runtime_event(
            APP_STARTED,
            source="startup",
            metadata=carryover or {},
            priority=priority,
            # 无上次关闭记录（首启 / 上次异常退出）时只落盘，不注入空洞的「已启动」提示。
            inject=carryover is not None,
        )

    def _emit_app_closed_event(self) -> None:
        """关闭前落盘 app.closed（供下次启动衔接）。退出链路可能多次触发，做一次性保护。"""
        if getattr(self, "_runtime_app_closed_logged", False):
            return
        self._runtime_app_closed_logged = True
        self.emit_runtime_event(
            APP_CLOSED,
            source="shutdown",
            metadata={"interrupted_reply": self.worker_thread is not None},
            inject=False,
        )

    @Slot()
    def close_tts_tools(self) -> None:
        providers = [self.tts_provider, *self.retired_tts_providers]
        self.retired_tts_providers = []
        seen: set[int] = set()
        for provider in providers:
            provider_id = id(provider)
            if provider_id in seen:
                continue
            seen.add(provider_id)
            close = getattr(provider, "close", None)
            if not callable(close):
                continue
            try:
                close()
            except Exception as exc:  # noqa: BLE001
                debug_log(
                    "TTS",
                    "关闭 TTS Provider 失败",
                    {"provider": type(provider).__name__, "error": str(exc)},
                )

    @Slot()
    def close_mcp_tools(self) -> None:
        if self.mcp_tool_provider is None:
            return
        self.mcp_tool_provider.close()
        self.mcp_tool_provider = None

    @Slot()
    def close_plugins(self) -> None:
        self.plugin_manager.shutdown_all()

    def _handle_mouse_press(self, event: QMouseEvent, source_widget: QWidget | None = None) -> bool:
        if event.button() == Qt.MouseButton.LeftButton:
            self.drag_anchor = self._drag_anchor_from_event(event, source_widget)
            event.accept()
            return True
        if event.button() == Qt.MouseButton.RightButton:
            event.accept()
            return True
        return False

    def _handle_mouse_move(self, event: QMouseEvent) -> bool:
        if event.buttons() & Qt.MouseButton.LeftButton and self.drag_anchor is not None:
            if not self._dragging:
                # 首次进入拖动：收起输入栏，避免静态模糊背景与移动后的真实桌面对不上而穿帮。
                self._dragging = True
                self.input_bar_animator.suspend_for_drag()
            self.move(event.globalPosition().toPoint() - self.drag_anchor)
            event.accept()
            return True
        return False

    def _handle_mouse_release(self, event: QMouseEvent) -> bool:
        if event.button() == Qt.MouseButton.LeftButton:
            was_dragging = self._dragging
            self.drag_anchor = None
            self._dragging = False
            if was_dragging:
                # 拖动结束：延一帧等窗口真正落位，再重截新位置桌面并重新显示输入栏。
                QTimer.singleShot(0, self._finish_drag_resume)
            else:
                # 单击（非拖动）桌宠：若气泡处于自动隐藏态则唤回。
                self._handle_pet_click()
            event.accept()
            return True
        if event.button() == Qt.MouseButton.RightButton:
            self._show_context_menu(event.position().toPoint())
            event.accept()
            return True
        return False

    def _finish_drag_resume(self) -> None:
        """拖动松手后：让输入栏按可见性重算（重截新位置桌面后现身）。

        单窗口重构后气泡/输入栏为子控件，已随主窗口移动到新位置，无需重定位；
        仅在 macOS 上补一发 raise 保证 z 序，再触发输入栏动画恢复。
        """
        if sys.platform == "darwin":
            self._raise_foreground_controls()
        animator = getattr(self, "input_bar_animator", None)
        if animator is not None:
            animator.resume_after_drag()

    def _handle_pet_click(self) -> None:
        """单击桌宠（非拖动）：唤回被自动隐藏的气泡。具体由气泡自动隐藏控制器实现。"""
        controller = getattr(self, "bubble_auto_hide", None)
        if controller is not None:
            controller.handle_pet_clicked()

    def _apply_bubble_settings(self, settings: BubbleSettings) -> None:
        """应用气泡无操作自动隐藏配置到控制器（设置保存后调用）。"""
        self.bubble_settings = settings
        controller = getattr(self, "bubble_auto_hide", None)
        if controller is not None:
            controller.set_settings(
                enabled=settings.auto_hide_enabled,
                delay_seconds=settings.auto_hide_delay_seconds,
            )

    def _drag_anchor_from_event(
        self,
        event: QMouseEvent,
        source_widget: QWidget | None = None,
    ) -> QPoint:
        position = event.position().toPoint()
        if source_widget is None or source_widget is self:
            return position

        # source 可能在独立子窗口（气泡卡片）里，经全局坐标中转到主窗口本地坐标，
        # 对跨窗口控件也有效（mapTo 仅对同一窗口内的后代有效）。
        map_to_global = getattr(source_widget, "mapToGlobal", None)
        if callable(map_to_global):
            return self.mapFromGlobal(map_to_global(position))
        return position

    def _schedule_screen_change_relayout(self) -> None:
        QTimer.singleShot(0, self._restore_geometry_after_screen_change)

    def _restore_geometry_after_screen_change(self) -> None:
        self._apply_pet_layout()
        self._schedule_native_topmost_sync()

    def _apply_reply_segment(self, segment: ChatSegment) -> None:
        # 同轮回复内各段高度延续：不在此重置，避免"段间先缩后扩"产生闪现。
        # 高度重置由 _collapse_auto_fit_bubble_height 在 cancel_reply_flow 前统一处理。
        self.portrait_controller.apply_for_segment(segment)
        self._sync_reply_history_index_for_segment(segment)
        # 新台词开始：保持气泡显示并暂停自动隐藏倒计时。
        controller = getattr(self, "bubble_auto_hide", None)
        if controller is not None:
            controller.notify_speaking()

    def _normal_input_placeholder_text(self, profile: CharacterProfile | None = None) -> str:
        profile = profile or self.character_profile
        return f"和{profile.display_name}说点什么..."

    def _reply_waiting_placeholder_text(self) -> str:
        return f"{self.character_profile.display_name}正在思考中…"

    def _set_reply_waiting_ui(self, waiting: bool) -> None:
        """切换回复等待期间的输入区状态：保留输入能力，只提示当前正在等待。"""
        if getattr(self, "startup_initializing", False):
            waiting = False
        self.reply_waiting_ui_active = waiting
        if hasattr(self, "input_edit"):
            self.input_edit.setPlaceholderText(
                self._reply_waiting_placeholder_text()
                if waiting
                else self._normal_input_placeholder_text()
            )
            self._set_widget_dynamic_property(self.input_edit, "replyWaiting", waiting)
        if hasattr(self, "send_button"):
            self._set_widget_dynamic_property(self.send_button, "replyWaiting", waiting)
        self._sync_input_bar_waiting_visibility()

    def _sync_input_bar_waiting_visibility(self) -> None:
        animator = getattr(self, "input_bar_animator", None)
        sync = getattr(animator, "sync", None)
        if callable(sync):
            sync()

    def _set_widget_dynamic_property(self, widget: QWidget | None, name: str, value: object) -> None:
        if widget is None:
            return
        property_getter = getattr(widget, "property", None)
        if callable(property_getter) and property_getter(name) == value:
            return
        set_property = getattr(widget, "setProperty", None)
        if not callable(set_property):
            return
        set_property(name, value)
        style_getter = getattr(widget, "style", None)
        if not callable(style_getter):
            return
        style = style_getter()
        style.unpolish(widget)
        style.polish(widget)
        update = getattr(widget, "update", None)
        if callable(update):
            update()

    def _remember_reply_history_segments(self, segments: list[ChatSegment]) -> None:
        clean_segments = [segment for segment in segments if segment.text.strip()]
        if not clean_segments:
            return
        self.reply_history_segments.extend(clean_segments)
        if self.reply_history_index is None:
            self.reply_history_index = len(self.reply_history_segments) - 1
        self._update_reply_history_buttons()

    def _load_reply_history_from_store(self) -> None:
        try:
            entries = self.history_store.load()
        except OSError as exc:
            print(f"[History] 回溯历史读取失败：{exc}")
            debug_log("History", "回溯历史读取失败", {"error": str(exc)})
            entries = []
        self.reply_history_segments = _reply_history_segments_from_entries(entries)
        self.reply_history_index = (
            len(self.reply_history_segments) - 1
            if self.reply_history_segments
            else None
        )
        self.reply_history_review_active = False
        self._update_reply_history_buttons()

    def _sync_reply_history_index_for_segment(self, segment: ChatSegment) -> None:
        for index in range(len(self.reply_history_segments) - 1, -1, -1):
            if self.reply_history_segments[index] is segment:
                self.reply_history_index = index
                self.reply_history_review_active = False
                self._update_reply_history_buttons()
                return
        for index in range(len(self.reply_history_segments) - 1, -1, -1):
            if self.reply_history_segments[index] == segment:
                self.reply_history_index = index
                self.reply_history_review_active = False
                self._update_reply_history_buttons()
                return

    @Slot()
    def _show_previous_reply_history(self) -> None:
        index = self._normalized_reply_history_index()
        if index is None:
            return
        self._show_reply_history_at(index - 1)

    @Slot()
    def _show_next_reply_history(self) -> None:
        index = self._normalized_reply_history_index()
        if index is None:
            return
        self._show_reply_history_at(index + 1)

    def _show_reply_history_at(self, index: int) -> None:
        if not self._can_review_reply_history():
            return
        if index < 0 or index >= len(self.reply_history_segments):
            return

        segment = self.reply_history_segments[index]
        self.reply_history_index = index
        self.reply_history_review_active = True
        self.portrait_controller.apply_for_segment(segment)
        self.subtitle_controller.show_text_immediately(segment.display_text(self.subtitle_language))
        self._log_interaction_stage(
            "reply_history_reviewed",
            {"index": index, "history_count": len(self.reply_history_segments)},
        )
        self._update_reply_history_buttons()

    def _exit_reply_history_review(self, *, update_buttons: bool = True) -> None:
        self.reply_history_review_active = False
        if update_buttons:
            self._update_reply_history_buttons()

    def _refresh_reply_history_review_text(self) -> bool:
        if not self.reply_history_review_active:
            return False
        index = self._normalized_reply_history_index()
        if index is None:
            return False
        segment = self.reply_history_segments[index]
        self.subtitle_controller.show_text_immediately(segment.display_text(self.subtitle_language))
        return True

    def _normalized_reply_history_index(self) -> int | None:
        segments = getattr(self, "reply_history_segments", [])
        if not segments:
            if hasattr(self, "reply_history_index"):
                self.reply_history_index = None
            return None
        if getattr(self, "reply_history_index", None) is None:
            self.reply_history_index = len(segments) - 1
        else:
            self.reply_history_index = max(
                0,
                min(self.reply_history_index, len(segments) - 1),
            )
        return self.reply_history_index

    def _can_review_reply_history(self) -> bool:
        if len(getattr(self, "reply_history_segments", [])) < 2:
            return False
        if getattr(self, "worker_thread", None) is not None:
            return False
        subtitle_controller = getattr(self, "subtitle_controller", None)
        if (
            subtitle_controller is not None
            and hasattr(subtitle_controller, "is_reply_sequence_active")
            and subtitle_controller.is_reply_sequence_active()
        ):
            return False
        return True

    def _update_reply_history_buttons(self) -> None:
        previous_button = getattr(self, "reply_history_previous_button", None)
        next_button = getattr(self, "reply_history_next_button", None)
        if previous_button is None or next_button is None:
            return

        index = self._normalized_reply_history_index()
        can_review = self._can_review_reply_history()
        previous_button.setEnabled(can_review and index is not None and index > 0)
        next_button.setEnabled(
            can_review
            and index is not None
            and index < len(getattr(self, "reply_history_segments", [])) - 1
        )

    def _raise_foreground_controls(self) -> None:
        # 子控件 z 序：气泡/输入栏需浮在立绘之上。
        if hasattr(self, "bubble"):
            self.bubble.raise_()
        if hasattr(self, "input_card"):
            self.input_card.raise_()
        self._raise_open_dialogs()

    def _raise_open_dialogs(self) -> None:
        # 独立窗口打开时应始终在桌宠卡片之上，避免说话时被卡片盖住。
        for dialog in (
            getattr(self, "settings_dialog", None),
            getattr(self, "history_window", None),
            getattr(self, "runtime_log_window", None),
        ):
            if dialog is not None and dialog.isVisible():
                dialog.raise_()

    def _any_dialog_open(self) -> bool:
        for dialog in (
            getattr(self, "settings_dialog", None),
            getattr(self, "history_window", None),
        ):
            if dialog is not None and dialog.isVisible():
                return True
        return False

    def _update_tray_icon_pixmap(self, pixmap: QPixmap) -> None:
        _ = pixmap
        if hasattr(self, "tray_icon"):
            self.tray_icon.setIcon(_build_status_tray_icon(self.theme_settings.primary_color))

    def _apply_fonts(self) -> None:
        text_font = _rounded_chinese_font(13, QFont.Weight.Bold)
        name_font = _rounded_japanese_font(10, QFont.Weight.Bold)
        button_font = _rounded_chinese_font(11, QFont.Weight.ExtraBold)

        self.name_label.setFont(name_font)
        self._apply_speech_font()
        self.input_edit.setFont(text_font)
        self.screenshot_button.setFont(button_font)
        self.send_button.setFont(button_font)

    def _apply_speech_font(self) -> None:
        if self.subtitle_language == SUBTITLE_LANGUAGE_ZH:
            self.speech_label.setFont(_rounded_chinese_font(15, QFont.Weight.Medium))
            return
        self.speech_label.setFont(_rounded_japanese_font(15, QFont.Weight.Medium))

    def _current_portrait_size(self) -> tuple[int, int]:
        """当前立绘标签实际尺寸；标签尚未贴图时回退到按缩放的名义尺寸。"""
        w = self.label.width()
        h = self.label.height()
        if w > 0 and h > 0:
            return w, h
        scale = self.portrait_scale_percent / 100
        return round(PORTRAIT_BASE_MAX_WIDTH * scale), round(PORTRAIT_BASE_MAX_HEIGHT * scale)

    def _effective_bubble_height(self) -> int:
        """自适应文本高度优先，回退到用户设置高度。"""
        if self._auto_fit_bubble_height is not None:
            return self._auto_fit_bubble_height
        return self.bubble_height

    def _compute_pet_layout(self) -> PetLayout:
        pw, ph = self._current_portrait_size()
        return compute_pet_layout(
            portrait_width=pw,
            portrait_height=ph,
            control_panel_width=self.control_panel_width,
            bubble_height=self._effective_bubble_height(),
            vertical_offset=self.control_panel_vertical_offset,
            input_bar_offset=self.input_bar_offset,
        )

    def _portrait_anchor_global(self) -> QPoint:
        """当前布局下立绘底边中心的屏幕坐标——参数变化时把它钉在原位即可让立绘位置不动。

        用「当前布局的 portrait_anchor（窗口本地坐标）映射到全局」，而非读立绘标签几何：
        前者与 _apply_pet_layout 写回时用的是同一套整除公式，能精确往返、不产生逐次累积的像素漂移。
        """
        ax, ay = self._compute_pet_layout().portrait_anchor
        return self.mapToGlobal(QPoint(ax, ay))

    def _apply_pet_layout(self, *, anchor_global: QPoint | None = None) -> None:
        """重算统一布局，并把主窗口与三个子控件一次性（单帧）摆到位。

        anchor_global 给定时：保持立绘底边中心钉在该屏幕点（改气泡高度/输入栏下移/缩放
        都不移动立绘）；为 None 时按当前位置直接 resize（仅初始化/换屏用）。
        气泡/输入栏现为窗口内子控件，随主窗口同帧合成，不再有跨窗口同步竞态。
        """
        layout = self._compute_pet_layout()
        new_w, new_h = layout.window_size
        ax, ay = layout.portrait_anchor
        # setUpdatesEnabled(False) 把窗口几何与子控件位置的更新合并到同一抑制区间，
        # 恢复绘制后单帧呈现，避免任何中间错位帧。用「保存/恢复」而非硬置 True：
        # 当外层（如立绘缩放）已抑帧时，这里不会提前恢复绘制，保证整段操作只出一帧。
        was_enabled = self.updatesEnabled()
        self.setUpdatesEnabled(False)
        try:
            if anchor_global is not None and self.isVisible():
                self.setGeometry(anchor_global.x() - ax, anchor_global.y() - ay, new_w, new_h)
            else:
                self.resize(new_w, new_h)
            self.stage_size = (new_w, new_h)
            self._place_pet_children(layout)
        finally:
            self.setUpdatesEnabled(was_enabled)

    def _place_pet_children(self, layout: PetLayout) -> None:
        """按布局把立绘/气泡/输入栏卡片摆到窗口本地坐标（不改窗口尺寸）。"""
        if not hasattr(self, "input_card"):
            return
        px, py, pw, ph = layout.portrait_rect
        self.label.setGeometry(px, py, pw, ph)
        self.portrait_transition_label.setGeometry(px, py, pw, ph)
        bx, by, bw, bh = layout.bubble_rect
        self.bubble.setGeometry(bx, by, bw, bh)
        ix, iy, iw, ih = layout.input_rect
        self.input_card.setGeometry(ix, iy, iw, ih)
        # 软件模糊背景截图需要输入栏/气泡的窗口本地矩形（转全局），此处缓存。
        self._bubble_local_rect = QRect(bx, by, bw, bh)
        self._input_local_rect = QRect(ix, iy, iw, ih)
        self._sync_input_bar_native_backdrop_geometry()

    def _fit_bubble_for_label_height(self, label_h: int) -> None:
        """打字机溢出回调：按标签实际高度逐行扩展气泡（不持久化、不超上限）。"""
        name_h = self.name_label.sizeHint().height()
        # 纵向开销：bubble_layout 上下 margin(12+14) + name_label + 内层 spacing(6) + 余量(4)
        overhead = 12 + name_h + 6 + 14 + 4
        needed = label_h + overhead
        current = self._effective_bubble_height()
        if needed <= current:
            return
        line_h = self.speech_label.fontMetrics().lineSpacing()
        new_h = min(current + line_h, MAX_BUBBLE_HEIGHT)
        if new_h == current:
            return
        self._auto_fit_bubble_height = new_h
        # 单窗口原子布局：以立绘底边为锚点向上扩展气泡，立绘不动、子控件同帧到位。
        self._apply_pet_layout(anchor_global=self._portrait_anchor_global())

    def _collapse_auto_fit_bubble_height(self) -> None:
        """将自适应气泡高度收回到用户设置值（回复结束/打断时调用），以立绘底边为锚点收缩。"""
        if self._auto_fit_bubble_height is None:
            return
        self._auto_fit_bubble_height = None
        self._apply_pet_layout(anchor_global=self._portrait_anchor_global())

    def _layout_stage(self) -> None:
        """重新摆放子控件到当前窗口（PortraitController 的 relayout 回调 / resizeEvent）。

        只摆子控件、不改窗口尺寸，避免 setGeometry → resizeEvent → _layout_stage 递归；
        窗口尺寸的变更统一由 _apply_pet_layout 负责。
        """
        self._place_pet_children(self._compute_pet_layout())

    def _local_rect_to_global(self, rect: QRect) -> QRect:
        return QRect(self.mapToGlobal(rect.topLeft()), rect.size())

    def _refresh_input_blur_background(self) -> None:
        """输入栏现身前刷新软件模糊背景：截输入栏正后方桌面，模糊后铺到背景层。

        此回调在非纯色模式下绑定到 InputBarAnimator，由其在卡片现身前调用。
        先隐藏输入栏卡片（主窗口该区域透明，露出正后方桌面），截图后再由动画器显示。
        """
        background = getattr(self, "input_blur_background", None)
        input_rect = getattr(self, "_input_local_rect", None)
        if background is None or input_rect is None:
            return

        self.input_card.hide()
        # 让出一帧，确保合成器把刚隐藏的卡片移出画面，否则会截到残影。
        QApplication.processEvents()

        try:
            global_rect = self._local_rect_to_global(input_rect)
            blurred = self._build_blurred_background(global_rect)
            if blurred is not None and not blurred.isNull():
                background.set_blurred_pixmap(blurred)
        except Exception as exc:  # noqa: BLE001
            debug_log("UI", "输入栏软件模糊背景刷新失败", {"error": str(exc)})

    def _build_blurred_background(self, global_rect: QRect) -> QPixmap | None:
        """截取虚拟桌面，裁出 global_rect（逻辑全局坐标）对应区域并做高斯模糊。

        capture_virtual_desktop_pixmap 已把各屏物理像素归一化贴入「逻辑尺寸」的虚拟桌面图，
        故这里直接按逻辑坐标裁剪即可，无需再做 devicePixelRatio 换算。
        """
        desktop_pixmap, virtual_geometry = self._capture_virtual_desktop_pixmap()
        if desktop_pixmap.isNull():
            return None
        offset = global_rect.topLeft() - virtual_geometry.topLeft()
        crop = QRect(offset.x(), offset.y(), global_rect.width(), global_rect.height())
        crop = crop.intersected(desktop_pixmap.rect())
        if crop.isEmpty():
            return None
        cropped = desktop_pixmap.copy(crop)
        # 模糊力度：radius 作用在降采样后的小图上，downscale 越大放大回来越糊。
        return make_blurred_pixmap(cropped, radius=4.0, downscale=2)

    def _cursor_in_pet_region(self) -> bool:
        # 设置/历史窗口打开时禁用输入栏浮现，避免盖住对话框。
        if self._any_dialog_open():
            return False
        # 单窗口重构后气泡/输入栏已并入主窗口，主窗口几何即桌宠整体区域；
        # 光标落在其中即视为悬停桌宠（暂停自动隐藏倒计时）。
        pos = QCursor.pos()
        return self.isVisible() and self.frameGeometry().contains(pos)

    def _input_bar_pinned(self) -> bool:
        """输入栏在以下任一情况保持常显，避免用户操作中途被收起。

        注意：不把「对话进行中(active_interaction_id)」整体算进来；但等待模型回复时输入栏有状态提示
        与呼吸动效，需要保持可见直到回复流程结束。
        """
        # 设置/历史窗口打开时不固定输入栏，配合 hover 禁用一起彻底收起。
        if self._any_dialog_open():
            return False
        return (
            self.input_edit.hasFocus()
            or bool(self.input_edit.text().strip())
            or bool(getattr(self, "reply_waiting_ui_active", False))
            # 用待确认动作状态而非 panel.isVisible()：输入栏卡片收起时 panel 的可见性会假阴性。
            or self.pending_tool_action is not None
        )

    def _create_tray_icon(self) -> None:
        icon = _build_status_tray_icon(self.theme_settings.primary_color)
        self.tray_icon = QSystemTrayIcon(icon, self)
        self.tray_icon.setToolTip(self.character_profile.display_name)
        self.tray_icon.setContextMenu(self._build_menu())
        self.tray_icon.activated.connect(self._handle_tray_activated)
        self.tray_icon.show()

    def _build_menu(self) -> QMenu:
        return build_pet_tray_menu(
            self,
            chinese_subtitles_checked=self.subtitle_language == SUBTITLE_LANGUAGE_ZH,
            free_access_checked=self.free_access_enabled,
            always_on_top_checked=self.always_on_top_enabled,
            interactions_enabled=not getattr(self, "startup_initializing", False),
            window_visible=self.isVisible(),
            on_hide=self._hide_to_tray,
            on_show=self._show_from_tray,
            on_toggle_chinese_subtitles=self._toggle_chinese_subtitles,
            on_toggle_free_access=self._toggle_free_access,
            on_toggle_always_on_top=self._toggle_always_on_top,
            on_show_history=self.show_history,
            on_show_runtime_log=self.show_runtime_log,
            on_show_settings=self.show_settings,
        )

    def _refresh_tray_menu(self) -> None:
        if hasattr(self, "tray_icon"):
            old_menu = self.tray_icon.contextMenu()
            self.tray_icon.setContextMenu(self._build_menu())
            if old_menu is not None:
                old_menu.deleteLater()

    def _show_context_menu(self, position: QPoint) -> None:
        _ = position
        self._build_menu().exec(QCursor.pos())
        self._sync_native_topmost_state()

    def _handle_tray_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self.toggle_visible()

    def _move_to_default_position(self) -> None:
        screen = QApplication.primaryScreen()
        if screen is None:
            return
        geometry = screen.availableGeometry()
        x = geometry.right() - self.width() - 40
        y = geometry.bottom() - self.height() - 20
        self.move(max(geometry.left(), x), max(geometry.top(), y))

    def _begin_interaction(self, source: str) -> None:
        self.interaction_sequence += 1
        now = time.perf_counter()
        self.active_interaction_id = f"interaction-{self.interaction_sequence}"
        self.active_interaction_started_at = now
        self.active_interaction_last_at = now
        debug_log(
            "Latency",
            "输入事件开始",
            {
                "interaction_id": self.active_interaction_id,
                "source": source,
                "input_chars": len(self.input_edit.text()),
                "worker_busy": self.worker_thread is not None,
            },
        )

    def _log_input_key_event(self, event: object) -> None:
        self._mark_user_activity()
        key_event = event if isinstance(event, QKeyEvent) else None
        debug_log(
            "Input",
            "输入框按键事件",
            {
                "key": int(key_event.key()) if key_event is not None else "",
                "text": key_event.text() if key_event is not None else "",
                "modifiers": str(key_event.modifiers()) if key_event is not None else "",
                "input_chars": len(self.input_edit.text()),
                "worker_busy": self.worker_thread is not None,
            },
        )

    def _log_interaction_stage(self, stage: str, data: dict[str, Any] | None = None) -> None:
        if not self.active_interaction_id or self.active_interaction_started_at is None:
            return
        now = time.perf_counter()
        previous = self.active_interaction_last_at or self.active_interaction_started_at
        self.active_interaction_last_at = now
        payload: dict[str, Any] = {
            "interaction_id": self.active_interaction_id,
            "stage": stage,
            "elapsed_ms": int((now - self.active_interaction_started_at) * 1000),
            "delta_ms": int((now - previous) * 1000),
        }
        if data:
            payload.update(data)
        debug_log("Latency", "交互阶段", payload)

    def _end_interaction(self, outcome: str) -> None:
        self._log_interaction_stage("interaction_finished", {"outcome": outcome})
        self.active_interaction_id = ""
        self.active_interaction_started_at = None
        self.active_interaction_last_at = None
        self._update_reply_history_buttons()
        # 每完成一轮对话（含完整回复）累计一次，驱动自动记忆整理触发
        if outcome == "reply_completed":
            self._record_completed_memory_turn()
            # 说完话：开始气泡无操作自动隐藏倒计时。
            controller = getattr(self, "bubble_auto_hide", None)
            if controller is not None:
                controller.notify_settled()

    def _mark_user_activity(self) -> None:
        self.last_user_activity_at = time.perf_counter()

    @Slot()
    def _handle_return_pressed(self) -> None:
        if getattr(self, "startup_initializing", False):
            return
        if self.worker_thread is not None:
            return
        self._begin_interaction("return_pressed")
        self.send_message("return_pressed")

    @Slot()
    def _handle_send_button_clicked(self) -> None:
        if getattr(self, "startup_initializing", False):
            return
        self._begin_interaction("send_button_clicked")
        self.send_message("send_button_clicked")

    @Slot()
    def _handle_screenshot_button_clicked(self) -> None:
        self._mark_user_activity()
        if getattr(self, "startup_initializing", False):
            return
        if self.worker_thread is not None:
            return
        if not self.screen_observation_enabled:
            show_themed_information(self, "截图已关闭", "请先在设置中开启屏幕观察权限。")
            return

        debug_log("PetWindow", "开始手动框选截图")
        QTimer.singleShot(120, self._show_manual_screenshot_overlay)

    def _show_manual_screenshot_overlay(self) -> None:
        try:
            desktop_pixmap, virtual_geometry = self._capture_virtual_desktop_pixmap()
        except RuntimeError as exc:
            show_themed_warning(self, "截图失败", str(exc))
            debug_log("PetWindow", "手动框选截图启动失败", {"error": str(exc)})
            return

        overlay = ManualScreenshotOverlay(desktop_pixmap, virtual_geometry)
        overlay.selected.connect(self._handle_manual_screenshot_selected)
        overlay.cancelled.connect(self._handle_manual_screenshot_cancelled)
        overlay.destroyed.connect(self._clear_manual_screenshot_overlay_ref)
        self.manual_screenshot_overlay = overlay
        overlay.show()
        overlay.raise_()
        overlay.activateWindow()

    def _capture_virtual_desktop_pixmap(self) -> tuple[QPixmap, QRect]:
        return capture_virtual_desktop_pixmap()

    @Slot(object)
    def _handle_manual_screenshot_selected(self, pixmap: QPixmap) -> None:
        self.show()
        self.raise_()
        try:
            observation = build_screen_observation_from_pixmap(pixmap)
        except RuntimeError as exc:
            show_themed_warning(self, "截图失败", str(exc))
            debug_log("PetWindow", "手动框选截图编码失败", {"error": str(exc)})
            return

        self.pending_manual_screen_observation = observation
        self._update_manual_screenshot_button()
        debug_log(
            "PetWindow",
            "手动框选截图已附加到下一条消息",
            {
                "width": observation.width,
                "height": observation.height,
                "captured_at": observation.captured_at,
                "screen_name": observation.screen_name,
                "image": observation.data_url,
            },
        )

    @Slot()
    def _handle_manual_screenshot_cancelled(self) -> None:
        self.show()
        self.raise_()
        debug_log("PetWindow", "手动框选截图已取消")

    @Slot()
    def _clear_manual_screenshot_overlay_ref(self) -> None:
        self.manual_screenshot_overlay = None

    def _clear_manual_screen_observation(self) -> None:
        if self.pending_manual_screen_observation is None:
            return
        self.pending_manual_screen_observation = None
        self._update_manual_screenshot_button()
        debug_log("PetWindow", "待发送手动截图已清除")

    def _update_manual_screenshot_button(self) -> None:
        attached = self.pending_manual_screen_observation is not None
        self.screenshot_button.setText("")
        icon_path = _SCREENSHOT_ATTACHED_ICON_PATH if attached else _SCREENSHOT_ICON_PATH
        self.screenshot_button.setIcon(QIcon(str(icon_path)))
        self.screenshot_button.setProperty("screenshotAttached", attached)
        self.screenshot_button.style().unpolish(self.screenshot_button)
        self.screenshot_button.style().polish(self.screenshot_button)
        self.screenshot_button.update()

    @Slot()
    def send_message(self, source: str = "direct_call") -> None:
        if getattr(self, "startup_initializing", False):
            return
        text = self.input_edit.text().strip()
        manual_observation = self.pending_manual_screen_observation
        self._mark_user_activity()
        if not self.active_interaction_id:
            self._begin_interaction(source)
        self._log_interaction_stage(
            "send_message_enter",
            {
                "source": source,
                "text": text,
                "has_manual_screenshot": manual_observation is not None,
                "worker_busy": self.worker_thread is not None,
            },
        )
        if (not text and manual_observation is None) or self.worker_thread is not None:
            debug_log(
                "PetWindow",
                "发送消息被忽略",
                {
                    "has_text": bool(text),
                    "has_manual_screenshot": manual_observation is not None,
                    "worker_busy": self.worker_thread is not None,
                },
            )
            self._log_interaction_stage(
                "send_message_ignored",
                {
                    "has_text": bool(text),
                    "has_manual_screenshot": manual_observation is not None,
                    "worker_busy": self.worker_thread is not None,
                },
            )
            self._end_interaction("ignored")
            return
        if manual_observation is not None and not self.screen_observation_enabled:
            show_themed_information(self, "截图已关闭", "屏幕观察权限已关闭，本次截图不会发送。")
            self._clear_manual_screen_observation()
            self._end_interaction("ignored")
            return

        if not text and manual_observation is not None:
            text = MANUAL_SCREENSHOT_DEFAULT_TEXT

        self._set_pending_tool_action(None)
        exit_reply_history_review = getattr(self, "_exit_reply_history_review", None)
        if exit_reply_history_review is not None:
            exit_reply_history_review()
        animator = getattr(self, "input_bar_animator", None)
        if animator is not None:
            animator.play_send_feedback()
        self.input_edit.clear()
        self._log_interaction_stage("input_cleared")
        self._collapse_auto_fit_bubble_height()
        self._show_waiting_reply_placeholder()
        self._log_interaction_stage("placeholder_reply_shown")

        visual_observation_jobs: list[VisualObservationJob] = []
        if manual_observation is not None:
            visual_id = generate_visual_observation_id()
            request_user_message = build_screen_observation_user_message(text, manual_observation)
            recorded_user_text = append_manual_observation_marker(text, manual_observation, visual_id)
            visual_observation_jobs.append(
                VisualObservationJob(
                    id=visual_id,
                    source="manual_screenshot",
                    user_text=text,
                    observation=manual_observation,
                )
            )
        else:
            request_user_message: dict[str, Any] = {"role": "user", "content": text}
            recorded_user_text = text

        request_messages = _add_visual_context_to_messages(
            [*self.messages, request_user_message],
            user_text=text,
            store=getattr(self, "visual_observation_store", None),
            has_current_image=manual_observation is not None,
        )
        # 注入运行时事件上下文：与视觉上下文同样只进 request_messages，不写入 self.messages、不持久化。
        runtime_event_queue = getattr(self, "runtime_event_queue", None)
        if runtime_event_queue is not None:
            request_messages = _add_runtime_event_context_to_messages(
                request_messages,
                runtime_event_queue.drain(),
            )
        request_messages = trim_messages_for_model(request_messages)
        debug_log(
            "PetWindow",
            "用户消息入队",
            {
                "text": text,
                "has_manual_screenshot": manual_observation is not None,
                "history_messages": len(self.messages),
                "request_messages": summarize_messages(request_messages),
            },
        )
        self._log_interaction_stage(
            "request_messages_ready",
            {
                "history_messages": len(self.messages),
                "request_message_count": len(request_messages),
                "has_manual_screenshot": manual_observation is not None,
            },
        )
        self._record_user_message(recorded_user_text)
        self._clear_proactive_screen_context_batch("sent_user_message")
        if manual_observation is not None:
            self.pending_manual_screen_observation = None
            self._update_manual_screenshot_button()
        if visual_observation_jobs:
            self.pending_visual_observation_jobs = [
                *getattr(self, "pending_visual_observation_jobs", []),
                *visual_observation_jobs,
            ]
        self._log_interaction_stage("user_message_recorded")
        self._start_chat_worker(request_messages)

    def _show_waiting_reply_placeholder(self) -> None:
        """显示模型回复等待动效，并阻止自动隐藏在等待期间藏起气泡。"""
        self._set_reply_waiting_ui(True)
        controller = getattr(self, "bubble_auto_hide", None)
        if controller is not None:
            controller.notify_speaking()
        subtitle_controller = getattr(self, "subtitle_controller", None)
        if subtitle_controller is None:
            return
        start_waiting_indicator = getattr(subtitle_controller, "start_waiting_indicator", None)
        if callable(start_waiting_indicator):
            start_waiting_indicator()
            return
        subtitle_controller.cancel_reply_flow("...")

    def _start_chat_worker(self, request_messages: list[dict[str, Any]]) -> None:
        visual_observation_jobs = getattr(self, "pending_visual_observation_jobs", [])
        self.pending_visual_observation_jobs = []
        self._set_busy(True)
        self._log_interaction_stage("ui_busy_enabled")
        debug_log(
            "PetWindow",
            "启动聊天 Worker",
            {
                "message_count": len(request_messages),
                "messages": summarize_messages(request_messages),
            },
        )
        self.worker_thread = QThread(self)
        self.worker = ChatWorker(
            self.agent_runtime,
            request_messages,
            visual_observation_store=getattr(self, "visual_observation_store", None),
            visual_observation_jobs=visual_observation_jobs,
        )
        self.worker.moveToThread(self.worker_thread)
        self.worker_thread.started.connect(self.worker.run)
        self.worker.progress.connect(self._handle_progress_reply)
        self.worker.finished.connect(self._handle_reply)
        self.worker.failed.connect(self._handle_error)
        self.worker.finished.connect(self.worker_thread.quit)
        self.worker.failed.connect(self.worker_thread.quit)
        self.worker_thread.finished.connect(self._cleanup_worker)
        self.worker_thread.start()
        self._log_interaction_stage("chat_worker_started")

    @Slot(object)
    def _handle_progress_reply(self, progress: AgentProgress) -> None:
        reply = progress.reply
        if not reply.text.strip():
            return
        self._log_interaction_stage(
            "agent_progress_received",
            {
                "stage": progress.stage,
                "segments": len(reply.segments),
                "metadata": progress.metadata,
            },
        )
        debug_log(
            "PetWindow",
            "收到 Agent 中间回复",
            {
                "stage": progress.stage,
                "segments": len(reply.segments),
                "metadata": progress.metadata,
            },
        )
        self.messages.append({"role": "assistant", "content": reply.text})
        self._record_assistant_reply_history(reply)

    @Slot(object)
    def _handle_reply(self, result: AgentResult) -> None:
        self._log_interaction_stage(
            "agent_result_received",
            {
                "segments": len(result.reply.segments),
                "actions": [action.type for action in result.actions],
            },
        )
        debug_log(
            "PetWindow",
            "收到 Agent 回复",
            {
                "segments": len(result.reply.segments),
                "actions": [action.type for action in result.actions],
            },
        )
        if self._queue_screen_observation_followup(result):
            self._log_interaction_stage("screen_observation_followup_queued")
            return
        reply = result.reply
        self.messages.append({"role": "assistant", "content": reply.text})
        self._record_assistant_reply_history(reply, _debug=result._debug)
        self._log_interaction_stage("assistant_message_recorded")
        self._show_reply_segments(reply.segments)
        self._apply_pending_action_from_result(result)

    def _queue_screen_observation_followup(self, result: AgentResult) -> bool:
        if not any(action.type == SCREEN_OBSERVATION_REQUEST_ACTION for action in result.actions):
            return False
        if (
            not self.screen_observation_enabled
            or not self.model_vision_enabled
            or not self.autonomous_screen_observation_enabled
        ):
            self._log_interaction_stage(
                "screen_observation_disabled",
                {
                    "screen_observation_enabled": self.screen_observation_enabled,
                    "model_vision_enabled": self.model_vision_enabled,
                    "autonomous_screen_observation_enabled": self.autonomous_screen_observation_enabled,
                },
            )
            debug_log(
                "PetWindow",
                "屏幕观察请求被禁用",
                {
                    "screen_observation_enabled": self.screen_observation_enabled,
                    "model_vision_enabled": self.model_vision_enabled,
                    "autonomous_screen_observation_enabled": self.autonomous_screen_observation_enabled,
                },
            )
            self._consume_agent_result(_build_screen_observation_disabled_result())
            return True
        user_message_index = _last_user_message_index(self.messages)
        if user_message_index is None:
            self._log_interaction_stage("screen_observation_missing_user_message")
            debug_log("PetWindow", "屏幕观察缺少可关联用户消息")
            self._consume_agent_result(_build_screen_observation_failed_result("缺少可关联的用户消息。"))
            return True

        text = str(self.messages[user_message_index].get("content", ""))
        self.screen_observation_followup_in_progress = True
        try:
            observation = capture_screen_observation(self)
        except RuntimeError as exc:
            self.screen_observation_followup_in_progress = False
            self._log_interaction_stage("screen_observation_failed", {"error": str(exc)})
            debug_log("PetWindow", "屏幕观察失败", {"error": str(exc)})
            self._consume_agent_result(_build_screen_observation_failed_result(str(exc)))
            return True

        visual_id = generate_visual_observation_id()
        observed_message = build_screen_observation_user_message(text, observation)
        self.messages[user_message_index] = {
            "role": "user",
            "content": append_observation_marker(text, observation, visual_id),
        }
        self._record_history("system", append_observation_marker("", observation, visual_id).strip())
        self.pending_visual_observation_jobs = [
            *getattr(self, "pending_visual_observation_jobs", []),
            VisualObservationJob(
                id=visual_id,
                source="autonomous_screen",
                user_text=text,
                observation=observation,
            ),
        ]
        # 截图消息包含 base64，必须作为本次 follow-up 的最后一条消息保留。
        # 中间进度回复已经展示给用户，不再放入这次入模上下文，避免字符裁剪丢掉截图。
        self.pending_screen_observation_messages = trim_messages_for_model(
            [*self.messages[:user_message_index], observed_message]
        )
        self.screen_observation_followup_in_progress = False
        debug_log(
            "PetWindow",
            "屏幕观察 follow-up 已排队",
            {
                "original_text": text,
                "width": observation.width,
                "height": observation.height,
                "captured_at": observation.captured_at,
                "screen_name": observation.screen_name,
                "image": observation.data_url,
                "message_count": len(self.pending_screen_observation_messages),
            },
        )
        self._log_interaction_stage(
            "screen_observation_captured",
            {
                "width": observation.width,
                "height": observation.height,
                "screen_name": observation.screen_name,
            },
        )
        return True

    def _queue_event_screen_observation_followup(
        self,
        result: AgentResult,
        event: AgentEvent | None,
        reminder_id: str | None,
    ) -> bool:
        screen_action = _first_screen_observation_request(result)
        if screen_action is None:
            return False
        if event is None or event.type != "proactive_check":
            self._consume_agent_result(_build_screen_observation_failed_result("缺少可关联的主动事件。"))
            return True
        if not self._proactive_screen_context_allowed():
            self._log_interaction_stage(
                "event_screen_observation_disabled",
                {
                    "proactive_screen_context_enabled": (
                        self.proactive_care_settings.screen_context_enabled
                    ),
                },
            )
            self._consume_agent_result(_build_screen_observation_disabled_result())
            return True
        if isinstance(event.payload.get("screen_context"), dict) or isinstance(
            event.payload.get("screen_contexts"),
            list,
        ):
            self._consume_agent_result(_build_screen_observation_failed_result("本轮主动事件已经包含屏幕截图。"))
            return True

        reason = str(screen_action.payload.get("reason", "")).strip()
        self.screen_observation_followup_in_progress = True
        try:
            observation = capture_screen_observation(self)
        except RuntimeError as exc:
            self.screen_observation_followup_in_progress = False
            self._log_interaction_stage("event_screen_observation_failed", {"error": str(exc)})
            debug_log("PetWindow", "主动事件屏幕观察失败", {"error": str(exc)})
            self._consume_agent_result(_build_screen_observation_failed_result(str(exc)))
            return True

        payload = dict(event.payload)
        payload["screen_context"] = {
            "data_url": observation.data_url,
            "width": observation.width,
            "height": observation.height,
            "captured_at": observation.captured_at,
            "screen_name": observation.screen_name,
        }
        payload["screen_observation_requested_by_model"] = True
        payload["screen_observation_reason"] = reason
        self.pending_screen_observation_event = AgentEvent(type=event.type, payload=payload)
        self.pending_screen_observation_event_reminder_id = reminder_id
        self.screen_observation_followup_in_progress = False
        visual_id = generate_visual_observation_id()
        self.pending_event_visual_observation_jobs = [
            *getattr(self, "pending_event_visual_observation_jobs", []),
            VisualObservationJob(
                id=visual_id,
                source="autonomous_screen",
                user_text=reason,
                observation=observation,
            ),
        ]
        self._record_history("system", append_observation_marker("", observation, visual_id).strip())
        debug_log(
            "PetWindow",
            "主动事件屏幕观察 follow-up 已排队",
            {
                "event_type": event.type,
                "reason": reason,
                "width": observation.width,
                "height": observation.height,
                "captured_at": observation.captured_at,
                "screen_name": observation.screen_name,
                "image": observation.data_url,
            },
        )
        self._log_interaction_stage(
            "event_screen_observation_captured",
            {
                "width": observation.width,
                "height": observation.height,
                "screen_name": observation.screen_name,
            },
        )
        return True

    def _record_user_message(self, text: str) -> None:
        self.messages.append({"role": "user", "content": text})
        self._record_history("user", text)

    @Slot()
    def confirm_pending_action(self) -> None:
        if self.pending_tool_action is None or self.worker_thread is not None:
            return
        self._mark_user_activity()
        self._begin_interaction("confirm_action_clicked")
        action = self.pending_tool_action
        self._log_interaction_stage("confirm_action", action.to_dict())
        self._set_pending_tool_action(None)
        self._clear_queued_reply_segments_for_action_resolution()
        self._run_action_worker(confirmed_action=action)

    @Slot()
    def cancel_pending_action(self) -> None:
        if self.pending_tool_action is None or self.worker_thread is not None:
            return
        self._mark_user_activity()
        self._begin_interaction("cancel_action_clicked")
        action = self.pending_tool_action
        self._log_interaction_stage("cancel_action", action.to_dict())
        self._set_pending_tool_action(None)
        self._clear_queued_reply_segments_for_action_resolution()
        self._run_action_worker(cancelled_action=action)

    def _run_action_worker(
        self,
        confirmed_action: PendingToolAction | None = None,
        cancelled_action: PendingToolAction | None = None,
    ) -> None:
        self._set_busy(True)
        self._log_interaction_stage(
            "action_worker_start",
            {
                "confirmed": confirmed_action.tool_name if confirmed_action is not None else "",
                "cancelled": cancelled_action.tool_name if cancelled_action is not None else "",
            },
        )
        self.worker_thread = QThread(self)
        self.worker = ChatWorker(
            self.agent_runtime,
            confirmed_action=confirmed_action,
            cancelled_action=cancelled_action,
        )
        self.worker.moveToThread(self.worker_thread)
        self.worker_thread.started.connect(self.worker.run)
        self.worker.progress.connect(self._handle_progress_reply)
        self.worker.finished.connect(self._handle_action_reply)
        self.worker.failed.connect(self._handle_error)
        self.worker.finished.connect(self.worker_thread.quit)
        self.worker.failed.connect(self.worker_thread.quit)
        self.worker_thread.finished.connect(self._cleanup_worker)
        self.worker_thread.start()
        self._log_interaction_stage("action_worker_started")

    @Slot(object)
    def _handle_action_reply(self, result: AgentResult) -> None:
        self._log_interaction_stage(
            "action_result_received",
            {
                "segments": len(result.reply.segments),
                "actions": [action.type for action in result.actions],
            },
        )
        self._consume_agent_result(result)

    def _consume_agent_result(self, result: AgentResult, record_history: bool = True) -> None:
        reply = result.reply
        self._log_interaction_stage(
            "consume_agent_result",
            {
                "segments": len(reply.segments),
                "record_history": record_history,
            },
        )
        if record_history:
            self.messages.append({"role": "assistant", "content": reply.text})
            self._record_assistant_reply_history(reply, _debug=result._debug)
        self._show_reply_segments(reply.segments)
        self._apply_pending_action_from_result(result)

    def _apply_pending_action_from_result(self, result: AgentResult) -> None:
        for action in result.actions:
            if action.type != "pending_action":
                continue
            try:
                self._set_pending_tool_action(PendingToolAction.from_dict(action.payload))
            except ValueError as exc:
                print(f"[Tool] 待确认动作无效：{exc}")
            return
        self._set_pending_tool_action(None)

    def _set_pending_tool_action(self, action: PendingToolAction | None) -> None:
        self.pending_tool_action = action
        has_action = action is not None
        self.tool_confirmation_panel.set_action(action)
        if hasattr(self, "input_bar_animator"):
            self.input_bar_animator.sync()
        panel_state = self.tool_confirmation_panel.state_snapshot()
        debug_log(
            "PetWindow",
            "待确认动作 UI 状态已更新",
            {
                "has_action": has_action,
                "tool_name": action.tool_name if action is not None else "",
                **panel_state,
            },
        )

    def _clear_queued_reply_segments_for_action_resolution(self) -> None:
        self.subtitle_controller.clear_queued_reply_segments_for_action_resolution()

    @Slot()
    def _check_proactive_care(self) -> None:
        if getattr(self, "startup_initializing", False):
            return
        if not self._can_run_proactive_care():
            return

        now = time.perf_counter()
        if self._should_capture_proactive_screen_context(now):
            self._capture_proactive_screen_context(now)
        if not self._should_send_proactive_care_batch(now):
            return

        event = self._build_proactive_care_event(now)
        self.pending_event_visual_observation_jobs = [
            *getattr(self, "pending_event_visual_observation_jobs", []),
            *_build_proactive_visual_observation_jobs(event),
        ]
        self.last_proactive_care_at = now
        self._record_history("system", PROACTIVE_SCREEN_CONTEXT_HISTORY_MARKER)
        self._clear_proactive_screen_context_batch("sent")
        self._run_event_worker(event)

    def _can_run_proactive_care(self) -> bool:
        if not self._proactive_screen_context_allowed():
            return False
        if (
            self.worker_thread is not None
            or self.active_reminder_id is not None
            or self.active_event_type
            or self.pending_tool_action is not None
            or self.pending_screen_observation_messages is not None
            or self.screen_observation_followup_in_progress
            or self.active_interaction_id
        ):
            return False
        if self.input_edit.text().strip() or self.speech_timer.isActive():
            return False
        subtitle_controller = getattr(self, "subtitle_controller", None)
        if subtitle_controller is not None and subtitle_controller.current_segment_in_progress():
            return False
        if subtitle_controller is None and getattr(self, "current_segment_sequence_id", None) is not None and (
            not getattr(self, "current_segment_speech_done", True)
            or not getattr(self, "current_segment_tts_done", True)
        ):
            return False
        return True

    def _should_capture_proactive_screen_context(self, now: float) -> bool:
        check_interval_seconds = self.proactive_care_settings.check_interval_minutes * 60
        seconds_since_pet_interaction = now - self.last_user_activity_at
        if (
            seconds_since_pet_interaction + PROACTIVE_TIMER_DUE_GRACE_SECONDS
            < check_interval_seconds
        ):
            return False
        if self.last_proactive_screen_context_at is None:
            return True
        return (
            now - self.last_proactive_screen_context_at + PROACTIVE_TIMER_DUE_GRACE_SECONDS
            >= check_interval_seconds
        )

    def _capture_proactive_screen_context(self, now: float) -> None:
        self.last_proactive_screen_context_at = now
        try:
            observation = capture_screen_observation(self)
        except RuntimeError as exc:
            debug_log("ProactiveCare", "主动屏幕上下文获取失败", {"error": str(exc)})
            return

        context = {
            "data_url": observation.data_url,
            "width": observation.width,
            "height": observation.height,
            "captured_at": observation.captured_at,
            "screen_name": observation.screen_name,
        }
        if not self.proactive_screen_contexts:
            self.proactive_screen_context_batch_started_at = now
        self.proactive_screen_contexts.append(context)
        batch_limit = self.proactive_care_settings.normalized().screen_context_batch_limit
        while len(self.proactive_screen_contexts) > batch_limit:
            self.proactive_screen_contexts.pop(0)
            self.proactive_screen_context_dropped_count += 1
        debug_log(
            "ProactiveCare",
            "主动屏幕上下文已缓存",
            {
                "width": observation.width,
                "height": observation.height,
                "captured_at": observation.captured_at,
                "screen_name": observation.screen_name,
                "batch_count": len(self.proactive_screen_contexts),
                "dropped_count": self.proactive_screen_context_dropped_count,
                "image": observation.data_url,
            },
        )

    def _should_send_proactive_care_batch(self, now: float) -> bool:
        if not self.proactive_screen_contexts:
            return False
        if self.proactive_screen_context_batch_started_at is None:
            return False
        return (
            now - self.proactive_screen_context_batch_started_at
            >= self.proactive_care_settings.cooldown_minutes * 60
        )

    def _build_proactive_care_event(self, now: float | None = None) -> AgentEvent:
        now = time.perf_counter() if now is None else now
        screen_contexts = [dict(context) for context in self.proactive_screen_contexts]
        payload: dict[str, Any] = {
            "triggered_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "seconds_since_pet_interaction": int(now - self.last_user_activity_at),
            "check_interval_minutes": self.proactive_care_settings.check_interval_minutes,
            "cooldown_minutes": self.proactive_care_settings.cooldown_minutes,
            "screen_context_allowed": self._proactive_screen_context_allowed(),
            "screen_context_count": len(screen_contexts),
            "screen_context_dropped_count": self.proactive_screen_context_dropped_count,
        }
        recent_conversation = _build_proactive_recent_conversation_for_window(self)
        if recent_conversation:
            payload["recent_conversation"] = recent_conversation
            payload["recent_conversation_summary_hint"] = (
                PROACTIVE_RECENT_CONVERSATION_SUMMARY_HINT
            )
        if screen_contexts:
            payload["screen_contexts"] = screen_contexts
            payload["screen_context_window_started_at"] = screen_contexts[0].get("captured_at", "")
            payload["screen_context_window_ended_at"] = screen_contexts[-1].get("captured_at", "")
            debug_log(
                "ProactiveCare",
                "主动屏幕上下文批次已附加",
                {
                    "batch_count": len(screen_contexts),
                    "dropped_count": self.proactive_screen_context_dropped_count,
                    "started_at": payload["screen_context_window_started_at"],
                    "ended_at": payload["screen_context_window_ended_at"],
                },
            )
        return AgentEvent(type="proactive_check", payload=payload)

    def _proactive_screen_context_allowed(self) -> bool:
        return self.proactive_care_settings.allows_screen_context()

    def _sync_proactive_care_timer(self) -> None:
        if self._proactive_screen_context_allowed():
            if not self.proactive_care_timer.isActive():
                self.proactive_care_timer.start()
        else:
            self.proactive_care_timer.stop()
            self._clear_proactive_screen_context_batch("disabled")

    def _clear_proactive_screen_context_batch(self, reason: str) -> None:
        had_batch = bool(self.proactive_screen_contexts)
        self.proactive_screen_contexts = []
        self.proactive_screen_context_batch_started_at = None
        self.last_proactive_screen_context_at = None
        self.proactive_screen_context_dropped_count = 0
        if had_batch:
            debug_log("ProactiveCare", "主动屏幕上下文批次已清空", {"reason": reason})

    def _run_event_worker(self, event: AgentEvent, reminder_id: str | None = None) -> None:
        if getattr(self, "startup_initializing", False):
            return
        if self.worker_thread is not None or self.active_reminder_id is not None or self.active_event_type:
            return

        self._begin_interaction(event.type)
        self._log_interaction_stage(
            "event_worker_start",
            {
                "reminder_id": reminder_id,
                "event": {"type": event.type, "payload": event.payload},
            },
        )
        self.active_event = event
        self.active_event_type = event.type
        self.active_reminder_id = reminder_id
        self.active_reminder_text = str(event.payload.get("text", ""))
        self._set_busy(True)
        self.worker_thread = QThread(self)
        self.worker = EventWorker(
            self.agent_runtime,
            event,
        )
        self.worker.visual_observation_store = getattr(self, "visual_observation_store", None)
        self.worker.visual_observation_jobs = getattr(self, "pending_event_visual_observation_jobs", [])
        self.pending_event_visual_observation_jobs = []
        self.worker.moveToThread(self.worker_thread)
        self.worker_thread.started.connect(self.worker.run)
        self.worker.progress.connect(self._handle_progress_reply)
        self.worker.finished.connect(self._handle_event_reply)
        self.worker.failed.connect(self._handle_event_error)
        self.worker.finished.connect(self.worker_thread.quit)
        self.worker.failed.connect(self.worker_thread.quit)
        self.worker_thread.finished.connect(self._cleanup_worker)
        self.worker_thread.start()
        self._log_interaction_stage("event_worker_started")

    @Slot(object)
    def _handle_event_reply(self, result: AgentResult) -> None:
        self._log_interaction_stage(
            "event_result_received",
            {"event_type": self.active_event_type, "segments": len(result.reply.segments)},
        )
        event = self.active_event
        reminder_id = self.active_reminder_id
        if self._queue_event_screen_observation_followup(result, event, reminder_id):
            self._clear_active_event()
            return
        self._clear_active_event()
        if not result.reply.text.strip() and not result.reply.translation.strip() and not result.actions:
            self._log_interaction_stage("event_silent", {"event_type": event.type if event else ""})
            return
        self._consume_agent_result(result)
        if reminder_id is not None:
            self._mark_reminder_completed(reminder_id)

    @Slot(str)
    def _handle_event_error(self, message: str) -> None:
        event_type = self.active_event_type
        self._log_interaction_stage("event_error", {"event_type": event_type, "message": message})
        reminder_id = self.active_reminder_id
        reminder_text = self.active_reminder_text
        self._clear_active_event()
        print(f"[Event] 主动事件生成失败：{message}")
        if event_type == "reminder_due":
            result = AgentResult(
                reply=ChatReply(
                    [
                        ChatSegment(
                            text=f"時間だよ。{reminder_text}",
                            tone="请求",
                            translation=f"到时间了：{reminder_text}",
                            portrait="伸手命令",
                        )
                    ]
                )
            )
            self._consume_agent_result(result)
        elif event_type == "proactive_check":
            result = AgentResult(
                reply=ChatReply(
                    [
                        ChatSegment(
                            text="少し休んでもいいんじゃない？無理しすぎないでよね。",
                            tone="请求",
                            translation="稍微休息一下也可以吧？别太勉强自己。",
                            portrait="伸手命令",
                        )
                    ]
                )
            )
            self._consume_agent_result(result)
        if reminder_id is not None:
            self._mark_reminder_completed(reminder_id)

    def _clear_active_event(self) -> None:
        self.active_event = None
        self.active_event_type = ""
        self.active_reminder_id = None
        self.active_reminder_text = ""

    def _mark_reminder_completed(self, reminder_id: str) -> None:
        try:
            self.reminder_store.mark_completed(reminder_id)
        except ValueError as exc:
            print(f"[Reminder] 标记完成失败：{exc}")

    @Slot(str)
    def _handle_error(self, message: str) -> None:
        self._log_interaction_stage("worker_error", {"message": message})
        if self.messages and self.messages[-1]["role"] == "user":
            self.messages.pop()
        self._record_history("error", message)
        self._collapse_auto_fit_bubble_height()
        self.subtitle_controller.cancel_reply_flow(
            "……通信に失敗した。設定を確認して。", transition=True
        )
        show_themed_warning(self, "请求失败", message)
        self._end_interaction("error")

    @Slot()
    def _cleanup_worker(self) -> None:
        self._log_interaction_stage(
            "cleanup_worker_enter",
            {
                "has_pending_screen_observation": self.pending_screen_observation_messages is not None,
                "has_pending_screen_observation_event": self.pending_screen_observation_event is not None,
                "screen_observation_followup_in_progress": self.screen_observation_followup_in_progress,
            },
        )
        if self.worker is not None:
            self.worker.deleteLater()
        if self.worker_thread is not None:
            self.worker_thread.deleteLater()
        self.worker = None
        self.worker_thread = None
        if self.screen_observation_followup_in_progress:
            self._log_interaction_stage("screen_observation_cleanup_deferred")
            QTimer.singleShot(0, self._cleanup_worker)
            return
        if self.pending_screen_observation_messages is not None:
            request_messages = self.pending_screen_observation_messages
            self.pending_screen_observation_messages = None
            self._log_interaction_stage(
                "screen_observation_worker_restart",
                {"message_count": len(request_messages)},
            )
            self._start_chat_worker(request_messages)
            return
        if self.pending_screen_observation_event is not None:
            event = self.pending_screen_observation_event
            reminder_id = self.pending_screen_observation_event_reminder_id
            self.pending_screen_observation_event = None
            self.pending_screen_observation_event_reminder_id = None
            self._log_interaction_stage(
                "event_screen_observation_worker_restart",
                {"event_type": event.type},
            )
            self._run_event_worker(event, reminder_id)
            return
        self._set_busy(False)
        self._log_interaction_stage("ui_busy_disabled")
        self._maybe_start_auto_memory_curation()

    def _record_completed_memory_turn(self) -> None:
        if not self.memory_curation_settings.enabled:
            return
        pending_turns = self.memory_curation_state.increment_pending_turns()
        debug_log("Memory", "自动记忆轮次已累计", {"pending_turns": pending_turns})
        if pending_turns >= self.memory_curation_settings.trigger_turns:
            QTimer.singleShot(0, self._maybe_start_auto_memory_curation)

    def _maybe_start_auto_memory_curation(self) -> None:
        if getattr(self, "startup_initializing", False):
            return
        if not self.memory_curation_settings.enabled:
            return
        if self.memory_curation_state.pending_turns() < self.memory_curation_settings.trigger_turns:
            return
        if not self._memory_curation_can_start():
            return
        entries = self.memory_curation_state.unprocessed_entries(self.history_store.load())
        if not entries:
            return
        self._start_memory_curation(
            entries,
            mode="auto",
            target_history_count=len(self.history_store.load()),
            consumed_turns=self.memory_curation_state.pending_turns(),
        )

    def _maybe_start_memory_backfill(self) -> None:
        if getattr(self, "startup_initializing", False):
            return
        if not self.memory_curation_settings.enabled:
            return
        if self.pending_history_clear_after_curation:
            return
        state = self.memory_curation_state.snapshot()
        if state.get("backfill_completed"):
            return
        if not self._memory_curation_can_start():
            QTimer.singleShot(1000, self._maybe_start_memory_backfill)
            return
        entries = self.history_store.load()
        if not entries:
            self.memory_curation_state.mark_processed(0, backfill_completed=True)
            return
        limited_entries = entries[-self.memory_curation_settings.backfill_limit :]
        self._start_memory_curation(
            limited_entries,
            mode="backfill",
            target_history_count=len(entries),
            consumed_turns=0,
        )

    def _memory_curation_can_start(self) -> bool:
        return (
            self.worker_thread is None
            and self.memory_curation_thread is None
            and self.pending_tool_action is None
            and self.pending_screen_observation_messages is None
            and self.pending_screen_observation_event is None
            and not self.screen_observation_followup_in_progress
        )

    def _start_memory_curation(
        self,
        entries: list[ChatHistoryEntry],
        *,
        mode: str,
        target_history_count: int,
        consumed_turns: int,
    ) -> None:
        if not entries or self.memory_curation_thread is not None:
            return
        debug_log(
            "Memory",
            "启动记忆整理",
            {
                "mode": mode,
                "entry_count": len(entries),
                "target_history_count": target_history_count,
                "consumed_turns": consumed_turns,
            },
        )
        self.memory_curation_mode = mode
        self.memory_curation_target_history_count = target_history_count
        self.memory_curation_consumed_turns = consumed_turns
        self.memory_curation_thread = QThread(self)
        self.memory_curation_worker = MemoryCurationWorker(self.memory_curator, entries)
        self.memory_curation_worker.moveToThread(self.memory_curation_thread)
        self.memory_curation_thread.started.connect(self.memory_curation_worker.run)
        self.memory_curation_worker.finished.connect(self._handle_memory_curation_finished)
        self.memory_curation_worker.failed.connect(self._handle_memory_curation_failed)
        self.memory_curation_worker.finished.connect(self.memory_curation_thread.quit)
        self.memory_curation_worker.failed.connect(self.memory_curation_thread.quit)
        self.memory_curation_thread.finished.connect(self._cleanup_memory_curation_worker)
        self.memory_curation_thread.start()

    @Slot(object)
    def _handle_memory_curation_finished(self, result: MemoryCurationResult) -> None:
        mode = self.memory_curation_mode
        debug_log(
            "Memory",
            "记忆整理完成",
            {
                "mode": mode,
                "result": result,
                "target_history_count": self.memory_curation_target_history_count,
                "consumed_turns": self.memory_curation_consumed_turns,
            },
        )
        if mode == "history_clear":
            if result.processed_entries > 0 and result.returned == 0:
                show_themed_warning(
                    self,
                    "整理失败",
                    "记忆整理没有写入任何结果，已保留聊天历史。请检查日志后再重试。",
                )
                return
            try:
                self.history_store.clear()
                self.memory_curation_state.mark_history_cleared()
            except OSError as exc:
                show_themed_warning(self, "清空失败", f"记忆已整理，但清空历史失败：{exc}")
            else:
                if self.history_window is not None:
                    self.history_window.refresh()
                show_themed_information(self, "整理完成", result.summary())
            return

        self.memory_curation_state.mark_processed(
            self.memory_curation_target_history_count,
            consumed_turns=self.memory_curation_consumed_turns,
            backfill_completed=True if mode == "backfill" else None,
        )

    @Slot(str)
    def _handle_memory_curation_failed(self, message: str) -> None:
        debug_log(
            "Memory",
            "记忆整理失败",
            {
                "mode": self.memory_curation_mode,
                "error": message,
            },
        )
        if self.memory_curation_mode == "history_clear":
            show_themed_warning(self, "整理失败", f"历史没有清空，原因：{message}")

    @Slot()
    def _cleanup_memory_curation_worker(self) -> None:
        if self.memory_curation_worker is not None:
            self.memory_curation_worker.deleteLater()
        if self.memory_curation_thread is not None:
            self.memory_curation_thread.deleteLater()
        self.memory_curation_worker = None
        self.memory_curation_thread = None
        self.memory_curation_mode = ""
        self.memory_curation_target_history_count = 0
        self.memory_curation_consumed_turns = 0
        if self.pending_history_clear_after_curation:
            self.pending_history_clear_after_curation = False
            if self._start_pending_history_clear_after_curation():
                return
        if self.history_window is not None:
            self.history_window.set_memory_save_busy(False)
        QTimer.singleShot(0, self._maybe_start_auto_memory_curation)

    def _start_pending_history_clear_after_curation(self) -> bool:
        if not self._memory_curation_can_start():
            return False
        entries = self.history_store.load()
        if not entries:
            if self.history_window is not None:
                self.history_window.refresh()
            return False
        if not self._memory_store_ready_for_history_clear():
            self._show_memory_not_ready_for_history_clear()
            return False
        self._reset_memory_curation_cache_for_history_clear()
        self._start_memory_curation(
            entries,
            mode="history_clear",
            target_history_count=len(entries),
            consumed_turns=self.memory_curation_state.pending_turns(),
        )
        return self.memory_curation_thread is not None

    @Slot(object)
    def apply_deferred_services(self, services: "DeferredStartupServices") -> None:
        """后台启动服务就绪后注入同一个真实主窗口。"""

        self._move_tts_provider_to_ui_thread(services.tts_provider)
        if self.mcp_tool_provider is not None and self.mcp_tool_provider is not services.mcp_tool_provider:
            self.mcp_tool_provider.close()
        if self.plugin_manager is not services.plugin_manager:
            self.plugin_manager.shutdown_all()

        self._disconnect_tts_error_signal(self.tts_provider)
        self._retire_tts_provider(self.tts_provider)
        self.tts_provider = services.tts_provider
        self.voice_playback_controller.set_provider(services.tts_provider)
        self._connect_tts_error_signal(services.tts_provider)
        self._warm_up_tts_playback(services.tts_provider)
        self._start_tts_ready_warmup(services.tts_provider)
        self.tool_registry = services.tool_registry
        self.free_access_enabled = self.tool_registry.free_access_enabled
        self.agent_runtime.tools = services.tool_registry
        self.agent_runtime.set_prompt_patches(services.plugin_manager.prompt_patches)
        self.mcp_tool_provider = services.mcp_tool_provider
        self.plugin_manager = services.plugin_manager
        self._sync_plugin_chat_ui_widgets()
        self.mcp_settings = services.mcp_settings

        self.startup_initializing = False
        self._emit_app_started_event()
        self.input_edit.setPlaceholderText(self._normal_input_placeholder_text())
        self._collapse_auto_fit_bubble_height()
        self.subtitle_controller.cancel_reply_flow(self.character_profile.initial_message)
        if self.memory_status_message_active:
            QTimer.singleShot(
                MEMORY_STATUS_STARTUP_DELAY_MS,
                self._show_pending_memory_status_after_startup,
            )
        self._set_busy(False)
        self.reminder_timer.start()
        self._sync_proactive_care_timer()
        QTimer.singleShot(0, self._maybe_start_memory_backfill)
        if hasattr(self, "tray_icon"):
            self.tray_icon.setContextMenu(self._build_menu())
        debug_log(
            "Startup",
            "后台启动服务已注入窗口",
            {
                "tool_count": len(self.tool_registry.all()),
                "mcp_enabled": self.mcp_tool_provider is not None,
                "tts_provider": type(self.tts_provider).__name__,
                "error_count": len(services.errors),
            },
        )
        for error in services.errors:
            print(f"[Startup] {error}")
            if error.startswith("TTS"):
                self._show_tts_error(error)

    @Slot(str)
    def handle_deferred_startup_failed(self, error: str) -> None:
        self.startup_initializing = False
        self.input_edit.setPlaceholderText(self._normal_input_placeholder_text())
        self._collapse_auto_fit_bubble_height()
        self.subtitle_controller.cancel_reply_flow(f"初始化失败：{error}")
        self._set_busy(False)
        if hasattr(self, "tray_icon"):
            self.tray_icon.setContextMenu(self._build_menu())
        debug_log("Startup", "后台启动服务失败", {"error": error})
        print(f"[Startup] 后台初始化失败：{error}")

    def _sync_plugin_chat_ui_widgets(self) -> None:
        layout = self.input_bar.layout() if hasattr(self, "input_bar") else None
        if layout is None:
            return
        for widget in getattr(self, "plugin_chat_ui_widget_instances", []):
            layout.removeWidget(widget)
            widget.setParent(None)
            widget.deleteLater()
        self.plugin_chat_ui_widget_instances = []

        contributions = getattr(self.plugin_manager, "chat_ui_widgets", [])
        for index, contribution in enumerate(sorted(contributions, key=lambda item: item.order)):
            try:
                widget = contribution.build(self.input_bar)
            except Exception as exc:
                widget = QLabel(f"{contribution.widget_id} 加载失败：{exc}", self.input_bar)
                widget.setObjectName("pluginChatWidgetError")
                widget.setToolTip(str(exc))
            if not isinstance(widget, QWidget):
                continue
            layout.insertWidget(1 + index, widget)
            self.plugin_chat_ui_widget_instances.append(widget)

    def _move_tts_provider_to_ui_thread(self, provider: TTSProvider) -> None:
        if not isinstance(provider, QObject):
            return
        application = QApplication.instance()
        if application is None:
            return
        if provider.thread() == application.thread():
            return
        provider.moveToThread(application.thread())

    def _connect_tts_error_signal(self, provider: TTSProvider) -> None:
        error_signal = getattr(provider, "error_occurred", None)
        connect = getattr(error_signal, "connect", None)
        if not callable(connect):
            return
        try:
            connect(self._show_tts_error)
        except (TypeError, RuntimeError) as exc:
            debug_log("TTS", "连接 TTS 错误提示信号失败", {"error": str(exc)})

    def _disconnect_tts_error_signal(self, provider: TTSProvider) -> None:
        error_signal = getattr(provider, "error_occurred", None)
        disconnect = getattr(error_signal, "disconnect", None)
        if not callable(disconnect):
            return
        try:
            disconnect(self._show_tts_error)
        except (TypeError, RuntimeError):
            pass

    def _warm_up_current_tts_playback(self) -> None:
        self._warm_up_tts_playback(self.tts_provider)

    def _warm_up_tts_playback(self, provider: TTSProvider) -> None:
        warm_up = getattr(provider, "warm_up_playback", None)
        if not callable(warm_up):
            return
        try:
            warm_up()
        except Exception as exc:  # noqa: BLE001
            debug_log(
                "TTS",
                "播放器预热请求失败",
                {
                    "provider": type(provider).__name__,
                    "error": str(exc),
                },
            )

    def _start_current_tts_ready_warmup(self) -> None:
        self._start_tts_ready_warmup(self.tts_provider)

    def _start_tts_ready_warmup(self, provider: TTSProvider) -> None:
        if isinstance(provider, NullTTSProvider):
            debug_log("TTS", "TTS 已关闭，跳过服务预热")
            return
        ensure_ready = getattr(provider, "ensure_ready", None)
        if not callable(ensure_ready):
            return
        if self.tts_ready_warmup_thread is not None:
            debug_log("TTS", "TTS 服务预热已在进行，跳过重复请求")
            return

        thread = QThread(self)
        worker = TTSReadyWarmupWorker(provider)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.failed.connect(self._handle_tts_ready_warmup_failed)
        worker.finished.connect(thread.quit)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(self._cleanup_tts_ready_warmup_worker)
        self.tts_ready_warmup_thread = thread
        self.tts_ready_warmup_worker = worker
        thread.start()

    @Slot(str)
    def _handle_tts_ready_warmup_failed(self, message: str) -> None:
        self._show_tts_error(message)

    @Slot()
    def _cleanup_tts_ready_warmup_worker(self) -> None:
        self.tts_ready_warmup_thread = None
        self.tts_ready_warmup_worker = None

    def _apply_startup_initializing_state(self) -> None:
        self.input_edit.setPlaceholderText(STARTUP_INITIALIZING_TEXT)
        self._set_busy(True)
        if hasattr(self, "tray_icon"):
            self.tray_icon.setContextMenu(self._build_menu())

    def _set_busy(self, busy: bool) -> None:
        startup_initializing = getattr(self, "startup_initializing", False)
        controls_enabled = not busy and not startup_initializing
        self.input_edit.setEnabled(not startup_initializing)
        self.screenshot_button.setEnabled(controls_enabled)
        self.send_button.setEnabled(controls_enabled)
        tool_confirmation_panel = getattr(self, "tool_confirmation_panel", None)
        if tool_confirmation_panel is not None:
            tool_confirmation_panel.set_busy(busy or startup_initializing)
        else:
            self.confirm_action_button.setEnabled(controls_enabled)
            self.cancel_action_button.setEnabled(controls_enabled)
        if startup_initializing:
            self.send_button.setText("初始化")
        else:
            self.send_button.setText("等待" if busy else "发送")
            set_reply_waiting_ui = getattr(self, "_set_reply_waiting_ui", None)
            if callable(set_reply_waiting_ui):
                set_reply_waiting_ui(busy)
        self._log_interaction_stage("set_busy", {"busy": busy})
        update_reply_history_buttons = getattr(self, "_update_reply_history_buttons", None)
        if update_reply_history_buttons is not None:
            update_reply_history_buttons()

    @Slot(str)
    def set_speech(self, text: str) -> None:
        self.subtitle_controller.set_speech(text)

    def _connect_memory_status_listener(self) -> None:
        add_listener = getattr(self.memory_store, "add_status_listener", None)
        if not callable(add_listener):
            return
        try:
            add_listener(self.memory_status_changed.emit)
        except (TypeError, RuntimeError) as exc:
            debug_log("Memory", "连接长期记忆状态监听失败", {"error": str(exc)})

    @Slot(str, str)
    def _handle_memory_status_changed(self, status: str, message: str) -> None:
        message = str(message).strip()
        if not message:
            return
        debug_log("Memory", "长期记忆状态变化", {"status": status, "message": message})
        if status in {"loading", "reloading", "failed"}:
            self._show_memory_status_message(status, message)
            return
        if status == "ready":
            self._show_memory_ready_message(message)

    def _show_memory_status_message(self, status: str, message: str) -> None:
        self.memory_status_message_active = True
        self.memory_status_last_status = status
        self.memory_status_last_message = message
        if status == "failed":
            self._show_memory_failure_dialog(message)
        if (
            not self.startup_initializing
            and not self.active_interaction_id
            and not self.reply_history_review_active
        ):
            self.subtitle_controller.show_text_immediately(message)

    def _show_memory_failure_dialog(self, message: str) -> None:
        if getattr(self, "memory_failure_dialog_last_message", "") == message:
            return
        if self._should_defer_memory_failure_dialog():
            self.memory_failure_dialog_pending_message = message
            return
        self._display_memory_failure_dialog(message)

    def _should_defer_memory_failure_dialog(self) -> bool:
        if getattr(self, "startup_initializing", False):
            return True
        is_visible = getattr(self, "isVisible", None)
        if callable(is_visible):
            return not bool(is_visible())
        return False

    def _display_memory_failure_dialog(self, message: str) -> None:
        if getattr(self, "memory_failure_dialog_last_message", "") == message:
            return
        self.memory_failure_dialog_pending_message = ""
        self.memory_failure_dialog_last_message = message
        show_themed_warning(self, "记忆模型下载失败", message)

    @Slot()
    def _show_pending_memory_status_after_startup(self) -> None:
        if (
            not self.memory_status_message_active
            or self.startup_initializing
            or self.active_interaction_id
            or self.reply_history_review_active
            or not self.memory_status_last_message
        ):
            return
        self.subtitle_controller.show_text_immediately(self.memory_status_last_message)
        self._show_pending_memory_failure_dialog()

    @Slot()
    def _show_pending_memory_failure_dialog(self) -> None:
        message = getattr(self, "memory_failure_dialog_pending_message", "")
        if (
            not message
            or getattr(self, "startup_initializing", False)
            or getattr(self, "memory_status_last_status", "") != "failed"
        ):
            return
        if self._should_defer_memory_failure_dialog():
            return
        self._display_memory_failure_dialog(message)

    def _show_memory_ready_message(self, message: str) -> None:
        _ = message
        self.memory_status_last_status = "ready"
        self.memory_failure_dialog_pending_message = ""
        if not self.memory_status_message_active:
            return
        self.memory_status_message_active = False
        if self.active_interaction_id or self.reply_history_review_active:
            return
        QTimer.singleShot(MEMORY_STATUS_DISPLAY_MS, self._restore_memory_status_speech)

    @Slot()
    def _restore_memory_status_speech(self) -> None:
        if self.memory_status_message_active:
            return
        if self.active_interaction_id or self.reply_history_review_active:
            return
        self.subtitle_controller.show_text_immediately(self.character_profile.initial_message)

    @Slot(str)
    def _show_tts_error(self, message: str) -> None:
        message = str(message).strip()
        if not message:
            return
        text = f"TTS 异常：{_compact_tts_error(message)}"
        self.tts_error_label.setText(text)
        self.tts_error_label.setToolTip(message)
        self.tts_error_label.setVisible(True)
        self.tts_error_timer.start(TTS_ERROR_DISPLAY_MS)
        self._log_interaction_stage("tts_error_visible", {"message": message})
        debug_log("TTS", "TTS 错误已显示到界面", {"message": message})

    @Slot()
    def _hide_tts_error(self) -> None:
        self.tts_error_label.clear()
        self.tts_error_label.setToolTip("")
        self.tts_error_label.setVisible(False)

    def toggle_visible(self) -> None:
        if self.isVisible():
            self._hide_to_tray()
        else:
            self._show_from_tray()

    @Slot()
    def _hide_to_tray(self) -> None:
        self.hidden_to_tray = True
        self.pet_hidden_at = time.perf_counter()
        self.emit_runtime_event(PET_HIDDEN, source="tray")
        self.hide()
        self._refresh_tray_menu()

    @Slot()
    def _show_from_tray(self) -> None:
        self.hidden_to_tray = False
        # 启动阶段的初次显示不算「重新打开」，避免首启被误判。
        if not getattr(self, "startup_initializing", False):
            hidden_at = self.pet_hidden_at
            metadata: dict[str, Any] = {}
            priority = 0
            if hidden_at is not None:
                hidden_duration = int(time.perf_counter() - hidden_at)
                metadata["hidden_duration"] = hidden_duration
                if hidden_duration >= LONG_HIDDEN_SECONDS:
                    priority = 1
            self.emit_runtime_event(
                PET_REOPENED, source="tray", metadata=metadata, priority=priority
            )
        self.pet_hidden_at = None
        self.show()
        self.raise_()
        self.activateWindow()
        self._refresh_tray_menu()

    def emit_runtime_event(
        self,
        event_type: str,
        *,
        source: str = "",
        metadata: dict[str, Any] | None = None,
        priority: int = 0,
        inject: bool = True,
    ) -> None:
        """运行时事件的唯一发射入口（后续情绪 / 好感 / 插件订阅在此接入）。

        - 始终落盘到 RuntimeEventLog（行为日志 + 跨会话衔接）；
        - inject=True 时同时入内存队列，等下一次用户消息注入模型请求。
          app.closed 等跨进程事件用 inject=False（队列随进程消亡，只对落盘有意义）。
        """
        event = RuntimeEvent(
            event_type=event_type,
            source=source,
            metadata=dict(metadata or {}),
            priority=priority,
        )
        log = getattr(self, "runtime_event_log", None)
        if log is not None:
            log.append(event)
        if inject:
            self.runtime_event_queue.push(event)
        debug_log("PetWindow", "运行时事件", {"event": event.to_dict(), "inject": inject})

    def _handle_application_activated(self) -> None:
        if getattr(self, "hidden_to_tray", False):
            QTimer.singleShot(0, self._show_from_tray)

    @Slot()
    def show_history(self) -> None:
        if self.history_window is None:
            self.history_window = HistoryWindow(
                self.history_store,
                self.subtitle_language,
                self._save_history_to_memory_and_clear,
                self.theme_settings,
                self,
            )
        self.history_window.set_subtitle_language(self.subtitle_language)
        self.history_window.set_theme_settings(self.theme_settings)
        # 始终置顶，避免被桌宠卡片（同为置顶窗口）盖住。
        _mark_dialog_always_on_top(self.history_window)
        self.history_window.refresh()
        self.history_window.show()
        self.history_window.raise_()
        self.history_window.activateWindow()

    @Slot()
    def show_runtime_log(self) -> None:
        if self.runtime_log_window is None:
            self.runtime_log_window = RuntimeLogWindow(
                theme_settings=self.theme_settings,
                parent=self,
            )
        self.runtime_log_window.set_theme_settings(self.theme_settings)
        self.runtime_log_window.refresh(reset=True)
        self.runtime_log_window.show()
        self.runtime_log_window.raise_()
        self.runtime_log_window.activateWindow()

    def _save_history_to_memory_and_clear(self) -> None:
        if self.memory_curation_thread is not None:
            if self.memory_curation_mode in {"auto", "backfill"}:
                self.pending_history_clear_after_curation = True
                show_themed_information(
                    self,
                    "整理中",
                    "当前正在自动整理记忆，结束后会继续清空并保存历史。",
                )
                return
            show_themed_information(self, "整理中", "记忆整理已经在进行中，请稍后再试。")
            if self.history_window is not None:
                self.history_window.set_memory_save_busy(False)
            return
        if self.worker_thread is not None:
            show_themed_information(self, "正在回复", "当前聊天还没处理完，稍后再整理历史。")
            if self.history_window is not None:
                self.history_window.set_memory_save_busy(False)
            return
        entries = self.history_store.load()
        if not entries:
            if self.history_window is not None:
                self.history_window.set_memory_save_busy(False)
                self.history_window.refresh()
            return
        if not self._memory_store_ready_for_history_clear():
            self._show_memory_not_ready_for_history_clear()
            if self.history_window is not None:
                self.history_window.set_memory_save_busy(False)
            return
        self._reset_memory_curation_cache_for_history_clear()
        self._start_memory_curation(
            entries,
            mode="history_clear",
            target_history_count=len(entries),
            consumed_turns=self.memory_curation_state.pending_turns(),
        )

    def _memory_store_ready_for_history_clear(self) -> bool:
        is_ready = getattr(self.memory_store, "is_ready", None)
        if not callable(is_ready):
            return True
        try:
            return bool(is_ready())
        except Exception as exc:  # noqa: BLE001
            debug_log("Memory", "检查长期记忆就绪状态失败", {"error": str(exc)})
            return False

    def _show_memory_not_ready_for_history_clear(self) -> None:
        message = getattr(self, "memory_status_last_message", "") or (
            "长期记忆系统还在初始化。首次启动或覆盖更新后，"
            "可能需要准备本地嵌入模型，请稍等就绪后再试。"
        )
        show_themed_information(self, "记忆初始化中", message)

    def _reset_memory_curation_cache_for_history_clear(self) -> None:
        reset_cache = getattr(self.memory_store, "reset_curation_cache", None)
        if not callable(reset_cache):
            return
        try:
            reset_counts = reset_cache()
        except Exception as exc:  # noqa: BLE001
            debug_log("Memory", "重置 mem0 整理缓存失败", {"error": str(exc)})
            return
        debug_log("Memory", "已重置 mem0 整理缓存", reset_counts)

    @Slot()
    def show_settings(self) -> None:
        if getattr(self, "startup_initializing", False):
            return
        active_dialog = getattr(self, "settings_dialog", None)
        if active_dialog is not None:
            self._activate_settings_dialog(active_dialog)
            return
        try:
            tts_settings = self.settings_service.load_tts_settings(
                validate_enabled=False,
                character_profile=self.character_profile,
            )
        except (OSError, TTSConfigError) as exc:
            show_themed_warning(self, "配置读取失败", f"TTS 配置读取失败，将使用默认值打开设置：{exc}")
            tts_settings = self._default_tts_settings()

        dialog = SettingsDialog(
            self.api_client.settings,
            tts_settings,
            self.base_dir,
            self.character_registry,
            self.character_profile,
            self.proactive_care_settings,
            self.mcp_settings,
            self.debug_log_settings,
            self.memory_store,
            getattr(self.plugin_manager, "tools_tabs", []),
            getattr(self.plugin_manager, "settings_panels", []),
            parent=self,
            portrait_scale_percent=self.portrait_scale_percent,
            control_panel_width=self.control_panel_width,
            bubble_height=self.bubble_height,
            control_panel_vertical_offset=self.control_panel_vertical_offset,
            input_bar_offset=self.input_bar_offset,
            subtitle_typing_interval_ms=self.subtitle_typing_interval_ms,
            reply_segment_pause_ms=self.reply_segment_pause_ms,
            theme_settings=getattr(self, "theme_settings", DEFAULT_THEME_SETTINGS),
            startup_settings=getattr(self, "startup_settings", StartupSettings()),
            bubble_settings=getattr(self, "bubble_settings", BubbleSettings()),
            on_layout_preview=self._preview_layout,
        )
        self.settings_dialog = dialog
        # 始终置顶，避免被桌宠卡片（同为置顶窗口）盖住。
        _mark_dialog_always_on_top(dialog)
        # 记录打开前的立绘缩放与控制组布局，便于取消时回滚实时预览。
        original_layout = (
            self.portrait_scale_percent,
            self.control_panel_width,
            self.bubble_height,
            self.control_panel_vertical_offset,
            self.input_bar_offset,
        )
        # 设置期间保持气泡与输入栏常显并停掉自动隐藏，方便实时观察调整效果。
        bubble_auto_hide = getattr(self, "bubble_auto_hide", None)
        if bubble_auto_hide is not None:
            bubble_auto_hide.notify_speaking()
        input_bar_animator = getattr(self, "input_bar_animator", None)
        if input_bar_animator is not None:
            input_bar_animator.set_force_visible(True)
        try:
            dialog_result = dialog.exec()
        finally:
            if getattr(self, "settings_dialog", None) is dialog:
                self.settings_dialog = None
            # 关闭设置后恢复气泡自动隐藏计时与输入栏常规显隐。
            if bubble_auto_hide is not None:
                bubble_auto_hide.notify_settled()
            if input_bar_animator is not None:
                input_bar_animator.set_force_visible(False)
        if dialog_result != QDialog.DialogCode.Accepted:
            # 取消/关闭：回滚到打开前的立绘与控制组布局，撤销实时预览的改动。
            self._preview_layout(*original_layout)
            return
        result_subtitle_typing_interval_ms = getattr(
            dialog,
            "result_subtitle_typing_interval_ms",
            self.subtitle_typing_interval_ms,
        )
        result_reply_segment_pause_ms = getattr(
            dialog,
            "result_reply_segment_pause_ms",
            self.reply_segment_pause_ms,
        )
        result_theme_settings = getattr(
            dialog,
            "result_theme_settings",
            getattr(self, "theme_settings", DEFAULT_THEME_SETTINGS),
        )
        current_startup_settings = getattr(self, "startup_settings", StartupSettings())
        result_startup_settings = getattr(
            dialog,
            "result_startup_settings",
            current_startup_settings,
        )
        result_bubble_settings = getattr(
            dialog,
            "result_bubble_settings",
            getattr(self, "bubble_settings", BubbleSettings()),
        )
        result_control_panel_width = getattr(
            dialog, "result_control_panel_width", self.control_panel_width
        )
        result_bubble_height = getattr(
            dialog, "result_bubble_height", self.bubble_height
        )
        result_control_panel_vertical_offset = getattr(
            dialog,
            "result_control_panel_vertical_offset",
            self.control_panel_vertical_offset,
        )
        result_input_bar_offset = getattr(
            dialog, "result_input_bar_offset", self.input_bar_offset
        )
        if (
            dialog.result_api_settings is None
            or dialog.result_tts_settings is None
            or dialog.result_character_id is None
            or dialog.result_proactive_care_settings is None
            or dialog.result_mcp_settings is None
            or dialog.result_debug_log_settings is None
            or result_startup_settings is None
            or not isinstance(result_startup_settings, StartupSettings)
            or dialog.result_portrait_scale_percent is None
            or result_theme_settings is None
            or result_subtitle_typing_interval_ms is None
            or result_reply_segment_pause_ms is None
        ):
            return
        (
            result_subtitle_typing_interval_ms,
            result_reply_segment_pause_ms,
        ) = normalize_subtitle_display_speed(
            result_subtitle_typing_interval_ms,
            result_reply_segment_pause_ms,
        )

        dialog_character_registry = getattr(dialog, "character_registry", None) or self.character_registry
        try:
            selected_profile = dialog_character_registry.get(dialog.result_character_id)
        except CharacterConfigError as exc:
            show_themed_critical(self, "角色配置无效", str(exc))
            return

        new_tts_provider = self._create_tts_provider_from_settings(dialog.result_tts_settings)
        if new_tts_provider is None:
            return

        api_changed = dialog.result_api_settings != self.api_client.settings
        startup_settings_changed = result_startup_settings != current_startup_settings
        theme_write_mode = getattr(dialog, "result_theme_write_mode", "unchanged")
        should_write_character_theme = _should_write_character_theme(theme_write_mode, selected_profile)
        try:
            if api_changed:
                self.settings_service.save_api_settings(dialog.result_api_settings)
            self.settings_service.save_tts_settings(dialog.result_tts_settings)
            if should_write_character_theme:
                save_character_theme(
                    selected_profile,
                    result_theme_settings,
                    source=THEME_SOURCE_PACKAGE,
                )
                dialog_character_registry = CharacterRegistry(self.base_dir)
                selected_profile = dialog_character_registry.get(selected_profile.id)
            self.character_registry = dialog_character_registry
            self.settings_service.save_current_character_id(
                self.character_registry,
                selected_profile.id,
            )
            self.settings_service.save_proactive_care_settings(
                dialog.result_proactive_care_settings
            )
            self.settings_service.save_mcp_runtime_settings(dialog.result_mcp_settings)
            self.settings_service.save_debug_log_settings(dialog.result_debug_log_settings)
            if startup_settings_changed:
                self._apply_launch_at_login_settings(result_startup_settings)
                self.settings_service.save_startup_settings(result_startup_settings)
            if result_theme_settings != getattr(self, "theme_settings", DEFAULT_THEME_SETTINGS):
                self.settings_service.save_theme_settings(result_theme_settings)
            self._save_system_config_values(
                "ui",
                {
                    "portrait_scale_percent": dialog.result_portrait_scale_percent,
                    "subtitle_typing_interval_ms": result_subtitle_typing_interval_ms,
                    "reply_segment_pause_ms": result_reply_segment_pause_ms,
                },
            )
            self.settings_service.save_bubble_settings(result_bubble_settings)
        except (CharacterConfigError, OSError) as exc:
            show_themed_critical(self, "保存失败", f"无法保存设置：{exc}")
            return

        if api_changed:
            self.api_client.update_settings(dialog.result_api_settings)
            self.memory_store.reload_api_settings(dialog.result_api_settings, wait=False)
        self._apply_layout_settings(
            portrait_scale_percent=dialog.result_portrait_scale_percent,
            control_panel_width=result_control_panel_width,
            bubble_height=result_bubble_height,
            vertical_offset=result_control_panel_vertical_offset,
            input_bar_offset=result_input_bar_offset,
            persist=True,
        )
        self._apply_subtitle_display_speed(
            result_subtitle_typing_interval_ms,
            result_reply_segment_pause_ms,
        )
        self._apply_bubble_settings(result_bubble_settings)
        apply_theme_settings = getattr(self, "_apply_theme_settings", None)
        if callable(apply_theme_settings):
            apply_theme_settings(result_theme_settings)
        else:
            self.theme_settings = result_theme_settings
        self.proactive_care_settings = dialog.result_proactive_care_settings
        mcp_restart_required = dialog.result_mcp_settings != self.mcp_settings
        self.mcp_settings = dialog.result_mcp_settings
        self.debug_log_settings = dialog.result_debug_log_settings
        self.startup_settings = result_startup_settings
        self._sync_proactive_care_timer()
        disconnect_tts_error_signal = getattr(self, "_disconnect_tts_error_signal", None)
        if callable(disconnect_tts_error_signal):
            disconnect_tts_error_signal(self.tts_provider)
        keep_local_tts_service = _should_keep_tts_local_service(
            self.tts_provider,
            new_tts_provider,
        )
        self._retire_tts_provider(
            self.tts_provider,
            keep_local_service=keep_local_tts_service,
        )
        self.tts_provider = new_tts_provider
        self.voice_playback_controller.set_provider(new_tts_provider)
        connect_tts_error_signal = getattr(self, "_connect_tts_error_signal", None)
        if callable(connect_tts_error_signal):
            connect_tts_error_signal(new_tts_provider)
        self._warm_up_tts_playback(new_tts_provider)
        start_tts_ready_warmup = getattr(self, "_start_tts_ready_warmup", None)
        if callable(start_tts_ready_warmup):
            start_tts_ready_warmup(new_tts_provider)
        self._apply_character(selected_profile)
        if hasattr(self, "tray_icon"):
            self.tray_icon.setContextMenu(self._build_menu())
        message = "设置已保存，后续聊天和朗读将使用新配置。"
        if api_changed:
            message += "\n\n长期记忆系统正在后台刷新 API 配置。"
        if mcp_restart_required:
            message += "\n\nWindows MCP 开关需要重启 Sakura 后才会生效。"
        if startup_settings_changed:
            message += "\n\n登录自启动设置已更新。"
        if getattr(dialog, "result_plugin_config_changed", False):
            message += "\n\n插件启用状态需要重启 Sakura 后才会生效。"
        show_themed_information(self, "保存成功", message)

    def _activate_settings_dialog(self, dialog: SettingsDialog) -> None:
        """重复打开设置时激活已有窗口，避免托盘菜单创建多个设置页。"""

        show = getattr(dialog, "show", None)
        if callable(show):
            show()
        raise_window = getattr(dialog, "raise_", None)
        if callable(raise_window):
            raise_window()
        activate_window = getattr(dialog, "activateWindow", None)
        if callable(activate_window):
            activate_window()

    @Slot(bool)
    def _toggle_chinese_subtitles(self, checked: bool) -> None:
        next_language = SUBTITLE_LANGUAGE_ZH if checked else SUBTITLE_LANGUAGE_JA
        if next_language == self.subtitle_language:
            return

        previous_language = self.subtitle_language
        self.subtitle_language = next_language
        try:
            self._save_system_config_values(
                "ui",
                {"subtitle_language": next_language},
            )
        except OSError as exc:
            self.subtitle_language = previous_language
            self._apply_speech_font()
            show_themed_warning(self, "保存失败", f"无法保存字幕设置：{exc}")
            return

        self._apply_speech_font()
        self.subtitle_controller.set_subtitle_language(self.subtitle_language)
        if not self._refresh_reply_history_review_text():
            self.subtitle_controller.restart_current_segment_speech()
        if self.history_window is not None:
            self.history_window.set_subtitle_language(self.subtitle_language)

    @Slot(bool)
    def _toggle_model_vision(self, checked: bool) -> None:
        self._set_model_vision_enabled(checked)

    def _set_model_vision_enabled(self, enabled: bool) -> None:
        enabled = enabled and self.screen_observation_enabled
        self.model_vision_enabled = enabled
        self.agent_runtime.set_model_vision_enabled(enabled)
        if hasattr(self, "tray_icon"):
            self.tray_icon.setContextMenu(self._build_menu())

    @Slot(bool)
    def _toggle_autonomous_screen_observation(self, checked: bool) -> None:
        self.autonomous_screen_observation_enabled = checked and self.screen_observation_enabled
        self.agent_runtime.set_autonomous_screen_observation_enabled(
            self.autonomous_screen_observation_enabled
        )
        try:
            self._save_system_config_values(
                "screen_observation",
                {
                    "autonomous_enabled": self.autonomous_screen_observation_enabled,
                },
            )
        except OSError as exc:
            show_themed_warning(self, "保存失败", f"无法保存自主看屏幕设置：{exc}")
        if hasattr(self, "tray_icon"):
            self.tray_icon.setContextMenu(self._build_menu())

    @Slot(bool)
    def _toggle_free_access(self, checked: bool) -> None:
        self.free_access_enabled = checked
        self.tool_registry.set_free_access_enabled(checked)
        self._save_system_config_values("ui", {"free_access_enabled": checked})
        if hasattr(self, "tray_icon"):
            self.tray_icon.setContextMenu(self._build_menu())

    @Slot(bool)
    def _toggle_always_on_top(self, checked: bool) -> None:
        if checked == self.always_on_top_enabled:
            return
        previous_enabled = self.always_on_top_enabled
        self.always_on_top_enabled = checked
        try:
            self._save_system_config_values("ui", {"always_on_top_enabled": checked})
        except OSError as exc:
            self.always_on_top_enabled = previous_enabled
            show_themed_warning(self, "保存失败", f"无法保存置顶设置：{exc}")
            return

        self._apply_window_flags()
        if checked:
            self.raise_()
        if hasattr(self, "tray_icon"):
            self.tray_icon.setContextMenu(self._build_menu())

    def _create_tts_provider_from_settings(
        self,
        settings: GPTSoVITSTTSSettings,
    ) -> TTSProvider | None:
        if not settings.enabled:
            debug_log("PetWindow", "设置保存后 TTS 保持关闭")
            return NullTTSProvider()
        try:
            provider = (
                GenieTTSProvider(settings, adopt_existing_service=False)
                if settings.provider == TTS_PROVIDER_GENIE
                else GPTSoVITSTTSProvider(settings, adopt_existing_service=False)
            )
            debug_log(
                "PetWindow",
                "设置保存后 TTS Provider 已创建",
                {
                    "provider": settings.provider,
                    "api_url": settings.api_url,
                    "timeout_seconds": settings.timeout_seconds,
                },
            )
            return provider
        except TTSConfigError as exc:
            debug_log("PetWindow", "TTS 配置无效", {"error": str(exc)})
            show_themed_critical(self, "TTS 配置无效", f"无法启用 TTS，当前语音配置保持不变：{exc}")
            return None

    def _retire_tts_provider(
        self,
        provider: TTSProvider,
        *,
        keep_local_service: bool = False,
    ) -> None:
        if keep_local_service:
            detach = getattr(provider, "detach_local_service", None)
            if callable(detach):
                try:
                    detach()
                    debug_log(
                        "TTS",
                        "切换配置时保留本地 TTS 服务进程",
                        {"provider": type(provider).__name__},
                    )
                except Exception as exc:  # noqa: BLE001
                    debug_log(
                        "TTS",
                        "交出旧 TTS 本地服务所有权失败",
                        {"provider": type(provider).__name__, "error": str(exc)},
                    )
        close = getattr(provider, "close", None)
        if callable(close):
            try:
                close()
            except Exception as exc:  # noqa: BLE001
                debug_log(
                    "TTS",
                    "切换配置时关闭旧 TTS Provider 失败",
                    {"provider": type(provider).__name__, "error": str(exc)},
                )
        self.retired_tts_providers.append(provider)

    def _default_tts_settings(self) -> GPTSoVITSTTSSettings:
        if self.character_profile.voice is not None:
            return GPTSoVITSTTSSettings.from_character_profile(
                character_profile=self.character_profile,
                enabled=False,
                api_url="http://127.0.0.1:9880/tts",
                ref_lang=self.character_profile.voice.ref_lang,
                text_lang=self.character_profile.voice.text_lang,
                timeout_seconds=60,
                validate_enabled=False,
            )
        return GPTSoVITSTTSSettings(
            enabled=False,
            api_url="http://127.0.0.1:9880/tts",
            ref_audio_path=self.base_dir / "ref" / "VO01_2210.ogg",
            ref_text_path=self.base_dir / "ref" / "text.txt",
            ref_text="",
            ref_lang="ja",
            text_lang="ja",
            timeout_seconds=60,
        )

    def _record_history(
        self,
        role: str,
        content: str,
        translation: str = "",
        tone: str = "",
        portrait: str = "",
        _debug: dict | None = None,
    ) -> None:
        try:
            self.history_store.append(role, content, translation, tone, portrait, _debug=_debug)
        except OSError as exc:
            print(f"[History] 写入失败：{exc}")
            debug_log(
                "History",
                "写入失败",
                {
                    "role": role,
                    "content": content,
                    "translation": translation,
                    "tone": tone,
                    "portrait": portrait,
                    "error": str(exc),
                },
            )

    def _record_assistant_reply_history(self, reply: ChatReply, _debug: dict | None = None) -> None:
        clean_segments = [segment for segment in reply.segments if segment.text.strip()]
        if not clean_segments:
            return
        for i, segment in enumerate(clean_segments):
            self._record_history(
                "assistant",
                segment.text,
                segment.translation,
                segment.tone,
                segment.portrait,
                _debug=_debug if i == 0 else None,
            )

    @Slot()
    def _check_due_reminders(self) -> None:
        if getattr(self, "startup_initializing", False):
            return
        if self.worker_thread is not None or self.active_reminder_id is not None:
            return
        try:
            due_reminders = self.reminder_store.due_reminders()
        except ValueError as exc:
            print(f"[Reminder] 读取失败：{exc}")
            debug_log("Reminder", "读取失败", {"error": str(exc)})
            return
        if not due_reminders:
            return

        reminder = due_reminders[0]
        reminder_id = str(reminder.get("id", ""))
        reminder_text = str(reminder.get("text", ""))
        reminder_trigger_at = str(reminder.get("trigger_at", ""))
        if not reminder_id:
            debug_log("Reminder", "跳过缺少 id 的到期提醒", {"reminder": reminder})
            return
        debug_log(
            "Reminder",
            "触发到期提醒",
            {
                "id": reminder_id,
                "text": reminder_text,
                "trigger_at": reminder_trigger_at,
                "due_count": len(due_reminders),
            },
        )
        self._run_event_worker(
            AgentEvent(
                type="reminder_due",
                payload={
                    "id": reminder_id,
                    "text": reminder_text,
                    "trigger_at": reminder_trigger_at,
                },
            ),
            reminder_id,
        )

    def _show_reply_segments(self, segments: list[ChatSegment]) -> None:
        self._exit_reply_history_review(update_buttons=False)
        self._remember_reply_history_segments(segments)
        self.subtitle_controller.show_segments(segments)

    def _load_subtitle_language(self) -> str:
        system_values = self._load_system_config_values("ui")
        language = str(system_values.get("subtitle_language", "")).strip().lower()
        if language == SUBTITLE_LANGUAGE_JA:
            return SUBTITLE_LANGUAGE_JA
        return SUBTITLE_LANGUAGE_ZH

    def _load_portrait_scale_percent(self) -> int:
        system_values = self._load_system_config_values("ui")
        return normalize_portrait_scale_percent(
            system_values.get("portrait_scale_percent", PORTRAIT_SCALE_DEFAULT_PERCENT)
        )

    def _load_control_panel_width(self) -> int:
        system_values = self._load_system_config_values("ui")
        return normalize_control_panel_width(
            system_values.get("control_panel_width", DEFAULT_CONTROL_PANEL_WIDTH)
        )

    def _load_bubble_height(self) -> int:
        system_values = self._load_system_config_values("ui")
        return normalize_bubble_height(
            system_values.get("bubble_height", DEFAULT_BUBBLE_HEIGHT)
        )

    def _load_control_panel_vertical_offset(self) -> int:
        system_values = self._load_system_config_values("ui")
        return normalize_control_panel_vertical_offset(
            system_values.get(
                "control_panel_vertical_offset",
                DEFAULT_CONTROL_PANEL_VERTICAL_OFFSET,
            )
        )

    def _load_input_bar_offset(self) -> int:
        system_values = self._load_system_config_values("ui")
        return normalize_input_bar_offset(
            system_values.get("input_bar_offset", DEFAULT_INPUT_BAR_OFFSET)
        )

    def _load_subtitle_display_speed(self) -> tuple[int, int]:
        system_values = self._load_system_config_values("ui")
        return normalize_subtitle_display_speed(
            system_values.get("subtitle_typing_interval_ms", SPEECH_TYPING_INTERVAL_MS),
            system_values.get("reply_segment_pause_ms", REPLY_SEGMENT_PAUSE_MS),
        )

    def _load_screen_observation_enabled(self) -> bool:
        system_values = self._load_system_config_values("screen_observation")
        if "enabled" in system_values:
            enabled = _parse_bool(system_values.get("enabled"), default=True)
            debug_log("PetWindow", "屏幕观察 YAML 配置已加载", {"enabled": enabled})
            return enabled
        return True

    def _load_autonomous_screen_observation_enabled(self) -> bool:
        system_values = self._load_system_config_values("screen_observation")
        if "autonomous_enabled" in system_values:
            enabled = _parse_bool(system_values.get("autonomous_enabled"), default=True)
            enabled = enabled and self.screen_observation_enabled
            debug_log("PetWindow", "自主屏幕观察 YAML 配置已加载", {"enabled": enabled})
            return enabled
        return self.screen_observation_enabled

    def _load_free_access_enabled(self) -> bool:
        """从 system_config.yaml 加载完整访问权限设置。"""
        system_values = self._load_system_config_values("ui")
        if "free_access_enabled" in system_values:
            return _parse_bool(system_values.get("free_access_enabled"), default=True)
        return True

    def _load_always_on_top_enabled(self) -> bool:
        """从 system_config.yaml 加载主窗口置顶设置，默认不置顶。"""
        system_values = self._load_system_config_values("ui")
        if "always_on_top_enabled" in system_values:
            return _parse_bool(system_values.get("always_on_top_enabled"), default=False)
        return False

    def _load_system_config_values(self, section: str) -> dict[str, Any]:
        return self.settings_service.load_system_values(section)

    def _save_system_config_values(
        self,
        section: str,
        values: dict[str, Any],
    ) -> None:
        self.settings_service.save_system_values(section, values)

    def _apply_launch_at_login_settings(self, settings: StartupSettings) -> None:
        try:
            set_launch_at_login_enabled(self.base_dir, settings.launch_at_login)
        except (LaunchAtLoginError, OSError) as exc:
            raise OSError(f"无法更新登录自启动：{exc}") from exc

    def _window_flags(self) -> Qt.WindowType:
        flags = Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool
        if self.always_on_top_enabled:
            flags |= Qt.WindowType.WindowStaysOnTopHint
        return flags

    def _apply_window_flags(self) -> None:
        was_visible = self.isVisible()
        self.setWindowFlags(self._window_flags())
        # 单窗口重构后气泡/输入栏为子控件，无独立置顶标志需同步。
        if was_visible:
            self.show()
            self._schedule_native_topmost_sync()
            QTimer.singleShot(0, self._raise_foreground_controls)

    def _schedule_native_topmost_sync(self) -> None:
        if sys.platform not in {"win32", "darwin"}:
            return
        QTimer.singleShot(0, self._sync_native_topmost_state)

    def _sync_native_topmost_state(self) -> None:
        if not self.isVisible():
            return
        if sys.platform == "win32":
            try:
                import ctypes

                hwnd = int(self.winId())
                hwnd_topmost = -1
                hwnd_notopmost = -2
                swp_no_size = 0x0001
                swp_no_move = 0x0002
                swp_no_activate = 0x0010
                insert_after = hwnd_topmost if self.always_on_top_enabled else hwnd_notopmost
                flags = swp_no_size | swp_no_move | swp_no_activate
                for window in self._topmost_sync_windows():
                    ctypes.windll.user32.SetWindowPos(
                        int(window.winId()), insert_after, 0, 0, 0, 0, flags
                    )
            except Exception as exc:  # noqa: BLE001
                debug_log("PetWindow", "同步原生置顶状态失败", {"error": str(exc)})
            return
        if sys.platform == "darwin":
            try:
                for window in self._topmost_sync_windows():
                    _set_macos_window_topmost(int(window.winId()), self.always_on_top_enabled)
            except Exception as exc:  # noqa: BLE001
                debug_log("PetWindow", "同步 macOS 原生置顶状态失败", {"error": str(exc)})

    def _topmost_sync_windows(self):
        # 单窗口重构后只有主窗口一个顶层窗口，置顶仅作用于它。
        return [self]

    def _apply_layout_settings(
        self,
        *,
        portrait_scale_percent: object,
        control_panel_width: object,
        bubble_height: object,
        vertical_offset: object,
        input_bar_offset: object,
        persist: bool,
    ) -> None:
        """一次性应用「立绘缩放 + 控制组布局」：归一化 → 锁定立绘底边锚点 → 更新状态（含按需重贴立绘）
        → 单次统一布局（一次 setGeometry，全程抑帧）。persist=True 时无条件持久化控制组布局。

        合并为单次几何提交，是为了消除「缩放」「控制组」两步各自 setGeometry 造成的窗口二次跳动
        ——setUpdatesEnabled 只压 Qt 重绘，压不住 OS 层窗口移动，两次 setGeometry 会被合成出抖动。
        持久化不再依赖 changed 判定：预览阶段已把内存值改写为新值，点确定时若按 changed 判断会被
        当作未变更而漏存，导致重开丢失气泡/输入栏调整。
        """
        next_scale = normalize_portrait_scale_percent(portrait_scale_percent)
        next_width = normalize_control_panel_width(control_panel_width)
        next_bubble_height = normalize_bubble_height(bubble_height)
        next_offset = normalize_control_panel_vertical_offset(vertical_offset)
        next_input_offset = normalize_input_bar_offset(input_bar_offset)

        # 在任何状态变更之前锁定立绘底边的屏幕点，保证缩放/调参后立绘站位不动。
        anchor = self._portrait_anchor_global()
        scale_changed = next_scale != self.portrait_scale_percent

        was_enabled = self.updatesEnabled()
        self.setUpdatesEnabled(False)
        try:
            self.portrait_scale_percent = next_scale
            self.control_panel_width = next_width
            self.bubble_height = next_bubble_height
            self.control_panel_vertical_offset = next_offset
            self.input_bar_offset = next_input_offset
            # 用户设置值作为气泡高度下限：新设置 >= 当前自适应高度时清除自适应，回归用户值；
            # 新设置 < 自适应高度时保留自适应，等拖过自适应高度再接管，避免拖动错位。
            if self._auto_fit_bubble_height is not None and next_bubble_height >= self._auto_fit_bubble_height:
                self._auto_fit_bubble_height = None
            if scale_changed:
                self.portrait_controller.set_portrait_scale_percent(next_scale)
                self.portrait_controller.apply_current()  # 按新缩放重贴立绘（抑帧中，无中间帧）
            self._apply_pet_layout(anchor_global=anchor)  # 单次 setGeometry
        finally:
            self.setUpdatesEnabled(was_enabled)
        if persist:
            self._save_control_panel_layout()

    def _save_control_panel_layout(self) -> None:
        try:
            self._save_system_config_values(
                "ui",
                {
                    "control_panel_width": self.control_panel_width,
                    "bubble_height": self.bubble_height,
                    "control_panel_vertical_offset": self.control_panel_vertical_offset,
                    "input_bar_offset": self.input_bar_offset,
                },
            )
        except OSError as exc:
            debug_log("PetWindow", "保存控制组布局失败", {"error": str(exc)})

    def _preview_layout(
        self,
        portrait_scale_percent: object,
        control_panel_width: object,
        bubble_height: object,
        vertical_offset: object,
        input_bar_offset: object,
    ) -> None:
        """设置对话框滑块拖动时的实时预览：立绘缩放 + 控制组布局以单次几何提交立即应用，不持久化。"""
        self._apply_layout_settings(
            portrait_scale_percent=portrait_scale_percent,
            control_panel_width=control_panel_width,
            bubble_height=bubble_height,
            vertical_offset=vertical_offset,
            input_bar_offset=input_bar_offset,
            persist=False,
        )

    def _apply_subtitle_display_speed(
        self,
        subtitle_typing_interval_ms: int,
        reply_segment_pause_ms: int,
    ) -> None:
        (
            self.subtitle_typing_interval_ms,
            self.reply_segment_pause_ms,
        ) = normalize_subtitle_display_speed(
            subtitle_typing_interval_ms,
            reply_segment_pause_ms,
        )
        subtitle_controller = getattr(self, "subtitle_controller", None)
        set_display_speed = getattr(subtitle_controller, "set_display_speed", None)
        if callable(set_display_speed):
            set_display_speed(
                self.subtitle_typing_interval_ms,
                self.reply_segment_pause_ms,
            )

    def _apply_theme_settings(self, theme_settings: ThemeSettings) -> None:
        self.theme_settings = (theme_settings or DEFAULT_THEME_SETTINGS).normalized()
        self.setStyleSheet(pet_window_stylesheet(self.theme_settings))
        self._apply_app_chrome_stylesheet()
        self._apply_card_window_theme()
        if self.history_window is not None:
            self.history_window.set_theme_settings(self.theme_settings)
        if self.runtime_log_window is not None:
            self.runtime_log_window.set_theme_settings(self.theme_settings)
        if hasattr(self, "tray_icon"):
            self.tray_icon.setIcon(_build_status_tray_icon(self.theme_settings.primary_color))

    def _apply_app_chrome_stylesheet(self) -> None:
        # 全局美化滚动条与菜单等独立顶层控件。
        app = QApplication.instance()
        if app is not None:
            app.setStyleSheet(build_app_chrome_stylesheet(self.theme_settings))

    def _apply_card_window_theme(self) -> None:
        # 单窗口重构后气泡/输入栏为子控件，样式由主窗口 setStyleSheet 级联，无需各自 set_theme。
        # 仅需更新输入栏软件模糊背景层的叠色与暗色遮罩，并按当前模式重建背景管线。
        background = getattr(self, "input_blur_background", None)
        if background is not None:
            background.set_tint(self._card_tint())
            background.set_shadow_overlay(self._card_shadow_overlay())
        self._sync_input_bar_backdrop()

    def _card_tint(self) -> QColor:
        # 亚克力磨砂底色：从气泡背景色派生，alpha 偏低让背后桌面更通透、磨砂更淡。
        tint = QColor(self.theme_settings.bubble_background_color)
        tint.setAlpha(55)
        return tint

    def _card_shadow_overlay(self) -> QColor:
        # 由主题主色压暗得到轻遮罩：保留主题倾向，同时保持“黑色遮罩”的压光效果。
        source = QColor(self.theme_settings.primary_color)
        overlay = QColor(
            int(source.red() * 0.35),
            int(source.green() * 0.35),
            int(source.blue() * 0.35),
            24,
        )
        return overlay

    # ── 输入栏视觉效果（对称统一管线）────────────────────────────────

    def _input_bar_visual_effect_mode(self) -> str:
        mode = VisualEffectMode.validate(
            getattr(self.theme_settings, "visual_effect_mode", VisualEffectMode.DEFAULT)
        )
        if mode == VisualEffectMode.WINDOWS_ACRYLIC:
            # 单窗口输入栏没有独立 HWND，旧 Windows 亚克力配置按当前可用效果降级为软件高斯模糊。
            return VisualEffectMode.GAUSSIAN_BLUR
        if mode == VisualEffectMode.MACOS_VISUAL_EFFECT and sys.platform != "darwin":
            return VisualEffectMode.GAUSSIAN_BLUR
        return mode

    def _apply_input_bar_visual_effect_property(self, mode: str) -> None:
        """同步动态样式属性，让纯色块等模式能触发对应 QSS。"""
        for widget in (getattr(self, "input_bar", None), getattr(self, "input_edit", None)):
            if widget is None:
                continue
            if widget.property("visualEffectMode") == mode:
                continue
            widget.setProperty("visualEffectMode", mode)
            style = widget.style()
            style.unpolish(widget)
            style.polish(widget)
            widget.update()

    def _input_bar_uses_native_macos_backdrop(self) -> bool:
        return (
            sys.platform == "darwin"
            and self._input_bar_visual_effect_mode() == VisualEffectMode.MACOS_VISUAL_EFFECT
        )

    def _input_bar_blur_pipeline(
        self,
    ) -> tuple[
        bool,
        Callable[[], None] | None,
        Callable[[], None] | None,
        Callable[[], None] | None,
    ]:
        """根据当前视觉效果模式返回背景层与动画 hook。

        单窗口重构后输入栏为子控件，Windows 亚克力依赖独立 HWND，不再作为可选效果：
        - SOLID：纯色块，无背景层、无回调；
        - GAUSSIAN_BLUR / 旧 WINDOWS_ACRYLIC：窗口内软件高斯模糊；
        - macOS 原生毛玻璃：NSVisualEffectView，显示后挂载，隐藏前移除。
        同时同步动态 QSS 属性，使纯色等模式能触发对应样式。
        """
        mode = self._input_bar_visual_effect_mode()
        self._apply_input_bar_visual_effect_property(mode)
        if mode == VisualEffectMode.SOLID:
            return False, None, None, None
        if mode == VisualEffectMode.MACOS_VISUAL_EFFECT:
            return False, None, self._apply_input_bar_native_backdrop, self._remove_input_bar_native_backdrop
        return True, self._refresh_input_blur_background, None, None

    def _sync_input_bar_backdrop(self) -> None:
        """外观效果模式 / 主题改变时，重建输入栏背景管线。"""
        native_enabled = self._input_bar_uses_native_macos_backdrop()
        needs_bg, before_show, after_show, before_hide = self._input_bar_blur_pipeline()
        card = getattr(self, "input_card", None)
        bg = getattr(self, "input_blur_background", None)
        if card is not None:
            card.set_background_layer(bg if needs_bg else None)
        if native_enabled:
            self._sync_input_bar_native_backdrop_geometry()
        else:
            self._remove_input_bar_native_backdrop()
        animator = getattr(self, "input_bar_animator", None)
        if animator is not None:
            set_before_show = getattr(animator, "set_before_show", None)
            if callable(set_before_show):
                set_before_show(before_show)
            set_after_show = getattr(animator, "set_after_show", None)
            if callable(set_after_show):
                set_after_show(after_show)
            set_before_hide = getattr(animator, "set_before_hide", None)
            if callable(set_before_hide):
                set_before_hide(before_hide)

    def _apply_input_bar_native_backdrop(self) -> None:
        """在 macOS 输入栏子视图背后安装原生 NSVisualEffectView。"""
        if not self._input_bar_uses_native_macos_backdrop():
            return
        card = getattr(self, "input_card", None)
        if card is None or not card.isVisible():
            return
        backdrop = getattr(self, "input_native_backdrop", None)
        if backdrop is None:
            backdrop = MacOSVisualEffectBackdrop()
            self.input_native_backdrop = backdrop
        try:
            backdrop.apply(card, self._card_tint())
        except Exception as exc:  # noqa: BLE001
            debug_log("UI", "输入栏 macOS 原生毛玻璃应用失败", {"error": str(exc)})

    def _remove_input_bar_native_backdrop(self) -> None:
        """移除输入栏 macOS 原生毛玻璃层，避免模式切换或隐藏后残留。"""
        backdrop = getattr(self, "input_native_backdrop", None)
        card = getattr(self, "input_card", None)
        if backdrop is None or card is None:
            return
        try:
            backdrop.remove(card)
        except Exception as exc:  # noqa: BLE001
            debug_log("UI", "输入栏 macOS 原生毛玻璃移除失败", {"error": str(exc)})

    def _sync_input_bar_native_backdrop_geometry(self) -> None:
        """输入栏布局变化时同步 NSVisualEffectView frame。"""
        if not self._input_bar_uses_native_macos_backdrop():
            return
        self._apply_input_bar_native_backdrop()

    # ── 角色切换 ─────────────────────────────────────────────────────

    def _apply_character(self, profile: CharacterProfile) -> None:
        previous_character_id = self.character_profile.id
        self.character_profile = profile
        self.system_prompt = load_character_system_prompt(profile)
        self.memory_store.set_scope(profile.id)
        self.agent_runtime.update_character(self.system_prompt, profile.reply_tones, profile.portrait_choices)
        self.setWindowTitle(profile.display_name)
        self.name_label.setText(profile.display_name)
        self.input_edit.setPlaceholderText(self._normal_input_placeholder_text(profile))
        # 角色切换可能改变立绘实际尺寸，需按新立绘重算窗口几何；全程抑帧避免中间错位帧，
        # 以立绘底边为锚点保持桌宠站位不动。
        anchor = self._portrait_anchor_global()
        was_enabled = self.updatesEnabled()
        self.setUpdatesEnabled(False)
        try:
            self.portrait_controller.set_profile(profile)
            self._apply_pet_layout(anchor_global=anchor)
        finally:
            self.setUpdatesEnabled(was_enabled)
        if hasattr(self, "tray_icon"):
            self.tray_icon.setToolTip(profile.display_name)
            self.tray_icon.setIcon(_build_status_tray_icon(self.theme_settings.primary_color))

        self.history_store = self._create_history_store(profile)
        self.runtime_event_log = self._create_runtime_event_log(profile)
        self.pet_hidden_at = None
        self.visual_observation_store = self._create_visual_observation_store(profile)
        if self.history_window is not None:
            self.history_window.set_history_store(self.history_store, profile.display_name)

        self._load_reply_history_from_store()
        if profile.id != previous_character_id:
            self.messages = []
            self._collapse_auto_fit_bubble_height()
            self.subtitle_controller.cancel_reply_flow(profile.initial_message)

    def _create_history_store(self, profile: CharacterProfile) -> ChatHistoryStore:
        history_path = self.base_dir / "data" / "chat_history" / f"{profile.id}.jsonl"
        self._migrate_legacy_history(profile, history_path)
        return ChatHistoryStore(history_path, profile.display_name)

    def _create_runtime_event_log(self, profile: CharacterProfile) -> RuntimeEventLog:
        event_path = self.base_dir / "data" / "runtime_events" / f"{profile.id}.jsonl"
        return RuntimeEventLog(event_path)

    def _create_visual_observation_store(self, profile: CharacterProfile) -> VisualObservationStore:
        visual_path = self.base_dir / "data" / "visual_observations" / f"{profile.id}.jsonl"
        return VisualObservationStore(visual_path)

    def _migrate_legacy_history(self, profile: CharacterProfile, history_path: Path) -> None:
        if profile.id != DEFAULT_CHARACTER_ID or history_path.exists():
            return
        legacy_path = self.base_dir / "data" / "chat_history.jsonl"
        if not legacy_path.exists():
            return
        try:
            history_path.parent.mkdir(parents=True, exist_ok=True)
            history_path.write_text(legacy_path.read_text(encoding="utf-8"), encoding="utf-8")
        except OSError as exc:
            print(f"[History] 旧历史迁移失败：{exc}")


def _build_screen_observation_disabled_result() -> AgentResult:
    return AgentResult(
        reply=ChatReply(
            [
                ChatSegment(
                    text="画面を見る設定がオフになっているよ。設定で許可してから、もう一度試して。",
                    tone="请求",
                    translation="获取屏幕信息现在是关闭的。请在设置里允许后再试。",
                    portrait="伸手命令",
                )
            ]
        )
    )


def _build_screen_observation_failed_result(message: str) -> AgentResult:
    return AgentResult(
        reply=ChatReply(
            [
                ChatSegment(
                    text="今は画面を取得できなかったみたい。権限や表示環境を確認して。",
                    tone="困惑",
                    translation=f"这次没能获取屏幕截图：{message}",
                    portrait="张嘴疑问",
                )
            ]
        )
    )


def _first_screen_observation_request(result: AgentResult) -> AgentAction | None:
    for action in result.actions:
        if action.type == SCREEN_OBSERVATION_REQUEST_ACTION:
            return action
    return None


def _add_visual_context_to_messages(
    messages: list[dict[str, Any]],
    *,
    user_text: str,
    store: VisualObservationStore | None,
    has_current_image: bool,
) -> list[dict[str, Any]]:
    if store is None or has_current_image:
        return messages

    if should_inject_visual_context(user_text):
        records = store.recent(limit=3)
    else:
        records = store.recent(limit=1, since_minutes=VISUAL_OBSERVATION_RECENT_MINUTES)
    context_message = build_visual_context_message(user_text, records)
    if context_message is None:
        return messages

    return [*messages[:-1], context_message, messages[-1]]


def _add_runtime_event_context_to_messages(
    messages: list[dict[str, Any]],
    events: list[RuntimeEvent],
) -> list[dict[str, Any]]:
    """把待注入的运行时事件合并成一条 system 上下文，插在历史与当前用户消息之间。

    与 _add_visual_context_to_messages 同模式：只作用于本次 request_messages，
    不修改 self.messages、不写入 chat_history。无事件或消息为空时原样返回。
    """
    if not events or not messages:
        return messages
    context_message = build_runtime_event_context_message(events)
    if context_message is None:
        return messages
    return [*messages[:-1], context_message, messages[-1]]


def _build_proactive_visual_observation_jobs(event: AgentEvent) -> list[VisualObservationJob]:
    screen_contexts = event.payload.get("screen_contexts")
    if not isinstance(screen_contexts, list) or not screen_contexts:
        return []
    return [
        VisualObservationJob(
            id=generate_visual_observation_id(),
            source="proactive_screen_context",
            user_text="主动关怀屏幕上下文批次",
            screen_contexts=[
                dict(context)
                for context in screen_contexts
                if isinstance(context, dict)
            ],
        )
    ]


def _build_proactive_recent_conversation(
    messages: list[dict[str, Any]],
    *,
    limit: int = PROACTIVE_RECENT_CONVERSATION_LIMIT,
    content_limit: int = PROACTIVE_RECENT_CONVERSATION_CONTENT_LIMIT,
) -> list[dict[str, str]]:
    """为主动事件提取近期用户/助手对话，帮助模型理解一段时间内的语境。"""
    recent: list[dict[str, str]] = []
    for message in messages:
        role = str(message.get("role", "")).strip()
        if role not in {"user", "assistant"}:
            continue
        content = _proactive_recent_conversation_content(message.get("content"))
        if not content or content == PROACTIVE_SCREEN_CONTEXT_HISTORY_MARKER:
            continue
        recent.append(
            {
                "role": role,
                "content": _truncate_proactive_recent_conversation_content(
                    content,
                    content_limit,
                ),
            }
        )
    return recent[-limit:]


def _build_proactive_recent_conversation_for_window(
    window: Any,
    *,
    limit: int = PROACTIVE_RECENT_CONVERSATION_LIMIT,
    content_limit: int = PROACTIVE_RECENT_CONVERSATION_CONTENT_LIMIT,
) -> list[dict[str, str]]:
    """主动事件优先读取持久化历史，避免重启后丢失近期语境。"""
    history_entries = _load_proactive_history_entries(window)
    if history_entries:
        return _build_proactive_recent_conversation_from_history_entries(
            history_entries,
            subtitle_language=str(getattr(window, "subtitle_language", SUBTITLE_LANGUAGE_ZH)),
            limit=limit,
            content_limit=content_limit,
        )
    return _build_proactive_recent_conversation(
        getattr(window, "messages", []),
        limit=limit,
        content_limit=content_limit,
    )


def _load_proactive_history_entries(window: Any) -> list[ChatHistoryEntry]:
    history_store = getattr(window, "history_store", None)
    if history_store is None or not hasattr(history_store, "load"):
        return []
    try:
        entries = history_store.load()
    except OSError as exc:
        debug_log("ProactiveCare", "读取近期聊天历史失败", {"error": str(exc)})
        return []
    return [entry for entry in entries if isinstance(entry, ChatHistoryEntry)]


def _build_proactive_recent_conversation_from_history_entries(
    entries: list[ChatHistoryEntry],
    *,
    subtitle_language: str,
    limit: int = PROACTIVE_RECENT_CONVERSATION_LIMIT,
    content_limit: int = PROACTIVE_RECENT_CONVERSATION_CONTENT_LIMIT,
) -> list[dict[str, str]]:
    messages: list[dict[str, Any]] = []
    for entry in entries:
        if entry.role not in {"user", "assistant"}:
            continue
        messages.append(
            {
                "role": entry.role,
                "content": entry.display_content(subtitle_language),
            }
        )
    return _build_proactive_recent_conversation(
        messages,
        limit=limit,
        content_limit=content_limit,
    )


def _proactive_recent_conversation_content(content: Any) -> str:
    if isinstance(content, str):
        return " ".join(content.split())
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict):
                text = item.get("text")
                if isinstance(text, str):
                    parts.append(text)
        return " ".join(" ".join(parts).split())
    if isinstance(content, dict):
        text = content.get("text")
        if isinstance(text, str):
            return " ".join(text.split())
    return ""


def _truncate_proactive_recent_conversation_content(content: str, limit: int) -> str:
    if len(content) <= limit:
        return content
    return content[: max(0, limit - 1)].rstrip() + "…"


def _last_user_message_index(messages: list[dict[str, Any]]) -> int | None:
    for index in range(len(messages) - 1, -1, -1):
        if messages[index].get("role") == "user":
            return index
    return None


def _reply_history_segments_from_entries(entries: list[ChatHistoryEntry]) -> list[ChatSegment]:
    segments: list[ChatSegment] = []
    for entry in entries:
        if entry.role != "assistant" or not entry.content.strip():
            continue
        recovered = parse_chat_reply_result(entry.content.strip())
        if not recovered.needs_retry and len(recovered.reply.segments) > 1:
            segments.extend(recovered.reply.segments)
            continue
        tone = entry.tone.strip()
        if tone:
            segment = ChatSegment(
                entry.content.strip(),
                tone,
                entry.translation.strip(),
                entry.portrait.strip(),
            )
        else:
            segment = ChatSegment(
                entry.content.strip(),
                translation=entry.translation.strip(),
                portrait=entry.portrait.strip(),
            )
        segments.append(segment)
    return segments


def _compact_tts_error(message: str, limit: int = 160) -> str:
    compacted = " ".join(str(message).split())
    if len(compacted) <= limit:
        return compacted
    return compacted[: max(0, limit - 1)].rstrip() + "…"


def _parse_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    normalized = str(value).strip().lower()
    if normalized in {"1", "true", "yes", "on", "enabled"}:
        return True
    if normalized in {"0", "false", "no", "off", "disabled"}:
        return False
    return default


def _is_screen_change_event(event: object) -> bool:
    """兼容旧版 Qt：缺少 ScreenChangeInternal 枚举时直接忽略。"""

    screen_change_type = getattr(QEvent.Type, "ScreenChangeInternal", None)
    event_type = getattr(event, "type", None)
    return screen_change_type is not None and callable(event_type) and event_type() == screen_change_type


def _configure_reply_history_panel(panel: QFrame) -> None:
    panel.setObjectName("replyHistoryPanel")
    panel.setFixedSize(REPLY_HISTORY_PANEL_WIDTH, REPLY_HISTORY_PANEL_HEIGHT)


def _configure_reply_history_button(button: QToolButton, *, text: str, tooltip: str) -> None:
    button.setObjectName("replyHistoryButton")
    button.setText(text)
    button.setFixedSize(REPLY_HISTORY_BUTTON_SIZE, REPLY_HISTORY_BUTTON_SIZE)
    button.setToolTip(tooltip)
    button.setAutoRaise(False)


def _build_status_tray_icon(color_text: str) -> QIcon:
    color = QColor(color_text)
    if not color.isValid():
        color = QColor(DEFAULT_THEME_SETTINGS.primary_color)

    pixmap = QPixmap(32, 32)
    pixmap.fill(Qt.GlobalColor.transparent)

    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(color)
    painter.drawEllipse(3, 3, 26, 26)
    painter.setPen(QColor("#ffffff"))
    painter.setFont(_rounded_chinese_font(18, QFont.Weight.ExtraBold))
    painter.drawText(pixmap.rect(), Qt.AlignmentFlag.AlignCenter, "S")
    painter.end()

    return QIcon(pixmap)


def _should_keep_tts_local_service(old_provider: TTSProvider, new_provider: TTSProvider) -> bool:
    old_settings = getattr(old_provider, "settings", None)
    new_settings = getattr(new_provider, "settings", None)
    if not isinstance(old_settings, GPTSoVITSTTSSettings) or not isinstance(
        new_settings,
        GPTSoVITSTTSSettings,
    ):
        return False
    if not old_settings.enabled or not new_settings.enabled:
        return False
    if old_settings.provider != new_settings.provider:
        return False
    if old_settings.api_url.strip() != new_settings.api_url.strip():
        return False
    if old_settings.work_dir is None or new_settings.work_dir is None:
        return False
    return (
        _same_optional_path(old_settings.work_dir, new_settings.work_dir)
        and _same_optional_path(old_settings.python_path, new_settings.python_path)
        and _same_optional_path(old_settings.tts_config_path, new_settings.tts_config_path)
    )


def _same_optional_path(left: Path | None, right: Path | None) -> bool:
    if left is None or right is None:
        return left is None and right is None
    return left.resolve() == right.resolve()


def _should_write_character_theme(theme_write_mode: object, profile: CharacterProfile) -> bool:
    return theme_write_mode in {"manual", "ai"}


def _mark_dialog_always_on_top(window) -> None:  # type: ignore[no-untyped-def]
    # 给设置/历史窗口加置顶标志，使其与桌宠卡片同处置顶层、再靠 raise 保持在最上。
    set_flag = getattr(window, "setWindowFlag", None)
    if callable(set_flag):
        set_flag(Qt.WindowType.WindowStaysOnTopHint, True)


def _set_macos_window_topmost(window_id: int, enabled: bool) -> None:
    """同步 macOS NSWindow 层级，确保置顶窗口能跟随当前 Space。"""

    import ctypes
    import ctypes.util

    objc = ctypes.CDLL(ctypes.util.find_library("objc") or "/usr/lib/libobjc.A.dylib")
    sel_register_name = objc.sel_registerName
    sel_register_name.argtypes = [ctypes.c_char_p]
    sel_register_name.restype = ctypes.c_void_p

    def selector(name: bytes) -> int:
        return int(sel_register_name(name))

    def message(restype: object, *argtypes: object) -> object:
        return ctypes.CFUNCTYPE(restype, ctypes.c_void_p, ctypes.c_void_p, *argtypes)(
            ("objc_msgSend", objc)
        )

    send_bool = message(ctypes.c_bool, ctypes.c_void_p)
    send_ptr = message(ctypes.c_void_p)
    send_level = message(None, ctypes.c_long)
    send_hides_on_deactivate = message(None, ctypes.c_bool)
    send_ulong = message(ctypes.c_ulong)
    send_collection = message(None, ctypes.c_ulong)

    obj = ctypes.c_void_p(window_id)
    sel_window = selector(b"window")
    sel_responds_to_selector = selector(b"respondsToSelector:")
    if send_bool(obj, ctypes.c_void_p(sel_responds_to_selector), ctypes.c_void_p(sel_window)):
        ns_window = send_ptr(obj, ctypes.c_void_p(sel_window))
        if not ns_window:
            return
    else:
        ns_window = window_id

    ns_window_ptr = ctypes.c_void_p(int(ns_window))
    ns_window_collection_behavior_can_join_all_spaces = 1 << 0
    ns_window_collection_behavior_move_to_active_space = 1 << 1
    ns_window_collection_behavior_full_screen_auxiliary = 1 << 8
    ns_floating_window_level = 3
    ns_modal_panel_window_level = 8

    level = ns_modal_panel_window_level if enabled else ns_floating_window_level
    send_level(ns_window_ptr, ctypes.c_void_p(selector(b"setLevel:")), level)

    sel_set_hides_on_deactivate = selector(b"setHidesOnDeactivate:")
    if send_bool(
        ns_window_ptr,
        ctypes.c_void_p(sel_responds_to_selector),
        ctypes.c_void_p(sel_set_hides_on_deactivate),
    ):
        send_hides_on_deactivate(
            ns_window_ptr,
            ctypes.c_void_p(sel_set_hides_on_deactivate),
            not enabled,
        )

    collection_behavior = int(send_ulong(ns_window_ptr, ctypes.c_void_p(selector(b"collectionBehavior"))))
    if enabled:
        collection_behavior |= (
            ns_window_collection_behavior_can_join_all_spaces
            | ns_window_collection_behavior_full_screen_auxiliary
        )
        collection_behavior &= ~ns_window_collection_behavior_move_to_active_space
    else:
        collection_behavior &= ~ns_window_collection_behavior_can_join_all_spaces
        collection_behavior |= ns_window_collection_behavior_move_to_active_space
    send_collection(
        ns_window_ptr,
        ctypes.c_void_p(selector(b"setCollectionBehavior:")),
        collection_behavior,
    )
