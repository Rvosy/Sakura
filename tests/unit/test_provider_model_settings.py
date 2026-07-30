from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from app.config import provider_model_settings as module
from app.config.core_config_reader import CoreConfigReader
from app.config.provider_model_settings import (
    ProviderModelSettingsError,
    ProviderModelSettingsRepository,
)
from app.config.settings_service import AppSettingsService
from app.config.models import ApiConfigProfile, ModelSelectionSettings, ModelSlotSelection
from app.llm.api_client import ApiSettings


SECRET = "KEEP_THIS_SECRET_BYTE_FOR_BYTE"


def _root(tmp_path: Path, *, version: int = 4) -> Path:
    config = tmp_path / "data" / "config"
    config.mkdir(parents=True)
    (config / "system_config.yaml").write_text(
        yaml.safe_dump({"config_version": version}, sort_keys=False),
        encoding="utf-8",
    )
    return tmp_path


def _api(root: Path) -> Path:
    return root / "data" / "config" / "api.yaml"


def _write_current(root: Path) -> None:
    _api(root).write_text(
        yaml.safe_dump(
            {
                "llm": {
                    "base_url": "https://fixture.invalid/v1",
                    "api_key": SECRET,
                    "model": "chat-model",
                    "timeout_seconds": 60,
                    "legacy_unknown": "keep-llm",
                },
                "api_profiles": [
                    {
                        "id": "fixture",
                        "alias": "Fixture",
                        "base_url": "https://fixture.invalid/v1",
                        "api_key": SECRET,
                        "provider_unknown": "keep-provider",
                        "models": [
                            {"name": "chat-model", "model_unknown": "keep-model"},
                            {"name": "vision-model"},
                        ],
                    }
                ],
                "model_slots": {
                    "chat": {
                        "profile_id": "fixture",
                        "model": "chat-model",
                        "slot_unknown": "keep-chat-slot",
                    },
                    "vision_chat": {"profile_id": "fixture", "model": "vision-model"},
                    "memory_curation": {"profile_id": "fixture", "model": "chat-model"},
                    "future_slot": {"opaque": ["preserve-slot"]},
                },
                "tts": {"enabled": False, "private_unknown": "keep-tts"},
                "top_unknown": {"nested": "keep-top"},
            },
            allow_unicode=True,
            sort_keys=False,
        ),
        encoding="utf-8",
    )


def _draft(*, action: str = "keep", value: str = "") -> dict[str, object]:
    return {
        "providers": [
            {
                "id": "fixture",
                "alias": "Fixture edited",
                "base_url": "https://fixture.invalid/v1",
                "models": ["chat-model", "vision-model"],
                "credential": {"action": action, "value": value},
            }
        ],
        "model_slots": {
            "chat": {"profile_id": "fixture", "model": "chat-model"},
            "vision_chat": {"profile_id": "fixture", "model": "vision-model"},
        },
        "settings": {
            "timeout_seconds": 30,
            "temperature": 0.7,
            "top_p": None,
            "max_tokens": 2048,
        },
    }


def test_snapshot_is_side_effect_free_and_never_returns_saved_secret(tmp_path: Path) -> None:
    root = _root(tmp_path)
    _write_current(root)
    before = _api(root).read_bytes()

    snapshot = ProviderModelSettingsRepository(root).snapshot()

    assert snapshot["providers"] == [
        {
            "id": "fixture",
            "alias": "Fixture",
            "base_url": "https://fixture.invalid/v1",
            "configured": True,
            "models": ["chat-model", "vision-model"],
        }
    ]
    assert SECRET not in repr(snapshot)
    assert _api(root).read_bytes() == before


