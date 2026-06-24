from __future__ import annotations

import json
import os
import secrets
import sys
import traceback
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from PySide6.QtCore import QObject, QProcess, QProcessEnvironment, QThread, Signal
from PySide6.QtWidgets import QApplication, QWidget

from app.agent.memory_curator import MemoryCurationSettings
from app.agent.memory import (
    DEFAULT_MEMORY_CONFIDENCE,
    DEFAULT_MEMORY_IMPORTANCE,
    DEFAULT_MEMORY_LAYER,
    DEFAULT_MEMORY_SOURCE,
    MEMORY_LAYER_LABELS,
    MEMORY_LAYERS,
)
from app.agent.mcp import MCPRuntimeSettings, normalize_mcp_runtime_settings
from app.agent.runtime_limits import (
    MAX_CONFIGURABLE_AGENT_STEPS_PER_TURN,
    MAX_CONFIGURABLE_TOOL_CALLS_PER_STEP,
    MAX_CONFIGURABLE_TOOL_CALLS_PER_TURN,
    MIN_AGENT_STEPS_PER_TURN,
    MIN_TOOL_CALLS_PER_STEP,
    MIN_TOOL_CALLS_PER_TURN,
    RuntimeLoopSettings,
    normalize_runtime_loop_settings,
)
from app.agent.screen_awareness import (
    SCREEN_AWARENESS_MAX_CHECK_INTERVAL_MINUTES,
    SCREEN_AWARENESS_MAX_COOLDOWN_MINUTES,
    SCREEN_AWARENESS_MAX_SCREEN_CONTEXT_BATCH_LIMIT,
    SCREEN_AWARENESS_MIN_CHECK_INTERVAL_MINUTES,
    SCREEN_AWARENESS_MIN_COOLDOWN_MINUTES,
    SCREEN_AWARENESS_MIN_SCREEN_CONTEXT_BATCH_LIMIT,
    ScreenAwarenessSettings,
    estimate_screen_context_image_tokens_for_size,
)
from app.config.character_loader import CharacterProfile, CharacterRegistry
from app.config.defaults import (
    DEFAULT_BASE_URL,
    DEFAULT_PROFILE_ALIAS,
    DEFAULT_PROFILE_ID,
    DEFAULT_TEXT_MODEL,
)
from app.config.model_slots import normalize_provider_models, resolve_model_slot
from app.config.models import (
    MODEL_SLOT_CHAT,
    MODEL_SLOT_LABELS,
    MODEL_SLOT_ORDER,
    ApiConfigProfile,
    ModelSelectionSettings,
    ModelSlotSelection,
)
from app.config.settings_service import (
    BACKCHANNEL_MAX_DELAY_MS,
    BACKCHANNEL_MIN_DELAY_MS,
    BUBBLE_AUTO_HIDE_MAX_DELAY_SECONDS,
    BUBBLE_AUTO_HIDE_MIN_DELAY_SECONDS,
    BackchannelSettings,
    BubbleSettings,
    DebugLogSettings,
    StartupSettings,
)
from app.llm.api_client import ApiSettings
from app.plugins.discovery import PluginDiscovery
from app.plugins.models import (
    PERMISSION_CHAT_UI,
    PERMISSION_CONTEXT_PROVIDER,
    PERMISSION_EVENT_APP,
    PERMISSION_EVENT_CHARACTER,
    PERMISSION_EVENT_MESSAGE,
    PERMISSION_EVENT_TTS,
    PERMISSION_MOBILE_CHAT,
    PERMISSION_PLUGIN_SETTINGS,
    PERMISSION_PROMPT_PATCH,
    PERMISSION_RENDERER,
    PERMISSION_SETTINGS_PANEL,
    PERMISSION_TOOL,
    PERMISSION_TOOLS_TAB,
    PluginSettingsContribution,
    PluginSettingsField,
)
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
    normalize_bubble_height,
    normalize_control_panel_vertical_offset,
    normalize_control_panel_width,
    normalize_input_bar_offset,
)
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
from app.ui.theme import (
    DEFAULT_THEME_SETTINGS,
    THEME_COLOR_FIELDS,
    ThemeSettings,
    theme_colors_to_mapping,
    theme_to_mapping,
)
from app.ui.settings.workers import ApiConnectionTestWorker, ApiModelListProbeWorker
from app.ui.window_backdrop import VisualEffectMode
from app.voice.tts_bundle import default_provider_bundle_notice, default_provider_bundle_work_dir, list_nvidia_gpus
from app.voice.tts_settings import (
    DEFAULT_GENIE_TTS_API_URL,
    DEFAULT_GPT_SOVITS_API_URL,
    TTS_PROVIDER_CUSTOM_GPT_SOVITS,
    TTS_PROVIDER_GENIE,
    TTS_PROVIDER_GPT_SOVITS,
    TTS_PROVIDER_NONE,
    GPTSoVITSTTSSettings,
)

TAURI_SETTINGS_TRIAL_ENV = "SAKURA_TAURI_SETTINGS_TRIAL"
TAURI_SETTINGS_BIN_ENV = "SAKURA_TAURI_SETTINGS_BIN"
TAURI_SETTINGS_PROTOCOL_VERSION = 2

# stdout 行以此标记开头时，携带一份实时布局预览（与 src-tauri/src/lib.rs 中常量保持一致）。
TAURI_LAYOUT_PREVIEW_MARKER = "@@SAKURA_LAYOUT_PREVIEW@@"
TAURI_SETTINGS_RESULT_MARKER = "@@SAKURA_SETTINGS_RESULT@@"
TAURI_SETTINGS_RPC_MARKER = "@@SAKURA_SETTINGS_RPC@@"
TAURI_SETTINGS_RPC_RESULT_MARKER = "@@SAKURA_SETTINGS_RPC_RESULT@@"

PLUGIN_PERMISSION_LABELS: dict[str, dict[str, str]] = {
    PERMISSION_TOOL: {"group": "工具", "label": "Agent 工具"},
    PERMISSION_TOOLS_TAB: {"group": "UI", "label": "工具页"},
    PERMISSION_SETTINGS_PANEL: {"group": "UI", "label": "Qt 设置面板"},
    PERMISSION_PLUGIN_SETTINGS: {"group": "UI", "label": "插件设置"},
    PERMISSION_CHAT_UI: {"group": "UI", "label": "聊天 UI"},
    PERMISSION_PROMPT_PATCH: {"group": "上下文", "label": "提示词补丁"},
    PERMISSION_CONTEXT_PROVIDER: {"group": "上下文", "label": "动态上下文"},
    PERMISSION_MOBILE_CHAT: {"group": "移动端", "label": "移动聊天"},
    PERMISSION_RENDERER: {"group": "渲染器", "label": "角色渲染器"},
    PERMISSION_EVENT_APP: {"group": "事件", "label": "应用事件"},
    PERMISSION_EVENT_MESSAGE: {"group": "事件", "label": "消息事件"},
    PERMISSION_EVENT_TTS: {"group": "事件", "label": "语音事件"},
    PERMISSION_EVENT_CHARACTER: {"group": "事件", "label": "角色事件"},
}


def _default_api_settings() -> ApiSettings:
    return ApiSettings(
        base_url=DEFAULT_BASE_URL,
        api_key="",
        model=DEFAULT_TEXT_MODEL,
    )


def _api_probe_settings(method: str, params: dict[str, Any]) -> ApiSettings:
    """把前端 api.list_models / api.test_connection 的参数转成 ApiSettings 并校验。"""
    base_url = str(params.get("base_url") or "").strip()
    api_key = str(params.get("api_key") or "").strip()
    model = str(params.get("model") or "").strip()
    if not base_url:
        raise ValueError("请先填写 Base URL。")
    if not api_key:
        raise ValueError("请先填写 API Key。")
    if method == "api.test_connection" and not model:
        raise ValueError("请先选择要测试的模型。")
    timeout_seconds = 60
    raw_timeout = params.get("timeout_seconds")
    if raw_timeout is not None:
        try:
            timeout_seconds = int(raw_timeout)
        except (TypeError, ValueError):
            timeout_seconds = 60
    # 控制单次探测时长，留在 Rust 30s RPC 超时之内。
    timeout_seconds = max(5, min(timeout_seconds, 25))
    return ApiSettings(
        base_url=base_url,
        api_key=api_key,
        model=model,
        timeout_seconds=timeout_seconds,
    )


@dataclass(frozen=True)
class TauriSystemBasicResult:
    debug_log: DebugLogSettings = field(default_factory=DebugLogSettings)
    subtitle_typing_interval_ms: int = SPEECH_TYPING_INTERVAL_MS
    reply_segment_pause_ms: int = REPLY_SEGMENT_PAUSE_MS
    bubble: BubbleSettings = field(default_factory=BubbleSettings)


@dataclass(frozen=True)
class TauriCharacterResult:
    character_id: str = ""
    portrait_scale_percent: int = PORTRAIT_SCALE_DEFAULT_PERCENT
    control_panel_width: int = DEFAULT_CONTROL_PANEL_WIDTH
    bubble_height: int = DEFAULT_BUBBLE_HEIGHT
    control_panel_vertical_offset: int = DEFAULT_CONTROL_PANEL_VERTICAL_OFFSET
    input_bar_offset: int = DEFAULT_INPUT_BAR_OFFSET


@dataclass(frozen=True)
class TauriApiResult:
    settings: ApiSettings = field(default_factory=_default_api_settings)
    profiles: list[ApiConfigProfile] = field(default_factory=list)
    model_selection: ModelSelectionSettings = field(default_factory=ModelSelectionSettings)


@dataclass(frozen=True)
class TauriTtsResult:
    enabled: bool = False
    provider: str = TTS_PROVIDER_NONE
    api_url: str = DEFAULT_GPT_SOVITS_API_URL
    work_dir: str = ""
    python_path: str = ""
    tts_config_path: str = ""
    timeout_seconds: int = 60


@dataclass(frozen=True)
class TauriSystemExtraResult:
    startup: StartupSettings = field(default_factory=StartupSettings)
    launch_at_login_supported: bool = True
    backchannel: BackchannelSettings = field(default_factory=BackchannelSettings)


@dataclass(frozen=True)
class TauriPluginResult:
    enabled_by_id: dict[str, bool] = field(default_factory=dict)
    settings_by_id: dict[str, dict[str, dict[str, Any]]] = field(default_factory=dict)


