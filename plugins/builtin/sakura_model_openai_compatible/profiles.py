"""Provider-owned connections. Saving never changes this process's effective profiles."""
from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
import json
import re
import threading
import uuid
from urllib.parse import urlparse


SERVICE_KEY = "sakura.model.openai_compatible"
_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


class ProfileError(ValueError):
    def __init__(self, code: str, message: str = "模型服务配置无效。") -> None:
        super().__init__(message)
        self.code = code


def _text(value, *, required=False, maximum=16384):
    if not isinstance(value, str) or "\x00" in value or len(value) > maximum:
        raise ProfileError("FIELD_INVALID")
    value = value.strip()
    if required and not value:
        raise ProfileError("FIELD_REQUIRED")
    return value


def _url(value):
    value = _text(value, required=True, maximum=2048).rstrip("/")
    try:
        parsed = urlparse(value)
        parsed.port
    except ValueError as error:
        raise ProfileError("BASE_URL_INVALID") from error
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ProfileError("BASE_URL_INVALID", "API 地址格式无效。")
    return value


def _timeout(value):
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 300:
        raise ProfileError("FIELD_INVALID", "请求超时须为 1 到 300 秒。")
    return value


def _models(raw):
    if isinstance(raw, str):
        raw = raw.splitlines()
    if not isinstance(raw, list):
        raise ProfileError("MODELS_INVALID")
    result = []
    seen = set()
    for value in raw:
        item = dict(value) if isinstance(value, Mapping) else {"modelId": value}
        model_id = _text(item.get("modelId", item.get("name", "")), maximum=256)
        if not model_id:
            continue
        if model_id in seen:
            raise ProfileError("MODEL_DUPLICATE")
        seen.add(model_id)
        item.pop("name", None)
        item.update(modelId=model_id, label=_text(item.get("label", model_id), maximum=256))
        window = item.get("contextWindowTokens")
        if window is not None and (isinstance(window, bool) or not isinstance(window, int) or not 4096 <= window <= 2_000_000):
            raise ProfileError("MODEL_CONTEXT_INVALID")
        modalities = item.get("inputModalities")
        if modalities is not None and (not isinstance(modalities, list) or any(value not in {"text", "image"} for value in modalities)):
            raise ProfileError("MODEL_CAPABILITIES_INVALID")
        if item.get("supportsTools") is not None and not isinstance(item["supportsTools"], bool):
            raise ProfileError("MODEL_CAPABILITIES_INVALID")
        result.append(item)
    return result


def _profiles(config):
    raw = config.get("profiles", [])
    if not isinstance(raw, list):
        raise ProfileError("PROFILES_INVALID")
    result = []
    seen = set()
    for value in raw:
        if not isinstance(value, Mapping):
            raise ProfileError("PROFILE_INVALID")
        item = dict(value)
        identity = _text(item.get("profileId", ""), required=True, maximum=64)
        if not _ID.fullmatch(identity) or identity in seen:
            raise ProfileError("PROFILE_ID_INVALID")
        seen.add(identity)
        item.update(profileId=identity, label=_text(item.get("label", identity), required=True, maximum=120),
                    base_url=_url(item["base_url"]) if item.get("base_url") else "", api_key=_text(item.get("api_key", "")),
                    timeout_seconds=_timeout(item.get("timeout_seconds", 60)), models=_models(item.get("models", [])))
        result.append(item)
    return result