def test_single_domain_save_preserves_unknowns_non_target_slot_and_kept_secret(tmp_path: Path) -> None:
    root = _root(tmp_path)
    _write_current(root)

    result = ProviderModelSettingsRepository(root).save(_draft())
    saved = yaml.safe_load(_api(root).read_text(encoding="utf-8"))

    assert result == {
        "saved": True,
        "change_plan": "core_restart_required",
        "setup_complete": True,
    }
    assert saved["api_profiles"][0]["api_key"] == SECRET
    assert saved["api_profiles"][0]["provider_unknown"] == "keep-provider"
    assert saved["api_profiles"][0]["models"][0]["model_unknown"] == "keep-model"
    assert saved["model_slots"]["memory_curation"]["model"] == "chat-model"
    assert saved["model_slots"]["chat"]["slot_unknown"] == "keep-chat-slot"
    assert saved["model_slots"]["future_slot"] == {"opaque": ["preserve-slot"]}
    assert saved["tts"]["private_unknown"] == "keep-tts"
    assert saved["top_unknown"]["nested"] == "keep-top"
    assert saved["llm"]["legacy_unknown"] == "keep-llm"


def test_unused_provider_draft_keeps_selected_chat_and_character_bootable(tmp_path: Path) -> None:
    root = _root(tmp_path)
    _write_current(root)
    (root / "data" / "config" / "characters.yaml").write_text(
        "current_character_id: N.A.V.I.\n",
        encoding="utf-8",
    )
    draft = _draft()
    draft["providers"].append(  # type: ignore[union-attr]
        {
            "id": "draft-provider",
            "alias": "Draft Provider",
            "base_url": "https://draft.invalid/v1",
            "models": [],
            "credential": {"action": "clear", "value": ""},
        }
    )

    ProviderModelSettingsRepository(root).save(draft)
    result = CoreConfigReader().read(root)

    assert result.config_problem is None
    assert result.current_character_id == "N.A.V.I."
    assert result.provider_selection is not None
    assert result.provider_selection.api_settings.model == "chat-model"


@pytest.mark.parametrize(
    ("action", "value", "expected"),
    [("replace", "NEW_SECRET", "NEW_SECRET"), ("clear", "", "")],
)
def test_credential_replace_and_explicit_clear(
    tmp_path: Path,
    action: str,
    value: str,
    expected: str,
) -> None:
    root = _root(tmp_path)
    _write_current(root)
    ProviderModelSettingsRepository(root).save(_draft(action=action, value=value))
    saved = yaml.safe_load(_api(root).read_text(encoding="utf-8"))
    assert saved["api_profiles"][0]["api_key"] == expected
    assert saved["llm"]["api_key"] == expected


