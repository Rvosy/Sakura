"""Core-owned Host Service used by the ordinary Sakura Mobile plugin."""

from __future__ import annotations

import base64
import threading
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.config.character_loader import CharacterRegistry
from app.core_host.character_presentation import project_character_presentation
from app.storage.paths import StoragePaths
from app.storage.timeline import TimelineKind, TimelineStore


class MobileHostError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass
class _MobileChatJob:
    owner_id: str
    operation_id: str
    done: threading.Event
    result: dict[str, Any] | None = None
    error_code: str = ""


class MobileHostService:
    """Expose current-character chat primitives without leaking Core objects."""

    def __init__(
        self,
        user_root: Path,
        *,
        session_provider: Callable[[], object | None],
        chat_boundary_provider: Callable[[], object | None],
        artifact_resolver: Callable[[str], object],
        artifact_releaser: Callable[[str], bool],
    ) -> None:
        self._user_root = Path(user_root)
        self._session_provider = session_provider
        self._chat_boundary_provider = chat_boundary_provider
        self._artifact_resolver = artifact_resolver
        self._artifact_releaser = artifact_releaser
        self._timeline = TimelineStore(StoragePaths(self._user_root).timeline_database())
        self._lock = threading.Lock()
        self._jobs: dict[str, _MobileChatJob] = {}

    def characters(self) -> list[dict[str, str]]:
        session = self._require_session()
        current = getattr(session, "character", None)
        current_id = str(getattr(current, "id", ""))
        if not current_id:
            raise MobileHostError("ASSISTANT_NOT_READY")
        try:
            profiles = CharacterRegistry(self._user_root).profiles
        except Exception as error:
            raise MobileHostError("CHARACTER_REGISTRY_UNAVAILABLE") from error
        return [
            {
                "id": profile.id,
                "name": profile.display_name,
                "initial_message": profile.initial_message,
                "current": "true" if profile.id == current_id else "false",
            }
            for profile in sorted(profiles.values(), key=lambda item: item.display_name.casefold())
        ]

    def history(self, character_id: str, limit: int = 50) -> list[dict[str, str]]:
        profile = self._current_character(character_id)
        try:
            entries, _cursor = self._timeline.read_recent(
                profile.id,
                max(1, min(int(limit), 200)),
            )
        except Exception as error:
            raise MobileHostError("TIMELINE_READ_FAILED") from error
        projected: list[dict[str, str]] = []
        for entry in entries:
            if entry.kind is TimelineKind.HUMAN:
                content = str(entry.payload.get("text") or "").strip()
                role = "user"
                raw_content = content
                translation = ""
            elif entry.kind is TimelineKind.ASSISTANT:
                raw_segments = entry.payload.get("segments")
                if not isinstance(raw_segments, list):
                    continue
                texts: list[str] = []
                raw_texts: list[str] = []
                translations: list[str] = []
                for segment in raw_segments:
                    if not isinstance(segment, Mapping):
                        continue
                    text = str(segment.get("text") or "").strip()
                    translated = str(segment.get("translation") or "").strip()
                    if text:
                        raw_texts.append(text)
                        texts.append(translated or text)
                    if translated:
                        translations.append(translated)
                content = "\n".join(texts)
                raw_content = "\n".join(raw_texts)
                translation = "\n".join(translations)
                role = "assistant"
            else:
                continue
            if content:
                projected.append({
                    "created_at": entry.created_at,
                    "role": role,
                    "content": content,
                    "raw_content": raw_content,
                    "translation": translation,
                })
        return projected

    def begin(
        self,
        plugin_id: str,
        character_id: str,
        text: str,
        artifact_descriptor: Mapping[str, Any] | None = None,
    ) -> dict[str, str]:
        profile = self._current_character(character_id)
        owner_id = str(plugin_id).strip()
        if not owner_id or len(owner_id) > 64:
            raise MobileHostError("PLUGIN_ID_INVALID")
        boundary = self._chat_boundary_provider()
        send = getattr(boundary, "run_host_message", None)
        if not callable(send):
            raise MobileHostError("MOBILE_CHAT_UNAVAILABLE")
        artifact_id = ""
        artifact: object | None = None
        descriptor = artifact_descriptor or {}
        try:
            if descriptor:
                if (
                    not isinstance(descriptor, Mapping)
                    or set(descriptor) != {"artifactId", "mediaType", "byteLength"}
                ):
                    raise MobileHostError("MOBILE_IMAGE_INVALID")
                artifact_id = str(descriptor.get("artifactId") or "")
                artifact = self._artifact_resolver(artifact_id)
                if (
                    getattr(artifact, "plugin_id", "") != owner_id
                    or getattr(artifact, "media_type", "") != descriptor.get("mediaType")
                    or getattr(artifact, "byte_length", -1) != descriptor.get("byteLength")
                    or not str(getattr(artifact, "media_type", "")).startswith("image/")
                ):
                    raise MobileHostError("MOBILE_IMAGE_INVALID")
        except MobileHostError:
            if artifact_id:
                self._release_artifact(artifact_id)
            raise
        except Exception as error:
            if artifact_id:
                self._release_artifact(artifact_id)
            raise MobileHostError("MOBILE_IMAGE_INVALID") from error

        job_id = f"mobile-job-{uuid.uuid4().hex}"
        operation_id = f"mobile-{uuid.uuid4().hex}"
        job = _MobileChatJob(owner_id, operation_id, threading.Event())
        with self._lock:
            self._jobs[job_id] = job

        def run() -> None:
            try:
                image_data_url = ""
                if artifact is not None:
                    payload = getattr(artifact, "path").read_bytes()
                    if len(payload) != getattr(artifact, "byte_length"):
                        raise MobileHostError("MOBILE_IMAGE_INVALID")
                    image_data_url = (
                        f"data:{getattr(artifact, 'media_type')};base64,"
                        + base64.b64encode(payload).decode("ascii")
                    )
                result = send(
                    str(text),
                    image_data_url,
                    operation_id=operation_id,
                )
                if not isinstance(result, Mapping):
                    raise MobileHostError("MOBILE_CHAT_FAILED")
                job.result = {"character_id": str(profile.id), **dict(result)}
            except Exception as error:
                code = getattr(error, "code", None)
                job.error_code = (
                    code if isinstance(code, str) and code else "MOBILE_CHAT_FAILED"
                )
            finally:
                if artifact_id:
                    self._release_artifact(artifact_id)
                job.done.set()

        worker = threading.Thread(
            target=run,
            name=f"sakura-mobile-chat-{job_id[-8:]}",
            daemon=True,
        )
        try:
            worker.start()
        except Exception as error:
            with self._lock:
                self._jobs.pop(job_id, None)
            if artifact_id:
                self._release_artifact(artifact_id)
            raise MobileHostError("MOBILE_CHAT_UNAVAILABLE") from error
        return {"jobId": job_id}

    def poll(self, plugin_id: str, job_id: str) -> dict[str, Any]:
        job = self._owned_job(plugin_id, job_id)
        if not job.done.is_set():
            return {"status": "running"}
        with self._lock:
            self._jobs.pop(job_id, None)
        if job.error_code:
            raise MobileHostError(job.error_code)
        return {"status": "completed", "result": dict(job.result or {})}

    def cancel(self, plugin_id: str, job_id: str) -> dict[str, bool]:
        job = self._owned_job(plugin_id, job_id)
        boundary = self._chat_boundary_provider()
        cancel = getattr(boundary, "cancel_host_message", None)
        accepted = bool(callable(cancel) and cancel(job.operation_id))
        return {"accepted": accepted}

    def revoke_scope(self, plugin_id: str) -> None:
        with self._lock:
            owned = [
                (job_id, job)
                for job_id, job in self._jobs.items()
                if job.owner_id == plugin_id
            ]
            for job_id, _job in owned:
                self._jobs.pop(job_id, None)
        boundary = self._chat_boundary_provider()
        cancel = getattr(boundary, "cancel_host_message", None)
        if callable(cancel):
            for _job_id, job in owned:
                cancel(job.operation_id)

    def theme(self) -> dict[str, object]:
        profile = self._current_character("")
        presentation = project_character_presentation(profile)
        tokens = presentation.get("themeTokens")
        return dict(tokens) if isinstance(tokens, Mapping) else {}

    def _require_session(self) -> object:
        session = self._session_provider()
        if session is None:
            raise MobileHostError("ASSISTANT_NOT_READY")
        return session

    def _current_character(self, character_id: str) -> object:
        profile = getattr(self._require_session(), "character", None)
        current_id = str(getattr(profile, "id", ""))
        requested = str(character_id).strip() or current_id
        if not current_id or requested != current_id:
            raise MobileHostError("MOBILE_CHARACTER_NOT_CURRENT")
        return profile

    def _owned_job(self, plugin_id: str, job_id: str) -> _MobileChatJob:
        with self._lock:
            job = self._jobs.get(str(job_id))
        if job is None or job.owner_id != str(plugin_id):
            raise MobileHostError("MOBILE_CHAT_JOB_NOT_FOUND")
        return job

    def _release_artifact(self, artifact_id: str) -> None:
        try:
            self._artifact_releaser(artifact_id)
        except Exception:
            pass


__all__ = ["MobileHostError", "MobileHostService"]
