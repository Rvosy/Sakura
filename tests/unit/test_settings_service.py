from __future__ import annotations

import uuid
from pathlib import Path

import pytest

from app.plugin_sdk.sakura_assistant_contract import RuntimeLoopSettings
from app.config.settings_service import (
    AppSettingsService,
    BubbleSettings,
)
from app.config.models import (
    ThemeSettings,
)
from app.config.yaml_config import load_yaml_mapping


class CharacterRegistryStub:
    profiles = {"sakura": object(), "nanami": object()}

    def get(self, character_id: str) -> object:
        if character_id not in self.profiles:
            raise KeyError(character_id)
        return self.profiles[character_id]


def test_settings_service_rejects_non_v1_system_config() -> None:
    root = _runtime_root("system_schema")
    service = AppSettingsService(root)
    service.system_config_path.parent.mkdir(parents=True)
    service.system_config_path.write_text("config_version: 2\n", encoding="utf-8")

    with pytest.raises(ValueError, match="版本不受支持"):
        service.load_startup_settings()


def test_settings_service_saves_character_without_replacing_other_domains() -> None:
    root = _runtime_root("yaml_save")
    service = AppSettingsService(root)
    service.config_dir.mkdir(parents=True)
    service.characters_config_path.write_text(
        "current_character_id: sakura\nother: keep\n", encoding="utf-8"
    )
    service.system_config_path.write_text(
        "config_version: 1\nstartup:\n  launch_at_login: true\n", encoding="utf-8"
    )

    service.save_current_character_id(CharacterRegistryStub(), "nanami")  # type: ignore[arg-type]
    characters = load_yaml_mapping(service.characters_config_path)
    system = load_yaml_mapping(service.system_config_path)
    assert characters == {"current_character_id": "nanami", "other": "keep"}
    assert system["startup"]["launch_at_login"] is True


def test_settings_service_reads_bubble_settings() -> None:
    root = _runtime_root("yaml_bubble")
    service = AppSettingsService(root)
    assert service.load_bubble_settings() == BubbleSettings()
    service.config_dir.mkdir(parents=True)
    service.system_config_path.write_text(
        "config_version: 1\nui:\n  bubble_auto_hide_enabled: false\n  bubble_auto_hide_delay_seconds: 8\n",
        encoding="utf-8",
    )

    assert service.load_bubble_settings() == BubbleSettings(
        auto_hide_enabled=False, auto_hide_delay_seconds=8
    )


def test_settings_service_reads_normalized_runtime_loop_settings() -> None:
    root = _runtime_root("yaml_runtime_loop")
    service = AppSettingsService(root)
    assert service.load_runtime_loop_settings() == RuntimeLoopSettings()
    service.config_dir.mkdir(parents=True)
    service.system_config_path.write_text(
        "config_version: 1\ntool_loop:\n  max_agent_steps_per_turn: 20\n"
        "  max_tool_calls_per_step: 6\n  max_tool_calls_per_turn: 4\n",
        encoding="utf-8",
    )

    loaded = service.load_runtime_loop_settings()
    assert loaded.max_agent_steps_per_turn == 12
    assert loaded.max_tool_calls_per_step == 6
    assert loaded.max_tool_calls_per_turn == 6


def test_settings_service_rejects_retired_debug_field() -> None:
    root = _runtime_root("yaml_debug")
    service = AppSettingsService(root)
    service.config_dir.mkdir(parents=True)
    service.system_config_path.write_text(
        "config_version: 1\ndebug:\n  raw_tts_service_enabled: true\n", encoding="utf-8"
    )

    with pytest.raises(ValueError, match="已废止"):
        service.load_debug_log_settings()


def test_settings_service_reads_theme_preferences_without_global_colors() -> None:
    root = _runtime_root("yaml_theme")
    service = AppSettingsService(root)
    service.config_dir.mkdir(parents=True)
    service.system_config_path.write_text(
        "config_version: 1\nui:\n  theme:\n    primary_color: '#112233'\n"
        "    ai_enabled: true\n    visual_effect_mode: solid\n",
        encoding="utf-8",
    )

    assert service.load_theme_settings() == ThemeSettings(
        ai_enabled=True, visual_effect_mode="solid"
    )


def test_settings_service_reads_character_theme_overrides() -> None:
    root = _runtime_root("yaml_character_theme_override")
    service = AppSettingsService(root)
    service.config_dir.mkdir(parents=True)
    service.system_config_path.write_text(
        "config_version: 1\nui:\n  character_theme_overrides:\n    N.A.V.I:\n"
        "      primary_color: '#112233'\n      accent_color: '#445566'\n",
        encoding="utf-8",
    )

    assert service.load_character_theme_overrides() == {
        "N.A.V.I": ThemeSettings(primary_color="#112233", accent_color="#445566")
    }


def test_settings_service_loads_default_theme_for_invalid_values() -> None:
    root = _runtime_root("yaml_theme_invalid")
    service = AppSettingsService(root)
    service.config_dir.mkdir(parents=True)
    service.system_config_path.write_text(
        "config_version: 1\nui:\n  theme:\n    primary_color: bad\n"
        "    primary_hover_color: '#123'\n    accent_color: '#123'\n    text_color: null\n"
        "    secondary_text_color: ''\n    ai_enabled: 'yes'\n",
        encoding="utf-8",
    )

    assert service.load_theme_settings() == ThemeSettings(ai_enabled=True)


def _runtime_root(name: str) -> Path:
    root = Path(__file__).resolve().parents[2] / "temp" / "test_runtime" / uuid.uuid4().hex / name
    root.mkdir(parents=True, exist_ok=True)
    return root
