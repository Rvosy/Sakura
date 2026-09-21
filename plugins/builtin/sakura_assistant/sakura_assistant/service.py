"""One ordinary Assistant provider; owns model policy and every in-flight model request."""
from __future__ import annotations

import base64
import json
import re
import threading
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

from sakura_assistant_contract import RuntimeLoopSettings, ScreenObservation
from sakura_cancellation import OperationCancelled
from sakura_context import ContextFragment
from sakura_model import ApiConfigError, ApiRequestError, ModelClient, ModelError
from sakura_tools import Tool, ToolExecutionResult, ToolRegistry
from . import diagnostics
from .agent.actions import AgentEvent
from .agent.runtime import AgentRuntime
from .agent.trace import AgentTraceRecorder, traced_message
from .agent.screen_observation import (
    build_manual_screen_observation_batch_user_message,
    build_screen_observation_batch_user_message,
)
from .llm.api_client import AssistantModelClient, DialogueSettings
from .llm.prompts.blocks import with_desktop_pet_context
from .history import PagedHistory


class AssistantError(RuntimeError):
    def __init__(self, code):
        super().__init__(code)
        self.code = code


@dataclass
class Operation:
    operation_id: str
    descriptor: dict
    cancelled: threading.Event = field(default_factory=threading.Event)
    state: str = "running"
    error: BaseException | None = None
    output: dict | None = None
    progress: list = field(default_factory=list)
    release_status: str | None = None
    worker: threading.Thread | None = None
    trace: AgentTraceRecorder | None = None

    def check(self):
        if self.cancelled.is_set():
            raise OperationCancelled()


class RemoteTools(ToolRegistry):
    def __init__(self, context, operation):
        self.remote = context.get("sakura.host.tools")
        self.artifacts = context.get("sakura.host.artifacts")
        self.operation = operation
        catalog = self.remote.catalog()
        super().__init__([Tool(name=row["name"], description=row["description"], parameters=row["parameters"],
            group=row["group"], risk=row["risk"], capability=row.get("capability"), source=row["source"],
            registration_id=row["registrationId"], timeout_seconds=row["timeoutSeconds"]) for row in catalog])

    def execute(self, name, arguments, **kwargs):
        self.operation.check()
        tool = self.get(name)
        if tool is None:
            return ToolExecutionResult(name, False, "", "未知工具。", "TOOL_NOT_FOUND")
        try:
            return self._execute(tool, arguments)
        except OperationCancelled:
            raise
        except Exception as error:
            self.operation.check()
            if getattr(error, "code", "") == "OPERATION_CANCELLED":
                raise OperationCancelled() from error
            code = str(getattr(error, "code", ""))
            if not re.fullmatch(r"[A-Z0-9_]{1,80}", code):
                code = "PLUGIN_TOOL_EXECUTION_FAILED"
            attributes = {**diagnostics.diagnostic_attributes(error),
                          **getattr(error, "diagnostics", {}), "tool_name": name, "reason_code": code}
            diagnostics.log_event("ToolRegistry", "工具执行失败", attributes,
                                  severity="error", event="tool.request.failed")
            return ToolExecutionResult(name, False, "", "工具执行失败。", code)

    def _execute(self, tool, arguments):
        name = tool.name
        result = self.remote.execute(tool.registration_id, name, arguments, timeout_seconds=tool.timeout_seconds)
        content = result.get("content")
        if isinstance(content, dict) and set(content) == {"content", "artifact"}:
            descriptor = content["artifact"]
            artifact = self.artifacts.resolve(descriptor["artifactId"])
            try:
                payload = Path(artifact["path"]).read_bytes()
                if len(payload) != descriptor["byteLength"] or artifact["mediaType"] != descriptor["mediaType"]:
                    raise AssistantError("TOOL_ARTIFACT_INVALID")
                content = {"content": content["content"], "artifact": {"type": "image",
                    "data": base64.b64encode(payload).decode("ascii"), "mimeType": artifact["mediaType"]}}
            finally:
                self.artifacts.release_received(descriptor["artifactId"])
        self.operation.check()
        return ToolExecutionResult(name, bool(result["success"]), content, result.get("error", ""), result.get("reason_code", ""))


