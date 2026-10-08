"""Input preparation, capture and recognition owned by the ASR core plugin."""
from __future__ import annotations

import re
import threading
from itertools import count
from collections import OrderedDict
from collections.abc import Mapping
from dataclasses import dataclass, field
from time import monotonic
from sakura_provider_errors import provider_failure

_ACTIVE = {"preparing", "ready", "recording", "recognizing"}
_BINDING_UNAVAILABLE = {"SERVICE_MISSING", "SERVICE_BINDING_EXPIRED", "PLUGIN_PROCESS_UNAVAILABLE", "GENERATION_INVALIDATED"}

class InputError(RuntimeError):
    def __init__(self, code, diagnostics=None):
        super().__init__(code)
        self.code = code
        self.diagnostics = diagnostics or {}

@dataclass
class _Input:
    recording_id: str
    purpose: str = "draft"
    input_device_id: str = ""
    requested_provider_id: str | None = None
    started: float = field(default_factory=monotonic)
    logged_states: set[str] = field(default_factory=set)
    state: str = "preparing"
    provider_id: str = ""
    service_key: str = ""
    scope_id: str = ""
    config_version: object = None
    language: str = "auto"
    resource_id: str = ""
    path: str = ""
    text: str = ""
    error_code: str = ""
    diagnostics: dict = field(default_factory=dict)
    submitted: bool = False
    cancelled: threading.Event = field(default_factory=threading.Event)
    changed: threading.Event = field(default_factory=threading.Event)
    worker: threading.Thread | None = None


