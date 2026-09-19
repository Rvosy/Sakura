from copy import deepcopy
from types import SimpleNamespace
import threading
import uuid

import pytest

from app.core_host.plugin_host_services import _SettingsHostService
from plugins.builtin.sakura_model_openai_compatible.profiles import ProfileError, ProviderProfiles, SERVICE_KEY

SECRET = "PROFILE_SECRET_MUST_NOT_ESCAPE"


class Config:
    def __init__(self):
        self.value = {"profiles": [{"profileId": "fixture", "label": "Fixture", "base_url": "https://fixture.invalid/v1", "api_key": SECRET,
                                   "timeout_seconds": 60, "models": [{"modelId": "model", "label": "Model", "contextWindowTokens": 128000}]}]}
    def get(self):
        return deepcopy(self.value)
    def update(self, patch):
        self.value.update(deepcopy(patch))
        return "applied"


class Context:
    def __init__(self):
        self.config = Config()
        self.callbacks = {}
        self.host = _SettingsHostService(lambda handle, _shape, *args: self.callbacks[handle](*args))
    def effect(self, _cleanup):
        pass
    def bind(self, key):
        assert key == SERVICE_KEY
        return SimpleNamespace(invoke=lambda method, *args, timeout_seconds: getattr(self.service, method)(*args))
    def handle(self, callback):
        if callback is None:
            return None
        handle = "cb_" + uuid.uuid4().hex
        self.callbacks[handle] = callback
        return handle
    def get(self, key):
        if key == "sakura.host.settings":
            def register(descriptor, *, load, save, actions):
                return self.host.call("register", [SERVICE_KEY, descriptor, {"load": self.handle(load), "save": self.handle(save), "actions": {key: self.handle(value) for key,value in actions.items()}}])
        elif key == "sakura.host.settings.surface-v0":
            def register(section, surface):
                return self.host.register_surface(SERVICE_KEY, section, surface)
        else:
            def register(section, descriptor, **callbacks):
                return self.host.register_collection(SERVICE_KEY, section, descriptor, {key: self.handle(callbacks.get(key)) for key in ("query", "create", "update", "delete")})
        return SimpleNamespace(register=register)


@pytest.fixture
def provider():
    context = Context()
    profiles = ProviderProfiles(context)
    profiles.register_settings()
    return profiles, context


def _query(context, collection="profiles"):
    return context.host.collection("query", SERVICE_KEY, "connections", collection, {"cursor": None, "limit": 25, "search": "", "filters": {}})


@pytest.mark.parametrize(("action", "value", "expected"), [("keep", "", SECRET), ("replace", "replacement", "replacement"), ("clear", "", "")])
def test_collection_credential_actions_are_private_and_effective_only_after_reload(provider, action, value, expected):
    profiles, context = provider
    item, = _query(context)["items"]
    assert SECRET not in repr(item)
    assert item["values"]["apiKey"] == ""
    result = context.host.collection("update", SERVICE_KEY, "connections", "profiles", {"itemId": item["itemId"], "values": {**{key: value for key, value in item["values"].items() if key != "configured"}, "credentialAction": action, "apiKey": value}})
    assert result["applicationState"] == "restart_required"
    assert result["values"]["apiKey"] == ""
    assert context.host.sections_for_plugin(SERVICE_KEY)[0]["reasonCode"] == "CONFIG_RELOAD_REQUIRED"
    assert profiles.resolve("fixture", "model")["api_key"] == SECRET
    assert ProviderProfiles(context).resolve("fixture", "model")["api_key"] == expected


def test_invalid_credentials_leave_saved_configuration_unchanged(provider):
    profiles, context = provider
    original = context.config.get()
    item = _query(context)["items"][0]
    with pytest.raises(ProfileError, match="配置无效"):
        profiles.update(item["itemId"], {**item["values"], "credentialAction": "keep", "apiKey": "accidental"})
    assert context.config.get() == original


def test_model_metadata_edit_preserves_active_catalog_and_survives_list_edit(provider):
    profiles, context = provider
    item = _query(context, "models")["items"][0]
    result = context.host.collection("update", SERVICE_KEY, "connections", "models", {"itemId": item["itemId"], "values": {**item["values"], "contextWindowTokens": 64000, "inputModalities": "image", "supportsTools": "yes"}})
    assert result["applicationState"] == "restart_required"
    assert profiles.describe("fixture", "model")["contextWindowTokens"] == 128000
    connection = _query(context)["items"][0]
    profiles.update(connection["itemId"], {**connection["values"], "models": "model\nnew-model"})
    reloaded = ProviderProfiles(context)
    assert reloaded.describe("fixture", "model") == {"contextWindowTokens": 64000, "contextWindowSource": "user", "inputModalities": ["text", "image"], "supportsTools": True}
    assert reloaded.describe("fixture", "new-model")["inputModalities"] is None
    assert SECRET not in repr(reloaded.catalog())


def test_explicit_apply_is_blocked_by_any_active_consumer(provider):
    profiles, _context = provider
    profiles.set_service(SimpleNamespace(has_active_jobs=lambda: True))
    with pytest.raises(ProfileError) as error:
        profiles._apply({})
    assert error.value.code == "MODEL_BUSY"


def test_probe_uses_saved_draft_without_applying_and_releases_finished_operation(provider):
    profiles, context = provider
    completed = threading.Event()
    calls = []
    class Service:
        def begin_probe(self, descriptor):
            calls.append(descriptor)
        def poll(self, *args):
            return {"sequence": 1, "state": "completed"}
        def result(self, operation):
            return {"response": {"models": ["new-model"]}}
        def release(self, operation):
            completed.set()
    context.service = Service()
    profiles.set_service(context.service)
    profiles._start_probe({"profileId": "fixture", "modelId": ""}, operation="list_models")
    assert completed.wait(2)
    profiles.close()
    assert context.config.get()["profiles"][0]["models"][0]["modelId"] == "new-model"
    assert profiles.catalog()[0]["models"][0]["modelId"] == "model"
    assert calls[0]["operation"] == "list_models"
    assert SECRET not in repr(profiles._load_actions())


def test_probe_failure_reports_safe_code_and_always_releases(provider):
    profiles, context = provider
    released = threading.Event()
    service = SimpleNamespace(begin_probe=lambda descriptor: None, poll=lambda *args: {"sequence": 1, "state": "failed"},
                              result=lambda operation: {"failure": {"code": "AUTH_REQUIRED", "message": SECRET}}, release=lambda operation: released.set())
    profiles.set_service(service)
    context.service = service
    profiles._start_probe({"profileId": "fixture", "modelId": "model"}, operation="test_connection")
    assert released.wait(2)
    profiles.close()
    assert profiles._load_actions()["probeStatus"]["message"] == "AUTH_REQUIRED"
    assert SECRET not in repr(profiles._load_actions())


def test_model_collection_identity_survives_reload_and_long_model_ids(provider):
    profiles, context = provider
    model_id = 'model-' + ('"' * 250)
    context.config.value["profiles"][0]["models"] = [{"modelId": model_id, "label": model_id}]
    item, = _query(context, "models")["items"]
    assert len(item["itemId"]) > 200
    reloaded = ProviderProfiles(context)
    same, = reloaded.query_models({"limit": 25})["items"]
    assert same["itemId"] == item["itemId"]
    result = reloaded.update_model(item["itemId"], {"contextWindowTokens": 64000, "inputModalities": "text", "supportsTools": "no"})
    assert result["itemId"] == item["itemId"]
    assert context.config.value["profiles"][0]["models"][0]["contextWindowTokens"] == 64000