@dataclass(frozen=True)
class TauriSettingsResult:
    screen_awareness: ScreenAwarenessSettings
    mcp: MCPRuntimeSettings
    runtime_loop: RuntimeLoopSettings
    system_basic: TauriSystemBasicResult = field(default_factory=TauriSystemBasicResult)
    theme: ThemeSettings = field(default_factory=lambda: DEFAULT_THEME_SETTINGS)
    character: TauriCharacterResult = field(default_factory=TauriCharacterResult)
    api: TauriApiResult = field(default_factory=TauriApiResult)
    tts: TauriTtsResult = field(default_factory=TauriTtsResult)
    system_extra: TauriSystemExtraResult = field(default_factory=TauriSystemExtraResult)
    memory_curation: MemoryCurationSettings = field(default_factory=MemoryCurationSettings)
    plugins: TauriPluginResult = field(default_factory=TauriPluginResult)


class TauriMemoryRpcWorker(QObject):
    succeeded = Signal(object)
    failed = Signal(str)
    finished = Signal()

    def __init__(self, memory_store: Any | None, method: str, params: dict[str, Any]) -> None:
        super().__init__()
        self.memory_store = memory_store
        self.method = method
        self.params = dict(params)

    def run(self) -> None:
        try:
            result = dispatch_tauri_memory_rpc(self.memory_store, self.method, self.params)
        except Exception as exc:  # noqa: BLE001 - UI RPC boundary reports readable errors.
            traceback.print_exc()
            self.failed.emit(str(exc))
        else:
            self.succeeded.emit(result)
        finally:
            self.finished.emit()


def tauri_settings_trial_enabled(environ: Mapping[str, str] | None = None) -> bool:
    value = (environ or os.environ).get(TAURI_SETTINGS_TRIAL_ENV, "")
    return value.strip().lower() in {"1", "true", "yes", "on"}


def resolve_tauri_settings_binary(
    base_dir: Path,
    environ: Mapping[str, str] | None = None,
) -> Path | None:
    env = environ or os.environ
    configured = env.get(TAURI_SETTINGS_BIN_ENV)
    if configured:
        path = Path(configured)
        return path if path.is_file() else None

    root = Path(base_dir)
    candidates = (
        root / "tools" / "settings-tauri" / "src-tauri" / "target" / "release" / "sakura-settings.exe",
        root / "tools" / "settings-tauri" / "src-tauri" / "target" / "debug" / "sakura-settings.exe",
    )
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


def build_tauri_screen_awareness_request(
    settings: ScreenAwarenessSettings,
    *,
    mcp_settings: MCPRuntimeSettings | None = None,
    runtime_loop_settings: RuntimeLoopSettings | None = None,
    debug_log_settings: DebugLogSettings | None = None,
    subtitle_typing_interval_ms: int = SPEECH_TYPING_INTERVAL_MS,
    reply_segment_pause_ms: int = REPLY_SEGMENT_PAUSE_MS,
    bubble_settings: BubbleSettings | None = None,
    theme_settings: ThemeSettings | None = None,
    model: str | None = None,
    parent_widget: QWidget | None = None,
    nonce: str | None = None,
) -> dict[str, Any]:
    return build_tauri_settings_request(
        settings,
        mcp_settings=mcp_settings,
        runtime_loop_settings=runtime_loop_settings,
        debug_log_settings=debug_log_settings,
        subtitle_typing_interval_ms=subtitle_typing_interval_ms,
        reply_segment_pause_ms=reply_segment_pause_ms,
        bubble_settings=bubble_settings,
        theme_settings=theme_settings,
        model=model,
        parent_widget=parent_widget,
        nonce=nonce,
    )


def build_tauri_settings_request(
    screen_awareness_settings: ScreenAwarenessSettings,
    *,
    base_dir: Path | None = None,
    mcp_settings: MCPRuntimeSettings | None = None,
    runtime_loop_settings: RuntimeLoopSettings | None = None,
    debug_log_settings: DebugLogSettings | None = None,
    subtitle_typing_interval_ms: int = SPEECH_TYPING_INTERVAL_MS,
    reply_segment_pause_ms: int = REPLY_SEGMENT_PAUSE_MS,
    bubble_settings: BubbleSettings | None = None,
    theme_settings: ThemeSettings | None = None,
    character_registry: CharacterRegistry | None = None,
    current_character: CharacterProfile | None = None,
    portrait_scale_percent: int = PORTRAIT_SCALE_DEFAULT_PERCENT,
    control_panel_width: int = DEFAULT_CONTROL_PANEL_WIDTH,
    bubble_height: int = DEFAULT_BUBBLE_HEIGHT,
    control_panel_vertical_offset: int = DEFAULT_CONTROL_PANEL_VERTICAL_OFFSET,
    input_bar_offset: int = DEFAULT_INPUT_BAR_OFFSET,
    api_settings: ApiSettings | None = None,
    api_profiles: list[ApiConfigProfile] | None = None,
    model_selection: ModelSelectionSettings | None = None,
    tts_settings: GPTSoVITSTTSSettings | None = None,
    startup_settings: StartupSettings | None = None,
    launch_at_login_supported: bool = True,
    backchannel_settings: BackchannelSettings | None = None,
    memory_curation_settings: MemoryCurationSettings | None = None,
    plugin_settings_contributions: list[PluginSettingsContribution] | None = None,
    model: str | None = None,
    parent_widget: QWidget | None = None,
    nonce: str | None = None,
) -> dict[str, Any]:
    normalized_screen_awareness = screen_awareness_settings.normalized()
    normalized_mcp = normalize_mcp_runtime_settings(mcp_settings or MCPRuntimeSettings())
    normalized_runtime_loop = normalize_runtime_loop_settings(runtime_loop_settings)
    normalized_subtitle = normalize_subtitle_display_speed(
        subtitle_typing_interval_ms,
        reply_segment_pause_ms,
    )
    normalized_bubble = (bubble_settings or BubbleSettings()).normalized()
    width, height = _screen_estimate_size(parent_widget)
    return {
        "version": TAURI_SETTINGS_PROTOCOL_VERSION,
        "nonce": nonce or secrets.token_urlsafe(16),
        "screen_awareness": _screen_awareness_to_mapping(normalized_screen_awareness),
        "mcp": _mcp_to_mapping(normalized_mcp),
        "runtime_loop": _runtime_loop_to_mapping(normalized_runtime_loop),
        "system_basic": _system_basic_to_mapping(
            debug_log_settings or DebugLogSettings(),
            normalized_subtitle[0],
            normalized_subtitle[1],
            normalized_bubble,
        ),
        "theme": _theme_to_mapping(theme_settings),
        "character": _character_to_mapping(
            character_registry,
            current_character,
            portrait_scale_percent=portrait_scale_percent,
            control_panel_width=control_panel_width,
            bubble_height=bubble_height,
            control_panel_vertical_offset=control_panel_vertical_offset,
            input_bar_offset=input_bar_offset,
        ),
        "api": _api_to_mapping(
            api_settings or _default_api_settings(),
            api_profiles,
            model_selection,
        ),
        "tts": _tts_to_mapping(tts_settings, base_dir),
        "system_extra": _system_extra_to_mapping(
            startup_settings or StartupSettings(),
            bool(launch_at_login_supported),
            backchannel_settings or BackchannelSettings(),
        ),
        "memory": _memory_to_mapping(memory_curation_settings or MemoryCurationSettings()),
        "plugins": _plugins_to_mapping(base_dir, plugin_settings_contributions),
        "theme_defaults": _theme_to_mapping(DEFAULT_THEME_SETTINGS),
        "theme_fields": [
            {"id": field, "label": label}
            for field, label, _default in THEME_COLOR_FIELDS
        ],
        "visual_effect_modes": [
            {
                "id": mode,
                "label": {
                    VisualEffectMode.SOLID: "纯色块",
                    VisualEffectMode.GAUSSIAN_BLUR: "高斯模糊",
                    VisualEffectMode.MACOS_VISUAL_EFFECT: "macOS 原生毛玻璃",
                }.get(mode, mode),
            }
            for mode in VisualEffectMode.available_modes()
        ],
        "limits": {
            "check_interval_minutes": [
                SCREEN_AWARENESS_MIN_CHECK_INTERVAL_MINUTES,
                SCREEN_AWARENESS_MAX_CHECK_INTERVAL_MINUTES,
            ],
            "cooldown_minutes": [
                SCREEN_AWARENESS_MIN_COOLDOWN_MINUTES,
                SCREEN_AWARENESS_MAX_COOLDOWN_MINUTES,
            ],
            "screen_context_batch_limit": [
                SCREEN_AWARENESS_MIN_SCREEN_CONTEXT_BATCH_LIMIT,
                SCREEN_AWARENESS_MAX_SCREEN_CONTEXT_BATCH_LIMIT,
            ],
            "max_agent_steps_per_turn": [
                MIN_AGENT_STEPS_PER_TURN,
                MAX_CONFIGURABLE_AGENT_STEPS_PER_TURN,
            ],
            "max_tool_calls_per_step": [
                MIN_TOOL_CALLS_PER_STEP,
                MAX_CONFIGURABLE_TOOL_CALLS_PER_STEP,
            ],
            "max_tool_calls_per_turn": [
                MIN_TOOL_CALLS_PER_TURN,
                MAX_CONFIGURABLE_TOOL_CALLS_PER_TURN,
            ],
            "subtitle_typing_interval_ms": [
                SUBTITLE_TYPING_INTERVAL_MIN_MS,
                SUBTITLE_TYPING_INTERVAL_MAX_MS,
            ],
            "reply_segment_pause_ms": [
                REPLY_SEGMENT_PAUSE_MIN_MS,
                REPLY_SEGMENT_PAUSE_MAX_MS,
            ],
            "bubble_auto_hide_delay_seconds": [
                BUBBLE_AUTO_HIDE_MIN_DELAY_SECONDS,
                BUBBLE_AUTO_HIDE_MAX_DELAY_SECONDS,
            ],
            "portrait_scale_percent": [
                PORTRAIT_SCALE_MIN_PERCENT,
                PORTRAIT_SCALE_MAX_PERCENT,
            ],
            "control_panel_width": [
                MIN_CONTROL_PANEL_WIDTH,
                MAX_CONTROL_PANEL_WIDTH,
            ],
            "bubble_height": [
                MIN_BUBBLE_HEIGHT,
                MAX_BUBBLE_HEIGHT,
            ],
            "control_panel_vertical_offset": [
                MIN_CONTROL_PANEL_VERTICAL_OFFSET,
                MAX_CONTROL_PANEL_VERTICAL_OFFSET,
            ],
            "input_bar_offset": [
                MIN_INPUT_BAR_OFFSET,
                MAX_INPUT_BAR_OFFSET,
            ],
            "api_timeout_seconds": [1, 600],
            "api_temperature": [0, 2],
            "api_top_p": [0, 1],
            "api_max_tokens": [1, 32768],
            "tts_timeout_seconds": [1, 600],
            "backchannel_delay_ms": [
                BACKCHANNEL_MIN_DELAY_MS,
                BACKCHANNEL_MAX_DELAY_MS,
            ],
            "backchannel_probability": [0, 1],
            "memory_trigger_turns": [1, 50],
        },
        "estimated_tokens_per_image": estimate_screen_context_image_tokens_for_size(
            width,
            height,
            model=model,
        ),
    }


