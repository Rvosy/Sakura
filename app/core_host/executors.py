"""Execution adapters and a developer contract, without a product mode selector."""

from __future__ import annotations

import json
import threading
import uuid
from dataclasses import dataclass

from app.core.cancellation import OperationCancelled
from app.llm.chat_reply import ChatReply, ChatSegment
from app.plugins.host_services import HOST_CALLER
from app.plugins.runtime_v4 import PluginRuntimeError


HOST_EXECUTORS_SERVICE = "sakura.host.executors"
EXECUTOR_SCHEMA_VERSION = 1


class ExecutionError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(code)
        self.code = code
        self.public_message = message
        self.retryable = False


@dataclass(frozen=True)
class ExecutionResult:
    reply: ChatReply
    visual_observation: object = None


class DefaultExecutor:
    """Wrap the existing dialogue implementation used by normal startup."""

    def __init__(self, pipeline):
        self.pipeline = pipeline

    def run_user_message(self, messages, **kwargs):
        return self._result(self.pipeline.run_user_message(messages, **kwargs))

    def run_event(self, event, **kwargs):
        return self._result(self.pipeline.run_event(event, **kwargs), event=True)

    @staticmethod
    def _result(result, *, event=False):
        allowed = {"tool_call", "event"} if event else {"tool_call"}
        if any(getattr(action, "type", "") not in allowed for action in getattr(result, "actions", [])):
            raise ExecutionError("UNEXPECTED_CHAT_ACTION", "Assistant 返回了不支持的动作。")
        return ExecutionResult(result.reply, getattr(result, "visual_observation", None))


def session_executor(session):
    executor = getattr(session, "executor", None)
    # Existing local consumers can still construct a pipeline-only Session.
    return executor if executor is not None else DefaultExecutor(session.pipeline)


class ExecutorCatalog:
    """Track process-owned registrations without selecting the chat consumer."""
    def __init__(self, manager, generation_id):
        self.manager = manager
        self.generation_id = generation_id
        self._lock = threading.Lock()
        self._entries = {}

    def register(self, descriptor):
        caller = HOST_CALLER.get()
        if not caller or caller == "sakura.core" or not isinstance(descriptor, dict):
            raise PluginRuntimeError("EXECUTOR_REGISTRATION_INVALID")
        if set(descriptor) != {"serviceKey", "displayName"}:
            raise PluginRuntimeError("EXECUTOR_REGISTRATION_INVALID")
        service, name = descriptor["serviceKey"], descriptor["displayName"]
        if not isinstance(service, str) or not isinstance(name, str) or not name.strip() or len(name) > 100:
            raise PluginRuntimeError("EXECUTOR_REGISTRATION_INVALID")
        identity = self.manager.service_identity(service, include_starting=True)
        if identity["providerId"] != caller:
            raise PluginRuntimeError("EXECUTOR_SOURCE_MISMATCH")
        with self._lock:
            if any(entry["serviceKey"] == service for entry in self._entries.values()):
                raise PluginRuntimeError("EXECUTOR_REGISTRATION_CONFLICT")
            registration = uuid.uuid4().hex
            self._entries[registration] = {
                "serviceKey": service, "displayName": name.strip(),
                "pluginId": caller, "identity": identity,
            }
        return {"registrationId": registration}

    def unregister(self, registration_id):
        with self._lock:
            entry = self._entries.get(registration_id)
            if entry and HOST_CALLER.get() not in {None, "sakura.core", entry["pluginId"]}:
                raise PluginRuntimeError("EXECUTOR_SOURCE_MISMATCH")
            return {"removed": self._entries.pop(registration_id, None) is not None}

    def candidates(self):
        with self._lock:
            entries = list(self._entries.values())
        result = []
        for entry in entries:
            try:
                if self.manager.service_identity(entry["serviceKey"]) == entry["identity"]:
                    result.append({key: entry[key] for key in ("serviceKey", "displayName", "pluginId")})
            except PluginRuntimeError:
                continue
        return sorted(result, key=lambda entry: entry["serviceKey"])

    def bind(self, service_key):
        with self._lock:
            entry = next((item for item in self._entries.values() if item["serviceKey"] == service_key), None)
        if entry is None:
            raise ExecutionError("EXECUTOR_UNAVAILABLE", "执行服务不可用，请检查插件。")
        return PluginExecutor(self.manager, self.generation_id, entry)


