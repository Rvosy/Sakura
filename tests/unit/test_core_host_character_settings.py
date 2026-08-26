from __future__ import annotations

import json
import zipfile
from pathlib import Path

import yaml

from app.core_host.character_settings import CharacterSettingsBoundary


GENERATION = "generation-character-settings"
CREDENTIAL = "0123456789abcdef0123456789abcdef"


def _request(name: str, payload: dict[str, object]) -> dict[str, object]:
    return {
        "protocolMajor": 2,
        "protocolMinor": 2,
        "kind": "request",
        "generationId": GENERATION,
        "generationCredential": CREDENTIAL,
        "id": name,
        "name": name,
        "payload": payload,
        "deadlineMs": 3000,
        "priority": "interactive",
    }


def _archive(path: Path, character_id: str = "fixture") -> Path:
    manifest = {
        "format": "sakura.character.archive",
        "version": 1,
        "character": {
            "id": character_id,
            "display_name": "Fixture",
            "card": "character/card.txt",
            "portrait": {"default": "character/portrait.png", "expressions": {}},
        },
    }
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("manifest.json", json.dumps(manifest))
        archive.writestr("character/card.txt", "You are Fixture.")
        archive.writestr("character/portrait.png", b"not-decoded-by-importer")
    return path


def test_empty_snapshot_and_first_import_auto_select(tmp_path: Path) -> None:
    applied: list[bool] = []
    boundary = CharacterSettingsBoundary(
        GENERATION,
        CREDENTIAL,
        tmp_path,
        runtime_apply=lambda: applied.append(True),
    )

    empty = boundary.handle(_request("characters.settings.get", {}))
    assert empty["payload"] == {
        "schemaVersion": 1,
        "revision": 1,
        "currentCharacterId": None,
        "characters": [],
    }

    imported = boundary.handle(
        _request(
            "characters.settings.import",
            {"path": str(_archive(tmp_path / "fixture.char"))},
        )
    )
    assert imported["ok"] is True
    assert imported["payload"]["currentCharacterId"] == "fixture"
    assert imported["payload"]["characters"] == [
        {"id": "fixture", "displayName": "Fixture", "hasVoice": False}
    ]
    assert applied == [True]
    saved = yaml.safe_load((tmp_path / "config" / "characters.yaml").read_text())
    assert saved == {"current_character_id": "fixture"}


def test_select_rejects_unknown_character_without_changing_config(tmp_path: Path) -> None:
    boundary = CharacterSettingsBoundary(GENERATION, CREDENTIAL, tmp_path)
    result = boundary.handle(
        _request("characters.settings.select", {"characterId": "missing"})
    )
    assert result["ok"] is False
    assert result["error"]["code"] == "CHARACTER_NOT_FOUND"
    assert not (tmp_path / "config" / "characters.yaml").exists()
