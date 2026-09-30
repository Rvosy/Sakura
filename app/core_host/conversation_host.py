"""Plugin-scoped user conversations through the ordinary desktop chat lane."""

from __future__ import annotations

import base64
import threading
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from app.core.runtime_log import diagnostic_attributes, log_event
from app.core_host.screen_host import caller_identity


class ConversationHostError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass
class _ConversationJob:
    owner: tuple[str, str]
    operation_id: str
    boundary: object
    done: threading.Event = field(default_factory=threading.Event)
    publication_lock: object = field(default_factory=threading.Lock)
    started: bool = False
    terminal: bool = False
    revoked: bool = False
    result: dict[str, Any] | None = None
    error: Exception | None = None


class ConversationHostService:
    def __init__(self, *, chat_boundary_provider: Callable, artifact_resolver: Callable,
                 artifact_releaser: Callable, emit_callback: Callable,
                 commit_scope: Callable | None = None) -> None:
        self._boundary_provider = chat_boundary_provider
        self._artifact_resolver = artifact_resolver
        self._artifact_releaser = artifact_releaser
        self._emit = emit_callback
        self._commit_scope = commit_scope or (lambda owner, commit: commit())
        self._lock = threading.Lock()
        self._jobs: dict[str, _ConversationJob] = {}
        self._session_epoch = 0
        self._closed = False

    def begin(self, character_id: str, text: str,
              artifact_descriptor: Mapping[str, Any] | None = None) -> dict[str, str]:
        owner = caller_identity()
        boundary = None
        artifact_id = ""
        operation_id = "conversation-" + uuid.uuid4().hex
        job_id = "conversation-job-" + uuid.uuid4().hex
        try:
            image_data_url = ""
            if artifact_descriptor is not None and artifact_descriptor != {}:
                try:
                    descriptor = artifact_descriptor
                    if not isinstance(descriptor, Mapping) or set(descriptor) != {"artifactId", "mediaType", "byteLength"}:
                        raise ConversationHostError("CHAT_IMAGE_INVALID")
                    artifact = self._artifact_resolver(str(descriptor["artifactId"]))
                    # Do not release another plugin's resource when a descriptor is forged.
                    if artifact.plugin_id != owner[0]:
                        raise ConversationHostError("CHAT_IMAGE_INVALID")
                    artifact_id = str(descriptor["artifactId"])
                    if (artifact.media_type != descriptor["mediaType"] or artifact.byte_length != descriptor["byteLength"]
                            or not artifact.media_type.startswith("image/")):
                        raise ConversationHostError("CHAT_IMAGE_INVALID")
                    payload = artifact.path.read_bytes()
                    if len(payload) != artifact.byte_length:
                        raise ConversationHostError("CHAT_IMAGE_INVALID")
                    image_data_url = f"data:{artifact.media_type};base64," + base64.b64encode(payload).decode("ascii")
                except ConversationHostError:
                    raise
                except Exception as error:
                    raise ConversationHostError("CHAT_IMAGE_INVALID") from error
            if not isinstance(character_id, str) or not isinstance(text, str):
                raise ConversationHostError("INVALID_CHAT_PAYLOAD")
            boundary = self._boundary_provider()
            if boundary is None:
                raise ConversationHostError("ASSISTANT_NOT_READY")
            with self._lock:
                epoch = self._session_epoch
            state = boundary.current_host_state()
            if not state["sessionId"]:
                raise ConversationHostError("ASSISTANT_NOT_READY")
            requested = character_id.strip() or state["characterId"]
            if requested != state["characterId"]:
                raise ConversationHostError("CHAT_CHARACTER_NOT_CURRENT")
            job = _ConversationJob(owner, operation_id, boundary)
            boundary.reserve_host_message(text, image_data_url, operation_id=operation_id,
                expected_character_id=requested, expected_session_id=state["sessionId"])
            with self._lock:
                if self._closed or epoch != self._session_epoch:
                    raise ConversationHostError("CHAT_SESSION_STALE")
                self._commit_scope(owner, lambda: self._jobs.__setitem__(job_id, job))
        except Exception:
            if boundary is not None:
                boundary.abandon_host_message(operation_id)
            self._release_artifact(artifact_id)
            raise

        metadata = {"operationId": operation_id, "sessionId": state["sessionId"],
                    "characterId": requested, "sourcePluginId": owner[0], "presentation": "interactive"}

        def publish(name: str, payload: Mapping) -> None:
            # Once started, only RealChat decides the terminal, including a late
            # scope revocation after the assistant entry was committed.
            with job.publication_lock:
                if job.terminal or (job.revoked and not job.started):
                    return
                job.started = True
                job.terminal = name != "chat.started"
                self._emit("host." + name, {**dict(payload), **metadata})

        def run() -> None:
            try:
                result = boundary.run_reserved_host_message(operation_id, emit=publish)
                job.result = {"character_id": requested, "operationId": operation_id, **result}
            except Exception as error:
                job.error = error
                log_event("Conversation", "用户对话失败",
                    {"operation_id": operation_id, **diagnostic_attributes(error,
                        reason_code=getattr(error, "code", "CHAT_FAILED"), stage="conversation_chat")},
                    event="conversation.chat.failed", severity="error")
            finally:
                self._release_artifact(artifact_id)
                job.done.set()

        worker = threading.Thread(target=run, name="sakura-conversation-" + operation_id[-8:], daemon=True)
        try:
            worker.start()
        except Exception as error:
            with self._lock:
                self._jobs.pop(job_id, None)
            boundary.abandon_host_message(operation_id)
            self._release_artifact(artifact_id)
            raise ConversationHostError("CHAT_START_FAILED") from error
        return {"jobId": job_id, "operationId": operation_id}

    def poll(self, job_id: str) -> dict[str, Any]:
        owner = caller_identity()
        with self._lock:
            job = self._job(owner, job_id)
            if not job.done.is_set():
                return {"status": "running"}
            self._jobs.pop(job_id, None)
        if job.error is not None:
            raise ConversationHostError(getattr(job.error, "code", "CHAT_FAILED")) from job.error
        return {"status": "completed", "result": dict(job.result or {})}

    def cancel(self, job_id: str) -> dict[str, bool]:
        owner = caller_identity()
        with self._lock:
            job = self._job(owner, job_id)
        return {"accepted": job.boundary.cancel_host_message(job.operation_id)}

    def _job(self, owner: tuple[str, str], job_id: str) -> _ConversationJob:
        job = self._jobs.get(str(job_id))
        if job is None or job.owner != owner:
            raise ConversationHostError("CHAT_JOB_NOT_FOUND")
        return job

    def _revoke(self, jobs) -> None:
        for job in jobs:
            with job.publication_lock:
                job.revoked = True
            job.boundary.cancel_host_message(job.operation_id)

    def revoke_scope(self, plugin_id: str) -> None:
        with self._lock:
            jobs = [job for job in self._jobs.values() if job.owner[0] == plugin_id]
            self._jobs = {key: job for key, job in self._jobs.items() if job.owner[0] != plugin_id}
        self._revoke(jobs)

    def invalidate_session(self) -> None:
        with self._lock:
            self._session_epoch += 1
            jobs = list(self._jobs.values())
            self._jobs.clear()
        self._revoke(jobs)

    def close(self) -> None:
        with self._lock:
            self._closed = True
        self.invalidate_session()

    def _release_artifact(self, artifact_id: str) -> None:
        if artifact_id:
            try:
                self._artifact_releaser(artifact_id)
            except Exception as error:
                log_event("Conversation", "对话图片回收失败",
                    diagnostic_attributes(error, reason_code="CHAT_IMAGE_RELEASE_FAILED", stage="conversation_cleanup"),
                    event="conversation.cleanup.failed", severity="error")
