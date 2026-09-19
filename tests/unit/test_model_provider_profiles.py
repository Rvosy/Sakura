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
        assert key == "sakura.host.settings"
        def register(descriptor, *, load=None, save=None, actions=None):
            return self.host.call("register", [SERVICE_KEY, descriptor, {"load": self.handle(load), "save": self.handle(save),
                "actions": {key: self.handle(value) for key, value in (actions or {}).items()}}])
        def place(section_id, **kwargs):
            return self.host.call("register", [SERVICE_KEY, {"kind": "placement", "sectionId": section_id,
                "pageId": kwargs["page_id"], "order": kwargs.get("order", 100)}, {}])
        return SimpleNamespace(register=register, place=place)


@pytest.fixture
def provider():
    context = Context()
    profiles = ProviderProfiles(context)
    profiles.register_settings()
    return profiles, context


@pytest.mark.parametrize(("action", "value", "expected"), [("keep", "", SECRET), ("replace", "replacement", "replacement"), ("clear", "", "")])
def test_editor_credential_actions_are_private_and_effective_only_after_reload(provider, action, value, expected):
    profiles, context = provider
    snapshot = context.host.sections_for_plugin(SERVICE_KEY)[0]
    assert SECRET not in repr(snapshot)
    draft = snapshot["values"]
    draft["connections"][0].update(credential_action=action, api_key=value)
    found, result = context.host.save(SERVICE_KEY, "connections", {k: draft[k] for k in ("connections", "probeRequest")})
    assert found and result["applicationState"] == "restart_required"
    assert context.config.get()["profiles"][0]["api_key"] == expected
    assert profiles.resolve("fixture", "model")["api_key"] == SECRET
    assert ProviderProfiles(context).resolve("fixture", "model")["api_key"] == expected


def test_invalid_second_connection_does_not_partially_save(provider):
    profiles, context = provider
    before = context.config.get()
    draft = profiles.load_editor()
    draft["connections"][0]["alias"] = "Changed"
    draft["connections"].append({"id": "broken", "alias": "Broken", "base_url": "not-a-url"})
    with pytest.raises(ProfileError):
        profiles.save_editor(draft)
    assert context.config.get() == before
    draft = profiles.load_editor()
    draft["connections"][0]["api_key"] = "must-not-be-used-with-keep"
    with pytest.raises(ProfileError):
        profiles.save_editor(draft)
    assert context.config.get() == before


def test_list_edit_retains_existing_model_metadata_and_unknown_profile_fields(provider):
    profiles, context = provider
    context.config.value["profiles"][0]["future"] = "keep"
    draft = profiles.load_editor()
    draft["connections"][0]["models"].append("new-model")
    profiles.save_editor(draft)
    saved = context.config.get()["profiles"][0]
    assert saved["models"][0]["contextWindowTokens"] == 128000
    assert saved["models"][0]["label"] == "Model"
    assert saved["models"][1]["modelId"] == "new-model"
    assert saved["future"] == "keep"
    assert len(profiles.catalog()[0]["models"]) == 1


def test_save_is_blocked_before_writing_when_any_consumer_is_active(provider):
    profiles, context = provider
    before = context.config.get()
    profiles.set_service(SimpleNamespace(has_active_jobs=lambda: True))
    with pytest.raises(ProfileError) as error:
        profiles.save_editor(profiles.load_editor())
    assert error.value.code == "MODEL_BUSY"
    assert context.config.get() == before


def test_discovery_uses_unsaved_connection_without_writing_and_releases(provider):
    profiles, context = provider
    before = context.config.get()
    released = threading.Event()
    calls = []
    context.service = SimpleNamespace(begin_probe=lambda descriptor: calls.append(descriptor),
        poll=lambda *args: {"sequence": 1, "state": "completed"},
        result=lambda operation: {"response": {"models": ["new-model"]}}, release=lambda operation: released.set())
    profiles.set_service(context.service)
    request = {"operation": "list_models", "requestId": "test", "profileId": "unsaved",
               "base_url": "https://unsaved.invalid/v1", "credential": {"action": "replace", "value": "draft-key"}, "timeout_seconds": 17}
    context.host.action(SERVICE_KEY, "connections", "probe", {"probeRequest": request})
    assert released.wait(2)
    profiles.close()
    result = profiles.editor_probe_status({})["values"]["probeResult"]
    assert result["state"] == "completed" and result["requestId"] == "test"
    assert result["models"][0]["modelId"] == "new-model"
    assert context.config.get() == before
    assert calls[0]["profileId"] == "unsaved"
    assert calls[0]["values"]["credential"]["value"] == "draft-key"
    assert "draft-key" not in repr(result)


def test_probe_failure_reports_safe_code_and_always_releases(provider):
    profiles, context = provider
    released = threading.Event()
    context.service = SimpleNamespace(begin_probe=lambda descriptor: None, poll=lambda *args: {"sequence": 1, "state": "failed"},
        result=lambda operation: {"failure": {"code": "AUTH_REQUIRED", "message": SECRET}}, release=lambda operation: released.set())
    profiles.set_service(context.service)
    profiles.editor_probe({"probeRequest": {"operation": "test_connection", "requestId": "test", "profileId": "fixture", "modelId": "model"}})
    assert released.wait(2)
    profiles.close()
    result = profiles.editor_probe_status({})
    assert result["values"]["probeResult"]["code"] == "AUTH_REQUIRED"
    assert SECRET not in repr(result)


def test_global_timeout_is_applied_only_on_explicit_edit(provider):
    profiles, context = provider
    context.config.value["profiles"].append({**context.config.value["profiles"][0], "profileId": "second", "timeout_seconds": 45})
    profiles.save_editor(profiles.load_editor())
    assert context.config.get()["profiles"][1]["timeout_seconds"] == 45
    profiles.save_timeout({"timeout_seconds": 23})
    assert [p["timeout_seconds"] for p in context.config.get()["profiles"]] == [23, 23]
    draft = profiles.load_editor()
    draft["connections"].append({"id": "new", "alias": "New", "base_url": "https://new.invalid/v1", "models": []})
    profiles.save_editor(draft)
    assert context.config.get()["profiles"][-1]["timeout_seconds"] == 23


def test_probe_cancel_is_request_scoped_and_releases_without_saving(provider):
    profiles, context = provider
    polling, resume, released = threading.Event(), threading.Event(), threading.Event()
    cancelled = []
    before = context.config.get()
    count = 0
    def poll(*args):
        nonlocal count
        count += 1
        if count == 1:
            polling.set()
            assert resume.wait(2)
            return {"sequence": 1, "state": "running"}
        return {"sequence": 2, "state": "cancelled"}
    context.service = SimpleNamespace(begin_probe=lambda descriptor: None, poll=poll,
        cancel=lambda operation: cancelled.append(operation),
        result=lambda operation: {"failure": {"code": "MODEL_CANCELLED"}}, release=lambda operation: released.set())
    profiles.set_service(context.service)
    profiles.editor_probe({"probeRequest": {"operation": "list_models", "requestId": "current"}})
    assert polling.wait(2)
    profiles.editor_cancel({"probeRequest": {"requestId": "old"}})
    assert not profiles._probe_cancel.is_set()
    profiles.editor_cancel({"probeRequest": {"requestId": "current"}})
    resume.set()
    assert released.wait(2)
    profiles.close()
    assert len(cancelled) == 1
    assert context.config.get() == before
    assert profiles.editor_probe_status({})["values"]["probeResult"]["state"] == "failed"
