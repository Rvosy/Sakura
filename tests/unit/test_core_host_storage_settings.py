from __future__ import annotations

from pathlib import Path

from app.core_host.storage_settings import StorageSettingsBoundary


GENERATION = "generation-storage-settings"
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


def test_default_custom_missing_and_reset_states(tmp_path: Path) -> None:
    user_root = tmp_path / "user"
    boundary = StorageSettingsBoundary(GENERATION, CREDENTIAL, user_root)

    default = boundary.handle(_request("storage.settings.get", {}))["payload"]
    assert default["ttsRoot"] == str(user_root.resolve() / "tts")
    assert default["ttsRootSource"] == "default"
    assert default["ttsRootAvailable"] is True

    custom = tmp_path / "external"
    custom.mkdir()
    chosen = boundary.handle(
        _request("storage.settings.choose_tts_root", {"path": str(custom)})
    )["payload"]
    assert chosen["ttsRoot"] == str(custom.resolve())
    assert chosen["ttsRootSource"] == "custom"

    custom.rmdir()
    missing = boundary.handle(_request("storage.settings.get", {}))["payload"]
    assert missing["ttsRootAvailable"] is False
    assert missing["reasonCode"] == "TTS_ROOT_MISSING"
    assert (user_root / "tts").is_dir()

    reset = boundary.handle(_request("storage.settings.reset_tts_root", {}))["payload"]
    assert reset["ttsRootSource"] == "default"
    assert reset["ttsRootAvailable"] is True


def test_custom_root_must_exist(tmp_path: Path) -> None:
    boundary = StorageSettingsBoundary(GENERATION, CREDENTIAL, tmp_path / "user")
    result = boundary.handle(
        _request(
            "storage.settings.choose_tts_root",
            {"path": str(tmp_path / "missing")},
        )
    )
    assert result["ok"] is False
    assert result["error"]["code"] == "TTS_STORAGE_UNAVAILABLE"
