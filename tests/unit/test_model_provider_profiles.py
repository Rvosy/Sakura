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
        self.logs = []
        self.host = _SettingsHostService(lambda handle, _shape, *args: self.callbacks[handle](*args))
    def effect(self, _cleanup):
        pass
    def handle(self, callback):
        if callback is None:
            return None
        handle = "cb_" + uuid.uuid4().hex
        self.callbacks[handle] = callback
        return handle
    def get(self, key):
        if key == "sakura.host.artifacts":
            return None
        if key == "sakura.host.logging":
            return SimpleNamespace(warning=lambda message, **kwargs: self.logs.append((message, kwargs)))
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


def test_editor_preserves_connection_creation_order_after_save_and_reload(provider):
    profiles, context = provider
    draft = profiles.load_editor()
    draft["connections"].append({"id": "new", "alias": "New", "base_url": "https://example.test/v1", "models": [], "createdAt": 12345})
    profiles.save_editor(draft)
    reloaded = ProviderProfiles(context).load_editor()
    assert reloaded["connections"][-1]["createdAt"] == 12345
    reloaded["connections"][-1]["alias"] = "Renamed"
    profiles.save_editor(reloaded)
    assert ProviderProfiles(context).load_editor()["connections"][-1]["createdAt"] == 12345


@pytest.mark.parametrize(("returned", "result_type", "state_type", "has_state", "field", "reason"), [
    (True, "bool", "missing", False, "result", "bool"),
    ({"unexpected": SECRET}, "dict", "missing", False, "applicationState", "缺少 applicationState"),
    ({"applicationState": SECRET}, "dict", "str", True, "applicationState", "applied"),
    ({"applicationState": [SECRET]}, "dict", "list", True, "applicationState", "list"),
    ([], "list", "missing", False, "result", "不能为空"),
    (["applied", 42], "list", "missing", False, "/1", "int"),
])
def test_invalid_save_result_keeps_plugin_section_and_shape_without_values(
    provider, tmp_path, returned, result_type, state_type, has_state, field, reason,
):
    from app.core_host.plugin_settings import PluginSettingsBoundary

    profiles, context = provider
    context.host._invoke_callback = lambda *_args: returned
    application = SimpleNamespace(settings_save=lambda plugin, section, values: context.host.save(plugin, section, values)[1])
    boundary = PluginSettingsBoundary("generation", "credential", tmp_path, application_provider=lambda: application)
    result = boundary.handle({
        "id": "save-shape", "name": "plugins.settings.save", "protocolMinor": 2,
        "generationId": "generation", "generationCredential": "credential",
        "payload": {"pluginId": SERVICE_KEY, "sectionId": "request", "values": {"timeout_seconds": 23}},
    })

    assert result["ok"] is False
    assert result["error"]["code"] == "SETTINGS_SAVE_RESULT_INVALID"
    details = result["error"]["details"]["diagnostics"]
    assert {key: details[key] for key in (
        "plugin_id", "section_id", "result_type", "has_application_state", "application_state_type",
    )} == {
        "plugin_id": SERVICE_KEY, "section_id": "request", "result_type": result_type,
        "has_application_state": has_state, "application_state_type": state_type,
    }
    assert SECRET not in repr(result)
    assert details["validation_field"] == field
    assert reason in details["diagnostic"]


@pytest.mark.parametrize("timeout", [None, "60", 0])
def test_prior_handoff_profile_and_normalized_drafts_remain_editable(timeout):
    context = Context()
    context.config.value["profiles"][0].update(label="", models=[" model ", "model"], timeout_seconds=timeout)
    before = context.config.get()
    profiles = ProviderProfiles(context)
    draft = profiles.load_editor()
    assert draft["connections"][0]["alias"] == "fixture"
    assert draft["connections"][0]["models"] == ["model"]
    assert profiles.resolve("fixture", "model")["api_key"] == SECRET
    assert profiles.resolve("fixture", "model")["timeout_seconds"] == 60
    assert context.config.get() == before

    draft["connections"][0]["models"].append("model")
    draft["connections"][0]["alias"] = ""
    profiles.save_editor(draft)
    assert context.config.get()["profiles"][0]["label"] == "fixture"
    assert [item["modelId"] for item in context.config.get()["profiles"][0]["models"]] == ["model"]


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


