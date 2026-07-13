"""无 Qt TTS 合成服务；Python 只生成受控临时音频，不负责播放。"""

from __future__ import annotations

import os
import shutil
import threading
import uuid
from concurrent.futures import Future
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from app.core.interaction import get_interaction_id
from app.core.resource_manager import ResourceManager
from app.storage.paths import StoragePaths
from app.voice.tts_service import GenieServiceSupervisor, TTSServiceSupervisor
from app.voice.tts_settings import GPTSoVITSTTSSettings, TTS_PROVIDER_GENIE
from app.voice.tts_synthesis import (
    GenieSynthesisEngine,
    GPTSoVITSSynthesisEngine,
    TTSSynthesisQueue,
)
from app.voice.tts_types import TTSPreparedAudio, _TTSRequest


DEFAULT_AUDIO_TTL_SECONDS = 300


class TTSSynthesisError(RuntimeError):
    pass


class TTSSynthesisCancelled(TTSSynthesisError):
    pass


class TTSSynthesisClosed(TTSSynthesisError):
    pass


@dataclass(frozen=True)
class TTSAudioResource:
    id: str
    path: Path
    media_type: str
    byte_length: int
    expires_at: str

    def to_private_dto(self) -> dict[str, Any]:
        return {
            "version": 1,
            "id": self.id,
            "path": str(self.path.resolve()),
            "mediaType": self.media_type,
            "byteLength": self.byte_length,
            "expiresAt": self.expires_at,
        }


@dataclass(frozen=True)
class TTSSynthesisResult:
    request_id: str
    resource: TTSAudioResource | None = None
    skipped_reason: str = ""


class TTSSynthesisHandle:
    def __init__(
        self,
        service: "TTSSynthesisService | NullTTSSynthesisService",
        request_id: str,
        future: Future[TTSSynthesisResult],
    ) -> None:
        self._service = service
        self.request_id = request_id
        self._future = future

    def result(self, timeout: float | None = None) -> TTSSynthesisResult:
        return self._future.result(timeout)

    def cancel(self) -> bool:
        return self._service.cancel(self.request_id)


