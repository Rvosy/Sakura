from __future__ import annotations

import json
from pathlib import Path

from app.config.character_loader import _load_profile
from app.config.settings_service import AppSettingsService, BackchannelSettings
from app.config.yaml_config import load_yaml_mapping


# --- BackchannelSettings 加载/保存 ------------------------------------------

def test_defaults_when_section_absent(tmp_path: Path) -> None:
    service = AppSettingsService(tmp_path)
    settings = service.load_backchannel_settings()
    assert settings.enabled is False
    assert settings.mode == "rules"
    assert settings.delay_ms == 600
    assert settings.probability == 1.0
    assert settings.tts_enabled is False
    assert settings.active is False


def test_save_load_round_trip(tmp_path: Path) -> None:
    service = AppSettingsService(tmp_path)
    service.save_backchannel_settings(
        BackchannelSettings(
            enabled=True,
            mode="rules",
            delay_ms=900,
            probability=0.6,
            tts_enabled=True,
        )
    )
    loaded = service.load_backchannel_settings()
    assert loaded.enabled is True
    assert loaded.delay_ms == 900
    assert loaded.probability == 0.6
    assert loaded.tts_enabled is True
    assert loaded.active is True


def test_save_preserves_other_sections(tmp_path: Path) -> None:
    service = AppSettingsService(tmp_path)
    service.save_system_values("ui", {"subtitle_language": "ja"})
    service.save_backchannel_settings(BackchannelSettings(enabled=True))
    system = load_yaml_mapping(service.system_config_path)
    assert system["ui"]["subtitle_language"] == "ja"
    assert system["backchannel"]["enabled"] is True


def test_normalized_clamps_values() -> None:
    settings = BackchannelSettings(
        enabled=True, mode="hybrid", delay_ms=999999, probability=3.0
    ).normalized()
    assert settings.mode == "hybrid"
    assert settings.delay_ms == 5000
    assert settings.probability == 1.0
    low = BackchannelSettings(mode="missing", delay_ms=1, probability=-0.5).normalized()
    assert low.mode == "rules"
    assert low.delay_ms == 100
    assert low.probability == 0.0


def test_mode_off_is_inactive() -> None:
    assert BackchannelSettings(enabled=True, mode="off").active is False


# --- character.json 的 backchannel 字段 --------------------------------------

def _write_character_package(tmp_path: Path, *, backchannel: str | None) -> Path:
    package = tmp_path / "sakura"
    package.mkdir()
    (package / "card.md").write_text("card", encoding="utf-8")
    (package / "default.png").write_bytes(b"png")
    data = {
        "id": "sakura",
        "display_name": "夜乃桜",
        "card": "card.md",
        "portrait": {"default": "default.png"},
    }
    if backchannel is not None:
        data["backchannel"] = backchannel
    (package / "character.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return package / "character.json"


def test_profile_resolves_backchannel_path(tmp_path: Path) -> None:
    manifest_path = _write_character_package(tmp_path, backchannel="backchannels/manifest.json")
    profile = _load_profile(manifest_path)
    assert profile.backchannel_manifest_path == (
        tmp_path / "sakura" / "backchannels" / "manifest.json"
    )


def test_profile_backchannel_missing_field_is_opt_out(tmp_path: Path) -> None:
    manifest_path = _write_character_package(tmp_path, backchannel=None)
    profile = _load_profile(manifest_path)
    assert profile.backchannel_manifest_path is None


def test_profile_backchannel_path_not_required_to_exist(tmp_path: Path) -> None:
    # 路径解析不校验存在:文件缺失由 manifest 加载方降级,不应炸掉角色包。
    manifest_path = _write_character_package(tmp_path, backchannel="missing/nowhere.json")
    profile = _load_profile(manifest_path)
    assert profile.backchannel_manifest_path is not None
    assert not profile.backchannel_manifest_path.exists()
