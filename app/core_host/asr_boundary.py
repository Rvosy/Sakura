"""Click-to-record input coordination; transcripts are draft results, never messages."""

from __future__ import annotations

import hmac
import re
import threading
from collections import OrderedDict
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from time import monotonic
from typing import Callable, Mapping

from app.core_host.audio_input import AudioInputError
from app.core.runtime_log import diagnostic_attributes
from app.core_host.protocol import error_payload, response

ASR_REQUEST_NAMES = frozenset({
    "asr.input.prepare", "asr.input.poll", "asr.input.cancel", "asr.input.capture_target",
    "asr.input.capture_ready", "asr.input.capture_discarded", "asr.input.capture_status", "asr.input.submit",
    "asr.settings.get", "asr.settings.save",
    "asr.input.availability",
})
_ACTIVE = {"preparing", "ready", "recording", "recognizing"}
_BINDING_UNAVAILABLE = {"SERVICE_MISSING", "SERVICE_BINDING_EXPIRED", "PLUGIN_PROCESS_UNAVAILABLE", "GENERATION_INVALIDATED"}


@dataclass
class _Input:
    recording_id: str
    context: dict
    character_id: str
    application: object
    purpose: str = "draft"
    revision: int = -1
    state: str = "preparing"
    provider_id: str = ""
    service_key: str = ""
    scope_id: str = ""
    hub_scope: dict = field(default_factory=dict)
    resource_id: str = ""
    text: str = ""
    error_code: str = ""
    diagnostics: dict = field(default_factory=dict)
    cancelled: threading.Event = field(default_factory=threading.Event)
    worker: threading.Thread | None = None


