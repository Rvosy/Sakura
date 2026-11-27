from __future__ import annotations

from pathlib import Path

import pytest

from app.config.settings_service import AppSettingsService
from app.config.yaml_config import load_yaml_mapping


class CharacterRegistryStub:
    profiles = {"sakura": object(), "nanami": object()}

    def get(self, character_id: str) -> object:
        if character_id not in self.profiles:
            raise KeyError(character_id)
        return self.profiles[character_id]


def test_settings_service_rejects_non_v1_system_config(tmp_path: Path) -> None:
    service = AppSettingsService(tmp_path)
    service.system_config_path.parent.mkdir(parents=True)
    service.system_config_path.write_text("config_version: 2\n", encoding="utf-8")

    with pytest.raises(ValueError, match="版本不受支持"):
        service.load_audio_input_device()


def test_settings_service_saves_character_without_replacing_other_domains(tmp_path: Path) -> None:
    service = AppSettingsService(tmp_path)
    service.config_dir.mkdir(parents=True)
    service.characters_config_path.write_text(
        "current_character_id: sakura\nother: keep\n"
        "visual_selections:\n  sakura: alternate\n  nanami: default\n"
        "seed_import_exclusions: [removed]\n", encoding="utf-8"
    )
    service.system_config_path.write_text(
        "config_version: 1\nstartup:\n  launch_at_login: true\n", encoding="utf-8"
    )

    service.save_current_character_id(CharacterRegistryStub(), "nanami")  # type: ignore[arg-type]
    characters = load_yaml_mapping(service.characters_config_path)
    system = load_yaml_mapping(service.system_config_path)
    assert characters == {
        "current_character_id": "nanami", "other": "keep",
        "visual_selections": {"sakura": "alternate", "nanami": "default"},
        "seed_import_exclusions": ["removed"],
    }
    assert system["startup"]["launch_at_login"] is True
