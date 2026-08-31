"""Generation-scoped Runtime v2 TTS authorization and persistence boundary."""

from __future__ import annotations

import concurrent.futures
import hmac
import threading
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from time import monotonic, sleep
from typing import Any, Callable, Mapping

from app.core_host.protocol import error_payload, event, response
from app.core.runtime_log import log_event
from app.storage.tts_storage import TtsStorage, TtsStorageUnavailable
from app.voice.recording_store import VoiceRecordingError, VoiceRecordingStore


TTS_CAPABILITY = "assistant.tts-v1"
TTS_REQUEST_NAMES = frozenset(
    {
        "tts.synthesis.start",
        "tts.synthesis.cancel",
        "tts.settings.get",
        "tts.settings.save",
        "tts.status.get",
        "tts.playback.observe",
    }
)
AUTHORIZATION_TTL_SECONDS = 300
PLAYBACK_TTL_SECONDS = 300
MAX_AUTHORIZATIONS = 32
MAX_ACTIVE_SYNTHESIS = 2
PLUGIN_JOB_POLL_INTERVAL_SECONDS = 0.05
PLUGIN_JOB_HOST_TIMEOUT_SECONDS = 305.0


class TTSBoundaryError(RuntimeError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        retryable: bool = False,
        provider_error_code: str | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable
        self.provider_error_code = provider_error_code

    def public_error(self) -> dict[str, Any]:
        payload = error_payload(self.code, self.message)
        payload["retryable"] = self.retryable
        return payload


@dataclass
class _Authorization:
    operation_id: str
    segment_index: int
    text: str
    tone: str
    portrait: str
    character_id: str
    history_entry_id: str
    expires_at: float
    state: str = "authorized"
    request_id: str = ""


class _PluginSynthesisHandle:
    """Core-side waiter for one short-call Hub job."""

    def __init__(self, application: object, request_id: str, provider_id: str) -> None:
        self._application = application
        self.request_id = request_id
        self.provider_id = provider_id

    def result(self, timeout: float) -> Mapping[str, Any]:
        deadline = monotonic() + max(0.0, timeout)
        while True:
            if monotonic() >= deadline:
                raise concurrent.futures.TimeoutError()
            try:
                result = getattr(self._application, "call_service")(
                    "sakura.tts",
                    "poll",
                    self.request_id,
                )
            except Exception as error:
                raise TTSBoundaryError(
                    "TTS_SERVICE_UNAVAILABLE",
                    "TTS Hub job is unavailable",
                    retryable=True,
                ) from error
            if not isinstance(result, Mapping):
                raise TTSBoundaryError("TTS_SYNTHESIS_FAILED", "TTS Hub job result is invalid")
            state = result.get("state")
            if result.get("requestId") != self.request_id:
                raise TTSBoundaryError("TTS_SYNTHESIS_FAILED", "TTS Hub job identity changed")
            if state == "failed":
                self._raise_failed(result.get("errorCode"))
            if result.get("providerId") != self.provider_id:
                raise TTSBoundaryError("TTS_SYNTHESIS_FAILED", "TTS Provider identity changed")
            if state == "running":
                sleep(min(PLUGIN_JOB_POLL_INTERVAL_SECONDS, max(0.0, deadline - monotonic())))
                continue
            if state == "cancelled":
                raise TTSBoundaryError(
                    "TTS_SYNTHESIS_CANCELLED",
                    "TTS synthesis was cancelled",
                )
            if state == "succeeded" and isinstance(result.get("artifact"), Mapping):
                return result
            raise TTSBoundaryError("TTS_SYNTHESIS_FAILED", "TTS Hub job result is invalid")

    def cancel(self) -> bool:
        try:
            result = getattr(self._application, "call_service")(
                "sakura.tts",
                "cancel",
                self.request_id,
            )
        except Exception:
            return False
        return bool(isinstance(result, Mapping) and result.get("accepted"))

    @staticmethod
    def _raise_failed(error_code: object) -> None:
        code = error_code if isinstance(error_code, str) else "TTS_SYNTHESIS_FAILED"
        if code in {
            "TTS_PROVIDER_NOT_SELECTED",
            "TTS_DISABLED",
            "TTS_PROVIDER_UNAVAILABLE",
            "TTS_JOB_NOT_FOUND",
        }:
            raise TTSBoundaryError(
                "TTS_SERVICE_UNAVAILABLE",
                "configured TTS Provider is unavailable",
                retryable=True,
                provider_error_code=code,
            )
        if code in {"TTS_ARTIFACT_INVALID", "TTS_JOB_RESULT_INVALID"}:
            raise TTSBoundaryError(
                "AUDIO_RECORDING_INVALID",
                "TTS audio artifact is invalid",
                provider_error_code=code,
            )
        raise TTSBoundaryError(
            "TTS_SYNTHESIS_FAILED",
            "TTS Provider synthesis failed",
            provider_error_code=code,
        )


class TTSBoundary:
    def __init__(
        self,
        generation_id: str,
        generation_credential: str,
        user_root: Path,
        *,
        session_provider: Callable[[], object | None],
        plugin_application_provider: Callable[[], object | None] | None = None,
        event_publisher: Callable[[dict[str, Any]], None] | None = None,
        recording_store: VoiceRecordingStore | None = None,
    ) -> None:
        self._generation_id = generation_id
        self._generation_credential = generation_credential
        self._user_root = Path(user_root)
        self._tts_storage = TtsStorage(self._user_root)
        self._session_provider = session_provider
        self._plugin_application_provider = plugin_application_provider
        self._event_publisher = event_publisher
        self._recordings = recording_store or VoiceRecordingStore(self._user_root)
        self._lock = threading.RLock()
        self._authorizations: dict[tuple[str, int], _Authorization] = {}
        self._handles: dict[str, object] = {}
        self._closed = False

    def set_event_publisher(self, publisher: Callable[[dict[str, Any]], None]) -> None:
        with self._lock:
            self._event_publisher = publisher

    def warmup_current_selection(self) -> None:
        """Queue startup of the selected managed Provider without delaying readiness."""

        with self._lock:
            if self._closed:
                return
        session = self._session_provider()
        application = self._plugin_application()
        character = getattr(session, "character", None) if session is not None else None
        character_id = str(getattr(character, "id", ""))
        if application is None or not character_id:
            return
        try:
            self._require_storage_root()
        except TTSBoundaryError as error:
            log_event(
                "TTS",
                "TTS startup warmup skipped because storage is unavailable",
                {
                    "generation": self._generation_id,
                    "code": error.code,
                },
                event="tts.service.warmup_skipped",
                severity="warning",
            )
            return
        try:
            result = getattr(application, "call_service")(
                "sakura.tts",
                "warmup",
                character_id,
            )
        except Exception:
            log_event(
                "TTS",
                "TTS startup warmup could not be queued",
                {
                    "generation": self._generation_id,
                    "code": "TTS_WARMUP_FAILED",
                },
                event="tts.service.warmup_failed",
                severity="warning",
            )
            return
        accepted = isinstance(result, Mapping) and bool(result.get("accepted"))
        reason_code = (
            result.get("reasonCode", "TTS_WARMUP_SKIPPED")
            if isinstance(result, Mapping)
            else "TTS_WARMUP_SKIPPED"
        )
        skipped = reason_code in {
            "TTS_DISABLED",
            "TTS_PROVIDER_NOT_SELECTED",
            "TTS_WARMUP_SKIPPED",
        }
        attributes: dict[str, object] = {
            "generation": self._generation_id,
            "provider": result.get("providerId", "") if isinstance(result, Mapping) else "",
            "status": "queued" if accepted else "skipped" if skipped else "failed",
            "reason_code": reason_code,
        }
        if isinstance(result, Mapping):
            if isinstance(result.get("stage"), str):
                attributes["stage"] = result["stage"]
            if isinstance(result.get("errorType"), str):
                attributes["error_type"] = result["errorType"]
        log_event(
            "TTS",
            "TTS startup warmup queued"
            if accepted
            else "TTS startup warmup skipped"
            if skipped
            else "TTS startup warmup failed",
            attributes,
            event=(
                "tts.service.warmup_queued"
                if accepted
                else "tts.service.warmup_skipped"
                if skipped
                else "tts.service.warmup_failed"
            ),
            severity="warning" if not accepted and not skipped else "info",
        )

    def authorize_segment(
        self,
        *,
        operation_id: str,
        segment_index: int,
        text: str,
        tone: str,
        portrait: str,
        character_id: str,
        history_entry_id: str,
    ) -> bool:
        if not text.strip() or segment_index < 0:
            return False
        if not self._synthesis_enabled(character_id):
            return False
        with self._lock:
            if self._closed:
                return False
            self._expire_locked()
            key = (operation_id, segment_index)
            self._authorizations[key] = _Authorization(
                operation_id=operation_id,
                segment_index=segment_index,
                text=text,
                tone=tone,
                portrait=portrait,
                character_id=character_id,
                history_entry_id=history_entry_id,
                expires_at=monotonic() + AUTHORIZATION_TTL_SECONDS,
            )
            while len(self._authorizations) > MAX_AUTHORIZATIONS:
                removable = next(
                    (
                        item_key
                        for item_key, item in self._authorizations.items()
                        if item.state != "synthesizing"
                    ),
                    None,
                )
                if removable is None:
                    break
                self._authorizations.pop(removable, None)
        return True

    def _synthesis_enabled(self, character_id: str) -> bool:
        """Return false only when the routed TTS Service explicitly says so."""

        application = self._plugin_application()
        if application is None:
            # Missing runtime state is an operational failure, not proof that
            # the user disabled TTS. Preserve the later diagnostic in that case.
            return True
        try:
            status = getattr(application, "call_service")(
                "sakura.tts",
                "status",
                character_id,
            )
        except Exception:
            return True
        if not isinstance(status, Mapping):
            return True
        return status.get("enabled") is not False

    def handle(self, request: dict[str, Any]) -> dict[str, Any]:
        try:
            self._validate_generation(request)
            name = str(request.get("name", ""))
            if name == "tts.synthesis.start":
                payload = self._handle_start(request)
            elif name == "tts.synthesis.cancel":
                payload = self._handle_cancel(request)
            elif name == "tts.settings.get":
                payload = self._handle_settings_get(request)
            elif name == "tts.settings.save":
                payload = self._handle_settings_save(request)
            elif name == "tts.status.get":
                payload = self._handle_status_get(request)
            elif name == "tts.playback.observe":
                payload = self._handle_playback_observe(request)
            else:
                raise TTSBoundaryError("UNKNOWN_CONTROL", "unsupported TTS request")
            return response(
                request,
                generation_id=self._generation_id,
                generation_credential=self._generation_credential,
                protocol_minor=int(request.get("protocolMinor", 2)),
                payload=payload,
            )
        except TTSBoundaryError as error:
            return response(
                request,
                generation_id=self._generation_id,
                generation_credential=self._generation_credential,
                protocol_minor=int(request.get("protocolMinor", 2)),
                error=error.public_error(),
            )

    def close(self) -> None:
        self.cancel_all()
        with self._lock:
            if self._closed:
                return
            self._closed = True
            self._handles.clear()
            self._authorizations.clear()
        self._recordings.cleanup_generation(self._generation_id)

    def cancel_all(self) -> None:
        """Signal every in-flight synthesis before Router/generation teardown waits."""

        with self._lock:
            handles = tuple(self._handles.values())
            for authorization in self._authorizations.values():
                if authorization.state in {"synthesizing", "cancelling"}:
                    authorization.state = "cancelling"
        for handle in handles:
            cancel = getattr(handle, "cancel", None)
            if callable(cancel):
                try:
                    cancel()
                except Exception:
                    pass

    def _handle_start(self, request: Mapping[str, Any]) -> dict[str, Any]:
        payload = request.get("payload")
        if not isinstance(payload, Mapping) or set(payload) != {"operationId", "segmentIndex"}:
            raise TTSBoundaryError("TTS_SEGMENT_NOT_AUTHORIZED", "invalid segment identity")
        operation_id = payload.get("operationId")
        segment_index = payload.get("segmentIndex")
        if (
            not isinstance(operation_id, str)
            or not operation_id.strip()
            or isinstance(segment_index, bool)
            or not isinstance(segment_index, int)
            or segment_index < 0
        ):
            raise TTSBoundaryError("TTS_SEGMENT_NOT_AUTHORIZED", "invalid segment identity")
        self._require_storage_root()
        with self._lock:
            self._ensure_open_locked()
            self._expire_locked()
            authorization = self._authorizations.get((operation_id, segment_index))
            if authorization is None or authorization.state != "authorized":
                raise TTSBoundaryError(
                    "TTS_SEGMENT_NOT_AUTHORIZED", "segment is not authorized for synthesis"
                )
            if sum(item.state == "synthesizing" for item in self._authorizations.values()) >= MAX_ACTIVE_SYNTHESIS:
                raise TTSBoundaryError(
                    "TTS_SERVICE_UNAVAILABLE", "TTS synthesis capacity is full", retryable=True
                )
            request_id = f"tts-{uuid.uuid4().hex}"
            authorization.state = "synthesizing"
            authorization.request_id = request_id

        started_at = monotonic()
        log_event(
            "TTS", "TTS synthesis started",
            {
                "operation_id": operation_id,
                "segment_index": segment_index,
                "request_id": request_id,
            },
            event="tts.synthesis.started",
        )

        try:
            descriptor, recording, provider_id = self._synthesize_with_plugin(
                authorization,
                request_id,
            )
            log_event(
                "TTS", "TTS recording committed",
                {
                    "provider": provider_id,
                    "operation_id": operation_id,
                    "segment_index": segment_index,
                    "request_id": request_id,
                    "recording_id": recording.recording_id,
                    "bytes": recording.byte_length,
                },
                event="tts.recording.committed",
            )
            with self._lock:
                authorization.state = "ready"
            log_event(
                "TTS", "TTS synthesis ready",
                {
                    "provider": provider_id,
                    "operation_id": operation_id,
                    "segment_index": segment_index,
                    "request_id": request_id,
                    "recording_id": recording.recording_id,
                    "bytes": recording.byte_length,
                    "elapsed_ms": round((monotonic() - started_at) * 1000),
                },
                event="tts.synthesis.ready",
            )
            self._publish(request, "tts.synthesis.ready", {**descriptor, "operationId": operation_id, "segmentIndex": segment_index})
            return descriptor
        except TTSBoundaryError as error:
            self._mark_failed(authorization)
            if error.code == "TTS_SYNTHESIS_CANCELLED":
                self._log_synthesis_terminal(
                    authorization,
                    error.code,
                    started_at,
                    "cancelled",
                )
                self._publish(
                    request,
                    "tts.synthesis.cancelled",
                    self._segment_payload(authorization),
                )
            else:
                self._log_synthesis_terminal(
                    authorization,
                    error.code,
                    started_at,
                    "failed",
                    provider_error_code=error.provider_error_code,
                )
                self._publish_failure(request, authorization, error)
            raise
        finally:
            with self._lock:
                self._handles.pop(request_id, None)

    def _handle_cancel(self, request: Mapping[str, Any]) -> dict[str, Any]:
        payload = request.get("payload")
        if not isinstance(payload, Mapping) or set(payload) not in (
            {"requestId"},
            {"operationId"},
        ):
            raise TTSBoundaryError("TTS_SYNTHESIS_CANCELLED", "invalid cancellation identity")
        if "operationId" in payload:
            operation_id = payload.get("operationId")
            if not isinstance(operation_id, str) or not operation_id.strip():
                raise TTSBoundaryError(
                    "TTS_SYNTHESIS_CANCELLED",
                    "invalid cancellation identity",
                )
            with self._lock:
                authorizations = [
                    item
                    for item in self._authorizations.values()
                    if item.operation_id == operation_id
                    and item.state in {"authorized", "synthesizing", "cancelling"}
                ]
                request_ids = [
                    item.request_id
                    for item in authorizations
                    if item.request_id
                ]
                for authorization in authorizations:
                    authorization.state = (
                        "cancelling" if authorization.request_id else "cancelled"
                    )
            accepted = bool(authorizations)
            for request_id in request_ids:
                accepted = self._cancel_request_id(request_id) or accepted
            return {"accepted": accepted, "operationId": operation_id}
        request_id = payload.get("requestId")
        if not isinstance(request_id, str) or not request_id.strip():
            raise TTSBoundaryError("TTS_SYNTHESIS_CANCELLED", "invalid cancellation identity")
        return {"accepted": self._cancel_request_id(request_id), "requestId": request_id}

    def _cancel_request_id(self, request_id: str) -> bool:
        with self._lock:
            authorization = next(
                (
                    item
                    for item in self._authorizations.values()
                    if item.request_id == request_id and item.state in {"synthesizing", "cancelling"}
                ),
                None,
            )
            if authorization is None:
                return False
            authorization.state = "cancelling"
            handle = self._handles.get(request_id)
        accepted = True
        accepted = bool(handle is not None and getattr(handle, "cancel")()) or accepted
        if handle is None:
            application = self._plugin_application()
            if application is not None:
                try:
                    result = getattr(application, "call_service")(
                        "sakura.tts",
                        "cancel",
                        request_id,
                    )
                    accepted = bool(
                        isinstance(result, Mapping) and result.get("accepted")
                    ) or accepted
                except Exception:
                    pass
        return accepted

    def _synthesize_with_plugin(
        self,
        authorization: _Authorization,
        request_id: str,
    ) -> tuple[dict[str, Any], object, str]:
        application = self._plugin_application()
        if application is None:
            raise TTSBoundaryError(
                "TTS_SERVICE_UNAVAILABLE",
                "TTS Hub is unavailable",
                retryable=True,
            )
        with self._lock:
            if authorization.state == "cancelling":
                raise TTSBoundaryError(
                    "TTS_SYNTHESIS_CANCELLED",
                    "TTS synthesis was cancelled",
                )
        try:
            result = getattr(application, "call_service")(
                "sakura.tts",
                "begin",
                {
                    "requestId": request_id,
                    "characterId": authorization.character_id,
                    "text": authorization.text,
                    "options": {
                        "tone": authorization.tone,
                        "portrait": authorization.portrait,
                    },
                },
            )
        except Exception as error:
            if getattr(error, "code", "") in {
                "SERVICE_MISSING",
                "GENERATION_INVALIDATED",
            }:
                raise TTSBoundaryError(
                    "TTS_SERVICE_UNAVAILABLE",
                    "TTS Hub is unavailable",
                    retryable=True,
                ) from error
            raise TTSBoundaryError(
                "TTS_SYNTHESIS_FAILED",
                "TTS Hub job could not start",
                retryable=True,
            ) from error
        if not isinstance(result, Mapping) or result.get("requestId") != request_id:
            raise TTSBoundaryError("TTS_SYNTHESIS_FAILED", "TTS Hub job result is invalid")
        if result.get("state") == "failed":
            _PluginSynthesisHandle._raise_failed(result.get("errorCode"))
        provider_id = result.get("providerId")
        if (
            result.get("state") != "running"
            or not isinstance(provider_id, str)
            or not provider_id
        ):
            raise TTSBoundaryError("TTS_SYNTHESIS_FAILED", "TTS Provider identity is invalid")
        handle = _PluginSynthesisHandle(application, request_id, provider_id)
        with self._lock:
            if self._closed:
                closed = True
                cancelled = False
            elif authorization.state == "cancelling":
                closed = False
                cancelled = True
                self._handles[request_id] = handle
            else:
                self._handles[request_id] = handle
                closed = False
                cancelled = False
        if closed:
            handle.cancel()
            raise TTSBoundaryError(
                "TTS_SYNTHESIS_CANCELLED",
                "TTS generation is closed",
            )
        if cancelled:
            handle.cancel()
        try:
            result = handle.result(PLUGIN_JOB_HOST_TIMEOUT_SECONDS)
        except concurrent.futures.TimeoutError as error:
            handle.cancel()
            raise TTSBoundaryError(
                "TTS_SYNTHESIS_TIMEOUT",
                "TTS synthesis timed out",
                retryable=True,
            ) from error
        with self._lock:
            if self._closed:
                closed = True
                cancelled = False
            elif authorization.state == "cancelling":
                closed = False
                cancelled = True
            elif authorization.state == "synthesizing":
                authorization.state = "committing"
                closed = False
                cancelled = False
            else:
                closed = False
                cancelled = True
        artifact = result.get("artifact")
        artifact_id = artifact.get("artifactId") if isinstance(artifact, Mapping) else None
        if closed:
            if isinstance(artifact_id, str):
                self._release_plugin_artifact(application, artifact_id)
            raise TTSBoundaryError(
                "TTS_SYNTHESIS_CANCELLED",
                "TTS generation is closed",
            )
        if cancelled:
            if isinstance(artifact_id, str):
                self._release_plugin_artifact(application, artifact_id)
            raise TTSBoundaryError(
                "TTS_SYNTHESIS_CANCELLED",
                "TTS synthesis was cancelled",
            )
        descriptor, recording = self._consume_plugin_audio_artifact(
            artifact if isinstance(artifact, Mapping) else {},
            authorization,
            provider=provider_id,
        )
        return descriptor, recording, provider_id

    def _consume_plugin_audio_artifact(
        self,
        descriptor: Mapping[str, Any],
        authorization: _Authorization,
        *,
        provider: str,
    ) -> tuple[dict[str, Any], object]:
        """Commit an authorized plugin result without delegating recording/playback ownership."""

        if not isinstance(descriptor, Mapping) or set(descriptor) != {
            "artifactId",
            "mediaType",
            "byteLength",
        }:
            raise TTSBoundaryError("AUDIO_RECORDING_INVALID", "invalid plugin audio artifact")
        artifact_id = descriptor.get("artifactId")
        media_type = descriptor.get("mediaType")
        byte_length = descriptor.get("byteLength")
        if (
            not isinstance(artifact_id, str)
            or not artifact_id.startswith("artifact_")
            or media_type != "audio/wav"
            or isinstance(byte_length, bool)
            or not isinstance(byte_length, int)
            or byte_length <= 0
        ):
            raise TTSBoundaryError("AUDIO_RECORDING_INVALID", "invalid plugin audio artifact")
        application = self._plugin_application()
        if application is None:
            raise TTSBoundaryError("TTS_SERVICE_UNAVAILABLE", "plugin application is unavailable")
        try:
            artifact = getattr(application, "resolve_committed_artifact")(artifact_id)
            if (
                getattr(artifact, "media_type", None) != media_type
                or getattr(artifact, "byte_length", None) != byte_length
            ):
                raise TTSBoundaryError(
                    "AUDIO_RECORDING_INVALID",
                    "plugin audio artifact descriptor mismatch",
                )
            recording = self._recordings.commit(
                Path(getattr(artifact, "path")),
                character_id=authorization.character_id,
                history_entry_id=authorization.history_entry_id,
                tone=authorization.tone,
                portrait=authorization.portrait,
                provider=provider,
            )
            expires = datetime.now(timezone.utc) + timedelta(seconds=PLAYBACK_TTL_SECONDS)
            playback = self._recordings.create_playback_copy(
                recording.recording_id,
                generation_id=self._generation_id,
                expires_at=expires.isoformat(timespec="seconds"),
            )
            public = {
                "opaqueId": playback.opaque_id,
                "recordingId": recording.recording_id,
                "mediaType": playback.media_type,
                "byteLength": playback.byte_length,
                "expiresAt": playback.expires_at,
            }
            return public, recording
        except TTSBoundaryError:
            raise
        except (OSError, ValueError, VoiceRecordingError) as error:
            raise TTSBoundaryError(
                "AUDIO_RECORDING_INVALID",
                "plugin audio artifact could not be committed",
            ) from error
        except Exception as error:
            raise TTSBoundaryError(
                "AUDIO_RECORDING_INVALID",
                "plugin audio artifact is unavailable",
            ) from error
        finally:
            self._release_plugin_artifact(application, artifact_id)

    @staticmethod
    def _release_plugin_artifact(application: object, artifact_id: str) -> None:
        try:
            getattr(application, "release_committed_artifact")(artifact_id)
        except Exception:
            # Worker/generation teardown remains the final generation-scoped cleanup.
            pass

    def _handle_settings_get(self, request: Mapping[str, Any]) -> dict[str, Any]:
        if request.get("payload") not in ({}, None):
            raise TTSBoundaryError("INVALID_TTS_SETTINGS", "settings get payload must be empty")
        return self._voice_settings_snapshot()

    def _handle_settings_save(self, request: Mapping[str, Any]) -> dict[str, Any]:
        payload = request.get("payload")
        if not isinstance(payload, Mapping) or set(payload) != {"settings"}:
            raise TTSBoundaryError("INVALID_TTS_SETTINGS", "settings save payload is invalid")
        draft = payload.get("settings")
        if not isinstance(draft, Mapping) or set(draft) != {
            "characterId",
            "enabled",
            "providerId",
            "sections",
        }:
            raise TTSBoundaryError("INVALID_TTS_SETTINGS", "settings draft is invalid")
        character_id = draft.get("characterId")
        enabled = draft.get("enabled")
        provider_id = draft.get("providerId")
        raw_sections = draft.get("sections")
        if (
            (character_id is not None and (not isinstance(character_id, str) or not character_id))
            or not isinstance(enabled, bool)
            or (provider_id is not None and (not isinstance(provider_id, str) or not provider_id))
            or not isinstance(raw_sections, list)
            or len(raw_sections) > 32
            or (character_id is None and (enabled or provider_id is not None))
        ):
            raise TTSBoundaryError("INVALID_TTS_SETTINGS", "settings draft is invalid")
        application = self._voice_application()
        if character_id is not None:
            application, current_character = self._voice_application_and_character()
            if character_id != str(getattr(current_character, "id", "")):
                raise TTSBoundaryError("INVALID_TTS_SETTINGS", "character identity changed")
        allowed = {
            (section.get("pluginId"), section.get("sectionId"))
            for section in getattr(application, "settings_sections")("voice")
            if isinstance(section, Mapping)
        }
        sections: list[tuple[str, str, Mapping[str, Any]]] = []
        for section in raw_sections:
            if not isinstance(section, Mapping) or set(section) != {
                "pluginId",
                "sectionId",
                "values",
            }:
                raise TTSBoundaryError("INVALID_TTS_SETTINGS", "settings section is invalid")
            plugin_id = section.get("pluginId")
            section_id = section.get("sectionId")
            values = section.get("values")
            if (
                not isinstance(plugin_id, str)
                or not isinstance(section_id, str)
                or (plugin_id, section_id) not in allowed
                or not isinstance(values, Mapping)
            ):
                raise TTSBoundaryError("INVALID_TTS_SETTINGS", "settings section is invalid")
            sections.append((plugin_id, section_id, values))

        application_states: list[str] = []
        saved_sections: list[dict[str, str]] = []
        try:
            for plugin_id, section_id, values in sections:
                result = getattr(application, "settings_save")(
                    plugin_id,
                    section_id,
                    values,
                )
                state = result.get("applicationState") if isinstance(result, Mapping) else None
                if state not in {"applied", "restart_required", "error"}:
                    raise ValueError("settings application state is invalid")
                application_states.append(str(state))
                saved_sections.append({"pluginId": plugin_id, "sectionId": section_id})
        except (OSError, RuntimeError, TypeError, ValueError) as exc:
            if saved_sections:
                return self._partial_voice_settings_save(
                    saved_sections,
                    application_states,
                    reason_code="TTS_PROVIDER_SETTINGS_SAVE_FAILED",
                )
            raise TTSBoundaryError(
                "INVALID_TTS_SETTINGS", "TTS Provider settings could not be saved"
            ) from exc

        selection_saved = False
        reason_code = "CHARACTER_REQUIRED"
        if character_id is not None:
            try:
                getattr(application, "call_service")(
                    "sakura.tts",
                    "configure",
                    character_id,
                    {"enabled": enabled, "provider": provider_id},
                )
            except (OSError, RuntimeError, TypeError, ValueError) as exc:
                if saved_sections:
                    return self._partial_voice_settings_save(
                        saved_sections,
                        application_states,
                        reason_code="TTS_SELECTION_SAVE_FAILED",
                    )
                raise TTSBoundaryError("INVALID_TTS_SETTINGS", "TTS settings could not be saved") from exc
            selection_saved = True
            reason_code = "READY"
        application_state = _combined_application_state(application_states)
        log_event(
            "TTS", "TTS settings saved",
            {
                "provider": provider_id or "",
                "status": "enabled" if enabled else "disabled",
                "selection_saved": selection_saved,
            },
            event="tts.settings.saved",
        )
        return {
            "snapshot": self._voice_settings_snapshot(),
            "applicationState": application_state,
            "saveState": "complete",
            "savedSections": saved_sections,
            "selectionSaved": selection_saved,
            "reasonCode": reason_code,
        }

    def _partial_voice_settings_save(
        self,
        saved_sections: list[dict[str, str]],
        application_states: list[str],
        *,
        reason_code: str,
    ) -> dict[str, Any]:
        try:
            snapshot: dict[str, Any] | None = self._voice_settings_snapshot()
        except TTSBoundaryError:
            snapshot = None
        log_event(
            "TTS",
            "TTS settings were only partially saved",
            {"reason_code": reason_code, "saved_sections": len(saved_sections)},
            event="tts.settings.partial",
            severity="warning",
        )
        return {
            "snapshot": snapshot,
            "applicationState": _combined_application_state(application_states),
            "saveState": "partial",
            "savedSections": saved_sections,
            "selectionSaved": False,
            "reasonCode": reason_code,
        }

    def _handle_status_get(self, request: Mapping[str, Any]) -> dict[str, Any]:
        if request.get("payload") not in ({}, None):
            raise TTSBoundaryError("INVALID_TTS_SETTINGS", "status payload must be empty")
        return self._voice_settings_snapshot()

    def _voice_application_and_character(self) -> tuple[object, object]:
        session = self._session_provider()
        application = self._voice_application()
        character = getattr(session, "character", None) if session is not None else None
        if character is None or not str(getattr(character, "id", "")):
            raise TTSBoundaryError(
                "TTS_SERVICE_UNAVAILABLE",
                "TTS capability settings are unavailable",
                retryable=True,
            )
        return application, character

    def _voice_application(self) -> object:
        application = self._plugin_application()
        if application is None:
            raise TTSBoundaryError(
                "TTS_SERVICE_UNAVAILABLE",
                "TTS capability settings are unavailable",
                retryable=True,
            )
        return application

    def _voice_settings_snapshot(self) -> dict[str, Any]:
        application = self._voice_application()
        session = self._session_provider()
        candidate = getattr(session, "character", None) if session is not None else None
        character_id = str(getattr(candidate, "id", ""))
        character = candidate if character_id else None
        try:
            status = (
                getattr(application, "call_service")("sakura.tts", "status", character_id)
                if character is not None
                else None
            )
            providers = (
                status.get("providers")
                if isinstance(status, Mapping)
                else getattr(application, "call_service")("sakura.tts", "listProviders")
            )
            sections = getattr(application, "settings_sections")("voice")
        except Exception as exc:
            raise TTSBoundaryError(
                "TTS_SERVICE_UNAVAILABLE",
                "TTS capability settings are unavailable",
                retryable=True,
            ) from exc
        if (character is not None and not isinstance(status, Mapping)) or not isinstance(sections, list):
            raise TTSBoundaryError("INVALID_TTS_SETTINGS", "TTS capability response is invalid")
        if not isinstance(providers, list):
            raise TTSBoundaryError("INVALID_TTS_SETTINGS", "TTS Provider list is invalid")
        return {
            "schemaVersion": 1,
            "character": (
                {
                    "characterId": character_id,
                    "displayName": str(getattr(character, "display_name", character_id))[:120],
                }
                if character is not None
                else None
            ),
            "selection": (
                {
                    "configured": bool(status.get("configured")),
                    "enabled": bool(status.get("enabled")),
                    "providerId": (
                        status.get("providerId")
                        if isinstance(status.get("providerId"), str)
                        else None
                    ),
                    "available": bool(status.get("available")),
                }
                if isinstance(status, Mapping)
                else None
            ),
            "providers": [dict(item) for item in providers if isinstance(item, Mapping)][:64],
            # The generic settings surface carries routing metadata such as
            # ``surface``.  Voice settings have their own stable response
            # schema, so project only its public section fields instead of
            # leaking generic host metadata into the WebView contract.
            "sections": [
                {
                    key: item[key]
                    for key in (
                        "pluginId",
                        "sectionId",
                        "title",
                        "reasonCode",
                        "fields",
                        "values",
                        "actions",
                        "collections",
                    )
                    if key in item
                }
                for item in sections
                if isinstance(item, Mapping)
            ][:32],
        }

    def _handle_playback_observe(self, request: Mapping[str, Any]) -> dict[str, Any]:
        payload = request.get("payload")
        if (
            not isinstance(payload, Mapping)
            or not {"playbackId", "recordingId", "state"}.issubset(payload)
            or not set(payload).issubset({"playbackId", "recordingId", "state", "errorCode"})
        ):
            raise TTSBoundaryError("AUDIO_PLAYBACK_FAILED", "invalid playback observation")
        playback_id = payload.get("playbackId")
        recording_id = payload.get("recordingId")
        state = payload.get("state")
        error_code = payload.get("errorCode")
        if (
            not isinstance(playback_id, str)
            or not playback_id.strip()
            or len(playback_id) > 128
            or (recording_id is not None and not isinstance(recording_id, str))
            or state not in {"started", "finished", "stopped", "failed"}
            or (
                error_code is not None
                and (
                    not isinstance(error_code, str)
                    or not error_code
                    or len(error_code) > 64
                    or not error_code.replace("_", "").isalnum()
                )
            )
        ):
            raise TTSBoundaryError("AUDIO_PLAYBACK_FAILED", "invalid playback observation")
        application = self._plugin_application()
        if application is not None:
            event_name = (
                "sakura.host.tts.started"
                if state == "started"
                else "sakura.host.tts.ended"
            )
            summary = {
                "playbackId": playback_id,
                "recordingId": recording_id,
                "outcome": state,
            }
            if error_code is not None:
                summary["errorCode"] = error_code
            try:
                getattr(application, "emit_event")(event_name, summary)
            except Exception:
                # Plugin publication is observational and must never affect playback.
                pass
        return {"accepted": True}

    def _plugin_application(self) -> object | None:
        if self._plugin_application_provider is not None:
            return self._plugin_application_provider()
        session = self._session_provider()
        return getattr(session, "plugin_application", None) if session is not None else None

    def _require_storage_root(self) -> Path:
        try:
            return self._tts_storage.require_root()
        except TtsStorageUnavailable as error:
            raise TTSBoundaryError(
                "TTS_STORAGE_UNAVAILABLE",
                "configured TTS storage is unavailable",
                retryable=True,
            ) from error

    def _validate_generation(self, request: Mapping[str, Any]) -> None:
        if request.get("generationId") != self._generation_id:
            raise TTSBoundaryError("STALE_GENERATION", "TTS request belongs to another generation")
        supplied = request.get("generationCredential")
        if not isinstance(supplied, str) or not hmac.compare_digest(supplied, self._generation_credential):
            raise TTSBoundaryError("STALE_GENERATION", "TTS generation credential is invalid")
        with self._lock:
            self._ensure_open_locked()

    def _ensure_open_locked(self) -> None:
        if self._closed:
            raise TTSBoundaryError("STALE_GENERATION", "TTS generation is closed")

    def _expire_locked(self) -> None:
        now = monotonic()
        for key, item in tuple(self._authorizations.items()):
            if item.expires_at <= now and item.state not in {
                "synthesizing",
                "cancelling",
                "committing",
            }:
                self._authorizations.pop(key, None)

    def _mark_failed(self, authorization: _Authorization) -> None:
        with self._lock:
            authorization.state = "failed"

    def _log_synthesis_terminal(
        self,
        authorization: _Authorization,
        code: str,
        started_at: float,
        outcome: str,
        *,
        provider_error_code: str | None = None,
    ) -> None:
        event_name = f"tts.synthesis.{outcome}"
        attributes: dict[str, object] = {
            "operation_id": authorization.operation_id,
            "segment_index": authorization.segment_index,
            "request_id": authorization.request_id,
            "code": code,
            "elapsed_ms": round((monotonic() - started_at) * 1000),
        }
        if provider_error_code:
            attributes["provider_error_code"] = provider_error_code
        log_event(
            "TTS", "TTS synthesis did not complete",
            attributes,
            event=event_name,
            severity="warning" if outcome == "failed" else "info",
        )

    def _publish(self, request: Mapping[str, Any], name: str, payload: Mapping[str, Any]) -> None:
        publisher = self._event_publisher
        if publisher is not None:
            publisher(
                event(
                    request,
                    generation_id=self._generation_id,
                    generation_credential=self._generation_credential,
                    protocol_minor=int(request.get("protocolMinor", 2)),
                    name=name,
                    payload=payload,
                )
            )

    def _publish_failure(
        self, request: Mapping[str, Any], authorization: _Authorization, error: TTSBoundaryError
    ) -> None:
        self._publish(
            request,
            "tts.synthesis.failed",
            {**self._segment_payload(authorization), "error": error.public_error()},
        )

    @staticmethod
    def _segment_payload(authorization: _Authorization) -> dict[str, Any]:
        return {
            "operationId": authorization.operation_id,
            "segmentIndex": authorization.segment_index,
            "requestId": authorization.request_id,
        }


def _combined_application_state(states: list[str]) -> str:
    if "error" in states:
        return "error"
    if "restart_required" in states:
        return "restart_required"
    return "applied"


__all__ = ["TTSBoundary", "TTSBoundaryError", "TTS_CAPABILITY", "TTS_REQUEST_NAMES"]