def parse_tauri_screen_awareness_result(
    path: Path,
    *,
    expected_nonce: str,
) -> ScreenAwarenessSettings:
    return parse_tauri_settings_result(path, expected_nonce=expected_nonce).screen_awareness


def parse_tauri_settings_result(
    path: Path,
    *,
    expected_nonce: str,
) -> TauriSettingsResult:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Tauri 设置结果无法读取：{exc}") from exc
    return parse_tauri_settings_payload(raw, expected_nonce=expected_nonce)


def parse_tauri_settings_payload(
    raw: object,
    *,
    expected_nonce: str,
) -> TauriSettingsResult:
    if not isinstance(raw, dict):
        raise ValueError("Tauri 设置结果格式无效。")
    if raw.get("version") != TAURI_SETTINGS_PROTOCOL_VERSION:
        raise ValueError(
            "Tauri 设置协议不匹配，请重建 Tauri 设置页或检查 SAKURA_TAURI_SETTINGS_BIN。"
        )
    if raw.get("nonce") != expected_nonce:
        raise ValueError("Tauri 设置结果校验失败。")
    settings = raw.get("screen_awareness")
    if not isinstance(settings, dict):
        raise ValueError("Tauri 设置结果缺少屏幕感知配置。")
    mcp = raw.get("mcp")
    if not isinstance(mcp, dict):
        raise ValueError("Tauri 设置结果缺少 MCP 配置。")
    runtime_loop = raw.get("runtime_loop")
    if not isinstance(runtime_loop, dict):
        raise ValueError("Tauri 设置结果缺少工具循环配置。")
    system_basic = raw.get("system_basic")
    if not isinstance(system_basic, dict):
        raise ValueError("Tauri 设置结果缺少系统基础配置。")
    debug_log = system_basic.get("debug_log")
    if not isinstance(debug_log, dict):
        raise ValueError("Tauri 设置结果缺少调试日志配置。")
    ui = system_basic.get("ui")
    if not isinstance(ui, dict):
        raise ValueError("Tauri 设置结果缺少字幕配置。")
    bubble = system_basic.get("bubble")
    if not isinstance(bubble, dict):
        raise ValueError("Tauri 设置结果缺少气泡配置。")
    theme = raw.get("theme")
    if not isinstance(theme, dict):
        raise ValueError("Tauri 设置结果缺少外观配置。")
    character = raw.get("character")
    if not isinstance(character, dict):
        raise ValueError("Tauri 设置结果缺少角色配置。")
    api = raw.get("api")
    if not isinstance(api, dict):
        raise ValueError("Tauri 设置结果缺少模型配置。")
    tts = raw.get("tts")
    if not isinstance(tts, dict):
        raise ValueError("Tauri 设置结果缺少语音配置。")
    system_extra = raw.get("system_extra")
    if not isinstance(system_extra, dict):
        raise ValueError("Tauri 设置结果缺少系统扩展配置。")
    memory = raw.get("memory")
    if not isinstance(memory, dict):
        raise ValueError("Tauri 设置结果缺少记忆配置。")
    plugins = raw.get("plugins")
    if plugins is None:
        plugins = {}
    if not isinstance(plugins, dict):
        raise ValueError("Tauri 设置结果字段无效：plugins")
    subtitle_typing_interval_ms, reply_segment_pause_ms = normalize_subtitle_display_speed(
        _required_int(ui, "subtitle_typing_interval_ms"),
        _required_int(ui, "reply_segment_pause_ms"),
    )
    api_result = _api_from_mapping_required(api)
    return TauriSettingsResult(
        screen_awareness=ScreenAwarenessSettings(
            enabled=_required_bool(settings, "enabled"),
            screen_context_enabled=_required_bool(settings, "screen_context_enabled"),
            check_interval_minutes=_required_int(settings, "check_interval_minutes"),
            cooldown_minutes=_required_int(settings, "cooldown_minutes"),
            screen_context_batch_limit=_required_int(settings, "screen_context_batch_limit"),
        ).normalized(),
        mcp=normalize_mcp_runtime_settings(
            MCPRuntimeSettings(windows_enabled=_required_bool(mcp, "windows_enabled"))
        ),
        runtime_loop=RuntimeLoopSettings(
            max_agent_steps_per_turn=_required_int(runtime_loop, "max_agent_steps_per_turn"),
            max_tool_calls_per_step=_required_int(runtime_loop, "max_tool_calls_per_step"),
            max_tool_calls_per_turn=_required_int(runtime_loop, "max_tool_calls_per_turn"),
        ).normalized(),
        system_basic=TauriSystemBasicResult(
            debug_log=_debug_log_from_mapping(debug_log),
            subtitle_typing_interval_ms=subtitle_typing_interval_ms,
            reply_segment_pause_ms=reply_segment_pause_ms,
            bubble=BubbleSettings(
                auto_hide_enabled=_required_bool(bubble, "auto_hide_enabled"),
                auto_hide_delay_seconds=_required_int(bubble, "auto_hide_delay_seconds"),
            ).normalized(),
        ),
        theme=_theme_from_mapping_required(theme),
        character=_character_from_mapping_required(character),
        api=api_result,
        tts=_tts_from_mapping_required(tts),
        system_extra=_system_extra_from_mapping_required(system_extra),
        memory_curation=_memory_from_mapping_required(memory),
        plugins=_plugins_from_mapping_required(plugins),
    )


def dispatch_tauri_memory_rpc(
    memory_store: Any | None,
    method: str,
    params: dict[str, Any],
) -> dict[str, Any]:
    if not method.startswith("memory."):
        raise ValueError(f"未知 Tauri RPC 方法：{method}")
    if memory_store is None:
        return {
            "status": "failed",
            "message": "长期记忆系统不可用。",
            "error": "memory store is not available",
            "memories": [],
        }
    if method == "memory.search":
        arguments = dict(params)
        arguments.setdefault("limit", 120)
        return memory_store.search_memory(arguments, wait=False)
    if method == "memory.upsert":
        arguments = dict(params)
        memory_id = str(arguments.get("id") or "").strip()
        if memory_id:
            arguments["id"] = memory_id
            return memory_store.update_memory(arguments, allow_sensitive=True, wait=False)
        return memory_store.create_memory(arguments, allow_sensitive=True, wait=False)
    if method == "memory.delete":
        ids = params.get("ids")
        if ids is None and params.get("id") is not None:
            ids = [params.get("id")]
        if not isinstance(ids, list):
            raise ValueError("memory.delete 需要 id 或 ids。")
        deleted: list[dict[str, Any]] = []
        failed: list[dict[str, str]] = []
        for raw_id in ids:
            memory_id = str(raw_id or "").strip()
            if not memory_id:
                continue
            result = memory_store.forget_memory({"id": memory_id}, wait=False)
            if result.get("status") in {"loading", "failed"}:
                failed.append(
                    {
                        "id": memory_id,
                        "error": str(result.get("error") or result.get("message") or "删除失败"),
                    }
                )
                continue
            deleted.append(result.get("memory") or result.get("forgotten") or {"id": memory_id})
        return {"deleted": deleted, "failed": failed, "ok": not failed}
    raise ValueError(f"未知 Tauri RPC 方法：{method}")


