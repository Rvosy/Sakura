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
    boundary = CharacterSettingsBoundary(GENERATION, CREDENTIAL, tmp_path)

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
    assert imported["payload"]["changePlan"] == "core_restart_required"
    snapshot = imported["payload"]["snapshot"]
    assert snapshot["currentCharacterId"] == "fixture"
    assert snapshot["characters"] == [
        {"id": "fixture", "displayName": "Fixture", "hasVoice": False}
    ]
    saved = yaml.safe_load((tmp_path / "config" / "characters.yaml").read_text())
    assert saved == {"current_character_id": "fixture"}


def test_select_same_character_is_unchanged_without_rewriting_config(tmp_path: Path) -> None:
    boundary = CharacterSettingsBoundary(GENERATION, CREDENTIAL, tmp_path)
    boundary.handle(
        _request(
            "characters.settings.import",
            {"path": str(_archive(tmp_path / "fixture.char"))},
        )
    )
    config = tmp_path / "config" / "characters.yaml"
    before = config.read_bytes()

    result = boundary.handle(
        _request("characters.settings.select", {"characterId": "fixture"})
    )

    assert result["ok"] is True
    assert result["payload"]["changePlan"] == "unchanged"
    assert result["payload"]["snapshot"]["currentCharacterId"] == "fixture"
    assert config.read_bytes() == before


def test_select_rejects_unknown_character_without_changing_config(tmp_path: Path) -> None:
    boundary = CharacterSettingsBoundary(GENERATION, CREDENTIAL, tmp_path)
    result = boundary.handle(
        _request("characters.settings.select", {"characterId": "missing"})
    )
    assert result["ok"] is False
    assert result["error"]["code"] == "CHARACTER_NOT_FOUND"
    assert not (tmp_path / "config" / "characters.yaml").exists()


def test_select_save_failure_keeps_existing_character_config(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    boundary = CharacterSettingsBoundary(GENERATION, CREDENTIAL, tmp_path)
    boundary.handle(
        _request(
            "characters.settings.import",
            {"path": str(_archive(tmp_path / "alpha.char", "alpha"))},
        )
    )
    boundary.handle(
        _request(
            "characters.settings.import",
            {"path": str(_archive(tmp_path / "beta.char", "beta"))},
        )
    )
    config = tmp_path / "config" / "characters.yaml"
    before = config.read_bytes()

    def fail_save(*_args, **_kwargs):  # type: ignore[no-untyped-def]
        raise OSError("isolated save failure")

    monkeypatch.setattr(
        type(boundary._settings),  # noqa: SLF001
        "save_current_character_id",
        fail_save,
    )
    result = boundary.handle(
        _request("characters.settings.select", {"characterId": "beta"})
    )

    assert result["ok"] is False
    assert result["error"]["code"] == "CHARACTER_CONFIG_SAVE_FAILED"
    assert config.read_bytes() == before
