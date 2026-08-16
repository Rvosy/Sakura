"""Qt-free synthesis adapter used by the Runtime v2 Core generation."""

from __future__ import annotations

import os
import threading
import uuid
from concurrent.futures import Future
from dataclasses import dataclass
from pathlib import Path

from app.core.interaction import get_interaction_id
from app.core.runtime_resources import ResourceRegistry
from app.voice.tts_service import GenieServiceSupervisor, TTSServiceSupervisor
from app.voice.tts_settings import GPTSoVITSTTSSettings, TTS_PROVIDER_GENIE
from app.voice.tts_synthesis import GenieSynthesisEngine, GPTSoVITSSynthesisEngine, TTSSynthesisQueue
from app.voice.tts_types import TTSPreparedAudio, _TTSRequest


class TTSSynthesisError(RuntimeError):
    code = "TTS_SERVICE_UNAVAILABLE"


class TTSSynthesisCancelled(TTSSynthesisError):
    code = "TTS_SYNTHESIS_CANCELLED"


class TTSSynthesisClosed(TTSSynthesisError):
    code = "STALE_GENERATION"


@dataclass(frozen=True)
class SynthesizedAudio:
    request_id: str
    path: Path
    byte_length: int


class TTSSynthesisHandle:
    def __init__(self, service: "TTSSynthesisService", request_id: str, future: Future) -> None:
        self._service = service
        self.request_id = request_id
        self._future = future

    def result(self, timeout: float | None = None) -> SynthesizedAudio:
        return self._future.result(timeout)

    def cancel(self) -> bool:
        return self._service.cancel(self.request_id)


class TTSSynthesisService:
    def __init__(
        self,
        settings: GPTSoVITSTTSSettings,
        *,
        base_dir: Path,
        cache_dir: Path,
    ) -> None:
        settings.validate()
        self._cache_dir = Path(cache_dir).resolve()
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        self._resources = ResourceRegistry()
        self._closed_event = threading.Event()
        supervisor_cls = GenieServiceSupervisor if settings.provider == TTS_PROVIDER_GENIE else TTSServiceSupervisor
        self._supervisor = supervisor_cls(
            settings,
            base_dir=base_dir,
            resource_manager=self._resources,
            is_closed=self._closed_event.is_set,
            adopt_existing_service=False,
        )
        engine = GenieSynthesisEngine() if settings.provider == TTS_PROVIDER_GENIE else GPTSoVITSSynthesisEngine()
        self._lock = threading.RLock()
        self._readiness = threading.Condition(self._lock)
        self._readiness_state = "idle"
        self._readiness_result: tuple[bool, str] | None = None
        self._closed = False
        self._requests: dict[str, tuple[_TTSRequest, Future[SynthesizedAudio]]] = {}
        self._queue = TTSSynthesisQueue(
            supervisor=self._supervisor,
            engine=engine,
            cache_dir=self._cache_dir,
            resource_manager=self._resources,
            sink=self,
            is_closed=self._is_closed,
        )

    @property
    def provider(self) -> str:
        return str(self._supervisor.settings.provider)

    def ensure_ready(self) -> tuple[bool, str]:
        """Start/probe the generation-owned service exactly once.

        Startup warmup and the first synthesis can race.  They deliberately
        share this condition so only one thread performs process cleanup and
        service startup while the other observes the same bounded result.
        """
        with self._readiness:
            while self._readiness_state == "starting" and not self._closed:
                self._readiness.wait()
            if self._closed:
                raise TTSSynthesisClosed("TTS generation is closed")
            if self._readiness_result is not None:
                return self._readiness_result
            self._readiness_state = "starting"
        try:
            result = self._supervisor.ensure_ready()
        except BaseException as exc:  # supervisor failures become a stable readiness result
            result = (False, str(exc))
        with self._readiness:
            if self._closed:
                self._readiness_state = "closed"
                self._readiness.notify_all()
                raise TTSSynthesisClosed("TTS generation is closed")
            self._readiness_result = (bool(result[0]), str(result[1]))
            self._readiness_state = "ready" if result[0] else "failed"
            self._readiness.notify_all()
            return self._readiness_result

    def synthesize(self, text: str, tone: str, *, request_id: str) -> TTSSynthesisHandle:
        content = str(text).strip()
        identifier = str(request_id).strip()
        if not content or not identifier:
            raise ValueError("TTS request identity and text must not be empty")
        ready, detail = self.ensure_ready()
        if not ready:
            raise TTSSynthesisError(detail)
        with self._lock:
            if self._closed:
                raise TTSSynthesisClosed("TTS generation is closed")
            if identifier in self._requests:
                raise ValueError("duplicate TTS request identity")
            future: Future[SynthesizedAudio] = Future()
            request = _TTSRequest(
                text=content,
                tone=tone,
                request_id=identifier,
                interaction_id=get_interaction_id(),
            )
            self._requests[identifier] = (request, future)
        self._queue.submit(request)
        return TTSSynthesisHandle(self, identifier, future)

    def cancel(self, request_id: str) -> bool:
        with self._lock:
            item = self._requests.get(request_id)
            if item is None:
                return False
            item[0].cancelled = True
        return self._queue.cancel_request(request_id)

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            self._closed_event.set()
            self._readiness_state = "closed"
            self._readiness.notify_all()
            pending = tuple(self._requests.values())
            self._requests.clear()
        self._queue.cancel_all()
        for request, future in pending:
            request.cancelled = True
            if not future.done():
                future.set_exception(TTSSynthesisClosed("TTS generation is closed"))
        self._resources.stop_all()

    def deliver_synthesized(self, request: _TTSRequest, audio_path: str) -> None:
        source = Path(audio_path)
        with self._lock:
            item = self._requests.pop(request.request_id, None)
        if item is None or request.cancelled or self._is_closed():
            source.unlink(missing_ok=True)
            return
        _stored, future = item
        try:
            resolved = source.resolve(strict=True)
            resolved.relative_to(self._cache_dir)
            target = self._cache_dir / f"synth-{uuid.uuid4().hex}.wav"
            if resolved != target:
                os.replace(resolved, target)
            result = SynthesizedAudio(request.request_id, target, target.stat().st_size)
        except (OSError, ValueError) as exc:
            source.unlink(missing_ok=True)
            future.set_exception(TTSSynthesisError(f"TTS audio validation failed: {exc}"))
            return
        future.set_result(result)

    def fail_audio_request(self, request: _TTSRequest, message: str) -> None:
        with self._lock:
            item = self._requests.pop(request.request_id, None)
        if item is not None and not item[1].done():
            item[1].set_exception(TTSSynthesisError(message))

    def skip_audio_request(self, request: _TTSRequest, reason: str) -> None:
        with self._lock:
            item = self._requests.pop(request.request_id, None)
        if item is not None and not item[1].done():
            item[1].set_exception(TTSSynthesisCancelled(reason))

    def deliver_audio(self, *_args) -> None:  # type: ignore[no-untyped-def]
        raise RuntimeError("headless synthesis must use deliver_synthesized")

    def deliver_prepared(self, _handle: TTSPreparedAudio, audio_path: str) -> None:
        Path(audio_path).unlink(missing_ok=True)

    def schedule_cleanup(self, audio_path: Path) -> None:
        Path(audio_path).unlink(missing_ok=True)

    def _is_closed(self) -> bool:
        with self._lock:
            return self._closed


__all__ = [
    "SynthesizedAudio",
    "TTSSynthesisCancelled",
    "TTSSynthesisClosed",
    "TTSSynthesisError",
    "TTSSynthesisHandle",
    "TTSSynthesisService",
]
