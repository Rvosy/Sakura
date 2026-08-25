from __future__ import annotations

from pathlib import Path

import yaml

from app.core_host.screen_awareness_settings import ScreenAwarenessSettingsBoundary


GENERATION_ID = "00000000-0000-4000-8000-000000004007"
GENERATION_CREDENTIAL = "a" * 32


def _request(request_id: str, name: str, payload: dict[str, object]) -> dict[str, object]:
    return {
        "id": request_id,
        "name": name,
        "generationId": GENERATION_ID,
        "generationCredential": GENERATION_CREDENTIAL,
        "protocolMajor": 2,
        "protocolMinor": 2,
        "payload": payload,
    }


def test_screen_awareness_defaults_and_legacy_enabled_fields_are_merged(tmp_path: Path) -> None:
    boundary = ScreenAwarenessSettingsBoundary(
        GENERATION_ID, GENERATION_CREDENTIAL, tmp_path
    )
    default = boundary.handle(_request("get-default", "screen_awareness.settings.get", {}))
    assert default["payload"]["settings"] == {
        "enabled": True,
        "checkIntervalMinutes": 20,
        "cooldownMinutes": 10,
        "batchLimit": 6,
        "resolution": "fullscreen",
    }

    path = tmp_path / "data/config/system_config.yaml"
    path.parent.mkdir(parents=True)
    path.write_text(
        "screen_awareness:\n  enabled: true\n  screen_context_enabled: false\n",
        encoding="utf-8",
    )
    merged = boundary.handle(_request("get-merged", "screen_awareness.settings.get", {}))
    assert merged["payload"]["settings"]["enabled"] is False


def test_screen_awareness_save_is_atomic_compatible_and_preserves_unrelated_yaml(
    tmp_path: Path,
) -> None:
    path = tmp_path / "data/config/system_config.yaml"
    path.parent.mkdir(parents=True)
    path.write_text(
        "config_version: 4\npreserve_top: yes\nscreen_awareness:\n  preserve_nested: yes\n",
        encoding="utf-8",
    )
    boundary = ScreenAwarenessSettingsBoundary(
        GENERATION_ID, GENERATION_CREDENTIAL, tmp_path
    )
    result = boundary.handle(
        _request(
            "save",
            "screen_awareness.settings.save",
            {
                "settings": {
                    "enabled": False,
                    "checkIntervalMinutes": 25,
                    "cooldownMinutes": 8,
                    "batchLimit": 4,
                    "resolution": "720p",
                }
            },
        )
    )
    assert result["ok"] is True
    assert set(result["payload"]) == {"schemaVersion", "settings"}
    assert result["payload"]["settings"] == {
        "enabled": False,
        "checkIntervalMinutes": 25,
        "cooldownMinutes": 8,
        "batchLimit": 4,
        "resolution": "720p",
    }
    saved = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert saved["preserve_top"] is True
    assert saved["screen_awareness"]["preserve_nested"] is True
    assert saved["screen_awareness"]["enabled"] is False
    assert saved["screen_awareness"]["screen_context_enabled"] is False
    assert saved["screen_awareness"]["check_interval_minutes"] == 25


def test_screen_awareness_save_rejects_invalid_or_extra_fields(tmp_path: Path) -> None:
    boundary = ScreenAwarenessSettingsBoundary(
        GENERATION_ID, GENERATION_CREDENTIAL, tmp_path
    )
    settings = {
        "enabled": True,
        "checkIntervalMinutes": 0,
        "cooldownMinutes": 10,
        "batchLimit": 6,
        "resolution": "fullscreen",
    }
    invalid = boundary.handle(
        _request("invalid", "screen_awareness.settings.save", {"settings": settings})
    )
    assert invalid["error"]["code"] == "FIELD_INVALID"
    settings["checkIntervalMinutes"] = 20
    settings["privatePath"] = "forbidden"
    extra = boundary.handle(
        _request("extra", "screen_awareness.settings.save", {"settings": settings})
    )
    assert extra["error"]["code"] == "INVALID_REQUEST"
