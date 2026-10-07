from __future__ import annotations

import json
from pathlib import Path
import pytest

from app.core_host.plugin_character import PluginCharacterStore
from app.core_host.plugin_character import PluginCharacterError


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


def test_current_character_changes_only_at_explicit_session_boundary(tmp_path: Path) -> None:
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
    old_generation.set_current("beta")
    assert old_generation.current("fixture.plugin") == new_generation.current("fixture.plugin")


@pytest.mark.parametrize("relative", ["../outside.bin", "/outside.bin", "C:/outside.bin", "linked.bin"])
def test_resource_declarations_reject_escape_without_rewriting_manifest(tmp_path, relative):
    _write_character(tmp_path, "alpha")
    package = tmp_path / "characters/alpha"
    if relative == "linked.bin":
        outside = tmp_path / "outside.bin"
        outside.write_bytes(b"outside")
        try:
            (package / relative).symlink_to(outside)
        except OSError:
            pytest.skip("symlinks unavailable")
    manifest = package / "character.json"
    original = manifest.read_bytes()
    with pytest.raises(PluginCharacterError, match="CHARACTER_RESOURCE_DECLARATION_INVALID"):
        PluginCharacterStore(tmp_path).declare_resources("future.plugin", "alpha", {"kind": "tts", "paths": [relative]})
    assert manifest.read_bytes() == original


def test_resource_declaration_cannot_replace_another_plugins_data(tmp_path):
    from app.core_host.plugin_host_services import _CharacterHostService, HostServiceError
    from app.plugins.host_services import HOST_CALLER

    _write_character(tmp_path, "alpha")
    service = _CharacterHostService(PluginCharacterStore(tmp_path))
    caller = HOST_CALLER.set("first.plugin")
    try:
        with pytest.raises(HostServiceError, match="CHARACTER_RESOURCE_OWNER_INVALID"):
            service.call("declare_resources", ["other.plugin", "alpha", {"kind": "tts", "paths": ["card.md"]}])
    finally:
        HOST_CALLER.reset(caller)
