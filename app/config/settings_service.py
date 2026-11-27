from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.config.character_loader import CharacterRegistry
from app.config.yaml_config import load_yaml_mapping, save_yaml_mapping
from app.storage.paths import StoragePaths
from app.agent.screen_awareness import (
    SCREEN_AWARENESS_DEFAULT_CHECK_INTERVAL_MINUTES,
    SCREEN_AWARENESS_DEFAULT_COOLDOWN_MINUTES,
    SCREEN_AWARENESS_DEFAULT_SCREEN_CONTEXT_BATCH_LIMIT,
    SCREEN_AWARENESS_DEFAULT_SCREEN_CONTEXT_RESOLUTION,
    ScreenAwarenessSettings,
)


CHARACTERS_CONFIG_FILE = "characters.yaml"
SYSTEM_CONFIG_FILE = "system_config.yaml"
SYSTEM_CONFIG_VERSION = 1


@dataclass(frozen=True)
class AppSettingsService:
    """读取和保存宿主共享 YAML 配置。"""

    base_dir: Path

    @property
    def config_dir(self) -> Path:
        return StoragePaths(self.base_dir).config_dir

    @property
    def characters_config_path(self) -> Path:
        return self.config_dir / CHARACTERS_CONFIG_FILE

    @property
    def system_config_path(self) -> Path:
        return self.config_dir / SYSTEM_CONFIG_FILE

    def load_screen_awareness_settings(self) -> ScreenAwarenessSettings:
        screen_awareness = self._system_section("screen_awareness")
        enabled = bool(
            _bool_value(screen_awareness.get("enabled"), True)
            and _bool_value(screen_awareness.get("screen_context_enabled"), True)
        )
        return ScreenAwarenessSettings(
            enabled=enabled,
            screen_context_enabled=True,
            check_interval_minutes=_int_value(
                screen_awareness.get("check_interval_minutes"),
                SCREEN_AWARENESS_DEFAULT_CHECK_INTERVAL_MINUTES,
            ),
            cooldown_minutes=_int_value(
                screen_awareness.get("cooldown_minutes"),
                SCREEN_AWARENESS_DEFAULT_COOLDOWN_MINUTES,
            ),
            screen_context_batch_limit=_int_value(
                screen_awareness.get("screen_context_batch_limit"),
                SCREEN_AWARENESS_DEFAULT_SCREEN_CONTEXT_BATCH_LIMIT,
            ),
            screen_context_resolution=str(
                screen_awareness.get(
                    "screen_context_resolution",
                    SCREEN_AWARENESS_DEFAULT_SCREEN_CONTEXT_RESOLUTION,
                )
            ),
        )

    def load_audio_input_device(self) -> str:
        value = self._system_section("audio_input").get("device_id", "")
        return value if isinstance(value, str) else ""

    def load_current_character_id(
        self,
        character_registry: CharacterRegistry,
    ) -> str | None:
        data = load_yaml_mapping(self.characters_config_path)
        configured = str(data.get("current_character_id", "")).strip()
        if configured in character_registry.profiles:
            return configured
        return None

    def save_current_character_id(
        self,
        character_registry: CharacterRegistry,
        character_id: str,
    ) -> None:
        self.save_character_selection(character_registry, character_id, {})

    def load_visual_selections(self) -> dict[str, str]:
        return _visual_selections(load_yaml_mapping(self.characters_config_path))

    def load_seed_import_exclusions(self) -> set[str]:
        return _seed_import_exclusions(load_yaml_mapping(self.characters_config_path))

    def selected_visual_resource(self, profile):
        resource_id = self.load_visual_selections().get(profile.id, profile.default_visual_id)
        return next((item for item in profile.visual_resources if item.id == resource_id), profile.current_visual_resource)

    def save_character_selection(self, character_registry, character_id: str, visual_selections: dict[str, str | None]) -> None:
        character_registry.get(character_id)
        data = load_yaml_mapping(self.characters_config_path)
        data["current_character_id"] = character_id
        selections = _visual_selections(data)
        for target, resource_id in visual_selections.items():
            profile = character_registry.get(target)
            if resource_id is None:
                selections.pop(target, None)
            elif any(item.id == resource_id for item in profile.visual_resources):
                selections[target] = resource_id
            else:
                raise ValueError("VISUAL_RESOURCE_NOT_FOUND")
        if selections:
            data["visual_selections"] = selections
        else:
            data.pop("visual_selections", None)
        save_yaml_mapping(self.characters_config_path, data)

    def forget_character(
        self,
        character_registry: CharacterRegistry,
        removed_id: str,
        next_character_id: str | None,
    ) -> None:
        data = load_yaml_mapping(self.characters_config_path)
        data["seed_import_exclusions"] = sorted(_seed_import_exclusions(data) | {removed_id})
        if next_character_id:
            character_registry.get(next_character_id)
            data["current_character_id"] = next_character_id
        else:
            configured = str(data.get("current_character_id", "")).strip()
            if configured == removed_id or configured not in character_registry.profiles:
                data.pop("current_character_id", None)
        selections = _visual_selections(data)
        selections.pop(removed_id, None)
        for key in [item for item in selections if item not in character_registry.profiles]:
            selections.pop(key, None)
        if selections:
            data["visual_selections"] = selections
        else:
            data.pop("visual_selections", None)
        save_yaml_mapping(self.characters_config_path, data)

    def _system_section(self, name: str) -> dict[str, Any]:
        return _mapping(self._system_document().get(name))

    def _system_document(self) -> dict[str, Any]:
        if not self.system_config_path.exists():
            return {"config_version": SYSTEM_CONFIG_VERSION}
        data = load_yaml_mapping(self.system_config_path)
        version = data.get("config_version")
        if isinstance(version, bool) or not isinstance(version, int) or version != SYSTEM_CONFIG_VERSION:
            raise ValueError("系统配置版本不受支持。")
        return data


def _visual_selections(data: dict[str, Any]) -> dict[str, str]:
    raw = data.get("visual_selections", {})
    return {key: value for key, value in raw.items() if isinstance(key, str) and isinstance(value, str)} if isinstance(raw, dict) else {}


def _seed_import_exclusions(data: dict[str, Any]) -> set[str]:
    raw = data.get("seed_import_exclusions", [])
    return {value for value in raw if isinstance(value, str) and value} if isinstance(raw, list) else set()


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _int_value(value: Any, default: int) -> int:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


def _bool_value(value: Any, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return default
