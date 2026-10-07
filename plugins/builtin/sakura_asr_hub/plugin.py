from __future__ import annotations
from sakura_provider_errors import provider_failure

import re
import threading
import time
from collections import OrderedDict
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any
from pathlib import Path

try:
    from .input_controller import InputController, InputError
except ImportError:
    from input_controller import InputController, InputError

_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,199}$")
_CODE = re.compile(r"^[A-Z][A-Z0-9_]{0,79}$")
MAX_JOBS = 64


@dataclass
class Binding:
    descriptor: dict
    resource_id: str
    proxy: Any
    job_id: str | None = None
    terminal: dict | None = None
    consumed: bool = False
    started_at: float = field(default_factory=time.monotonic)


class SakuraASRHub:
    def __init__(self, context):
        self.context = context
        try:
            self.logger = context.get("sakura.host.logging")
        except Exception:
            self.logger = None
        self.audio = context.get("sakura.host.audio_input")
        self.providers: dict[str, dict] = {}
        self.jobs: OrderedDict[str, Binding] = OrderedDict()
        self.lock = threading.RLock()
        self.closed = False
        self.inputs = InputController(self)

    def _log(self, event, message, level="info", **fields):
        try:
            getattr(self.logger, level)(message, fields={"event": event, **fields})
        except Exception:
            pass

    def registerProvider(self, descriptor):
        if not isinstance(descriptor, Mapping) or set(descriptor) != {"providerId", "serviceKey", "label", "processingLocation"}:
            raise ValueError("ASR_PROVIDER_INVALID")
        if any(not isinstance(descriptor[k], str) or not _ID.fullmatch(descriptor[k]) for k in ("providerId", "serviceKey")):
            raise ValueError("ASR_PROVIDER_INVALID")
        if not isinstance(descriptor["label"], str) or not 1 <= len(descriptor["label"].strip()) <= 120 or descriptor["processingLocation"] not in ("local", "remote"):
            raise ValueError("ASR_PROVIDER_INVALID")
        if getattr(self.context, "caller_id", None) != descriptor["providerId"]:
            raise ValueError("ASR_PROVIDER_IDENTITY_INVALID")
        scope = self.audio.verifyProvider(descriptor["providerId"], descriptor["serviceKey"])["scopeId"]
        value = {**descriptor, "scopeId": scope}
        with self.lock:
            old = self.providers.get(value["providerId"])
            if old and old["scopeId"] == scope and old["serviceKey"] != value["serviceKey"]:
                raise ValueError("ASR_PROVIDER_CONFLICT")
            self.providers[value["providerId"]] = value
        if old != value:
            self._log("asr.provider.registered", "语音输入引擎已登记", provider_id=value["providerId"], service_key=value["serviceKey"])
        return {"registered": True, "providerId": value["providerId"]}

    def unregisterProvider(self, provider_id, service_key):
        if getattr(self.context, "caller_id", None) != provider_id:
            raise ValueError("ASR_PROVIDER_IDENTITY_INVALID")
        with self.lock:
            item = self.providers.get(provider_id)
            removed = bool(item and item["serviceKey"] == service_key)
            if removed:
                # Runtime authenticates this caller, including its draining process.
                # The Service is already removed when dispose effects run; reload
                # waits for that process to exit before starting the next scope.
                del self.providers[provider_id]
                for request_id, binding in list(self.jobs.items()):
                    if binding.descriptor == item and binding.terminal is None:
                        self.cancel(request_id)
        if removed:
            self._log("asr.provider.unregistered", "语音输入引擎已移除", provider_id=provider_id)
        return {"removed": removed}

    def _proxy(self, descriptor):
        proxy = self.context.bind(descriptor["serviceKey"])
        scope = self.audio.verifyProvider(descriptor["providerId"], descriptor["serviceKey"])["scopeId"]
        if scope != descriptor["scopeId"]:
            raise ValueError("ASR_PROVIDER_UNAVAILABLE")
        return proxy

    def _status(self, descriptor):
        try:
            value = self._proxy(descriptor).status()
            if not isinstance(value, Mapping):
                raise ValueError("ASR_PROVIDER_RESULT_INVALID")
            # Only public readiness fields may cross back into the input UI.
            return {**{k: v for k, v in descriptor.items() if k != "scopeId"},
                    **{k: value[k] for k in ("state", "available", "ready", "errorCode", "reasonCode", "configVersion", "language", "diagnostics") if k in value}}
        except Exception as error:
            self._log("asr.provider.status.failed", "语音识别引擎状态读取失败", "error", provider_id=descriptor["providerId"])
            return {**{k: v for k, v in descriptor.items() if k != "scopeId"}, "available": False, "state": "unavailable", **provider_failure("ASR_PROVIDER_UNAVAILABLE", error), "configVersion": None}

    def listProviders(self):
        with self.lock:
            descriptors = list(self.providers.values())
        return [self._status(item) for item in descriptors]

    def status(self, provider_id=None):
        config = self.context.config.get()
        selected = provider_id or config.get("selectedProviderId")
        with self.lock:
            descriptor = self.providers.get(selected)
        value = self._status(descriptor) if descriptor else {"providerId": selected, "serviceKey": None, "configVersion": None, "available": False, "state": "unavailable", "errorCode": "ASR_PROVIDER_UNAVAILABLE" if selected else "ASR_PROVIDER_NOT_SELECTED"}
        if selected and descriptor is None:
            value.update(provider_failure("ASR_PROVIDER_UNAVAILABLE", f"语音识别引擎 {selected} 未向 ASR Hub 注册"))
            value["stage"] = "provider_selection"
        # A provider being initialized can import the old Hub choice once before registering.
        language = value.get("language", "auto") if descriptor else config.get("language", "auto")
        return {**value, "selectedProviderId": selected, "language": language}

    def configure(self, values):
        if not isinstance(values, Mapping) or not set(values) <= {"selectedProviderId", "inputDeviceId"}:
            raise InputError("ASR_SELECTION_INVALID")
        selected = values.get("selectedProviderId")
        if "inputDeviceId" in values:
            InputController._device_id(values["inputDeviceId"])
        if selected is not None and (not isinstance(selected, str) or not _ID.fullmatch(selected)):
            raise InputError("ASR_SELECTION_INVALID")
        previous = self.context.config.get()
        self.context.config.update(dict(values))
        result = self.status()
        if any(previous.get(key) != value for key, value in values.items()):
            self._log("asr.selection.changed", "语音输入设置已更改", provider_id=result["providerId"])
        return result

    def warmup(self, provider_id=None):
        selected = provider_id or self.context.config.get().get("selectedProviderId")
        with self.lock:
            descriptor = self.providers.get(selected)
        if descriptor:
            self._proxy(descriptor).warmup()
        return self.status(selected)

    def begin(self, request):
        result = self._begin(request)
        ids = {field: request.get(key) if isinstance(request, Mapping) and isinstance(request.get(key), str) and _ID.fullmatch(request[key]) else None for key, field in (("requestId", "request_id"), ("providerId", "provider_id"))}
        result = {**result, "providerId": ids["provider_id"], "requestId": ids["request_id"]}
        if result["state"] == "running":
            self._log("asr.request.started", "语音识别请求已路由", **ids)
        else:
            self._log("asr.request.rejected", "语音识别请求未受理", "warning", **ids, error_code=result["errorCode"])
        return result

    def _begin(self, request):
        if not isinstance(request, Mapping):
            return self._failed("ASR_REQUEST_INVALID")
        request_id = request.get("requestId")
        provider_id = request.get("providerId")
        if not isinstance(request_id, str) or not _ID.fullmatch(request_id) or not isinstance(request.get("audio"), Mapping):
            return self._failed("ASR_REQUEST_INVALID")
        with self.lock:
            if request_id in self.jobs or any(item.terminal is None for item in self.jobs.values()):
                return self._failed("ASR_BUSY")
            self._prune()
            if len(self.jobs) >= MAX_JOBS:
                return self._failed("ASR_CAPACITY_EXCEEDED")
            descriptor = self.providers.get(provider_id)
            if descriptor is None:
                return {"stage": "provider_selection", **self._failed("ASR_PROVIDER_UNAVAILABLE", provider_failure(
                    "ASR_PROVIDER_UNAVAILABLE", f"语音识别引擎 {provider_id} 未向 ASR Hub 注册")["diagnostics"])}
            try:
                proxy = self._proxy(descriptor)
                status = proxy.status()
                if not status.get("available"):
                    return self._failed(status.get("errorCode", "ASR_PROVIDER_UNAVAILABLE"), status.get("diagnostics"))
                if status.get("configVersion") != request.get("configVersion"):
                    return self._failed("ASR_CONFIGURATION_CHANGED")
                audio = self.audio.authorize(dict(request["audio"]), descriptor["serviceKey"])
                binding = Binding(dict(descriptor), audio["resourceId"], proxy)
                self.jobs[request_id] = binding
                job_id = proxy.begin({"requestId": request_id, "audio": audio, "language": request.get("language", "auto"), "configVersion": request.get("configVersion")})
                if isinstance(job_id, str) and _ID.fullmatch(job_id):
                    binding.job_id = job_id
                    return {"state": "running", "requestId": request_id, "providerId": provider_id}
                failure = self._failed(job_id.get("errorCode", "ASR_JOB_INVALID"), job_id.get("diagnostics")) if isinstance(job_id, Mapping) else self._failed("ASR_JOB_INVALID")
            except Exception as error:
                failure = self._failed(str(getattr(error, "code", error)))
                failure.update(provider_failure(failure["errorCode"], error))
            binding = self.jobs.get(request_id)
            if binding is not None:
                binding.terminal = failure
                binding.consumed = True
                self.audio.revoke(binding.resource_id)
            return {**failure, "requestId": request_id, "providerId": provider_id}

    def poll(self, request_id):
        with self.lock:
            binding = self.jobs.get(request_id)
            if binding is None:
                return self._failed("ASR_JOB_NOT_FOUND")
            if binding.terminal is None:
                failure_reported = False
                try:
                    value = binding.proxy.poll(binding.job_id)
                    state = value.get("state") if isinstance(value, Mapping) else None
                    if state == "succeeded" and isinstance(value.get("text"), str) and value["text"].strip() and len(value["text"]) <= 65536 and (value.get("language") is None or isinstance(value.get("language"), str)):
                        result = {"state": state, "text": value["text"], "language": value.get("language")}
                    elif state in ("running", "cancelled"):
                        result = {"state": state}
                    else:
                        result = self._failed(value.get("errorCode", "ASR_JOB_RESULT_INVALID") if isinstance(value, Mapping) else "ASR_JOB_RESULT_INVALID", value.get("diagnostics") if isinstance(value, Mapping) else None)
                except Exception as error:
                    self._log("asr.request.failed", "语音识别请求失败", "error", request_id=request_id, provider_id=binding.descriptor["providerId"], error_code="ASR_PROVIDER_UNAVAILABLE")
                    failure_reported = True
                    result = self._failed("ASR_PROVIDER_UNAVAILABLE", provider_failure("ASR_PROVIDER_UNAVAILABLE", error)["diagnostics"])
                if result["state"] != "running":
                    binding.terminal = result
                    self.audio.revoke(binding.resource_id)
                    if not failure_reported:
                        self._log("asr.request." + result["state"], {"succeeded": "语音识别请求完成", "failed": "语音识别请求失败", "cancelled": "语音识别请求已取消"}[result["state"]], "error" if result["state"] == "failed" else "info", request_id=request_id, provider_id=binding.descriptor["providerId"], duration_ms=round((time.monotonic() - binding.started_at) * 1000), **({"error_code": result["errorCode"]} if result["state"] == "failed" else {}))
            result = binding.terminal or {"state": "running"}
            if binding.terminal:
                binding.consumed = True
            return {**result, "requestId": request_id, "providerId": binding.descriptor["providerId"]}

    def cancel(self, request_id):
        with self.lock:
            binding = self.jobs.get(request_id)
            if binding is None or binding.terminal is not None:
                return {"accepted": False}
            # Invalidate first. The Host keeps active reader leases until release/exit.
            binding.terminal = {"state": "cancelled"}
            binding.consumed = True
            self.audio.revoke(binding.resource_id)
            self._log("asr.request.cancelled", "语音识别请求已取消", request_id=request_id, provider_id=binding.descriptor["providerId"], duration_ms=round((time.monotonic() - binding.started_at) * 1000))
            try:
                binding.proxy.cancel(binding.job_id)
            except Exception:
                pass
            return {"accepted": True}

    def _prune(self):
        if len(self.jobs) >= MAX_JOBS:
            for key in list(self.jobs):
                if self.jobs[key].consumed:
                    del self.jobs[key]
                    if len(self.jobs) < MAX_JOBS:
                        break

    @staticmethod
    def _failed(code, diagnostics=None):
        return {"state": "failed", "errorCode": code if isinstance(code, str) and _CODE.fullmatch(code) else "ASR_PROVIDER_FAILED", **({"diagnostics": dict(diagnostics)} if isinstance(diagnostics, Mapping) else {})}

    def input(self, name, payload):
        if self.context.caller_id != "sakura.core":
            raise InputError("ASR_INPUT_UNAUTHORIZED")
        return self.inputs.dispatch(name, payload)

    def load_settings(self):
        values = self.context.config.get()
        if "inputDeviceId" not in values:
            device_id = self.audio.legacyDevice()
            self.context.config.update({"inputDeviceId": device_id})
            values["inputDeviceId"] = device_id
        return {"selectedProviderId": values.get("selectedProviderId") or "", "inputDeviceId": values["inputDeviceId"]}

    def save_settings(self, values):
        self.configure({**values, **({"selectedProviderId": None} if values.get("selectedProviderId") == "" else {})})
        return "applied"

    def close(self):
        if self.closed:
            return
        self.closed = True
        self.inputs.close()
        for request_id in list(self.jobs):
            self.cancel(request_id)
        self._log("asr.hub.stopped", "语音输入协调插件已退出")


class SakuraASRHubPlugin:
    def setup(self, context):
        hub = SakuraASRHub(context)
        context.provide("sakura.asr", hub, exports=("registerProvider", "unregisterProvider", "listProviders", "status", "configure", "warmup", "begin", "poll", "cancel", "input", "load_settings"))
        context.effect(hub.close)
        settings = context.get("sakura.host.settings")
        settings.register({
            "sectionId": "input", "title": "语音输入", "order": 80,
            "fields": [
                {"key": "selectedProviderId", "label": "识别引擎", "type": "string", "default": "", "readonly": True},
                {"key": "inputDeviceId", "label": "麦克风", "type": "string", "default": ""},
            ],
            "presentation": {"component": "module", "source": Path(__file__).with_name("frontend").joinpath("input.js").read_text(encoding="utf-8")},
        }, load=hub.load_settings, save=hub.save_settings)
        context.get("sakura.host.settings.surface-v0").register("input", "voice-input")
        hub._log("asr.hub.started", "语音输入协调插件已启用")
