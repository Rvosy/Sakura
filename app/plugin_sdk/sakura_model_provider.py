"""模型服务共用的任务、取消和 Artifact 交付。"""
from __future__ import annotations

import json
import threading
import time
from collections import deque
from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path

from sakura_cancellation import OperationCancelled
from sakura_model import ModelError


@dataclass
class Job:
    operation_id: str
    owner: tuple[str, str]
    settings: dict
    descriptor: dict
    operation: str = "generate"
    cancelled: threading.Event = field(default_factory=threading.Event)
    state: str = "running"
    sequence: int = 0
    progress: deque = field(default_factory=lambda: deque(maxlen=128))
    response: dict | None = None
    failure: dict | None = None
    artifact_id: str | None = None
    released: bool = False
    worker: threading.Thread | None = None

    def check(self):
        if self.cancelled.is_set():
            raise OperationCancelled()


class ModelProvider:
    def setup(self, context, *, profiles, service_key, label):
        self.context = context
        self.profiles = profiles
        self.artifacts = context.get("sakura.host.artifacts")
        self.changed = threading.Condition()
        self.jobs = {}
        self.revoked = set()
        self.released_ids = {}
        self.closed = False
        self.profiles.set_service(self)
        self.profiles.register_settings()
        context.get("sakura.host.model_slots.v2").register_provider({"serviceKey": service_key, "label": label}, catalog=self.profiles.catalog)
        context.effect(self.close)
        context.on("sakura.host.scope.closed", self.scope_closed)
        context.provide(service_key, self, exports=("catalog", "describe", "begin", "begin_probe", "poll", "result", "cancel", "release"))

    def execute(self, settings, request, **kwargs):
        raise NotImplementedError

    def diagnostic_secrets(self, settings):
        return (settings.get("api_key", ""),)

    def _owner(self):
        owner = self.context.caller_id, self.context.caller_scope
        if not all(owner):
            raise ModelError("MODEL_CALLER_SCOPE_REQUIRED")
        return owner

    def catalog(self):
        return self.profiles.catalog()

    def describe(self, profile_id, model_id):
        return self.profiles.describe(profile_id, model_id)

    def begin(self, descriptor):
        owner = self._owner()
        settings = self.profiles.resolve(descriptor["profileId"], descriptor["modelId"])
        return self._start(owner, descriptor, settings)

    def begin_probe(self, descriptor):
        owner = self._owner()
        operation = descriptor["operation"]
        settings = self.profiles.resolve_probe(descriptor.get("profileId"), descriptor.get("values", {}), require_model=operation == "test_connection")
        return self._start(owner, descriptor, settings, operation)

    def _start(self, owner, descriptor, settings, operation="generate"):
        identity = descriptor["operationId"]
        with self.changed:
            if self.closed or owner in self.revoked or identity in self.released_ids.get(owner, ()):
                raise OperationCancelled()
            key = owner, identity
            if key in self.jobs:
                raise ModelError("MODEL_OPERATION_DUPLICATE")
            job = Job(identity, owner, deepcopy(settings), deepcopy(descriptor), operation=operation)
            self.jobs[key] = job
            job.worker = threading.Thread(target=self._run, args=(job,), name="model-request", daemon=True)
            try:
                job.worker.start()
            except BaseException:
                self.jobs.pop(key, None)
                raise
        return {"operationId": identity}

    def _job(self, owner, identity):
        job = self.jobs.get((owner, identity))
        if job is None:
            raise ModelError("MODEL_OPERATION_NOT_FOUND")
        return job

    def poll(self, operation_id, after_sequence=0, wait_ms=250):
        owner = self._owner()
        with self.changed:
            job = self._job(owner, operation_id)
            self.changed.wait_for(lambda: job.state != "running" or job.sequence > after_sequence, timeout=max(0, min(wait_ms, 500)) / 1000)
            batch = [(sequence, event) for sequence, event in job.progress if sequence > after_sequence][:32]
            sequence = batch[-1][0] if batch else job.sequence
            return {"state": "running" if sequence < job.sequence else job.state, "sequence": sequence,
                    "progress": [event for _sequence, event in batch],
                    "truncated": bool(job.progress and after_sequence < job.progress[0][0] - 1)}

    def result(self, operation_id):
        with self.changed:
            job = self._job(self._owner(), operation_id)
            if job.state == "running":
                raise ModelError("MODEL_RESULT_PENDING")
            if job.state == "cancelled":
                return {"failure": {"code": "OPERATION_CANCELLED", "message": "模型请求已取消。"}}
            return {"failure": deepcopy(job.failure)} if job.failure else deepcopy(job.response)

    def _remember_release(self, owner, operation_id):
        # 取消可能先于开始请求到达，迟到的开始请求不能重新创建任务。
        self.released_ids.setdefault(owner, set()).add(operation_id)

    def cancel(self, operation_id):
        owner = self._owner()
        with self.changed:
            job = self.jobs.get((owner, operation_id))
            if job is not None:
                job.cancelled.set()
            else:
                self._remember_release(owner, operation_id)
            self.changed.notify_all()
        return {"cancelled": True}

    def release(self, operation_id):
        owner = self._owner()
        with self.changed:
            job = self.jobs.get((owner, operation_id))
            if job is None:
                self._remember_release(owner, operation_id)
                return {"released": True}
            job.released = True
            job.cancelled.set()
            if job.state == "running":
                return {"released": False}
        self._dispose(job)
        return {"released": True}

    def _read_request(self, job):
        if "requestArtifact" not in job.descriptor:
            return job.descriptor.get("request", {})
        identity = job.descriptor["requestArtifact"]["artifactId"]
        artifact = self.artifacts.resolve(identity)
        expected_delivery = {"senderId": job.owner[0], "senderScope": job.owner[1], "operationId": job.operation_id}
        if artifact.get("delivery") != expected_delivery:
            # 不回收其他调用者拥有的文件。
            raise ModelError("MODEL_ARTIFACT_OWNER_INVALID")
        try:
            return json.loads(Path(artifact["path"]).read_bytes())
        finally:
            try:
                self.artifacts.release_received(identity, timeout_seconds=2)
            except Exception as error:
                if getattr(error, "code", "") != "ARTIFACT_NOT_FOUND":
                    details = self.context.exception_diagnostics(error, secrets=self.diagnostic_secrets(job.settings))
                    self.context.get("sakura.host.logging").warning("模型请求文件回收失败", fields={"operation_id": job.operation_id, **details})

    def _run(self, job):
        try:
            job.check()
            request = self._read_request(job)
            def progress(event):
                with self.changed:
                    job.check()
                    job.sequence += 1
                    job.progress.append((job.sequence, event))
                    self.changed.notify_all()
            response = self.execute(job.settings, request, cancel_checker=job.check, progress=progress, operation=job.operation)
            job.check()
            encoded = json.dumps(response, ensure_ascii=False).encode("utf-8")
            if len(encoded) > 32768:
                allocation = self.artifacts.allocate({"mediaType": "application/json", "suffix": ".json"})
                job.artifact_id = allocation["artifactId"]
                Path(allocation["path"]).write_bytes(encoded)
                self.artifacts.commit(job.artifact_id)
                descriptor = self.artifacts.deliver(job.artifact_id, {"providerId": job.owner[0], "scopeId": job.owner[1]}, job.operation_id)
                job.response = {"responseArtifact": descriptor}
            else:
                job.response = {"response": response}
        except OperationCancelled:
            job.failure = {"code": "OPERATION_CANCELLED", "message": "模型请求已取消。"}
        except Exception as error:
            details = self.context.exception_diagnostics(error, secrets=self.diagnostic_secrets(job.settings))
            job.failure = {"code": getattr(error, "code", "MODEL_REQUEST_FAILED"),
                           "message": details["diagnostic"],
                           "diagnostics": {**getattr(error, "diagnostics", {}), **details}}
        finally:
            # 工作结束后释放凭据和图片请求。
            job.settings.clear()
            job.descriptor.clear()
            with self.changed:
                job.state = "cancelled" if job.cancelled.is_set() else "failed" if job.failure else "completed"
                dispose = job.released
                self.changed.notify_all()
            if dispose:
                self._dispose(job)

    def _dispose(self, job):
        with self.changed:
            self.jobs.pop((job.owner, job.operation_id), None)
            artifact_id, job.artifact_id = job.artifact_id, None
            self.changed.notify_all()
        if artifact_id:
            try:
                self.artifacts.release_delivered(artifact_id, job.operation_id)
                self.artifacts.release(artifact_id)
                self.artifacts.release_received(artifact_id)
            except Exception as error:
                if getattr(error, "code", "") != "ARTIFACT_NOT_FOUND":
                    raise

    def scope_closed(self, payload):
        owner = payload["pluginId"], payload["scopeId"]
        with self.changed:
            self.revoked.add(owner)
            self.released_ids.pop(owner, None)
            jobs = [job for job in self.jobs.values() if job.owner == owner]
            for job in jobs:
                job.cancelled.set()
                job.released = True
            self.changed.notify_all()
        for job in jobs:
            if job.state != "running":
                self._dispose(job)

    def close(self):
        with self.changed:
            self.closed = True
            jobs = list(self.jobs.values())
            for job in jobs:
                job.cancelled.set()
                job.released = True
            self.changed.notify_all()
        deadline = time.monotonic() + 2
        for job in jobs:
            if job.worker:
                job.worker.join(max(0, deadline - time.monotonic()))
            if job.state != "running":
                self._dispose(job)
        if any(job.worker and job.worker.is_alive() for job in jobs):
            raise ModelError("MODEL_CLEANUP_PENDING")