class TTSSynthesisService:
    def __init__(
        self,
        *,
        supervisor: object,
        engine: object,
        cache_dir: Path,
        resource_manager: ResourceManager | None,
        audio_ttl_seconds: int = DEFAULT_AUDIO_TTL_SECONDS,
    ) -> None:
        self._supervisor = supervisor
        self._engine = engine
        self._cache_dir = Path(cache_dir).resolve()
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        self._resource_manager = resource_manager
        self._audio_ttl_seconds = max(1, int(audio_ttl_seconds))
        self._lock = threading.RLock()
        self._closed = False
        self._requests: dict[str, tuple[_TTSRequest, Future[TTSSynthesisResult]]] = {}
        self._queue = TTSSynthesisQueue(
            supervisor=supervisor,
            engine=engine,
            cache_dir=self._cache_dir,
            resource_manager=resource_manager,
            sink=self,
            is_closed=self._is_closed,
        )

    @classmethod
    def from_settings(
        cls,
        settings: GPTSoVITSTTSSettings,
        *,
        base_dir: Path,
        adopt_existing_service: bool = True,
    ) -> "TTSSynthesisService":
        settings.validate()
        resource_manager = ResourceManager()
        supervisor_cls = (
            GenieServiceSupervisor
            if settings.provider == TTS_PROVIDER_GENIE
            else TTSServiceSupervisor
        )
        closed = threading.Event()
        supervisor = supervisor_cls(
            settings,
            base_dir=base_dir,
            resource_manager=resource_manager,
            is_closed=closed.is_set,
            adopt_existing_service=adopt_existing_service,
        )
        engine = (
            GenieSynthesisEngine()
            if settings.provider == TTS_PROVIDER_GENIE
            else GPTSoVITSSynthesisEngine()
        )
        service = cls(
            supervisor=supervisor,
            engine=engine,
            cache_dir=StoragePaths(base_dir).tts_cache_dir,
            resource_manager=resource_manager,
        )
        service._closed_event = closed
        return service

    @property
    def service_ready(self) -> bool:
        return bool(getattr(self._supervisor, "service_ready", False))

    @property
    def text_lang(self) -> str:
        return str(getattr(getattr(self._supervisor, "settings", None), "text_lang", "ja"))

    def ensure_ready(self) -> tuple[bool, str]:
        callback = getattr(self._supervisor, "ensure_ready", None)
        if callable(callback):
            return callback()
        return True, "TTS 合成服务已就绪。"

    def synthesize(
        self,
        text: str,
        tone: str | None = None,
        *,
        request_id: str | None = None,
    ) -> TTSSynthesisHandle:
        content = str(text or "").strip()
        if not content:
            raise TTSSynthesisError("TTS text must not be empty")
        with self._lock:
            if self._closed:
                raise TTSSynthesisClosed("TTS synthesis service is closed")
            identifier = (request_id or f"tts-{uuid.uuid4().hex}").strip()
            if not identifier or identifier in self._requests:
                raise TTSSynthesisError("duplicate or invalid TTS request ID")
            future: Future[TTSSynthesisResult] = Future()
            request = _TTSRequest(
                text=content,
                tone=tone,
                request_id=identifier,
                interaction_id=get_interaction_id(),
            )
            self._requests[identifier] = (request, future)
        self._queue.submit(request)
        return TTSSynthesisHandle(self, identifier, future)

    def adopt_audio(
        self,
        source: Path,
        *,
        text: str,
        tone: str | None = None,
        request_id: str | None = None,
    ) -> TTSSynthesisHandle:
        with self._lock:
            if self._closed:
                raise TTSSynthesisClosed("TTS synthesis service is closed")
            identifier = (request_id or f"tts-{uuid.uuid4().hex}").strip()
            if not identifier or identifier in self._requests:
                raise TTSSynthesisError("duplicate or invalid TTS request ID")
            future: Future[TTSSynthesisResult] = Future()
            request = _TTSRequest(text=text, tone=tone, request_id=identifier)
            self._requests[identifier] = (request, future)
        imported = self._cache_dir / f"import-{uuid.uuid4().hex}.wav"
        try:
            shutil.copyfile(Path(source), imported)
        except OSError as exc:
            self.fail_audio_request(request, f"TTS audio import failed: {exc}")
        else:
            self.deliver_synthesized(request, str(imported))
        return TTSSynthesisHandle(self, identifier, future)

    def cancel(self, request_id: str) -> bool:
        identifier = str(request_id or "").strip()
        with self._lock:
            item = self._requests.get(identifier)
            if item is None:
                return False
            item[0].cancelled = True
        self._queue.cancel_request(identifier)
        return True

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            closed_event = getattr(self, "_closed_event", None)
            if closed_event is not None:
                closed_event.set()
            pending = tuple(self._requests.items())
            self._requests.clear()
        self._queue.cancel_all()
        for _request_id, (request, future) in pending:
            request.cancelled = True
            if not future.done():
                future.set_exception(TTSSynthesisClosed("TTS synthesis service is closed"))
        if self._resource_manager is not None:
            self._resource_manager.stop_all()

    def deliver_synthesized(self, request: _TTSRequest, audio_path: str) -> None:
        source = Path(audio_path)
        with self._lock:
            item = self._requests.pop(request.request_id, None)
        if item is None or request.cancelled or self._is_closed():
            self.schedule_cleanup(source)
            return
        _stored_request, future = item
        try:
            resolved = source.resolve(strict=True)
            resolved.relative_to(self._cache_dir)
            resource_id = f"audio-{uuid.uuid4().hex}"
            target = self._cache_dir / f"{resource_id}.wav"
            if resolved != target:
                os.replace(resolved, target)
            byte_length = target.stat().st_size
            expires_at = (
                datetime.now(timezone.utc) + timedelta(seconds=self._audio_ttl_seconds)
            ).isoformat(timespec="seconds")
            result = TTSSynthesisResult(
                request_id=request.request_id,
                resource=TTSAudioResource(
                    id=resource_id,
                    path=target,
                    media_type="audio/wav",
                    byte_length=byte_length,
                    expires_at=expires_at,
                ),
            )
        except (OSError, ValueError) as exc:
            self.schedule_cleanup(source)
            if not future.done():
                future.set_exception(TTSSynthesisError(f"TTS resource validation failed: {exc}"))
            return
        if not future.done():
            future.set_result(result)

    def deliver_audio(
        self,
        audio_path: str,
        on_started,
        on_finished,
        text: str,
    ) -> None:  # type: ignore[no-untyped-def]
        _ = audio_path, on_started, on_finished, text
        raise RuntimeError("headless TTS sink requires deliver_synthesized")

    def deliver_prepared(self, handle: TTSPreparedAudio, audio_path: str) -> None:
        _ = handle
        self.schedule_cleanup(Path(audio_path))

    def fail_audio_request(self, request: _TTSRequest, message: str) -> None:
        with self._lock:
            item = self._requests.pop(request.request_id, None)
        if item is not None and not item[1].done():
            item[1].set_exception(TTSSynthesisError(message))

    def skip_audio_request(self, request: _TTSRequest, reason: str) -> None:
        with self._lock:
            item = self._requests.pop(request.request_id, None)
        if item is None or item[1].done():
            return
        if request.cancelled or reason in {"请求已取消", "Provider 已关闭"}:
            item[1].set_exception(TTSSynthesisCancelled(reason))
        else:
            item[1].set_result(
                TTSSynthesisResult(request_id=request.request_id, skipped_reason=reason)
            )

    def schedule_cleanup(self, audio_path: Path) -> None:
        try:
            Path(audio_path).unlink(missing_ok=True)
        except OSError:
            pass

    def _is_closed(self) -> bool:
        with self._lock:
            return self._closed


class NullTTSSynthesisService:
    service_ready = False
    text_lang = "ja"

    def ensure_ready(self) -> tuple[bool, str]:
        return True, "TTS 已关闭。"

    def synthesize(
        self,
        text: str,
        tone: str | None = None,
        *,
        request_id: str | None = None,
    ) -> TTSSynthesisHandle:
        _ = text, tone
        identifier = (request_id or f"tts-{uuid.uuid4().hex}").strip()
        future: Future[TTSSynthesisResult] = Future()
        future.set_result(
            TTSSynthesisResult(request_id=identifier, skipped_reason="tts_disabled")
        )
        return TTSSynthesisHandle(self, identifier, future)

    def cancel(self, request_id: str) -> bool:
        _ = request_id
        return False

    def adopt_audio(
        self,
        source: Path,
        *,
        text: str,
        tone: str | None = None,
        request_id: str | None = None,
    ) -> TTSSynthesisHandle:
        _ = source
        return self.synthesize(text, tone, request_id=request_id)

    def close(self) -> None:
        return


def create_tts_synthesis_service(
    settings: GPTSoVITSTTSSettings,
    *,
    base_dir: Path,
) -> TTSSynthesisService | NullTTSSynthesisService:
    if not settings.enabled:
        return NullTTSSynthesisService()
    return TTSSynthesisService.from_settings(settings, base_dir=base_dir)
