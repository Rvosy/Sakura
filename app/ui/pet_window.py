from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import time
from datetime import datetime, timedelta
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
    QRegion,
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
from app.core.cancellation import CancellationToken, OperationCancelled
from app.core.debug_log import debug_log, summarize_messages
from app.config.settings_service import BackchannelSettings, BubbleSettings, StartupSettings
from app.backchannel.audio_cache import BackchannelAudioCache, voice_fingerprint
from app.backchannel.classifier import RuleClassifier
from app.backchannel.controller import BackchannelController
from app.backchannel.hybrid_classifier import HybridBackchannelClassifier
from app.backchannel.manifest import BackchannelManifestError, load_backchannel_manifest
from app.backchannel.eval_log import BackchannelEvalLogger
from app.backchannel.models import BackchannelLabel, BackchannelManifest
from app.backchannel.resolver import BackchannelChoice
from app.core.interaction import clear_interaction_id, set_interaction_id
from app.storage.atomic import atomic_write_text
from app.storage.paths import StoragePaths
from app.plugins.manager import (
    PLUGIN_EVENT_AI_MESSAGE,
    PLUGIN_EVENT_APP_START,
    PLUGIN_EVENT_CHARACTER_LOADED,
    PLUGIN_EVENT_TTS_END,
    PLUGIN_EVENT_TTS_START,
    PLUGIN_EVENT_USER_MESSAGE,
)
from app.plugins.events import (
    EVENT_APP_CLOSING,
    EVENT_APP_STARTED,
    EVENT_CHAT_MESSAGE_RECEIVED,
    EVENT_CHAT_MESSAGE_SENT,
    EVENT_TTS_FINISHED,
    EVENT_TTS_STARTED,
)
from app.ui.state import PetUiState, PetUiStateStore
from app.platforms.launch_at_login import (
    LaunchAtLoginError,
    set_launch_at_login_enabled,
)
from app.ui.history_window import HistoryWindow
from app.ui.log_window import RuntimeLogWindow
from app.agent.screen_awareness import (
    SCREEN_AWARENESS_CONTEXT_HISTORY_MARKER,
    SCREEN_AWARENESS_IMAGE_DETAIL,
    SCREEN_AWARENESS_TIMER_DUE_GRACE_SECONDS,
    SCREEN_AWARENESS_TIMER_POLL_INTERVAL_MS,
    ScreenAwarenessSettings,
)
from app.agent.screen_observation import (
    CapturedScreenImage,
    SCREEN_OBSERVATION_HISTORY_MARKER,
    SCREEN_OBSERVATION_MAX_EDGE,
    ScreenObservation,
    append_manual_observation_marker,
    append_observation_marker,
    build_screen_observation_from_image,
    build_screen_observation_user_message,
    capture_screen_image,
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
from app.voice.factory import create_tts_provider
from app.voice.tts_settings import DEFAULT_GPT_SOVITS_API_URL, GPTSoVITSTTSSettings, TTSConfigError
from app.voice.tts import (
    NullTTSProvider,
    TTSPreparedAudio,
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
SPEAKING_STATE_TIMEOUT_MS = 45_000
THREAD_SHUTDOWN_WAIT_MS = 1_000
TRANSIENT_PROGRESS_MESSAGE_KEY = "_sakura_transient_progress"
SUBTITLE_LANGUAGE_JA = "ja"
SUBTITLE_LANGUAGE_ZH = "zh"
MANUAL_SCREENSHOT_DEFAULT_TEXT = "请根据我框选的截图继续对话。"
_UI_ASSETS_DIR = Path(__file__).with_name("assets")
_SCREENSHOT_ICON_PATH = _UI_ASSETS_DIR / "screenshot-select.svg"
_SCREENSHOT_ATTACHED_ICON_PATH = _UI_ASSETS_DIR / "screenshot-attached.svg"
SCREEN_AWARENESS_RECENT_CONVERSATION_LIMIT = 12
SCREEN_AWARENESS_RECENT_CONVERSATION_CONTENT_LIMIT = 800
SCREEN_AWARENESS_RECENT_CONVERSATION_SUMMARY_HINT = (
    "这些 recent_conversation 消息用于理解这段时间发生了什么、用户当前阶段和 Sakura "
    "刚刚说过什么；不要逐字复述，应结合屏幕变化找话题，并避免连续重复同一类话题或休息提醒。"
)
SCREEN_AWARENESS_EVENT_TYPE = "screen_awareness_check"
LEGACY_PROACTIVE_EVENT_TYPE = "proactive_check"
SCREEN_AWARENESS_VISUAL_SOURCE = "screen_awareness_context"
SCREEN_AWARENESS_STATE_FILE = "screen_awareness_state.json"
SCREEN_AWARENESS_HEALTH_TOPIC = "health_reminder"
SCREEN_AWARENESS_HEALTH_KEYWORDS = (
    "休息",
    "休憩",
    "睡觉",
    "睡眠",
    "睡",
    "熬夜",
    "夜更かし",
    "寝",
    "眠",
    "喝水",
    "水分",
    "补水",
)

# 兼容旧测试和旧插件引用；新代码请使用 SCREEN_AWARENESS_*。
PROACTIVE_RECENT_CONVERSATION_LIMIT = SCREEN_AWARENESS_RECENT_CONVERSATION_LIMIT
PROACTIVE_RECENT_CONVERSATION_CONTENT_LIMIT = SCREEN_AWARENESS_RECENT_CONVERSATION_CONTENT_LIMIT
PROACTIVE_RECENT_CONVERSATION_SUMMARY_HINT = SCREEN_AWARENESS_RECENT_CONVERSATION_SUMMARY_HINT
PROACTIVE_SCREEN_CONTEXT_HISTORY_MARKER = SCREEN_AWARENESS_CONTEXT_HISTORY_MARKER
REPLY_HISTORY_PANEL_WIDTH = 34
REPLY_HISTORY_PANEL_HEIGHT = 70
REPLY_HISTORY_BUTTON_SIZE = 30
REPLY_HISTORY_PREVIOUS_SYMBOL = "▲"
REPLY_HISTORY_NEXT_SYMBOL = "▼"
DEFAULT_STAGE_WIDTH = 860
DEFAULT_STAGE_HEIGHT = 640
# 立绘缩放时碰撞箱高度下限：底部 UI 区（气泡 128 + 输入框 52 + 间距 94 = 274px）
# 加上立绘顶部约 146px 可见区，合计 ~420px。
MIN_STAGE_HEIGHT = 420
BACKCHANNEL_AUDIO_PREPARE_LIMIT = 16


def _without_transient_progress_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        message
        for message in messages
        if not message.get(TRANSIENT_PROGRESS_MESSAGE_KEY)
    ]


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
        self._cancel_token = CancellationToken()

    @Slot()
    def cancel(self) -> None:
        self._cancel_token.cancel()

    @Slot()
    def run(self) -> None:
        try:
            self._cancel_token.throw_if_cancelled()
            ensure_ready = getattr(self.provider, "ensure_ready", None)
            if not callable(ensure_ready):
                return
            debug_log("TTS", "开始后台预热 TTS 服务", {"provider": type(self.provider).__name__})
            ok, message = ensure_ready()
            self._cancel_token.throw_if_cancelled()
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
        except OperationCancelled:
            debug_log("TTS", "后台预热 TTS 服务已取消", {"provider": type(self.provider).__name__})
        except Exception as exc:  # noqa: BLE001
            if self._cancel_token.is_cancelled():
                debug_log("TTS", "后台预热 TTS 服务已取消", {"provider": type(self.provider).__name__})
                return
            message = f"TTS 服务预热失败：{exc}"
            debug_log(
                "TTS",
                "后台预热 TTS 服务异常",
                {"provider": type(self.provider).__name__, "error": str(exc)},
            )
            self.failed.emit(message)
        finally:
            self.finished.emit()


class ScreenObservationEncodeWorker(QObject):
    """后台压缩屏幕截图，避免 QImage 缩放和 JPEG 编码阻塞 UI。"""

    finished = Signal(object, object)
    failed = Signal(object, str)
    cancelled = Signal(object)

    def __init__(self, captured: CapturedScreenImage, context: dict[str, Any]) -> None:
        super().__init__()
        self.captured = captured
        self.context = context
        self._cancel_token = CancellationToken()

    @Slot()
    def cancel(self) -> None:
        self._cancel_token.cancel()

    @Slot()
    def run(self) -> None:
        try:
            self._cancel_token.throw_if_cancelled()
            max_edge = _screen_observation_max_edge_from_context(self.context)
            if self.context.get("preserve_original_resolution") is True:
                max_edge = max(1, self.captured.image.width(), self.captured.image.height())
            observation = build_screen_observation_from_image(
                self.captured,
                max_edge=max_edge,
            )
            self._cancel_token.throw_if_cancelled()
        except OperationCancelled:
            self.cancelled.emit(self.context)
            return
        except Exception as exc:  # noqa: BLE001
            if self._cancel_token.is_cancelled():
                self.cancelled.emit(self.context)
                return
            self.failed.emit(self.context, str(exc))
            return
        self.finished.emit(self.context, observation)


def _screen_observation_max_edge_from_context(context: dict[str, Any]) -> int:
    value = context.get("max_edge")
    try:
        max_edge = int(value)
    except (TypeError, ValueError):
        return SCREEN_OBSERVATION_MAX_EDGE
    return max(1, max_edge)


class PetWindow(QWidget):
    memory_status_changed = Signal(str, str)
    # 插件请求把文本填入输入框；用信号 marshal 回 UI 线程（ASR 等可能在后台线程触发）。
    plugin_input_text_requested = Signal(str)

    def __init__(
        self,
        context: AppContext,
    ) -> None:
        super().__init__()
        # 插件填充输入框的信号在此连接，确保后台线程触发时 marshal 回 UI 线程。
        self.plugin_input_text_requested.connect(self._apply_plugin_input_text)
        self.context = context
        self.base_dir = context.base_dir
        self.startup_initializing = context.startup_initializing
        self.deferred_startup_thread: QThread | None = None
        self.deferred_startup_worker: QObject | None = None
        self.tts_ready_warmup_thread: QThread | None = None
        self.tts_ready_warmup_worker: QObject | None = None
        self.screen_observation_encode_thread: QThread | None = None
        self.screen_observation_encode_worker: QObject | None = None
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
        self._wire_plugin_service_backends()
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
        self.screen_awareness_settings = getattr(context, "screen_awareness_settings", None)
        if self.screen_awareness_settings is None:
            self.screen_awareness_settings = context.proactive_care_settings
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
        self._shutdown_in_progress = False
        self._shutdown_lingering_threads: list[tuple[QThread, QObject | None]] = []
        self._retired_qobject_wrappers: list[QObject] = []
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
        self.last_screen_awareness_at: float | None = None
        self.last_screen_awareness_context_at: float | None = None
        self.screen_awareness_context_batch_started_at: float | None = None
        self.screen_awareness_contexts: list[dict[str, Any]] = []
        self.screen_awareness_context_dropped_count = 0
        self.interaction_sequence = 0
        self.active_interaction_id = ""
        self.active_interaction_started_at: float | None = None
        self.active_interaction_last_at: float | None = None
        # UI 统一状态源：thinking/streaming/speaking/error 的唯一权威
        self.ui_state = PetUiStateStore(self)
        self.ui_state.state_changed.connect(self._handle_ui_state_changed)
        self.speaking_state_watchdog = QTimer(self)
        self.speaking_state_watchdog.setSingleShot(True)
        self.speaking_state_watchdog.setInterval(SPEAKING_STATE_TIMEOUT_MS)
        self.speaking_state_watchdog.timeout.connect(self._handle_speaking_state_timeout)
        self.reply_history_segments: list[ChatSegment] = []
        self.reply_history_index: int | None = None
        self.reply_history_review_active = False
        self.reminder_timer = QTimer(self)
        self.reminder_timer.setInterval(REMINDER_CHECK_INTERVAL_MS)
        self.reminder_timer.timeout.connect(self._check_due_reminders)
        self.screen_awareness_timer = QTimer(self)
        self.screen_awareness_timer.setInterval(SCREEN_AWARENESS_TIMER_POLL_INTERVAL_MS)
        self.screen_awareness_timer.timeout.connect(self._check_screen_awareness)
        if not self.startup_initializing:
            self.reminder_timer.start()
            self._sync_screen_awareness_timer()
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
                "screen_awareness": self.screen_awareness_settings,
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

        # 舞台调试可视化层:开发者选项(设置页)或 env SAKURA_STAGE_DEBUG=1 启用,默认完全惰性。
        # 画窗口/布局/实际立绘三框 + DPR 等数值,用于诊断布局/碰撞与 mac HiDPI 逻辑/物理错配。
        self._stage_debug_overlay = None
        self._apply_stage_debug_overlay(
            self.debug_log_settings.stage_debug_overlay
            or bool(os.environ.get("SAKURA_STAGE_DEBUG"))
        )
        # 舞台碰撞遮罩:开发者选项,把命中区裁到内容矩形并集(立绘四周空白点击穿透);默认关。
        self._stage_collision_mask_enabled = False
        self._apply_stage_collision_mask(
            self.debug_log_settings.stage_collision_mask
            or bool(os.environ.get("SAKURA_STAGE_MASK"))
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
            self._emit_tts_start_plugin_event,
            self._emit_tts_end_plugin_event,
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
        # 本地快速接话层:等待主 LLM 期间显示一句角色化过渡反应(默认关闭)。
        self.backchannel_settings = self.settings_service.load_backchannel_settings()
        self.backchannel_manifest: BackchannelManifest | None = None
        self._backchannel_prepared_audio: dict[tuple[str, str, str], TTSPreparedAudio] = {}
        self._active_backchannel_audio: TTSPreparedAudio | None = None
        self.backchannel_eval_logger = BackchannelEvalLogger(
            self.base_dir, enabled=self.debug_log_settings.enabled
        )
        self.backchannel_controller = BackchannelController(
            self._create_backchannel_classifier(self.backchannel_settings),
            self._display_backchannel,
            settings=self.backchannel_settings,
            on_classified=self._log_backchannel_classification,
            parent=self,
        )
        self._load_backchannel_manifest_for(self.character_profile)
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
            self.renderer_manager = None
        else:
            # 插件化角色渲染后端依赖已加载插件；非首帧启动路径可立即初始化。
            self.renderer_manager = self._activate_renderer_manager()

        application = QApplication.instance()
        if application is not None:
            application.aboutToQuit.connect(self.close_external_tools)
            if sys.platform == "darwin":
                application.installEventFilter(self)

    @property
    def proactive_care_settings(self) -> Any:
        """兼容旧属性；新代码请使用 screen_awareness_settings。"""
        return self.screen_awareness_settings

    @proactive_care_settings.setter
    def proactive_care_settings(self, value: Any) -> None:
        self.screen_awareness_settings = value

    @property
    def proactive_care_timer(self) -> QTimer:
        return self.screen_awareness_timer

    @proactive_care_timer.setter
    def proactive_care_timer(self, value: QTimer) -> None:
        self.screen_awareness_timer = value

    @property
    def last_proactive_care_at(self) -> float | None:
        return self.last_screen_awareness_at

    @last_proactive_care_at.setter
    def last_proactive_care_at(self, value: float | None) -> None:
        self.last_screen_awareness_at = value

    @property
    def last_proactive_screen_context_at(self) -> float | None:
        return self.last_screen_awareness_context_at

    @last_proactive_screen_context_at.setter
    def last_proactive_screen_context_at(self, value: float | None) -> None:
        self.last_screen_awareness_context_at = value

    @property
    def proactive_screen_context_batch_started_at(self) -> float | None:
        return self.screen_awareness_context_batch_started_at

    @proactive_screen_context_batch_started_at.setter
    def proactive_screen_context_batch_started_at(self, value: float | None) -> None:
        self.screen_awareness_context_batch_started_at = value

    @property
    def proactive_screen_contexts(self) -> list[dict[str, Any]]:
        return self.screen_awareness_contexts

    @proactive_screen_contexts.setter
    def proactive_screen_contexts(self, value: list[dict[str, Any]]) -> None:
        self.screen_awareness_contexts = value

    @property
    def proactive_screen_context_dropped_count(self) -> int:
        return self.screen_awareness_context_dropped_count

    @proactive_screen_context_dropped_count.setter
    def proactive_screen_context_dropped_count(self, value: int) -> None:
        self.screen_awareness_context_dropped_count = value

    def resizeEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        super().resizeEvent(event)
        self._layout_stage()
        self._sync_renderer_overlay_geometry()

    def moveEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        super().moveEvent(event)
        # 气泡/输入栏是子控件会自动跟随；独立渲染器窗口需要同步屏幕坐标。
        self._sync_renderer_overlay_geometry()

    def showEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        super().showEvent(event)
        # 子控件随主窗口显示；此处只需把它们摆到位并启动动画/自动隐藏。
        self._layout_stage()
        self._sync_renderer_overlay_geometry()
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

    def _init_renderer_manager(self) -> Any:
        """初始化插件化角色渲染后端；失败返回 None（沿用现有立绘显示）。"""
        try:
            from app.renderers import RendererManager

            collect_renderers = getattr(self.plugin_manager, "collect_renderers", None)
            renderer_contributions = collect_renderers() if callable(collect_renderers) else []
            manager = RendererManager(
                settings_service=self.settings_service,
                character_profile=self.character_profile,
                event_bus=getattr(self.plugin_manager, "event_bus", None),
                parent_window=self,
                renderer_contributions=renderer_contributions,
            )
            manager.select_and_init()
            if manager.is_overlay_active:
                manager.load_character()
                if getattr(manager, "replaces_default_portrait", False):
                    self._set_portrait_overlay_suppressed(True)
                self._sync_renderer_overlay_geometry(manager=manager)
                manager.show()
                debug_log(
                    "RendererManager",
                    "已启用独立角色渲染窗口",
                    {"renderer": manager.active_renderer_name},
                )
            return manager
        except Exception as exc:  # noqa: BLE001 — 渲染后端初始化失败不得影响桌宠启动
            debug_log("RendererManager", "初始化失败，回退现有显示", {"error": str(exc)})
            return None

    def _activate_renderer_manager(self) -> Any:
        """按当前插件集合重新创建角色渲染后端。"""
        self._close_renderer_manager()
        manager = self._init_renderer_manager()
        self.renderer_manager = manager
        self._start_gaze_tracking()
        return manager

    def _close_renderer_manager(self) -> None:
        self._stop_gaze_tracking()
        manager = getattr(self, "renderer_manager", None)
        if manager is None:
            return
        try:
            manager.close()
        except Exception as exc:  # noqa: BLE001
            debug_log("RendererManager", "关闭失败", {"error": str(exc)})
        finally:
            self.renderer_manager = None
            self._set_portrait_overlay_suppressed(False)

    def _start_gaze_tracking(self) -> None:
        """overlay 渲染器激活时，周期采样鼠标位置驱动角色视线追踪。

        仅在独立渲染器（如 MMD）接管显示时启用；默认 PNG 立绘无需追踪，
        避免无意义的定时器开销。
        """
        manager = getattr(self, "renderer_manager", None)
        if manager is None or not getattr(manager, "is_overlay_active", False):
            return
        timer = getattr(self, "_gaze_timer", None)
        if timer is None:
            timer = QTimer(self)
            timer.setInterval(50)  # ~20fps，顺滑且开销可控
            timer.timeout.connect(self._on_gaze_tick)
            self._gaze_timer = timer
        if not timer.isActive():
            timer.start()

    def _stop_gaze_tracking(self) -> None:
        timer = getattr(self, "_gaze_timer", None)
        if timer is not None and timer.isActive():
            timer.stop()

    def _on_gaze_tick(self) -> None:
        """把鼠标全局坐标归一化为角色坐标系 (x,y)∈[-1,1] 后驱动视线追踪。"""
        manager = getattr(self, "renderer_manager", None)
        if manager is None or not getattr(manager, "is_overlay_active", False):
            return
        try:
            layout = self._compute_pet_layout()
            px, py, pw, ph = layout.portrait_rect
            if pw <= 0 or ph <= 0:
                return
            top_left = self.mapToGlobal(QPoint(px, py))
            center_x = top_left.x() + pw / 2.0
            center_y = top_left.y() + ph / 2.0
            cursor = QCursor.pos()
            # 以立绘矩形半宽/半高为基准归一化：x>0 鼠标在右、y>0 鼠标在下。
            nx = max(-1.0, min(1.0, (cursor.x() - center_x) / (pw / 2.0)))
            ny = max(-1.0, min(1.0, (cursor.y() - center_y) / (ph / 2.0)))
            # 节流：变化过小不下发，减少 runJavaScript 频次。
            last = getattr(self, "_gaze_last", None)
            if last is not None and abs(nx - last[0]) < 0.02 and abs(ny - last[1]) < 0.02:
                return
            self._gaze_last = (nx, ny)
            manager.look_at(nx, ny)
        except Exception as exc:  # noqa: BLE001 — 视线追踪异常不得影响主窗口
            debug_log("RendererManager", "视线追踪更新失败", {"error": str(exc)})

    def _sync_renderer_overlay_geometry(
        self,
        manager: Any | None = None,
        layout: PetLayout | None = None,
    ) -> None:
        """把独立渲染窗口贴到当前立绘矩形，避免覆盖气泡和输入栏。"""
        manager = manager or getattr(self, "renderer_manager", None)
        if manager is None or not getattr(manager, "is_overlay_active", False):
            return
        if layout is None:
            layout = self._compute_pet_layout()
        px, py, pw, ph = layout.portrait_rect
        top_left = self.mapToGlobal(QPoint(px, py))
        try:
            manager.set_geometry(top_left.x(), top_left.y(), pw, ph)
            manager.stack_below(self, topmost=bool(getattr(self, "always_on_top_enabled", False)))
        except Exception as exc:  # noqa: BLE001 — 渲染后端异常不得影响主窗口
            debug_log("RendererManager", "同步渲染窗口几何失败", {"error": str(exc)})

    def _set_portrait_overlay_suppressed(self, suppressed: bool) -> None:
        """独立渲染器接管角色显示时隐藏原 PNG 立绘。"""
        for widget_name in ("label", "portrait_transition_label"):
            widget = getattr(self, widget_name, None)
            if widget is None:
                continue
            if suppressed:
                widget.hide()
            elif widget_name == "label":
                widget.show()

    def _resuppress_portrait_if_renderer_active(self) -> None:
        manager = getattr(self, "renderer_manager", None)
        if manager is not None and getattr(manager, "replaces_default_portrait", False):
            self._set_portrait_overlay_suppressed(True)

    def _maybe_resuppress_portrait(self) -> None:
        suppress = getattr(self, "_resuppress_portrait_if_renderer_active", None)
        if callable(suppress):
            suppress()

    def closeEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        self.close_external_tools()
        super().closeEvent(event)

    @Slot()
    def close_external_tools(self) -> None:
        if getattr(self, "_shutdown_in_progress", False):
            return
        self._shutdown_in_progress = True
        self._emit_app_closed_event()
        self._stop_speaking_state_watchdog()
        self.messages = _without_transient_progress_messages(self.messages)
        subtitle_controller = getattr(self, "subtitle_controller", None)
        if subtitle_controller is not None:
            subtitle_controller.cancel_reply_flow()
        backchannel_controller = getattr(self, "backchannel_controller", None)
        shutdown_backchannel = getattr(backchannel_controller, "shutdown", None)
        if callable(shutdown_backchannel) and not shutdown_backchannel(
            THREAD_SHUTDOWN_WAIT_MS / 1000
        ):
            debug_log(
                "PetWindow",
                "接话分类线程未在退出等待时间内结束，将在后台自然完成",
                {"wait_ms": THREAD_SHUTDOWN_WAIT_MS},
            )
        self._shutdown_qthread("worker_thread", "worker")
        self._shutdown_qthread("memory_curation_thread", "memory_curation_worker")
        self._shutdown_qthread("deferred_startup_thread", "deferred_startup_worker")
        self._shutdown_qthread("tts_ready_warmup_thread", "tts_ready_warmup_worker")
        self._shutdown_qthread("screen_observation_encode_thread", "screen_observation_encode_worker")
        self.close_tts_tools()
        self.close_mcp_tools()
        self.close_plugins()
        self._close_renderer_manager()

    def _shutdown_qthread(self, thread_attr: str, worker_attr: str) -> None:
        thread = getattr(self, thread_attr, None)
        worker = getattr(self, worker_attr, None)
        if thread is None:
            setattr(self, worker_attr, None)
            return
        debug_log("PetWindow", "准备关闭后台线程", {"thread": thread_attr})
        try:
            cancel = getattr(worker, "cancel", None)
            if callable(cancel):
                cancel()
            thread.requestInterruption()
            if thread.isRunning():
                thread.quit()
                if not thread.wait(THREAD_SHUTDOWN_WAIT_MS):
                    debug_log(
                        "PetWindow",
                        "后台线程未在退出等待时间内结束",
                        {"thread": thread_attr, "wait_ms": THREAD_SHUTDOWN_WAIT_MS},
                    )
                    self._keep_shutdown_lingering_thread(thread, worker)
                    return
        except RuntimeError as exc:
            debug_log("PetWindow", "关闭后台线程失败", {"thread": thread_attr, "error": str(exc)})
        setattr(self, thread_attr, None)
        setattr(self, worker_attr, None)

    def _keep_shutdown_lingering_thread(self, thread: QThread, worker: QObject | None) -> None:
        if any(item_thread is thread for item_thread, _worker in self._shutdown_lingering_threads):
            return
        self._shutdown_lingering_threads.append((thread, worker))
        try:
            thread.finished.connect(lambda _thread=thread: self._release_shutdown_lingering_thread(_thread))
        except RuntimeError:
            self._release_shutdown_lingering_thread(thread)

    def _release_shutdown_lingering_thread(self, thread: QThread) -> None:
        remaining: list[tuple[QThread, QObject | None]] = []
        released_worker: QObject | None = None
        for item_thread, item_worker in self._shutdown_lingering_threads:
            if item_thread is thread:
                released_worker = item_worker
                continue
            remaining.append((item_thread, item_worker))
        self._shutdown_lingering_threads = remaining
        if released_worker is not None:
            try:
                released_worker.deleteLater()
            except RuntimeError:
                pass
        try:
            thread.deleteLater()
        except RuntimeError:
            pass

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
        self._emit_plugin_event(
            PLUGIN_EVENT_APP_START,
            {
                "character_id": self.character_profile.id,
                "character_name": self.character_profile.display_name,
                "carryover": carryover or {},
            },
            source="startup",
        )
        self._emit_plugin_event(
            PLUGIN_EVENT_CHARACTER_LOADED,
            {
                "character_id": self.character_profile.id,
                "character_name": self.character_profile.display_name,
                "previous_character_id": "",
            },
            source="startup",
        )
        emit_plugin_bus_event = getattr(self, "_emit_plugin_bus_event", None)
        if callable(emit_plugin_bus_event):
            emit_plugin_bus_event(
                EVENT_APP_STARTED,
                {
                    "character_id": self.character_profile.id,
                    "character_name": self.character_profile.display_name,
                },
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
        emit_plugin_bus_event = getattr(self, "_emit_plugin_bus_event", None)
        if callable(emit_plugin_bus_event):
            emit_plugin_bus_event(
                EVENT_APP_CLOSING,
                {"interrupted_reply": self.worker_thread is not None},
            )

    def _emit_plugin_event(
        self,
        event_type: str,
        payload: dict[str, Any] | None = None,
        *,
        source: str = "pet_window",
    ) -> None:
        manager = getattr(self, "plugin_manager", None)
        emit_event = getattr(manager, "emit_event", None)
        if not callable(emit_event):
            return
        try:
            emit_event(event_type, payload or {}, source=source)
        except Exception as exc:  # noqa: BLE001
            debug_log(
                "PluginManager",
                "插件事件派发失败",
                {"event_type": event_type, "error": str(exc)},
            )

    def _emit_plugin_bus_event(
        self,
        event_name: str,
        payload: dict[str, Any] | None = None,
    ) -> None:
        """向插件事件总线派发事件（与旧 hook 机制并存）。"""
        manager = getattr(self, "plugin_manager", None)
        emit_bus = getattr(manager, "emit_bus_event", None)
        if not callable(emit_bus):
            return
        try:
            emit_bus(event_name, payload or {})
        except Exception as exc:  # noqa: BLE001
            debug_log(
                "PluginEventBus",
                "插件总线事件派发失败",
                {"event": event_name, "error": str(exc)},
            )

    def _emit_tts_start_plugin_event(self, segment: ChatSegment, sequence_id: int) -> None:
        payload = self._tts_plugin_payload(segment, sequence_id)
        self._emit_plugin_event(
            PLUGIN_EVENT_TTS_START,
            payload,
            source="tts",
        )
        emit_plugin_bus_event = getattr(self, "_emit_plugin_bus_event", None)
        if callable(emit_plugin_bus_event):
            emit_plugin_bus_event(EVENT_TTS_STARTED, payload)

    def _emit_tts_end_plugin_event(self, segment: ChatSegment, sequence_id: int) -> None:
        payload = self._tts_plugin_payload(segment, sequence_id)
        self._emit_plugin_event(
            PLUGIN_EVENT_TTS_END,
            payload,
            source="tts",
        )
        emit_plugin_bus_event = getattr(self, "_emit_plugin_bus_event", None)
        if callable(emit_plugin_bus_event):
            emit_plugin_bus_event(EVENT_TTS_FINISHED, payload)

    def _tts_plugin_payload(self, segment: ChatSegment, sequence_id: int) -> dict[str, Any]:
        return {
            "sequence_id": sequence_id,
            "text": segment.text,
            "translation": segment.translation,
            "tone": segment.tone,
            "portrait": segment.portrait,
            "character_id": self.character_profile.id,
        }

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

    def _clear_input_focus_for_pet_interaction(self) -> None:
        """点击/拖动桌宠本体时解除输入框焦点，避免输入栏被误判为常显。"""
        input_edit = getattr(self, "input_edit", None)
        has_focus = getattr(input_edit, "hasFocus", None)
        if not callable(has_focus) or not has_focus():
            return
        clear_focus = getattr(input_edit, "clearFocus", None)
        if callable(clear_focus):
            clear_focus()

    def _handle_mouse_press(self, event: QMouseEvent, source_widget: QWidget | None = None) -> bool:
        if event.button() == Qt.MouseButton.LeftButton:
            self._clear_input_focus_for_pet_interaction()
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

    def _apply_backchannel_settings(self, settings: BackchannelSettings) -> None:
        """应用本地接话层配置，并在启用接话语音时准备缺失音频。

        不在此处预热 TTS:调用方(设置保存/延迟启动)已先行预热。
        服务尚未就绪时预生成会被就绪门控跳过,由预热成功回调
        (_handle_tts_ready_warmup_succeeded)补做首批合成。
        """
        self.backchannel_settings = settings.normalized()
        controller = getattr(self, "backchannel_controller", None)
        if controller is not None:
            set_classifier = getattr(controller, "set_classifier", None)
            if callable(set_classifier):
                set_classifier(self._create_backchannel_classifier(self.backchannel_settings))
            controller.set_settings(self.backchannel_settings)
        if not self._backchannel_tts_wanted():
            # 配置层面不需要接话语音才丢弃缓存;服务暂未就绪不算"不需要"。
            self._discard_backchannel_audio_cache()
            return
        self._prepare_backchannel_audio_cache()

    def _create_backchannel_classifier(
        self,
        settings: BackchannelSettings,
    ) -> RuleClassifier | HybridBackchannelClassifier:
        normalized = settings.normalized()
        if normalized.mode == "hybrid":
            # 首次分类仍由 BackchannelController 的受控后台线程冷加载；不额外启动
            # 无法纳入关闭生命周期的预热线程。
            return HybridBackchannelClassifier.from_model_cache(self.base_dir)
        return RuleClassifier()

    def _log_backchannel_classification(
        self,
        text: str,
        label: "BackchannelLabel | None",
        choice: "BackchannelChoice | None",
    ) -> None:
        logger = getattr(self, "backchannel_eval_logger", None)
        if logger is not None:
            logger.log(text, label, choice, mode=self.backchannel_settings.normalized().mode)

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
        # 正式回复开始:放弃尚未触发的接话(已显示的接话被正式字幕自然覆盖)。
        self._cancel_backchannel()
        # 同轮回复内各段高度延续：不在此重置，避免"段间先缩后扩"产生闪现。
        # 高度重置由 _collapse_auto_fit_bubble_height 在 cancel_reply_flow 前统一处理。
        self.portrait_controller.apply_for_segment(segment)
        maybe_resuppress = getattr(self, "_maybe_resuppress_portrait", None)
        if callable(maybe_resuppress):
            maybe_resuppress()
        self._sync_reply_history_index_for_segment(segment)
        self.ui_state.begin_speaking("reply_segment")
        self._start_speaking_state_watchdog()
        # 新台词开始：保持气泡显示并暂停自动隐藏倒计时。
        controller = getattr(self, "bubble_auto_hide", None)
        if controller is not None:
            controller.notify_speaking()

    def _cancel_backchannel(self) -> None:
        controller = getattr(self, "backchannel_controller", None)
        if controller is not None:
            controller.cancel()
        self._discard_active_backchannel_audio()
        # 正式回复开始:未合成完的接话预生成请求让位,把串行合成队列
        # 让给回复分段;已就绪的音频保留备用,不浪费。
        self._discard_unready_backchannel_audio()

    def _discard_unready_backchannel_audio(self) -> None:
        prepared = getattr(self, "_backchannel_prepared_audio", None)
        if not prepared:
            return
        provider = getattr(self, "tts_provider", None)
        discard_prepared = getattr(provider, "discard_prepared", None)
        removed = 0
        for key in list(prepared.keys()):
            handle = prepared[key]
            if handle.audio_path is not None and not handle.failed:
                continue
            prepared.pop(key, None)
            removed += 1
            if callable(discard_prepared):
                try:
                    discard_prepared(handle)
                except Exception as exc:  # noqa: BLE001
                    debug_log("Backchannel", "让位丢弃接话预生成失败", {"error": str(exc)})
        if removed:
            debug_log(
                "Backchannel",
                "回复开始,未就绪的接话合成请求已让位",
                {"removed": removed, "kept_ready": len(prepared)},
            )

    def _load_backchannel_manifest_for(self, profile: CharacterProfile) -> None:
        """加载当前角色的接话清单;缺失/非法即停用该功能(角色级 opt-out)。"""
        controller = getattr(self, "backchannel_controller", None)
        if controller is None:
            return
        self._discard_backchannel_audio_cache()
        self.backchannel_manifest = None
        self._backchannel_audio_cache = None
        path = profile.backchannel_manifest_path
        if path is None:
            controller.set_manifest(None)
            return
        try:
            manifest = load_backchannel_manifest(path, profile=profile)
        except BackchannelManifestError as exc:
            debug_log("Backchannel", "接话清单加载失败,功能停用", {"error": str(exc)})
            controller.set_manifest(None)
            return
        self.backchannel_manifest = manifest if manifest else None
        # 合成音频的磁盘持久化:按角色分目录,声线指纹做失效;
        # 角色包保持只读,运行时产物一律落 data/。
        self._backchannel_audio_cache = BackchannelAudioCache(
            self.base_dir / "data" / "backchannels" / profile.id / "audio",
            voice_fingerprint(profile.voice),
        )
        controller.set_manifest(self.backchannel_manifest)
        self._prepare_backchannel_audio_cache()

    def _display_backchannel(self, choice: BackchannelChoice) -> None:
        """显示接话:只走轻量字幕+立绘路径。

        临时段绝不进入回复历史(_remember_reply_history_segments)、聊天记录
        (_record_history)、LLM messages 上下文或分段播放队列。
        """
        segment = ChatSegment(
            ja=choice.variant.ja,
            zh=choice.variant.zh,
            tone=choice.template.tone,
            portrait=choice.template.portrait,
        )
        controller = getattr(self, "bubble_auto_hide", None)
        if controller is not None:
            # 唤回可能已被自动隐藏的气泡;倒计时由正式回复完成时的 notify_settled 重启。
            controller.notify_speaking()
        self.portrait_controller.apply_for_segment(segment)
        self.subtitle_controller.set_speech(
            segment.display_text(self.subtitle_language), pulse=True
        )
        self._play_backchannel_audio(choice)
        self._log_interaction_stage("backchannel_shown", {"template": choice.template.id})

    def _backchannel_tts_wanted(self) -> bool:
        """配置层面是否需要接话语音(不关心服务当下是否可达)。"""
        settings = getattr(self, "backchannel_settings", BackchannelSettings()).normalized()
        if not settings.active or not settings.tts_enabled:
            return False
        provider = getattr(self, "tts_provider", None)
        return provider is not None and not isinstance(provider, NullTTSProvider)

    def _backchannel_tts_active(self) -> bool:
        """接话语音现在可用:配置需要 + 服务实际可达。

        没有 service_ready 概念的 provider(如测试桩)视为就绪,
        与旧行为一致;GPT-SoVITS/Genie 在服务探测成功前返回 False,
        避免 prepare() 的 HTTP 调用成批静默失败。
        """
        if not self._backchannel_tts_wanted():
            return False
        if getattr(self, "tts_ready_warmup_thread", None) is not None:
            return False
        provider = getattr(self, "tts_provider", None)
        service_ready = getattr(provider, "service_ready", None)
        if service_ready is None:
            return True
        return bool(service_ready)

    def _backchannel_audio_key(self, choice: BackchannelChoice) -> tuple[str, str, str]:
        return (choice.template.id, choice.template.tone, choice.variant.ja)

    def _prepare_backchannel_audio_cache(self) -> None:
        """为缺少 audio 字段的接话变体预提交 TTS 合成请求。

        这里只做运行期预生成,不写回角色包;角色包持久化音频仍由离线/overlay 流程负责。
        """
        if not self._backchannel_tts_active():
            return
        manifest = getattr(self, "backchannel_manifest", None)
        if manifest is None:
            return
        prepared = getattr(self, "_backchannel_prepared_audio", None)
        if prepared is None:
            prepared = {}
            self._backchannel_prepared_audio = prepared
        cache = getattr(self, "_backchannel_audio_cache", None)
        # 空闲时机先把已合成完、尚未播放的句柄落盘:不落盘则应用重启后
        # 这些合成全部白做。落盘成功即丢弃句柄(provider 顺带清理它的
        # 临时文件),后续播放统一走磁盘缓存分支。
        if cache is not None:
            discard_prepared = getattr(self.tts_provider, "discard_prepared", None)
            for key in list(prepared.keys()):
                handle = prepared[key]
                if handle.audio_path is None or handle.failed:
                    continue
                if cache.store(key[1], key[2], handle.audio_path) is None:
                    continue
                prepared.pop(key, None)
                if callable(discard_prepared):
                    try:
                        discard_prepared(handle)
                    except Exception as exc:  # noqa: BLE001
                        debug_log("Backchannel", "落盘后丢弃合成句柄失败", {"error": str(exc)})
        provider = self.tts_provider
        queued = 0
        missing_audio = 0
        for template in manifest.templates:
            for variant in template.variants:
                if self._backchannel_variant_audio_available(manifest, variant.audio):
                    continue
                # 之前合成并持久化过的不再重复合成(动态链接:内容寻址命中)。
                if cache is not None and cache.lookup(template.tone, variant.ja) is not None:
                    continue
                missing_audio += 1
                key = (template.id, template.tone, variant.ja)
                if key in prepared:
                    continue
                if queued >= BACKCHANNEL_AUDIO_PREPARE_LIMIT:
                    continue
                try:
                    prepared[key] = provider.prepare(variant.ja, template.tone)
                    queued += 1
                except Exception as exc:  # noqa: BLE001
                    debug_log(
                        "Backchannel",
                        "接话音频预生成请求失败",
                        {
                            "template": template.id,
                            "text": variant.ja,
                            "tone": template.tone,
                            "error": str(exc),
                        },
                    )
        if missing_audio:
            debug_log(
                "Backchannel",
                "接话清单存在缺失音频,已提交运行期预生成",
                {
                    "missing_audio": missing_audio,
                    "queued": queued,
                    "limit": BACKCHANNEL_AUDIO_PREPARE_LIMIT,
                },
            )

    def _backchannel_variant_audio_available(
        self,
        manifest: BackchannelManifest,
        audio: str | None,
    ) -> bool:
        return self._resolve_backchannel_audio_path(manifest, audio) is not None

    def _resolve_backchannel_audio_path(
        self,
        manifest: BackchannelManifest | None,
        audio: str | None,
    ) -> Path | None:
        if not audio:
            return None
        path = Path(audio)
        if not path.is_absolute() and manifest is not None and manifest.source_path is not None:
            path = manifest.source_path.parent / path
        return path if path.exists() else None

    def _copy_backchannel_audio_for_playback(self, source: Path) -> Path | None:
        suffix = source.suffix or ".wav"
        temp_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                prefix="sakura_backchannel_",
                suffix=suffix,
                delete=False,
            ) as temp_file:
                temp_path = Path(temp_file.name)
            shutil.copyfile(source, temp_path)
            return temp_path
        except Exception as exc:  # noqa: BLE001
            debug_log(
                "Backchannel",
                "接话预置音频复制失败",
                {"audio_path": str(source), "error": str(exc)},
            )
            try:
                if temp_path is not None:
                    temp_path.unlink(missing_ok=True)
            except Exception:
                pass
            return None

    def _play_backchannel_audio(self, choice: BackchannelChoice) -> None:
        if not self._backchannel_tts_wanted():
            return
        manifest = getattr(self, "backchannel_manifest", None)
        source_audio = self._resolve_backchannel_audio_path(manifest, choice.variant.audio)
        if source_audio is not None:
            playable_audio = self._copy_backchannel_audio_for_playback(source_audio)
            if playable_audio is not None:
                handle = TTSPreparedAudio(
                    text=choice.variant.ja,
                    tone=choice.template.tone,
                    audio_path=playable_audio,
                )
                self._request_backchannel_audio_playback(choice, handle)
                return

        cache = getattr(self, "_backchannel_audio_cache", None)
        # 磁盘缓存命中:之前合成并持久化的音频直接播放。
        # 仍需临时复制——provider 播放结束会删除播放文件,直接播缓存
        # 文件会把缓存本体删掉(这也是预置音频复制机制必须保留的原因)。
        if cache is not None:
            cached_audio = cache.lookup(choice.template.tone, choice.variant.ja)
            if cached_audio is not None:
                playable_audio = self._copy_backchannel_audio_for_playback(cached_audio)
                if playable_audio is not None:
                    handle = TTSPreparedAudio(
                        text=choice.variant.ja,
                        tone=choice.template.tone,
                        audio_path=playable_audio,
                    )
                    self._request_backchannel_audio_playback(choice, handle)
                    return

        prepared = getattr(self, "_backchannel_prepared_audio", {})
        key = self._backchannel_audio_key(choice)
        handle = prepared.get(key)
        # 只播已就绪的音频;未就绪/缺失一律仅字幕,不在对话中触发补合成——
        # provider 的合成队列是串行 FIFO,此刻塞入接话合成会让正式回复
        # 分段的合成(及由其 on_started 驱动的字幕)整体延后。
        # 补合成统一安排在空闲时机(回复完成/预热成功/清单加载)。
        if handle is None or handle.audio_path is None or handle.failed:
            debug_log(
                "Backchannel",
                "接话音频尚未预生成完成,本次仅显示字幕",
                {"template": choice.template.id, "text": choice.variant.ja},
            )
            return
        prepared.pop(key, None)
        # 播放前落盘:provider 播放后会删除合成文件,此刻是持久化的
        # 最后机会;下次同句直接走上面的磁盘缓存分支。
        if cache is not None and handle.audio_path is not None:
            cache.store(choice.template.tone, choice.variant.ja, handle.audio_path)
        self._request_backchannel_audio_playback(choice, handle)

    def _request_backchannel_audio_playback(
        self,
        choice: BackchannelChoice,
        handle: TTSPreparedAudio,
    ) -> None:
        self._active_backchannel_audio = handle
        try:
            self.tts_provider.speak_prepared(
                handle,
                on_finished=lambda h=handle: self._handle_backchannel_audio_finished(h),
            )
            self._log_interaction_stage(
                "backchannel_tts_requested",
                {"template": choice.template.id, "tone": choice.template.tone},
            )
        except Exception as exc:  # noqa: BLE001
            self._active_backchannel_audio = None
            debug_log(
                "Backchannel",
                "接话音频播放请求失败",
                {"template": choice.template.id, "error": str(exc)},
            )
            # 正常路径的音频文件由 provider 播后/丢弃时统一清理
            # (_finish_current_audio / discard_prepared → _schedule_audio_cleanup);
            # 这里是唯一的缝隙:播放请求异常时句柄未入队,文件无人接管。
            discard_prepared = getattr(self.tts_provider, "discard_prepared", None)
            if callable(discard_prepared):
                try:
                    discard_prepared(handle)
                except Exception:  # noqa: BLE001
                    pass

    def _handle_backchannel_audio_finished(self, handle: TTSPreparedAudio) -> None:
        if getattr(self, "_active_backchannel_audio", None) is handle:
            self._active_backchannel_audio = None
        # 此刻正式回复往往即将到达/正在播放,不立即补合成(避免抢占回复
        # 分段的串行合成队列);补合成在回复完成(reply_completed)时进行。

    def _discard_active_backchannel_audio(self) -> None:
        handle = getattr(self, "_active_backchannel_audio", None)
        if handle is None:
            return
        self._active_backchannel_audio = None
        discard_prepared = getattr(self.tts_provider, "discard_prepared", None)
        if not callable(discard_prepared):
            return
        try:
            discard_prepared(handle)
        except Exception as exc:  # noqa: BLE001
            debug_log("Backchannel", "取消接话音频失败", {"error": str(exc)})

    def _discard_backchannel_audio_cache(self) -> None:
        self._discard_active_backchannel_audio()
        prepared = getattr(self, "_backchannel_prepared_audio", None)
        if not prepared:
            return
        provider = getattr(self, "tts_provider", None)
        discard_prepared = getattr(provider, "discard_prepared", None)
        for handle in prepared.values():
            try:
                if callable(discard_prepared):
                    discard_prepared(handle)
            except Exception as exc:  # noqa: BLE001
                debug_log("Backchannel", "丢弃接话预生成音频失败", {"error": str(exc)})
        prepared.clear()

    @Slot(object)
    def _handle_ui_state_changed(self, state: PetUiState) -> None:
        if state == PetUiState.SPEAKING:
            self._start_speaking_state_watchdog()
            return
        self._stop_speaking_state_watchdog()

    def _start_speaking_state_watchdog(self) -> None:
        watchdog = getattr(self, "speaking_state_watchdog", None)
        if watchdog is not None:
            watchdog.start()

    def _stop_speaking_state_watchdog(self) -> None:
        watchdog = getattr(self, "speaking_state_watchdog", None)
        if watchdog is not None and watchdog.isActive():
            watchdog.stop()

    @Slot()
    def _handle_speaking_state_timeout(self) -> None:
        if self.ui_state.state != PetUiState.SPEAKING:
            return
        subtitle_controller = getattr(self, "subtitle_controller", None)
        if subtitle_controller is None or not subtitle_controller.is_reply_sequence_active():
            self.ui_state.finish("speaking_timeout")
            return
        debug_log(
            "PetWindow",
            "SPEAKING 状态超时，强制结束当前回复",
            {"timeout_ms": SPEAKING_STATE_TIMEOUT_MS},
        )
        subtitle_controller.cancel_reply_flow()
        if self.active_interaction_id:
            self._end_interaction("speaking_timeout")
        else:
            self.ui_state.finish("speaking_timeout")

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
            debug_log("History", "回溯历史读取失败", {"error": str(exc)})
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
        maybe_resuppress = getattr(self, "_maybe_resuppress_portrait", None)
        if callable(maybe_resuppress):
            maybe_resuppress()
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
        self._sync_renderer_overlay_geometry(layout=layout)

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
        layout = self._compute_pet_layout()
        self._place_pet_children(layout)
        self._update_stage_debug_overlay(layout)
        self._update_stage_mask(layout)

    def _apply_stage_collision_mask(self, enabled: bool, *, refresh: bool = False) -> None:
        """开关舞台碰撞遮罩。关闭时清除遮罩(整窗可点);开启 + refresh 时立即重算。"""
        self._stage_collision_mask_enabled = bool(enabled)
        if not enabled:
            self.clearMask()
        elif refresh:
            self._update_stage_mask(self._compute_pet_layout())

    def _update_stage_mask(self, layout) -> None:  # type: ignore[no-untyped-def]
        """把窗口命中/绘制区裁到「立绘+气泡+输入栏」矩形并集 ∪ 可见直接子控件,空白处穿透。

        始终并入三个布局矩形(气泡/输入栏即便此刻隐藏也预留,避免其出现时被裁);再并入所有
        可见直接子控件(回复历史等),确保不裁掉任何可见 UI。任何异常都清除遮罩降级为整窗可点。
        """
        if not getattr(self, "_stage_collision_mask_enabled", False):
            return
        try:
            region = QRegion()
            for x, y, w, h in (layout.portrait_rect, layout.bubble_rect, layout.input_rect):
                region = region.united(QRegion(x, y, w, h))
            overlay = getattr(self, "_stage_debug_overlay", None)
            for child in self.children():
                if isinstance(child, QWidget) and child is not overlay and child.isVisible():
                    region = region.united(QRegion(child.geometry()))
            self.setMask(region)
        except Exception as exc:  # noqa: BLE001
            debug_log("UI", "舞台碰撞遮罩更新失败,清除遮罩降级", {"error": str(exc)})
            self.clearMask()

    def _apply_stage_debug_overlay(self, enabled: bool, *, refresh: bool = False) -> None:
        """按开关创建/销毁舞台调试层。refresh=True 时立即重排以填充数值(运行期切换用)。"""
        overlay = getattr(self, "_stage_debug_overlay", None)
        if enabled and overlay is None:
            from app.ui.stage_debug_overlay import StageDebugOverlay

            overlay = StageDebugOverlay(self)
            overlay.setGeometry(self.rect())
            overlay.show()
            self._stage_debug_overlay = overlay
            if refresh:
                self._layout_stage()
        elif not enabled and overlay is not None:
            overlay.hide()
            overlay.deleteLater()
            self._stage_debug_overlay = None

    def _update_stage_debug_overlay(self, layout) -> None:  # type: ignore[no-untyped-def]
        """刷新舞台调试层(仅当启用时存在);并打印一组诊断数值。"""
        overlay = getattr(self, "_stage_debug_overlay", None)
        if overlay is None:
            return
        overlay.setGeometry(self.rect())
        overlay.raise_()
        px, py, pw, ph = layout.portrait_rect
        pm = self.label.pixmap()
        pm_info = (
            f"{pm.width()}x{pm.height()} dpr={pm.devicePixelRatio():.2f}"
            if pm is not None and not pm.isNull()
            else "none"
        )
        screen = self.screen()
        screen_dpr = screen.devicePixelRatio() if screen is not None else 0.0
        lg = self.label.geometry()
        info = (
            f"win logical   : {self.width()}x{self.height()}\n"
            f"devicePixelRatio: win={self.devicePixelRatioF():.2f} screen={screen_dpr:.2f}\n"
            f"stage_size    : {self.stage_size}\n"
            f"portrait_rect : ({px},{py},{pw},{ph})  [green]\n"
            f"label.geometry: ({lg.x()},{lg.y()},{lg.width()},{lg.height()})  [blue]\n"
            f"label pixmap  : {pm_info}"
        )
        overlay.update_debug(
            portrait_rect=QRect(px, py, pw, ph),
            label_rect=QRect(lg.x(), lg.y(), lg.width(), lg.height()),
            info=info,
        )

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
        if not self._input_bar_foreground_allowed():
            return False
        # 单窗口重构后气泡/输入栏已并入主窗口，主窗口几何即桌宠整体区域；
        # 但只有桌宠实际位于光标下方时才视为悬停，避免窗口被其他软件遮挡时误触发输入栏浮现。
        pos = QCursor.pos()
        return (
            self.isVisible()
            and self.frameGeometry().contains(pos)
            and self._cursor_over_exposed_pet_window(pos)
        )

    def _cursor_over_exposed_pet_window(self, pos: QPoint) -> bool:
        """确认当前全局坐标实际命中的 Qt 窗口属于桌宠。

        单纯用 frameGeometry 会在桌宠被其他窗口遮挡时误判 hover；widgetAt/topLevelAt
        会经过平台命中测试，被外部窗口盖住时不会返回当前桌宠窗口。
        """
        widget = QApplication.widgetAt(pos)
        if widget is not None:
            return widget is self or widget.window() is self or self.isAncestorOf(widget)

        top_level_at = getattr(QApplication, "topLevelAt", None)
        window_handle = self.windowHandle()
        if not callable(top_level_at) or window_handle is None:
            return False
        try:
            return top_level_at(pos) is window_handle
        except Exception:
            return False

    def _input_bar_foreground_allowed(self) -> bool:
        """非置顶模式下，只有桌宠处于前台窗口时才允许输入栏显示。"""
        if bool(getattr(self, "always_on_top_enabled", False)):
            return True
        return self._is_pet_foreground_window()

    def _is_pet_foreground_window(self) -> bool:
        """判断桌宠是否为当前前台窗口，优先使用 Windows 原生前台 HWND。"""
        if sys.platform == "win32":
            try:
                import ctypes

                hwnd = int(self.winId())
                if hwnd:
                    return int(ctypes.windll.user32.GetForegroundWindow()) == hwnd
            except Exception:
                pass

        active_window = QApplication.activeWindow()
        if active_window is not None:
            return (
                active_window is self
                or active_window.window() is self
                or self.isAncestorOf(active_window)
            )
        try:
            return bool(self.isActiveWindow())
        except Exception:
            return False

    def _input_bar_pinned(self) -> bool:
        """输入栏在以下任一情况保持常显，避免用户操作中途被收起。

        注意：不把「对话进行中(active_interaction_id)」整体算进来；但等待模型回复时输入栏有状态提示
        与呼吸动效，需要保持可见直到回复流程结束。
        """
        # 设置/历史窗口打开时不固定输入栏，配合 hover 禁用一起彻底收起。
        if self._any_dialog_open():
            return False
        if not self._input_bar_foreground_allowed():
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
        # UI 线程后续的 debug_log 自动带上交互 ID；worker/TTS 线程由各自入口恢复
        set_interaction_id(self.active_interaction_id)
        self.ui_state.begin_thinking(source)
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
        clear_interaction_id()
        # 失败结局保持 ERROR 状态供动效展示，直到下一次交互进入 thinking
        if outcome != "error":
            self.ui_state.finish(outcome)
        self._update_reply_history_buttons()
        # 每完成一轮对话（含完整回复）累计一次，驱动自动记忆整理触发
        if outcome == "reply_completed":
            self._record_completed_memory_turn()
            # 说完话：开始气泡无操作自动隐藏倒计时。
            controller = getattr(self, "bubble_auto_hide", None)
            if controller is not None:
                controller.notify_settled()
            # 空闲时机:补合成本轮消耗/让位掉的接话音频(对话中绝不补,
            # 避免抢占回复分段的串行合成队列)。
            refill_backchannel_audio = getattr(self, "_prepare_backchannel_audio_cache", None)
            if callable(refill_backchannel_audio):
                refill_backchannel_audio()

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
        if pixmap.isNull():
            show_themed_warning(self, "截图失败", "框选截图为空。")
            return
        if not self._start_screen_observation_encode(
            CapturedScreenImage(
                image=pixmap.toImage().copy(),
                captured_at=datetime.now().astimezone().isoformat(timespec="seconds"),
                screen_name="manual-selection",
            ),
            {"kind": "manual"},
        ):
            show_themed_warning(self, "截图处理中", "上一张截图还在处理，请稍后再试。")
            return

    def _finish_manual_screen_observation(self, observation: ScreenObservation) -> None:
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
        # 等待期接话:延迟后若主回复尚未到达,显示一句角色化过渡反应。
        backchannel = getattr(self, "backchannel_controller", None)
        if backchannel is not None:
            backchannel.schedule(text)

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
        clear_screen_awareness_batch = getattr(
            self,
            "_clear_screen_awareness_context_batch",
            None,
        )
        if callable(clear_screen_awareness_batch):
            clear_screen_awareness_batch("sent_user_message")
        else:
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
            interaction_id=self.active_interaction_id,
        )
        self.worker.moveToThread(self.worker_thread)
        self.worker_thread.started.connect(self.worker.run)
        self.worker.progress.connect(self._handle_progress_reply)
        self.worker.finished.connect(self._handle_reply)
        self.worker.failed.connect(self._handle_error)
        self.worker.finished.connect(self.worker_thread.quit)
        self.worker.failed.connect(self.worker_thread.quit)
        self.worker.cancelled.connect(self.worker_thread.quit)
        self.worker_thread.finished.connect(self._cleanup_worker)
        self.worker_thread.start()
        self._log_interaction_stage("chat_worker_started")

    @Slot(object)
    def _handle_progress_reply(self, progress: AgentProgress) -> None:
        if getattr(self, "_shutdown_in_progress", False):
            return
        reply = progress.reply
        if not reply.text.strip():
            return
        self.ui_state.begin_streaming(progress.stage)
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
        self.messages.append(
            {
                "role": "assistant",
                "content": reply.text,
                TRANSIENT_PROGRESS_MESSAGE_KEY: True,
            }
        )
        self._record_assistant_reply_history(reply)

    def _remove_transient_progress_messages(self) -> None:
        self.messages = _without_transient_progress_messages(self.messages)

    @Slot(object)
    def _handle_reply(self, result: AgentResult) -> None:
        if getattr(self, "_shutdown_in_progress", False):
            self.messages = _without_transient_progress_messages(self.messages)
            return
        self.messages = _without_transient_progress_messages(self.messages)
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
        self._emit_plugin_event(
            PLUGIN_EVENT_AI_MESSAGE,
            {
                "text": reply.text,
                "segments": [_segment_plugin_payload(segment) for segment in reply.segments],
                "character_id": self.character_profile.id,
            },
            source="agent",
        )
        emit_plugin_bus_event = getattr(self, "_emit_plugin_bus_event", None)
        if callable(emit_plugin_bus_event):
            emit_plugin_bus_event(
                EVENT_CHAT_MESSAGE_SENT,
                {
                    "text": reply.text,
                    "character_id": self.character_profile.id,
                },
            )
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
            captured = capture_screen_image(self)
        except RuntimeError as exc:
            self.screen_observation_followup_in_progress = False
            self._log_interaction_stage("screen_observation_failed", {"error": str(exc)})
            debug_log("PetWindow", "屏幕观察失败", {"error": str(exc)})
            self._consume_agent_result(_build_screen_observation_failed_result(str(exc)))
            return True
        if not self._start_screen_observation_encode(
            captured,
            {
                "kind": "chat_followup",
                "user_message_index": user_message_index,
                "text": text,
            },
        ):
            self.screen_observation_followup_in_progress = False
            self._consume_agent_result(_build_screen_observation_failed_result("屏幕截图正在处理中。"))
            return True
        return True

    def _finish_chat_screen_observation_followup(
        self,
        context: dict[str, Any],
        observation: ScreenObservation,
    ) -> None:
        user_message_index = int(context.get("user_message_index", -1))
        text = str(context.get("text", ""))
        if user_message_index < 0 or user_message_index >= len(self.messages):
            self.screen_observation_followup_in_progress = False
            self._consume_agent_result(_build_screen_observation_failed_result("缺少可关联的用户消息。"))
            self._resume_screen_observation_followup_cleanup()
            return

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
        self._resume_screen_observation_followup_cleanup()

    def _queue_event_screen_observation_followup(
        self,
        result: AgentResult,
        event: AgentEvent | None,
        reminder_id: str | None,
    ) -> bool:
        screen_action = _first_screen_observation_request(result)
        if screen_action is None:
            return False
        if event is None or not _is_screen_awareness_event_type(event.type):
            self._consume_agent_result(_build_screen_observation_failed_result("缺少可关联的主动事件。"))
            return True
        if not self._screen_awareness_context_allowed():
            self._log_interaction_stage(
                "event_screen_observation_disabled",
                {
                    "screen_awareness_enabled": (
                        self._current_screen_awareness_settings().screen_context_enabled
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
            captured = capture_screen_image(self)
        except RuntimeError as exc:
            self.screen_observation_followup_in_progress = False
            self._log_interaction_stage("event_screen_observation_failed", {"error": str(exc)})
            debug_log("PetWindow", "主动事件屏幕观察失败", {"error": str(exc)})
            self._consume_agent_result(_build_screen_observation_failed_result(str(exc)))
            return True
        if not self._start_screen_observation_encode(
            captured,
            {
                "kind": "event_followup",
                "event": event,
                "reminder_id": reminder_id,
                "reason": reason,
                **self._screen_awareness_encode_options(),
            },
        ):
            self.screen_observation_followup_in_progress = False
            self._consume_agent_result(_build_screen_observation_failed_result("屏幕截图正在处理中。"))
            return True
        return True

    def _finish_event_screen_observation_followup(
        self,
        context: dict[str, Any],
        observation: ScreenObservation,
    ) -> None:
        event = context.get("event")
        if not isinstance(event, AgentEvent):
            self.screen_observation_followup_in_progress = False
            self._consume_agent_result(_build_screen_observation_failed_result("缺少可关联的主动事件。"))
            self._resume_screen_observation_followup_cleanup()
            return
        reminder_id = context.get("reminder_id")
        reminder_id = reminder_id if isinstance(reminder_id, str) else None
        reason = str(context.get("reason", "")).strip()
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
        self._resume_screen_observation_followup_cleanup()

    def _start_screen_observation_encode(
        self,
        captured: CapturedScreenImage,
        context: dict[str, Any],
    ) -> bool:
        if (
            getattr(self, "_shutdown_in_progress", False)
            or self.screen_observation_encode_thread is not None
        ):
            return False
        thread = QThread(self)
        worker = ScreenObservationEncodeWorker(captured, context)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.finished.connect(self._handle_screen_observation_encoded)
        worker.failed.connect(self._handle_screen_observation_encode_failed)
        worker.cancelled.connect(self._handle_screen_observation_encode_cancelled)
        worker.finished.connect(thread.quit)
        worker.failed.connect(thread.quit)
        worker.cancelled.connect(thread.quit)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(self._cleanup_screen_observation_encode_worker)
        self.screen_observation_encode_thread = thread
        self.screen_observation_encode_worker = worker
        thread.start()
        return True

    @Slot(object, object)
    def _handle_screen_observation_encoded(
        self,
        context: dict[str, Any],
        observation: ScreenObservation,
    ) -> None:
        if getattr(self, "_shutdown_in_progress", False):
            self.screen_observation_followup_in_progress = False
            return
        kind = context.get("kind")
        if kind == "chat_followup":
            self._finish_chat_screen_observation_followup(context, observation)
        elif kind == "event_followup":
            self._finish_event_screen_observation_followup(context, observation)
        elif kind in {"screen_awareness_context", "proactive_context"}:
            self._finish_screen_awareness_context(context, observation)
        elif kind == "manual":
            self._finish_manual_screen_observation(observation)

    @Slot(object, str)
    def _handle_screen_observation_encode_failed(self, context: dict[str, Any], message: str) -> None:
        if getattr(self, "_shutdown_in_progress", False):
            self.screen_observation_followup_in_progress = False
            return
        kind = context.get("kind")
        if kind == "chat_followup":
            self.screen_observation_followup_in_progress = False
            self._log_interaction_stage("screen_observation_failed", {"error": message})
            debug_log("PetWindow", "屏幕观察失败", {"error": message})
            self._consume_agent_result(_build_screen_observation_failed_result(message))
            self._resume_screen_observation_followup_cleanup()
        elif kind == "event_followup":
            self.screen_observation_followup_in_progress = False
            self._log_interaction_stage("event_screen_observation_failed", {"error": message})
            debug_log("PetWindow", "主动事件屏幕观察失败", {"error": message})
            self._consume_agent_result(_build_screen_observation_failed_result(message))
            self._resume_screen_observation_followup_cleanup()
        elif kind in {"screen_awareness_context", "proactive_context"}:
            debug_log("ScreenAwareness", "主动屏幕上下文编码失败", {"error": message})
        elif kind == "manual":
            show_themed_warning(self, "截图失败", message)
            debug_log("PetWindow", "手动框选截图编码失败", {"error": message})

    @Slot(object)
    def _handle_screen_observation_encode_cancelled(self, context: dict[str, Any]) -> None:
        if context.get("kind") in {"chat_followup", "event_followup"}:
            self.screen_observation_followup_in_progress = False
            self._resume_screen_observation_followup_cleanup()

    @Slot()
    def _cleanup_screen_observation_encode_worker(self) -> None:
        self._retain_qobject_wrappers_until_deleted(
            self.screen_observation_encode_thread,
            self.screen_observation_encode_worker,
        )
        self.screen_observation_encode_thread = None
        self.screen_observation_encode_worker = None

    def _retain_qobject_wrappers_until_deleted(self, *objects: QObject | None) -> None:
        """Keep PySide wrappers alive until Qt has processed deleteLater.

        A queued Qt signal can reach Python while Qt is already tearing down the
        same QObject. If assigning an attribute drops the final Python wrapper
        reference at that point, Shiboken may try to destroy an object whose C++
        lifetime is already controlled by Qt. Retaining wrappers briefly avoids
        that double-destruction window; invalid wrappers are pruned later.
        """
        retained = [obj for obj in objects if obj is not None]
        if not retained:
            return
        wrappers = getattr(self, "_retired_qobject_wrappers", None)
        if wrappers is None:
            wrappers = []
            self._retired_qobject_wrappers = wrappers
        wrappers.extend(retained)
        QTimer.singleShot(1000, self._prune_retired_qobject_wrappers)

    @Slot()
    def _prune_retired_qobject_wrappers(self) -> None:
        wrappers = getattr(self, "_retired_qobject_wrappers", None)
        if not wrappers:
            return
        try:
            import shiboken6
        except ImportError:
            return
        alive: list[QObject] = []
        for wrapper in wrappers:
            try:
                if shiboken6.isValid(wrapper):
                    alive.append(wrapper)
            except (RuntimeError, TypeError):
                pass
        self._retired_qobject_wrappers = alive

    def _resume_screen_observation_followup_cleanup(self) -> None:
        if getattr(self, "_shutdown_in_progress", False):
            return
        if self.worker_thread is None:
            QTimer.singleShot(0, self._cleanup_worker)

    def _record_user_message(self, text: str) -> None:
        self.messages.append({"role": "user", "content": text})
        self._record_history("user", text)
        emit_plugin_event = getattr(self, "_emit_plugin_event", None)
        if callable(emit_plugin_event):
            emit_plugin_event(
                PLUGIN_EVENT_USER_MESSAGE,
                {
                    "text": text,
                    "character_id": self.character_profile.id,
                },
                source="user",
            )
        emit_plugin_bus_event = getattr(self, "_emit_plugin_bus_event", None)
        if callable(emit_plugin_bus_event):
            emit_plugin_bus_event(
                EVENT_CHAT_MESSAGE_RECEIVED,
                {
                    "text": text,
                    "character_id": self.character_profile.id,
                },
            )

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
            interaction_id=self.active_interaction_id,
        )
        self.worker.moveToThread(self.worker_thread)
        self.worker_thread.started.connect(self.worker.run)
        self.worker.progress.connect(self._handle_progress_reply)
        self.worker.finished.connect(self._handle_action_reply)
        self.worker.failed.connect(self._handle_error)
        self.worker.finished.connect(self.worker_thread.quit)
        self.worker.failed.connect(self.worker_thread.quit)
        self.worker.cancelled.connect(self.worker_thread.quit)
        self.worker_thread.finished.connect(self._cleanup_worker)
        self.worker_thread.start()
        self._log_interaction_stage("action_worker_started")

    @Slot(object)
    def _handle_action_reply(self, result: AgentResult) -> None:
        if getattr(self, "_shutdown_in_progress", False):
            self.messages = _without_transient_progress_messages(self.messages)
            return
        self._log_interaction_stage(
            "action_result_received",
            {
                "segments": len(result.reply.segments),
                "actions": [action.type for action in result.actions],
            },
        )
        self._consume_agent_result(result)

    def _consume_agent_result(self, result: AgentResult, record_history: bool = True) -> None:
        self.messages = _without_transient_progress_messages(self.messages)
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
                debug_log("Tool", "待确认动作无效", {"error": str(exc)})
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
    def _check_screen_awareness(self) -> None:
        if getattr(self, "startup_initializing", False):
            return
        if not self._can_run_screen_awareness():
            return

        now = time.perf_counter()
        if self._should_capture_screen_awareness_context(now):
            self._capture_screen_awareness_context(now)
        if not self._should_send_screen_awareness_batch(now):
            return

        event = self._build_screen_awareness_event(now)
        self.pending_event_visual_observation_jobs = [
            *getattr(self, "pending_event_visual_observation_jobs", []),
            *_build_screen_awareness_visual_observation_jobs(event),
        ]
        self.last_screen_awareness_at = now
        self._record_history("system", SCREEN_AWARENESS_CONTEXT_HISTORY_MARKER)
        self._clear_screen_awareness_context_batch("sent")
        self._run_event_worker(event)

    def _can_run_screen_awareness(self) -> bool:
        if not self._screen_awareness_context_allowed():
            return False
        if (
            self.worker_thread is not None
            or self.active_reminder_id is not None
            or self.active_event_type
            or self.pending_tool_action is not None
            or self.pending_screen_observation_messages is not None
            or self.screen_observation_followup_in_progress
            or self.screen_observation_encode_thread is not None
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

    def _current_screen_awareness_settings(self) -> Any:
        settings = getattr(self, "screen_awareness_settings", None)
        if settings is not None:
            return settings
        return getattr(self, "proactive_care_settings")

    def _should_capture_screen_awareness_context(self, now: float) -> bool:
        settings = self._current_screen_awareness_settings()
        check_interval_seconds = settings.check_interval_minutes * 60
        seconds_since_pet_interaction = now - self.last_user_activity_at
        if (
            seconds_since_pet_interaction + SCREEN_AWARENESS_TIMER_DUE_GRACE_SECONDS
            < check_interval_seconds
        ):
            return False
        if self.last_screen_awareness_context_at is None:
            return True
        return (
            now - self.last_screen_awareness_context_at + SCREEN_AWARENESS_TIMER_DUE_GRACE_SECONDS
            >= check_interval_seconds
        )

    def _capture_screen_awareness_context(self, now: float) -> None:
        self.last_screen_awareness_context_at = now
        try:
            captured = capture_screen_image(self)
        except RuntimeError as exc:
            debug_log("ScreenAwareness", "主动屏幕上下文获取失败", {"error": str(exc)})
            return
        if not self._start_screen_observation_encode(
            captured,
            {
                "kind": "screen_awareness_context",
                "captured_at_monotonic": now,
                **self._screen_awareness_encode_options(),
            },
        ):
            debug_log("ScreenAwareness", "主动屏幕上下文编码忙，跳过本次截图")
            return

    def _finish_screen_awareness_context(
        self,
        context_data: dict[str, Any],
        observation: ScreenObservation,
    ) -> None:
        captured_at_monotonic = context_data.get("captured_at_monotonic")
        if not isinstance(captured_at_monotonic, (int, float)):
            captured_at_monotonic = time.perf_counter()
        context = {
            "data_url": observation.data_url,
            "width": observation.width,
            "height": observation.height,
            "captured_at": observation.captured_at,
            "screen_name": observation.screen_name,
            "detail": str(context_data.get("detail") or SCREEN_AWARENESS_IMAGE_DETAIL),
        }
        if not self.screen_awareness_contexts:
            self.screen_awareness_context_batch_started_at = float(captured_at_monotonic)
        self.screen_awareness_contexts.append(context)
        batch_limit = self._current_screen_awareness_settings().normalized().screen_context_batch_limit
        while len(self.screen_awareness_contexts) > batch_limit:
            self.screen_awareness_contexts.pop(0)
            self.screen_awareness_context_dropped_count += 1
        debug_log(
            "ScreenAwareness",
            "主动屏幕上下文已缓存",
            {
                "width": observation.width,
                "height": observation.height,
                "captured_at": observation.captured_at,
                "screen_name": observation.screen_name,
                "batch_count": len(self.screen_awareness_contexts),
                "dropped_count": self.screen_awareness_context_dropped_count,
                "image": observation.data_url,
            },
        )

    def _should_send_screen_awareness_batch(self, now: float) -> bool:
        if not self.screen_awareness_contexts:
            return False
        if self.screen_awareness_context_batch_started_at is None:
            return False
        return (
            now - self.screen_awareness_context_batch_started_at
            >= self._current_screen_awareness_settings().cooldown_minutes * 60
        )

    def _build_screen_awareness_event(self, now: float | None = None) -> AgentEvent:
        now = time.perf_counter() if now is None else now
        screen_contexts = [dict(context) for context in self.screen_awareness_contexts]
        payload: dict[str, Any] = {
            "triggered_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "seconds_since_pet_interaction": int(now - self.last_user_activity_at),
            "check_interval_minutes": self._current_screen_awareness_settings().check_interval_minutes,
            "cooldown_minutes": self._current_screen_awareness_settings().cooldown_minutes,
            "screen_context_allowed": self._screen_awareness_context_allowed(),
            "screen_context_count": len(screen_contexts),
            "screen_context_dropped_count": self.screen_awareness_context_dropped_count,
        }
        recent_conversation = _build_screen_awareness_recent_conversation_for_window(self)
        if recent_conversation:
            payload["recent_conversation"] = recent_conversation
            payload["recent_conversation_summary_hint"] = (
                SCREEN_AWARENESS_RECENT_CONVERSATION_SUMMARY_HINT
            )
        if screen_contexts:
            payload["screen_contexts"] = screen_contexts
            payload["screen_context_window_started_at"] = screen_contexts[0].get("captured_at", "")
            payload["screen_context_window_ended_at"] = screen_contexts[-1].get("captured_at", "")
            debug_log(
                "ScreenAwareness",
                "主动屏幕上下文批次已附加",
                {
                    "batch_count": len(screen_contexts),
                    "dropped_count": self.screen_awareness_context_dropped_count,
                    "started_at": payload["screen_context_window_started_at"],
                    "ended_at": payload["screen_context_window_ended_at"],
                },
            )
        return AgentEvent(type=SCREEN_AWARENESS_EVENT_TYPE, payload=payload)

    def _screen_awareness_context_allowed(self) -> bool:
        return self._current_screen_awareness_settings().allows_screen_context()

    def _screen_awareness_encode_options(self) -> dict[str, Any]:
        return {
            "preserve_original_resolution": True,
            "detail": SCREEN_AWARENESS_IMAGE_DETAIL,
        }

    def _sync_screen_awareness_timer(self) -> None:
        if self._screen_awareness_context_allowed():
            if not self.screen_awareness_timer.isActive():
                self.screen_awareness_timer.start()
        else:
            self.screen_awareness_timer.stop()
            self._clear_screen_awareness_context_batch("disabled")

    def _clear_screen_awareness_context_batch(self, reason: str) -> None:
        had_batch = bool(self.screen_awareness_contexts)
        self.screen_awareness_contexts = []
        self.screen_awareness_context_batch_started_at = None
        self.last_screen_awareness_context_at = None
        self.screen_awareness_context_dropped_count = 0
        if had_batch:
            debug_log("ScreenAwareness", "主动屏幕上下文批次已清空", {"reason": reason})

    # 兼容旧方法名；新代码请使用 screen_awareness 命名。
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
            *_build_screen_awareness_visual_observation_jobs(event),
        ]
        self.last_proactive_care_at = now
        self._record_history("system", SCREEN_AWARENESS_CONTEXT_HISTORY_MARKER)
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
            or self.screen_observation_encode_thread is not None
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
        settings = PetWindow._current_screen_awareness_settings(self)
        check_interval_seconds = settings.check_interval_minutes * 60
        seconds_since_pet_interaction = now - self.last_user_activity_at
        if (
            seconds_since_pet_interaction + SCREEN_AWARENESS_TIMER_DUE_GRACE_SECONDS
            < check_interval_seconds
        ):
            return False
        if self.last_proactive_screen_context_at is None:
            return True
        return (
            now - self.last_proactive_screen_context_at + SCREEN_AWARENESS_TIMER_DUE_GRACE_SECONDS
            >= check_interval_seconds
        )

    def _capture_proactive_screen_context(self, now: float) -> None:
        self.last_proactive_screen_context_at = now
        try:
            captured = capture_screen_image(self)
        except RuntimeError as exc:
            debug_log("ScreenAwareness", "主动屏幕上下文获取失败", {"error": str(exc)})
            return
        if not self._start_screen_observation_encode(
            captured,
            {
                "kind": "screen_awareness_context",
                "captured_at_monotonic": now,
                **self._screen_awareness_encode_options(),
            },
        ):
            debug_log("ScreenAwareness", "主动屏幕上下文编码忙，跳过本次截图")
            return

    def _finish_proactive_screen_context(
        self,
        context_data: dict[str, Any],
        observation: ScreenObservation,
    ) -> None:
        captured_at_monotonic = context_data.get("captured_at_monotonic")
        if not isinstance(captured_at_monotonic, (int, float)):
            captured_at_monotonic = time.perf_counter()
        context = {
            "data_url": observation.data_url,
            "width": observation.width,
            "height": observation.height,
            "captured_at": observation.captured_at,
            "screen_name": observation.screen_name,
        }
        if not self.proactive_screen_contexts:
            self.proactive_screen_context_batch_started_at = float(captured_at_monotonic)
        self.proactive_screen_contexts.append(context)
        batch_limit = (
            PetWindow._current_screen_awareness_settings(self)
            .normalized()
            .screen_context_batch_limit
        )
        while len(self.proactive_screen_contexts) > batch_limit:
            self.proactive_screen_contexts.pop(0)
            self.proactive_screen_context_dropped_count += 1
        debug_log(
            "ScreenAwareness",
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
            >= PetWindow._current_screen_awareness_settings(self).cooldown_minutes * 60
        )

    def _build_proactive_care_event(self, now: float | None = None) -> AgentEvent:
        now = time.perf_counter() if now is None else now
        screen_contexts = [dict(context) for context in self.proactive_screen_contexts]
        settings = PetWindow._current_screen_awareness_settings(self)
        payload: dict[str, Any] = {
            "triggered_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "seconds_since_pet_interaction": int(now - self.last_user_activity_at),
            "check_interval_minutes": settings.check_interval_minutes,
            "cooldown_minutes": settings.cooldown_minutes,
            "screen_context_allowed": self._proactive_screen_context_allowed(),
            "screen_context_count": len(screen_contexts),
            "screen_context_dropped_count": self.proactive_screen_context_dropped_count,
        }
        recent_conversation = _build_screen_awareness_recent_conversation_for_window(self)
        if recent_conversation:
            payload["recent_conversation"] = recent_conversation
            payload["recent_conversation_summary_hint"] = (
                SCREEN_AWARENESS_RECENT_CONVERSATION_SUMMARY_HINT
            )
        if screen_contexts:
            payload["screen_contexts"] = screen_contexts
            payload["screen_context_window_started_at"] = screen_contexts[0].get("captured_at", "")
            payload["screen_context_window_ended_at"] = screen_contexts[-1].get("captured_at", "")
            debug_log(
                "ScreenAwareness",
                "主动屏幕上下文批次已附加",
                {
                    "batch_count": len(screen_contexts),
                    "dropped_count": self.proactive_screen_context_dropped_count,
                    "started_at": payload["screen_context_window_started_at"],
                    "ended_at": payload["screen_context_window_ended_at"],
                },
            )
        return AgentEvent(type=LEGACY_PROACTIVE_EVENT_TYPE, payload=payload)

    def _proactive_screen_context_allowed(self) -> bool:
        return PetWindow._current_screen_awareness_settings(self).allows_screen_context()

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
            debug_log("ScreenAwareness", "主动屏幕上下文批次已清空", {"reason": reason})

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
            interaction_id=self.active_interaction_id,
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
        self.worker.cancelled.connect(self.worker_thread.quit)
        self.worker_thread.finished.connect(self._cleanup_worker)
        self.worker_thread.start()
        self._log_interaction_stage("event_worker_started")

    @Slot(object)
    def _handle_event_reply(self, result: AgentResult) -> None:
        if getattr(self, "_shutdown_in_progress", False):
            self.messages = _without_transient_progress_messages(self.messages)
            self._clear_active_event()
            return
        self.messages = _without_transient_progress_messages(self.messages)
        self._log_interaction_stage(
            "event_result_received",
            {"event_type": self.active_event_type, "segments": len(result.reply.segments)},
        )
        event = self.active_event
        reminder_id = self.active_reminder_id
        if self._queue_event_screen_observation_followup(result, event, reminder_id):
            self._clear_active_event()
            return
        result = self._filter_screen_awareness_reply(result, event)
        self._clear_active_event()
        if not result.reply.text.strip() and not result.reply.translation.strip() and not result.actions:
            self._log_interaction_stage("event_silent", {"event_type": event.type if event else ""})
            if reminder_id is not None:
                self._mark_reminder_completed(reminder_id)
            self._end_interaction("event_silent")
            return
        self._consume_agent_result(result)
        if reminder_id is not None:
            self._mark_reminder_completed(reminder_id)

    def _filter_screen_awareness_reply(
        self,
        result: AgentResult,
        event: AgentEvent | None,
    ) -> AgentResult:
        if event is None or not _is_screen_awareness_event_type(event.type):
            return result
        if not _is_screen_awareness_health_reply(result.reply):
            return result
        night_key = _screen_awareness_night_key()
        if not night_key:
            return result
        if self._screen_awareness_health_reminder_seen(night_key):
            debug_log(
                "ScreenAwareness",
                "夜间健康类主动提醒已达上限，改为屏幕内容分析",
                {"night_key": night_key},
            )
            return AgentResult(
                reply=_build_screen_awareness_non_health_reply(result.reply, event),
                actions=result.actions,
                _debug=result._debug,
            )
        self._record_screen_awareness_health_reminder(night_key)
        return result

    def _screen_awareness_health_reminder_seen(self, night_key: str) -> bool:
        state = self._load_screen_awareness_state()
        health = state.get(SCREEN_AWARENESS_HEALTH_TOPIC)
        if not isinstance(health, dict):
            return False
        return str(health.get("night_key", "")) == night_key

    def _record_screen_awareness_health_reminder(self, night_key: str) -> None:
        state = self._load_screen_awareness_state()
        state[SCREEN_AWARENESS_HEALTH_TOPIC] = {
            "night_key": night_key,
            "last_reminder_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        }
        self._save_screen_awareness_state(state)

    def _screen_awareness_state_path(self) -> Path:
        return StoragePaths(Path(getattr(self, "base_dir", Path.cwd()))).screen_awareness_state()

    def _load_screen_awareness_state(self) -> dict[str, Any]:
        path = self._screen_awareness_state_path()
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return data if isinstance(data, dict) else {}

    def _save_screen_awareness_state(self, state: dict[str, Any]) -> None:
        try:
            atomic_write_text(
                self._screen_awareness_state_path(),
                json.dumps(state, ensure_ascii=False, indent=2),
                encoding="utf-8",
                backup=False,
            )
        except OSError as exc:
            debug_log("ScreenAwareness", "主动屏幕感知状态保存失败", {"error": str(exc)})

    @Slot(str)
    def _handle_event_error(self, message: str) -> None:
        self.messages = _without_transient_progress_messages(self.messages)
        if getattr(self, "_shutdown_in_progress", False):
            self._clear_active_event()
            return
        event_type = self.active_event_type
        self._log_interaction_stage("event_error", {"event_type": event_type, "message": message})
        reminder_id = self.active_reminder_id
        reminder_text = self.active_reminder_text
        self._clear_active_event()
        debug_log("Event", "主动事件生成失败", {"error": message})
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
        elif _is_screen_awareness_event_type(event_type):
            self._log_interaction_stage("screen_awareness_error_silent")
            # 主动感知失败时静默结束本轮交互。若不结束，active_interaction_id 会一直占用，
            # _can_run_proactive_care 会持续返回 False，导致此后不再触发任何主动感知。
            self._end_interaction("screen_awareness_error_silent")
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
            debug_log("Reminder", "标记完成失败", {"error": str(exc)})

    @Slot(str)
    def _handle_error(self, message: str) -> None:
        self._cancel_backchannel()
        self.messages = _without_transient_progress_messages(self.messages)
        if getattr(self, "_shutdown_in_progress", False):
            return
        self._log_interaction_stage("worker_error", {"message": message})
        self.ui_state.fail("worker_error")
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
        self._retain_qobject_wrappers_until_deleted(self.worker_thread, self.worker)
        if self.worker is not None:
            self.worker.deleteLater()
        if self.worker_thread is not None:
            self.worker_thread.deleteLater()
        self.worker = None
        self.worker_thread = None
        if getattr(self, "_shutdown_in_progress", False):
            self.pending_screen_observation_messages = None
            self.pending_screen_observation_event = None
            self.pending_screen_observation_event_reminder_id = None
            self.screen_observation_followup_in_progress = False
            return
        if self.screen_observation_followup_in_progress:
            self._log_interaction_stage("screen_observation_cleanup_deferred")
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
        self.memory_curation_worker.cancelled.connect(self.memory_curation_thread.quit)
        self.memory_curation_thread.finished.connect(self._cleanup_memory_curation_worker)
        self.memory_curation_thread.start()

    @Slot(object)
    def _handle_memory_curation_finished(self, result: MemoryCurationResult) -> None:
        if getattr(self, "_shutdown_in_progress", False):
            return
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
        if getattr(self, "_shutdown_in_progress", False):
            return
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
        self._retain_qobject_wrappers_until_deleted(
            self.memory_curation_thread,
            self.memory_curation_worker,
        )
        if self.memory_curation_worker is not None:
            self.memory_curation_worker.deleteLater()
        if self.memory_curation_thread is not None:
            self.memory_curation_thread.deleteLater()
        self.memory_curation_worker = None
        self.memory_curation_thread = None
        self.memory_curation_mode = ""
        self.memory_curation_target_history_count = 0
        self.memory_curation_consumed_turns = 0
        if getattr(self, "_shutdown_in_progress", False):
            self.pending_history_clear_after_curation = False
            return
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

        if getattr(self, "_shutdown_in_progress", False):
            self._close_deferred_services(services)
            return

        self._move_tts_provider_to_ui_thread(services.tts_provider)
        if self.mcp_tool_provider is not None and self.mcp_tool_provider is not services.mcp_tool_provider:
            self.mcp_tool_provider.close()
        if self.plugin_manager is not services.plugin_manager:
            self.plugin_manager.shutdown_all()

        self._discard_backchannel_audio_cache()
        self._disconnect_tts_error_signal(self.tts_provider)
        self._retire_tts_provider(self.tts_provider)
        self.tts_provider = services.tts_provider
        self.voice_playback_controller.set_provider(services.tts_provider)
        self._connect_tts_error_signal(services.tts_provider)
        self._warm_up_tts_playback(services.tts_provider)
        self._start_tts_ready_warmup(services.tts_provider)
        self._prepare_backchannel_audio_cache()
        self.tool_registry = services.tool_registry
        self.free_access_enabled = self.tool_registry.free_access_enabled
        self.agent_runtime.tools = services.tool_registry
        self.agent_runtime.set_prompt_patches(services.plugin_manager.prompt_patches)
        self.agent_runtime.set_context_providers(services.plugin_manager.context_providers)
        # 把插件事件总线接到工具执行与 LLM 请求链路，供插件订阅 tool.* / llm.request.*。
        emit_bus_event = getattr(services.plugin_manager, "emit_bus_event", None)
        if callable(emit_bus_event):
            services.tool_registry.set_event_emitter(emit_bus_event)
            api_client = getattr(self.agent_runtime, "api_client", None)
            if api_client is not None and hasattr(api_client, "set_event_emitter"):
                api_client.set_event_emitter(emit_bus_event)
        self.mcp_tool_provider = services.mcp_tool_provider
        self.plugin_manager = services.plugin_manager
        self._wire_plugin_service_backends()
        self._sync_plugin_chat_ui_widgets()
        self.mcp_settings = services.mcp_settings
        self.renderer_manager = self._activate_renderer_manager()

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
        self._sync_screen_awareness_timer()
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
            debug_log("Startup", "后台初始化错误", {"error": error})
            if error.startswith("TTS"):
                self._show_tts_error(error)

    @Slot(str)
    def handle_deferred_startup_failed(self, error: str) -> None:
        if getattr(self, "_shutdown_in_progress", False):
            return
        self.startup_initializing = False
        self.input_edit.setPlaceholderText(self._normal_input_placeholder_text())
        self._collapse_auto_fit_bubble_height()
        self.subtitle_controller.cancel_reply_flow(f"初始化失败：{error}")
        self._set_busy(False)
        if hasattr(self, "tray_icon"):
            self.tray_icon.setContextMenu(self._build_menu())
        debug_log("Startup", "后台启动服务失败", {"error": error})
        debug_log("Startup", "后台初始化失败", {"error": error})

    def _close_deferred_services(self, services: "DeferredStartupServices") -> None:
        debug_log("Startup", "关闭期间收到后台启动结果，立即释放服务")
        for provider in (getattr(services, "tts_provider", None),):
            close = getattr(provider, "close", None)
            if callable(close):
                try:
                    close()
                except Exception as exc:  # noqa: BLE001
                    debug_log("TTS", "关闭延迟启动 TTS Provider 失败", {"error": str(exc)})
        mcp_tool_provider = getattr(services, "mcp_tool_provider", None)
        close_mcp = getattr(mcp_tool_provider, "close", None)
        if callable(close_mcp):
            try:
                close_mcp()
            except Exception as exc:  # noqa: BLE001
                debug_log("MCP", "关闭延迟启动 MCP Provider 失败", {"error": str(exc)})
        plugin_manager = getattr(services, "plugin_manager", None)
        shutdown_all = getattr(plugin_manager, "shutdown_all", None)
        if callable(shutdown_all):
            try:
                shutdown_all()
            except Exception as exc:  # noqa: BLE001
                debug_log("PluginManager", "关闭延迟启动插件失败", {"error": str(exc)})

    def _wire_plugin_service_backends(self) -> None:
        """把宿主真实后端注入插件服务门面（当前：输入框填充）。

        每次 plugin_manager 建立后调用。sink 在插件调用时才读取输入控件，
        因此即使此刻输入栏尚未构建也安全。
        """
        services = getattr(getattr(self, "plugin_manager", None), "services", None)
        if services is None:
            return
        try:
            services.set_backends(input_text_sink=self._request_fill_input_text)
        except Exception as exc:  # noqa: BLE001 — 装配失败不得阻断启动
            debug_log("PetWindow", "注入插件服务后端失败", {"error": str(exc)})

    def _request_fill_input_text(self, text: str) -> None:
        """插件侧入口：请求把文本填入输入框。

        可能在后台线程（如 ASR 回调）被调用，通过信号 marshal 回 UI 线程。
        """
        self.plugin_input_text_requested.emit(str(text))

    @Slot(str)
    def _apply_plugin_input_text(self, text: str) -> None:
        """在 UI 线程把文本填入输入框并聚焦（替换当前内容，不发送）。"""
        if not hasattr(self, "input_edit"):
            return
        try:
            self.input_edit.setText(text)  # QLineEdit.setText 会把光标置于末尾
            self.input_edit.setFocus()
        except RuntimeError as exc:
            # 输入控件可能已被销毁
            debug_log("PetWindow", "填入插件输入文本失败", {"error": str(exc)})

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
        worker.succeeded.connect(self._handle_tts_ready_warmup_succeeded)
        worker.failed.connect(self._handle_tts_ready_warmup_failed)
        worker.finished.connect(thread.quit)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(self._cleanup_tts_ready_warmup_worker)
        self.tts_ready_warmup_thread = thread
        self.tts_ready_warmup_worker = worker
        thread.start()

    @Slot(str)
    def _handle_tts_ready_warmup_succeeded(self, _message: str) -> None:
        # 服务就绪后补做接话音频预生成:设置保存/延迟启动时服务通常还在
        # 冷启动,彼时的预生成被就绪门控跳过,首批合成在这里发起。
        self._prepare_backchannel_audio_cache()

    @Slot(str)
    def _handle_tts_ready_warmup_failed(self, message: str) -> None:
        if getattr(self, "_shutdown_in_progress", False):
            return
        self._show_tts_error(message)

    @Slot()
    def _cleanup_tts_ready_warmup_worker(self) -> None:
        self._retain_qobject_wrappers_until_deleted(
            self.tts_ready_warmup_thread,
            self.tts_ready_warmup_worker,
        )
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
        # 独立窗口：可最小化、有独立任务栏按钮；桌宠置顶时同步置顶以免被桌宠盖住。
        _configure_secondary_window(
            self.history_window,
            keep_on_top=bool(getattr(self, "always_on_top_enabled", False)),
        )
        self.history_window.refresh()
        _present_secondary_window(self.history_window)

    @Slot()
    def show_runtime_log(self) -> None:
        if self.runtime_log_window is None:
            self.runtime_log_window = RuntimeLogWindow(
                theme_settings=self.theme_settings,
                parent=self,
            )
        self.runtime_log_window.set_theme_settings(self.theme_settings)
        # 独立窗口：可最小化、有独立任务栏按钮；桌宠置顶时同步置顶（与设置/历史窗口一致）。
        _configure_secondary_window(
            self.runtime_log_window,
            keep_on_top=bool(getattr(self, "always_on_top_enabled", False)),
        )
        self.runtime_log_window.refresh(reset=True)
        _present_secondary_window(self.runtime_log_window)

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

        screen_awareness_settings = getattr(self, "screen_awareness_settings", None)
        if screen_awareness_settings is None:
            screen_awareness_settings = getattr(
                self,
                "proactive_care_settings",
                ScreenAwarenessSettings(),
            )

        dialog = SettingsDialog(
            self.api_client.settings,
            tts_settings,
            self.base_dir,
            self.character_registry,
            self.character_profile,
            screen_awareness_settings,
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
            backchannel_settings=getattr(self, "backchannel_settings", BackchannelSettings()),
            on_layout_preview=self._preview_layout,
        )
        self.settings_dialog = dialog
        # 非模态打开：设置窗口开着时仍可正常点击/拖动桌宠。可最小化、有独立任务栏按钮；
        # 桌宠置顶时同步置顶，避免设置窗口被置顶的桌宠盖住。
        _configure_secondary_window(
            dialog,
            keep_on_top=bool(getattr(self, "always_on_top_enabled", False)),
        )
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
        # 非模态没有阻塞返回值，结果处理改由 finished 信号驱动；闭包捕获打开前状态用于回滚。
        dialog.finished.connect(
            lambda result: self._on_settings_dialog_finished(
                dialog,
                result,
                original_layout,
                bubble_auto_hide,
                input_bar_animator,
            )
        )
        _present_secondary_window(dialog)

    def _on_settings_dialog_finished(
        self,
        dialog: SettingsDialog,
        dialog_result: int,
        original_layout: tuple[int, int, int, int, int],
        bubble_auto_hide: object,
        input_bar_animator: object,
    ) -> None:
        """设置窗口关闭后的结果处理（原 exec() 之后的同步逻辑，迁移到非模态信号回调）。"""
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
        result_backchannel_settings = getattr(
            dialog,
            "result_backchannel_settings",
            getattr(self, "backchannel_settings", BackchannelSettings()),
        )
        result_screen_awareness_settings = getattr(
            dialog,
            "result_screen_awareness_settings",
            None,
        )
        if result_screen_awareness_settings is None:
            result_screen_awareness_settings = getattr(
                dialog,
                "result_proactive_care_settings",
                None,
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
            or result_screen_awareness_settings is None
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
        if result_backchannel_settings is None or not isinstance(
            result_backchannel_settings,
            BackchannelSettings,
        ):
            result_backchannel_settings = getattr(
                self,
                "backchannel_settings",
                BackchannelSettings(),
            )
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
            save_screen_awareness_settings = getattr(
                self.settings_service,
                "save_screen_awareness_settings",
                None,
            )
            if callable(save_screen_awareness_settings):
                save_screen_awareness_settings(result_screen_awareness_settings)
            else:
                self.settings_service.save_proactive_care_settings(
                    result_screen_awareness_settings
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
            save_backchannel_settings = getattr(
                self.settings_service,
                "save_backchannel_settings",
                None,
            )
            if callable(save_backchannel_settings):
                save_backchannel_settings(result_backchannel_settings)
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
        self.screen_awareness_settings = result_screen_awareness_settings
        mcp_restart_required = dialog.result_mcp_settings != self.mcp_settings
        self.mcp_settings = dialog.result_mcp_settings
        self.debug_log_settings = dialog.result_debug_log_settings
        eval_logger = getattr(self, "backchannel_eval_logger", None)
        if eval_logger is not None:
            eval_logger.set_enabled(self.debug_log_settings.enabled)
        self._apply_stage_debug_overlay(
            self.debug_log_settings.stage_debug_overlay, refresh=True
        )
        self._apply_stage_collision_mask(
            self.debug_log_settings.stage_collision_mask, refresh=True
        )
        self.startup_settings = result_startup_settings
        sync_screen_awareness_timer = getattr(self, "_sync_screen_awareness_timer", None)
        if callable(sync_screen_awareness_timer):
            sync_screen_awareness_timer()
        else:
            self._sync_proactive_care_timer()
        discard_backchannel_audio_cache = getattr(
            self,
            "_discard_backchannel_audio_cache",
            None,
        )
        if callable(discard_backchannel_audio_cache):
            discard_backchannel_audio_cache()
        # 仅在 TTS 配置或角色变化时才重建 provider。无谓重建会在 TTS 服务
        # 后台探测(warmup)期间退休/替换正被探测的旧 provider,后台 warmup
        # 线程与主线程并发拆解同一 provider 引发原生闪退(切换接话语音等
        # 不改 TTS 配置的保存场景尤其高发)。配置等价则保留现有 provider
        # 及其在途 warmup。
        character_changed = selected_profile.id != getattr(self.character_profile, "id", None)
        if _tts_provider_needs_rebuild(
            self.tts_provider,
            new_tts_provider,
            character_changed=character_changed,
        ):
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
        else:
            # 配置与角色均未变:丢弃刚建好的等价 provider(adopt=False,未启动
            # 服务/播放器,close 为惰性安全操作),保留现有 provider 不动。
            close_unused = getattr(new_tts_provider, "close", None)
            if callable(close_unused):
                try:
                    close_unused()
                except Exception as exc:  # noqa: BLE001
                    debug_log("TTS", "丢弃未使用的等价 TTS Provider 失败", {"error": str(exc)})
            debug_log("PetWindow", "TTS 配置与角色均未变,保留现有 Provider,跳过重建")
        self._apply_character(selected_profile)
        apply_backchannel_settings = getattr(self, "_apply_backchannel_settings", None)
        if callable(apply_backchannel_settings):
            apply_backchannel_settings(result_backchannel_settings)
        else:
            self.backchannel_settings = result_backchannel_settings
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
        _present_secondary_window(dialog)

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
        # 已打开的设置/历史/日志窗口需跟随桌宠置顶状态更新，否则桌宠置顶后会反盖住它们。
        self._sync_secondary_windows_topmost()
        if hasattr(self, "tray_icon"):
            self.tray_icon.setContextMenu(self._build_menu())

    def _sync_secondary_windows_topmost(self) -> None:
        """桌宠置顶状态切换时，让已打开的副窗口跟随更新置顶，保持在桌宠之上。"""
        keep_on_top = bool(getattr(self, "always_on_top_enabled", False))
        for window in (
            getattr(self, "settings_dialog", None),
            getattr(self, "history_window", None),
            getattr(self, "runtime_log_window", None),
        ):
            if window is None or not window.isVisible():
                continue
            _configure_secondary_window(window, keep_on_top=keep_on_top)
            _present_secondary_window(window)

    def _create_tts_provider_from_settings(
        self,
        settings: GPTSoVITSTTSSettings,
    ) -> TTSProvider | None:
        if not settings.enabled:
            debug_log("PetWindow", "设置保存后 TTS 保持关闭")
            return NullTTSProvider()
        try:
            # 统一走工厂；补传 base_dir 修正旧实现缓存目录回退 __file__ 推算的问题
            provider = create_tts_provider(
                settings,
                base_dir=self.base_dir,
                adopt_existing_service=False,
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
                api_url=DEFAULT_GPT_SOVITS_API_URL,
                ref_lang=self.character_profile.voice.ref_lang,
                text_lang=self.character_profile.voice.text_lang,
                timeout_seconds=60,
                validate_enabled=False,
            )
        return GPTSoVITSTTSSettings(
            enabled=False,
            api_url=DEFAULT_GPT_SOVITS_API_URL,
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
            debug_log("History", "写入失败", {"error": str(exc)})
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
            debug_log("Reminder", "读取失败", {"error": str(exc)})
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
        # 正式回复分段即将进入串行 TTS 合成队列:先让未就绪的接话预生成请求让位。
        # 不能只依赖 _apply_reply_segment 里的 _cancel_backchannel——那是 TTS
        # on_started 回调,等它触发时回复音频早已排在一整队接话 prepare 之后,
        # 等待动效停不下来(回复卡在"等待中"),被丢弃的接话还会被反复重排合成。
        # 用 getattr 取方法以兼容仅挂载部分方法的精简测试桩。
        cancel_backchannel = getattr(self, "_cancel_backchannel", None)
        if callable(cancel_backchannel):
            cancel_backchannel()
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
                self._stack_renderer_overlay_below()
            except Exception as exc:  # noqa: BLE001
                debug_log("PetWindow", "同步原生置顶状态失败", {"error": str(exc)})
            return
        if sys.platform == "darwin":
            try:
                for window in self._topmost_sync_windows():
                    _set_macos_window_topmost(int(window.winId()), self.always_on_top_enabled)
                self._stack_renderer_overlay_below()
            except Exception as exc:  # noqa: BLE001
                debug_log("PetWindow", "同步 macOS 原生置顶状态失败", {"error": str(exc)})

    def _topmost_sync_windows(self):
        # 单窗口重构后只有主窗口一个顶层窗口，置顶仅作用于它。
        return [self]

    def _stack_renderer_overlay_below(self) -> None:
        manager = getattr(self, "renderer_manager", None)
        if manager is None or not getattr(manager, "is_overlay_active", False):
            return
        try:
            manager.stack_below(self, topmost=bool(getattr(self, "always_on_top_enabled", False)))
        except Exception as exc:  # noqa: BLE001
            debug_log("RendererManager", "同步渲染窗口层级失败", {"error": str(exc)})

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
                maybe_resuppress = getattr(self, "_maybe_resuppress_portrait", None)
                if callable(maybe_resuppress):
                    maybe_resuppress()
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
            maybe_resuppress = getattr(self, "_maybe_resuppress_portrait", None)
            if callable(maybe_resuppress):
                maybe_resuppress()
            self._apply_pet_layout(anchor_global=anchor)
        finally:
            self.setUpdatesEnabled(was_enabled)
        self._load_backchannel_manifest_for(profile)
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
            self._emit_plugin_event(
                PLUGIN_EVENT_CHARACTER_LOADED,
                {
                    "character_id": profile.id,
                    "character_name": profile.display_name,
                    "previous_character_id": previous_character_id,
                },
                source="character",
            )

    def _create_history_store(self, profile: CharacterProfile) -> ChatHistoryStore:
        # 路径与旧历史迁移统一走 bootstrap 的公开 helper，避免两处实现漂移
        from app.core.bootstrap import create_history_store

        return create_history_store(self.base_dir, profile)

    def _create_runtime_event_log(self, profile: CharacterProfile) -> RuntimeEventLog:
        from app.core.bootstrap import create_runtime_event_log

        return create_runtime_event_log(self.base_dir, profile)

    def _create_visual_observation_store(self, profile: CharacterProfile) -> VisualObservationStore:
        from app.core.bootstrap import create_visual_observation_store

        return create_visual_observation_store(self.base_dir, profile)


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


def _segment_plugin_payload(segment: ChatSegment) -> dict[str, str]:
    return {
        "text": segment.text,
        "translation": segment.translation,
        "tone": segment.tone,
        "portrait": segment.portrait,
    }


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


def _is_screen_awareness_event_type(event_type: str) -> bool:
    return event_type in {SCREEN_AWARENESS_EVENT_TYPE, LEGACY_PROACTIVE_EVENT_TYPE}


def _is_screen_awareness_health_reply(reply: ChatReply) -> bool:
    return any(
        _is_screen_awareness_health_segment(segment)
        for segment in reply.segments
    )


def _is_screen_awareness_health_segment(segment: ChatSegment) -> bool:
    text = "\n".join(
        part
        for part in (segment.text, segment.translation)
        if part
    )
    return any(keyword in text for keyword in SCREEN_AWARENESS_HEALTH_KEYWORDS)


def _build_screen_awareness_non_health_reply(
    reply: ChatReply,
    event: AgentEvent | None,
) -> ChatReply:
    non_health_segments = [
        segment
        for segment in reply.segments
        if not _is_screen_awareness_health_segment(segment)
    ]
    if non_health_segments:
        return ChatReply(non_health_segments)
    return _build_screen_awareness_screen_content_reply(event)


def _build_screen_awareness_screen_content_reply(event: AgentEvent | None) -> ChatReply:
    visual_context = _first_screen_awareness_visual_context(event)
    summary = _screen_awareness_text_value(
        visual_context.get("summary") if visual_context else None
    )
    visible_texts = _screen_awareness_text_list(
        visual_context.get("visible_texts") if visual_context else None,
        limit=3,
    )
    notable_elements = _screen_awareness_text_list(
        visual_context.get("notable_elements") if visual_context else None,
        limit=3,
    )

    if summary:
        zh = f"我先按屏幕内容说：{summary}"
        ja = f"画面内容のほうを見るね。{summary}"
    else:
        screen_count = _screen_awareness_screen_context_count(event)
        if screen_count > 0:
            zh = f"我先按屏幕内容说：这批主动感知拿到了 {screen_count} 张屏幕上下文，可以顺着当前窗口和任务状态继续看。"
            ja = f"画面内容のほうを見るね。今回は {screen_count} 枚の画面文脈をもとに、今のウィンドウと作業状態に沿って見るよ。"
        else:
            zh = "我先按屏幕内容说：当前画面有新的上下文，可以顺着可见窗口和任务状态继续推进。"
            ja = "画面内容のほうを見るね。今の画面の文脈に沿って、見えているウィンドウと作業状態から続けるよ。"

    if visible_texts:
        visible = "、".join(visible_texts)
        zh += f" 画面里比较明确的文字有：{visible}。"
        ja += f" 見えている文字は「{visible}」あたり。"
    elif notable_elements:
        notable = "、".join(notable_elements)
        zh += f" 比较值得关注的是：{notable}。"
        ja += f" 目立つ要素は「{notable}」あたり。"

    return ChatReply(
        [
            ChatSegment(
                text=ja,
                translation=zh,
                tone="中性",
                portrait="思考",
            )
        ]
    )


def _first_screen_awareness_visual_context(event: AgentEvent | None) -> dict[str, Any] | None:
    if event is None:
        return None
    visual_contexts = event.payload.get("visual_contexts")
    if not isinstance(visual_contexts, list):
        return None
    for context in visual_contexts:
        if isinstance(context, dict):
            return context
    return None


def _screen_awareness_screen_context_count(event: AgentEvent | None) -> int:
    if event is None:
        return 0
    count = event.payload.get("screen_context_count")
    if isinstance(count, int) and count > 0:
        return count
    screen_contexts = event.payload.get("screen_contexts")
    if isinstance(screen_contexts, list):
        return len(screen_contexts)
    return 0


def _screen_awareness_text_value(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _screen_awareness_text_list(value: Any, *, limit: int) -> list[str]:
    if not isinstance(value, list):
        return []
    result: list[str] = []
    for item in value:
        text = _screen_awareness_text_value(item)
        if not text:
            continue
        result.append(text)
        if len(result) >= limit:
            break
    return result


def _screen_awareness_night_key(now: datetime | None = None) -> str:
    current = now or datetime.now().astimezone()
    if current.hour >= 23:
        return current.date().isoformat()
    if current.hour < 6:
        return (current.date() - timedelta(days=1)).isoformat()
    return ""


def _build_screen_awareness_visual_observation_jobs(event: AgentEvent) -> list[VisualObservationJob]:
    screen_contexts = event.payload.get("screen_contexts")
    if not isinstance(screen_contexts, list) or not screen_contexts:
        return []
    return [
        VisualObservationJob(
            id=generate_visual_observation_id(),
            source=SCREEN_AWARENESS_VISUAL_SOURCE,
            user_text="主动屏幕感知上下文批次",
            screen_contexts=[
                dict(context)
                for context in screen_contexts
                if isinstance(context, dict)
            ],
        )
    ]


def _build_screen_awareness_recent_conversation(
    messages: list[dict[str, Any]],
    *,
    limit: int = SCREEN_AWARENESS_RECENT_CONVERSATION_LIMIT,
    content_limit: int = SCREEN_AWARENESS_RECENT_CONVERSATION_CONTENT_LIMIT,
) -> list[dict[str, str]]:
    """为主动事件提取近期用户/助手对话，帮助模型理解一段时间内的语境。"""
    recent: list[dict[str, str]] = []
    for message in messages:
        role = str(message.get("role", "")).strip()
        if role not in {"user", "assistant"}:
            continue
        content = _screen_awareness_recent_conversation_content(message.get("content"))
        if not content or content == SCREEN_AWARENESS_CONTEXT_HISTORY_MARKER:
            continue
        recent.append(
            {
                "role": role,
                "content": _truncate_screen_awareness_recent_conversation_content(
                    content,
                    content_limit,
                ),
            }
        )
    return recent[-limit:]


def _build_screen_awareness_recent_conversation_for_window(
    window: Any,
    *,
    limit: int = SCREEN_AWARENESS_RECENT_CONVERSATION_LIMIT,
    content_limit: int = SCREEN_AWARENESS_RECENT_CONVERSATION_CONTENT_LIMIT,
) -> list[dict[str, str]]:
    """主动事件优先读取持久化历史，避免重启后丢失近期语境。"""
    history_entries = _load_screen_awareness_history_entries(window)
    if history_entries:
        return _build_screen_awareness_recent_conversation_from_history_entries(
            history_entries,
            subtitle_language=str(getattr(window, "subtitle_language", SUBTITLE_LANGUAGE_ZH)),
            limit=limit,
            content_limit=content_limit,
        )
    return _build_screen_awareness_recent_conversation(
        getattr(window, "messages", []),
        limit=limit,
        content_limit=content_limit,
    )


def _load_screen_awareness_history_entries(window: Any) -> list[ChatHistoryEntry]:
    history_store = getattr(window, "history_store", None)
    if history_store is None or not hasattr(history_store, "load"):
        return []
    try:
        entries = history_store.load()
    except OSError as exc:
        debug_log("ScreenAwareness", "读取近期聊天历史失败", {"error": str(exc)})
        return []
    return [entry for entry in entries if isinstance(entry, ChatHistoryEntry)]


def _build_screen_awareness_recent_conversation_from_history_entries(
    entries: list[ChatHistoryEntry],
    *,
    subtitle_language: str,
    limit: int = SCREEN_AWARENESS_RECENT_CONVERSATION_LIMIT,
    content_limit: int = SCREEN_AWARENESS_RECENT_CONVERSATION_CONTENT_LIMIT,
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
    return _build_screen_awareness_recent_conversation(
        messages,
        limit=limit,
        content_limit=content_limit,
    )


def _screen_awareness_recent_conversation_content(content: Any) -> str:
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


def _truncate_screen_awareness_recent_conversation_content(content: str, limit: int) -> str:
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


def _tts_provider_needs_rebuild(
    old_provider: TTSProvider,
    new_provider: TTSProvider,
    *,
    character_changed: bool,
) -> bool:
    """保存设置时是否需要用 new_provider 替换 old_provider。

    仅在角色变化、provider 类型变化(如启停 TTS)或 TTS 配置变化时才重建。
    GPTSoVITSTTSSettings 为 frozen dataclass,其相等比较已覆盖声线/模型/
    语气参考等全部字段,故"settings 相等"即"provider 等价"。

    无谓重建会在 TTS 服务后台探测(warmup)期间退休正被探测的旧 provider,
    后台线程与主线程并发拆解同一 provider 引发原生崩溃;配置等价时返回
    False,保留现有 provider 及其在途 warmup,既避险又免去无谓 churn。
    """
    if character_changed:
        return True
    if type(old_provider) is not type(new_provider):
        return True
    return getattr(old_provider, "settings", None) != getattr(new_provider, "settings", None)


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


def _configure_secondary_window(window, *, keep_on_top: bool = False) -> None:  # type: ignore[no-untyped-def]
    # 设置/历史窗口作为独立窗口处理：可最小化、有独立任务栏按钮，点击其他窗口时正常退到后面。
    #
    # 根因：这两个窗口以桌宠（Qt.Tool）为父窗口，在 Windows 上属于“被拥有窗口”。
    # 当桌宠开启置顶（WS_EX_TOPMOST）时，系统强制让被拥有窗口在 z 序上位于拥有者
    # 之上，于是它们也被一起置顶——单清 Qt 的 WindowStaysOnTopHint 标志无效，
    # show 后再用 SetWindowPos 下推也会被系统打回。唯一可靠的办法是切断原生拥有
    # 关系：setParent(None) 把它变成独立顶层窗口，就不再继承桌宠的置顶。
    #
    # keep_on_top：桌宠开启置顶时，桌宠自身是 HWND_TOPMOST，而脱离父子关系后的副窗口
    # 属于普通层，会被桌宠永久盖住。此时需让副窗口同样置顶（独立窗口设 Qt 置顶标志即可
    # 生效），再靠 raise 浮在桌宠之上；桌宠未置顶时副窗口保持普通层，可正常退到后面。
    #
    # 注意：setParent(None) 会重置 window flags，所以必须先 detach、再设 flags。
    # 窗口的 Python 引用由调用方持有（设置窗口靠 self.settings_dialog，历史/日志窗口靠
    # self.history_window / self.runtime_log_window），脱离 Qt 对象树后生命周期仍安全。
    set_parent = getattr(window, "setParent", None)
    if callable(set_parent) and window.parent() is not None:
        set_parent(None)
    set_flag = getattr(window, "setWindowFlag", None)
    if callable(set_flag):
        set_flag(Qt.WindowType.WindowStaysOnTopHint, keep_on_top)
        # QDialog 默认标题栏只有关闭按钮，补上系统菜单与最小化按钮，让窗口可被最小化。
        set_flag(Qt.WindowType.WindowTitleHint, True)
        set_flag(Qt.WindowType.WindowSystemMenuHint, True)
        set_flag(Qt.WindowType.WindowMinimizeButtonHint, True)
        set_flag(Qt.WindowType.WindowMaximizeButtonHint, True)
    # 顶层窗口默认应有任务栏按钮；保险起见在 Windows 上显式写入 WS_EX_APPWINDOW、
    # 清掉 WS_EX_TOOLWINDOW，确保最小化后能在任务栏单独点回来。
    if sys.platform == "win32":
        _force_windows_taskbar_button(window)


def _present_secondary_window(window: QWidget) -> None:
    """显示/恢复普通副窗口：若已最小化，则从最小化状态恢复后再激活。"""
    is_minimized = getattr(window, "isMinimized", None)
    show_normal = getattr(window, "showNormal", None)

    if callable(is_minimized) and is_minimized() and callable(show_normal):
        show_normal()
    else:
        show = getattr(window, "show", None)
        if callable(show):
            show()

    # 双保险：清掉 WindowMinimized，并请求激活状态。
    window_state = getattr(window, "windowState", None)
    set_window_state = getattr(window, "setWindowState", None)
    if callable(window_state) and callable(set_window_state):
        state = window_state()
        state = (state & ~Qt.WindowState.WindowMinimized) | Qt.WindowState.WindowActive
        set_window_state(state)

    raise_window = getattr(window, "raise_", None)
    if callable(raise_window):
        raise_window()

    activate_window = getattr(window, "activateWindow", None)
    if callable(activate_window):
        activate_window()


def _force_windows_taskbar_button(window) -> None:  # type: ignore[no-untyped-def]
    # 在原生窗口上设置 WS_EX_APPWINDOW、清掉 WS_EX_TOOLWINDOW，
    # 使被父窗口拥有的对话框获得独立任务栏按钮。必须在 show() 之前调用。
    win_id = getattr(window, "winId", None)
    if not callable(win_id):
        return
    try:
        import ctypes

        hwnd = int(win_id())  # 触发原生窗口创建并取得 HWND
        if not hwnd:
            return
        GWL_EXSTYLE = -20
        WS_EX_APPWINDOW = 0x00040000
        WS_EX_TOOLWINDOW = 0x00000080
        user32 = ctypes.windll.user32  # type: ignore[attr-defined]
        user32.GetWindowLongW.restype = ctypes.c_long
        user32.GetWindowLongW.argtypes = [ctypes.c_void_p, ctypes.c_int]
        user32.SetWindowLongW.restype = ctypes.c_long
        user32.SetWindowLongW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_long]
        ex_style = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
        new_style = (ex_style | WS_EX_APPWINDOW) & ~WS_EX_TOOLWINDOW
        if new_style != ex_style:
            user32.SetWindowLongW(hwnd, GWL_EXSTYLE, new_style)
    except (OSError, ValueError, AttributeError):
        # 取不到原生句柄或非标准 Win32 环境时静默跳过，最小化按钮仍可用。
        return


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