class TauriSettingsProcess(QObject):
    completed = Signal(object)
    applied = Signal(object)
    cancelled = Signal()
    failed = Signal(str)
    layout_preview = Signal(object)

    def __init__(
        self,
        *,
        base_dir: Path,
        settings: ScreenAwarenessSettings,
        mcp_settings: MCPRuntimeSettings | None = None,
        runtime_loop_settings: RuntimeLoopSettings | None = None,
        debug_log_settings: DebugLogSettings | None = None,
        subtitle_typing_interval_ms: int = SPEECH_TYPING_INTERVAL_MS,
        reply_segment_pause_ms: int = REPLY_SEGMENT_PAUSE_MS,
        bubble_settings: BubbleSettings | None = None,
        theme_settings: ThemeSettings | None = None,
        character_registry: CharacterRegistry | None = None,
        current_character: CharacterProfile | None = None,
        portrait_scale_percent: int = PORTRAIT_SCALE_DEFAULT_PERCENT,
        control_panel_width: int = DEFAULT_CONTROL_PANEL_WIDTH,
        bubble_height: int = DEFAULT_BUBBLE_HEIGHT,
        control_panel_vertical_offset: int = DEFAULT_CONTROL_PANEL_VERTICAL_OFFSET,
        input_bar_offset: int = DEFAULT_INPUT_BAR_OFFSET,
        api_settings: ApiSettings | None = None,
        api_profiles: list[ApiConfigProfile] | None = None,
        model_selection: ModelSelectionSettings | None = None,
        tts_settings: GPTSoVITSTTSSettings | None = None,
        startup_settings: StartupSettings | None = None,
        launch_at_login_supported: bool = True,
        backchannel_settings: BackchannelSettings | None = None,
        memory_curation_settings: MemoryCurationSettings | None = None,
        memory_store: Any | None = None,
        plugin_settings_contributions: list[PluginSettingsContribution] | None = None,
        model: str | None = None,
        parent_widget: QWidget | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self.base_dir = Path(base_dir)
        self.settings = settings
        self.mcp_settings = mcp_settings or MCPRuntimeSettings()
        self.runtime_loop_settings = normalize_runtime_loop_settings(runtime_loop_settings)
        self.debug_log_settings = debug_log_settings or DebugLogSettings()
        self.subtitle_typing_interval_ms = subtitle_typing_interval_ms
        self.reply_segment_pause_ms = reply_segment_pause_ms
        self.bubble_settings = bubble_settings or BubbleSettings()
        self.theme_settings = theme_settings or DEFAULT_THEME_SETTINGS
        self.character_registry = character_registry
        self.current_character = current_character
        self.portrait_scale_percent = portrait_scale_percent
        self.control_panel_width = control_panel_width
        self.bubble_height = bubble_height
        self.control_panel_vertical_offset = control_panel_vertical_offset
        self.input_bar_offset = input_bar_offset
        self.api_settings = api_settings or _default_api_settings()
        self.api_profiles = api_profiles
        self.model_selection = model_selection
        self.tts_settings = tts_settings
        self.startup_settings = startup_settings or StartupSettings()
        self.launch_at_login_supported = bool(launch_at_login_supported)
        self.backchannel_settings = backchannel_settings or BackchannelSettings()
        self.memory_curation_settings = memory_curation_settings or MemoryCurationSettings()
        self.memory_store = memory_store
        self.plugin_settings_contributions = list(plugin_settings_contributions or [])
        self.model = model
        self.parent_widget = parent_widget
        self._process: QProcess | None = None
        self._nonce = ""
        self._done = False
        self._cleaned = False
        self._request_payload = b""
        self._stdout_buffer = ""
        # 在途的异步探测线程，按 RPC id 索引，避免被 GC；窗口销毁时统一收尾。
        self._api_probes: dict[str, tuple[QThread, QObject]] = {}
        self._memory_rpcs: dict[str, tuple[QThread, QObject]] = {}

    def start(self) -> bool:
        if not tauri_settings_trial_enabled():
            return False
        binary = resolve_tauri_settings_binary(self.base_dir)
        if binary is None:
            return False

        request = self._build_request()
        process = QProcess(self)
        process.setProgram(str(binary))
        process.setArguments([])
        process.setWorkingDirectory(str(self.base_dir))
        process.setProcessEnvironment(QProcessEnvironment.systemEnvironment())
        process.started.connect(self._send_request)
        process.finished.connect(self._handle_finished)
        process.errorOccurred.connect(self._handle_error)
        process.readyReadStandardOutput.connect(self._handle_stdout)

        self._process = process
        self._nonce = str(request["nonce"])
        self._request_payload = json.dumps(request, ensure_ascii=False).encode("utf-8")
        process.start()
        return True

    def focus_window(self) -> bool:
        """把已打开的 Tauri 设置窗口还原并前置（用于重复唤起时找回最小化的窗口）。"""
        process = self._process
        if process is None:
            return False
        try:
            pid = int(process.processId())
        except (TypeError, ValueError):
            return False
        if pid <= 0:
            return False
        if sys.platform == "win32":
            return _restore_windows_for_pid(pid)
        return False

    def shutdown(self, timeout_ms: int = 1000) -> None:
        self._done = True
        process = self._process
        if process is not None:
            try:
                process.closeWriteChannel()
            except RuntimeError:
                pass
            try:
                if process.state() != QProcess.ProcessState.NotRunning:
                    process.terminate()
                    if not process.waitForFinished(timeout_ms):
                        process.kill()
                        process.waitForFinished(timeout_ms)
            except RuntimeError:
                pass
        self._cleanup()

    def _build_request(self) -> dict[str, Any]:
        return build_tauri_settings_request(
            self.settings,
            base_dir=self.base_dir,
            mcp_settings=self.mcp_settings,
            runtime_loop_settings=self.runtime_loop_settings,
            debug_log_settings=self.debug_log_settings,
            subtitle_typing_interval_ms=self.subtitle_typing_interval_ms,
            reply_segment_pause_ms=self.reply_segment_pause_ms,
            bubble_settings=self.bubble_settings,
            theme_settings=self.theme_settings,
            character_registry=self.character_registry,
            current_character=self.current_character,
            portrait_scale_percent=self.portrait_scale_percent,
            control_panel_width=self.control_panel_width,
            bubble_height=self.bubble_height,
            control_panel_vertical_offset=self.control_panel_vertical_offset,
            input_bar_offset=self.input_bar_offset,
            api_settings=self.api_settings,
            api_profiles=self.api_profiles,
            model_selection=self.model_selection,
            tts_settings=self.tts_settings,
            startup_settings=self.startup_settings,
            launch_at_login_supported=self.launch_at_login_supported,
            backchannel_settings=self.backchannel_settings,
            memory_curation_settings=self.memory_curation_settings,
            plugin_settings_contributions=self.plugin_settings_contributions,
            model=self.model,
            parent_widget=self.parent_widget,
        )

    def _send_request(self) -> None:
        process = self._process
        if process is None or self._done:
            return
        try:
            payload = self._request_payload + b"\n"
            if process.write(payload) < 0:
                raise OSError("write returned a negative byte count")
        except (OSError, RuntimeError) as exc:
            self._done = True
            self.failed.emit(f"Tauri 设置请求发送失败：{exc}")
            try:
                process.kill()
            except RuntimeError:
                pass
            self._cleanup()

    def _handle_finished(self, exit_code: int, exit_status: QProcess.ExitStatus) -> None:
        self._handle_stdout(flush=True)
        try:
            if not self._done:
                self._done = True
                if exit_status != QProcess.ExitStatus.NormalExit or exit_code != 0:
                    self.failed.emit(
                        "Tauri 设置窗口异常退出"
                        f"（exit_code={exit_code}），请重建 Tauri 设置页或检查 "
                        f"{TAURI_SETTINGS_BIN_ENV}。"
                    )
                    return
                self.cancelled.emit()
        finally:
            self._cleanup()

    def _handle_stdout(self, *, flush: bool = False) -> None:
        process = self._process
        if process is None:
            return
        chunk = bytes(process.readAllStandardOutput()).decode("utf-8", errors="replace")
        if not chunk and not flush:
            return
        self._stdout_buffer += chunk
        *lines, self._stdout_buffer = self._stdout_buffer.split("\n")
        if flush and self._stdout_buffer:
            lines.append(self._stdout_buffer)
            self._stdout_buffer = ""
        for line in lines:
            if self._done:
                return
            stripped = line.strip()
            if stripped.startswith(TAURI_LAYOUT_PREVIEW_MARKER):
                payload = stripped[len(TAURI_LAYOUT_PREVIEW_MARKER):]
                try:
                    data = json.loads(payload)
                except json.JSONDecodeError:
                    continue
                if isinstance(data, dict):
                    self.layout_preview.emit(data)
                continue
            if stripped.startswith(TAURI_SETTINGS_RPC_MARKER):
                payload = stripped[len(TAURI_SETTINGS_RPC_MARKER):]
                self._handle_rpc_request(payload)
                continue
            if not stripped.startswith(TAURI_SETTINGS_RESULT_MARKER):
                continue
            payload = stripped[len(TAURI_SETTINGS_RESULT_MARKER):]
            try:
                data = json.loads(payload)
                result = parse_tauri_settings_payload(
                    data,
                    expected_nonce=self._nonce,
                )
            except (ValueError, json.JSONDecodeError) as exc:
                self._done = True
                self.failed.emit(str(exc))
                continue
            # 「应用」：持久化但保持窗口打开，进程继续监听后续保存/应用。
            if isinstance(data, dict) and data.get("keep_open"):
                self.applied.emit(result)
                continue
            self._done = True
            self.completed.emit(result)

    def _handle_rpc_request(self, payload: str) -> None:
        try:
            request = json.loads(payload)
        except json.JSONDecodeError as exc:
            self._send_rpc_response("", ok=False, error=f"RPC 请求格式无效：{exc}")
            return
        if not isinstance(request, dict):
            self._send_rpc_response("", ok=False, error="RPC 请求必须是对象。")
            return
        request_id = request.get("id")
        method = request.get("method")
        params = request.get("params", {})
        if not isinstance(request_id, str) or not request_id:
            self._send_rpc_response("", ok=False, error="RPC 请求缺少 id。")
            return
        if not isinstance(method, str) or not method:
            self._send_rpc_response(request_id, ok=False, error="RPC 请求缺少 method。")
            return
        if not isinstance(params, dict):
            self._send_rpc_response(request_id, ok=False, error="RPC params 必须是对象。")
            return
        # 模型检测/连通性测试是阻塞网络调用，放到后台线程，避免冻结主程序 Qt 事件循环。
        if method in ("api.list_models", "api.test_connection"):
            self._dispatch_api_probe(request_id, method, params)
            return
        if method.startswith("memory."):
            self._dispatch_memory_rpc(request_id, method, params)
            return
        try:
            result = self._dispatch_rpc(method, params)
        except Exception as exc:  # noqa: BLE001
            traceback.print_exc()
            self._send_rpc_response(request_id, ok=False, error=str(exc))
            return
        self._send_rpc_response(request_id, ok=True, result=result)

    def _dispatch_api_probe(self, request_id: str, method: str, params: dict[str, Any]) -> None:
        """在后台线程跑 list_models / test_connection，完成后经 RPC 回传结果。"""
        try:
            settings = _api_probe_settings(method, params)
        except ValueError as exc:
            self._send_rpc_response(request_id, ok=False, error=str(exc))
            return

        thread = QThread(self)
        if method == "api.test_connection":
            worker: QObject = ApiConnectionTestWorker(settings)
        else:
            worker = ApiModelListProbeWorker(settings)
        worker.moveToThread(thread)
        self._api_probes[request_id] = (thread, worker)
        thread.started.connect(worker.run)

        def _on_success(payload: Any) -> None:
            if method == "api.test_connection":
                self._send_rpc_response(request_id, ok=True, result={"message": str(payload)})
            else:
                models = [str(item) for item in payload if str(item).strip()]
                self._send_rpc_response(request_id, ok=True, result={"models": models})

        def _on_failure(message: str) -> None:
            self._send_rpc_response(request_id, ok=False, error=str(message) or "请求失败。")

        worker.succeeded.connect(_on_success)
        worker.failed.connect(_on_failure)
        worker.finished.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(lambda rid=request_id: self._api_probes.pop(rid, None))
        thread.start()

    def _dispatch_memory_rpc(self, request_id: str, method: str, params: dict[str, Any]) -> None:
        """在后台线程访问 mem0/Qdrant，避免记忆页加载阻塞 Qt 主事件循环。"""
        thread = QThread()
        worker: QObject = TauriMemoryRpcWorker(self.memory_store, method, params)
        worker.moveToThread(thread)
        self._memory_rpcs[request_id] = (thread, worker)
        thread.started.connect(worker.run)

        def _on_success(payload: object) -> None:
            result = payload if isinstance(payload, dict) else {"result": payload}
            self._send_rpc_response(request_id, ok=True, result=result)

        def _on_failure(message: str) -> None:
            self._send_rpc_response(request_id, ok=False, error=str(message) or "请求失败。")

        worker.succeeded.connect(_on_success)
        worker.failed.connect(_on_failure)
        worker.finished.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(lambda rid=request_id: self._memory_rpcs.pop(rid, None))
        thread.start()

    def _dispatch_rpc(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        if method == "plugin.settings_action":
            return dispatch_tauri_plugin_settings_action(
                self.plugin_settings_contributions,
                params,
            )
        if method.startswith("memory."):
            return dispatch_tauri_memory_rpc(self.memory_store, method, params)
        raise ValueError(f"未知 Tauri RPC 方法：{method}")

    def _send_rpc_response(
        self,
        request_id: str,
        *,
        ok: bool,
        result: dict[str, Any] | None = None,
        error: str = "",
    ) -> None:
        process = self._process
        if process is None or self._done:
            return
        payload = {"id": request_id, "ok": bool(ok)}
        if ok:
            payload["result"] = result or {}
        else:
            payload["error"] = error or "RPC 请求失败。"
        line = (
            TAURI_SETTINGS_RPC_RESULT_MARKER
            + json.dumps(payload, ensure_ascii=False, default=str)
            + "\n"
        )
        try:
            process.write(line.encode("utf-8"))
        except RuntimeError:
            return

    def _handle_error(self, error: QProcess.ProcessError) -> None:
        if self._done:
            return
        self._done = True
        self.failed.emit(f"Tauri 设置窗口启动失败：{error.name}。")
        self._cleanup()

    def _cleanup(self) -> None:
        if self._cleaned:
            return
        self._cleaned = True
        self._request_payload = b""
        # 收尾在途 RPC 线程，避免线程仍在运行时随 self 被销毁。
        for pending in (self._api_probes, self._memory_rpcs):
            for thread, _worker in list(pending.values()):
                try:
                    thread.quit()
                    thread.wait(3000)
                except RuntimeError:
                    pass
            pending.clear()
        process = self._process
        if process is not None:
            process.deleteLater()
        self.deleteLater()


def _restore_windows_for_pid(pid: int) -> bool:
    """枚举属于该进程的可见顶层窗口，若被最小化则还原，并尝试前置。"""
    try:
        import ctypes
        from ctypes import wintypes
    except Exception:  # noqa: BLE001
        return False

    user32 = ctypes.windll.user32
    sw_restore = 9
    found: list[int] = []

    enum_proc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def _callback(hwnd: int, _lparam: int) -> bool:
        # 只挑可见、无属主的顶层窗口，避免命中 WebView2 的工具/提示子窗口。
        if not user32.IsWindowVisible(hwnd):
            return True
        if user32.GetWindow(hwnd, 4):  # GW_OWNER
            return True
        window_pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(window_pid))
        if window_pid.value == pid:
            found.append(hwnd)
        return True

    try:
        user32.EnumWindows(enum_proc(_callback), 0)
    except Exception:  # noqa: BLE001
        return False
    if not found:
        return False
    for hwnd in found:
        if user32.IsIconic(hwnd):
            user32.ShowWindow(hwnd, sw_restore)
        user32.SetForegroundWindow(hwnd)
    return True


def _screen_awareness_to_mapping(settings: ScreenAwarenessSettings) -> dict[str, object]:
    return {
        "enabled": bool(settings.enabled),
        "screen_context_enabled": bool(settings.screen_context_enabled),
        "check_interval_minutes": int(settings.check_interval_minutes),
        "cooldown_minutes": int(settings.cooldown_minutes),
        "screen_context_batch_limit": int(settings.screen_context_batch_limit),
    }


def _mcp_to_mapping(settings: MCPRuntimeSettings) -> dict[str, object]:
    return {
        "windows_enabled": bool(settings.windows_enabled),
    }


def _runtime_loop_to_mapping(settings: RuntimeLoopSettings) -> dict[str, object]:
    return {
        "max_agent_steps_per_turn": int(settings.max_agent_steps_per_turn),
        "max_tool_calls_per_step": int(settings.max_tool_calls_per_step),
        "max_tool_calls_per_turn": int(settings.max_tool_calls_per_turn),
    }


def _system_basic_to_mapping(
    debug_log: DebugLogSettings,
    subtitle_typing_interval_ms: int,
    reply_segment_pause_ms: int,
    bubble: BubbleSettings,
) -> dict[str, object]:
    return {
        "debug_log": {
            "enabled": bool(debug_log.enabled),
            "body_enabled": bool(debug_log.body_enabled),
            "file_enabled": bool(debug_log.file_enabled),
            "stage_debug_overlay": bool(debug_log.stage_debug_overlay),
            "stage_collision_mask": bool(debug_log.stage_collision_mask),
        },
        "ui": {
            "subtitle_typing_interval_ms": int(subtitle_typing_interval_ms),
            "reply_segment_pause_ms": int(reply_segment_pause_ms),
        },
        "bubble": {
            "auto_hide_enabled": bool(bubble.auto_hide_enabled),
            "auto_hide_delay_seconds": int(bubble.auto_hide_delay_seconds),
        },
    }


def _theme_to_mapping(settings: ThemeSettings | None) -> dict[str, object]:
    return theme_to_mapping(settings or DEFAULT_THEME_SETTINGS)


def _character_theme_colors(profile: CharacterProfile | None) -> dict[str, object]:
    """角色自带主题的配色；用于在设置页切换角色时跟随换色（与 PySide 行为一致）。"""
    theme = getattr(profile, "theme_settings", None) if profile is not None else None
    return theme_colors_to_mapping(theme or DEFAULT_THEME_SETTINGS)


def _character_to_mapping(
    character_registry: CharacterRegistry | None,
    current_character: CharacterProfile | None,
    *,
    portrait_scale_percent: int,
    control_panel_width: int,
    bubble_height: int,
    control_panel_vertical_offset: int,
    input_bar_offset: int,
) -> dict[str, object]:
    profiles = getattr(character_registry, "profiles", {}) if character_registry is not None else {}
    characters: list[dict[str, object]] = []
    if isinstance(profiles, Mapping):
        iterable = profiles.values()
    else:
        iterable = ()
    for profile in iterable:
        profile_id = str(getattr(profile, "id", "")).strip()
        if not profile_id:
            continue
        characters.append(
            {
                "id": profile_id,
                "display_name": str(getattr(profile, "display_name", "") or profile_id),
                "has_voice": getattr(profile, "voice", None) is not None,
                "theme": _character_theme_colors(profile),
            }
        )
    current_id = str(getattr(current_character, "id", "") or "").strip()
    if current_id and not any(item["id"] == current_id for item in characters):
        characters.append(
            {
                "id": current_id,
                "display_name": str(
                    getattr(current_character, "display_name", "") or current_id
                ),
                "has_voice": getattr(current_character, "voice", None) is not None,
                "theme": _character_theme_colors(current_character),
            }
        )
    return {
        "current_character_id": current_id,
        "characters": characters,
        "layout": {
            "portrait_scale_percent": normalize_portrait_scale_percent(portrait_scale_percent),
            "control_panel_width": normalize_control_panel_width(control_panel_width),
            "bubble_height": normalize_bubble_height(bubble_height),
            "control_panel_vertical_offset": normalize_control_panel_vertical_offset(
                control_panel_vertical_offset
            ),
            "input_bar_offset": normalize_input_bar_offset(input_bar_offset),
        },
    }


def _api_to_mapping(
    settings: ApiSettings,
    profiles: list[ApiConfigProfile] | None,
    model_selection: ModelSelectionSettings | None,
) -> dict[str, object]:
    normalized_profiles = _normalized_request_api_profiles(settings, profiles)
    normalized_selection = _normalized_request_model_selection(
        settings,
        normalized_profiles,
        model_selection,
    )
    return {
        "settings": {
            "timeout_seconds": _clamp_int_value(settings.timeout_seconds, 1, 600),
            "temperature": _optional_float_value(settings.temperature, 0.0, 2.0),
            "top_p": _optional_float_value(settings.top_p, 0.0, 1.0),
            "max_tokens": _optional_positive_int_value(settings.max_tokens, 32768),
        },
        "profiles": [
            {
                "id": profile.id,
                "alias": profile.alias,
                "base_url": profile.base_url,
                "api_key": profile.api_key,
                "models": list(profile.models),
            }
            for profile in normalized_profiles
        ],
        "model_selection": _model_selection_to_mapping(normalized_selection),
        "slot_fields": [
            {
                "id": slot,
                "label": MODEL_SLOT_LABELS.get(slot, slot),
                "required": slot == MODEL_SLOT_CHAT,
            }
            for slot in MODEL_SLOT_ORDER
        ],
    }


def _tts_to_mapping(settings: GPTSoVITSTTSSettings | None, base_dir: Path | None) -> dict[str, object]:
    current = settings or GPTSoVITSTTSSettings(
        enabled=False,
        api_url=DEFAULT_GPT_SOVITS_API_URL,
        ref_audio_path=Path(),
        ref_text_path=Path(),
        ref_text="",
    )
    return {
        "enabled": bool(current.enabled),
        "provider": str(current.provider or TTS_PROVIDER_NONE),
        "providers": [
            {"id": TTS_PROVIDER_NONE, "label": "关闭"},
            {"id": TTS_PROVIDER_GPT_SOVITS, "label": "内置 GPT-SoVITS"},
            {"id": TTS_PROVIDER_CUSTOM_GPT_SOVITS, "label": "外部 GPT-SoVITS"},
            {"id": TTS_PROVIDER_GENIE, "label": "Genie TTS"},
        ],
        "api_url": current.api_url
        or (
            DEFAULT_GENIE_TTS_API_URL
            if current.provider == TTS_PROVIDER_GENIE
            else DEFAULT_GPT_SOVITS_API_URL
        ),
        "work_dir": _path_to_text(current.work_dir),
        "python_path": _path_to_text(current.python_path),
        "tts_config_path": _path_to_text(current.tts_config_path),
        "provider_defaults": _tts_provider_defaults(base_dir),
        "timeout_seconds": _clamp_int_value(current.timeout_seconds, 1, 600),
    }


def _tts_provider_defaults(base_dir: Path | None) -> dict[str, dict[str, str]]:
    gpus = list_nvidia_gpus() if base_dir is not None else None

    def bundled(provider: str, api_url: str) -> dict[str, str]:
        work_dir = (
            default_provider_bundle_work_dir(provider, base_dir, gpus=gpus)
            if base_dir is not None
            else None
        )
        return {
            "api_url": api_url,
            "work_dir": _path_to_text(work_dir),
            "python_path": _path_to_text(work_dir / "runtime" / "python.exe" if work_dir is not None else None),
            "notice": default_provider_bundle_notice(provider, base_dir, gpus=gpus) if base_dir is not None else "",
        }

    return {
        TTS_PROVIDER_GPT_SOVITS: bundled(TTS_PROVIDER_GPT_SOVITS, DEFAULT_GPT_SOVITS_API_URL),
        TTS_PROVIDER_GENIE: bundled(TTS_PROVIDER_GENIE, DEFAULT_GENIE_TTS_API_URL),
        TTS_PROVIDER_CUSTOM_GPT_SOVITS: {
            "api_url": DEFAULT_GPT_SOVITS_API_URL,
            "work_dir": "",
            "python_path": "",
            "notice": "",
        },
    }


def _system_extra_to_mapping(
    startup: StartupSettings,
    launch_at_login_supported: bool,
    backchannel: BackchannelSettings,
) -> dict[str, object]:
    normalized_backchannel = backchannel.normalized()
    return {
        "startup": {
            "launch_at_login": bool(startup.launch_at_login),
            "launch_at_login_supported": bool(launch_at_login_supported),
        },
        "backchannel": {
            "enabled": bool(normalized_backchannel.enabled),
            "mode": normalized_backchannel.mode,
            "delay_ms": int(normalized_backchannel.delay_ms),
            "probability": float(normalized_backchannel.probability),
            "tts_enabled": bool(normalized_backchannel.tts_enabled),
            "timeout_ms": int(normalized_backchannel.timeout_ms),
        },
    }


def _memory_to_mapping(settings: MemoryCurationSettings) -> dict[str, object]:
    return {
        "curation": {
            "enabled": bool(settings.enabled),
            "trigger_turns": _clamp_int_value(settings.trigger_turns, 1, 50),
            "backfill_limit": max(1, int(settings.backfill_limit)),
        },
        "layers": [
            {"id": layer, "label": MEMORY_LAYER_LABELS.get(layer, layer)}
            for layer in MEMORY_LAYERS
        ],
        "defaults": {
            "layer": DEFAULT_MEMORY_LAYER,
            "source": DEFAULT_MEMORY_SOURCE,
            "importance": DEFAULT_MEMORY_IMPORTANCE,
            "confidence": DEFAULT_MEMORY_CONFIDENCE,
        },
        "page_size": 120,
    }


def _plugins_to_mapping(
    base_dir: Path | None,
    plugin_settings_contributions: list[PluginSettingsContribution] | None = None,
) -> dict[str, object]:
    items: list[dict[str, object]] = []
    settings_by_plugin = _group_plugin_settings(plugin_settings_contributions)
    if base_dir is not None:
        for spec in PluginDiscovery(Path(base_dir)).discover():
            plugin_id = str(spec.plugin_id or "").strip()
            if not plugin_id:
                continue
            items.append(
                {
                    "id": plugin_id,
                    "name": str(spec.name or plugin_id),
                    "author": str(spec.author or ""),
                    "version": str(spec.version or "0.0.0"),
                    "description": str(spec.description or ""),
                    "enabled": bool(spec.enabled),
                    "required": bool(spec.required),
                    "permissions": list(spec.permissions),
                    "source": str(spec.source or ""),
                    "priority": int(spec.priority),
                    "entry": str(spec.entry or ""),
                    "settings": [
                        _plugin_settings_to_mapping(contribution)
                        for contribution in settings_by_plugin.get(plugin_id, [])
                    ],
                }
            )
    return {
        "items": items,
        "permission_labels": PLUGIN_PERMISSION_LABELS,
    }


def _group_plugin_settings(
    contributions: list[PluginSettingsContribution] | None,
) -> dict[str, list[PluginSettingsContribution]]:
    grouped: dict[str, list[PluginSettingsContribution]] = {}
    for contribution in contributions or []:
        plugin_id = str(contribution.plugin_id or "").strip()
        if not plugin_id:
            continue
        grouped.setdefault(plugin_id, []).append(contribution)
    for values in grouped.values():
        values.sort(key=lambda item: item.order)
    return grouped


def _plugin_settings_to_mapping(contribution: PluginSettingsContribution) -> dict[str, object]:
    values: dict[str, Any] = {}
    error = ""
    if callable(contribution.load):
        try:
            loaded = contribution.load()
            if isinstance(loaded, dict):
                values = dict(loaded)
        except Exception as exc:  # noqa: BLE001 - 单个插件设置读取失败不阻断设置页
            error = str(exc)
    fields = [_plugin_settings_field_to_mapping(field, values) for field in contribution.fields]
    return {
        "section_id": str(contribution.section_id),
        "title": str(contribution.title),
        "order": float(contribution.order),
        "values": {
            field["key"]: field["value"]
            for field in fields
            if isinstance(field.get("key"), str)
        },
        "fields": fields,
        "actions": [
            {
                "action_id": str(action.action_id),
                "label": str(action.label),
                "description": str(action.description or ""),
                "danger": bool(action.danger),
            }
            for action in contribution.actions
        ],
        "error": error,
    }


def _plugin_settings_field_to_mapping(
    field: PluginSettingsField,
    values: dict[str, Any],
) -> dict[str, object]:
    key = str(field.key)
    value = values.get(key, field.default)
    mapping: dict[str, object] = {
        "key": key,
        "label": str(field.label),
        "type": str(field.field_type or "text"),
        "value": value,
        "default": field.default,
        "description": str(field.description or ""),
        "options": [
            {"value": option.get("value"), "label": str(option.get("label", option.get("value", "")))}
            for option in field.options
            if isinstance(option, dict)
        ],
        "required": bool(field.required),
        "readonly": bool(field.readonly),
        "copyable": bool(field.copyable),
        "restart_required": bool(field.restart_required),
    }
    if field.minimum is not None:
        mapping["minimum"] = field.minimum
    if field.maximum is not None:
        mapping["maximum"] = field.maximum
    if field.step is not None:
        mapping["step"] = field.step
    return mapping


def apply_tauri_plugin_settings(
    contributions: list[PluginSettingsContribution] | None,
    settings_by_id: dict[str, dict[str, dict[str, Any]]],
) -> bool:
    """校验并保存 Tauri 返回的插件设置。"""
    if not settings_by_id:
        return False
    by_key = _plugin_settings_by_key(contributions)
    changed = False
    for plugin_id, sections in settings_by_id.items():
        for section_id, values in sections.items():
            contribution = by_key.get((plugin_id, section_id))
            if contribution is None:
                raise ValueError(f"未知插件设置区块：{plugin_id}.{section_id}")
            normalized = _normalize_plugin_setting_values(contribution, values)
            current = _plugin_current_settings(contribution)
            if normalized == current:
                continue
            if callable(contribution.save):
                contribution.save(normalized)
                changed = True
    return changed


def dispatch_tauri_plugin_settings_action(
    contributions: list[PluginSettingsContribution] | None,
    params: dict[str, Any],
) -> dict[str, Any]:
    """执行 Tauri 插件设置页的受控动作。"""
    plugin_id = _required_plugin_rpc_str(params, "plugin_id")
    section_id = _required_plugin_rpc_str(params, "section_id")
    action_id = _required_plugin_rpc_str(params, "action_id")
    contribution = _plugin_settings_by_key(contributions).get((plugin_id, section_id))
    if contribution is None:
        raise ValueError(f"未知插件设置区块：{plugin_id}.{section_id}")
    action = next(
        (item for item in contribution.actions if item.action_id == action_id),
        None,
    )
    if action is None or not callable(action.handler):
        raise ValueError(f"未知插件设置动作：{plugin_id}.{section_id}.{action_id}")
    raw_values = params.get("values", {})
    if not isinstance(raw_values, dict):
        raise ValueError("插件设置动作 values 必须是对象。")
    values = _normalize_plugin_setting_values(contribution, raw_values)
    result = action.handler(values)
    if isinstance(result, dict):
        return result
    return {"result": result}


def _plugin_settings_by_key(
    contributions: list[PluginSettingsContribution] | None,
) -> dict[tuple[str, str], PluginSettingsContribution]:
    by_key: dict[tuple[str, str], PluginSettingsContribution] = {}
    for contribution in contributions or []:
        plugin_id = str(contribution.plugin_id or "").strip()
        section_id = str(contribution.section_id or "").strip()
        if not plugin_id or not section_id:
            continue
        by_key[(plugin_id, section_id)] = contribution
    return by_key


def _plugin_current_settings(contribution: PluginSettingsContribution) -> dict[str, Any]:
    values: dict[str, Any] = {}
    if callable(contribution.load):
        try:
            loaded = contribution.load()
        except Exception:  # noqa: BLE001 - 读取失败时仍允许用户保存新值
            loaded = None
        if isinstance(loaded, dict):
            values = loaded
    return _normalize_plugin_setting_values(contribution, values)


def _normalize_plugin_setting_values(
    contribution: PluginSettingsContribution,
    values: dict[str, Any],
) -> dict[str, Any]:
    fields_by_key = {str(field.key): field for field in contribution.fields}
    result: dict[str, Any] = {}
    for key in values:
        field = fields_by_key.get(str(key))
        if field is None:
            raise ValueError(f"未知插件设置字段：{contribution.plugin_id}.{contribution.section_id}.{key}")
        if field.readonly:
            continue
    for key, field in fields_by_key.items():
        if field.readonly:
            continue
        value = values.get(key, field.default)
        result[key] = _normalize_plugin_setting_value(contribution, field, value)
    return result


def _normalize_plugin_setting_value(
    contribution: PluginSettingsContribution,
    field: PluginSettingsField,
    value: Any,
) -> Any:
    field_type = str(field.field_type or "text").strip().lower()
    label = f"{contribution.plugin_id}.{contribution.section_id}.{field.key}"
    if field_type == "boolean":
        if not isinstance(value, bool):
            raise ValueError(f"插件设置字段无效：{label}")
        return value
    if field_type == "integer":
        if isinstance(value, bool):
            raise ValueError(f"插件设置字段无效：{label}")
        try:
            parsed = int(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"插件设置字段无效：{label}") from exc
        if field.minimum is not None:
            parsed = max(int(field.minimum), parsed)
        if field.maximum is not None:
            parsed = min(int(field.maximum), parsed)
        return parsed
    if field_type == "number":
        if isinstance(value, bool):
            raise ValueError(f"插件设置字段无效：{label}")
        try:
            parsed_float = float(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"插件设置字段无效：{label}") from exc
        if field.minimum is not None:
            parsed_float = max(float(field.minimum), parsed_float)
        if field.maximum is not None:
            parsed_float = min(float(field.maximum), parsed_float)
        return parsed_float
    if field_type == "select":
        allowed = [option.get("value") for option in field.options if isinstance(option, dict)]
        if allowed and value not in allowed:
            raise ValueError(f"插件设置字段无效：{label}")
        return value
    text = "" if value is None else str(value)
    if field.required and not text.strip():
        raise ValueError(f"插件设置字段不能为空：{label}")
    return text


def _required_plugin_rpc_str(mapping: dict[str, Any], key: str) -> str:
    value = mapping.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"插件设置 RPC 缺少字段：{key}")
    return value.strip()


