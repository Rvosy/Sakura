"""Read only Core-owned system and character selection settings."""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import yaml
from app.storage.paths import StoragePaths

SUPPORTED_CORE_CONFIG_VERSION = 1


@dataclass(frozen=True)
class StableReadinessError:
    state: Literal["setup_required", "failed"]
    code: str
    message: str
    retryable: bool = False


@dataclass(frozen=True)
class CoreConfigReadResult:
    current_character_id: str | None
    config_problem: StableReadinessError | None = None


def _read(path, default, *, allow_empty=False):
    if not path.exists():
        return default
    value = yaml.safe_load(path.read_text(encoding="utf-8"))
    if value is None and allow_empty:
        return default
    if not isinstance(value, Mapping):
        raise ValueError("CONFIG_DATA_INVALID")
    return dict(value)


class CoreConfigReader:
    def read(self, user_root: Path) -> CoreConfigReadResult:
        paths = StoragePaths(user_root)
        try:
            system = _read(paths.system_config(), {"config_version": SUPPORTED_CORE_CONFIG_VERSION})
            version = system.get("config_version")
            if type(version) is not int or version != SUPPORTED_CORE_CONFIG_VERSION:
                return CoreConfigReadResult(None, StableReadinessError("failed", "CONFIG_VERSION_UNSUPPORTED", "配置版本不受支持。"))
            characters = _read(paths.characters_config(), {}, allow_empty=True)
            current = characters.get("current_character_id")
            if current is not None and not isinstance(current, str):
                raise ValueError("CONFIG_DATA_INVALID")
            return CoreConfigReadResult(current.strip() or None if current is not None else None)
        except (OSError, UnicodeError, ValueError, yaml.YAMLError):
            return CoreConfigReadResult(None, StableReadinessError("failed", "CONFIG_DATA_INVALID", "配置数据不可用。"))
