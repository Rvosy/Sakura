"""Ordinary model Service client. No provider protocol, prompts or model policy."""
from __future__ import annotations

import json
import threading
import time
import uuid
from pathlib import Path
from collections.abc import Mapping

if __package__:
    from .sakura_cancellation import OperationCancelled, check_cancelled
else:
    from sakura_cancellation import OperationCancelled, check_cancelled


class ModelError(RuntimeError):
    def __init__(self, code, message="模型请求失败。", *, diagnostics=None):
        super().__init__(message)
        self.code = code
        self.diagnostics = dict(diagnostics or {})
        self.status_code = self.diagnostics.get("httpStatus")


class ModelClient:
    """Pins one provider instance; an uncertain begin acknowledgement is never replayed."""

    def __init__(self, context, reference, *, expected_identity=None):
        if not isinstance(reference, Mapping) or set(reference) != {"serviceKey", "profileId", "modelId"} or not all(isinstance(v, str) and v for v in reference.values()):
            raise ModelError("MODEL_REFERENCE_INVALID", "请先配置模型服务。")
        self.reference = dict(reference)
        self._service = context.bind(reference["serviceKey"])
        if expected_identity is not None and self._service.identity != dict(expected_identity):
            raise ModelError("SERVICE_BINDING_EXPIRED", "模型服务已重新加载，请重新建立对话会话。")
        self._artifacts = context.get("sakura.host.artifacts")
        self._lock = threading.Lock()
        self._closed = False
        self._operations = set()
        self._artifact_cleanups = {}
        self.description = self._service.invoke("describe", reference["profileId"], reference["modelId"], timeout_seconds=5)

    @property
    def identity(self):
        return self._service.identity

    def complete(self, request, *, cancel_checker=None, progress_callback=None):
        operation_id = uuid.uuid4().hex
        with self._lock:
            if self._closed:
                raise OperationCancelled()
            self._operations.add(operation_id)
        request_artifact = None
        committed = delivered = delivery_attempted = begin_attempted = False
        def check():
            check_cancelled(cancel_checker)
            with self._lock:
                if self._closed:
                    raise OperationCancelled()
        try:
            check()
            descriptor = {"operationId": operation_id, "profileId": self.reference["profileId"], "modelId": self.reference["modelId"]}
            encoded = json.dumps(request, ensure_ascii=False).encode("utf-8")
            if len(encoded) > 32768:
                allocation = self._artifacts.allocate({"mediaType": "application/json", "suffix": ".json"})
                request_artifact = allocation["artifactId"]
                Path(allocation["path"]).write_bytes(encoded)
                self._artifacts.commit(request_artifact)
                committed = True
                delivery_attempted = True
                descriptor["requestArtifact"] = self._artifacts.deliver(request_artifact, self._service.identity, operation_id)
                delivered = True
            else:
                descriptor["request"] = request
            check()
            begin_attempted = True
            ack = self._service.invoke("begin", descriptor, timeout_seconds=5)
            if not isinstance(ack, Mapping) or ack.get("operationId") != operation_id:
                raise ModelError("MODEL_ACK_INVALID")
            sequence = 0
            while True:
                check()
                state = self._service.invoke("poll", operation_id, sequence, 250, timeout_seconds=2)
                sequence = state["sequence"]
                if progress_callback is not None:
                    for event in state.get("progress", ()):
                        progress_callback(event)
                if state["state"] != "running":
                    break
            check()
            if state["state"] == "cancelled":
                raise OperationCancelled()
            result = self._service.invoke("result", operation_id, timeout_seconds=5)
            if "failure" in result:
                failure = result["failure"]
                if failure.get("code") == "OPERATION_CANCELLED":
                    raise OperationCancelled()
                raise ModelError(failure["code"], failure["message"], diagnostics=failure.get("diagnostics"))
            if "responseArtifact" in result:
                identity = result["responseArtifact"]["artifactId"]
                artifact = self._artifacts.resolve(identity)
                expected_delivery = {"senderId": self.identity["providerId"], "senderScope": self.identity["scopeId"], "operationId": operation_id}
                if artifact.get("delivery") != expected_delivery:
                    raise ModelError("MODEL_ARTIFACT_OWNER_INVALID")
                try:
                    data = Path(artifact["path"]).read_bytes()
                    if artifact["mediaType"] != "application/json" or len(data) != artifact["byteLength"]:
                        raise ModelError("MODEL_RESULT_INVALID")
                    response = json.loads(data)
                finally:
                    self._artifacts.release_received(identity, timeout_seconds=2)
            else:
                response = result["response"]
            check()
            return response
        finally:
            # release also cancels and registers disposal when the worker exits.
            # Send it first so a slow cancel ACK cannot consume its cleanup budget.
            deadline = time.monotonic() + 2
            cleanup = []
            if request_artifact is not None:
                if delivery_attempted:
                    cleanup.append((self._artifacts.release_delivered, (request_artifact, operation_id)))
                if not delivered:
                    cleanup.append((self._artifacts.release_received if committed else self._artifacts.release, (request_artifact,)))
                with self._lock:
                    self._artifact_cleanups[operation_id] = cleanup
            if begin_attempted:
                self._release_operation(operation_id, deadline)
            else:
                with self._lock:
                    self._operations.discard(operation_id)
            self._release_artifacts(operation_id, deadline)

    def _release_operation(self, operation_id, deadline):
        with self._lock:
            if operation_id not in self._operations:
                return
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return
        try:
            result = self._service.invoke("release", operation_id, timeout_seconds=remaining)
        except Exception:
            return  # Keep the unknown operation for close; never rebind or replay begin.
        # False acknowledges cancellation plus deferred disposal by the Provider.
        # A missing/invalid ACK does not establish either kind of ownership transfer.
        if isinstance(result, Mapping) and isinstance(result.get("released"), bool):
            with self._lock:
                self._operations.discard(operation_id)

    def _release_artifacts(self, operation_id, deadline):
        with self._lock:
            cleanup = tuple(self._artifact_cleanups.get(operation_id, ()))
        for release, args in cleanup:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            try:
                release(*args, timeout_seconds=remaining)
            except Exception as error:
                if getattr(error, "code", "") != "ARTIFACT_NOT_FOUND":
                    continue
            with self._lock:
                pending = self._artifact_cleanups.get(operation_id, [])
                if (release, args) in pending:
                    pending.remove((release, args))
                if not pending:
                    self._artifact_cleanups.pop(operation_id, None)

    def complete_raw(self, system_prompt, messages, *, temperature=.2, response_format=None, max_tokens=None, cancel_checker=None, trace_metadata=None):
        parameters = {"temperature": temperature}
        if max_tokens is not None:
            parameters["max_tokens"] = max_tokens
        response = self.complete({"messages": [{"role": "system", "content": system_prompt}, *messages],
                                  "parameters": parameters, "responseFormat": response_format}, cancel_checker=cancel_checker)
        return str(response["message"].get("content") or "")

    def close(self):
        with self._lock:
            self._closed = True
            operations = tuple(self._operations | self._artifact_cleanups.keys())
        deadline = time.monotonic() + 2
        for operation_id in operations:
            self._release_operation(operation_id, deadline)
            self._release_artifacts(operation_id, deadline)
