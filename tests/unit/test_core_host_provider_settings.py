from types import SimpleNamespace

import pytest

from app.config.model_references import EMPTY_REFERENCE, ModelReferenceRepository
from app.core_host.provider_settings import ProviderSettingsBoundary

GENERATION = "generation-provider-settings"
CREDENTIAL = "0123456789abcdef0123456789abcdef"
REF = {"serviceKey": "vendor.models", "profileId": "fixture", "modelId": "model"}
CATALOG = [{"serviceKey": REF["serviceKey"], "pluginId": "vendor.plugin", "label": "Vendor", "profiles": [{"profileId": "fixture", "label": "Fixture", "models": [{"modelId": "model", "label": "model"}]}]}]


def _request(name, payload=None, identity="request"):
    return {"protocolMajor": 2, "protocolMinor": 2, "kind": "request", "generationId": GENERATION,
            "generationCredential": CREDENTIAL, "id": identity, "name": "settings.provider_model." + name,
            "payload": {} if payload is None else payload, "deadlineMs": 3000, "priority": "interactive"}


def _boundary(root, *, application=None, apply=None):
    ModelReferenceRepository(root).save({"chat": REF, "vision_chat": EMPTY_REFERENCE})
    if application is None:
        application = SimpleNamespace(model_catalog=lambda: CATALOG, model_slots=lambda: [])
    boundary = ProviderSettingsBoundary(GENERATION, CREDENTIAL, root, plugin_application_provider=lambda: application, runtime_apply=apply)
    boundary.enable()
    return boundary


def _draft(boundary):
    snapshot = boundary.handle(_request("get"))["payload"]
    return {"model_slots": {slot["identity"]: slot["selection"] for slot in snapshot["model_slots"]}}


def test_model_settings_exposes_only_references_and_public_catalog(tmp_path):
    boundary = _boundary(tmp_path)
    payload = boundary.handle(_request("get"))["payload"]
    assert payload["schema_version"] == 2
    assert payload["setup_complete"]
    assert payload["model_slots"][0]["selection"] == REF
    assert payload["providers"] == CATALOG
    assert "settings" not in payload
    assert "api_key" not in repr(payload)


def test_save_before_configuration_handoff_cannot_erase_legacy_selection(tmp_path):
    config = tmp_path / "config"
    config.mkdir()
    legacy = config / "api.yaml"
    legacy.write_text("model_slots: {chat: {profile_id: old, model: old-model}}", encoding="utf-8")
    before = legacy.read_bytes()
    boundary = ProviderSettingsBoundary(GENERATION, CREDENTIAL, tmp_path)
    boundary.enable()
    result = boundary.handle(_request("save", {"draft": _draft(boundary)}))
    assert result["error"]["code"] == "MODEL_CONFIGURATION_NOT_READY"
    assert not ModelReferenceRepository(tmp_path).path.exists()
    assert legacy.read_bytes() == before


def test_unavailable_reference_is_visible_and_preserved_without_provider(tmp_path):
    application = SimpleNamespace(model_catalog=lambda: [], model_slots=lambda: [])
    boundary = _boundary(tmp_path, application=application)
    payload = boundary.handle(_request("get"))["payload"]
    assert not payload["setup_complete"]
    assert payload["model_slots"][0]["reasonCode"] == "MODEL_REFERENCE_UNAVAILABLE"
    assert boundary.handle(_request("save", {"draft": _draft(boundary)}))["ok"]
    assert ModelReferenceRepository(tmp_path).active()["chat"] == REF


def test_save_failure_distinguishes_persisted_selection_from_runtime_apply(tmp_path):
    def fail():
        raise OSError("PRIVATE_INTERNAL_DETAIL")
    boundary = _boundary(tmp_path, apply=fail)
    draft = _draft(boundary)
    draft["model_slots"]["core:vision_chat"] = REF
    result = boundary.handle(_request("save", {"draft": draft}))
    assert result["error"]["code"] == "CONFIG_APPLY_FAILED"
    assert "PRIVATE_INTERNAL_DETAIL" not in result["error"]["message"]
    assert ModelReferenceRepository(tmp_path).load()["vision_chat"] == REF