def test_existing_long_connection_values_can_be_saved_without_shortening(provider):
    profiles, context = provider
    context.config.value["profiles"][0].update(
        label="自建模型服务" * 30,
        base_url="https://fixture.invalid/" + "gateway/" * 300 + "v1",
        api_key="token-" + "a" * 17000,
        models=[{"modelId": "model-" + "a" * 300, "label": "模型" * 300,
                 "contextWindowTokens": 4_000_000, "inputModalities": ["text", "image", "audio"]}],
    )
    model = context.config.get()["profiles"][0]["models"][0]
    expected = ProviderProfiles(context).resolve("fixture", model["modelId"])
    draft = profiles.load_editor()
    draft["connections"][0].update(credential_action="replace", api_key=expected["api_key"])
    profiles.save_editor(draft)
    restored = ProviderProfiles(context)
    assert restored.resolve("fixture", model["modelId"]) == expected
    assert restored.catalog()[0]["models"] == [model]
    assert profiles.load_editor()["connections"][0]["alias"] == draft["connections"][0]["alias"]


def test_discovery_uses_unsaved_connection_without_writing(provider):
    profiles, context = provider
    before = context.config.get()
    calls = []

    def execute(settings, request, *, operation, **kwargs):
        calls.append((settings, operation))
        return {"models": ["new-model"]}

    profiles.set_service(SimpleNamespace(execute=execute))
    request = {"operation": "list_models", "requestId": "test", "profileId": "unsaved",
               "base_url": "https://unsaved.invalid/v1", "credential": {"action": "replace", "value": "draft-key"}, "timeout_seconds": 17}
    context.host.action(SERVICE_KEY, "connections", "probe", {"probeRequest": request})
    profiles._probe_thread.join(2)
    result = profiles.editor_probe_status({})["values"]["probeResult"]
    assert result["state"] == "completed" and result["requestId"] == "test"
    assert result["models"][0]["modelId"] == "new-model"
    assert context.config.get() == before
    settings, operation = calls[0]
    assert operation == "list_models"
    assert settings["base_url"] == request["base_url"] and settings["api_key"] == "draft-key"
    assert "draft-key" not in repr(result)


def test_probe_failure_preserves_reason_and_redacts_credentials(provider):
    from sakura_model import ModelError

    profiles, context = provider
    draft_secret = 'draft-key-with-"quotes"'

    def execute(*args, **kwargs):
        raise ModelError("MODEL_AUTHENTICATION_FAILED", f"model access denied {SECRET} {draft_secret}",
                         diagnostics={"http_status": 403, "errno": 61, "winerror": 10061, "exit_code": 1})

    profiles.set_service(SimpleNamespace(execute=execute))
    profiles.editor_probe({"probeRequest": {"operation": "test_connection", "requestId": "test", "profileId": "fixture", "modelId": "model",
                                           "credential": {"action": "replace", "value": draft_secret}}})
    profiles._probe_thread.join(2)
    result = profiles.editor_probe_status({})["values"]["probeResult"]
    assert result["state"] == "failed" and result["code"] == "MODEL_AUTHENTICATION_FAILED"
    assert "model access denied" in result["message"]
    assert result["diagnostics"]["http_status"] == 403
    assert SECRET not in repr(result) and draft_secret not in str(result)


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


def test_probe_cancel_is_request_scoped_without_saving(provider):
    profiles, context = provider
    running, resume = threading.Event(), threading.Event()
    before = context.config.get()

    def execute(settings, request, *, cancel_checker, **kwargs):
        running.set()
        assert resume.wait(2)
        cancel_checker()
        return {}

    profiles.set_service(SimpleNamespace(execute=execute))
    profiles.editor_probe({"probeRequest": {"operation": "list_models", "requestId": "current", "profileId": "fixture"}})
    assert running.wait(2)
    profiles.editor_cancel({"probeRequest": {"requestId": "old"}})
    assert not profiles._probe_cancel.is_cancelled()
    profiles.editor_cancel({"probeRequest": {"requestId": "current"}})
    resume.set()
    profiles._probe_thread.join(2)
    assert context.config.get() == before
    assert profiles.editor_probe_status({})["values"]["probeResult"]["code"] == "OPERATION_CANCELLED"