class PluginExecutor:
    """One Service instance, with explicit operation identity across background calls."""

    def __init__(self, manager, generation_id, entry):
        self.manager = manager
        self.generation_id = generation_id
        self.service_key = entry["serviceKey"]
        self.display_name = entry["displayName"]
        self.identity = dict(entry["identity"])
        self._closed = threading.Event()
        self.character = None

    def initialize(self, character):
        self.character = character
        value = self._call("describe")
        if not isinstance(value, dict) or isinstance(value.get("schemaVersion"), bool) or value.get("schemaVersion") != EXECUTOR_SCHEMA_VERSION:
            raise ExecutionError("EXECUTOR_CONTRACT_UNSUPPORTED", "互动插件接口版本不兼容。")
        if value.get("ready") is not True:
            raise ExecutionError("EXECUTOR_NOT_READY", "执行服务尚未就绪，请检查插件设置。")
        inputs = value.get("inputs")
        if not isinstance(inputs, list) or len(inputs) > 16 or not all(isinstance(item, str) for item in inputs) or "text" not in inputs:
            raise ExecutionError("EXECUTOR_CONTRACT_UNSUPPORTED", "互动插件不支持文字输入。")
        self.inputs = inputs

    def _call(self, method, *args):
        if self._closed.is_set():
            raise ExecutionError("EXECUTOR_BINDING_EXPIRED", "执行服务已停止。")
        try:
            return self.manager.call_bound_service(self.service_key, self.identity, method, *args, timeout=1.0)
        except PluginRuntimeError as error:
            if error.code in {"SERVICE_BINDING_EXPIRED", "SERVICE_MISSING", "GENERATION_INVALIDATED", "PLUGIN_PROCESS_EXITED"}:
                raise ExecutionError("EXECUTOR_BINDING_EXPIRED", "执行服务已停止或重载。") from error
            raise

    def commit(self, callback):
        try:
            return self.manager.commit_bound_service(self.service_key, self.identity, callback)
        except PluginRuntimeError as error:
            raise ExecutionError("EXECUTOR_BINDING_EXPIRED", "互动插件已停止或重载，结果未提交。") from error

    def validate_binding(self):
        self.commit(lambda: None)

    def execute(self, request, *, cancel, progress):
        try:
            return self._execute(request, cancel=cancel, progress=progress)
        except OperationCancelled:
            raise
        except BaseException as error:
            # A malformed response or failed RPC may leave real work alive.
            # Stop only this process lifetime, never the same-ID replacement.
            if not isinstance(error, ExecutionError) or error.code != "EXECUTOR_TASK_FAILED":
                try:
                    self.manager.stop_bound_service(
                        self.service_key, self.identity, reason="EXECUTOR_PROTOCOL_FAILED",
                    )
                except PluginRuntimeError as cleanup_error:
                    if cleanup_error.code != "PLUGIN_CLEANUP_FAILED":
                        raise
                    raise ExecutionError(
                        "EXECUTOR_STOP_UNCONFIRMED",
                        "无法确认互动插件已停止，请退出并重新启动 Sakura。",
                    ) from cleanup_error
            raise

    def _execute(self, request, *, cancel, progress):
        operation_id = request["operationId"]
        last_sequence = -1
        cancel_sent = False
        try:
            accepted = self._call("begin", request)
            if not isinstance(accepted, dict) or accepted.get("operationId") != operation_id:
                raise ExecutionError("EXECUTOR_RESULT_INVALID", "互动插件未能确认本次任务。")
        except PluginRuntimeError as error:
            if error.code != "PLUGIN_CALL_TIMEOUT":
                raise
            # begin may already have started work. Never send begin twice.
        while True:
            cancelling = cancel.is_cancelled() or self._closed.is_set()
            if cancelling and not cancel_sent:
                try:
                    self._call("cancel", {"operationId": operation_id})
                    cancel_sent = True
                except PluginRuntimeError as error:
                    if error.code != "PLUGIN_CALL_TIMEOUT":
                        raise
            try:
                value = self._call("read", {"operationId": operation_id, "afterSequence": last_sequence})
            except PluginRuntimeError as error:
                if error.code != "PLUGIN_CALL_TIMEOUT":
                    raise
                # A query timeout says nothing about the actual task. Keep it busy.
                self._closed.wait(0.1)
                continue
            if not isinstance(value, dict) or value.get("operationId") != operation_id:
                raise ExecutionError("EXECUTOR_RESULT_INVALID", "互动插件返回了无效结果。")
            state = value.get("state")
            if state in {"running", "cancelling"}:
                sequence = value.get("sequence")
                if isinstance(sequence, bool) or not isinstance(sequence, int) or sequence < 0:
                    raise ExecutionError("EXECUTOR_RESULT_INVALID", "互动插件返回了无效进度。")
                if sequence > last_sequence:
                    text = value.get("progress", "")
                    if not isinstance(text, str) or len(text) > 500:
                        raise ExecutionError("EXECUTOR_RESULT_INVALID", "互动插件返回了无效进度。")
                    progress(text)
                    last_sequence = sequence
                self._closed.wait(0.1)
                continue
            if state not in {"completed", "failed", "cancelled"}:
                raise ExecutionError("EXECUTOR_RESULT_INVALID", "互动插件返回了无效状态。")
            if cancelling or state == "cancelled":
                raise OperationCancelled()
            if state == "failed":
                raise ExecutionError("EXECUTOR_TASK_FAILED", "执行服务失败，请查看运行日志。")
            return ExecutionResult(parse_reply(value.get("reply")))

    def close(self):
        # Session retirement is only allowed after its operation has drained.
        self._closed.set()


def parse_reply(value):
    if not isinstance(value, dict) or set(value) != {"segments"}:
        raise ExecutionError("EXECUTOR_RESULT_INVALID", "互动插件返回了无效回复。")
    raw = value["segments"]
    if not isinstance(raw, list) or len(raw) > 64 or len(json.dumps(value, ensure_ascii=False).encode("utf-8")) > 64 * 1024:
        raise ExecutionError("EXECUTOR_RESULT_INVALID", "互动插件回复超出传输范围。")
    segments = []
    for item in raw:
        if not isinstance(item, dict) or "text" not in item or set(item) - {"text", "translation", "tone", "portrait", "suppressTts", "control"}:
            raise ExecutionError("EXECUTOR_RESULT_INVALID", "互动插件返回了无效片段。")
        texts = {key: item.get(key, "中性" if key == "tone" else "") for key in ("text", "translation", "tone", "portrait")}
        if not all(isinstance(text, str) for text in texts.values()) or not isinstance(item.get("suppressTts", False), bool):
            raise ExecutionError("EXECUTOR_RESULT_INVALID", "互动插件返回了无效片段。")
        control = item.get("control")
        if control is not None:
            from app.llm.visual_control import validate_visual_control
            try:
                control = validate_visual_control(control)
            except ValueError as error:
                raise ExecutionError("EXECUTOR_RESULT_INVALID", "互动插件返回了无效表现控制。") from error
        segments.append(ChatSegment(**texts, suppress_tts=item.get("suppressTts", False), control=control))
    return ChatReply(segments)
