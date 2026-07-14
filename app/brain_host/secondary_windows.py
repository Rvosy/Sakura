"""同一 Tauri App 的设置、工作室、历史与诊断 Brain API。"""

from __future__ import annotations

import secrets
from contextlib import contextmanager
from dataclasses import is_dataclass, replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Mapping

from app.agent.mcp.settings import MCPRuntimeSettings, resolve_desktop_mcp
from app.agent.runtime_limits import RuntimeLoopSettings
from app.agent.screen_awareness import (
    SCREEN_AWARENESS_MAX_CHECK_INTERVAL_MINUTES,
    SCREEN_AWARENESS_MAX_COOLDOWN_MINUTES,
    SCREEN_AWARENESS_MAX_SCREEN_CONTEXT_BATCH_LIMIT,
    SCREEN_AWARENESS_MIN_CHECK_INTERVAL_MINUTES,
    SCREEN_AWARENESS_MIN_COOLDOWN_MINUTES,
    SCREEN_AWARENESS_MIN_SCREEN_CONTEXT_BATCH_LIMIT,
    SCREEN_AWARENESS_SCREEN_CONTEXT_RESOLUTIONS,
    ScreenAwarenessSettings,
    estimate_screen_context_image_tokens_for_size,
    screen_context_resolution_size,
)
from app.config.character_archive import (
    export_character_archive,
    export_character_voice_archive,
    import_character_archive,
    import_character_voice_archive,
)
from app.config.character_loader import CharacterRegistry
from app.config.character_studio import CharacterStudioService
from app.config.defaults import (
    BUTTON_FONT_SIZE_MAX,
    BUTTON_FONT_SIZE_MIN,
    DEFAULT_BUTTON_FONT_SIZE,
    DEFAULT_INPUT_FONT_SIZE,
    DEFAULT_NAME_FONT_SIZE,
    DEFAULT_SPEECH_FONT_SIZE,
    INPUT_FONT_SIZE_MAX,
    INPUT_FONT_SIZE_MIN,
    NAME_FONT_SIZE_MAX,
    NAME_FONT_SIZE_MIN,
    SPEECH_FONT_SIZE_MAX,
    SPEECH_FONT_SIZE_MIN,
)
from app.config.models import (
    MODEL_SLOT_CHAT,
    MODEL_SLOT_LABELS,
    MODEL_SLOT_MEMORY_CURATION,
    MODEL_SLOT_ORDER,
    MODEL_SLOT_VISION_CHAT,
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
from app.config.theme import (
    DEFAULT_THEME_SETTINGS,
    THEME_COLOR_FIELDS,
    ThemeSettings,
    resolve_effective_theme,
    theme_colors_to_mapping,
    theme_from_mapping,
    theme_to_mapping,
)
from app.llm.api_client import ApiSettings
from app.storage.atomic import atomic_write_bytes
from app.storage.paths import StoragePaths
from app.voice.tts_settings import (
    DEFAULT_GENIE_TTS_API_URL,
    DEFAULT_GPT_SOVITS_API_URL,
    TTS_PROVIDER_CUSTOM_GPT_SOVITS,
    TTS_PROVIDER_GENIE,
    TTS_PROVIDER_GPT_SOVITS,
)


SETTINGS_PROTOCOL_VERSION = 3
STUDIO_PROTOCOL_VERSION = 1
DEFAULT_SCREEN_SIZE = (1280, 720)

PORTRAIT_SCALE_LIMIT = (50, 150)
CONTROL_PANEL_WIDTH_LIMIT = (420, 860)
BUBBLE_HEIGHT_LIMIT = (96, 260)
CONTROL_PANEL_OFFSET_LIMIT = (-200, 200)
INPUT_BAR_OFFSET_LIMIT = (0, 200)


@contextmanager
def _configuration_file_transaction(paths: list[Path]):
    snapshots: dict[Path, bytes | None] = {}
    for raw_path in paths:
        path = Path(raw_path).resolve(strict=False)
        if path in snapshots:
            continue
        snapshots[path] = path.read_bytes() if path.is_file() else None
    try:
        yield
    except BaseException as error:
        rollback_errors: list[str] = []
        for path, content in snapshots.items():
            try:
                if content is None:
                    path.unlink(missing_ok=True)
                else:
                    atomic_write_bytes(path, content)
            except BaseException as rollback_error:  # pragma: no cover - rare filesystem failure
                rollback_errors.append(f"{path}: {rollback_error}")
        if rollback_errors:
            raise RuntimeError(
                f"{error}；配置回滚失败：{'；'.join(rollback_errors)}"
            ) from error
        raise


def _settings_transaction_paths(service: Any, base_dir: Path) -> list[Path]:
    paths: list[Path] = []
    for attribute in (
        "api_config_path",
        "characters_config_path",
        "system_config_path",
    ):
        value = getattr(service, attribute, None)
        if value is not None:
            paths.append(Path(value))
    paths.append(StoragePaths(Path(base_dir)).plugins_config())
    return paths

PLUGIN_PERMISSION_LABELS = {
    "tool": {"group": "工具", "label": "Agent 工具"},
    "tools_tab": {"group": "UI", "label": "工具页"},
    "plugin_settings": {"group": "UI", "label": "插件设置"},
    "chat_ui": {"group": "UI", "label": "聊天 UI"},
    "prompt_patch": {"group": "上下文", "label": "提示词补丁"},
    "context_provider": {"group": "上下文", "label": "动态上下文"},
    "mobile_chat": {"group": "移动端", "label": "移动聊天"},
    "renderer": {"group": "渲染器", "label": "角色渲染器"},
    "event.app": {"group": "事件", "label": "应用事件"},
    "event.message": {"group": "事件", "label": "消息事件"},
    "event.tts": {"group": "事件", "label": "语音事件"},
    "event.character": {"group": "事件", "label": "角色事件"},
}


def secondary_window_request(application: Any, kind: str, payload: Mapping[str, Any]) -> dict[str, Any]:
    if kind == "settings":
        startup = application._current_startup_state()
        application.startup = startup
        request = build_settings_request(
            application.context,
            base_dir=application.config.base_dir,
            nonce=str(payload.get("nonce") or "") or None,
            onboarding=startup.get("state") != "ready",
        )
        request["resources"] = _settings_resource_manager(application).snapshot()
        return request
    if kind == "studio":
        return build_studio_request(
            application.config.base_dir,
            initial_character_id=str(
                payload.get("characterId")
                or getattr(getattr(application.context, "character_profile", None), "id", "")
            ),
            theme_settings=_current_theme(application.context),
        )
    if kind == "history":
        return history_page(
            application.context,
            cursor=payload.get("cursor"),
            limit=payload.get("limit", 50),
        )
    if kind == "diagnostics":
        return diagnostics_snapshot(application)
    raise ValueError(f"未知次级窗口：{kind}")


def secondary_host_call(application: Any, method: str, params: Mapping[str, Any]) -> dict[str, Any]:
    arguments = dict(params)
    if method == "settings.apply":
        settings = arguments.get("settings")
        if not isinstance(settings, dict):
            raise ValueError("settings.apply 需要 settings 对象。")
        return apply_settings_payload(application, settings)
    if method == "history.page":
        return history_page(
            application.context,
            cursor=arguments.get("cursor"),
            limit=arguments.get("limit", 50),
        )
    if method == "diagnostics.snapshot":
        return diagnostics_snapshot(application)
    if method == "studio.launch":
        return {"openWindow": "studio", "characterId": str(arguments.get("character_id") or "")}
    if method.startswith("studio."):
        result = dispatch_studio_rpc(application.config.base_dir, method, arguments)
        if method == "studio.save_character":
            character_id = str(
                result.get("current_character_id") or result.get("saved_character_id") or ""
            ).strip()
            refresh_character = getattr(application, "refresh_character", None)
            if character_id and callable(refresh_character):
                refresh_character(character_id)
        return result
    if method.startswith("memory."):
        return _dispatch_memory_rpc(getattr(application.context, "memory_store", None), method, arguments)
    if method.startswith("character."):
        result = _dispatch_character_rpc(application.config.base_dir, method, arguments)
        character_id = str(result.get("current_character_id") or "").strip()
        refresh_character = getattr(application, "refresh_character", None)
        if character_id and callable(refresh_character):
            refresh_character(character_id)
        return result
    if method == "resources.status":
        return _settings_resource_manager(application).snapshot()
    if method.startswith("resources."):
        return _settings_resource_manager(application).dispatch(method, arguments)
    if method in {"api.list_models", "api.test_connection", "tts.test", "theme.generate_ai"}:
        return {
            "status": "unavailable",
            "message": "该探测操作将在同一窗口内保留，但当前 Brain 服务未配置可复用的异步探测器。",
        }
    if method in {"theme.pick_screen_color", "studio.pick_screen_color"}:
        return {"cancelled": True}
    if method == "plugin.settings_action":
        contributions = getattr(getattr(application.context, "plugin_manager", None), "plugin_settings", [])
        return _dispatch_plugin_settings_action(contributions, arguments)
    raise ValueError(f"未知次级窗口方法：{method}")


def build_settings_request(
    context: Any,
    *,
    base_dir: Path,
    nonce: str | None = None,
    onboarding: bool = False,
) -> dict[str, Any]:
    service = context.settings_service
    ui = _load_system_values(service, "ui")
    screen_observation = _load_system_values(service, "screen_observation")
    screen = _call_or_value(service, "load_screen_awareness_settings", context, "screen_awareness_settings", ScreenAwarenessSettings()).normalized()
    mcp = _call_or_value(service, "load_mcp_runtime_settings", context, "mcp_settings", MCPRuntimeSettings())
    runtime_loop = _call_or_value(
        service,
        "load_runtime_loop_settings",
        getattr(context, "agent_runtime", None),
        "runtime_loop_settings",
        RuntimeLoopSettings(),
    ).normalized()
    debug = _call_or_value(service, "load_debug_log_settings", context, "debug_log_settings", DebugLogSettings())
    bubble = _call(service, "load_bubble_settings", BubbleSettings()).normalized()
    user_theme = _call(service, "load_theme_settings", DEFAULT_THEME_SETTINGS).normalized()
    overrides = _call(service, "load_character_theme_overrides", {})
    profile = getattr(context, "character_profile", None)
    registry = getattr(context, "character_registry", SimpleNamespace(profiles={}))
    theme = resolve_effective_theme(
        profile,
        overrides.get(getattr(profile, "id", "")) if isinstance(overrides, Mapping) else None,
        user_theme,
    )
    api_settings = _call(
        service,
        "load_api_settings",
        getattr(context, "settings", ApiSettings("", "", "")),
    )
    profiles = _call(service, "load_api_profiles", [])
    model_selection = _call(service, "load_model_selection", ModelSelectionSettings())
    tts = _load_tts_settings(service, profile)
    startup = _call_or_value(service, "load_startup_settings", context, "startup_settings", StartupSettings())
    backchannel = _call(service, "load_backchannel_settings", BackchannelSettings()).normalized()
    memory = _call_or_value(
        service,
        "load_memory_curation_settings",
        context,
        "memory_curation_settings",
        _memory_defaults(),
    )
    width, height = DEFAULT_SCREEN_SIZE
    estimates = {}
    for resolution in SCREEN_AWARENESS_SCREEN_CONTEXT_RESOLUTIONS:
        estimate_width, estimate_height = screen_context_resolution_size(width, height, resolution)
        estimates[resolution] = {
            "width": estimate_width,
            "height": estimate_height,
            "tokens": estimate_screen_context_image_tokens_for_size(
                estimate_width,
                estimate_height,
                model=getattr(api_settings, "model", None),
            ),
        }
    desktop_mcp = resolve_desktop_mcp()
    return {
        "version": SETTINGS_PROTOCOL_VERSION,
        "nonce": nonce or secrets.token_urlsafe(16),
        "onboarding": bool(onboarding),
        "screen_awareness": _screen_mapping(screen),
        "mcp": {
            "windows_enabled": bool(getattr(mcp, "windows_enabled", False)),
            "desktop": {
                "supported": desktop_mcp is not None,
                "label": getattr(desktop_mcp, "label", "") if desktop_mcp is not None else "",
                "experimental_text": "实验性功能；仅在明确需要桌面控制时启用。",
            },
        },
        "runtime_loop": _runtime_loop_mapping(runtime_loop),
        "system_basic": {
            "debug_log": {
                "enabled": bool(debug.enabled),
                "body_enabled": bool(debug.body_enabled),
                "file_enabled": bool(debug.file_enabled),
                "profile": debug.profile,
                "stage_debug_overlay": bool(debug.stage_debug_overlay),
                "stage_collision_mask": bool(debug.stage_collision_mask),
            },
            "ui": {
                "subtitle_typing_interval_ms": _int(ui.get("subtitle_typing_interval_ms"), 35),
                "reply_segment_pause_ms": _int(ui.get("reply_segment_pause_ms"), 100),
                "speech_font_size": _int(ui.get("speech_font_size"), DEFAULT_SPEECH_FONT_SIZE),
                "name_font_size": _int(ui.get("name_font_size"), DEFAULT_NAME_FONT_SIZE),
                "input_font_size": _int(ui.get("input_font_size"), DEFAULT_INPUT_FONT_SIZE),
                "button_font_size": _int(ui.get("button_font_size"), DEFAULT_BUTTON_FONT_SIZE),
            },
            "bubble": {
                "auto_hide_enabled": bool(bubble.auto_hide_enabled),
                "auto_hide_delay_seconds": int(bubble.auto_hide_delay_seconds),
            },
        },
        "theme": theme_to_mapping(theme),
        "character": _character_mapping(registry, profile, theme, overrides, ui),
        "api": _api_mapping(api_settings, profiles, model_selection),
        "tts": _tts_mapping(tts, base_dir),
        "system_extra": {
            "startup": {
                "launch_at_login": bool(startup.launch_at_login),
                "launch_at_login_supported": True,
            },
            "backchannel": {
                "enabled": bool(backchannel.enabled),
                "mode": backchannel.mode,
                "delay_ms": int(backchannel.delay_ms),
                "probability": float(backchannel.probability),
                "tts_enabled": bool(backchannel.tts_enabled),
                "timeout_ms": int(backchannel.timeout_ms),
            },
        },
        "memory": _memory_mapping(memory),
        "plugins": _plugins_mapping(base_dir, getattr(getattr(context, "plugin_manager", None), "plugin_settings", [])),
        "resources": {},
        "theme_defaults": theme_to_mapping(DEFAULT_THEME_SETTINGS),
        "theme_fields": [
            {"id": field, "label": label} for field, label, _default in THEME_COLOR_FIELDS
        ],
        "visual_effect_modes": [
            {"id": "solid", "label": "纯色块"},
            {"id": "gaussian_blur", "label": "高斯模糊"},
        ],
        "limits": _settings_limits(),
        "estimated_tokens_per_image": estimate_screen_context_image_tokens_for_size(
            width,
            height,
            model=getattr(api_settings, "model", None),
        ),
        "screen_resolution_estimates": estimates,
        "screen_observation": {
            "enabled": bool(screen_observation.get("enabled", True)),
            "autonomous_enabled": bool(screen_observation.get("autonomous_enabled", True)),
        },
    }


def apply_settings_payload(application: Any, payload: Mapping[str, Any]) -> dict[str, Any]:
    context = application.context
    service = context.settings_service
    bootstrap_only = not hasattr(context, "agent_runtime")
    previous_mcp = getattr(context, "mcp_settings", MCPRuntimeSettings())
    screen_data = _required_mapping(payload, "screen_awareness")
    screen = ScreenAwarenessSettings(
        enabled=_bool(screen_data.get("enabled"), True),
        screen_context_enabled=_bool(screen_data.get("screen_context_enabled"), True),
        check_interval_minutes=_int(screen_data.get("check_interval_minutes"), 2),
        cooldown_minutes=_int(screen_data.get("cooldown_minutes"), 10),
        screen_context_batch_limit=_int(screen_data.get("screen_context_batch_limit"), 6),
        screen_context_resolution=str(screen_data.get("screen_context_resolution") or "fullscreen"),
    ).normalized()
    mcp_data = _required_mapping(payload, "mcp")
    mcp = MCPRuntimeSettings(windows_enabled=_bool(mcp_data.get("windows_enabled"), False))
    loop_data = _required_mapping(payload, "runtime_loop")
    runtime_loop = RuntimeLoopSettings(
        max_agent_steps_per_turn=_int(loop_data.get("max_agent_steps_per_turn"), 5),
        max_tool_calls_per_step=_int(loop_data.get("max_tool_calls_per_step"), 4),
        max_tool_calls_per_turn=_int(loop_data.get("max_tool_calls_per_turn"), 20),
    ).normalized()
    basic = _required_mapping(payload, "system_basic")
    debug_data = _required_mapping(basic, "debug_log")
    ui_data = _required_mapping(basic, "ui")
    bubble_data = _required_mapping(basic, "bubble")
    debug = DebugLogSettings(
        enabled=_bool(debug_data.get("enabled"), True),
        body_enabled=_bool(debug_data.get("body_enabled"), False),
        file_enabled=_bool(debug_data.get("file_enabled"), True),
        profile=str(debug_data.get("profile") or "info"),
        stage_debug_overlay=_bool(debug_data.get("stage_debug_overlay"), False),
        stage_collision_mask=_bool(debug_data.get("stage_collision_mask"), True),
    )
    bubble = BubbleSettings(
        auto_hide_enabled=_bool(bubble_data.get("auto_hide_enabled"), True),
        auto_hide_delay_seconds=_int(bubble_data.get("auto_hide_delay_seconds"), 5),
    ).normalized()
    character_data = _required_mapping(payload, "character")
    character_id = str(character_data.get("current_character_id") or "").strip()
    registry = getattr(context, "character_registry", None)
    if registry is None or not callable(getattr(registry, "get", None)):
        registry = CharacterRegistry(application.config.base_dir)
    selected_profile = registry.get(character_id)
    layout = _required_mapping(character_data, "layout")
    theme = theme_from_mapping(_required_mapping(payload, "theme")).normalized()
    theme_changed = _bool(payload.get("theme_changed"), True)
    extra = _required_mapping(payload, "system_extra")
    startup_data = _required_mapping(extra, "startup")
    backchannel_data = _required_mapping(extra, "backchannel")
    startup = StartupSettings(launch_at_login=_bool(startup_data.get("launch_at_login"), False))
    backchannel = BackchannelSettings(
        enabled=_bool(backchannel_data.get("enabled"), False),
        mode=str(backchannel_data.get("mode") or "rules"),
        delay_ms=_int(backchannel_data.get("delay_ms"), 600),
        probability=_float(backchannel_data.get("probability"), 1.0),
        tts_enabled=_bool(backchannel_data.get("tts_enabled"), False),
        timeout_ms=_int(backchannel_data.get("timeout_ms"), 400),
    ).normalized()
    memory_data = _required_mapping(payload, "memory")
    curation = _required_mapping(memory_data, "curation")
    current_memory = _call(service, "load_memory_curation_settings", _memory_defaults())
    memory_values = {
        "trigger_turns": _int(curation.get("trigger_turns"), 8),
        "backfill_limit": _int(curation.get("backfill_limit"), 200),
    }
    memory = _replace_settings(current_memory, memory_values)
    layout_values = {
        "portrait_scale_percent": _clamp(
            layout.get("portrait_scale_percent"), *PORTRAIT_SCALE_LIMIT, 100
        ),
        "control_panel_width": _clamp(
            layout.get("control_panel_width"), *CONTROL_PANEL_WIDTH_LIMIT, 640
        ),
        "bubble_height": _clamp(
            layout.get("bubble_height"), *BUBBLE_HEIGHT_LIMIT, 128
        ),
        "control_panel_vertical_offset": _clamp(
            layout.get("control_panel_vertical_offset"),
            *CONTROL_PANEL_OFFSET_LIMIT,
            0,
        ),
        "input_bar_offset": _clamp(
            layout.get("input_bar_offset"), *INPUT_BAR_OFFSET_LIMIT, 0
        ),
    }
    ui_values = {
        **layout_values,
        "subtitle_typing_interval_ms": _clamp(
            ui_data.get("subtitle_typing_interval_ms"), 5, 200, 35
        ),
        "reply_segment_pause_ms": _clamp(
            ui_data.get("reply_segment_pause_ms"), 0, 3000, 100
        ),
        "speech_font_size": _clamp(
            ui_data.get("speech_font_size"),
            SPEECH_FONT_SIZE_MIN,
            SPEECH_FONT_SIZE_MAX,
            DEFAULT_SPEECH_FONT_SIZE,
        ),
        "name_font_size": _clamp(
            ui_data.get("name_font_size"),
            NAME_FONT_SIZE_MIN,
            NAME_FONT_SIZE_MAX,
            DEFAULT_NAME_FONT_SIZE,
        ),
        "input_font_size": _clamp(
            ui_data.get("input_font_size"),
            INPUT_FONT_SIZE_MIN,
            INPUT_FONT_SIZE_MAX,
            DEFAULT_INPUT_FONT_SIZE,
        ),
        "button_font_size": _clamp(
            ui_data.get("button_font_size"),
            BUTTON_FONT_SIZE_MIN,
            BUTTON_FONT_SIZE_MAX,
            DEFAULT_BUTTON_FONT_SIZE,
        ),
    }
    prepared_api = _prepare_api_payload(payload.get("api"))
    prepared_tts = _prepare_tts_payload(service, selected_profile, payload.get("tts"))
    plugin_data = payload.get("plugins")
    if plugin_data is not None and not isinstance(plugin_data, Mapping):
        raise ValueError("plugins 必须是对象。")
    enabled_by_id = plugin_data.get("enabled_by_id", {}) if isinstance(plugin_data, Mapping) else {}
    settings_by_id = plugin_data.get("settings_by_id", {}) if isinstance(plugin_data, Mapping) else {}
    if not isinstance(enabled_by_id, Mapping):
        raise ValueError("plugins.enabled_by_id 必须是对象。")
    if not isinstance(settings_by_id, Mapping):
        raise ValueError("plugins.settings_by_id 必须是对象。")
    normalized_enabled_by_id = {
        str(key): bool(value) for key, value in enabled_by_id.items() if str(key)
    }
    prepared_plugins = _prepare_plugin_settings(
        getattr(getattr(context, "plugin_manager", None), "plugin_settings", []),
        settings_by_id,
    )

    previous_runtime = {
        "screen": getattr(
            context,
            "screen_awareness_settings",
            _call(service, "load_screen_awareness_settings", ScreenAwarenessSettings()),
        ),
        "mcp": previous_mcp,
        "loop": getattr(
            getattr(context, "agent_runtime", None),
            "runtime_loop_settings",
            _call(service, "load_runtime_loop_settings", RuntimeLoopSettings()),
        ),
        "debug": getattr(
            context,
            "debug_log_settings",
            _call(service, "load_debug_log_settings", DebugLogSettings()),
        ),
        "startup": getattr(
            context,
            "startup_settings",
            _call(service, "load_startup_settings", StartupSettings()),
        ),
        "memory": getattr(context, "memory_curation_settings", current_memory),
        "character_id": str(getattr(getattr(context, "character_profile", None), "id", "")),
        "screen_enabled": getattr(application, "_screen_awareness_enabled", True),
        "screen_resolution": getattr(application, "_screen_context_resolution", "fullscreen"),
    }
    applied_plugins: list[tuple[Any, dict[str, Any]]] = []
    runtime_started = False
    restart_required: list[str] = []
    try:
        with _configuration_file_transaction(
            _settings_transaction_paths(service, application.config.base_dir)
        ):
            service.save_screen_awareness_settings(screen)
            service.save_mcp_runtime_settings(mcp)
            service.save_runtime_loop_settings(runtime_loop)
            service.save_debug_log_settings(debug)
            service.save_bubble_settings(bubble)
            service.save_backchannel_settings(backchannel)
            service.save_memory_curation_settings(memory)
            service.save_startup_settings(startup)
            service.save_current_character_id(registry, character_id)
            if theme_changed:
                service.save_theme_settings(theme)
                save_override = getattr(service, "save_character_theme_override", None)
                if callable(save_override):
                    save_override(character_id, theme)
            service.save_system_values("ui", ui_values)
            _save_prepared_api_payload(service, prepared_api)
            _save_prepared_tts_payload(service, prepared_tts)
            if normalized_enabled_by_id and _save_plugin_enabled_overrides(
                application.config.base_dir, normalized_enabled_by_id
            ):
                restart_required.append("plugins")
            _apply_prepared_plugin_settings(prepared_plugins, applied_plugins)

            runtime_started = True
            application._screen_awareness_enabled = screen.allows_screen_context()
            application._screen_context_resolution = screen.screen_context_resolution
            refresh_character = getattr(application, "refresh_character", None)
            if bootstrap_only:
                if callable(refresh_character):
                    refresh_character(character_id)
            else:
                runtime = getattr(context, "agent_runtime", None)
                set_loop = getattr(runtime, "set_runtime_loop_settings", None)
                if callable(set_loop):
                    set_loop(runtime_loop)
                refresh_runtime = getattr(application, "refresh_runtime_settings", None)
                if callable(refresh_runtime):
                    refresh_runtime(
                        screen_awareness=screen,
                        mcp=mcp,
                        debug=debug,
                        startup=startup,
                        memory_curation=memory,
                    )
                application.sync_scheduler_jobs(start=False)
                refresh_api = getattr(application, "refresh_api_settings", None)
                if callable(refresh_api):
                    refresh_api()
                if callable(refresh_character):
                    refresh_character(character_id)
            refresh_tts = getattr(application, "refresh_tts", None)
            if not bootstrap_only and callable(refresh_tts):
                refresh_tts()
    except BaseException as error:
        rollback_errors = _rollback_plugin_settings(applied_plugins)
        if runtime_started:
            rollback_errors.extend(
                _restore_runtime_after_failed_settings(
                    application,
                    context,
                    previous_runtime,
                    bootstrap_only=bootstrap_only,
                )
            )
        if rollback_errors:
            raise RuntimeError(
                f"{error}；运行时回滚失败：{'；'.join(rollback_errors)}"
            ) from error
        raise

    if mcp != previous_mcp:
        restart_required.append("mcp")
    return {
        "version": 1,
        "applied": True,
        "restartRequired": restart_required,
        "characterId": character_id,
        "theme": theme_to_mapping(theme),
        "layout": dict(layout_values),
        "runtimeLayout": {
            **layout_values,
            "speech_font_size": ui_values["speech_font_size"],
            "name_font_size": ui_values["name_font_size"],
            "input_font_size": ui_values["input_font_size"],
            "button_font_size": ui_values["button_font_size"],
        },
        "settings": build_settings_request(application.context, base_dir=application.config.base_dir),
    }


def _restore_runtime_after_failed_settings(
    application: Any,
    context: Any,
    previous: Mapping[str, Any],
    *,
    bootstrap_only: bool,
) -> list[str]:
    errors: list[str] = []

    def attempt(label: str, callback: Any) -> None:
        if not callable(callback):
            return
        try:
            callback()
        except BaseException as error:  # pragma: no cover - defensive external rollback
            errors.append(f"{label}: {error}")

    application._screen_awareness_enabled = previous["screen_enabled"]
    application._screen_context_resolution = previous["screen_resolution"]
    previous_character_id = str(previous.get("character_id") or "")
    refresh_character = getattr(application, "refresh_character", None)
    if bootstrap_only:
        if previous_character_id and callable(refresh_character):
            attempt("角色", lambda: refresh_character(previous_character_id))
        return errors

    runtime = getattr(context, "agent_runtime", None)
    set_loop = getattr(runtime, "set_runtime_loop_settings", None)
    if callable(set_loop):
        attempt("Agent 循环", lambda: set_loop(previous["loop"]))
    refresh_runtime = getattr(application, "refresh_runtime_settings", None)
    if callable(refresh_runtime):
        attempt(
            "运行时设置",
            lambda: refresh_runtime(
                screen_awareness=previous["screen"],
                mcp=previous["mcp"],
                debug=previous["debug"],
                startup=previous["startup"],
                memory_curation=previous["memory"],
            ),
        )
    sync_scheduler = getattr(application, "sync_scheduler_jobs", None)
    if callable(sync_scheduler):
        attempt("调度器", lambda: sync_scheduler(start=False))
    attempt("API", getattr(application, "refresh_api_settings", None))
    if previous_character_id and callable(refresh_character):
        attempt("角色", lambda: refresh_character(previous_character_id))
    attempt("TTS", getattr(application, "refresh_tts", None))
    return errors


def build_studio_request(
    base_dir: Path,
    *,
    initial_character_id: str = "",
    nonce: str | None = None,
    theme_settings: ThemeSettings | None = None,
) -> dict[str, Any]:
    service = CharacterStudioService(base_dir)
    theme = theme_to_mapping(theme_settings or DEFAULT_THEME_SETTINGS)
    return {
        "version": STUDIO_PROTOCOL_VERSION,
        "nonce": nonce or secrets.token_hex(16),
        "initial_character_id": str(initial_character_id or ""),
        "characters": service.list_characters(current_character_id=str(initial_character_id or "")),
        "theme": theme,
        "theme_defaults": theme,
        "theme_fields": [
            {"id": field, "label": label} for field, label, _default in THEME_COLOR_FIELDS
        ],
    }


def dispatch_studio_rpc(base_dir: Path, method: str, params: dict[str, Any]) -> dict[str, Any]:
    if method == "studio.pick_screen_color":
        return {"cancelled": True}
    service = CharacterStudioService(base_dir)
    if method == "studio.list_characters":
        return {"characters": service.list_characters(current_character_id=str(params.get("current_character_id") or ""))}
    if method == "studio.open_character":
        return service.open_character(_required_text(params, "character_id"))
    if method == "studio.create_character":
        return service.create_character(_required_mapping(params, "doc"))
    if method in {"studio.save_draft", "studio.save_workspace_draft"}:
        doc = _required_mapping(params, "doc")
        if method.endswith("workspace_draft"):
            return service.save_workspace_draft(_required_text(params, "workspace_id"), doc)
        return service.save_draft(doc, _required_path(params, "package_dir"))
    workspace = _workspace_reference(params)
    if method == "studio.save_character":
        return service.save_character(
            _required_mapping(params, "doc"),
            workspace,
            current_character_id=str(params.get("current_character_id") or ""),
        )
    if method == "studio.import_portrait":
        return service.import_portrait(workspace, _required_path(params, "path"), label=str(params.get("label") or "default"))
    if method == "studio.import_portrait_folder":
        return service.import_portrait_folder(workspace, _required_path(params, "path"))
    if method == "studio.import_voice_model":
        return service.import_voice_model(workspace, _required_path(params, "path"), model_type=_required_text(params, "model_type"))
    if method == "studio.import_reference_audio":
        return service.import_reference_audio(workspace, _required_path(params, "path"))
    if method == "studio.import_reference_audio_folder":
        return service.import_reference_audio_folder(workspace, _required_path(params, "path"), ref_lang=str(params.get("ref_lang") or "ja"))
    if method == "studio.load_reference_audio_preview":
        return service.load_reference_audio_preview(workspace, _required_text(params, "relative_path"))
    if method == "studio.discard_draft":
        return service.discard_draft(_required_text(params, "workspace_id"), current_character_id=str(params.get("current_character_id") or ""))
    if method == "studio.release_workspace":
        return service.release_workspace(_required_text(params, "workspace_id"))
    if method == "studio.export_archive":
        return service.export_archive(workspace, _required_path(params, "path"), include_voice=bool(params.get("include_voice")))
    raise ValueError(f"未知 Studio 方法：{method}")


def history_page(context: Any, *, cursor: object, limit: object) -> dict[str, Any]:
    page_size = _clamp(limit, 1, 100, 50)
    offset = max(0, _int(cursor, 0))
    store = getattr(context, "history_store", None)
    if store is None:
        return {
            "version": 1,
            "character": {"id": "", "displayName": "Sakura"},
            "theme": theme_to_mapping(_current_theme(context)),
            "items": [],
            "nextCursor": None,
            "hasMore": False,
        }
    recent = store.load_recent(offset + page_size + 1)
    end = max(0, len(recent) - offset)
    start = max(0, end - page_size)
    items = recent[start:end]
    has_more = start > 0 or len(recent) > offset + page_size
    next_cursor = str(offset + len(items)) if has_more else None
    return {
        "version": 1,
        "character": {
            "id": str(getattr(getattr(context, "character_profile", None), "id", "")),
            "displayName": str(
                getattr(getattr(context, "character_profile", None), "display_name", "Sakura")
            ),
        },
        "theme": theme_to_mapping(_current_theme(context)),
        "items": [
            {
                "createdAt": item.created_at,
                "role": item.role,
                "content": item.content,
                "translation": item.translation,
                "tone": item.tone,
                "portrait": item.portrait,
            }
            for item in items
        ],
        "nextCursor": next_cursor,
        "hasMore": has_more,
    }


def diagnostics_snapshot(application: Any) -> dict[str, Any]:
    context = application.context
    manager = getattr(context, "plugin_manager", None)
    plugin_items = []
    for result in getattr(manager, "results", []) or []:
        spec = getattr(result, "spec", None)
        plugin_items.append(
            {
                "id": str(getattr(spec, "plugin_id", "")),
                "loaded": bool(getattr(result, "loaded", False)),
                "error": str(getattr(result, "error", "") or ""),
            }
        )
    mcp = getattr(context, "mcp_tool_provider", None)
    try:
        mcp_tools = list(mcp.list_tools()) if mcp is not None else []
    except Exception:  # noqa: BLE001
        mcp_tools = []
    registry = getattr(context, "resource_registry", None)
    active_count = getattr(registry, "active_resource_count", 0)
    labels = getattr(registry, "resource_labels", ())
    return {
        "version": 1,
        "theme": theme_to_mapping(_current_theme(context)),
        "brain": {
            "state": application.state,
            "sessionId": application.config.session_id,
            "busy": bool(getattr(getattr(application, "assistant", None), "busy", False)),
            "characterId": str(getattr(getattr(context, "character_profile", None), "id", "")),
        },
        "plugins": {
            "loaded": sum(1 for item in plugin_items if item["loaded"]),
            "failed": sum(1 for item in plugin_items if not item["loaded"]),
            "items": plugin_items,
        },
        "mcp": {"ready": mcp is not None, "toolCount": len(mcp_tools)},
        "tts": {
            "ready": bool(getattr(getattr(application, "tts_service", None), "service_ready", False)),
            "service": type(getattr(application, "tts_service", None)).__name__,
        },
        "resources": {"activeCount": int(active_count), "labels": list(labels)},
        "scheduler": {
            "running": bool(getattr(getattr(application, "scheduler", None), "running", False)),
            "jobs": list(getattr(getattr(application, "scheduler", None), "job_names", ())),
        },
    }


def _current_theme(context: Any) -> ThemeSettings:
    service = getattr(context, "settings_service", None)
    profile = getattr(context, "character_profile", None)
    user_theme = _call(service, "load_theme_settings", DEFAULT_THEME_SETTINGS)
    overrides = _call(service, "load_character_theme_overrides", {})
    override = overrides.get(getattr(profile, "id", "")) if isinstance(overrides, Mapping) else None
    return resolve_effective_theme(profile, override, user_theme)


def _settings_resource_manager(application: Any) -> Any:
    manager = getattr(application, "settings_resource_tasks", None)
    if manager is not None:
        return manager
    from app.core.settings_resource_tasks import settings_resource_task_manager

    manager = settings_resource_task_manager(
        application.config.base_dir,
        memory_store=getattr(application.context, "memory_store", None),
    )
    application.settings_resource_tasks = manager
    return manager


def _character_mapping(registry: Any, current: Any, theme: ThemeSettings, overrides: Any, ui: Mapping[str, Any]) -> dict[str, Any]:
    characters = []
    profiles = getattr(registry, "profiles", {})
    for profile in profiles.values() if isinstance(profiles, Mapping) else []:
        profile_theme = overrides.get(profile.id) if isinstance(overrides, Mapping) else None
        profile_theme = profile_theme or getattr(profile, "theme_settings", None) or theme
        colors = theme_colors_to_mapping(profile_theme.normalized())
        characters.append(
            {
                "id": profile.id,
                "display_name": profile.display_name,
                "has_voice": getattr(profile, "voice", None) is not None,
                "has_exportable_voice": getattr(profile, "voice", None) is not None,
                "theme": colors,
                "default_theme": theme_colors_to_mapping((getattr(profile, "theme_settings", None) or theme).normalized()),
            }
        )
    return {
        "current_character_id": str(getattr(current, "id", "")),
        "characters": characters,
        "layout": {
            "portrait_scale_percent": _clamp(ui.get("portrait_scale_percent"), *PORTRAIT_SCALE_LIMIT, 100),
            "control_panel_width": _clamp(ui.get("control_panel_width"), *CONTROL_PANEL_WIDTH_LIMIT, 640),
            "bubble_height": _clamp(ui.get("bubble_height"), *BUBBLE_HEIGHT_LIMIT, 128),
            "control_panel_vertical_offset": _clamp(ui.get("control_panel_vertical_offset"), *CONTROL_PANEL_OFFSET_LIMIT, 0),
            "input_bar_offset": _clamp(ui.get("input_bar_offset"), *INPUT_BAR_OFFSET_LIMIT, 0),
        },
    }


def _api_mapping(settings: Any, profiles: list[Any], selection: Any) -> dict[str, Any]:
    return {
        "settings": {
            "timeout_seconds": _clamp(getattr(settings, "timeout_seconds", 60), 1, 600, 60),
            "temperature": getattr(settings, "temperature", None),
            "top_p": getattr(settings, "top_p", None),
            "max_tokens": getattr(settings, "max_tokens", None),
        },
        "profiles": [
            {
                "id": str(profile.id),
                "alias": str(profile.alias),
                "base_url": str(profile.base_url),
                "api_key": str(profile.api_key),
                "models": list(profile.models),
            }
            for profile in profiles
        ],
        "model_selection": {
            "slots": {
                slot: _slot_mapping(selection.get(slot) if hasattr(selection, "get") else None)
                for slot in MODEL_SLOT_ORDER
            }
        },
        "slot_fields": [
            {"id": slot, "label": MODEL_SLOT_LABELS.get(slot, slot), "required": slot == MODEL_SLOT_CHAT}
            for slot in MODEL_SLOT_ORDER
        ],
    }


def _tts_mapping(settings: Any, base_dir: Path) -> dict[str, Any]:
    return {
        "enabled": bool(getattr(settings, "enabled", False)),
        "provider": str(getattr(settings, "provider", "none") or "none"),
        "providers": [
            {"id": TTS_PROVIDER_GPT_SOVITS, "label": "内置 GPT-SoVITS"},
            {"id": TTS_PROVIDER_CUSTOM_GPT_SOVITS, "label": "外部 GPT-SoVITS"},
            {"id": TTS_PROVIDER_GENIE, "label": "Genie TTS"},
        ],
        "api_url": str(getattr(settings, "api_url", "") or ""),
        "work_dir": _path_text(getattr(settings, "work_dir", None)),
        "python_path": _path_text(getattr(settings, "python_path", None)),
        "tts_config_path": _path_text(getattr(settings, "tts_config_path", None)),
        "provider_defaults": {
            TTS_PROVIDER_GPT_SOVITS: {"api_url": DEFAULT_GPT_SOVITS_API_URL, "work_dir": str(base_dir / "tts" / "g50"), "python_path": str(base_dir / "tts" / "g50" / "runtime" / "python.exe"), "notice": ""},
            TTS_PROVIDER_GENIE: {"api_url": DEFAULT_GENIE_TTS_API_URL, "work_dir": str(base_dir / "tts" / "cpu"), "python_path": str(base_dir / "tts" / "cpu" / "runtime" / "python.exe"), "notice": ""},
            TTS_PROVIDER_CUSTOM_GPT_SOVITS: {"api_url": DEFAULT_GPT_SOVITS_API_URL, "work_dir": "", "python_path": "", "notice": ""},
        },
        "timeout_seconds": _clamp(getattr(settings, "timeout_seconds", 60), 1, 600, 60),
    }


def _memory_mapping(settings: Any) -> dict[str, Any]:
    return {
        "curation": {"enabled": True, "trigger_turns": _clamp(getattr(settings, "trigger_turns", 8), 1, 50, 8), "backfill_limit": max(1, _int(getattr(settings, "backfill_limit", 200), 200))},
        "layers": [
            {"id": "core_profile", "label": "常驻画像"},
            {"id": "semantic", "label": "稳定事实"},
            {"id": "episodic", "label": "事件总结"},
            {"id": "procedural", "label": "协作习惯"},
            {"id": "session", "label": "当前会话"},
        ],
        "defaults": {"layer": "semantic", "source": "manual", "importance": 0.5, "confidence": 0.75},
        "page_size": 120,
    }


def _plugins_mapping(base_dir: Path, contributions: list[Any]) -> dict[str, Any]:
    settings_by_plugin: dict[str, list[Any]] = {}
    for contribution in contributions or []:
        plugin_id = str(getattr(contribution, "plugin_id", "")).strip()
        if plugin_id:
            settings_by_plugin.setdefault(plugin_id, []).append(contribution)
    for values in settings_by_plugin.values():
        values.sort(key=lambda item: float(getattr(item, "order", 100.0)))
    items = []
    for spec in _discover_plugins(base_dir):
        plugin_id = str(spec["id"])
        items.append(
            {
                **spec,
                "settings": [
                    _plugin_settings_mapping(contribution)
                    for contribution in settings_by_plugin.get(plugin_id, [])
                ],
            }
        )
    return {"items": items, "permission_labels": PLUGIN_PERMISSION_LABELS}


def _plugin_settings_mapping(contribution: Any) -> dict[str, Any]:
    values: dict[str, Any] = {}
    error = ""
    if callable(contribution.load):
        try:
            loaded = contribution.load()
            if isinstance(loaded, dict):
                values = dict(loaded)
        except Exception as exc:  # noqa: BLE001 - 单个插件失败不阻断设置页
            error = str(exc)
    fields = [_plugin_settings_field_mapping(field, values) for field in contribution.fields]
    return {
        "section_id": str(contribution.section_id),
        "title": str(contribution.title),
        "order": float(contribution.order),
        "values": {str(field["key"]): field["value"] for field in fields},
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


def _plugin_settings_field_mapping(
    field: Any,
    values: dict[str, Any],
) -> dict[str, Any]:
    key = str(field.key)
    mapping = {
        "key": key,
        "label": str(field.label),
        "type": str(field.field_type or "text"),
        "value": values.get(key, field.default),
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


def _settings_limits() -> dict[str, list[float | int]]:
    return {
        "check_interval_minutes": [SCREEN_AWARENESS_MIN_CHECK_INTERVAL_MINUTES, SCREEN_AWARENESS_MAX_CHECK_INTERVAL_MINUTES],
        "cooldown_minutes": [SCREEN_AWARENESS_MIN_COOLDOWN_MINUTES, SCREEN_AWARENESS_MAX_COOLDOWN_MINUTES],
        "screen_context_batch_limit": [SCREEN_AWARENESS_MIN_SCREEN_CONTEXT_BATCH_LIMIT, SCREEN_AWARENESS_MAX_SCREEN_CONTEXT_BATCH_LIMIT],
        "max_agent_steps_per_turn": [1, 12],
        "max_tool_calls_per_step": [1, 10],
        "max_tool_calls_per_turn": [1, 30],
        "subtitle_typing_interval_ms": [5, 200],
        "reply_segment_pause_ms": [0, 3000],
        "bubble_auto_hide_delay_seconds": [BUBBLE_AUTO_HIDE_MIN_DELAY_SECONDS, BUBBLE_AUTO_HIDE_MAX_DELAY_SECONDS],
        "portrait_scale_percent": list(PORTRAIT_SCALE_LIMIT),
        "control_panel_width": list(CONTROL_PANEL_WIDTH_LIMIT),
        "bubble_height": list(BUBBLE_HEIGHT_LIMIT),
        "control_panel_vertical_offset": list(CONTROL_PANEL_OFFSET_LIMIT),
        "input_bar_offset": list(INPUT_BAR_OFFSET_LIMIT),
        "speech_font_size": [SPEECH_FONT_SIZE_MIN, SPEECH_FONT_SIZE_MAX],
        "name_font_size": [NAME_FONT_SIZE_MIN, NAME_FONT_SIZE_MAX],
        "input_font_size": [INPUT_FONT_SIZE_MIN, INPUT_FONT_SIZE_MAX],
        "button_font_size": [BUTTON_FONT_SIZE_MIN, BUTTON_FONT_SIZE_MAX],
        "api_timeout_seconds": [1, 600],
        "api_temperature": [0, 2],
        "api_top_p": [0, 1],
        "api_max_tokens": [1, 32768],
        "tts_timeout_seconds": [1, 600],
        "backchannel_delay_ms": [BACKCHANNEL_MIN_DELAY_MS, BACKCHANNEL_MAX_DELAY_MS],
        "backchannel_probability": [0, 1],
        "memory_trigger_turns": [1, 50],
    }


def _prepare_api_payload(raw: object) -> dict[str, Any] | None:
    if not isinstance(raw, Mapping):
        return None
    profiles = []
    profile_ids: set[str] = set()
    for item in raw.get("profiles", []):
        if not isinstance(item, Mapping):
            raise ValueError("API profiles 必须是对象列表。")
        profile = ApiConfigProfile(
            id=str(item.get("id") or "").strip(),
            alias=str(item.get("alias") or "").strip(),
            base_url=str(item.get("base_url") or "").strip().rstrip("/"),
            api_key=str(item.get("api_key") or "").strip(),
            models=tuple(
                str(model).strip()
                for model in item.get("models", [])
                if str(model).strip()
            ),
        )
        if not profile.id:
            raise ValueError("API 供应商缺少 id。")
        if profile.id in profile_ids:
            raise ValueError(f"API 供应商 id 重复：{profile.id}")
        profile_ids.add(profile.id)
        profiles.append(profile)
    selection_data = raw.get("model_selection")
    slots = selection_data.get("slots", {}) if isinstance(selection_data, Mapping) else {}
    selection = ModelSelectionSettings(
        chat=_slot_from_mapping(slots.get(MODEL_SLOT_CHAT)),
        vision_chat=_optional_slot_from_mapping(slots.get(MODEL_SLOT_VISION_CHAT)),
        memory_curation=_optional_slot_from_mapping(slots.get(MODEL_SLOT_MEMORY_CURATION)),
    )
    settings_data = raw.get("settings") if isinstance(raw.get("settings"), Mapping) else {}
    chat = selection.chat
    profile = next((item for item in profiles if item.id == chat.profile_id), profiles[0] if profiles else None)
    api_settings = None
    if profile is not None:
        if not profile.base_url:
            raise ValueError(f"API 供应商缺少 Base URL：{profile.id}")
        selected_model = chat.model or (profile.models[0] if profile.models else "")
        if not selected_model:
            raise ValueError(f"API 供应商缺少模型：{profile.id}")
        if chat.profile_id and chat.profile_id not in profile_ids:
            raise ValueError(f"聊天模型引用了未知供应商：{chat.profile_id}")
        if chat.model and chat.model not in profile.models:
            raise ValueError(f"聊天模型不属于供应商 {profile.id}：{chat.model}")
        api_settings = ApiSettings(
            base_url=profile.base_url,
            api_key=profile.api_key,
            model=selected_model,
            timeout_seconds=_clamp(settings_data.get("timeout_seconds"), 1, 600, 60),
            temperature=_optional_float(settings_data.get("temperature")),
            top_p=_optional_float(settings_data.get("top_p")),
            max_tokens=_optional_int(settings_data.get("max_tokens")),
        )
    return {
        "profiles": profiles,
        "selection": selection,
        "settings": api_settings,
    }


def _save_prepared_api_payload(service: Any, prepared: dict[str, Any] | None) -> None:
    if prepared is None:
        return
    save_profiles = getattr(service, "save_api_profiles", None)
    if callable(save_profiles):
        save_profiles(prepared["profiles"])
    save_selection = getattr(service, "save_model_selection", None)
    if callable(save_selection):
        save_selection(prepared["selection"])
    save_settings = getattr(service, "save_api_settings", None)
    if prepared["settings"] is not None and callable(save_settings):
        save_settings(prepared["settings"])


def _save_api_payload(service: Any, raw: object) -> None:
    _save_prepared_api_payload(service, _prepare_api_payload(raw))


def _prepare_tts_payload(service: Any, profile: Any, raw: object) -> Any | None:
    if not isinstance(raw, Mapping):
        return None
    previous = _load_tts_settings(service, profile)
    return _replace_settings(
        previous,
        {
            "enabled": _bool(raw.get("enabled"), False),
            "provider": str(raw.get("provider") or "none"),
            "api_url": str(raw.get("api_url") or previous.api_url),
            "work_dir": _optional_path(raw.get("work_dir")),
            "python_path": _optional_path(raw.get("python_path")),
            "tts_config_path": _optional_path(raw.get("tts_config_path")),
            "timeout_seconds": _clamp(raw.get("timeout_seconds"), 1, 600, 60),
        },
    )


def _save_prepared_tts_payload(service: Any, prepared: Any | None) -> None:
    save_tts = getattr(service, "save_tts_settings", None)
    if prepared is not None and callable(save_tts):
        save_tts(prepared)


def _save_tts_payload(service: Any, profile: Any, raw: object) -> None:
    _save_prepared_tts_payload(service, _prepare_tts_payload(service, profile, raw))


def _dispatch_memory_rpc(store: Any, method: str, params: dict[str, Any]) -> dict[str, Any]:
    if store is None:
        return {"status": "failed", "message": "长期记忆系统不可用。", "memories": []}
    if method == "memory.search":
        arguments = dict(params)
        arguments.setdefault("limit", 120)
        return store.search_memory(arguments, wait=False)
    if method == "memory.upsert":
        return (
            store.update_memory(params, allow_sensitive=True, wait=False)
            if str(params.get("id") or "").strip()
            else store.create_memory(params, allow_sensitive=True, wait=False)
        )
    if method == "memory.delete":
        memory_id = _required_text(params, "id")
        return store.forget_memory({"id": memory_id}, wait=False)
    raise ValueError(f"未知记忆方法：{method}")


def _dispatch_character_rpc(base_dir: Path, method: str, params: dict[str, Any]) -> dict[str, Any]:
    if method == "character.import_archive":
        result = import_character_archive(_required_path(params, "path"), base_dir)
        return {"current_character_id": result.character_id, "characters": _character_summaries(CharacterRegistry(base_dir), result.character_id)}
    registry = CharacterRegistry(base_dir)
    if method == "character.import_voice_archive":
        result = import_character_voice_archive(_required_path(params, "path"), base_dir, _required_text(params, "character_id"))
        return {"current_character_id": result.character_id, "characters": _character_summaries(CharacterRegistry(base_dir), result.character_id)}
    if method == "character.export_archive":
        profile = registry.get(_required_text(params, "character_id"))
        kind = _required_text(params, "kind")
        output = _required_path(params, "path")
        if kind == "voice":
            export_character_voice_archive(profile, output)
        else:
            export_character_archive(profile, output, include_voice=kind == "full")
        return {"current_character_id": profile.id, "characters": _character_summaries(registry, profile.id), "output_path": str(output)}
    raise ValueError(f"未知角色方法：{method}")


def _character_summaries(registry: CharacterRegistry, current_id: str) -> list[dict[str, Any]]:
    return [
        {"id": profile.id, "display_name": profile.display_name, "has_voice": profile.voice is not None}
        for profile in registry.all()
    ]


def _screen_mapping(settings: ScreenAwarenessSettings) -> dict[str, Any]:
    return {
        "enabled": settings.enabled,
        "screen_context_enabled": settings.screen_context_enabled,
        "check_interval_minutes": settings.check_interval_minutes,
        "cooldown_minutes": settings.cooldown_minutes,
        "screen_context_batch_limit": settings.screen_context_batch_limit,
        "screen_context_resolution": settings.screen_context_resolution,
    }


def _runtime_loop_mapping(settings: RuntimeLoopSettings) -> dict[str, int]:
    return {
        "max_agent_steps_per_turn": settings.max_agent_steps_per_turn,
        "max_tool_calls_per_step": settings.max_tool_calls_per_step,
        "max_tool_calls_per_turn": settings.max_tool_calls_per_turn,
    }


def _slot_mapping(slot: Any) -> dict[str, str]:
    return {"profile_id": str(getattr(slot, "profile_id", "")), "model": str(getattr(slot, "model", ""))}


def _slot_from_mapping(raw: object) -> ModelSlotSelection:
    mapping = raw if isinstance(raw, Mapping) else {}
    return ModelSlotSelection(profile_id=str(mapping.get("profile_id") or ""), model=str(mapping.get("model") or ""))


def _optional_slot_from_mapping(raw: object) -> ModelSlotSelection | None:
    slot = _slot_from_mapping(raw)
    return slot if slot.configured else None


def _workspace_reference(params: Mapping[str, Any]) -> str | Path:
    workspace_id = str(params.get("workspace_id") or "").strip()
    return workspace_id or _required_path(params, "package_dir")


def _required_mapping(mapping: Mapping[str, Any], key: str) -> dict[str, Any]:
    value = mapping.get(key)
    if not isinstance(value, Mapping):
        raise ValueError(f"缺少对象字段：{key}")
    return dict(value)


def _required_text(mapping: Mapping[str, Any], key: str) -> str:
    value = mapping.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"缺少字段：{key}")
    return value.strip()


def _required_path(mapping: Mapping[str, Any], key: str) -> Path:
    return Path(_required_text(mapping, key))


def _load_tts_settings(service: Any, profile: Any) -> Any:
    callback = getattr(service, "load_tts_settings", None)
    if not callable(callback):
        return _tts_defaults()
    try:
        return callback(validate_enabled=False, character_profile=profile)
    except TypeError:
        return callback(character_profile=profile)


def _call(service: Any, method: str, default: Any) -> Any:
    callback = getattr(service, method, None)
    return callback() if callable(callback) else default


def _call_or_value(service: Any, method: str, owner: Any, attribute: str, default: Any) -> Any:
    callback = getattr(service, method, None)
    return callback() if callable(callback) else getattr(owner, attribute, default)


def _replace_settings(current: Any, changes: Mapping[str, Any]) -> Any:
    if is_dataclass(current):
        return replace(current, **changes)
    current_values = vars(current) if hasattr(current, "__dict__") else {}
    return SimpleNamespace(**{**current_values, **changes})


def _load_system_values(service: Any, section: str) -> dict[str, Any]:
    callback = getattr(service, "load_system_values", None)
    value = callback(section) if callable(callback) else {}
    return dict(value) if isinstance(value, Mapping) else {}


def _memory_defaults() -> Any:
    from app.agent.memory_curator import MemoryCurationSettings

    return MemoryCurationSettings()


def _tts_defaults() -> Any:
    from app.voice.tts_settings import GPTSoVITSTTSSettings

    return GPTSoVITSTTSSettings(False, "", Path(), Path(), "", provider="none")


def _path_text(value: object) -> str:
    return str(value) if isinstance(value, Path) else str(value or "")


def _optional_path(value: object) -> Path | None:
    text = str(value or "").strip()
    return Path(text) if text else None


def _int(value: object, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _float(value: object, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _bool(value: object, default: bool) -> bool:
    return value if isinstance(value, bool) else default


def _clamp(value: object, minimum: int, maximum: int, default: int) -> int:
    return max(minimum, min(maximum, _int(value, default)))


def _optional_float(value: object) -> float | None:
    return None if value is None else _float(value, 0.0)


def _optional_int(value: object) -> int | None:
    return None if value is None else _int(value, 0)


def _discover_plugins(base_dir: Path) -> list[dict[str, Any]]:
    from app.plugins.discovery import PluginDiscovery

    items: list[dict[str, Any]] = []
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
                "source": str(spec.source or "manifest"),
                "priority": int(spec.priority),
                "entry": str(spec.entry or ""),
            }
        )
    return items


def _save_plugin_enabled_overrides(base_dir: Path, enabled_by_id: Mapping[str, Any]) -> bool:
    from app.plugins.discovery import save_plugin_enabled_overrides

    normalized = {str(key): bool(value) for key, value in enabled_by_id.items() if str(key)}
    return save_plugin_enabled_overrides(Path(base_dir), normalized)


def _apply_plugin_settings(
    contributions: list[Any],
    settings_by_id: Mapping[str, Any],
) -> bool:
    prepared = _prepare_plugin_settings(contributions, settings_by_id)
    applied: list[tuple[Any, dict[str, Any]]] = []
    return _apply_prepared_plugin_settings(prepared, applied)


def _prepare_plugin_settings(
    contributions: list[Any],
    settings_by_id: Mapping[str, Any],
) -> list[tuple[Any, dict[str, Any], dict[str, Any]]]:
    by_key = _plugin_settings_by_key(contributions)
    prepared: list[tuple[Any, dict[str, Any], dict[str, Any]]] = []
    for plugin_id, sections in settings_by_id.items():
        if not isinstance(sections, Mapping):
            raise ValueError(f"插件设置无效：{plugin_id}")
        for section_id, raw_values in sections.items():
            if not isinstance(raw_values, Mapping):
                raise ValueError(f"插件设置无效：{plugin_id}.{section_id}")
            contribution = by_key.get((str(plugin_id), str(section_id)))
            if contribution is None:
                raise ValueError(f"未知插件设置区块：{plugin_id}.{section_id}")
            values = _normalize_plugin_settings(contribution, raw_values)
            current = _current_plugin_settings(contribution)
            if values == current:
                continue
            prepared.append((contribution, values, current))
    return prepared


def _apply_prepared_plugin_settings(
    prepared: list[tuple[Any, dict[str, Any], dict[str, Any]]],
    applied: list[tuple[Any, dict[str, Any]]],
) -> bool:
    changed = False
    for contribution, values, current in prepared:
        if callable(contribution.save):
            applied.append((contribution, current))
            contribution.save(values)
            changed = True
    return changed


def _rollback_plugin_settings(applied: list[tuple[Any, dict[str, Any]]]) -> list[str]:
    errors: list[str] = []
    for contribution, previous in reversed(applied):
        try:
            if callable(contribution.save):
                contribution.save(previous)
        except BaseException as error:  # pragma: no cover - plugin-defined rollback failure
            plugin_id = str(getattr(contribution, "plugin_id", "plugin"))
            section_id = str(getattr(contribution, "section_id", "settings"))
            errors.append(f"{plugin_id}.{section_id}: {error}")
    return errors


def _dispatch_plugin_settings_action(
    contributions: list[Any],
    params: Mapping[str, Any],
) -> dict[str, Any]:
    plugin_id = _required_text(params, "plugin_id")
    section_id = _required_text(params, "section_id")
    action_id = _required_text(params, "action_id")
    contribution = _plugin_settings_by_key(contributions).get((plugin_id, section_id))
    if contribution is None:
        raise ValueError(f"未知插件设置区块：{plugin_id}.{section_id}")
    action = next((item for item in contribution.actions if item.action_id == action_id), None)
    if action is None or not callable(action.handler):
        raise ValueError(f"未知插件设置动作：{plugin_id}.{section_id}.{action_id}")
    raw_values = params.get("values", {})
    if not isinstance(raw_values, Mapping):
        raise ValueError("插件设置动作 values 必须是对象。")
    result = action.handler(_normalize_plugin_settings(contribution, raw_values))
    return result if isinstance(result, dict) else {"result": result}


def _plugin_settings_by_key(
    contributions: list[Any],
) -> dict[tuple[str, str], Any]:
    result: dict[tuple[str, str], Any] = {}
    for contribution in contributions or []:
        plugin_id = str(contribution.plugin_id or "").strip()
        section_id = str(contribution.section_id or "").strip()
        if plugin_id and section_id:
            result[(plugin_id, section_id)] = contribution
    return result


def _current_plugin_settings(contribution: Any) -> dict[str, Any]:
    values: Mapping[str, Any] = {}
    if callable(contribution.load):
        try:
            loaded = contribution.load()
            if isinstance(loaded, Mapping):
                values = loaded
        except Exception:  # noqa: BLE001 - 读取失败时仍允许保存
            pass
    return _normalize_plugin_settings(contribution, values)


def _normalize_plugin_settings(
    contribution: Any,
    values: Mapping[str, Any],
) -> dict[str, Any]:
    fields = {str(field.key): field for field in contribution.fields}
    unknown = next((str(key) for key in values if str(key) not in fields), "")
    if unknown:
        raise ValueError(
            f"未知插件设置字段：{contribution.plugin_id}.{contribution.section_id}.{unknown}"
        )
    result: dict[str, Any] = {}
    for key, field in fields.items():
        if field.readonly:
            continue
        result[key] = _normalize_plugin_setting_value(
            contribution,
            field,
            values.get(key, field.default),
        )
    return result


def _normalize_plugin_setting_value(
    contribution: Any,
    field: Any,
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
        allowed = [item.get("value") for item in field.options if isinstance(item, dict)]
        if allowed and value not in allowed:
            raise ValueError(f"插件设置字段无效：{label}")
        return value
    text = "" if value is None else str(value)
    if field.required and not text.strip():
        raise ValueError(f"插件设置字段不能为空：{label}")
    return text


def _required_text(mapping: Mapping[str, Any], key: str) -> str:
    value = mapping.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"插件设置 RPC 缺少字段：{key}")
    return value.strip()
