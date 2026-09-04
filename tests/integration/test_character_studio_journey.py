from __future__ import annotations

import json
import zipfile
from pathlib import Path

import yaml

from app.core_host.character_studio import CharacterStudioBoundary


GENERATION = "generation-studio-journey"
CREDENTIAL = "1234567890abcdef1234567890abcdef"


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
        "deadlineMs": 30_000,
        "priority": "interactive",
    }


def _call(
    boundary: CharacterStudioBoundary,
    name: str,
    payload: dict[str, object],
) -> dict[str, object]:
    result = boundary.handle(_request(name, payload))
    assert result["ok"] is True, result
    return result["payload"]


def _write_character(root: Path) -> None:
    package = root / "characters" / "sakura"
    (package / "portraits").mkdir(parents=True)
    (package / "portraits" / "default.png").write_bytes(b"portrait")
    (package / "card.md").write_text("old card", encoding="utf-8")
    (package / "character.json").write_text(
        json.dumps(
            {
                "id": "sakura",
                "display_name": "Sakura",
                "card": "card.md",
                "portrait": {
                    "default": "portraits/default.png",
                    "expressions": {"默认": "portraits/default.png"},
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (root / "config").mkdir()
    (root / "config" / "characters.yaml").write_text(
        yaml.safe_dump({"current_character_id": "sakura"}), encoding="utf-8"
    )


def test_character_studio_current_character_publish_and_export_journey(tmp_path: Path) -> None:
    _write_character(tmp_path)
    boundary = CharacterStudioBoundary(GENERATION, CREDENTIAL, tmp_path)
    bootstrap = _call(boundary, "studio.bootstrap", {"initialCharacterId": "sakura"})
    assert bootstrap["selectedCharacterId"] == "sakura"
    opened = _call(boundary, "studio.character.open", {"characterId": "sakura"})
    doc = opened["doc"]
    doc["cardText"] = "new card"
    _call(
        boundary,
        "studio.draft.save",
        {"workspaceId": opened["workspaceId"], "doc": doc},
    )

    resumed_boundary = CharacterStudioBoundary(GENERATION, CREDENTIAL, tmp_path)
    resumed = _call(
        resumed_boundary,
        "studio.character.open",
        {"characterId": "sakura"},
    )
    assert resumed["resumed"] is True
    assert resumed["doc"]["cardText"] == "new card"
    published = _call(
        resumed_boundary,
        "studio.character.publish",
        {"workspaceId": resumed["workspaceId"], "doc": resumed["doc"]},
    )
    assert published["changePlan"] == "core_restart_required"
    assert (tmp_path / "characters" / "sakura" / "card.md").read_text(encoding="utf-8") == "new card"

    output = tmp_path / "sakura.char"
    exported = _call(
        resumed_boundary,
        "studio.archive.export",
        {
            "workspaceId": published["workspaceId"],
            "path": str(output),
            "includeVoice": False,
        },
    )
    assert exported["outputPath"] == str(output)
    with zipfile.ZipFile(output) as archive:
        assert json.loads(archive.read("manifest.json"))["character"]["id"] == "sakura"


def test_character_studio_reference_preview_descriptor_stays_core_private(tmp_path: Path) -> None:
    boundary = CharacterStudioBoundary(GENERATION, CREDENTIAL, tmp_path)
    created = _call(
        boundary,
        "studio.character.create",
        {"doc": {"id": "voice", "displayName": "Voice"}},
    )
    source = tmp_path / "reference.wav"
    source.write_bytes(b"RIFF-fixture")
    imported = _call(
        boundary,
        "studio.asset.import",
        {
            "workspaceId": created["workspaceId"],
            "kind": "referenceAudio",
            "path": str(source),
        },
    )
    preview = _call(
        boundary,
        "studio.reference.preview",
        {
            "workspaceId": created["workspaceId"],
            "relativePath": imported["relativePath"],
        },
    )

    assert preview["mediaType"] == "audio/wav"
    assert preview["byteLength"] == len(b"RIFF-fixture")
    assert Path(preview["sourcePath"]).is_file()
    assert "data:" not in json.dumps(preview)
