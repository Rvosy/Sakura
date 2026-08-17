"""Generation-scoped Runtime v2 TTS authorization and persistence boundary."""

from __future__ import annotations

import concurrent.futures
import hmac
import threading
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from time import monotonic
from typing import Any, Callable, Mapping

from app.core_host.protocol import error_payload, event, response
from app.core.runtime_log import log_event
from app.storage.paths import StoragePaths
from app.voice.recording_store import VoiceRecordingError, VoiceRecordingStore
from app.voice.tts_synthesis_service import (
    SynthesizedAudio,
    TTSSynthesisCancelled,
    TTSSynthesisClosed,
    TTSSynthesisError,
    TTSSynthesisService,
)


TTS_CAPABILITY = "assistant.tts-v1"
TTS_REQUEST_NAMES = frozenset(
    {
        "tts.synthesis.start",
        "tts.synthesis.cancel",
        "tts.settings.get",
        "tts.settings.save",
        "tts.settings.test",
        "tts.status.get",
        "tts.bundle.status",
        "tts.bundle.install",
        "tts.bundle.cancel",
        "tts.playback.observe",
    }
)
AUTHORIZATION_TTL_SECONDS = 300
PLAYBACK_TTL_SECONDS = 300
MAX_AUTHORIZATIONS = 32
MAX_ACTIVE_SYNTHESIS = 2


