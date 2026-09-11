"""Click-to-record input coordination; transcripts are draft results, never messages."""

from __future__ import annotations

import hmac
import re
import threading
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path
from time import monotonic
from typing import Callable, Mapping

from app.core_host.audio_input import AudioInputError
from app.config.settings_service import AppSettingsService
from app.core.runtime_log import log_message, diagnostic_attributes
from app.core_host.protocol import error_payload, response

ASR_REQUEST_NAMES = frozenset({
    "asr.input.prepare", "asr.input.poll", "asr.input.cancel", "asr.input.capture_target",
    "asr.input.capture_ready", "asr.input.capture_discarded", "asr.input.capture_status", "asr.input.submit",
    "asr.settings.get", "asr.settings.save", "asr.settings.action",
    "asr.input.availability",
})
_ACTIVE = {"preparing", "ready", "recording", "recognizing"}


@dataclass
class _Input:
    recording_id: str
    context: dict
    character_id: str
    application: object
    purpose: str = "draft"
    input_device_id: str = ""
    requested_provider_id: str | None = None
    started: float = field(default_factory=monotonic)
    logged_states: set[str] = field(default_factory=set)
    state: str = "preparing"
    provider_id: str = ""
    service_key: str = ""
    scope_id: str = ""
    hub_scope: dict = field(default_factory=dict)
    config_version: object = None
    language: str = "auto"
    resource_id: str = ""
    path: str = ""
    text: str = ""
    error_code: str = ""
    diagnostics: dict = field(default_factory=dict)
    submitted: bool = False
    capture_started: float | None = None
    cancelled: threading.Event = field(default_factory=threading.Event)
    changed: threading.Event = field(default_factory=threading.Event)
    worker: threading.Thread | None = None


