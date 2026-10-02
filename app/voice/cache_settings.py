"""Voice replay cache location, size and idle fill, shared with the settings window."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

DEFAULT_MAX_BYTES = 512 * 1024 * 1024
_SCHEMA_VERSION = 1


@dataclass(frozen=True)
class VoiceCacheSettings:
    directory: Path | None
    max_bytes: int
    idle_fill: bool


def cache_settings_path(user_root: Path) -> Path:
    return Path(user_root) / "config" / "voice_cache.json"


def load_voice_cache_settings(user_root: Path) -> VoiceCacheSettings:
    """Return the custom recordings directory, or None for the default, plus the byte cap."""

    path = cache_settings_path(user_root)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return VoiceCacheSettings(None, DEFAULT_MAX_BYTES, False)
    if not isinstance(data, dict) or data.get("schemaVersion") != _SCHEMA_VERSION:
        return VoiceCacheSettings(None, DEFAULT_MAX_BYTES, False)
    max_bytes = data.get("maxBytes")
    if isinstance(max_bytes, bool) or not isinstance(max_bytes, int) or max_bytes <= 0:
        max_bytes = DEFAULT_MAX_BYTES
    idle_fill = data.get("idleFill") is True
    raw_directory = data.get("directory")
    if not isinstance(raw_directory, str) or not raw_directory.strip():
        return VoiceCacheSettings(None, max_bytes, idle_fill)
    directory = Path(raw_directory)
    if not directory.is_absolute():
        return VoiceCacheSettings(None, max_bytes, idle_fill)
    return VoiceCacheSettings(directory, max_bytes, idle_fill)