def _debug_log_from_mapping(mapping: dict[str, Any]) -> DebugLogSettings:
    enabled = _required_bool(mapping, "enabled")
    return DebugLogSettings(
        enabled=enabled,
        body_enabled=enabled and _required_bool(mapping, "body_enabled"),
        file_enabled=_required_bool(mapping, "file_enabled"),
        stage_debug_overlay=_required_bool(mapping, "stage_debug_overlay"),
        stage_collision_mask=_required_bool(mapping, "stage_collision_mask"),
    )


def _theme_from_mapping_required(mapping: dict[str, Any]) -> ThemeSettings:
    values: dict[str, str] = {}
    for field, _label, _default in THEME_COLOR_FIELDS:
        value = mapping.get(field)
        if not isinstance(value, str):
            raise ValueError(f"Tauri 设置结果字段无效：theme.{field}")
        values[field] = value
    ai_enabled = mapping.get("ai_enabled")
    if not isinstance(ai_enabled, bool):
        raise ValueError("Tauri 设置结果字段无效：theme.ai_enabled")
    visual_effect_mode = mapping.get("visual_effect_mode")
    if not isinstance(visual_effect_mode, str):
        raise ValueError("Tauri 设置结果字段无效：theme.visual_effect_mode")
    return ThemeSettings(
        **values,
        ai_enabled=ai_enabled,
        visual_effect_mode=visual_effect_mode,
    ).normalized()


