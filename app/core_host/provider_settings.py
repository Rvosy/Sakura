"""Generation-scoped public model selection; provider settings remain plugin-owned."""
from __future__ import annotations

import hmac
import threading
from collections.abc import Callable, Mapping
from pathlib import Path

from app.config.model_references import EMPTY_REFERENCE, ModelReferenceRepository, model_reference
from .protocol import error_payload, response

SETTINGS_REQUEST_NAMES = frozenset({"settings.provider_model.get", "settings.provider_model.save"})


class ModelSettingsError(ValueError):
    def __init__(self, code, message="模型设置操作失败。", *, feature="model.slots", field=""):
        super().__init__(message)
        self.code, self.message, self.feature, self.field = code, message, feature, field


class ProviderSettingsBoundary:
    def __init__(self, generation_id: str, generation_credential: str, app_root: Path, *,
                 plugin_application_provider: Callable[[], object | None] | None = None, runtime_apply=None):
        self._generation_id = generation_id
        self._generation_credential = generation_credential
        self._repository = ModelReferenceRepository(app_root)
        self._plugin_application_provider = plugin_application_provider
        self._runtime_apply = runtime_apply
        self._lock = threading.Lock()
        self._save_lock = threading.Lock()
        self._closed = self._enabled = False

    def enable(self):
        with self._lock:
            self._enabled = True

    def _application(self):
        return self._plugin_application_provider() if self._plugin_application_provider else None

    def handle(self, request):
        supplied = request.get("generationCredential")
        if request.get("generationId") != self._generation_id or not isinstance(supplied, str) or not hmac.compare_digest(supplied, self._generation_credential):
            raise RuntimeError("GENERATION_IDENTITY_MISMATCH")
        try:
            with self._lock:
                if not self._enabled or self._closed:
                    raise ModelSettingsError("CAPABILITY_NEGOTIATION_FAILED", "设置能力尚未就绪。")
            name = request.get("name")
            if name == "settings.provider_model.get":
                if request.get("payload") != {}:
                    raise ModelSettingsError("INVALID_REQUEST")
                payload = self._snapshot()
            elif name == "settings.provider_model.save":
                raw = request.get("payload")
                if not isinstance(raw, Mapping) or set(raw) != {"draft"}:
                    raise ModelSettingsError("INVALID_REQUEST")
                with self._save_lock:
                    with self._lock:
                        if self._closed:
                            raise ModelSettingsError("CAPABILITY_NEGOTIATION_FAILED", "设置能力尚未就绪。")
                    payload = self._save(raw["draft"])
                    if self._runtime_apply is not None and payload["save_state"] == "complete":
                        try:
                            self._runtime_apply()
                        except Exception as error:
                            raise ModelSettingsError("CONFIG_APPLY_FAILED", "模型选择已保存，尚未应用。请结束对话后重新保存或重新加载。") from error
            else:
                raise ModelSettingsError("UNKNOWN_COMMAND")
            return self._response(request, payload=payload)
        except ModelSettingsError as error:
            return self._response(request, error=error)
        except Exception as error:
            code = getattr(error, "code", "")
            if not isinstance(code, str) or not code or len(code) > 80 or not code.replace("_", "").isalnum():
                code = "MODEL_SETTINGS_FAILED"
            return self._response(request, error=ModelSettingsError(code))

    def _catalog(self):
        application = self._application()
        if application is None:
            return []
        return application.model_catalog()

    def _plugin_slots(self):
        application = self._application()
        return application.model_slots() if application is not None else []

    def _snapshot(self):
        application = self._application()
        issue = getattr(application, "model_configuration_issue", lambda: None)()
        try:
            selections = self._repository.load()
        except (OSError, ValueError):
            issue = "CONFIG_DATA_INVALID"
            selections = {name: dict(EMPTY_REFERENCE) for name in ("chat", "vision_chat")}
        slots = [{"identity": f"core:{name}", "ownerType": "core", "ownerId": "sakura.core", "slotId": name,
                  "label": label, "description": "", "modelKind": "chat_completion", "required": name == "chat",
                  "order": order, "reasonCode": "READY", "selection": selections[name]}
                 for name, label, order in (("chat", "对话模型", 10), ("vision_chat", "视觉对话模型", 20))]
        slots.extend(self._plugin_slots())
        catalog = self._catalog()
        choices = {(provider["serviceKey"], profile["profileId"], model["modelId"])
                   for provider in catalog for profile in provider.get("profiles", []) for model in profile.get("models", [])}
        for slot in slots:
            selection = slot.get("selection", EMPTY_REFERENCE)
            if selection.get("serviceKey") and tuple(selection.get(key, "") for key in EMPTY_REFERENCE) not in choices:
                slot["reasonCode"] = "MODEL_REFERENCE_UNAVAILABLE"
        return {"schema_version": 2, "providers": catalog, "model_slots": sorted(slots, key=lambda slot: (slot.get("order", 100), slot["identity"])),
                "configuration_issue": {"code": issue, "message": "原模型配置无法读取。请在模型服务中检查连接，重新选择并保存模型。旧 API 配置会保留。"} if issue else None,
                "setup_complete": bool(selections["chat"]["serviceKey"] and slots[0]["reasonCode"] == "READY"), "change_plans": ["applied"]}

    def _save(self, raw):
        if not self._repository.path.exists() and self._application() is None:
            raise ModelSettingsError("MODEL_CONFIGURATION_NOT_READY", "模型配置尚未准备完成，请稍后重试。")
        if not isinstance(raw, Mapping) or set(raw) != {"model_slots"} or not isinstance(raw["model_slots"], Mapping):
            raise ModelSettingsError("INVALID_REQUEST")
        snapshot = self._snapshot()
        current = {slot["identity"]: slot for slot in snapshot["model_slots"]}
        choices = {(provider["serviceKey"], profile["profileId"], model["modelId"])
                   for provider in snapshot["providers"] for profile in provider.get("profiles", []) for model in profile.get("models", [])}
        if set(raw["model_slots"]) != set(current):
            raise ModelSettingsError("MODEL_SLOTS_INVALID", "模型槽已变化，请刷新后重试。")
        selections = {}
        for identity, value in raw["model_slots"].items():
            try:
                selections[identity] = model_reference(value)
            except ValueError as error:
                raise ModelSettingsError("MODEL_SLOT_INCOMPLETE", "请选择模型服务和模型。", field=identity) from error
            selection = selections[identity]
            if selection["serviceKey"] and selection != current[identity].get("selection") and tuple(selection.values()) not in choices:
                raise ModelSettingsError("MODEL_REFERENCE_UNAVAILABLE", "所选模型已不可用，请刷新后重试。", field=identity)
        # An unavailable reference remains editable and can be saved unchanged.
        # Installing/reloading a provider must never silently erase a selection.
        repairing = bool(snapshot["configuration_issue"]) or not self._repository.path.exists()
        if repairing and tuple(selections["core:chat"].values()) not in choices:
            raise ModelSettingsError("MODEL_CONFIGURATION_NOT_READY", "请选择可用的对话模型后保存。")
        self._repository.save({name: selections[f"core:{name}"] for name in ("chat", "vision_chat")}, replace_invalid=repairing)
        saved = ["core:chat", "core:vision_chat"]
        application = self._application()
        failed = None
        reload_required = False
        for identity, selection in selections.items():
            if not identity.startswith("plugin:") or selection == current[identity].get("selection"):
                continue
            try:
                result = application.model_slot_save(identity, selection)
                state = result.get("applicationState", "applied")
                if state not in {"applied", "restart_required"}:
                    raise ModelSettingsError("MODEL_SLOT_SAVE_FAILED")
                reload_required |= state == "restart_required"
                saved.append(identity)
            except Exception as error:
                failed = {"identity": identity, "ownerType": "plugin", "ownerId": identity.split(":", 2)[1], "reasonCode": getattr(error, "code", "MODEL_SLOT_SAVE_FAILED")}
                break
        return {"saved": True, "change_plan": "applied", "save_state": "partial" if failed else "complete",
                "saved_slots": saved, "failed_slot": failed, "plugin_reload_required": reload_required,
                "setup_complete": tuple(selections["core:chat"].values()) in choices}

    def close(self):
        with self._lock:
            self._closed = True

    def _response(self, request, *, payload=None, error=None):
        failure = None
        if error is not None:
            failure = error_payload(error.code, error.message)
            failure["details"] = {"feature": error.feature, "field": error.field}
        return response(request, generation_id=self._generation_id, generation_credential=self._generation_credential,
                        protocol_minor=int(request["protocolMinor"]), payload=payload, error=failure)