class InputController:
    def __init__(self, hub):
        self.hub = hub
        self.audio = hub.audio
        self._lock = threading.RLock()
        self._tasks = {}
        self._cancelled_ids = OrderedDict()
        self._closed = False
        self._snapshots = count()

    def dispatch(self, name, payload):
        if name == "prepare":
            return self._prepare(payload)
        recording_id = payload.get("recordingId")
        if name in {"cancel", "capture_discarded"}:
            with self._lock:
                self._cancelled_ids[recording_id] = None
                while len(self._cancelled_ids) > 64:
                    self._cancelled_ids.popitem(last=False)
                if recording_id not in self._tasks:
                    return {"recordingId": recording_id, "state": "cancelled"}
        task = self._task(recording_id)
        if name == "cancel":
            self._cancel(task)
            return self._snapshot(task)
        if name == "capture_discarded":
            return self._discard_capture(task, payload.get("errorCode"), payload.get("diagnostic"))
        if name == "capture_target":
            with self._lock:
                self._require_context(task)
                if task.state != "ready" or task.resource_id:
                    raise InputError("ASR_STATE_INVALID")
                target = self.audio.create(task.recording_id, task.provider_id, task.service_key, task.scope_id)
                task.resource_id, task.path = target["resourceId"], target["path"]
                return {"recordingId": task.recording_id, "resourceId": task.resource_id, "path": task.path, "inputDeviceId": task.input_device_id}
        if name == "capture_ready":
            with self._lock:
                self._require_context(task)
                if task.state != "ready" or not task.resource_id:
                    raise InputError("ASR_STATE_INVALID")
                task.state = "recording"
                task.changed.set()
                return self._snapshot(task)
        if name == "submit":
            return self._submit(task)
        if name in {"poll", "capture_status"}:
            with self._lock:
                if task.state in _ACTIVE | {"succeeded"}:
                    try:
                        self._require_binding(task)
                    except Exception as error:
                        task.state, task.error_code, task.text = "failed", "ASR_PROVIDER_UNAVAILABLE", ""
                        task.diagnostics = provider_failure(task.error_code, error)["diagnostics"]
                        task.cancelled.set()
                        task.changed.set()
                return self._snapshot(task)
        raise InputError("ASR_REQUEST_INVALID")

    def _prepare(self, payload: Mapping) -> dict:
        purpose = payload.get("purpose", "draft")
        if not isinstance(purpose, str) or purpose not in {"draft", "test"}:
            raise InputError("ASR_CONTEXT_INVALID")
        if purpose != "test" and ("providerId" in payload or "inputDeviceId" in payload):
            raise InputError("ASR_CONTEXT_INVALID")
        requested_provider = payload.get("providerId")
        if requested_provider is not None and (not isinstance(requested_provider, str)
                or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,199}", requested_provider)):
            raise InputError("ASR_SELECTION_INVALID")
        device_id = self._device_id(payload.get("inputDeviceId", self.hub.load_settings()["inputDeviceId"]))
        recording_id = payload.get("recordingId")
        if not isinstance(recording_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", recording_id):
            raise InputError("ASR_RECORDING_INVALID")
        with self._lock:
            if self._closed:
                raise InputError("STALE_GENERATION")
            if recording_id in self._cancelled_ids:
                raise InputError("ASR_CANCELLED")
            if recording_id in self._tasks:
                raise InputError("ASR_RECORDING_REUSED")
            if any(task.state in _ACTIVE for task in self._tasks.values()):
                raise InputError("ASR_BUSY")
            if len(self._tasks) >= 32:
                removable = next((key for key, task in self._tasks.items()
                                  if task.state not in _ACTIVE and (task.worker is None or not task.worker.is_alive())), None)
                if removable is None:
                    raise InputError("ASR_BUSY")
                self._tasks.pop(removable)
            task = _Input(recording_id,
                          purpose=purpose, input_device_id=device_id, requested_provider_id=requested_provider)
            self._tasks[recording_id] = task
            self._log_state(task, "preparing")
            task.worker = threading.Thread(target=self._run, args=(task,), name="sakura-asr-input", daemon=True)
            task.worker.start()
            return self._snapshot(task)

    def _run(self, task: _Input) -> None:
        call = lambda method, *args: getattr(self.hub, method)(*args)
        try:
            with self._lock:
                self._require_context(task)
            status = (call("status", task.requested_provider_id) if task.requested_provider_id
                      else call("status"))
            if not isinstance(status, Mapping) or not status.get("providerId") or not status.get("serviceKey"):
                raise InputError(status.get("reasonCode", status.get("errorCode", "ASR_PROVIDER_NOT_SELECTED")) if isinstance(status, Mapping) else "ASR_PROVIDER_UNAVAILABLE", status.get("diagnostics") if isinstance(status, Mapping) else None)
            if task.requested_provider_id and status["providerId"] != task.requested_provider_id:
                raise InputError("ASR_PROVIDER_IDENTITY_INVALID")
            with self._lock:
                self._require_context(task)
                task.provider_id = status["providerId"]
                task.service_key = status["serviceKey"]
                task.config_version = status.get("configVersion")
                task.language = status.get("language", "auto")
                identity = self.audio.verifyProvider(task.provider_id, task.service_key)
                if identity["providerId"] != task.provider_id:
                    raise InputError("ASR_PROVIDER_IDENTITY_INVALID")
                task.scope_id = identity["scopeId"]
            call("warmup", task.provider_id)
            while True:
                self._require_context(task)
                self._require_binding(task)
                status = call("status", task.provider_id)
                if status.get("configVersion") != task.config_version:
                    raise InputError("ASR_PROVIDER_CONFIGURATION_CHANGED")
                if status.get("state") == "ready" and status.get("available"):
                    break
                if status.get("state") not in {"preparing", "loading", "warming"}:
                    raise InputError(status.get("reasonCode", status.get("errorCode", "ASR_PROVIDER_UNAVAILABLE")), status.get("diagnostics"))
                task.cancelled.wait(0.1)
            with self._lock:
                self._require_context(task)
                task.state = "ready"
                self._log_state(task, "ready")
            while not task.submitted:
                self._require_context(task)
                self._require_binding(task)
                task.changed.wait(0.1)
                task.changed.clear()
            self._require_context(task)
            audio = self.audio.finish(task.resource_id)
            self._require_context(task)
            started = call("begin", {"requestId": task.recording_id, "providerId": task.provider_id,
                                                   "configVersion": task.config_version, "language": task.language,
                                                   "audio": audio})
            if started.get("state") != "running":
                raise InputError(started.get("errorCode", "ASR_PROVIDER_UNAVAILABLE"), started.get("diagnostics"))
            while True:
                self._require_context(task)
                self._require_binding(task)
                result = call("poll", task.recording_id)
                if result.get("requestId") != task.recording_id or result.get("providerId") != task.provider_id:
                    raise InputError("ASR_RESULT_INVALID")
                state = result.get("state")
                if state == "succeeded":
                    text = result.get("text")
                    if not isinstance(text, str) or not text.strip():
                        raise InputError("ASR_NO_SPEECH")
                    with self._lock:
                        self._require_context(task)
                        self._require_binding(task)
                        task.text, task.state = text.strip(), "succeeded"
                        self._log_state(task, "succeeded")
                    break
                if state != "running":
                    raise InputError(result.get("errorCode", "ASR_CANCELLED" if state == "cancelled" else "ASR_RESULT_INVALID"), result.get("diagnostics"))
                task.cancelled.wait(0.1)
        except Exception as error:
            with self._lock:
                if task.state not in {"cancelled", "consumed", "failed"}:
                    failed_stage = task.state
                    task.state = "failed"
                    task.error_code = getattr(error, "code", "ASR_PROVIDER_UNAVAILABLE")
                    if not isinstance(task.error_code, str) or not re.fullmatch(r"[A-Z0-9_]{1,80}", task.error_code):
                        task.error_code = "ASR_PROVIDER_UNAVAILABLE"
                    elif task.error_code in _BINDING_UNAVAILABLE:
                        task.error_code = "ASR_PROVIDER_UNAVAILABLE"
                    task.diagnostics = {**provider_failure(task.error_code, error)["diagnostics"], "stage": failed_stage}
        finally:
            with self._lock:
                if "succeeded" not in task.logged_states:
                    self._log_state(task, task.state)
            if task.resource_id:
                self.audio.revoke(task.resource_id)
                # Once submit transfers the completed producer, cancellation
                # before commit must also relinquish that producer reservation.
                if task.submitted:
                    self.audio.producerDone(task.resource_id)
            if task.state not in {"succeeded", "consumed"}:
                try:
                    call("cancel", task.recording_id)
                except Exception:
                    pass

    def _submit(self, task: _Input) -> dict:
        with self._lock:
            if task.state != "recording":
                if task.resource_id and not task.submitted and task.state in {"cancelled", "failed"}:
                    self.audio.producerDone(task.resource_id)
                raise InputError("ASR_STATE_INVALID")
            self._require_context(task)
            task.state = "recognizing"
            self._log_state(task, "recognizing")
            task.submitted = True
            task.changed.set()
            return self._snapshot(task)

    def _discard_capture(self, task: _Input, error_code: object, diagnostic: object = None) -> dict:
        if error_code is not None and (not isinstance(error_code, str)
                or not re.fullmatch(r"[A-Z][A-Z0-9_]{0,79}", error_code)):
            raise InputError("ASR_REQUEST_INVALID")
        with self._lock:
            if error_code and error_code != "ASR_CANCELLED" and task.state in _ACTIVE:
                task.state = "failed"
                task.error_code = error_code
                if isinstance(diagnostic, str):
                    task.diagnostics = provider_failure(error_code, diagnostic)["diagnostics"]
                self._log_state(task, "failed")
            self._cancel(task)
            if task.resource_id:
                self.audio.producerDone(task.resource_id)
            return self._snapshot(task)

    def _cancel(self, task: _Input) -> None:
        with self._lock:
            previous = task.state
            task.cancelled.set()
            task.changed.set()
            task.text = ""
            # Cleanup and late cancellation must not turn a failure into a silent cancel.
            if previous != "failed":
                task.state = "cancelled"
            if previous in _ACTIVE:
                self._log_state(task, "cancelled")
            if task.resource_id:
                self.audio.revoke(task.resource_id)

    def close(self):
        with self._lock:
            self._closed = True
            tasks = tuple(self._tasks.values())
        for task in tasks:
            self._cancel(task)
        deadline = monotonic() + 3
        for task in tasks:
            if task.worker:
                task.worker.join(max(0, deadline - monotonic()))

    def _require_binding(self, task):
        if task.service_key and self.audio.verifyProvider(task.provider_id, task.service_key) != {"providerId": task.provider_id, "scopeId": task.scope_id}:
            raise InputError("ASR_PROVIDER_UNAVAILABLE")

    def _require_context(self, task):
        if self._closed or task.cancelled.is_set():
            raise InputError("ASR_CANCELLED")

    def _log_state(self, task, state):
        if state in task.logged_states:
            return
        messages = {"preparing": "语音输入开始准备", "ready": "语音输入准备完成", "recognizing": "录音已提交识别",
                    "succeeded": "语音识别结果已就绪", "failed": "语音输入失败", "cancelled": "语音输入已取消"}
        if state not in messages:
            return
        task.logged_states.add(state)
        self.hub._log("asr.input." + state, messages[state], "error" if state == "failed" else "info",
                      recording_id=task.recording_id, purpose=task.purpose, provider_id=task.provider_id,
                      **({"error_code": task.error_code, **task.diagnostics} if state == "failed" else {}))

    def _task(self, recording_id):
        with self._lock:
            task = self._tasks.get(recording_id)
            if task is None:
                raise InputError("ASR_RECORDING_NOT_FOUND")
            return task

    def _snapshot(self, task):
        result = {"recordingId": task.recording_id, "state": task.state, "purpose": task.purpose, "revision": next(self._snapshots),
                  "language": task.language, "inputDeviceId": task.input_device_id,
                  "providerId": task.provider_id or None, "serviceKey": task.service_key, "scopeId": task.scope_id}
        if task.state == "succeeded":
            result["text"] = task.text
        if task.error_code:
            result.update(errorCode=task.error_code, diagnostics=dict(task.diagnostics))
        return result

    @staticmethod
    def _device_id(value):
        if not isinstance(value, str) or len(value) > 4096 or any(ord(char) < 32 for char in value):
            raise InputError("ASR_INPUT_DEVICE_INVALID")
        return value