def _character_from_mapping_required(mapping: dict[str, Any]) -> TauriCharacterResult:
    character_id = _required_str(mapping, "current_character_id").strip()
    if not character_id:
        raise ValueError("Tauri 设置结果字段无效：character.current_character_id")
    layout = mapping.get("layout")
    if not isinstance(layout, dict):
        raise ValueError("Tauri 设置结果缺少角色布局配置。")
    return TauriCharacterResult(
        character_id=character_id,
        portrait_scale_percent=normalize_portrait_scale_percent(
            _required_int(layout, "portrait_scale_percent")
        ),
        control_panel_width=normalize_control_panel_width(
            _required_int(layout, "control_panel_width")
        ),
        bubble_height=normalize_bubble_height(_required_int(layout, "bubble_height")),
        control_panel_vertical_offset=normalize_control_panel_vertical_offset(
            _required_int(layout, "control_panel_vertical_offset")
        ),
        input_bar_offset=normalize_input_bar_offset(_required_int(layout, "input_bar_offset")),
    )


def _api_from_mapping_required(mapping: dict[str, Any]) -> TauriApiResult:
    raw_profiles = mapping.get("profiles")
    if not isinstance(raw_profiles, list):
        raise ValueError("Tauri 设置结果缺少 API 供应商配置。")
    raw_selection = mapping.get("model_selection")
    if not isinstance(raw_selection, dict):
        raise ValueError("Tauri 设置结果缺少模型槽位配置。")
    profiles = _api_profiles_from_raw(
        raw_profiles,
        selected_models_by_profile_id=_selected_models_by_profile_id(raw_selection),
    )
    model_selection = _model_selection_from_mapping_required(raw_selection)
    raw_settings = mapping.get("settings")
    if not isinstance(raw_settings, dict):
        raise ValueError("Tauri 设置结果缺少 API 基础配置。")
    defaults = _default_api_settings()
    base_settings = ApiSettings(
        base_url=defaults.base_url,
        api_key=defaults.api_key,
        model=defaults.model,
        timeout_seconds=_clamp_int_value(_required_int(raw_settings, "timeout_seconds"), 1, 600),
        temperature=_optional_float_from_mapping(raw_settings, "temperature", 0.0, 2.0),
        top_p=_optional_float_from_mapping(raw_settings, "top_p", 0.0, 1.0),
        max_tokens=_optional_int_from_mapping(raw_settings, "max_tokens", 1, 32768),
    )
    resolved = resolve_model_slot(
        profiles,
        model_selection,
        MODEL_SLOT_CHAT,
        base_settings,
    )
    if resolved is None:
        raise ValueError("Tauri 设置结果中的聊天模型不可用。")
    return TauriApiResult(
        settings=resolved.settings,
        profiles=profiles,
        model_selection=model_selection,
    )