def _read_generation(saved):
    # Earlier handoffs copied optional values that the legacy reader ignored.
    # Normalize the effective view without rewriting the user's configuration.
    values = {}
    for key, minimum, maximum in (("temperature", 0, 2), ("top_p", 0, 1), ("max_tokens", 1, 1000000)):
        value = saved.get(key)
        valid = (not isinstance(value, bool) and isinstance(value, (int, float))
                 and minimum <= value <= maximum and (key != "max_tokens" or isinstance(value, int)))
        values[key] = value if valid else None
    return values


class AssistantPlugin:
    def setup(self, context):
        self.context = context
        self.changed = threading.Condition()
        self.operations = {}
        self.closed = False
        self.trace = None
        self.generation = _read_generation(context.config.get().get("generation", {}))
        self.context_window_tokens = context.config.get().get("contextWindowTokens")
        self._register_settings()
        diagnostics.configure(context.get("sakura.host.logging"))
        context.effect(self.close)
        context.provide("sakura.assistant", self, exports=("prepare", "begin", "poll", "result", "cancel", "release"))

    def _register_settings(self):
        fields = [{"key": "temperature", "label": "创造性 (temperature)", "type": "number", "default": None, "displayDefault": 0.8, "step": 0.01, "description": "越高越多变，越低越稳定。", "minimum": 0, "maximum": 2},
                  {"key": "top_p", "label": "用词多样性 (top_p)", "type": "number", "default": None, "optionalToggle": True, "displayDefault": 1, "step": 0.01, "description": "值越低，用词范围越集中。", "minimum": 0, "maximum": 1},
                  {"key": "max_tokens", "label": "回复长度上限 (max_tokens)", "type": "integer", "default": None, "optionalToggle": True, "displayDefault": 2048, "minimum": 1, "maximum": 1000000}]
        def load():
            return _read_generation(self.context.config.get().get("generation", {}))
        def save(request):
            values = request.get("values", request)
            result = {}
            for field in fields:
                value = values.get(field["key"])
                if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float)) or not field["minimum"] <= value <= field["maximum"] or (field["type"] == "integer" and not isinstance(value, int))):
                    raise ApiConfigError("生成参数无效。")
                result[field["key"]] = value
            if self.context.config.update({"generation": result}) == "error":
                raise ApiConfigError("生成参数保存失败。")
            return {"applicationState": "restart_required"}
        self.context.get("sakura.host.settings").register({"sectionId": "generation", "title": "高级参数", "order": 30,
            "presentation": {"component": "form", "group": "model-advanced", "collapsible": True}, "fields": fields}, load=load, save=save)
        settings = self.context.get("sakura.host.settings")
        settings.place("generation", page_id="host:model", order=30)
        def save_budget(values):
            value = values.get("contextWindowTokens")
            if value is not None and (type(value) is not int or not 4096 <= value <= 2000000):
                raise ApiConfigError("上下文上限无效。")
            if self.context.config.update({"contextWindowTokens": value}) == "error":
                raise ApiConfigError("上下文上限保存失败。")
            return {"applicationState": "restart_required"}
        settings.register({"sectionId": "context_budget", "title": "高级参数", "order": 10,
            "presentation": {"component": "form", "group": "model-advanced", "collapsible": True},
            "fields": [{"key": "contextWindowTokens", "label": "上下文上限", "type": "integer", "default": None,
                        "minimum": 4096, "maximum": 2000000, "unit": "tokens", "placeholder": "例如 1000000", "description": "默认 32K tokens"}]},
            load=lambda: {"contextWindowTokens": self.context.config.get().get("contextWindowTokens")}, save=save_budget)
        settings.place("context_budget", page_id="host:model", order=10)

    def _settings(self, session):
        slots = session.get("modelSlots", {})
        value = slots.get("chat")
        if not isinstance(value, dict) or not all(value.get(key) for key in ("serviceKey", "profileId", "modelId")):
            raise ApiConfigError("PROVIDER_SETUP_REQUIRED")
        return value, slots.get("vision_chat")

    def prepare(self, session):
        clients = []
        try:
            chat, vision = self._settings(session)
            clients.append(ModelClient(self.context, chat))
            if vision and vision != chat:
                clients.append(ModelClient(self.context, vision))
        except Exception as error:
            for client in clients:
                client.close()
            diagnostics.log_event("Assistant", "模型服务尚未就绪", diagnostics.diagnostic_attributes(error), event="assistant.model.not_ready", severity="warning")
            code = getattr(error, "code", "PROVIDER_SETUP_REQUIRED")
            return {"state": "setup_required", "code": code, "message": "请先配置模型服务。", "retryable": False}
        bindings = {"chat": clients[0].identity}
        if vision:
            bindings["vision_chat"] = clients[-1].identity
        for client in clients:
            client.close()
        return {"state": "ready", "code": "READY", "message": "Assistant 已就绪。", "retryable": False, "modelBindings": bindings}

    def begin(self, descriptor):
        operation_id = descriptor.get("operationId")
        if not isinstance(operation_id, str) or not operation_id:
            raise AssistantError("ASSISTANT_INPUT_INVALID")
        with self.changed:
            if self.closed:
                raise AssistantError("ASSISTANT_CLOSED")
            if self.operations:
                raise AssistantError("ASSISTANT_BUSY")
            operation = Operation(operation_id, dict(descriptor))
            self.operations[operation_id] = operation
            operation.worker = threading.Thread(target=self._run, args=(operation,), name="assistant-turn", daemon=True)
            try:
                operation.worker.start()
            except BaseException:
                if self.operations.get(operation_id) is operation:
                    self.operations.pop(operation_id)
                self.changed.notify_all()
                raise
        return {"operationId": operation_id}

    def _get(self, operation_id):
        operation = self.operations.get(operation_id)
        if operation is None:
            raise AssistantError("ASSISTANT_OPERATION_NOT_FOUND")
        return operation

    def poll(self, operation_id, after_sequence=0, wait_ms=500):
        with self.changed:
            operation = self._get(operation_id)
            if operation.state == "running" and len(operation.progress) <= after_sequence:
                self.changed.wait_for(lambda: operation.state != "running" or len(operation.progress) > after_sequence,
                    timeout=min(max(wait_ms, 0), 500) / 1000)
            value = {"state": operation.state, "sequence": len(operation.progress), "progress": operation.progress[after_sequence:]}
            if operation.error is not None:
                value["failure"] = classify_failure(operation.error)
            return value

    def result(self, operation_id):
        with self.changed:
            operation = self._get(operation_id)
            if operation.state == "running":
                raise AssistantError("ASSISTANT_RESULT_PENDING")
            if operation.error is not None:
                # Raising the original object preserves worker traceback and all causal exceptions over RPC.
                raise operation.error
            return operation.output

    def cancel(self, operation_id):
        with self.changed:
            operation = self._get(operation_id)
            operation.cancelled.set()
            self.changed.notify_all()
        return {"cancelled": True}

    def release(self, operation_id, terminal_status):
        if terminal_status not in {"completed", "cancelled", "failed"}:
            raise AssistantError("ASSISTANT_TERMINAL_INVALID")
        with self.changed:
            operation = self._get(operation_id)
            operation.release_status = terminal_status
            if operation.state == "running":
                operation.cancelled.set()
            else:
                self._dispose(operation)
        return {"released": operation.state != "running"}

    def _dispose(self, operation):
        failures = []
        try:
            if operation.trace is not None:
                try:
                    operation.trace.finish_operation(operation.operation_id, status=operation.release_status or "cancelled")
                except Exception as error:
                    failures.append(("trace", error))
                finally:
                    operation.trace = None
            if operation.output is not None:
                try:
                    self.context.get("sakura.host.artifacts").release_received(operation.output["artifactId"])
                except Exception as error:
                    # The receiver or Host scope sweep may already have removed it.
                    if getattr(error, "code", "") != "ARTIFACT_NOT_FOUND":
                        failures.append(("output", error))
                finally:
                    operation.output = None
        finally:
            # Only the exact owner can leave this slot. A failed log write must
            # never leave an already stopped operation occupying the provider.
            with self.changed:
                if self.operations.get(operation.operation_id) is operation:
                    self.operations.pop(operation.operation_id)
                self.changed.notify_all()
        for stage, error in failures:
            diagnostics.log_event("Assistant", "Assistant 资源回收失败",
                {"operation_id": operation.operation_id, "stage": stage,
                 **diagnostics.diagnostic_attributes(error)},
                event="assistant.resource.close_failed", severity="error")

    def _read_input(self, operation):
        artifacts = self.context.get("sakura.host.artifacts")
        descriptor = artifacts.resolve(operation.descriptor["input"]["artifactId"])
        try:
            payload = Path(descriptor["path"]).read_bytes()
            if len(payload) != descriptor["byteLength"]:
                raise AssistantError("ASSISTANT_INPUT_INVALID")
            return json.loads(payload)
        finally:
            artifacts.release_received(descriptor["artifactId"])

    def _run(self, operation):
        token = diagnostics._operation.set(operation.operation_id)
        models = []
        try:
            request = self._read_input(operation)
            operation.check()
            chat, vision = self._settings(request["session"])
            bindings = request["session"].get("modelBindings", {})
            if not bindings.get("chat"):
                raise ApiConfigError("模型会话已失效，请重新加载 Assistant。")
            models.append(ModelClient(self.context, chat, expected_identity=bindings["chat"]))
            if vision and vision != chat:
                if not bindings.get("vision_chat"):
                    raise ApiConfigError("视觉模型会话已失效。")
                models.append(ModelClient(self.context, vision, expected_identity=bindings["vision_chat"]))
            if self.trace is None:
                logs = self.context.get("sakura.host.storage").resolve("data", "logs")
                self.trace = AgentTraceRecorder(logs)
            trace = operation.trace = self.trace
            character = request["session"]["character"]
            def adapter(model):
                description = model.description
                settings = DialogueSettings(model=model.reference["modelId"],
                    temperature=self.generation.get("temperature"), top_p=self.generation.get("top_p"), max_tokens=self.generation.get("max_tokens"),
                    context_window_tokens=self.context_window_tokens or 32768, context_window_source="user" if self.context_window_tokens else "fallback")
                return AssistantModelClient(settings, model, agent_trace_recorder=trace)
            client = adapter(models[0])
            vision_client = adapter(models[1]) if len(models) > 1 else None
            contexts = self.context.get("sakura.host.context")
            providers = []
            for row in contexts.catalog():
                def collect(context_request, registration_id=row["registrationId"]):
                    operation.check()
                    return tuple(ContextFragment(**item) for item in contexts.collect(registration_id, asdict(context_request)))
                providers.append(SimpleNamespace(provider_id=row["providerId"], description=row["description"], order=row["order"],
                    enabled=row["enabled"], scope=row["scope"], failure_policy=row["failurePolicy"], plugin_id=row["pluginId"], build_context=collect))
            runtime = AgentRuntime(client, with_desktop_pet_context(character["systemPrompt"]),
                reply_tones=character["replyTones"], character_id=character["id"], character_name=character["displayName"],
                tools=RemoteTools(self.context, operation), context_providers=providers, strict_provider_errors=True, vision_api_client=vision_client,
                runtime_loop_settings=RuntimeLoopSettings(**request["session"]["loopSettings"]), agent_trace_recorder=trace)
            runtime.set_visual_binding(SimpleNamespace(reply_visual=request["session"].get("replyVisual")))
            def progress(value):
                with self.changed:
                    operation.progress.append(asdict(value))
                    self.changed.notify_all()
            with runtime.trace_operation(operation.operation_id):
                if (request.get("event") or {}).get("type") == "update_available":
                    value = request["event"]
                    result = runtime.handle_event(AgentEvent(type=value["type"], payload=value["payload"]),
                        cancel_checker=operation.check, progress_callback=progress)
                else:
                    history = PagedHistory(self.context.get("sakura.host.timeline"), character["id"], request["historyCursor"], datetime.fromisoformat(request["historyNow"]), artifacts=self.context.get("sakura.host.artifacts"), history_token=request["historyToken"], cancel_checker=operation.check)
                    runtime.context_orchestrator.history = history
                    attachment = request.get("attachment")
                    message = {"role": "user", "content": request["message"]}
                    observations = tuple(ScreenObservation(**item) for item in attachment["observations"]) if attachment else ()
                    if attachment:
                        builder = (build_manual_screen_observation_batch_user_message
                                   if attachment.get("source") == "manual" else build_screen_observation_batch_user_message)
                        message = builder(request["message"], observations)
                    message = traced_message(message, "observation_input" if attachment else "user_input", turn_id=request["turnId"],
                        entry_ids=tuple(request["entryIds"]), human_entry_id=request.get("humanEntryId", ""),
                        observation_entry_ids=tuple(request.get("observationEntryIds", ())),
                        runtime_items=tuple({"kind": "image_input", "width": item.width, "height": item.height, "detail": "low"} for item in observations))
                    result = runtime.handle_user_message([*history.proactive_messages(), message], cancel_checker=operation.check, progress_callback=progress)
                operation.check()
                payload = {"reply": asdict(result.reply), "actions": [asdict(action) for action in result.actions], "visual_observation": result.visual_observation}
                artifacts = self.context.get("sakura.host.artifacts")
                allocation = artifacts.allocate({"mediaType": "application/json", "suffix": ".json"})
                Path(allocation["path"]).write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
                operation.output = artifacts.commit(allocation["artifactId"])
        except BaseException as error:
            operation.error = error
        finally:
            for model in models:
                model.close()
            diagnostics._operation.reset(token)
            with self.changed:
                operation.state = "cancelled" if operation.cancelled.is_set() else "failed" if operation.error else "completed"
                try:
                    if operation.release_status is not None:
                        self._dispose(operation)
                finally:
                    self.changed.notify_all()

    def close(self):
        with self.changed:
            self.closed = True
            operations = tuple(self.operations.values())
            for operation in operations:
                operation.cancelled.set()
                operation.release_status = "cancelled"
        for operation in operations:
            if operation.worker is not None:
                operation.worker.join(timeout=0.5)
        if any(operation.worker and operation.worker.is_alive() for operation in operations):
            raise AssistantError("ASSISTANT_CLEANUP_PENDING")


