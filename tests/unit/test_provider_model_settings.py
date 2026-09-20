"""The old Core credential repository is replaced by a one-time ownership handoff."""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from app.config import model_references as module
from app.config.model_references import EMPTY_REFERENCE, OPENAI_SERVICE, ModelReferenceRepository, migrate_legacy_model_configuration, model_reference


REF = {"serviceKey": OPENAI_SERVICE, "profileId": "fixture", "modelId": "chat-model"}
SECRET = "PRIVATE_HANDOFF_KEY"


def _legacy(root):
    path = root / "config" / "api.yaml"
    path.parent.mkdir(parents=True)
    path.write_text(yaml.safe_dump({
        "llm": {"temperature": 0.3, "top_p": 0.8, "max_tokens": 1234, "timeout_seconds": 45},
        "api_profiles": [{"id": "fixture", "alias": "Fixture", "base_url": "https://fixture.invalid/v1", "api_key": SECRET,
                          "models": [{"name": "chat-model"}, {"name": "vision-model"}]}],
        "model_slots": {"chat": {"profile_id": "fixture", "model": "chat-model", "context_window_tokens": 128000},
                        "vision_chat": {"profile_id": "fixture", "model": "vision-model"}},
        "unrelated": {"keep": True},
    }), encoding="utf-8")
    return path


def _provider(root):
    return root / "data" / "plugins" / OPENAI_SERVICE / "config.json"


def _assistant(root):
    return root / "data" / "plugins" / "sakura.assistant.default" / "config.json"


def test_handoff_preserves_source_and_assigns_each_setting_to_its_owner(tmp_path):
    legacy = _legacy(tmp_path)
    before = legacy.read_bytes()
    migrate_legacy_model_configuration(tmp_path)
    assert legacy.read_bytes() == before
    profile, = json.loads(_provider(tmp_path).read_text(encoding="utf-8"))["profiles"]
    assert profile["api_key"] == SECRET
    assert profile["timeout_seconds"] == 45
    assert profile["models"][0]["contextWindowTokens"] == 128000
    assert json.loads(_assistant(tmp_path).read_text(encoding="utf-8"))["generation"] == {"temperature": 0.3, "top_p": 0.8, "max_tokens": 1234}
    repository = ModelReferenceRepository(tmp_path)
    assert repository.active()["chat"] == REF
    assert repository.active()["vision_chat"]["modelId"] == "vision-model"
    assert SECRET not in repository.path.read_text(encoding="utf-8")
    legacy.write_text("invalid: [", encoding="utf-8")
    migrate_legacy_model_configuration(tmp_path)
    assert repository.active()["chat"] == REF


def test_interrupted_handoff_resumes_without_overwriting_saved_destinations(tmp_path, monkeypatch):
    _legacy(tmp_path)
    original_write = module._write
    def interrupt(path, value):
        if path == _assistant(tmp_path):
            raise OSError("interrupted")
        original_write(path, value)
    monkeypatch.setattr(module, "_write", interrupt)
    with pytest.raises(OSError):
        migrate_legacy_model_configuration(tmp_path)
    assert not ModelReferenceRepository(tmp_path).path.exists()
    saved = json.loads(_provider(tmp_path).read_text(encoding="utf-8"))
    saved["profiles"][0]["api_key"] = "USER_UPDATED_KEY"
    original_write(_provider(tmp_path), saved)
    monkeypatch.setattr(module, "_write", original_write)
    migrate_legacy_model_configuration(tmp_path)
    assert json.loads(_provider(tmp_path).read_text(encoding="utf-8")) == saved
    assert ModelReferenceRepository(tmp_path).active()["chat"] == REF


def test_existing_plugin_fields_win_and_unrelated_data_survives(tmp_path):
    _legacy(tmp_path)
    module._write(_provider(tmp_path), {"profiles": [], "custom": "keep"})
    module._write(_assistant(tmp_path), {"generation": {"temperature": 1}, "custom": "keep"})
    migrate_legacy_model_configuration(tmp_path)
    assert json.loads(_provider(tmp_path).read_text(encoding="utf-8")) == {"profiles": [], "custom": "keep"}
    assert json.loads(_assistant(tmp_path).read_text(encoding="utf-8"))["generation"] == {"temperature": 1}
    assert ModelReferenceRepository(tmp_path).load()["chat"] == REF


def test_reference_identity_keeps_provider_key_and_optional_vision_inherits_chat(tmp_path):
    repository = ModelReferenceRepository(tmp_path)
    other = {**REF, "serviceKey": "vendor.other"}
    repository.save({"chat": other, "vision_chat": EMPTY_REFERENCE})
    assert repository.active() == {"chat": other, "vision_chat": other}
    assert repository.load()["vision_chat"] == EMPTY_REFERENCE


@pytest.mark.parametrize("value", [{"profileId": "fixture", "modelId": "x"}, {**REF, "apiKey": SECRET}, {**REF, "modelId": 3}, {**REF, "serviceKey": ""}])
def test_invalid_or_credential_bearing_references_are_rejected(value):
    with pytest.raises(ValueError, match="MODEL_SLOT_SELECTION_INVALID"):
        model_reference(value)


def test_legacy_llm_only_configuration_is_retained(tmp_path):
    legacy = _legacy(tmp_path)
    legacy.write_text(yaml.safe_dump({"llm": {"base_url": "https://fixture.invalid/v1", "api_key": SECRET, "model": "old-model"}}), encoding="utf-8")
    migrate_legacy_model_configuration(tmp_path)
    assert ModelReferenceRepository(tmp_path).active()["chat"]["modelId"] == "old-model"
    assert json.loads(_provider(tmp_path).read_text(encoding="utf-8"))["profiles"][0]["api_key"] == SECRET


