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
        self._selection = {"profileId": self._active[0]["profileId"] if self._active else "", "modelId": ""}
        self._probe_state = {"state": "neutral", "label": "尚未测试", "message": ""}

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

    def _load_actions(self):
        with self._lock:
            status = dict(self._probe_state)
            return {**self._selection, "probeStatus": status}

    def _save_actions(self, values):
        with self._lock:
            self._selection = {key: _text(values.get(key, ""), maximum=256) for key in ("profileId", "modelId")}
        return {"applicationState": "applied"}

    def _apply(self, _values):
        if self._service is not None and self._service.has_active_jobs():
            raise ProfileError("MODEL_BUSY", "模型仍在处理请求，请稍后应用。")
        return {"applicationState": "restart_required", "message": "模型服务配置已保存。"}

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
                                             "profileId": values.get("profileId", ""), "values": {"modelId": values.get("modelId", "")}}, timeout_seconds=5)
            except Exception:
                try:
                    bound.invoke("release", operation_id, timeout_seconds=1)
                except Exception:
                    pass
                raise
            self._probe = operation_id
            self._selection = {key: values.get(key, "") for key in ("profileId", "modelId")}
            self._probe_cancel.clear()
            self._probe_state = {"state": "working", "label": "正在测试" if operation == "test_connection" else "正在获取模型", "message": ""}

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
                    with self._lock:
                        profiles = self._saved()
                        profile = next(item for item in profiles if item["profileId"] == values["profileId"])
                        existing = {item["modelId"]: item for item in profile["models"]}
                        profile["models"] = [dict(existing.get(item["modelId"], {}), **item) for item in _models(result["models"])]
                        self._write(profiles)
                    message = "模型列表已保存，应用后生效。"
                else:
                    message = str(values.get("modelId", ""))
                state = {"state": "ready", "label": "已获取模型" if operation == "list_models" else "连接成功", "message": message}
            except Exception as error:
                state = {"state": "error", "label": "模型测试失败", "message": str(getattr(error, "code", "MODEL_PROBE_FAILED"))}
            finally:
                try:
                    bound.invoke("release", operation_id, timeout_seconds=1)
                except Exception:
                    state = {"state": "error", "label": "模型测试失败", "message": "MODEL_PROBE_CLEANUP_FAILED"}
                finally:
                    with self._lock:
                        self._probe = None
                        self._probe_state = state

        worker = threading.Thread(target=run, name="model-settings-probe", daemon=True)
        with self._lock:
            self._probe_thread = worker
        worker.start()
        return {"values": self._load_actions(), "message": ""}

    def _cancel_probe(self, _values):
        with self._lock:
            operation = self._probe
        if operation:
            self._probe_cancel.set()
        return {"values": self._load_actions(), "message": ""}

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

    @staticmethod
    def _item(profile):
        return {"itemId": profile["profileId"], "values": {
            "label": profile["label"], "base_url": profile["base_url"],
            "configured": bool(profile["api_key"]), "credentialAction": "keep", "apiKey": "",
            "models": "\n".join(item["modelId"] for item in profile["models"]),
            "timeout_seconds": profile["timeout_seconds"],
        }}

    def query(self, request):
        with self._lock:
            items = [self._item(item) for item in self._saved()]
        needle = str(request.get("search", "")).casefold()
        if needle:
            items = [item for item in items if needle in (item["values"]["label"] + item["values"]["base_url"]).casefold()]
        start = int(request.get("cursor") or 0)
        limit = max(1, min(int(request.get("limit", 25)), 100))
        return {"items": items[start:start + limit], "nextCursor": str(start + limit) if start + limit < len(items) else None, "total": len(items)}

    def create(self, values):
        return self._upsert(uuid.uuid4().hex, values, create=True)

    def update(self, identity, values):
        return self._upsert(identity, values, create=False)

    def _upsert(self, identity, values, *, create):
        if not isinstance(values, Mapping):
            raise ProfileError("PROFILE_INVALID")
        with self._lock:
            profiles = self._saved()
            previous = next((item for item in profiles if item["profileId"] == identity), None)
            if previous is None and not create:
                raise ProfileError("MODEL_REFERENCE_INVALID")
            previous = previous or {}
            old_models = {item["modelId"]: item for item in previous.get("models", [])}
            models = [dict(old_models.get(item["modelId"], {}), **item) for item in _models(values.get("models", ""))]
            item = dict(previous, profileId=identity, label=_text(values.get("label", ""), required=True, maximum=120),
                        base_url=_url(values.get("base_url", "")),
                        api_key=self._credential(previous.get("api_key", ""), {"action": values.get("credentialAction", "keep"), "value": values.get("apiKey", "")}),
                        models=models, timeout_seconds=_timeout(values.get("timeout_seconds", 60)))
            profiles = [item if old["profileId"] == identity else old for old in profiles]
            if create:
                profiles.append(item)
            self._write(profiles)
            return {**self._item(item), "applicationState": "restart_required"}

    def delete(self, identity):
        with self._lock:
            profiles = self._saved()
            self._write([item for item in profiles if item["profileId"] != identity])
        return {"deleted": any(item["profileId"] == identity for item in profiles), "applicationState": "restart_required"}

    def _model_item(self, profile, model):
        identity = json.dumps([profile["profileId"], model["modelId"]], ensure_ascii=False, separators=(",", ":"))
        return {"itemId": identity,
                "values": {"profile": profile["label"], "modelId": model["modelId"],
                           "contextWindowTokens": model.get("contextWindowTokens"),
                           "inputModalities": "unknown" if model.get("inputModalities") is None else "image" if "image" in model["inputModalities"] else "text",
                           "supportsTools": "unknown" if model.get("supportsTools") is None else "yes" if model["supportsTools"] else "no"}}

    def query_models(self, request):
        with self._lock:
            items = [self._model_item(profile, model) for profile in self._saved() for model in profile["models"]]
        needle = str(request.get("search", "")).casefold()
        items = [item for item in items if needle in (item["values"]["profile"] + item["values"]["modelId"]).casefold()]
        start = int(request.get("cursor") or 0)
        limit = max(1, min(int(request.get("limit", 25)), 100))
        return {"items": items[start:start + limit], "nextCursor": str(start + limit) if start + limit < len(items) else None, "total": len(items)}

    def update_model(self, identity, values):
        with self._lock:
            try:
                profile_id, model_id = json.loads(identity)
            except (ValueError, TypeError):
                raise ProfileError("MODEL_REFERENCE_INVALID") from None
            profiles = self._saved()
            profile = next((item for item in profiles if item["profileId"] == profile_id), None)
            model = next((item for item in profile["models"] if item["modelId"] == model_id), None) if profile else None
            if model is None or self._model_item(profile, model)["itemId"] != identity:
                raise ProfileError("MODEL_REFERENCE_INVALID")
            modality, tools = values.get("inputModalities", "unknown"), values.get("supportsTools", "unknown")
            if modality not in {"unknown", "text", "image"} or tools not in {"unknown", "yes", "no"}:
                raise ProfileError("MODEL_CAPABILITIES_INVALID")
            model.update(contextWindowTokens=values.get("contextWindowTokens"),
                         inputModalities=None if modality == "unknown" else ["text", "image"] if modality == "image" else ["text"],
                         supportsTools=None if tools == "unknown" else tools == "yes")
            _models([model])
            self._write(profiles)
            return {**self._model_item(profile, model), "applicationState": "restart_required"}

    def register_settings(self):
        section = "connections"
        self._context.get("sakura.host.settings").register({"sectionId": section, "title": "模型服务", "order": 10,
            "fields": [
                {"key": "profileId", "label": "连接", "type": "select", "default": self._active[0]["profileId"] if self._active else "",
                 "options": [{"value": "", "label": "请选择"}, *[{"value": item["profileId"], "label": item["label"]} for item in self._active]]},
                {"key": "modelId", "label": "测试模型 ID", "type": "string", "default": ""},
                {"key": "probeStatus", "label": "测试结果", "type": "status", "placement": "section_header", "default": {"state": "neutral", "label": "尚未测试", "message": ""}}],
            "actions": [{"actionId": "applyProfiles", "label": "应用"}, {"actionId": "listModels", "label": "获取模型列表"},
                        {"actionId": "testConnection", "label": "测试连接"}, {"actionId": "cancelProbe", "label": "取消测试"}],
        }, load=self._load_actions, save=self._save_actions,
           actions={"applyProfiles": self._apply, "listModels": lambda values: self._start_probe(values, operation="list_models"),
                    "testConnection": lambda values: self._start_probe(values, operation="test_connection"), "cancelProbe": self._cancel_probe})
        self._context.get("sakura.host.settings.surface-v0").register(section, "providers")
        self._context.get("sakura.host.settings.collection-v0").register(section, {
            "collectionId": "profiles", "title": "连接配置", "scope": "global", "searchable": True,
            "description": "保存后重新加载模型服务生效。",
            "deleteConfirmation": "删除后，使用此连接的模型将不可用。",
            "columns": [{"key": "label", "label": "名称", "type": "string"},
                        {"key": "base_url", "label": "API 地址", "type": "string"},
                        {"key": "configured", "label": "已设置 API Key", "type": "boolean"}],
            "fields": [{"key": "label", "label": "名称", "type": "string", "required": True},
                       {"key": "base_url", "label": "API 地址", "type": "string", "required": True},
                       {"key": "credentialAction", "label": "API Key 操作", "type": "select", "default": "keep", "options": [
                           {"value": "keep", "label": "保留"}, {"value": "replace", "label": "替换"}, {"value": "clear", "label": "清除"}]},
                       {"key": "apiKey", "label": "新 API Key", "type": "password", "default": ""},
                       {"key": "models", "label": "模型 ID（每行一个）", "type": "string", "default": "", "maxLength": 16384},
                       {"key": "timeout_seconds", "label": "请求超时（秒）", "type": "integer", "default": 60, "minimum": 1, "maximum": 300}],
        }, query=self.query, create=self.create, update=self.update, delete=self.delete)
        self._context.get("sakura.host.settings.collection-v0").register(section, {
            "collectionId": "models", "title": "模型参数", "scope": "global", "searchable": True,
            "columns": [{"key": "profile", "label": "连接", "type": "string"}, {"key": "modelId", "label": "模型", "type": "string"}],
            "fields": [{"key": "profile", "label": "连接", "type": "readonly"},
                       {"key": "modelId", "label": "模型", "type": "readonly"},
                       {"key": "contextWindowTokens", "label": "上下文窗口（Token）", "type": "integer", "default": None, "minimum": 4096, "maximum": 2000000},
                       {"key": "inputModalities", "label": "输入能力", "type": "select", "default": "unknown", "options": [
                           {"value": "unknown", "label": "未知"}, {"value": "text", "label": "文本"}, {"value": "image", "label": "文本与图像"}]},
                       {"key": "supportsTools", "label": "工具调用", "type": "select", "default": "unknown", "options": [
                           {"value": "unknown", "label": "未知"}, {"value": "yes", "label": "支持"}, {"value": "no", "label": "不支持"}]}],
        }, query=self.query_models, update=self.update_model)