def _tts_from_mapping_required(mapping: dict[str, Any]) -> TauriTtsResult:
    enabled = _required_bool(mapping, "enabled")
    provider = _normalize_tauri_tts_provider(_required_str(mapping, "provider"), enabled)
    api_url = _required_str(mapping, "api_url").strip()
    if enabled and not _is_http_url(api_url):
        raise ValueError("Tauri 设置结果字段无效：tts.api_url")
    if not api_url:
        api_url = DEFAULT_GENIE_TTS_API_URL if provider == TTS_PROVIDER_GENIE else DEFAULT_GPT_SOVITS_API_URL
    return TauriTtsResult(
        enabled=enabled,
        provider=provider,
        api_url=api_url,
        work_dir=_required_str(mapping, "work_dir").strip(),
        python_path=_required_str(mapping, "python_path").strip(),
        tts_config_path=_required_str(mapping, "tts_config_path").strip(),
        timeout_seconds=_clamp_int_value(_required_int(mapping, "timeout_seconds"), 1, 600),
    )


def _system_extra_from_mapping_required(mapping: dict[str, Any]) -> TauriSystemExtraResult:
    startup = mapping.get("startup")
    if not isinstance(startup, dict):
        raise ValueError("Tauri 设置结果缺少启动配置。")
    backchannel = mapping.get("backchannel")
    if not isinstance(backchannel, dict):
        raise ValueError("Tauri 设置结果缺少接话配置。")
    return TauriSystemExtraResult(
        startup=StartupSettings(
            launch_at_login=_required_bool(startup, "launch_at_login"),
        ),
        launch_at_login_supported=_required_bool(startup, "launch_at_login_supported"),
        backchannel=BackchannelSettings(
            enabled=_required_bool(backchannel, "enabled"),
            mode=_required_str(backchannel, "mode"),
            delay_ms=_required_int(backchannel, "delay_ms"),
            probability=_required_number(backchannel, "probability"),
            tts_enabled=_required_bool(backchannel, "tts_enabled"),
            timeout_ms=_required_int(backchannel, "timeout_ms"),
        ).normalized(),
    )


def _memory_from_mapping_required(mapping: dict[str, Any]) -> MemoryCurationSettings:
    curation = mapping.get("curation")
    if not isinstance(curation, dict):
        raise ValueError("Tauri 设置结果缺少记忆整理配置。")
    return MemoryCurationSettings(
        enabled=_required_bool(curation, "enabled"),
        trigger_turns=_clamp_int_value(_required_int(curation, "trigger_turns"), 1, 50),
        backfill_limit=max(1, _required_int(curation, "backfill_limit")),
    )


def _plugins_from_mapping_required(mapping: dict[str, Any]) -> TauriPluginResult:
    raw = mapping.get("enabled_by_id", {})
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise ValueError("Tauri 设置结果字段无效：plugins.enabled_by_id")
    enabled_by_id: dict[str, bool] = {}
    for plugin_id, enabled in raw.items():
        if not isinstance(plugin_id, str) or not plugin_id.strip():
            raise ValueError("Tauri 设置结果字段无效：plugins.enabled_by_id")
        if not isinstance(enabled, bool):
            raise ValueError(f"Tauri 设置结果字段无效：plugins.enabled_by_id.{plugin_id}")
        enabled_by_id[plugin_id.strip()] = enabled
    raw_settings = mapping.get("settings_by_id", {})
    if raw_settings is None:
        raw_settings = {}
    if not isinstance(raw_settings, dict):
        raise ValueError("Tauri 设置结果字段无效：plugins.settings_by_id")
    settings_by_id: dict[str, dict[str, dict[str, Any]]] = {}
    for plugin_id, sections in raw_settings.items():
        if not isinstance(plugin_id, str) or not plugin_id.strip():
            raise ValueError("Tauri 设置结果字段无效：plugins.settings_by_id")
        if not isinstance(sections, dict):
            raise ValueError(f"Tauri 设置结果字段无效：plugins.settings_by_id.{plugin_id}")
        section_values: dict[str, dict[str, Any]] = {}
        for section_id, values in sections.items():
            if not isinstance(section_id, str) or not section_id.strip():
                raise ValueError(f"Tauri 设置结果字段无效：plugins.settings_by_id.{plugin_id}")
            if not isinstance(values, dict):
                raise ValueError(
                    f"Tauri 设置结果字段无效：plugins.settings_by_id.{plugin_id}.{section_id}"
                )
            section_values[section_id.strip()] = dict(values)
        settings_by_id[plugin_id.strip()] = section_values
    return TauriPluginResult(enabled_by_id=enabled_by_id, settings_by_id=settings_by_id)