@pytest.mark.parametrize("alias", ["", None, "   "])
def test_handoff_normalizes_legacy_profiles_before_provider_startup(tmp_path, alias):
    from plugins.builtin.sakura_model_openai_compatible.profiles import ProviderProfiles

    legacy = _legacy(tmp_path)
    raw = yaml.safe_load(legacy.read_text(encoding="utf-8"))
    raw["api_profiles"][0].update(id=" fixture ", alias=alias,
        models=[{"name": " chat-model "}, {"name": "chat-model"}, {"name": "vision-model"}])
    raw["model_slots"]["chat"].update(profile_id=" fixture ", model=" chat-model ")
    raw["llm"]["timeout_seconds"] = None
    legacy.write_text(yaml.safe_dump(raw), encoding="utf-8")
    before = legacy.read_bytes()

    migrate_legacy_model_configuration(tmp_path)

    provider = ProviderProfiles(SimpleNamespace(config=SimpleNamespace(get=lambda: module._read(_provider(tmp_path)))))
    profile, = provider.catalog()
    assert profile["label"] == "fixture"
    assert [model["modelId"] for model in profile["models"]] == ["chat-model", "vision-model"]
    assert provider.resolve("fixture", "chat-model")["timeout_seconds"] == 60
    assert provider.describe("fixture", "chat-model")["contextWindowTokens"] == 128000
    assert ModelReferenceRepository(tmp_path).active()["chat"] == REF
    assert legacy.read_bytes() == before


@pytest.mark.parametrize("change", [{"id": "invalid/id"}, {"base_url": "invalid"}, {"models": [{"name": "bad\x00model"}]}])
def test_invalid_legacy_profile_does_not_commit_handoff_and_can_be_corrected(tmp_path, change):
    legacy = _legacy(tmp_path)
    original = legacy.read_text(encoding="utf-8")
    raw = yaml.safe_load(original)
    raw["api_profiles"][0].update(change)
    legacy.write_text(yaml.safe_dump(raw), encoding="utf-8")
    with pytest.raises(ValueError, match="CONFIG_DATA_INVALID"):
        migrate_legacy_model_configuration(tmp_path)
    assert not ModelReferenceRepository(tmp_path).path.exists()
    assert not _provider(tmp_path).exists()
    legacy.write_text(original, encoding="utf-8")
    migrate_legacy_model_configuration(tmp_path)
    assert ModelReferenceRepository(tmp_path).active()["chat"] == REF


@pytest.mark.parametrize("generation,expected", [
    ({"temperature": 3, "top_p": False, "max_tokens": -1}, {}),
    ({"temperature": 0, "top_p": 0, "max_tokens": 1}, {"temperature": 0, "top_p": 0, "max_tokens": 1}),
])
def test_handoff_keeps_legacy_generation_defaults_and_explicit_zero(tmp_path, generation, expected):
    legacy = _legacy(tmp_path)
    raw = yaml.safe_load(legacy.read_text(encoding="utf-8"))
    raw["llm"].update(generation)
    legacy.write_text(yaml.safe_dump(raw), encoding="utf-8")
    migrate_legacy_model_configuration(tmp_path)
    assert module._read(_assistant(tmp_path))["generation"] == expected


def test_legacy_import_validation_discards_partial_handoff_before_optional_config_skip(tmp_path, monkeypatch):
    from app.legacy_import.importer import _validate_current_settings
    from app.legacy_import.errors import LegacyImportError
    legacy = _legacy(tmp_path)
    original = legacy.read_bytes()
    module._write(_provider(tmp_path), {"custom": "keep"})
    prior_provider = _provider(tmp_path).read_bytes()
    write = module._write
    def fail_assistant(path, value):
        if path == _assistant(tmp_path):
            raise OSError("fixture write failed")
        write(path, value)
    monkeypatch.setattr(module, "_write", fail_assistant)
    with pytest.raises(LegacyImportError) as error:
        _validate_current_settings(tmp_path)
    assert error.value.code == "LEGACY_SETTINGS_VALIDATION_FAILED"
    assert _provider(tmp_path).read_bytes() == prior_provider
    assert not _assistant(tmp_path).exists()
    assert not ModelReferenceRepository(tmp_path).path.exists()
    assert legacy.read_bytes() == original


def test_earlier_plugin_chat_budget_is_adopted_once_without_overwriting_clear(tmp_path):
    ModelReferenceRepository(tmp_path).save({"chat": REF, "vision_chat": EMPTY_REFERENCE})
    module._write(_provider(tmp_path), {"profiles": [{"profileId": "fixture", "models": [{"modelId": "chat-model", "contextWindowTokens": 96000}]}]})
    migrate_legacy_model_configuration(tmp_path)
    assert module._read(_assistant(tmp_path))["contextWindowTokens"] == 96000
    module._write(_assistant(tmp_path), {"contextWindowTokens": None, "custom": "keep"})
    migrate_legacy_model_configuration(tmp_path)
    assert module._read(_assistant(tmp_path)) == {"contextWindowTokens": None, "custom": "keep"}


@pytest.mark.parametrize("profiles", [None, [None], [{"profileId": "fixture", "models": None}], [{"profileId": "fixture", "models": [None]}]])
def test_invalid_prior_model_metadata_does_not_commit_budget_handoff(tmp_path, profiles):
    ModelReferenceRepository(tmp_path).save({"chat": REF, "vision_chat": EMPTY_REFERENCE})
    module._write(_provider(tmp_path), {"profiles": profiles})
    with pytest.raises(ValueError, match="CONFIG_DATA_INVALID"):
        migrate_legacy_model_configuration(tmp_path)
    assert not _assistant(tmp_path).exists()