class ProviderProfiles:
    def __init__(self, context):
        self._context = context
        self._config = context.config
        self._lock = threading.RLock()
        self._active = deepcopy(_profiles(self._config.get()))
        self._service = None
        self._probe = None
        self._probe_thread = None
        self._probe_cancel = threading.Event()
        self._probe_result = {}

    def set_service(self, service):
        self._service = service
        self._context.effect(self.close)

    def close(self):
        with self._lock:
            operation, worker = self._probe, self._probe_thread
        if operation:
            self._probe_cancel.set()
        if worker is not None and worker is not threading.current_thread():
            worker.join(timeout=2)

    def _start_probe(self, values, *, operation):
        with self._lock:
            if self._probe is not None:
                raise ProfileError("MODEL_PROBE_BUSY", "模型测试仍在进行。")
            if self._service is None:
                raise ProfileError("MODEL_PROVIDER_UNAVAILABLE")
            operation_id = "probe-" + uuid.uuid4().hex
            # Settings callbacks do not carry a model caller scope. Calling our
            # own published service through bind uses the same authenticated
            # owner contract as every other model consumer.
            bound = self._context.bind(SERVICE_KEY)
            try:
                bound.invoke("begin_probe", {"operationId": operation_id, "operation": operation,
                                             "profileId": values.get("profileId", ""), "values": {key: value for key, value in values.items() if key in {"modelId", "base_url", "credential", "timeout_seconds"}}}, timeout_seconds=5)
            except Exception:
                try:
                    bound.invoke("release", operation_id, timeout_seconds=1)
                except Exception:
                    pass
                raise
            self._probe = operation_id
            self._probe_result = {"requestId": values.get("requestId", ""), "state": "running"}
            self._probe_cancel.clear()

        def run():
            try:
                sequence = 0
                while True:
                    if self._probe_cancel.is_set():
                        bound.invoke("cancel", operation_id, timeout_seconds=1)
                    result = bound.invoke("poll", operation_id, sequence, 500, timeout_seconds=2)
                    sequence = result["sequence"]
                    if result["state"] != "running":
                        break
                completed = bound.invoke("result", operation_id, timeout_seconds=5)
                if completed.get("failure"):
                    code = completed["failure"].get("code", "MODEL_PROBE_FAILED")
                    raise ProfileError(code if isinstance(code, str) and re.fullmatch(r"[A-Z_]{1,80}", code) else "MODEL_PROBE_FAILED")
                result = completed["response"]
                if operation == "list_models":
                    result = {"models": _models(result["models"])}
                    message = ""
                else:
                    message = str(values.get("modelId", ""))
                state = {"state": "ready", "label": "已获取模型" if operation == "list_models" else "连接成功", "message": message}
            except Exception as error:
                result = {}
                state = {"state": "error", "label": "模型测试失败", "message": str(getattr(error, "code", "MODEL_PROBE_FAILED"))}
            finally:
                try:
                    bound.invoke("release", operation_id, timeout_seconds=1)
                except Exception:
                    state = {"state": "error", "label": "模型测试失败", "message": "MODEL_PROBE_CLEANUP_FAILED"}
                finally:
                    with self._lock:
                        self._probe = None
                        self._probe_result = {"requestId": values.get("requestId", ""),
                                              "state": "completed" if state["state"] == "ready" else "failed",
                                              "code": state["message"] if state["state"] == "error" else "",
                                              "models": result.get("models", []) if state["state"] == "ready" else []}

        worker = threading.Thread(target=run, name="model-settings-probe", daemon=True)
        with self._lock:
            self._probe_thread = worker
        worker.start()
        return self.editor_probe_status({})

    def catalog(self):
        public_keys = {"modelId", "label", "contextWindowTokens", "inputModalities", "supportsTools"}
        return [{"profileId": item["profileId"], "label": item["label"],
                 "models": [{key: deepcopy(value) for key, value in model.items() if key in public_keys} for model in item["models"]]}
                for item in self._active]

    def _profile(self, profile_id):
        value = next((item for item in self._active if item["profileId"] == profile_id), None)
        if value is None:
            raise ProfileError("MODEL_REFERENCE_INVALID")
        return value

    def describe(self, profile_id, model_id):
        profile = self._profile(profile_id)
        model = next((item for item in profile["models"] if item["modelId"] == model_id), None)
        if model is None:
            raise ProfileError("MODEL_REFERENCE_INVALID")
        window = model.get("contextWindowTokens")
        return {"contextWindowTokens": window or 32768, "contextWindowSource": "user" if window else "fallback",
                "inputModalities": deepcopy(model.get("inputModalities")), "supportsTools": model.get("supportsTools")}

    def resolve(self, profile_id, model_id):
        profile = self._profile(profile_id)
        description = self.describe(profile_id, model_id)
        return {"base_url": _url(profile["base_url"]), "api_key": profile["api_key"], "model": model_id,
                "timeout_seconds": profile["timeout_seconds"],
                "context_window_tokens": description["contextWindowTokens"], "context_window_source": description["contextWindowSource"]}

    def resolve_probe(self, profile_id, values, require_model=False):
        if not isinstance(values, Mapping):
            raise ProfileError("INVALID_REQUEST")
        saved = next((item for item in _profiles(self._config.get()) if item["profileId"] == profile_id), {})
        credential = values.get("credential", {"action": "keep"})
        key = self._credential(saved.get("api_key", ""), credential)
        return {"base_url": _url(values.get("base_url", saved.get("base_url", ""))), "api_key": key,
                "model": _text(values.get("modelId", values.get("model", "")), required=require_model, maximum=256),
                "timeout_seconds": _timeout(values.get("timeout_seconds", saved.get("timeout_seconds", 60)))}

    @staticmethod
    def _credential(previous, raw):
        if not isinstance(raw, Mapping) or set(raw) - {"action", "value"}:
            raise ProfileError("CREDENTIAL_ACTION_INVALID")
        action = raw.get("action")
        value = _text(raw.get("value", ""))
        if action not in {"keep", "replace", "clear"} or (action != "replace" and value):
            raise ProfileError("CREDENTIAL_ACTION_INVALID")
        if action == "replace" and not value:
            raise ProfileError("CREDENTIAL_REQUIRED")
        return previous if action == "keep" else value if action == "replace" else ""

    def _saved(self):
        return _profiles(self._config.get())

    def _write(self, profiles):
        # No on_change handler applies profiles in this instance. A new bound process
        # is the only point at which saved credentials become effective.
        result = self._config.update({"profiles": profiles})
        if result == "error":
            raise ProfileError("CONFIG_APPLY_FAILED")

    def load_editor(self):
        with self._lock:
            return {"connections": [{"id": p["profileId"], "alias": p["label"], "base_url": p["base_url"],
                     "api_key": "", "configured": bool(p["api_key"]), "credential_action": "keep",
                     "models": [m["modelId"] for m in p["models"]], "timeout_seconds": p["timeout_seconds"]}
                    for p in self._saved()], "probeRequest": {}, "probeResult": dict(self._probe_result)}

    def save_editor(self, values):
        if self._service is not None and self._service.has_active_jobs():
            raise ProfileError("MODEL_BUSY", "模型仍在处理请求，请稍后应用。")
        raw = values.get("connections")
        if not isinstance(raw, list):
            raise ProfileError("PROFILES_INVALID")
        with self._lock:
            previous = {p["profileId"]: p for p in self._saved()}
            profiles = []
            for value in raw:
                if not isinstance(value, Mapping):
                    raise ProfileError("PROFILE_INVALID")
                identity = _text(value.get("id", ""), required=True, maximum=64)
                old = previous.get(identity, {})
                old_models = {m["modelId"]: m for m in old.get("models", [])}
                models = [deepcopy(old_models.get(m["modelId"], m)) for m in _models(value.get("models", []))]
                profiles.append(dict(old, profileId=identity,
                    label=_text(value.get("alias", ""), required=True, maximum=120),
                    base_url=_url(value.get("base_url", "")), models=models,
                    timeout_seconds=_timeout(value.get("timeout_seconds", old.get("timeout_seconds", self.load_timeout()["timeout_seconds"]))),
                    api_key=self._credential(old.get("api_key", ""), {"action": value.get("credential_action", "keep"), "value": value.get("api_key", "")})))
            # Validate the entire set before its single write, including duplicate identities.
            self._write(_profiles({"profiles": profiles}))
        return {"applicationState": "restart_required"}

    def load_timeout(self):
        saved = self._config.get()
        profiles = _profiles(saved)
        return {"timeout_seconds": saved.get("timeout_seconds", profiles[0]["timeout_seconds"] if profiles else 60)}

    def save_timeout(self, values):
        timeout = _timeout(values.get("timeout_seconds"))
        if self._service is not None and self._service.has_active_jobs():
            raise ProfileError("MODEL_BUSY", "模型仍在处理请求，请稍后应用。")
        with self._lock:
            profiles = [dict(p, timeout_seconds=timeout) for p in self._saved()]
            if self._config.update({"timeout_seconds": timeout, "profiles": profiles}) == "error":
                raise ProfileError("CONFIG_APPLY_FAILED")
        return {"applicationState": "restart_required"}

    def editor_probe(self, values):
        request = values.get("probeRequest", {})
        operation = request.get("operation")
        if operation not in {"list_models", "test_connection"}:
            raise ProfileError("INVALID_REQUEST")
        self._start_probe(request, operation=operation)
        return self.editor_probe_status({})

    def editor_probe_status(self, _values):
        with self._lock:
            return {"values": {"probeResult": deepcopy(self._probe_result)}}

    def editor_cancel(self, values):
        with self._lock:
            request_id = values.get("probeRequest", {}).get("requestId")
            if request_id == self._probe_result.get("requestId"):
                self._probe_cancel.set()
        return self.editor_probe_status({})

    def register_settings(self):
        settings = self._context.get("sakura.host.settings")
        settings.register({"sectionId": "connections", "title": "模型服务", "order": 10,
            "presentation": {"component": "connection-editor", "serviceKey": SERVICE_KEY, "valueField": "connections",
                             "requestField": "probeRequest", "resultField": "probeResult", "timeoutSection": "request", "timeoutField": "timeout_seconds",
                             "probeAction": "probe", "statusAction": "probeStatus", "cancelAction": "cancelProbe"},
            "fields": [{"key": "connections", "label": "连接", "type": "data", "default": []},
                       {"key": "probeRequest", "label": "测试请求", "type": "data", "default": {}},
                       {"key": "probeResult", "label": "测试结果", "type": "data", "default": {}, "readonly": True}],
            "actions": [{"actionId": "probe", "label": "测试连接"}, {"actionId": "probeStatus", "label": "测试状态"},
                        {"actionId": "cancelProbe", "label": "取消测试"}]},
            load=self.load_editor, save=self.save_editor,
            actions={"probe": self.editor_probe, "probeStatus": self.editor_probe_status, "cancelProbe": self.editor_cancel})
        settings.place("connections", page_id="host:providers", order=10)
        settings.register({"sectionId": "request", "title": "高级参数", "order": 20,
            "presentation": {"component": "form", "group": "model-advanced", "collapsible": True},
            "fields": [{"key": "timeout_seconds", "label": "请求超时时间", "type": "integer", "default": 60,
                        "minimum": 1, "maximum": 300, "unit": "秒"}]}, load=self.load_timeout, save=self.save_timeout)
        settings.place("request", page_id="host:model", order=20)