class ASRBoundary:
    def __init__(self, generation_id: str, generation_credential: str, *,
                 user_root: Path, plugin_application_provider: Callable, character_presentation_provider: Callable) -> None:
        self._generation = generation_id
        self._credential = generation_credential
        self._application = plugin_application_provider
        self._character = character_presentation_provider
        self._settings_service = AppSettingsService(user_root)
        self._lock = threading.RLock()
        self._tasks: dict[str, _Input] = {}
        self._cancelled_ids: OrderedDict[str, None] = OrderedDict()
        self._closed = False

    def handle(self, request: dict) -> dict:
        try:
            credential = request.get("generationCredential")
            if (request.get("generationId") != self._generation or not isinstance(credential, str)
                    or not hmac.compare_digest(credential, self._credential) or self._closed):
                raise AudioInputError("STALE_GENERATION")
            payload = request.get("payload")
            if not isinstance(payload, Mapping):
                raise AudioInputError("ASR_REQUEST_INVALID")
            name = request.get("name")
            if name == "asr.input.availability":
                try:
                    self._app().service_identity("sakura.asr")
                    result = {"enabled": True}
                except Exception:
                    result = {"enabled": False}
            elif name == "asr.settings.get":
                result = self._settings()
            elif name == "asr.settings.save":
                values = payload.get("values", payload)
                if not isinstance(values, Mapping) or not set(values) <= {"inputDeviceId", "selectedProviderId"}:
                    raise AudioInputError("ASR_SELECTION_INVALID")
                values = dict(values)
                device_id = self._device_id(values.pop("inputDeviceId")) if "inputDeviceId" in values else None
                if values:
                    self._app().call_service("sakura.asr", "configure", values)
                if device_id is not None:
                    self._settings_service.save_audio_input_device(device_id)
                result = self._settings()
            elif name == "asr.settings.action":
                result = self._app().settings_action(payload["pluginId"], payload["sectionId"],
                                                     payload["actionId"], payload.get("values", {}))
            elif name == "asr.input.prepare":
                result = self._prepare(payload)
            else:
                if name in {"asr.input.cancel", "asr.input.capture_discarded"}:
                    recording_id = payload.get("recordingId")
                    if not isinstance(recording_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", recording_id):
                        raise AudioInputError("ASR_RECORDING_INVALID")
                    with self._lock:
                        self._cancelled_ids[recording_id] = None
                        while len(self._cancelled_ids) > 64:
                            self._cancelled_ids.popitem(last=False)
                        if recording_id not in self._tasks:
                            return self._response(request, payload={"recordingId": recording_id, "state": "cancelled"})
                task = self._task(payload.get("recordingId"))
                if name == "asr.input.cancel":
                    self._cancel(task)
                    result = self._snapshot(task)
                elif name == "asr.input.capture_discarded":
                    result = self._discard_capture(task, payload.get("errorCode"))
                elif name == "asr.input.capture_target":
                    with self._lock:
                        self._require_context(task)
                        if task.state != "ready" or task.resource_id:
                            raise AudioInputError("ASR_STATE_INVALID")
                        target = task.application.audio_input.allocate(task.recording_id, task.provider_id,
                                                                      task.service_key, task.scope_id)
                        task.resource_id, task.path = target["resourceId"], target["path"]
                        result = {"recordingId": task.recording_id, "path": task.path,
                                  "inputDeviceId": task.input_device_id}
                elif name == "asr.input.capture_ready":
                    with self._lock:
                        self._require_context(task)
                        if task.state != "ready" or not task.resource_id:
                            raise AudioInputError("ASR_STATE_INVALID")
                        task.state = "recording"
                        task.capture_started = monotonic()
                        task.changed.set()
                        result = self._snapshot(task)
                elif name == "asr.input.submit":
                    result = self._submit(task)
                elif name == "asr.input.poll":
                    with self._lock:
                        if task.state in _ACTIVE | {"succeeded"}:
                            self._require_context(task)
                        result = self._snapshot(task)
                        if task.state == "succeeded":
                            # One delivery opportunity per recording, even with concurrent polls.
                            task.state = "consumed"
                            task.text = ""
                elif name == "asr.input.capture_status":
                    with self._lock:
                        result = {"recordingId": task.recording_id, "state": task.state}
                        if task.error_code:
                            result["errorCode"] = task.error_code
                else:
                    raise AudioInputError("ASR_REQUEST_INVALID")
            return self._response(request, payload=result)
        except Exception as error:
            code = getattr(error, "code", "ASR_SERVICE_UNAVAILABLE")
            if not isinstance(code, str) or not re.fullmatch(r"[A-Z0-9_]{1,80}", code):
                code = "ASR_SERVICE_UNAVAILABLE"
            return self._response(request, error=error_payload(code, "语音输入未能完成，请检查插件中的语音输入设置后重试。"))

    def _prepare(self, payload: Mapping) -> dict:
        purpose = payload.get("purpose", "draft")
        if not isinstance(purpose, str) or purpose not in {"draft", "test"}:
            raise AudioInputError("ASR_CONTEXT_INVALID")
        if purpose != "test" and ("providerId" in payload or "inputDeviceId" in payload):
            raise AudioInputError("ASR_CONTEXT_INVALID")
        requested_provider = payload.get("providerId")
        if requested_provider is not None and (not isinstance(requested_provider, str)
                or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,199}", requested_provider)):
            raise AudioInputError("ASR_SELECTION_INVALID")
        device_id = self._device_id(payload.get("inputDeviceId", self._settings_service.load_audio_input_device()))
        recording_id = payload.get("recordingId")
        if not isinstance(recording_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", recording_id):
            raise AudioInputError("ASR_RECORDING_INVALID")
        context_id = payload.get("contextId")
        if not isinstance(context_id, str) or not 0 < len(context_id) <= 256:
            raise AudioInputError("ASR_CONTEXT_INVALID")
        context = {"contextId": context_id}
        for key in ("draftVersion", "selectionStart", "selectionEnd"):
            value = payload.get(key, 0)
            if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 2**53 - 1:
                raise AudioInputError("ASR_CONTEXT_INVALID")
            context[key] = value
        application = self._app()
        with self._lock:
            if self._closed:
                raise AudioInputError("STALE_GENERATION")
            if recording_id in self._cancelled_ids:
                raise AudioInputError("ASR_CANCELLED")
            if recording_id in self._tasks:
                raise AudioInputError("ASR_RECORDING_REUSED")
            if any(task.state in _ACTIVE for task in self._tasks.values()):
                raise AudioInputError("ASR_BUSY")
            if len(self._tasks) >= 32:
                removable = next((key for key, task in self._tasks.items()
                                  if task.state not in _ACTIVE and (task.worker is None or not task.worker.is_alive())), None)
                if removable is None:
                    raise AudioInputError("ASR_BUSY")
                self._tasks.pop(removable)
            task = _Input(recording_id, context, self._character_id(), application,
                          purpose=purpose, input_device_id=device_id, requested_provider_id=requested_provider)
            self._tasks[recording_id] = task
            self._log_state(task, "preparing")
            task.worker = threading.Thread(target=self._run, args=(task,), name="sakura-asr-input", daemon=True)
            task.worker.start()
            return self._snapshot(task)

    def _run(self, task: _Input) -> None:
        call = task.application.call_service
        try:
            status = (call("sakura.asr", "status", task.requested_provider_id) if task.requested_provider_id
                      else call("sakura.asr", "status"))
            if not isinstance(status, Mapping) or not status.get("providerId") or not status.get("serviceKey"):
                raise AudioInputError(status.get("reasonCode", status.get("errorCode", "ASR_PROVIDER_NOT_SELECTED")) if isinstance(status, Mapping) else "ASR_PROVIDER_UNAVAILABLE")
            if task.requested_provider_id and status["providerId"] != task.requested_provider_id:
                raise AudioInputError("ASR_PROVIDER_IDENTITY_INVALID")
            with self._lock:
                self._require_context(task)
                task.provider_id = status["providerId"]
                task.service_key = status["serviceKey"]
                task.config_version = status.get("configVersion")
                task.language = status.get("language", "auto")
                identity = task.application.service_identity(task.service_key)
                if identity["providerId"] != task.provider_id:
                    raise AudioInputError("ASR_PROVIDER_IDENTITY_INVALID")
                task.scope_id = identity["scopeId"]
                task.hub_scope = task.application.service_identity("sakura.asr")
            call("sakura.asr", "warmup", task.provider_id)
            deadline = monotonic() + 120
            while True:
                self._require_context(task)
                self._require_binding(task)
                status = call("sakura.asr", "status", task.provider_id)
                if status.get("configVersion") != task.config_version:
                    raise AudioInputError("ASR_PROVIDER_CONFIGURATION_CHANGED")
                if status.get("state") == "ready" and status.get("available"):
                    break
                if status.get("state") not in {"preparing", "loading", "warming"}:
                    raise AudioInputError(status.get("reasonCode", status.get("errorCode", "ASR_PROVIDER_UNAVAILABLE")))
                if monotonic() >= deadline:
                    raise AudioInputError("ASR_PREPARE_TIMEOUT")
                task.cancelled.wait(0.1)
            with self._lock:
                self._require_context(task)
                task.state = "ready"
                self._log_state(task, "ready")
            deadline = monotonic() + 120
            while not task.submitted:
                self._require_context(task)
                self._require_binding(task)
                capture_deadline = task.capture_started + 90 if task.capture_started is not None else deadline
                if monotonic() >= capture_deadline:
                    raise AudioInputError("ASR_CAPTURE_TIMEOUT")
                task.changed.wait(0.1)
                task.changed.clear()
            self._require_context(task)
            audio = task.application.audio_input.commit(task.resource_id)
            self._require_context(task)
            started = call("sakura.asr", "begin", {"requestId": task.recording_id, "providerId": task.provider_id,
                                                   "configVersion": task.config_version, "language": task.language,
                                                   "audio": audio})
            if started.get("state") != "running":
                raise AudioInputError(started.get("errorCode", "ASR_PROVIDER_UNAVAILABLE"))
            deadline = monotonic() + 180
            while True:
                self._require_context(task)
                self._require_binding(task)
                result = call("sakura.asr", "poll", task.recording_id)
                if result.get("requestId") != task.recording_id or result.get("providerId") != task.provider_id:
                    raise AudioInputError("ASR_RESULT_INVALID")
                state = result.get("state")
                if state == "succeeded":
                    text = result.get("text")
                    if not isinstance(text, str) or not text.strip() or len(text) > 32000:
                        raise AudioInputError("ASR_NO_SPEECH")
                    with self._lock:
                        self._require_context(task)
                        task.text = text.strip()
                        task.state = "succeeded"
                        self._log_state(task, "succeeded")
                    break
                if state != "running":
                    raise AudioInputError(result.get("errorCode", "ASR_CANCELLED" if state == "cancelled" else "ASR_RESULT_INVALID"))
                if monotonic() >= deadline:
                    raise AudioInputError("ASR_RECOGNITION_TIMEOUT")
                task.cancelled.wait(0.1)
        except Exception as error:
            with self._lock:
                if task.state not in {"cancelled", "consumed", "failed"}:
                    failed_stage = task.state
                    task.state = "failed"
                    task.error_code = getattr(error, "code", "ASR_PROVIDER_UNAVAILABLE")
                    if not isinstance(task.error_code, str) or not re.fullmatch(r"[A-Z0-9_]{1,80}", task.error_code):
                        task.error_code = "ASR_PROVIDER_UNAVAILABLE"
                    task.diagnostics = diagnostic_attributes(error, reason_code=task.error_code, stage=failed_stage)
        finally:
            with self._lock:
                if "succeeded" not in task.logged_states:
                    self._log_state(task, task.state)
            if task.resource_id:
                task.application.audio_input.revoke_resource(task.resource_id)
                # Once submit transfers the completed producer, cancellation
                # before commit must also relinquish that producer reservation.
                if task.submitted:
                    task.application.audio_input.producer_done(task.resource_id)
            if task.state not in {"succeeded", "consumed"}:
                try:
                    call("sakura.asr", "cancel", task.recording_id)
                except Exception:
                    pass

    def _submit(self, task: _Input) -> dict:
        with self._lock:
            if task.state != "recording":
                if task.resource_id and not task.submitted and task.state in {"cancelled", "failed"}:
                    task.application.audio_input.producer_done(task.resource_id)
                raise AudioInputError("ASR_STATE_INVALID")
            self._require_context(task)
            task.state = "recognizing"
            self._log_state(task, "recognizing")
            task.submitted = True
            task.changed.set()
            return self._snapshot(task)

    def _discard_capture(self, task: _Input, error_code: object) -> dict:
        if error_code is not None and (not isinstance(error_code, str)
                or not re.fullmatch(r"[A-Z][A-Z0-9_]{0,79}", error_code)):
            raise AudioInputError("ASR_REQUEST_INVALID")
        with self._lock:
            if error_code and error_code != "ASR_CANCELLED" and task.state in _ACTIVE:
                task.state = "failed"
                task.error_code = error_code
                self._log_state(task, "failed")
            self._cancel(task)
            if task.resource_id:
                task.application.audio_input.producer_done(task.resource_id)
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
            if previous in _ACTIVE | {"succeeded"}:
                self._log_state(task, "cancelled")
            if task.resource_id:
                task.application.audio_input.revoke_resource(task.resource_id)

    def cancel_all(self) -> None:
        with self._lock:
            for task in self._tasks.values():
                self._cancel(task)

    def close(self) -> None:
        with self._lock:
            self._closed = True
        self.cancel_all()
        deadline = monotonic() + 3
        for task in tuple(self._tasks.values()):
            if task.worker is not None:
                task.worker.join(max(0, deadline - monotonic()))

    def _settings(self) -> dict:
        app = self._app()
        hub_plugin_id = None
        try:
            hub_plugin_id = app.service_identity("sakura.asr")["providerId"]
            status = app.call_service("sakura.asr", "status")
            providers = app.call_service("sakura.asr", "listProviders")
        except Exception:
            status = {"state": "unavailable", "available": False, "errorCode": "ASR_SERVICE_UNAVAILABLE"}
            providers = []
        return {"schemaVersion": 1, **dict(status), "selectedProviderId": status.get("providerId"),
                "inputDeviceId": self._settings_service.load_audio_input_device(),
                "hubPluginId": hub_plugin_id, "providers": providers,
                "sections": app.settings_sections("voice-input")}

    def _log_state(self, task: _Input, state: str) -> None:
        # State changes only: polling, audio levels, paths and draft contents are not logs.
        messages = {"preparing": "语音输入开始准备", "ready": "语音输入准备完成",
                    "recognizing": "录音已提交识别", "succeeded": "语音识别结果已就绪",
                    "failed": "语音输入失败", "cancelled": "语音输入已取消"}
        if state not in messages or state in task.logged_states:
            return
        task.logged_states.add(state)
        fields = {"event": f"asr.input.{state}", "recording_id": task.recording_id,
                  "purpose": task.purpose, "provider_id": task.provider_id or task.requested_provider_id,
                  "elapsed_ms": max(0, round((monotonic() - task.started) * 1000))}
        if state == "failed":
            fields["reason_code"] = task.error_code
            fields.update(task.diagnostics)
        log_message("warning" if state == "failed" else "info", messages[state], fields=fields, component="core")

    def _require_binding(self, task: _Input) -> None:
        if (task.application.service_identity(task.service_key) != {"providerId": task.provider_id, "scopeId": task.scope_id}
                or task.application.service_identity("sakura.asr") != task.hub_scope):
            raise AudioInputError("ASR_PROVIDER_UNAVAILABLE")

    def _require_context(self, task: _Input) -> None:
        if (self._closed or task.cancelled.is_set()
                or (task.purpose == "draft" and self._character_id() != task.character_id)):
            self._cancel(task)
            raise AudioInputError("ASR_CANCELLED")

    def _character_id(self) -> str:
        value = self._character()
        return str(value.get("characterId", "")) if isinstance(value, Mapping) else ""

    def _app(self):
        value = self._application()
        if value is None:
            raise AudioInputError("ASR_SERVICE_UNAVAILABLE")
        return value

    def _task(self, recording_id: str) -> _Input:
        with self._lock:
            task = self._tasks.get(recording_id) if isinstance(recording_id, str) else None
            if task is None:
                raise AudioInputError("ASR_RECORDING_NOT_FOUND")
            return task

    def _snapshot(self, task: _Input) -> dict:
        value = {"recordingId": task.recording_id, "state": task.state, **task.context,
                 "purpose": task.purpose,
                 "coreGenerationId": self._generation, "providerId": task.provider_id or None}
        if task.state == "succeeded":
            value["text"] = task.text
        if task.error_code:
            value["errorCode"] = task.error_code
        return value

    @staticmethod
    def _device_id(value: object) -> str:
        if not isinstance(value, str) or len(value) > 4096 or any(ord(char) < 32 for char in value):
            raise AudioInputError("ASR_INPUT_DEVICE_INVALID")
        return value

    def _response(self, request: dict, **kwargs) -> dict:
        return response(request, generation_id=self._generation, generation_credential=self._credential,
                        protocol_minor=int(request.get("protocolMinor", 2)), **kwargs)
