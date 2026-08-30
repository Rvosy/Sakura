from __future__ import annotations

import json
from pathlib import Path

from app.core_host.plugin_character import PluginCharacterStore


def _write_character(root: Path, character_id: str) -> None:
    package = root / "characters" / character_id
    package.mkdir(parents=True)
    (package / "card.md").write_text(f"You are {character_id}.", encoding="utf-8")
    (package / "portrait.png").write_bytes(b"fixture")
    (package / "character.json").write_text(
        json.dumps(
            {
                "id": character_id,
                "display_name": character_id,
                "card": "card.md",
                "portrait": {"default": "portrait.png", "expressions": {}},
            }
        ),
        encoding="utf-8",
    )


def _select(root: Path, character_id: str) -> None:
    config = root / "config"
    config.mkdir(parents=True, exist_ok=True)
    (config / "characters.yaml").write_text(
        f"current_character_id: {character_id}\n",
        encoding="utf-8",
    )


def test_current_character_is_frozen_for_the_generation(tmp_path: Path) -> None:
    _write_character(tmp_path, "alpha")
    _write_character(tmp_path, "beta")
    _select(tmp_path, "alpha")
    old_generation = PluginCharacterStore(tmp_path)

    _select(tmp_path, "beta")
    new_generation = PluginCharacterStore(tmp_path)

    assert old_generation.current("fixture.plugin") == {
        "id": "alpha",
        "systemPrompt": "You are alpha.",
    }
    assert new_generation.current("fixture.plugin") == {
        "id": "beta",
        "systemPrompt": "You are beta.",
    }