class ASRBoundary:
    def __init__(self, generation_id: str, generation_credential: str, *,
                 user_root: Path, plugin_application_provider: Callable, character_presentation_provider: Callable) -> None:
        self._generation = generation_id
        self._credential = generation_credential
        self._application = plugin_application_provider
        self._character = character_presentation_provider
        self._lock = threading.RLock()
        self._tasks: dict[str, _Input] = {}
        self._cancelled_ids: OrderedDict[str, None] = OrderedDict()
        self._closed = False
        self._switching_character = False

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
                except Exception as error:
                    result = {"enabled": False, "diagnostics": diagnostic_attributes(
                        error, reason_code="ASR_SERVICE_UNAVAILABLE", stage="asr.availability")}
            elif name == "asr.settings.get":
                result = self._settings()
            elif name == "asr.settings.save":
                self._app().call_service("sakura.asr", "configure", payload.get("values", payload))
                result = self._settings()
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
                    result = self._discard_capture(task, payload.get("errorCode"), payload.get("diagnostic"))
                elif name in {"asr.input.capture_target", "asr.input.capture_ready", "asr.input.submit"}:
                    with self._lock:
                        self._require_context(task)
                    result = self._call_hub(task, "input", name.removeprefix("asr.input."), dict(payload))
                    if name == "asr.input.capture_target":
                        task.resource_id = result.pop("resourceId")
                    else:
                        self._accept_snapshot(task, result)
                        result = self._snapshot(task)
                elif name == "asr.input.poll":
                    with self._lock:
                        if task.state in _ACTIVE | {"succeeded"}:
                            self._require_context(task)
                        result = self._deliver_transcript(task) if task.state == "succeeded" else self._snapshot(task)
                elif name == "asr.input.capture_status":
                    with self._lock:
                        result = {"recordingId": task.recording_id, "state": task.state}
                        if task.error_code:
                            result["errorCode"] = task.error_code
                            result["diagnostics"] = dict(task.diagnostics)
                else:
                    raise AudioInputError("ASR_REQUEST_INVALID")
            return self._response(request, payload=result)
        except Exception as error:
            code = getattr(error, "code", "ASR_SERVICE_UNAVAILABLE")
            if code == "PLUGIN_CALL_FAILED" and re.fullmatch(r"ASR_[A-Z0-9_]+", str(error)):
                code = str(error)
            if not isinstance(code, str) or not re.fullmatch(r"[A-Z0-9_]{1,80}", code):
                code = "ASR_SERVICE_UNAVAILABLE"
            return self._response(request, error=error_payload(code, "语音输入未能完成，请检查插件中的语音输入设置后重试。"))

    def _prepare(self, payload: Mapping) -> dict:
        purpose = payload.get("purpose", "draft")
        if not isinstance(purpose, str) or purpose not in {"draft", "test"}:
            raise AudioInputError("ASR_CONTEXT_INVALID")
        if purpose != "test" and ("providerId" in payload or "inputDeviceId" in payload):
            raise AudioInputError("ASR_CONTEXT_INVALID")
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
            if self._switching_character:
                raise AudioInputError("ASR_BUSY")
            if recording_id in self._cancelled_ids:
                raise AudioInputError("ASR_CANCELLED")
            if recording_id in self._tasks:
                raise AudioInputError("ASR_RECORDING_REUSED")
            if len(self._tasks) >= 32:
                removable = next((key for key, task in self._tasks.items()
                                  if task.state not in _ACTIVE and (task.worker is None or not task.worker.is_alive())), None)
                if removable is None:
                    raise AudioInputError("ASR_BUSY")
                self._tasks.pop(removable)
            task = _Input(recording_id, context, self._character_id(), application,
                          purpose=purpose)
            self._tasks[recording_id] = task
            try:
                task.hub_scope = application.service_identity("sakura.asr")
                result = self._call_hub(task, "input", "prepare", dict(payload))
                self._accept_snapshot(task, result)
            except Exception:
                self._tasks.pop(recording_id)
                raise
            task.worker = threading.Thread(target=self._run, args=(task,), name="sakura-asr-input", daemon=True)
            task.worker.start()
            return self._snapshot(task)

    def _run(self, task: _Input) -> None:
        # The plugin owns recording and inference. This observer only binds their
        # result to the current chat and discards output from invalidated sources.
        try:
            while not task.cancelled.is_set():
                result = self._call_hub(task, "input", "poll", {"recordingId": task.recording_id})
                self._accept_snapshot(task, result)
                if task.state not in _ACTIVE:
                    break
                task.cancelled.wait(0.05)
        except Exception as error:
            with self._lock:
                if task.state not in {"cancelled", "consumed", "failed"}:
                    task.state, task.error_code, task.text = "failed", "ASR_PROVIDER_UNAVAILABLE", ""
                    task.diagnostics = diagnostic_attributes(error, reason_code=task.error_code, stage="asr.input")
        finally:
            if task.state not in {"succeeded", "consumed"}:
                self._cancel_hub(task)

    def _accept_snapshot(self, task: _Input, result: Mapping) -> None:
        with self._lock:
            self._require_context(task)
            if result["revision"] <= task.revision:
                return
            task.revision = result["revision"]
            task.provider_id = result.get("providerId") or ""
            task.service_key = result.get("serviceKey", "")
            task.scope_id = result.get("scopeId", "")
            if result["state"] == "succeeded":
                self._commit_transcript(task, result["text"])
            elif task.state not in {"succeeded", "consumed"}:
                task.state = result["state"]
                task.error_code = result.get("errorCode", "")
                task.diagnostics = result.get("diagnostics", {})

    def _discard_capture(self, task: _Input, error_code: object, diagnostic: object = None) -> dict:
        try:
            result = self._call_hub(task, "input", "capture_discarded", {
                "recordingId": task.recording_id, "errorCode": error_code, "diagnostic": diagnostic})
            with self._lock:
                if task.state != "cancelled":
                    task.revision = max(task.revision, result["revision"])
                    task.state = result["state"]
                    task.error_code = result.get("errorCode", task.error_code)
                    task.diagnostics = result.get("diagnostics", task.diagnostics)
                    task.text = ""
        finally:
            if task.resource_id:
                task.application.audio_input.producer_done(task.resource_id)
        return self._snapshot(task)

    def _cancel_hub(self, task: _Input) -> None:
        try:
            self._call_hub(task, "input", "cancel", {"recordingId": task.recording_id})
        except Exception:
            # An exited Hub has no live input to cancel; the native producer
            # retains its host reservation until it reports capture_discarded.
            if task.resource_id:
                task.application.audio_input.revoke_resource(task.resource_id)

    def _cancel(self, task: _Input) -> None:
        with self._lock:
            task.cancelled.set()
            task.text = ""
            if task.state != "failed":
                task.state = "cancelled"
        self._cancel_hub(task)

    def cancel_all(self) -> None:
        with self._lock:
            for task in self._tasks.values():
                self._cancel(task)

    @contextmanager
    def suspend_for_character_change(self):
        with self._lock:
            self._switching_character = True
        try:
            self.cancel_all()
            yield
        finally:
            with self._lock:
                self._switching_character = False

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
        except Exception as error:
            status = {"state": "unavailable", "available": False, "errorCode": "ASR_SERVICE_UNAVAILABLE", "diagnostics": diagnostic_attributes(error, reason_code="ASR_SERVICE_UNAVAILABLE", stage="settings")}
            providers = []
        return {"schemaVersion": 1, **dict(status), "selectedProviderId": status.get("providerId"),
                "inputDeviceId": app.call_service("sakura.asr", "load_settings")["inputDeviceId"] if hub_plugin_id else "",
                "hubPluginId": hub_plugin_id, "providers": providers}

    def _call_hub(self, task: _Input, method: str, *args: object) -> object:
        return task.application.call_bound_service("sakura.asr", task.hub_scope, method, *args)

    def _commit_transcript(self, task: _Input, text: str) -> None:
        self._require_context(task)

        def commit() -> None:
            task.text = text
            task.state = "succeeded"

        self._bound_sources(task, commit)

    def _deliver_transcript(self, task: _Input) -> dict:
        def consume() -> dict:
            result = self._snapshot(task)
            # One delivery opportunity per recording, even with concurrent polls.
            task.state = "consumed"
            task.text = ""
            return result

        try:
            return self._bound_sources(task, consume)
        except Exception as error:
            code = getattr(error, "code", None)
            if not isinstance(code, str) or code not in _BINDING_UNAVAILABLE:
                raise
            task.state = "failed"
            task.text = ""
            task.error_code = "ASR_PROVIDER_UNAVAILABLE"
            task.diagnostics = diagnostic_attributes(error, reason_code=task.error_code, stage="succeeded")
            return self._snapshot(task)

    def _bound_sources(self, task: _Input, commit):
        # Both source identities and the local result update share the runtime's
        # existing lifecycle lock. Callbacks only touch local task state.
        # Context checks and audio cleanup must stay outside that lock.
        return task.application.commit_bound_service("sakura.asr", task.hub_scope, lambda:
            task.application.commit_bound_service(task.service_key,
                {"providerId": task.provider_id, "scopeId": task.scope_id}, commit))

    def _require_context(self, task: _Input) -> None:
        if (self._closed or self._switching_character or task.cancelled.is_set()
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
            value["diagnostics"] = dict(task.diagnostics)
        return value

    def _response(self, request: dict, **kwargs) -> dict:
        return response(request, generation_id=self._generation, generation_credential=self._credential,
                        protocol_minor=int(request.get("protocolMinor", 2)), **kwargs)