def classify_failure(error):
    from .agent.context_orchestrator import ContextContributionError
    from .llm.prompts.runtime import ContextWindowExceededError
    from sakura_provider_errors import provider_http_status, public_provider_http_message
    code = getattr(error, "code", "")
    if isinstance(error, OperationCancelled):
        return {"code": "OPERATION_CANCELLED", "message": "对话已取消。", "retryable": False}
    if isinstance(error, (ContextContributionError, ContextWindowExceededError)):
        return {"code": "CONTEXT_WINDOW_EXCEEDED" if isinstance(error, ContextWindowExceededError) else error.code,
                "message": error.public_message(), "retryable": False, "attributes": error.log_attributes()}
    if isinstance(error, ApiConfigError):
        return {"code": "PROVIDER_CONFIGURATION_INVALID", "message": "模型服务配置无效。", "retryable": False}
    if isinstance(error, (ApiRequestError, ModelError)):
        status = provider_http_status(error)
        if status is not None:
            return {"code": "PROVIDER_REQUEST_FAILED", "message": public_provider_http_message(error, status),
                    "retryable": status == 429 or status >= 500, "attributes": {"http_status": status}}
        text = str(error).lower()
        if any(item in text for item in ("格式无法解析", "invalid json", "remained invalid", "missing", "empty response")):
            return {"code": "PROVIDER_RESPONSE_INVALID", "message": "模型服务响应格式无效：回复结构不符合协议。", "retryable": False}
        return {"code": "PROVIDER_REQUEST_FAILED", "message": "模型请求失败。", "retryable": True}
    return {"code": code or "CHAT_EXECUTION_FAILED", "message": "对话处理失败。", "retryable": False}
