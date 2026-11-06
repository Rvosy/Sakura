"""每条 Codex 连接分别保存账号、模型目录和登录任务。"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path
import threading
import time
import uuid
import webbrowser

from sakura_cancellation import OperationCancelled
from sakura_model import ModelError

if __package__:
    from .oauth import Account, DEVICE_URL
else:
    from oauth import Account, DEVICE_URL


SERVICE_KEY = "sakura.model.codex"


@dataclass
class AccountOperation:
    result: dict
    cancelled: threading.Event = field(default_factory=threading.Event)
    worker: threading.Thread | None = None


class CodexProfiles:
    def __init__(self, context):
        self.context = context
        self.lock = threading.RLock()
        self.active = context.config.get()
        self.accounts = {}
        self.operations = {}
        self.usage = {}
        self.service = None
        self.closed = False
        self.logger = context.get("sakura.host.logging")
        context.config.on_change(self.apply_config)
        if "profiles" not in self.active:
            # 原连接保留 chatgpt 身份和凭据路径，已有模型用途无需重新选择。
            legacy = self.active.get("added") or self.active.get("models") or context.data_path("auth.json").exists()
            profiles = [{"profileId": "chatgpt", "label": "OpenAI Official", "createdAt": 0,
                         "models": [{"modelId": model, "label": model} if isinstance(model, str) else model
                                    for model in self.active.get("models", [])]}] if legacy else []
            values = {key: value for key, value in self.active.items() if key not in {"added", "models"}}
            if context.config.replace({**values, "profiles": profiles}) == "error":
                raise ModelError("CONFIG_APPLY_FAILED", "Codex 连接配置未能应用，请重试。")

    def apply_config(self, values):
        with self.lock:
            self.active = deepcopy(values)
        return "applied"

    def set_service(self, service):
        self.service = service
        self.context.effect(self.close)

    def profile(self, profile_id):
        profile = next((item for item in self.active["profiles"] if item["profileId"] == profile_id), None)
        if profile is None:
            raise ModelError("MODEL_REFERENCE_INVALID", "此模型服务已不存在，请重新选择。")
        return profile

    def account_for(self, profile_id):
        with self.lock:
            self.profile(profile_id)
            if profile_id not in self.accounts:
                path = "auth.json" if profile_id == "chatgpt" else f"accounts/{profile_id}.json"
                self.accounts[profile_id] = Account(self.context.data_path(path))
            return self.accounts[profile_id]

    def catalog(self):
        with self.lock:
            return [{"profileId": p["profileId"], "label": p["label"], "models": deepcopy(p["models"])}
                    for p in self.active["profiles"]]

    def describe(self, profile_id, model_id):
        with self.lock:
            model = next((m for m in self.profile(profile_id)["models"] if m["modelId"] == model_id), None)
            if model is None:
                raise ModelError("MODEL_REFERENCE_INVALID", "此模型不在当前连接的目录中，请重新选择模型。")
            window = model.get("contextWindowTokens")
            return {"contextWindowTokens": window or 32768, "contextWindowSource": "provider" if window else "fallback",
                    "inputModalities": model.get("inputModalities"), "supportsTools": model.get("supportsTools")}

    def resolve(self, profile_id, model_id):
        return self.resolve_probe(profile_id, {"modelId": model_id})

    def resolve_probe(self, profile_id, values, require_model=False):
        with self.lock:
            model = values["modelId"] if require_model else values.get("modelId", "")
            timeout = values.get("timeout_seconds", self.active.get("timeout_seconds", 120))
            return {"profileId": profile_id, "model": model, "account": self.account_for(profile_id).snapshot(), "timeout_seconds": timeout}

    def load(self):
        return {"operationRequest": {}, **self.status({})["values"]}

    def update_config(self, patch):
        if self.context.config.update(patch) == "error":
            raise ModelError("CONFIG_APPLY_FAILED", "Codex 配置未能应用，请重试。")

    def add(self, values):
        with self.lock:
            profiles = deepcopy(self.active["profiles"])
            labels = {p["label"] for p in profiles}
            label, number = "OpenAI Official", 1
            while label in labels:
                number += 1
                label = f"OpenAI Official {number}"
            profiles.append({"profileId": uuid.uuid4().hex, "label": label, "models": [],
                             "createdAt": values.get("operationRequest", {}).get("createdAt", int(time.time() * 1000))})
            self.update_config({"profiles": profiles})
            return self.status({})

    def rename(self, values):
        request = values["operationRequest"]
        label = request["label"].strip()
        if not label:
            raise ModelError("FIELD_REQUIRED", "请填写模型服务名称。")
        with self.lock:
            self.profile(request["profileId"])
            self.update_config({"profiles": [dict(p, label=label) if p["profileId"] == request["profileId"] else p
                                             for p in self.active["profiles"]]})
            return self.status({})

    def remove(self, values):
        profile_id = values["operationRequest"]["profileId"]
        with self.lock:
            account = self.account_for(profile_id)
            self.update_config({"profiles": [p for p in self.active["profiles"] if p["profileId"] != profile_id]})
            operation = self.operations.pop(profile_id, None)
            if operation:
                operation.cancelled.set()
            account.close()
            account.logout()
            self.accounts.pop(profile_id, None)
            self.usage.pop(profile_id, None)
        if operation and operation.worker:
            operation.worker.join(2)
        return self.status({})

    def status(self, _values):
        with self.lock:
            profiles = []
            for profile in self.active["profiles"]:
                profile_id = profile["profileId"]
                operation = self.operations.get(profile_id)
                profiles.append({**deepcopy(profile), "account": self.account_for(profile_id).public(),
                                 "usage": deepcopy(self.usage.get(profile_id)),
                                 "operationResult": deepcopy(operation.result) if operation else {}})
            return {"values": {"profiles": profiles}}

    def start(self, values):
        request = values["operationRequest"]
        kind, identity, profile_id = request["operation"], request["requestId"], request["profileId"]
        with self.lock:
            if self.closed:
                raise OperationCancelled()
            account = self.account_for(profile_id)
            previous = self.operations.get(profile_id)
            if previous:
                previous.cancelled.set()
            operation = AccountOperation({"requestId": identity, "state": "running", "operation": kind})
            self.operations[profile_id] = operation

            def check():
                if operation.cancelled.is_set():
                    raise OperationCancelled()

            def publish(value):
                with self.lock:
                    check()
                    operation.result.update(value)

            def run():
                started = time.monotonic()
                fields = {"operation_id": identity, "profile_id": profile_id, "stage": kind}
                if kind == "test_connection":
                    fields["model_id"] = request["modelId"]
                self.logger.info("Codex 操作开始", fields=fields)
                failure = None
                settings = None
                try:
                    if kind == "login":
                        account.login(check, publish)
                        with self.lock:
                            self.usage.pop(profile_id, None)
                    settings = self.resolve_probe(profile_id, request, require_model=kind == "test_connection")
                    if kind == "usage":
                        response = self.service.execute(settings, {}, cancel_checker=check, progress=lambda _event: None, operation="usage")
                        with self.lock:
                            check()
                            self.usage[profile_id] = response["usage"]
                        result = {"state": "completed", "message": "额度已更新。"}
                    elif kind == "test_connection":
                        response = self.service.execute(settings, {}, cancel_checker=check, progress=lambda _event: None, operation="test_connection")
                        result = {"state": "completed", "message": "模型请求完成。", "modelId": settings["model"],
                                  "reply": response["message"]["content"]}
                    else:
                        publish({"userCode": None, "message": "正在获取全部可用模型…"})
                        self.logger.info("Codex 获取模型目录", fields={**fields, "stage": "models"})
                        response = self.service.execute(settings, {}, cancel_checker=check, progress=lambda _event: None, operation="list_models")
                        models = response["models"]
                        with self.lock:
                            check()
                            self.update_config({"profiles": [dict(p, models=models) if p["profileId"] == profile_id else p
                                                             for p in self.active["profiles"]]})
                        result = {"state": "completed", "message": f"已同步 {len(models)} 个模型。" if models else "账号暂无可用模型。"}
                except OperationCancelled:
                    result = {"state": "cancelled", "message": "已取消。"}
                except Exception as error:
                    tokens = settings["account"]["tokens"] if settings else {}
                    diagnostics = self.context.exception_diagnostics(error, secrets=tuple(tokens.get(k, "") for k in ("access_token", "refresh_token", "id_token")))
                    failure = diagnostics
                    result = {"state": "failed", "code": getattr(error, "code", "CODEX_OPERATION_FAILED"),
                              "message": diagnostics["diagnostic"], "diagnostics": diagnostics}
                with self.lock:
                    if operation.cancelled.is_set():
                        result = {"state": "cancelled", "message": "已取消。"}
                    result["elapsedMs"] = round((time.monotonic() - started) * 1000)
                    operation.result = {"requestId": identity, "operation": kind, **result}
                fields = {**fields, "state": result["state"], "elapsed_ms": result["elapsedMs"]}
                if failure and result["state"] != "cancelled":
                    self.logger.warning("Codex 操作失败", fields={**fields, **failure})
                else:
                    self.logger.info("Codex 操作结束", fields=fields)

            operation.worker = threading.Thread(target=run, name="codex-settings", daemon=True)
            operation.worker.start()
        return self.status({})

    def cancel(self, values):
        request = values.get("operationRequest", {})
        with self.lock:
            operation = self.operations.get(request.get("profileId"))
            if operation and request.get("requestId") == operation.result.get("requestId"):
                operation.cancelled.set()
        return {}

    def open_login(self, _values):
        if not webbrowser.open(DEVICE_URL):
            raise ModelError("CODEX_BROWSER_OPEN_FAILED", f"无法打开浏览器，请手动访问 {DEVICE_URL}。")
        return {}

    def logout(self, values):
        profile_id = values["operationRequest"]["profileId"]
        with self.lock:
            operation = self.operations.get(profile_id)
            if operation:
                operation.cancelled.set()
                operation.result = {}
            self.account_for(profile_id).logout()
            self.usage.pop(profile_id, None)
        self.logger.info("Codex 已退出登录", fields={"profile_id": profile_id})
        return self.status({})

    def close(self):
        with self.lock:
            self.closed = True
            for operation in self.operations.values():
                operation.cancelled.set()
            for account in self.accounts.values():
                account.close()
            workers = [operation.worker for operation in self.operations.values()]
        for worker in workers:
            if worker and worker is not threading.current_thread():
                worker.join(2)

    def register_settings(self):
        settings = self.context.get("sakura.host.settings")
        settings.register({"sectionId": "account", "title": "OpenAI Official", "order": 20,
            "presentation": {"component": "module", "standalone": True,
                             "source": Path(__file__).with_name("frontend").joinpath("account.js").read_text(encoding="utf-8")},
            "fields": [{"key": "profiles", "label": "连接", "type": "data", "default": [], "readonly": True},
                       {"key": "operationRequest", "label": "操作", "type": "data", "default": {}}],
            "actions": [{"actionId": key, "label": label} for key, label in (
                ("add", "添加模型服务"), ("rename", "修改名称"), ("remove", "删除模型服务"),
                ("start", "开始"), ("status", "查看状态"), ("cancel", "取消"), ("openLogin", "打开登录页面"), ("logout", "退出登录"))]},
            load=self.load, save=lambda _values: {"applicationState": "applied"},
            actions={"add": self.add, "rename": self.rename, "remove": self.remove, "start": self.start,
                     "status": self.status, "cancel": self.cancel, "openLogin": self.open_login, "logout": self.logout})
        settings.place("account", page_id="host:providers", order=20)
