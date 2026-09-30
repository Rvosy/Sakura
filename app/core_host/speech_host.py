"""Plugin-scoped preparation of saved reply audio, independent of playback."""

from __future__ import annotations

import threading
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Callable

from app.core_host.screen_host import caller_identity


class SpeechHostError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass
class _SpeechJob:
    owner: tuple[str, str]
    boundary: object
    operation_id: str
    character_id: str
    entry_id: str
    segment_index: int
    done: threading.Event = field(default_factory=threading.Event)
    cancelled: bool = False
    result: dict[str, Any] | None = None
    error: Exception | None = None


class SpeechHostService:
    def __init__(self, *, boundary_provider: Callable, export_audio: Callable,
                 release_artifact: Callable, revoke_exports: Callable,
                 commit_scope: Callable) -> None:
        self._boundary_provider = boundary_provider
        self._export_audio = export_audio
        self._release_artifact = release_artifact
        self._revoke_exports = revoke_exports
        self._commit_scope = commit_scope
        self._lock = threading.RLock()
        self._jobs: dict[str, _SpeechJob] = {}
        self._closed = False
        self._switching_character = False

    def begin(self, character_id: str, history_entry_id: str, segment_index: int) -> dict[str, str]:
        owner = caller_identity()
        if not isinstance(character_id, str):
            raise SpeechHostError("SPEECH_REQUEST_INVALID")
        boundary = self._boundary_provider()
        if boundary is None:
            raise SpeechHostError("SPEECH_UNAVAILABLE")
        operation_id = "speech-" + uuid.uuid4().hex
        job_id = "speech-job-" + uuid.uuid4().hex
        with self._lock:
            if self._closed or self._switching_character:
                raise SpeechHostError("SPEECH_UNAVAILABLE")
            resolved_character = boundary.authorize_history_segment(
                operation_id, history_entry_id, segment_index,
                character_id=character_id.strip() or None,
            )
            job = _SpeechJob(owner, boundary, operation_id, resolved_character,
                             history_entry_id, segment_index)
            try:
                self._commit_scope(owner, lambda: self._jobs.__setitem__(job_id, job))
            except Exception:
                boundary.discard_speech(operation_id, segment_index)
                raise

        def run() -> None:
            try:
                recording = boundary.prepare_speech(operation_id, segment_index)
                with self._lock:
                    if job.cancelled or self._closed or self._jobs.get(job_id) is not job:
                        raise SpeechHostError("TTS_SYNTHESIS_CANCELLED")
                    artifact = self._export_audio(owner, recording)
                    job.result = {
                        "artifact": artifact, "recordingId": recording.recording_id,
                        "characterId": job.character_id, "historyEntryId": job.entry_id,
                        "segmentIndex": job.segment_index,
                    }
            except Exception as error:
                with self._lock:
                    job.error = error
            finally:
                job.done.set()

        worker = threading.Thread(target=run, name="sakura-speech-" + operation_id[-8:], daemon=True)
        try:
            worker.start()
        except Exception as error:
            with self._lock:
                self._jobs.pop(job_id, None)
            boundary.discard_speech(operation_id, segment_index)
            raise SpeechHostError("SPEECH_START_FAILED") from error
        return {"jobId": job_id}

    def poll(self, job_id: str) -> dict[str, Any]:
        with self._lock:
            job = self._job(caller_identity(), job_id)
            if not job.done.is_set():
                return {"status": "running"}
            self._jobs.pop(job_id, None)
            if job.cancelled:
                raise SpeechHostError("TTS_SYNTHESIS_CANCELLED")
            if job.error is not None:
                raise SpeechHostError(getattr(job.error, "code", "TTS_SYNTHESIS_FAILED")) from job.error
            return {"status": "completed", "result": dict(job.result or {})}

    def cancel(self, job_id: str) -> dict[str, bool]:
        with self._lock:
            job = self._job(caller_identity(), job_id)
            accepted = not job.cancelled
            self._cancel(job)
        return {"accepted": accepted}

    def _job(self, owner: tuple[str, str], job_id: str) -> _SpeechJob:
        job = self._jobs.get(job_id) if isinstance(job_id, str) else None
        if job is None or job.owner != owner:
            raise SpeechHostError("SPEECH_JOB_NOT_FOUND")
        return job

    def _cancel(self, job: _SpeechJob) -> None:
        job.cancelled = True
        job.boundary.cancel_speech(job.operation_id)
        if job.result is not None:
            self._release_artifact(job.result["artifact"]["artifactId"])
            job.result = None

    def revoke_scope(self, plugin_id: str) -> None:
        with self._lock:
            jobs = [job for job in self._jobs.values() if job.owner[0] == plugin_id]
            self._jobs = {key: job for key, job in self._jobs.items() if job.owner[0] != plugin_id}
            for job in jobs:
                self._cancel(job)
            self._revoke_exports(plugin_id)

    def invalidate_session(self) -> None:
        with self._lock:
            jobs = list(self._jobs.values())
            self._jobs.clear()
            for job in jobs:
                self._cancel(job)
            self._revoke_exports()

    @contextmanager
    def suspend_for_character_change(self):
        with self._lock:
            self._switching_character = True
        self.invalidate_session()
        try:
            yield
        finally:
            with self._lock:
                self._switching_character = False

    def close(self) -> None:
        with self._lock:
            self._closed = True
        self.invalidate_session()