@pytest.mark.parametrize("change", ["stale_slots", "partial_reference", "credentials", "unavailable"])
def test_invalid_drafts_never_change_saved_configuration(tmp_path, change):
    boundary = _boundary(tmp_path)
    draft = _draft(boundary)
    path = ModelReferenceRepository(tmp_path).path
    before = path.read_bytes()
    if change == "stale_slots":
        draft["model_slots"]["plugin:gone:slot"] = REF
    elif change == "partial_reference":
        draft["model_slots"]["core:chat"] = {"profileId": "fixture"}
    elif change == "unavailable":
        draft["model_slots"]["core:chat"] = {**REF, "serviceKey": "gone"}
    else:
        draft["providers"] = [{"apiKey": "PRIVATE"}]
    assert not boundary.handle(_request("save", {"draft": draft}))["ok"]
    assert path.read_bytes() == before


def test_plugin_slot_failure_reports_partial_save_and_skips_runtime_apply(tmp_path):
    slot = {"identity": "plugin:memory:curation", "ownerType": "plugin", "ownerId": "memory", "slotId": "curation", "selection": EMPTY_REFERENCE}
    def save(*_args):
        raise ValueError("failed")
    calls = []
    application = SimpleNamespace(model_catalog=lambda: CATALOG, model_slots=lambda: [slot], model_slot_save=save)
    boundary = _boundary(tmp_path, application=application, apply=lambda: calls.append("apply"))
    draft = _draft(boundary)
    draft["model_slots"][slot["identity"]] = REF
    result = boundary.handle(_request("save", {"draft": draft}))["payload"]
    assert result["save_state"] == "partial"
    assert result["saved_slots"] == ["core:chat", "core:vision_chat"]
    assert result["failed_slot"]["identity"] == slot["identity"]
    assert not calls


def test_stale_generation_and_closed_boundary_cannot_mutate_settings(tmp_path):
    boundary = _boundary(tmp_path)
    request = _request("get")
    request["generationCredential"] = "other"
    with pytest.raises(RuntimeError, match="GENERATION_IDENTITY_MISMATCH"):
        boundary.handle(request)
    boundary.close()
    assert boundary.handle(_request("get"))["error"]["code"] == "CAPABILITY_NEGOTIATION_FAILED"


@pytest.mark.parametrize("broken", ["legacy", "references"])
def test_corrupt_model_configuration_keeps_plugin_management_and_explicit_repair_available(tmp_path, broken):
    from app.core_host.plugin_runtime_application import PluginRuntimeApplication
    from app.storage.runtime_roots import RuntimeRoots
    config = tmp_path / "config"
    config.mkdir()
    legacy = config / "api.yaml"
    legacy.write_text("api_profiles: [" if broken == "legacy" else "api_profiles: []", encoding="utf-8")
    before = legacy.read_bytes()
    if broken == "references":
        (config / "model_slots.json").write_text("{broken", encoding="utf-8")
    application = PluginRuntimeApplication(RuntimeRoots(tmp_path, tmp_path), "fixture", SimpleNamespace(), specs=[])
    try:
        assert application.active_models() == {"chat": None, "vision_chat": None}
        assert application.model_configuration_issue() == "CONFIG_DATA_INVALID"
        # The real manager/HostService registration exists even when models fail.
        assert application.model_catalog() == []
        application.model_catalog = lambda: CATALOG
        boundary = ProviderSettingsBoundary(GENERATION, CREDENTIAL, tmp_path, plugin_application_provider=lambda: application)
        boundary.enable()
        result = boundary.handle(_request("get"))
        assert result["ok"]
        assert result["payload"]["configuration_issue"]["code"] == "CONFIG_DATA_INVALID"
        draft = _draft(boundary)
        denied = boundary.handle(_request("save", {"draft": draft}))
        assert denied["error"]["code"] == "MODEL_CONFIGURATION_NOT_READY"
        assert legacy.read_bytes() == before
        draft["model_slots"]["core:chat"] = REF
        assert boundary.handle(_request("save", {"draft": draft}))["ok"]
        assert application.active_models()["chat"] == REF
        assert application.model_configuration_issue() is None
        assert legacy.read_bytes() == before
    finally:
        application.close()