class TTSBoundaryError(RuntimeError):
    def __init__(self, code: str, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable

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


@dataclass
class _BundleTask:
    task_id: str
    bundle_key: str
    cancel: threading.Event
    thread: threading.Thread | None = None
    state: str = "starting"
    progress: int = 0
    result: dict[str, Any] | None = None
    error: dict[str, Any] | None = None


@dataclass
class _RuntimeStatus:
    provider: str
    endpoint_kind: str
    state: str
    error_code: str | None
    updated_at: str


class TTSBoundary:
    def __init__(
        self,
        generation_id: str,
        generation_credential: str,
        app_root: Path,
        *,
        session_provider: Callable[[], object | None],
        event_publisher: Callable[[dict[str, Any]], None] | None = None,
        recording_store: VoiceRecordingStore | None = None,
        synthesis_factory: Callable[..., object] | None = None,
    ) -> None:
        self._generation_id = generation_id
        self._generation_credential = generation_credential
        self._app_root = Path(app_root)
        self._session_provider = session_provider
        self._event_publisher = event_publisher
        self._recordings = recording_store or VoiceRecordingStore(self._app_root)
        self._synthesis_factory = synthesis_factory or TTSSynthesisService
        self._lock = threading.RLock()
        self._authorizations: dict[tuple[str, int], _Authorization] = {}
        self._handles: dict[str, object] = {}
        self._service: object | None = None
        self._bundle_task: _BundleTask | None = None
        self._runtime = _RuntimeStatus(
            "gpt-sovits", "managed", "waiting_for_session", None, self._now()
        )
        self._warmup_thread: threading.Thread | None = None
        self._test_lock = threading.Lock()
        self._closed = False

    def set_event_publisher(self, publisher: Callable[[dict[str, Any]], None]) -> None:
        with self._lock:
            self._event_publisher = publisher

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
    ) -> None:
        if not text.strip() or segment_index < 0:
            return
        with self._lock:
            if self._closed:
                return
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
            elif name == "tts.settings.test":
                payload = self._handle_settings_test(request)
            elif name == "tts.status.get":
                payload = self._handle_status_get(request)
            elif name == "tts.bundle.status":
                payload = self._bundle_status(request)
            elif name == "tts.bundle.install":
                payload = self._bundle_install(request)
            elif name == "tts.bundle.cancel":
                payload = self._bundle_cancel(request)
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
        with self._lock:
            if self._closed:
                return
            self._closed = True
            handles = tuple(self._handles.values())
            self._handles.clear()
            service = self._service
            self._service = None
            self._authorizations.clear()
            bundle_task = self._bundle_task
            if bundle_task is not None:
                bundle_task.cancel.set()
            warmup_thread = self._warmup_thread
            self._set_runtime_locked(state="stopping")
        for handle in handles:
            cancel = getattr(handle, "cancel", None)
            if callable(cancel):
                cancel()
        if service is not None:
            getattr(service, "close")()
        if bundle_task is not None and bundle_task.thread is not None:
            bundle_task.thread.join(timeout=3)
        if warmup_thread is not None and warmup_thread is not threading.current_thread():
            warmup_thread.join(timeout=3)
        self._recordings.cleanup_generation(self._generation_id)

    def on_session_ready(self) -> None:
        """Warm an enabled provider after Assistant publishes its session."""
        with self._lock:
            if self._closed or (self._warmup_thread is not None and self._warmup_thread.is_alive()):
                return
        try:
            settings = self._load_settings(validate_enabled=False)
        except TTSBoundaryError as exc:
            with self._lock:
                self._set_runtime_locked(state="failed", error_code=exc.code)
            return
        with self._lock:
            self._set_runtime_locked(
                provider=str(settings.provider),
                endpoint_kind=self._endpoint_kind_for_settings(settings),
                state="starting" if settings.enabled else "disabled",
                error_code=None,
            )
            if not settings.enabled:
                return
            worker = threading.Thread(
                target=self._warmup,
                args=(settings,),
                name="sakura-runtime-v2-tts-warmup",
                daemon=True,
            )
            self._warmup_thread = worker
        worker.start()

    def _retry_failed_runtime_after_bundle(self, provider: str) -> None:
        """Rebuild a failed managed service after its runtime bundle becomes available."""
        try:
            settings = self._load_settings(validate_enabled=False)
        except TTSBoundaryError:
            return
        if not settings.enabled or str(settings.provider) != provider:
            return
        if provider == "gpt-sovits" and getattr(settings, "custom_base_url", None):
            return
        with self._lock:
            if (
                self._closed
                or self._runtime.provider != provider
                or self._runtime.state != "failed"
                or self._handles
                or (self._warmup_thread is not None and self._warmup_thread.is_alive())
            ):
                return
            stale_service = self._service
            self._service = None
        if stale_service is not None:
            try:
                getattr(stale_service, "close")()
            except Exception:
                pass
        self.on_session_ready()

    def _warmup(self, settings: Any) -> None:
        provider = str(settings.provider)
        log_event(
            "TTS", "TTS startup started",
            {"provider": provider, "generation": self._generation_id},
            event="tts.startup.started",
        )
        try:
            service = self._service_for_current_session(settings=settings, ensure_ready=False)
            ensure_ready = getattr(service, "ensure_ready", None)
            ready, detail = ensure_ready() if callable(ensure_ready) else (True, "")
            if not ready:
                raise TTSSynthesisError(detail)
        except (TTSBoundaryError, TTSSynthesisError, TTSSynthesisClosed) as exc:
            code = self._stable_synthesis_error(exc)
            with self._lock:
                if self._closed:
                    state = "stopping"
                else:
                    state = "failed"
                    self._set_runtime_locked(provider=provider, state=state, error_code=code)
            event_name = "tts.startup.cancelled" if state == "stopping" else "tts.startup.failed"
            log_event(
                "TTS", "TTS startup did not become ready",
                {"provider": provider, "generation": self._generation_id, "code": code},
                event=event_name,
                severity="warning" if state == "failed" else "info",
            )
            return
        with self._lock:
            if self._closed:
                return
            self._set_runtime_locked(
                provider=provider,
                endpoint_kind=str(getattr(service, "endpoint_kind", self._endpoint_kind_for_settings(settings))),
                state="ready",
                error_code=None,
            )
        log_event(
            "TTS", "TTS startup ready",
            {"provider": provider, "generation": self._generation_id},
            event="tts.startup.ready",
        )

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
                "provider": self._runtime.provider,
                "operation_id": operation_id,
                "segment_index": segment_index,
                "request_id": request_id,
            },
            event="tts.synthesis.started",
        )

        source: Path | None = None
        try:
            service = self._service_for_current_session()
            handle = getattr(service, "synthesize")(
                authorization.text,
                authorization.tone,
                request_id=request_id,
            )
            with self._lock:
                self._handles[request_id] = handle
            try:
                synthesized: SynthesizedAudio = handle.result(
                    timeout=self._synthesis_timeout_seconds() + 5
                )
            except concurrent.futures.TimeoutError as exc:
                handle.cancel()
                raise TTSBoundaryError(
                    "TTS_SYNTHESIS_TIMEOUT", "TTS synthesis timed out", retryable=True
                ) from exc
            source = synthesized.path
            try:
                recording = self._recordings.commit(
                    source,
                    character_id=authorization.character_id,
                    history_entry_id=authorization.history_entry_id,
                    tone=authorization.tone,
                    portrait=authorization.portrait,
                    provider=str(getattr(service, "provider", "unknown")),
                )
            except VoiceRecordingError:
                raise
            except (OSError, ValueError) as exc:
                raise VoiceRecordingError(
                    "AUDIO_RECORDING_INVALID",
                    "recording could not be committed",
                    stage="commit",
                ) from exc
            log_event(
                "TTS", "TTS recording committed",
                {
                    "provider": str(getattr(service, "provider", "unknown")),
                    "operation_id": operation_id,
                    "segment_index": segment_index,
                    "request_id": request_id,
                    "recording_id": recording.recording_id,
                    "bytes": recording.byte_length,
                },
                event="tts.recording.committed",
            )
            expires = datetime.now(timezone.utc) + timedelta(seconds=PLAYBACK_TTL_SECONDS)
            playback = self._recordings.create_playback_copy(
                recording.recording_id,
                generation_id=self._generation_id,
                expires_at=expires.isoformat(timespec="seconds"),
            )
            descriptor = {
                "opaqueId": playback.opaque_id,
                "recordingId": recording.recording_id,
                "mediaType": playback.media_type,
                "byteLength": playback.byte_length,
                "expiresAt": playback.expires_at,
            }
            with self._lock:
                authorization.state = "ready"
                self._set_runtime_locked(
                    provider=str(getattr(service, "provider", "unknown")),
                    endpoint_kind=str(getattr(service, "endpoint_kind", self._runtime.endpoint_kind)),
                    state="ready",
                    error_code=None,
                )
            log_event(
                "TTS", "TTS synthesis ready",
                {
                    "provider": str(getattr(service, "provider", "unknown")),
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
            self._log_synthesis_terminal(authorization, error.code, started_at, "failed")
            self._publish_failure(request, authorization, error)
            raise
        except (TTSSynthesisCancelled, TTSSynthesisClosed) as error:
            self._mark_failed(authorization)
            wrapped = TTSBoundaryError("TTS_SYNTHESIS_CANCELLED", str(error))
            self._log_synthesis_terminal(authorization, wrapped.code, started_at, "cancelled")
            self._publish(request, "tts.synthesis.cancelled", self._segment_payload(authorization))
            raise wrapped from error
        except (TTSSynthesisError, VoiceRecordingError, OSError, ValueError) as error:
            self._mark_failed(authorization)
            code = self._stable_synthesis_error(error)
            if isinstance(error, (VoiceRecordingError, OSError, ValueError)) and not isinstance(error, TTSSynthesisError):
                log_event(
                    "TTS", "TTS recording commit failed",
                    {
                        "provider": self._runtime.provider,
                        "operation_id": authorization.operation_id,
                        "segment_index": authorization.segment_index,
                        "request_id": authorization.request_id,
                        "code": code,
                        "stage": getattr(error, "stage", "commit"),
                    },
                    event="tts.recording.failed",
                    severity="warning",
                )
            wrapped = TTSBoundaryError(
                code,
                "TTS synthesis failed",
                retryable=code in {
                    "TTS_SERVICE_UNAVAILABLE",
                    "RUNTIME_START_FAILED",
                    "RUNTIME_UNAVAILABLE",
                    "CONNECTION_FAILED",
                    "REQUEST_TIMEOUT",
                },
            )
            self._log_synthesis_terminal(authorization, code, started_at, "failed")
            self._publish_failure(request, authorization, wrapped)
            raise wrapped from error
        finally:
            if source is not None:
                source.unlink(missing_ok=True)
            with self._lock:
                self._handles.pop(request_id, None)

    def _handle_cancel(self, request: Mapping[str, Any]) -> dict[str, Any]:
        payload = request.get("payload")
        if not isinstance(payload, Mapping) or set(payload) != {"requestId"}:
            raise TTSBoundaryError("TTS_SYNTHESIS_CANCELLED", "invalid cancellation identity")
        request_id = payload.get("requestId")
        if not isinstance(request_id, str) or not request_id.strip():
            raise TTSBoundaryError("TTS_SYNTHESIS_CANCELLED", "invalid cancellation identity")
        with self._lock:
            handle = self._handles.get(request_id)
        accepted = bool(handle is not None and getattr(handle, "cancel")())
        return {"accepted": accepted, "requestId": request_id}

    def _handle_settings_get(self, request: Mapping[str, Any]) -> dict[str, Any]:
        if request.get("payload") not in ({}, None):
            raise TTSBoundaryError("INVALID_TTS_SETTINGS", "settings get payload must be empty")
        settings = self._load_settings(validate_enabled=False)
        return {"settings": self._settings_dto(settings), "coreRestartRequired": False}

    def _handle_settings_save(self, request: Mapping[str, Any]) -> dict[str, Any]:
        payload = request.get("payload")
        if not isinstance(payload, Mapping) or set(payload) != {"settings"}:
            raise TTSBoundaryError("INVALID_TTS_SETTINGS", "settings save payload is invalid")
        draft = payload.get("settings")
        if not isinstance(draft, Mapping):
            raise TTSBoundaryError("INVALID_TTS_SETTINGS", "settings draft is invalid")
        current = self._load_settings(validate_enabled=False)
        try:
            from app.config.settings_service import AppSettingsService
            updated = self._settings_from_draft(current, draft, validate=True)
            AppSettingsService(self._app_root).save_tts_settings(updated)
        except (OSError, RuntimeError, TypeError, ValueError) as exc:
            raise TTSBoundaryError("INVALID_TTS_SETTINGS", "TTS settings could not be saved") from exc
        log_event(
            "TTS", "TTS settings saved",
            {"provider": str(updated.provider), "status": "enabled" if updated.enabled else "disabled"},
            event="tts.settings.saved",
        )
        return {"settings": self._settings_dto(updated), "coreRestartRequired": True}

    def _handle_status_get(self, request: Mapping[str, Any]) -> dict[str, Any]:
        if request.get("payload") not in ({}, None):
            raise TTSBoundaryError("INVALID_TTS_SETTINGS", "status payload must be empty")
        settings = self._load_settings(validate_enabled=False)
        from app.voice.tts_bundle import (
            TTS_BUNDLES,
            compatible_tts_bundles,
            default_bundle_work_dir,
            recommend_tts_bundle,
        )

        compatible = {item.key: item for item in compatible_tts_bundles()}
        recommended = recommend_tts_bundle()
        with self._lock:
            # The installer publishes its terminal task state only after the
            # final directory replacement has completed. Keep the filesystem
            # scan under the same lock so a status response cannot combine a
            # pre-install directory view with a completed task snapshot.
            bundle_rows = []
            installed_by_provider: dict[str, bool] = {}
            for item in TTS_BUNDLES:
                installed = default_bundle_work_dir(item, self._app_root).is_dir()
                installed_by_provider[item.provider] = (
                    installed_by_provider.get(item.provider, False) or installed
                )
                if item.key not in compatible:
                    continue
                bundle_rows.append(
                    {
                        "key": item.key,
                        "label": item.label,
                        "provider": item.provider,
                        "installed": installed,
                        "size": int(item.size),
                        "recommended": bool(
                            recommended is not None and item.key == recommended.key
                        ),
                    }
                )
            work_dir_installed = bool(
                settings.work_dir is not None and Path(settings.work_dir).is_dir()
            )
            if work_dir_installed:
                installed_by_provider[str(settings.provider)] = True
            providers = []
            for provider_id, label in (
                ("gpt-sovits", "GPT-SoVITS"),
                ("genie-tts", "Genie TTS"),
            ):
                if (
                    provider_id == "gpt-sovits"
                    and provider_id == str(settings.provider)
                    and bool(getattr(settings, "custom_base_url", None))
                ):
                    availability = "configured"
                elif installed_by_provider.get(provider_id, False):
                    availability = "installed"
                elif any(item.provider == provider_id for item in compatible.values()):
                    availability = "not_installed"
                else:
                    availability = "unsupported"
                providers.append(
                    {"id": provider_id, "label": label, "availability": availability}
                )
            if self._runtime.state == "waiting_for_session":
                self._set_runtime_locked(
                    provider=str(settings.provider),
                    endpoint_kind=self._endpoint_kind_for_settings(settings),
                    state="waiting_for_session" if settings.enabled else "disabled",
                    error_code=None,
                )
            active = self._active_task_dto_locked()
            runtime = {
                "provider": self._runtime.provider,
                "endpointKind": self._runtime.endpoint_kind,
                "state": self._runtime.state,
                "errorCode": self._runtime.error_code,
                "updatedAt": self._runtime.updated_at,
            }
        return {
            "schemaVersion": 1,
            "enabled": bool(settings.enabled),
            "selectedProvider": str(settings.provider),
            "providers": providers,
            "bundles": bundle_rows,
            "runtime": runtime,
            "activeTask": active,
        }

    def _handle_settings_test(self, request: Mapping[str, Any]) -> dict[str, Any]:
        payload = request.get("payload")
        if not isinstance(payload, Mapping) or set(payload) != {"settings"}:
            raise TTSBoundaryError("INVALID_TTS_SETTINGS", "test settings payload is invalid")
        draft = payload.get("settings")
        if not isinstance(draft, Mapping):
            raise TTSBoundaryError("INVALID_TTS_SETTINGS", "test settings draft is invalid")
        try:
            from dataclasses import replace
            settings = self._settings_from_draft(
                self._load_settings(validate_enabled=False), draft, validate=False
            )
            settings = replace(settings, enabled=True)
            settings.validate()
        except (RuntimeError, TypeError, ValueError) as exc:
            raise TTSBoundaryError("INVALID_TTS_SETTINGS", "test settings draft is invalid") from exc
        provider = str(settings.provider)
        request_id = f"tts-test-{uuid.uuid4().hex}"
        log_event(
            "TTS", "TTS test started",
            {"provider": provider, "request_id": request_id},
            event="tts.test.started",
        )
        with self._test_lock:
            with self._lock:
                current_service = self._service
            reuse = current_service is not None and self._service_matches_settings(current_service, settings)
            service = current_service if reuse else self._synthesis_factory(
                settings,
                base_dir=self._app_root,
                cache_dir=StoragePaths(self._app_root).runtime_v2_tts_generation_dir(
                    self._generation_id
                ),
            )
            if not reuse and current_service is not None:
                getattr(current_service, "close")()
                with self._lock:
                    if self._service is current_service:
                        self._service = None
            try:
                handle = getattr(service, "synthesize")(
                    "音声テストです。", "neutral", request_id=request_id
                )
                synthesized = handle.result(timeout=int(settings.timeout_seconds) + 5)
            except Exception as exc:
                code = self._stable_synthesis_error(exc)
                log_event(
                    "TTS", "TTS test failed",
                    {"provider": provider, "request_id": request_id, "code": code},
                    event="tts.test.failed",
                    severity="warning",
                )
                raise TTSBoundaryError(code, "TTS test failed", retryable=True) from exc
            finally:
                if not reuse:
                    getattr(service, "close")()
                    # Restore the saved enabled configuration in the background.
                    self.on_session_ready()
        expires = datetime.now(timezone.utc) + timedelta(seconds=PLAYBACK_TTL_SECONDS)
        opaque_id = uuid.uuid4().hex
        target = StoragePaths(self._app_root).runtime_v2_tts_generation_dir(self._generation_id) / f"{opaque_id}.wav"
        target.parent.mkdir(parents=True, exist_ok=True)
        synthesized.path.replace(target)
        log_event(
            "TTS", "TTS test ready",
            {"provider": provider, "request_id": request_id, "bytes": target.stat().st_size},
            event="tts.test.ready",
        )
        return {
            "provider": provider,
            "opaqueId": opaque_id,
            "recordingId": None,
            "mediaType": "audio/wav",
            "byteLength": target.stat().st_size,
            "expiresAt": expires.isoformat(timespec="seconds"),
        }

    def _bundle_status(self, request: Mapping[str, Any]) -> dict[str, Any]:
        if request.get("payload") not in ({}, None):
            raise TTSBoundaryError("INVALID_TTS_BUNDLE", "bundle status payload must be empty")
        from app.voice.tts_bundle import compatible_tts_bundles, default_bundle_work_dir

        with self._lock:
            active_dto = self._active_task_dto_locked()
        return {
            "bundles": [
                {
                    "key": item.key,
                    "label": item.label,
                    "provider": item.provider,
                    "installed": default_bundle_work_dir(item, self._app_root).is_dir(),
                    "size": item.size,
                }
                for item in compatible_tts_bundles()
            ],
            "activeTask": active_dto,
        }

    def _bundle_install(self, request: Mapping[str, Any]) -> dict[str, Any]:
        payload = request.get("payload")
        if not isinstance(payload, Mapping) or set(payload) != {"bundleKey"}:
            raise TTSBoundaryError("INVALID_TTS_BUNDLE", "bundle install payload is invalid")
        bundle_key = payload.get("bundleKey")
        if not isinstance(bundle_key, str) or not bundle_key.strip():
            raise TTSBoundaryError("INVALID_TTS_BUNDLE", "bundle identity is invalid")
        from app.voice.tts_bundle import compatible_tts_bundles

        entry = next((item for item in compatible_tts_bundles() if item.key == bundle_key), None)
        if entry is None:
            raise TTSBoundaryError("INVALID_TTS_BUNDLE", "bundle is unavailable on this platform")
        with self._lock:
            current = self._bundle_task
            if current is not None and current.state in {"starting", "running"}:
                raise TTSBoundaryError(
                    "TTS_SERVICE_UNAVAILABLE", "another TTS bundle task is active", retryable=True
                )
            task = _BundleTask(f"tts-bundle-{uuid.uuid4().hex}", entry.key, threading.Event())
            self._bundle_task = task
        log_event(
            "TTS", "TTS bundle installation started",
            {"provider": entry.provider, "status": "starting", "progress": 0},
            event="tts.bundle.started",
        )

        def run() -> None:
            from app.voice.tts_bundle import install_tts_bundle

            # The install acknowledgement completes before this worker.  Core
            # events are request-scoped, so publishing with that completed ID
            # would make the Rust router fail closed.  The settings UI polls
            # activeTask for progress and terminal state instead.
            def check_cancel() -> None:
                if task.cancel.is_set():
                    raise TTSSynthesisCancelled("bundle installation cancelled")

            def progress(value: int) -> None:
                with self._lock:
                    task.state = "running"
                    task.progress = max(task.progress, max(0, min(100, int(value))))
                    state = task.state
                    task_progress = task.progress
                log_event(
                    "TTS", "TTS bundle installation progress",
                    {"provider": entry.provider, "stage": state, "progress": task_progress},
                    event="tts.bundle.progress",
                    verbosity=3,
                )

            try:
                result = install_tts_bundle(
                    entry,
                    self._app_root,
                    check_cancel=check_cancel,
                    on_progress=progress,
                )
                result_payload = {
                    "provider": result.provider,
                    "workDir": str(result.work_dir),
                    "pythonPath": str(result.python_path) if result.python_path else "",
                    "ttsConfigPath": str(result.tts_config_path) if result.tts_config_path else "",
                }
                log_event(
                    "TTS", "TTS bundle installation completed",
                    {"provider": entry.provider, "status": "completed", "progress": 100},
                    event="tts.bundle.completed",
                )
                with self._lock:
                    task.result = result_payload
                    task.state = "completed"
                    task.progress = 100
                self._retry_failed_runtime_after_bundle(entry.provider)
            except TTSSynthesisCancelled:
                with self._lock:
                    task_progress = task.progress
                log_event(
                    "TTS", "TTS bundle installation cancelled",
                    {"provider": entry.provider, "status": "cancelled", "progress": task_progress},
                    event="tts.bundle.cancelled",
                )
                with self._lock:
                    task.state = "cancelled"
            except Exception:
                failure = error_payload("TTS_SERVICE_UNAVAILABLE", "TTS bundle installation failed")
                with self._lock:
                    task_progress = task.progress
                log_event(
                    "TTS", "TTS bundle installation failed",
                    {"provider": entry.provider, "status": "failed", "progress": task_progress, "code": "TTS_SERVICE_UNAVAILABLE"},
                    event="tts.bundle.failed",
                    severity="warning",
                )
                with self._lock:
                    task.state = "failed"
                    task.error = failure

        task.thread = threading.Thread(
            target=run,
            name=f"sakura-{task.task_id}",
            daemon=True,
        )
        task.thread.start()
        return {
            "accepted": True,
            "taskId": task.task_id,
            "bundleKey": task.bundle_key,
            "state": task.state,
        }

    def _bundle_cancel(self, request: Mapping[str, Any]) -> dict[str, Any]:
        payload = request.get("payload")
        if not isinstance(payload, Mapping) or set(payload) != {"taskId"}:
            raise TTSBoundaryError("INVALID_TTS_BUNDLE", "bundle cancel payload is invalid")
        task_id = payload.get("taskId")
        with self._lock:
            task = self._bundle_task
            accepted = bool(
                isinstance(task_id, str)
                and task is not None
                and task.task_id == task_id
                and task.state in {"starting", "running"}
            )
            if accepted:
                assert task is not None
                task.cancel.set()
            thread = task.thread if accepted and task is not None else None
        if thread is not None:
            thread.join(timeout=3)
        return {
            "accepted": accepted,
            "taskId": task_id,
            "joined": bool(thread is not None and not thread.is_alive()),
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
        session = self._session_provider()
        worker = getattr(session, "plugin_worker", None) if session is not None else None
        if worker is not None:
            event_name = "tts.start" if state == "started" else "tts.end"
            summary = {
                "playbackId": playback_id,
                "recordingId": recording_id,
                "outcome": state,
            }
            if error_code is not None:
                summary["errorCode"] = error_code
            try:
                getattr(worker, "emit_event")(event_name, summary)
            except Exception:
                # Plugin publication is observational and must never affect playback.
                pass
        return {"accepted": True}

    def _service_for_current_session(
        self,
        *,
        settings: Any | None = None,
        ensure_ready: bool = True,
    ) -> object:
        with self._lock:
            self._ensure_open_locked()
            selected = self._service
        if selected is None:
            settings = settings or self._load_settings(validate_enabled=True)
            if not settings.enabled:
                raise TTSBoundaryError("TTS_SERVICE_UNAVAILABLE", "TTS is disabled")
            service = self._synthesis_factory(
                settings,
                base_dir=self._app_root,
                cache_dir=StoragePaths(self._app_root).runtime_v2_tts_generation_dir(self._generation_id),
            )
            with self._lock:
                if self._closed:
                    getattr(service, "close")()
                    raise TTSBoundaryError("STALE_GENERATION", "TTS generation is closed")
                if self._service is None:
                    self._service = service
                else:
                    getattr(service, "close")()
                selected = self._service
        if ensure_ready:
            ensure = getattr(selected, "ensure_ready", None)
            if callable(ensure):
                ready, detail = ensure()
                if not ready:
                    raise TTSBoundaryError(
                        self._stable_synthesis_error(TTSSynthesisError(detail)),
                        "TTS service is unavailable",
                        retryable=True,
                    )
        return selected

    def _load_settings(self, *, validate_enabled: bool) -> Any:
        from dataclasses import replace

        from app.config.settings_service import AppSettingsService

        session = self._session_provider()
        character = getattr(session, "character", None) if session is not None else None
        try:
            settings = AppSettingsService(self._app_root).load_tts_settings(
                validate_enabled=validate_enabled,
                character_profile=character,
            )
            # Legacy api.yaml files intentionally keep an empty work_dir.  In
            # Runtime v2 a bundled provider must still use the installed bundle
            # that the status DTO reports as available; otherwise the service
            # is treated as remote and startup stops after a failed port probe.
            managed_provider = (
                str(settings.provider) == "genie-tts"
                or (
                    str(settings.provider) == "gpt-sovits"
                    and getattr(settings, "custom_base_url", None) is None
                )
            )
            if settings.work_dir is None and managed_provider:
                from app.voice.runtime_compat import find_usable_runtime_python
                from app.voice.tts_bundle import default_provider_bundle_work_dir

                candidate = default_provider_bundle_work_dir(
                    str(settings.provider), self._app_root
                )
                if (
                    candidate is not None
                    and candidate.is_dir()
                    and find_usable_runtime_python(candidate / "runtime") is not None
                ):
                    settings = replace(settings, work_dir=candidate.resolve())
            return settings
        except (OSError, RuntimeError, ValueError) as exc:
            raise TTSBoundaryError("TTS_SERVICE_UNAVAILABLE", "TTS settings are unavailable") from exc

    def _synthesis_timeout_seconds(self) -> int:
        settings = getattr(getattr(self._service, "_supervisor", None), "settings", None)
        return int(getattr(settings, "timeout_seconds", 60))

    def _settings_dto(self, settings: Any) -> dict[str, Any]:
        return {
            "enabled": bool(settings.enabled),
            "provider": str(settings.provider),
            "apiUrl": str(settings.api_url),
            "customBaseUrl": str(getattr(settings, "custom_base_url", None) or ""),
            "ttsPath": str(getattr(settings, "tts_path", "/tts")),
            "remoteReferenceRoot": str(getattr(settings, "remote_reference_root", None) or ""),
            "workDir": str(settings.work_dir) if settings.work_dir is not None else "",
            "pythonPath": str(settings.python_path) if settings.python_path is not None else "",
            "timeoutSeconds": int(settings.timeout_seconds),
        }

    def _optional_path(self, value: Any) -> Path | None:
        text = str(value or "").strip()
        if not text:
            return None
        path = Path(text)
        return path if path.is_absolute() else self._app_root / path

    def _settings_from_draft(self, current: Any, draft: Mapping[str, Any], *, validate: bool) -> Any:
        from dataclasses import replace
        from app.config.settings_service import _join_gpt_sovits_url
        from app.voice.tts_settings import _normalize_tts_provider

        allowed = {
            "enabled", "provider", "apiUrl", "customBaseUrl", "ttsPath",
            "remoteReferenceRoot", "workDir", "pythonPath", "timeoutSeconds",
        }
        if set(draft) != allowed:
            raise ValueError("settings fields are invalid")
        enabled = draft["enabled"]
        timeout = draft["timeoutSeconds"]
        if not isinstance(enabled, bool) or isinstance(timeout, bool) or not isinstance(timeout, int):
            raise ValueError("settings scalar is invalid")
        provider = _normalize_tts_provider(str(draft["provider"]), True)
        custom_base_url = str(draft["customBaseUrl"] or "").strip().rstrip("/") or None
        tts_path = str(draft["ttsPath"] or "/tts").strip()
        if not tts_path.startswith("/"):
            tts_path = f"/{tts_path}"
        api_url = (
            _join_gpt_sovits_url(custom_base_url, tts_path)
            if provider == "gpt-sovits"
            else str(draft["apiUrl"]).strip()
        )
        updated = replace(
            current,
            enabled=enabled,
            provider=provider,
            api_url=api_url,
            custom_base_url=custom_base_url,
            tts_path=tts_path,
            remote_reference_root=str(draft["remoteReferenceRoot"] or "").strip() or None,
            work_dir=self._optional_path(draft["workDir"]),
            python_path=self._optional_path(draft["pythonPath"]),
            timeout_seconds=max(3, min(300, timeout)),
        )
        if validate and updated.enabled:
            updated.validate()
        return updated

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
            if item.expires_at <= now and item.state != "synthesizing":
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
    ) -> None:
        event_name = f"tts.synthesis.{outcome}"
        log_event(
            "TTS", "TTS synthesis did not complete",
            {
                "provider": self._runtime.provider,
                "operation_id": authorization.operation_id,
                "segment_index": authorization.segment_index,
                "request_id": authorization.request_id,
                "code": code,
                "elapsed_ms": round((monotonic() - started_at) * 1000),
            },
            event=event_name,
            severity="warning" if outcome == "failed" else "info",
        )

    def _active_task_dto_locked(self) -> dict[str, Any] | None:
        active = self._bundle_task
        if active is None:
            return None
        return {
            "taskId": active.task_id,
            "bundleKey": active.bundle_key,
            "state": active.state,
            "progress": active.progress,
            "cancellable": active.state in {"starting", "running"},
            "result": active.result,
            "error": active.error,
        }

    def _set_runtime_locked(
        self,
        *,
        provider: str | None = None,
        endpoint_kind: str | None = None,
        state: str,
        error_code: str | None = None,
    ) -> None:
        self._runtime = _RuntimeStatus(
            provider=provider or self._runtime.provider,
            endpoint_kind=endpoint_kind or self._runtime.endpoint_kind,
            state=state,
            error_code=error_code,
            updated_at=self._now(),
        )

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat(timespec="seconds")

    @staticmethod
    def _service_matches_settings(service: object, settings: Any) -> bool:
        current = getattr(getattr(service, "_supervisor", None), "settings", None)
        if current is None:
            return str(getattr(service, "provider", "")) == str(settings.provider)
        fields = (
            "provider", "api_url", "custom_base_url", "tts_path", "remote_reference_root",
            "work_dir", "python_path", "timeout_seconds",
        )
        return all(getattr(current, field, None) == getattr(settings, field, None) for field in fields)

    @staticmethod
    def _stable_synthesis_error(error: BaseException) -> str:
        if isinstance(error, VoiceRecordingError):
            return error.code
        text = str(error)
        for code in (
            "PROVIDER_NOT_FOUND",
            "INVALID_CONFIGURATION",
            "RUNTIME_START_FAILED",
            "RUNTIME_UNAVAILABLE",
            "CONNECTION_FAILED",
            "REQUEST_TIMEOUT",
            "SYNTHESIS_FAILED",
            "INVALID_AUDIO_RESPONSE",
            "REFERENCE_AUDIO_UNAVAILABLE",
            "TTS_STALE_PROCESS_KILL_FAILED",
            "TTS_PORT_OCCUPIED_BY_OTHER_PROCESS",
            "TTS_SYNTHESIS_TIMEOUT",
            "TTS_SYNTHESIS_CANCELLED",
        ):
            if code in text:
                return code
        return str(getattr(error, "code", "TTS_SERVICE_UNAVAILABLE"))

    @staticmethod
    def _endpoint_kind_for_settings(settings: Any) -> str:
        if str(getattr(settings, "provider", "")) == "gpt-sovits":
            return "custom" if getattr(settings, "custom_base_url", None) else "managed"
        return "managed"

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


__all__ = ["TTSBoundary", "TTSBoundaryError", "TTS_CAPABILITY", "TTS_REQUEST_NAMES"]