def _normalized_request_api_profiles(
    settings: ApiSettings,
    profiles: list[ApiConfigProfile] | None,
) -> list[ApiConfigProfile]:
    normalized: list[ApiConfigProfile] = []
    seen: set[str] = set()
    for profile in profiles or []:
        profile_id = str(profile.id).strip()
        if not profile_id or profile_id in seen:
            continue
        seen.add(profile_id)
        models = normalize_provider_models(profile.models)
        if not models:
            models = normalize_provider_models([settings.model])
        normalized.append(
            ApiConfigProfile(
                id=profile_id,
                alias=str(profile.alias or profile_id).strip(),
                base_url=str(profile.base_url or settings.base_url).strip().rstrip("/"),
                api_key=str(profile.api_key or "").strip(),
                models=models,
            )
        )
    if normalized:
        return normalized
    defaults = _default_api_settings()
    model = str(settings.model or defaults.model).strip()
    return [
        ApiConfigProfile(
            id=DEFAULT_PROFILE_ID,
            alias=DEFAULT_PROFILE_ALIAS,
            base_url=str(settings.base_url or defaults.base_url).strip().rstrip("/"),
            api_key=str(settings.api_key or "").strip(),
            models=normalize_provider_models([model]),
        )
    ]


def _normalized_request_model_selection(
    settings: ApiSettings,
    profiles: list[ApiConfigProfile],
    model_selection: ModelSelectionSettings | None,
) -> ModelSelectionSettings:
    selection = model_selection or ModelSelectionSettings()
    if resolve_model_slot(profiles, selection, MODEL_SLOT_CHAT, settings) is not None:
        return selection
    profile = profiles[0]
    model = profile.models[0] if profile.models else str(settings.model or _default_api_settings().model)
    return ModelSelectionSettings(
        chat=ModelSlotSelection(profile_id=profile.id, model=model),
        vision_chat=selection.vision_chat,
        memory_curation=selection.memory_curation,
    )


def _model_selection_to_mapping(selection: ModelSelectionSettings) -> dict[str, object]:
    slots: dict[str, object] = {}
    for slot in MODEL_SLOT_ORDER:
        selected = selection.get(slot)
        slots[slot] = (
            {
                "profile_id": selected.profile_id,
                "model": selected.model,
            }
            if selected is not None
            else {
                "profile_id": "",
                "model": "",
            }
        )
    return {"slots": slots}


def _selected_models_by_profile_id(mapping: dict[str, Any]) -> dict[str, list[str]]:
    """从模型槽位里提取可用模型，修复旧/异常 Tauri payload 的空 models 列表。"""
    slots = mapping.get("slots")
    if not isinstance(slots, dict):
        return {}
    selected: dict[str, list[str]] = {}
    for raw in slots.values():
        if not isinstance(raw, dict):
            continue
        profile_id = str(raw.get("profile_id", "")).strip()
        model = str(raw.get("model", "")).strip()
        if not profile_id or not model:
            continue
        models = selected.setdefault(profile_id, [])
        if model not in models:
            models.append(model)
    return selected


def _api_profiles_from_raw(
    raw_profiles: list[Any],
    *,
    selected_models_by_profile_id: dict[str, list[str]] | None = None,
) -> list[ApiConfigProfile]:
    profiles: list[ApiConfigProfile] = []
    seen: set[str] = set()
    selected_models_by_profile_id = selected_models_by_profile_id or {}
    for raw in raw_profiles:
        if not isinstance(raw, dict):
            raise ValueError("Tauri 设置结果字段无效：api.profiles")
        profile_id = _required_str(raw, "id").strip()
        if not profile_id or profile_id in seen:
            raise ValueError("Tauri 设置结果字段无效：api.profiles.id")
        seen.add(profile_id)
        alias = _required_str(raw, "alias").strip() or profile_id
        base_url = _required_str(raw, "base_url").strip().rstrip("/")
        if not base_url:
            raise ValueError("Tauri 设置结果字段无效：api.profiles.base_url")
        models = normalize_provider_models(raw.get("models"))
        if not models:
            models = normalize_provider_models(selected_models_by_profile_id.get(profile_id, []))
        if not models:
            raise ValueError("Tauri 设置结果字段无效：api.profiles.models")
        profiles.append(
            ApiConfigProfile(
                id=profile_id,
                alias=alias,
                base_url=base_url,
                api_key=_required_str(raw, "api_key").strip(),
                models=models,
            )
        )
    if not profiles:
        raise ValueError("Tauri 设置结果缺少可用 API 供应商。")
    return profiles


def _model_selection_from_mapping_required(mapping: dict[str, Any]) -> ModelSelectionSettings:
    slots = mapping.get("slots")
    if not isinstance(slots, dict):
        raise ValueError("Tauri 设置结果字段无效：api.model_selection.slots")
    chat = _slot_selection_from_mapping(slots, MODEL_SLOT_CHAT, required=True)
    assert chat is not None
    return ModelSelectionSettings(
        chat=chat,
        vision_chat=_slot_selection_from_mapping(slots, "vision_chat", required=False),
        memory_curation=_slot_selection_from_mapping(slots, "memory_curation", required=False),
    )


def _slot_selection_from_mapping(
    slots: dict[str, Any],
    slot: str,
    *,
    required: bool,
) -> ModelSlotSelection | None:
    raw = slots.get(slot)
    if not isinstance(raw, dict):
        if required:
            raise ValueError(f"Tauri 设置结果字段无效：api.model_selection.{slot}")
        return None
    profile_id = _required_str(raw, "profile_id").strip()
    model = _required_str(raw, "model").strip()
    if not profile_id and not model and not required:
        return None
    if not profile_id or not model:
        raise ValueError(f"Tauri 设置结果字段无效：api.model_selection.{slot}")
    return ModelSlotSelection(profile_id=profile_id, model=model)


def _normalize_tauri_tts_provider(provider: str, enabled: bool) -> str:
    if not enabled:
        return TTS_PROVIDER_NONE
    normalized = provider.strip().lower().replace("_", "-")
    aliases = {
        "": TTS_PROVIDER_GPT_SOVITS,
        "gptsovits": TTS_PROVIDER_GPT_SOVITS,
        "gpt-so-vits": TTS_PROVIDER_GPT_SOVITS,
        "gpt-sovits": TTS_PROVIDER_GPT_SOVITS,
        "custom-gpt-sovits": TTS_PROVIDER_CUSTOM_GPT_SOVITS,
        "external-gpt-sovits": TTS_PROVIDER_CUSTOM_GPT_SOVITS,
        "custom-sovits": TTS_PROVIDER_CUSTOM_GPT_SOVITS,
        "external-sovits": TTS_PROVIDER_CUSTOM_GPT_SOVITS,
        "genie": TTS_PROVIDER_GENIE,
        "genie-tts": TTS_PROVIDER_GENIE,
        "genietts": TTS_PROVIDER_GENIE,
    }
    normalized = aliases.get(normalized, normalized)
    if normalized not in {
        TTS_PROVIDER_GPT_SOVITS,
        TTS_PROVIDER_CUSTOM_GPT_SOVITS,
        TTS_PROVIDER_GENIE,
    }:
        raise ValueError("Tauri 设置结果字段无效：tts.provider")
    return normalized


def _is_http_url(value: str) -> bool:
    parsed = urlparse(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def _path_to_text(path: Path | None) -> str:
    return "" if path is None else str(path)


def _screen_estimate_size(parent_widget: QWidget | None) -> tuple[int, int]:
    screen = parent_widget.screen() if parent_widget is not None else None
    if screen is None:
        app = QApplication.instance()
        screen = app.primaryScreen() if app is not None else None
    if screen is None:
        return 1280, 720
    geometry = screen.geometry()
    dpr = screen.devicePixelRatio() or 1.0
    return (
        max(1, round(geometry.width() * dpr)),
        max(1, round(geometry.height() * dpr)),
    )


def _required_bool(mapping: dict[str, Any], key: str) -> bool:
    value = mapping.get(key)
    if not isinstance(value, bool):
        raise ValueError(f"Tauri 设置结果字段无效：{key}")
    return value


def _required_int(mapping: dict[str, Any], key: str) -> int:
    value = mapping.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"Tauri 设置结果字段无效：{key}")
    return value


def _required_number(mapping: dict[str, Any], key: str) -> float:
    value = mapping.get(key)
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError(f"Tauri 设置结果字段无效：{key}")
    return float(value)


def _required_str(mapping: dict[str, Any], key: str) -> str:
    value = mapping.get(key)
    if not isinstance(value, str):
        raise ValueError(f"Tauri 设置结果字段无效：{key}")
    return value


def _clamp_int_value(value: object, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = minimum
    return max(minimum, min(maximum, parsed))


def _optional_float_value(
    value: object,
    minimum: float,
    maximum: float,
) -> float | None:
    if value is None:
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return max(minimum, min(maximum, parsed))


def _optional_positive_int_value(value: object, maximum: int) -> int | None:
    if value is None:
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    if parsed <= 0:
        return None
    return min(maximum, parsed)


def _optional_float_from_mapping(
    mapping: dict[str, Any],
    key: str,
    minimum: float,
    maximum: float,
) -> float | None:
    value = mapping.get(key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError(f"Tauri 设置结果字段无效：{key}")
    return max(minimum, min(maximum, float(value)))


def _optional_int_from_mapping(
    mapping: dict[str, Any],
    key: str,
    minimum: int,
    maximum: int,
) -> int | None:
    value = mapping.get(key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"Tauri 设置结果字段无效：{key}")
    return max(minimum, min(maximum, value))
