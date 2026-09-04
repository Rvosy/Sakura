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


def _voice_archive(path: Path, *, complete: bool = True) -> Path:
    voice = {
        "tone_refs": "voice/refs/ref.txt",
        "ref_lang": "ja",
        "text_lang": "ja",
    }
    if complete:
        voice.update(
            {
                "gpt_model": "voice/models/gpt.ckpt",
                "sovits_model": "voice/models/sovits.pth",
            }
        )
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(
            "manifest.json",
            json.dumps(
                {
                    "format": "sakura.character.voice",
                    "version": 1,
                    "voice": voice,
                }
            ),
        )
        archive.writestr(
            "voice/refs/ref.txt",
            "voice/refs/tone_refs/happy.wav|JA|hello|开心\n",
        )
        archive.writestr("voice/refs/tone_refs/happy.wav", b"wav")
        if complete:
            archive.writestr("voice/models/gpt.ckpt", b"gpt")
            archive.writestr("voice/models/sovits.pth", b"sovits")
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
        {
            "id": "fixture",
            "displayName": "Fixture",
            "hasVoice": False,
            "hasExportableVoice": False,
        }
    ]
    saved = yaml.safe_load((tmp_path / "config" / "characters.yaml").read_text())
    assert saved == {"current_character_id": "fixture"}


def test_select_same_character_is_unchanged_without_rewriting_config(
    tmp_path: Path,
) -> None:
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


def test_voice_import_restarts_current_character_and_exports_all_package_kinds(
    tmp_path: Path,
) -> None:
    boundary = CharacterSettingsBoundary(GENERATION, CREDENTIAL, tmp_path)
    boundary.handle(
        _request(
            "characters.settings.import",
            {"path": str(_archive(tmp_path / "fixture.char"))},
        )
    )

    imported = boundary.handle(
        _request(
            "characters.settings.import_voice",
            {
                "path": str(_voice_archive(tmp_path / "fixture.voice")),
                "characterId": "fixture",
            },
        )
    )

    assert imported["ok"] is True
    assert imported["payload"]["changePlan"] == "core_restart_required"
    assert imported["payload"]["snapshot"]["characters"] == [
        {
            "id": "fixture",
            "displayName": "Fixture",
            "hasVoice": True,
            "hasExportableVoice": True,
        }
    ]

    outputs = {
        "full": tmp_path / "fixture-full.char",
        "card": tmp_path / "fixture-card",
        "voice": tmp_path / "fixture.voice.export.voice",
    }
    for kind, output in outputs.items():
        exported = boundary.handle(
            _request(
                "characters.settings.export",
                {"path": str(output), "characterId": "fixture", "kind": kind},
            )
        )
        assert exported["ok"] is True
        expected_output = output.with_suffix(".char") if kind == "card" else output
        assert exported["payload"]["outputPath"] == str(expected_output)
        assert expected_output.is_file()

    with zipfile.ZipFile(outputs["full"]) as archive:
        full_manifest = json.loads(archive.read("manifest.json"))
    with zipfile.ZipFile(outputs["card"].with_suffix(".char")) as archive:
        card_manifest = json.loads(archive.read("manifest.json"))
    with zipfile.ZipFile(outputs["voice"]) as archive:
        voice_manifest = json.loads(archive.read("manifest.json"))
    assert full_manifest["character"]["voice"]["gpt_model"]
    assert "voice" not in card_manifest["character"]
    assert voice_manifest["format"] == "sakura.character.voice"


def test_voice_import_for_inactive_character_does_not_restart(tmp_path: Path) -> None:
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

    result = boundary.handle(
        _request(
            "characters.settings.import_voice",
            {
                "path": str(_voice_archive(tmp_path / "beta.voice")),
                "characterId": "beta",
            },
        )
    )

    assert result["ok"] is True
    assert result["payload"]["changePlan"] == "unchanged"
    assert result["payload"]["snapshot"]["currentCharacterId"] == "alpha"


def test_incomplete_voice_cannot_be_exported_as_full_or_voice_package(
    tmp_path: Path,
) -> None:
    boundary = CharacterSettingsBoundary(GENERATION, CREDENTIAL, tmp_path)
    boundary.handle(
        _request(
            "characters.settings.import",
            {"path": str(_archive(tmp_path / "fixture.char"))},
        )
    )
    imported = boundary.handle(
        _request(
            "characters.settings.import_voice",
            {
                "path": str(_voice_archive(tmp_path / "partial.voice", complete=False)),
                "characterId": "fixture",
            },
        )
    )
    character = imported["payload"]["snapshot"]["characters"][0]
    assert character["hasVoice"] is True
    assert character["hasExportableVoice"] is False

    for kind, suffix in (("full", ".char"), ("voice", ".voice")):
        output = tmp_path / f"blocked{suffix}"
        result = boundary.handle(
            _request(
                "characters.settings.export",
                {"path": str(output), "characterId": "fixture", "kind": kind},
            )
        )
        assert result["ok"] is False
        assert result["error"]["code"] == "CHARACTER_VOICE_NOT_EXPORTABLE"
        assert not output.exists()


def test_select_rejects_unknown_character_without_changing_config(
    tmp_path: Path,
) -> None:
    boundary = CharacterSettingsBoundary(GENERATION, CREDENTIAL, tmp_path)
    result = boundary.handle(
        _request("characters.settings.select", {"characterId": "missing"})
    )
    assert result["ok"] is False
    assert result["error"]["code"] == "CHARACTER_NOT_FOUND"
    assert not (tmp_path / "config" / "characters.yaml").exists()


def test_select_save_failure_keeps_existing_character_config(
    tmp_path: Path, monkeypatch
) -> None:  # type: ignore[no-untyped-def]
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
