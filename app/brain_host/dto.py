"""AppContext 到 IPC JSON DTO 的转换。"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any

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
from app.config.theme import DEFAULT_THEME_SETTINGS, ThemeSettings, theme_to_mapping


def startup_state_dto(context: Any) -> dict[str, Any]:
    profile = context.character_profile
    portrait = _relative_asset_path(context.base_dir, profile.default_portrait_path)
    expression_portraits = _expression_portraits(profile)
    portrait_choices = list(expression_portraits)
    if not portrait_choices:
        raw_choices = getattr(profile, "portrait_choices", ())
        if isinstance(raw_choices, dict):
            portrait_choices = [str(key) for key in raw_choices]
        elif isinstance(raw_choices, (list, tuple)):
            portrait_choices = [str(key) for key in raw_choices]
    tool_registry = getattr(context, "tool_registry", None)
    tools = tool_registry.all() if tool_registry is not None else ()
    plugin_manager = getattr(context, "plugin_manager", None)
    plugin_results = getattr(plugin_manager, "results", ()) if plugin_manager is not None else ()
    settings_service = getattr(context, "settings_service", None)
    ui_values = _load_ui_values(settings_service)

    return {
        "version": 1,
        "state": "ready",
        "base_dir": str(Path(context.base_dir).resolve()),
        "character": {
            "id": profile.id,
            "display_name": profile.display_name,
            "initial_message": profile.initial_message,
            "default_portrait": portrait,
            "reply_tones": list(getattr(profile, "reply_tones", ())),
            "portrait_choices": portrait_choices,
            "portraits": {
                "default": portrait,
                "expressions": {
                    key: _relative_asset_path(context.base_dir, path)
                    for key, path in expression_portraits.items()
                },
            },
        },
        "theme": theme_to_mapping(_effective_theme(settings_service, profile)),
        "layout": {
            "portrait_scale_percent": _clamp_int(
                ui_values.get("portrait_scale_percent"), 50, 150, 100
            ),
            "control_panel_width": _clamp_int(
                ui_values.get("control_panel_width"), 420, 860, 640
            ),
            "bubble_height": _clamp_int(ui_values.get("bubble_height"), 96, 260, 128),
            "vertical_offset": _clamp_int(
                ui_values.get("control_panel_vertical_offset"), -200, 200, 0
            ),
            "input_bar_offset": _clamp_int(
                ui_values.get("input_bar_offset"), 0, 200, 0
            ),
            "speech_font_size": _clamp_int(
                ui_values.get("speech_font_size"),
                SPEECH_FONT_SIZE_MIN,
                SPEECH_FONT_SIZE_MAX,
                DEFAULT_SPEECH_FONT_SIZE,
            ),
            "name_font_size": _clamp_int(
                ui_values.get("name_font_size"),
                NAME_FONT_SIZE_MIN,
                NAME_FONT_SIZE_MAX,
                DEFAULT_NAME_FONT_SIZE,
            ),
            "input_font_size": _clamp_int(
                ui_values.get("input_font_size"),
                INPUT_FONT_SIZE_MIN,
                INPUT_FONT_SIZE_MAX,
                DEFAULT_INPUT_FONT_SIZE,
            ),
            "button_font_size": _clamp_int(
                ui_values.get("button_font_size"),
                BUTTON_FONT_SIZE_MIN,
                BUTTON_FONT_SIZE_MAX,
                DEFAULT_BUTTON_FONT_SIZE,
            ),
        },
        "subtitle": {
            "language": "ja"
            if str(ui_values.get("subtitle_language", "")).strip().lower() == "ja"
            else "zh",
            "typing_interval_ms": _clamp_int(
                ui_values.get("subtitle_typing_interval_ms"), 5, 200, 35
            ),
            "segment_pause_ms": _clamp_int(
                ui_values.get("reply_segment_pause_ms"), 0, 3000, 100
            ),
        },
        "model": {
            "base_url": context.settings.base_url,
            "model": context.settings.model,
            "timeout_seconds": context.settings.timeout_seconds,
        },
        "runtime": {
            "tool_count": len(tools),
            "plugin_count": sum(1 for result in plugin_results if getattr(result, "loaded", False)),
            "mcp_ready": getattr(context, "mcp_tool_provider", None) is not None,
            "tts_ready": bool(getattr(getattr(context, "tts_provider", None), "service_ready", False)),
            "startup_initializing": bool(getattr(context, "startup_initializing", False)),
        },
    }


def _relative_asset_path(base_dir: Path, path: object) -> str:
    target = Path(path)
    if not target.is_absolute():
        target = Path(base_dir) / target
    try:
        return target.resolve().relative_to(Path(base_dir).resolve()).as_posix()
    except (OSError, ValueError):
        return target.as_posix()


def _expression_portraits(profile: object) -> dict[str, Path]:
    raw = getattr(profile, "expression_portraits", None)
    if isinstance(raw, dict):
        return {str(key): Path(path) for key, path in raw.items()}
    choices = getattr(profile, "portrait_choices", None)
    if isinstance(choices, dict):
        return {str(key): Path(path) for key, path in choices.items()}
    return {}


def _load_ui_values(settings_service: object | None) -> dict[str, object]:
    load = getattr(settings_service, "load_system_values", None)
    if not callable(load):
        return {}
    values = load("ui")
    return values if isinstance(values, dict) else {}


def _effective_theme(settings_service: object | None, profile: object) -> ThemeSettings:
    load_theme = getattr(settings_service, "load_theme_settings", None)
    user_theme = load_theme() if callable(load_theme) else DEFAULT_THEME_SETTINGS
    if not isinstance(user_theme, ThemeSettings):
        user_theme = DEFAULT_THEME_SETTINGS
    load_override = getattr(settings_service, "load_character_theme_override", None)
    override = load_override(getattr(profile, "id", "")) if callable(load_override) else None
    if isinstance(override, ThemeSettings):
        colors = override.normalized()
    elif getattr(profile, "theme_source", None) == "package" and isinstance(
        getattr(profile, "theme_settings", None), ThemeSettings
    ):
        colors = profile.theme_settings.normalized()
    else:
        colors = DEFAULT_THEME_SETTINGS
    normalized_user = user_theme.normalized()
    return replace(
        colors,
        ai_enabled=normalized_user.ai_enabled,
        visual_effect_mode=normalized_user.visual_effect_mode,
    )


def _clamp_int(value: object, minimum: int, maximum: int, default: int) -> int:
    try:
        parsed = int(str(value).strip())
    except (TypeError, ValueError):
        return default
    return max(minimum, min(maximum, parsed))
