"""AppContext 到 IPC JSON DTO 的转换。"""

from __future__ import annotations

from pathlib import Path
from typing import Any


def startup_state_dto(context: Any) -> dict[str, Any]:
    profile = context.character_profile
    portrait_path = Path(profile.default_portrait_path)
    try:
        portrait = portrait_path.relative_to(context.base_dir).as_posix()
    except ValueError:
        portrait = portrait_path.as_posix()
    tool_registry = getattr(context, "tool_registry", None)
    tools = tool_registry.all() if tool_registry is not None else ()
    plugin_manager = getattr(context, "plugin_manager", None)
    plugin_results = getattr(plugin_manager, "results", ()) if plugin_manager is not None else ()

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
            "portrait_choices": list(getattr(profile, "portrait_choices", ())),
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
