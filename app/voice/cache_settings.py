"""Voice replay cache location, size and idle fill, shared with the settings window."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from app.storage.atomic import atomic_write_text


DEFAULT_MAX_BYTES = 512 * 1024 * 1024
MIN_MAX_BYTES = 32 * 1024 * 1024
MAX_MAX_BYTES = 20 * 1024 * 1024 * 1024
_SCHEMA_VERSION = 1


@dataclass(frozen=True)
class VoiceCacheSettings:
    directory: Path | None
    max_bytes: int
    idle_fill: bool


def cache_settings_path(user_root: Path) -> Path:
    return Path(user_root) / "config" / "voice_cache.json"


def default_recordings_dir(user_root: Path) -> Path:
    return Path(user_root) / "data" / "voice" / "recordings"


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
    if isinstance(max_bytes, bool) or not isinstance(max_bytes, int) or not MIN_MAX_BYTES <= max_bytes <= MAX_MAX_BYTES:
        max_bytes = DEFAULT_MAX_BYTES
    idle_fill = data.get("idleFill") is True
    raw_directory = data.get("directory")
    if not isinstance(raw_directory, str) or not raw_directory.strip():
        return VoiceCacheSettings(None, max_bytes, idle_fill)
    directory = Path(raw_directory)
    if not directory.is_absolute():
        return VoiceCacheSettings(None, max_bytes, idle_fill)
    try:
        resolved = directory.resolve()
    except OSError:
        return VoiceCacheSettings(None, max_bytes, idle_fill)
    if resolved.is_symlink() or (resolved.exists() and not resolved.is_dir()):
        return VoiceCacheSettings(None, max_bytes, idle_fill)
    return VoiceCacheSettings(resolved, max_bytes, idle_fill)


def save_voice_cache_settings(
    user_root: Path,
    directory: str,
    max_bytes: int,
    idle_fill: bool,
) -> VoiceCacheSettings:
    if isinstance(max_bytes, bool) or not isinstance(max_bytes, int) or not MIN_MAX_BYTES <= max_bytes <= MAX_MAX_BYTES:
        raise ValueError("max_bytes is invalid")
    if not isinstance(idle_fill, bool):
        raise ValueError("idle_fill is invalid")
    default_dir = default_recordings_dir(user_root)
    chosen = directory.strip()
    stored = ""
    effective: Path | None = None
    if chosen:
        path = Path(chosen)
        if not path.is_absolute():
            raise ValueError("directory must be absolute")
        resolved = path.resolve()
        if resolved.is_symlink() or (resolved.exists() and not resolved.is_dir()):
            raise ValueError("directory is unavailable")
        resolved.mkdir(parents=True, exist_ok=True)
        if resolved != default_dir.resolve():
            stored = str(resolved)
            effective = resolved
    document = {
        "schemaVersion": _SCHEMA_VERSION,
        "directory": stored,
        "maxBytes": max_bytes,
        "idleFill": idle_fill,
    }
    target = cache_settings_path(user_root)
    target.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(target, json.dumps(document, ensure_ascii=False, indent=2) + "\n", backup=False)
    return VoiceCacheSettings(effective, max_bytes, idle_fill)
