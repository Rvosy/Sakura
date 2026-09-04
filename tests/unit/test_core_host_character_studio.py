from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest
import yaml

from app.core_host.character_studio import CharacterStudioBoundary


GENERATION = "generation-character-studio"
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


def _write_character(root: Path, character_id: str) -> None:
    package = root / "characters" / character_id
    (package / "portraits").mkdir(parents=True)
    (package / "portraits" / "default.png").write_bytes(b"portrait")
    (package / "card.md").write_text(f"card for {character_id}", encoding="utf-8")
    (package / "character.json").write_text(
        json.dumps(
            {
                "id": character_id,
                "display_name": character_id.title(),
                "card": "card.md",
                "portrait": {"default": "portraits/default.png", "expressions": {}},
            }
        ),
        encoding="utf-8",
    )


def test_bootstrap_preserves_order_and_hides_filesystem_paths(tmp_path: Path) -> None:
    _write_character(tmp_path, "alpha")
    _write_character(tmp_path, "beta")
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "characters.yaml").write_text(
        yaml.safe_dump({"current_character_id": "beta"}), encoding="utf-8"
    )
    boundary = CharacterStudioBoundary(GENERATION, CREDENTIAL, tmp_path)

    result = boundary.handle(_request("studio.bootstrap", {"initialCharacterId": "alpha"}))

    assert result["ok"] is True
    payload = result["payload"]
    assert payload["schemaVersion"] == 1
    assert payload["selectedCharacterId"] == "alpha"
    assert payload["currentCharacterId"] == "beta"
    assert [item["id"] for item in payload["characters"]] == ["beta", "alpha"]
    assert [item["isCurrent"] for item in payload["characters"]] == [True, False]
    assert "packageDir" not in json.dumps(payload)


def test_open_uses_real_current_character_in_catalog(tmp_path: Path) -> None:
    _write_character(tmp_path, "alpha")
    _write_character(tmp_path, "beta")
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "characters.yaml").write_text(
        yaml.safe_dump({"current_character_id": "beta"}), encoding="utf-8"
    )
    boundary = CharacterStudioBoundary(GENERATION, CREDENTIAL, tmp_path)

    result = boundary.handle(
        _request("studio.character.open", {"characterId": "alpha"})
    )["payload"]

    assert result["currentCharacterId"] == "beta"
    assert next(item for item in result["characters"] if item["id"] == "beta")["isCurrent"]
    assert not next(item for item in result["characters"] if item["id"] == "alpha")["isCurrent"]


def test_publish_reports_restart_only_for_current_character(tmp_path: Path) -> None:
    _write_character(tmp_path, "alpha")
    _write_character(tmp_path, "beta")
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "characters.yaml").write_text(
        yaml.safe_dump({"current_character_id": "alpha"}), encoding="utf-8"
    )
    boundary = CharacterStudioBoundary(GENERATION, CREDENTIAL, tmp_path)
    opened = boundary.handle(
        _request("studio.character.open", {"characterId": "alpha"})
    )["payload"]
    doc = opened["doc"]
    doc["cardText"] = "updated"

    current = boundary.handle(
        _request(
            "studio.character.publish",
            {"workspaceId": opened["workspaceId"], "doc": doc},
        )
    )

    assert current["ok"] is True
    assert current["payload"]["changePlan"] == "core_restart_required"

    other = boundary.handle(
        _request("studio.character.open", {"characterId": "beta"})
    )["payload"]
    other_result = boundary.handle(
        _request(
            "studio.character.publish",
            {"workspaceId": other["workspaceId"], "doc": other["doc"]},
        )
    )
    assert other_result["payload"]["changePlan"] == "unchanged"


def test_boundary_rejects_unknown_dto_fields_and_generation(tmp_path: Path) -> None:
    boundary = CharacterStudioBoundary(GENERATION, CREDENTIAL, tmp_path)
    invalid = boundary.handle(
        _request(
            "studio.character.create",
            {"doc": {"id": "new", "displayName": "New", "packageDir": "/tmp/private"}},
        )
    )

    assert invalid["ok"] is False
    assert invalid["error"]["code"] == "STUDIO_REQUEST_INVALID"
    assert invalid["error"]["details"]["field"] == "packageDir"

    wrong_generation = _request("studio.bootstrap", {})
    wrong_generation["generationId"] = "old-generation"
    with pytest.raises(RuntimeError, match="GENERATION_IDENTITY_MISMATCH"):
        boundary.handle(wrong_generation)


def test_operation_cancel_reaches_active_copy_and_has_stable_terminal_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    boundary = CharacterStudioBoundary(GENERATION, CREDENTIAL, tmp_path)
    created = boundary.handle(
        _request(
            "studio.character.create",
            {"doc": {"id": "cancelled", "displayName": "Cancelled"}},
        )
    )["payload"]
    source = tmp_path / "model.ckpt"
    source.write_bytes(b"model")
    entered = threading.Event()
    release = threading.Event()
    original = boundary._service.import_voice_model  # noqa: SLF001

    def slow_import(*args, cancel_check=None, **kwargs):  # type: ignore[no-untyped-def]
        entered.set()
        while not release.wait(0.01):
            if cancel_check is not None:
                cancel_check()
        return original(*args, cancel_check=cancel_check, **kwargs)

    monkeypatch.setattr(boundary._service, "import_voice_model", slow_import)  # noqa: SLF001
    operation_id = "12345678-abcd-4000-8000-123456789abc"
    result: dict[str, object] = {}

    def run_import() -> None:
        result.update(
            boundary.handle(
                _request(
                    "studio.asset.import",
                    {
                        "workspaceId": created["workspaceId"],
                        "kind": "gptModel",
                        "path": str(source),
                        "operationId": operation_id,
                    },
                )
            )
        )

    worker = threading.Thread(target=run_import)
    worker.start()
    assert entered.wait(1)
    cancelled = boundary.handle(
        _request("studio.operation.cancel", {"operationId": operation_id})
    )
    release.set()
    worker.join(1)

    assert cancelled["payload"]["state"] == "cancel_requested"
    assert result["error"]["code"] == "STUDIO_OPERATION_CANCELLED"
    terminal = boundary.handle(
        _request("studio.operation.cancel", {"operationId": operation_id})
    )
    assert terminal["payload"] == {
        "schemaVersion": 1,
        "cancelled": False,
        "state": "not_found",
    }