def test_invalid_domain_or_atomic_failure_never_changes_target(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _root(tmp_path)
    _write_current(root)
    before = _api(root).read_bytes()
    invalid = _draft()
    invalid["model_slots"] = {
        "chat": {"profile_id": "missing", "model": "chat-model"},
        "vision_chat": {"profile_id": "", "model": ""},
    }
    with pytest.raises(ProviderModelSettingsError, match="模型槽"):
        ProviderModelSettingsRepository(root).save(invalid)
    assert _api(root).read_bytes() == before

    def fail_write(*_args: object, **_kwargs: object) -> None:
        raise OSError("fixture replace failure")

    monkeypatch.setattr(module, "atomic_write_text", fail_write)
    with pytest.raises(ProviderModelSettingsError) as caught:
        ProviderModelSettingsRepository(root).save(_draft())
    assert caught.value.code == "CONFIG_SAVE_FAILED"
    assert _api(root).read_bytes() == before


@pytest.mark.parametrize(
    "base_url",
    [
        "ftp://fixture.invalid/v1",
        "https://user:secret@fixture.invalid/v1",
        "https://fixture.invalid/v1?token=secret",
        "https://bad host/v1",
        "https://-bad.invalid/v1",
    ],
)
def test_invalid_provider_urls_fail_before_write(tmp_path: Path, base_url: str) -> None:
    root = _root(tmp_path)
    _write_current(root)
    before = _api(root).read_bytes()
    draft = _draft()
    draft["providers"][0]["base_url"] = base_url  # type: ignore[index]
    with pytest.raises(ProviderModelSettingsError) as caught:
        ProviderModelSettingsRepository(root).save(draft)
    assert caught.value.code == "BASE_URL_INVALID"
    assert _api(root).read_bytes() == before


@pytest.mark.parametrize("version", [3, 5])
def test_old_and_future_schema_are_read_only(tmp_path: Path, version: int) -> None:
    incompatible = _root(tmp_path / str(version), version=version)
    _write_current(incompatible)
    before = _api(incompatible).read_bytes()
    with pytest.raises(ProviderModelSettingsError) as caught:
        ProviderModelSettingsRepository(incompatible).save(_draft())
    assert caught.value.code == "CONFIG_VERSION_UNSUPPORTED"
    assert _api(incompatible).read_bytes() == before


def test_corrupt_yaml_and_invalid_current_domain_are_read_only(tmp_path: Path) -> None:
    corrupt = _root(tmp_path / "corrupt")
    _api(corrupt).write_text("api_profiles: [", encoding="utf-8")
    before = _api(corrupt).read_bytes()
    with pytest.raises(ProviderModelSettingsError) as caught:
        ProviderModelSettingsRepository(corrupt).save(_draft())
    assert caught.value.code == "CONFIG_DATA_INVALID"
    assert _api(corrupt).read_bytes() == before

    invalid = _root(tmp_path / "invalid")
    _api(invalid).write_text("api_profiles: {}\nmodel_slots: []\n", encoding="utf-8")
    before = _api(invalid).read_bytes()
    with pytest.raises(ProviderModelSettingsError) as caught:
        ProviderModelSettingsRepository(invalid).save(_draft())
    assert caught.value.code == "CONFIG_DATA_INVALID"
    assert _api(invalid).read_bytes() == before


def test_delete_all_providers_is_a_valid_setup_required_state(tmp_path: Path) -> None:
    root = _root(tmp_path)
    _write_current(root)
    result = ProviderModelSettingsRepository(root).save(
        {
            "providers": [],
            "model_slots": {"chat": {}, "vision_chat": {}},
            "settings": {
                "timeout_seconds": 60,
                "temperature": None,
                "top_p": None,
                "max_tokens": None,
            },
        }
    )
    saved = yaml.safe_load(_api(root).read_text(encoding="utf-8"))
    assert result["setup_complete"] is False
    assert saved["api_profiles"] == []
    assert saved["model_slots"]["memory_curation"]["model"] == "chat-model"


def test_legacy_service_to_v2_and_back_remains_compatible(tmp_path: Path) -> None:
    root = _root(tmp_path)
    legacy = AppSettingsService(root)
    legacy.save_api_profiles([
        ApiConfigProfile(
            id="legacy",
            alias="Legacy",
            base_url="https://legacy.invalid/v1",
            api_key=SECRET,
            models=("legacy-chat", "legacy-vision"),
        )
    ])
    legacy.save_model_selection(ModelSelectionSettings(
        chat=ModelSlotSelection(profile_id="legacy", model="legacy-chat"),
        vision_chat=ModelSlotSelection(profile_id="legacy", model="legacy-vision"),
    ))
    legacy.save_api_settings(ApiSettings(
        base_url="https://legacy.invalid/v1",
        api_key=SECRET,
        model="legacy-chat",
        timeout_seconds=42,
    ))

    repository = ProviderModelSettingsRepository(root)
    snapshot = repository.snapshot()
    provider = snapshot["providers"][0]
    assert provider["configured"] is True
    assert SECRET not in repr(snapshot)
    repository.save({
        "providers": [{
            "id": "legacy",
            "alias": "Edited by v2",
            "base_url": "https://legacy.invalid/v1",
            "models": ["legacy-chat", "legacy-vision"],
            "credential": {"action": "keep", "value": ""},
        }],
        "model_slots": snapshot["model_slots"],
        "settings": snapshot["settings"],
    })

    assert legacy.load_api_profiles()[0].alias == "Edited by v2"
    assert legacy.load_api_profiles()[0].api_key == SECRET
    assert legacy.load_model_selection().chat == ModelSlotSelection(
        profile_id="legacy",
        model="legacy-chat",
    )
    assert legacy.load_api_settings().api_key == SECRET
