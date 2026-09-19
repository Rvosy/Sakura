from __future__ import annotations

from collections.abc import Mapping
from contextlib import contextmanager
from functools import wraps

import json
import re
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Sequence

from sakura_cancellation import CancelChecker, OperationCancelled, check_cancelled
from sakura_assistant.diagnostics import submit_telemetry_model_call
from sakura_assistant.llm.chat_reply import (
    ChatReply,
    parse_chat_reply,
    parse_chat_reply_result,
    sanitize_reply_tones,
)
from sakura_assistant.diagnostics import diagnostic_attributes, log_event
from sakura_assistant.llm.prompt_templates import build_segmented_reply_instruction
from sakura_assistant.agent.trace import (
    AgentTraceRecorder,
    MessageProvenance,
    PromptTraceMetadata,
    TraceCall,
    message_provenance,
    prompt_metadata_with_context,
    strip_message_provenance,
    summarize_prompt_payload,
    traced_message,
)


STRUCTURED_JSON_RESPONSE_FORMAT = {"type": "json_object"}
ChatMessage = dict[str, Any]
SUPPORTED_CHAT_COMPLETION_PARAMS = {
    "temperature",
    "top_p",
    "max_tokens",
    "max_completion_tokens",
    "presence_penalty",
    "frequency_penalty",
    "response_format",
    "stream",
    "tools",
    "tool_choice",
}


from sakura_model import ApiConfigError, ApiRequestError, ModelClient, ModelError


@dataclass(frozen=True)
class DialogueSettings:
    model: str
    temperature: float | None = None
    top_p: float | None = None
    max_tokens: int | None = None
    context_window_tokens: int = 32768
    context_window_source: str = "fallback"


@dataclass(frozen=True)
class NativeToolCall:
    """模型返回的工具调用，保留 id 以便回填工具结果。"""

    id: str
    name: str
    arguments: dict[str, Any]
    arguments_json: str = "{}"
    arguments_error: str = ""


@dataclass(frozen=True)
class ChatCompletionTurn:
    """模型一次调用返回的 assistant 消息与解析记录。"""

    content: str
    tool_calls: list[NativeToolCall]
    message: dict[str, Any]
    runtime_context_role: str = "system"
    runtime_context_placement: str = "tail_system"
    usage: dict[str, Any] = field(default_factory=dict)
    raw_content: str = ""
    parse_status: str = "unparsed"
    trace_call: TraceCall | None = None


@dataclass
class _ClientConfiguration:
    settings: DialogueSettings
    runtime_context_role: str = "system"


def _scoped_request(method):
    @wraps(method)
    def invoke(self, *args, **kwargs):
        with self.request_scope():
            return method(self, *args, **kwargs)
    return invoke


class AssistantModelClient:
    def __init__(
        self,
        settings: DialogueSettings,
        model_client: ModelClient,
        *,
        agent_trace_recorder: AgentTraceRecorder | None = None,
    ) -> None:
        self._configuration = _ClientConfiguration(settings)
        self._model_client = model_client
        # 可选事件发射器（由宿主注入），用于派发 llm.request.* 插件事件。
        self._event_emit: Callable[[str, dict[str, Any] | None], None] | None = None
        self._agent_trace_recorder = agent_trace_recorder
        self._trace_local = threading.local()

    @property
    def _active_configuration(self) -> _ClientConfiguration:
        return getattr(self._trace_local, "configuration", self._configuration)

    @property
    def settings(self) -> DialogueSettings:
        return self._active_configuration.settings

    @property
    def _runtime_context_role(self) -> str:
        return self._active_configuration.runtime_context_role

    @_runtime_context_role.setter
    def _runtime_context_role(self, value: str) -> None:
        self._active_configuration.runtime_context_role = value

    @contextmanager
    def request_scope(self):
        """Keep Assistant context policy stable for one tool loop."""
        if getattr(self._trace_local, "configuration", None) is not None:
            yield self
            return
        self._trace_local.configuration = self._configuration
        try:
            yield self
        finally:
            del self._trace_local.configuration

    def close(self):
        self._model_client.close()

    def set_agent_trace_recorder(self, recorder: AgentTraceRecorder | None) -> None:
        self._agent_trace_recorder = recorder

    def mark_latest_trace_repair_requested(self, reason: str) -> None:
        if self._agent_trace_recorder is not None:
            self._agent_trace_recorder.mark_repair_requested(self.last_trace_call, reason)

    @property
    def last_trace_call(self) -> TraceCall | None:
        value = getattr(self._trace_local, "last_call", None)
        return value if isinstance(value, TraceCall) else None

    def set_event_emitter(
        self,
        emitter: Callable[[str, dict[str, Any] | None], None] | None,
    ) -> None:
        """注入插件事件发射器；传 None 关闭。"""
        self._event_emit = emitter

    def _emit_llm_event(self, event_name: str, payload: dict[str, Any] | None = None) -> None:
        """安全派发 LLM 请求事件，发射器异常不影响请求本身。"""
        emitter = self._event_emit
        if emitter is None:
            return
        try:
            emitter(event_name, payload)
        except Exception:  # noqa: BLE001 — 事件派发不得影响 LLM 请求
            pass

    @property
    def runtime_context_role(self) -> str:
        return self._runtime_context_role


    def resolve_dialogue_params(self) -> tuple[float, dict[str, Any]]:
        """返回角色对话用的生成参数：温度 + 额外参数（top_p/max_tokens）。

        仅供角色对话入口（chat() 与 Agent 主工具循环）调用；记忆抽取、视觉摘要、
        JSON 修复等内部功能调用必须保留各自硬编码的低温度，不得使用本方法，
        否则会被用户配置污染。未配置的字段回退到内置默认（温度 0.8）或直接不发送。
        """
        temperature = self.settings.temperature if self.settings.temperature is not None else 0.8
        extra: dict[str, Any] = {}
        if self.settings.top_p is not None:
            extra["top_p"] = self.settings.top_p
        if self.settings.max_tokens is not None:
            extra["max_tokens"] = self.settings.max_tokens
        return temperature, extra

    @_scoped_request
    def chat(
        self,
        system_prompt: str,
        messages: list[ChatMessage],
        reply_tones: list[str] | None = None,
        reply_visual: Mapping[str, Any] | None = None,
        *,
        cancel_checker: CancelChecker | None = None,
        runtime_context: str = "",
        trace_metadata: PromptTraceMetadata | None = None,
    ) -> ChatReply:
        segmented_reply_instruction = _build_segmented_reply_instruction(reply_tones, reply_visual)
        temperature, extra_params = self.resolve_dialogue_params()
        content = self.complete_raw(
            f"{system_prompt.strip()}\n\n{segmented_reply_instruction}",
            messages,
            temperature=temperature,
            response_format=STRUCTURED_JSON_RESPONSE_FORMAT,
            cancel_checker=cancel_checker,
            runtime_context=runtime_context,
            trace_metadata=trace_metadata,
            **extra_params,
        )
        check_cancelled(cancel_checker)

        parsed = parse_chat_reply_result(content)
        reply = sanitize_reply_tones(parsed.reply, reply_tones)
        changes: list[str] = []
        if parsed.needs_retry:
            changes.append("safety_fallback")
        if reply != parsed.reply:
            changes.append("tone_sanitized")
        if self._agent_trace_recorder is not None and changes:
            self._agent_trace_recorder.record_effective_reply(
                self.last_trace_call,
                _chat_reply_trace_mapping(reply),
                changes,
            )
        log_event(
            "API",
            "聊天回复解析完成",
            {
                **_model_call_log_attributes(self.last_trace_call),
                "segments": len(reply.segments),
                "segment_count": len(reply.segments),
                "parse_status": "valid" if parsed.ok else (parsed.reason or "invalid"),
                "tone": reply.tone,
                "portraits": [segment.portrait for segment in reply.segments],
                "reply": reply.text,
            },
            event="reply.processing.finished",
            verbosity=1,
        )
        return reply

    @_scoped_request
    def complete_raw(
        self,
        system_prompt: str,
        messages: list[ChatMessage],
        temperature: float = 0.8,
        *,
        cancel_checker: CancelChecker | None = None,
        runtime_context: str = "",
        trace_metadata: PromptTraceMetadata | None = None,
        **chat_params: Any,
    ) -> str:
        """返回模型原始文本，供 Agent Runtime 解析工具调用 JSON。"""
        self._ensure_chat_config("缺少 API Key。请在设置中完成 API 配置。")
        check_cancelled(cancel_checker)
        runtime_context_role = self._runtime_context_role
        payload, prompt_provenance, runtime_context_placement = _prepare_chat_completion_payload(
            model=self.settings.model,
            system_prompt=system_prompt,
            messages=_messages_with_runtime_context(
                messages,
                runtime_context,
                runtime_context_role,
                runtime_items=prompt_metadata_with_context(trace_metadata),
            ),
            temperature=temperature,
            chat_params=chat_params,
            trace_metadata=trace_metadata,
        )
        log_event(
            "API",
            "准备发送聊天补全请求",
            {
                "model": self.settings.model,
                "temperature": temperature,
                "message_count": len(payload["messages"]),
                "has_image": messages_contain_image(payload["messages"]),
                "chat_params": _filter_supported_chat_params(chat_params),
            },
        )
        try:
            data = self._call_model_with_trace(
                payload,
                cancel_checker=cancel_checker,
                prompt_provenance=prompt_provenance,
                trace_metadata=trace_metadata,
            )
        except ApiRequestError as exc:
            if (
                _has_system_runtime_facts(payload, prompt_provenance)
                and _is_runtime_context_role_unsupported_error(exc)
            ):
                self._runtime_context_role = "user"
                self._trace_local.pending_fallback = "runtime_context_role"
                payload, prompt_provenance, runtime_context_placement = _prepare_chat_completion_payload(
                    model=self.settings.model,
                    system_prompt=system_prompt,
                    messages=_messages_with_runtime_context(
                        messages,
                        runtime_context,
                        "user",
                        runtime_items=prompt_metadata_with_context(trace_metadata),
                    ),
                    temperature=temperature,
                    chat_params=chat_params,
                    trace_metadata=trace_metadata,
                )
                log_event(
                    "API",
                    "端点不支持非首位 system 上下文，已回退为 user 上下文",
                    diagnostic_attributes(
                        exc,
                        reason_code="SYSTEM_CONTEXT_ROLE_UNSUPPORTED",
                        stage="compatibility_fallback",
                    ),
                )
                data = self._call_model_with_trace(
                    payload,
                    cancel_checker=cancel_checker,
                    prompt_provenance=prompt_provenance,
                    trace_metadata=trace_metadata,
                )
            else:
                raise
        check_cancelled(cancel_checker)

        try:
            raw_message = data["choices"][0]["message"]
            content = raw_message["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise ApiRequestError(f"API 返回格式无法解析：{json.dumps(data, ensure_ascii=False)}") from exc

        result = str(content).strip()
        usage = _summarize_token_usage(data.get("usage"))
        trace_call = self.last_trace_call
        if self._agent_trace_recorder is not None and isinstance(raw_message, dict):
            self._agent_trace_recorder.record_model_reply(
                trace_call,
                raw_message=raw_message,
                usage=usage,
            )
        log_event(
            "API",
            "模型原始文本返回",
            {
                **_model_call_log_attributes(trace_call),
                "content": result,
                "reply_chars": len(result),
                "parse_status": (
                    "empty"
                    if not result
                    else "valid"
                    if _is_valid_json(result)
                    else "invalid_json"
                    if result.lstrip().startswith(("{", "[", "```json"))
                    else "text"
                ),
                "usage": usage,
                **usage,
            },
            event="api.response.received",
            verbosity=1,
        )
        return result

    @_scoped_request
    def complete_with_tools(
        self,
        system_prompt: str,
        messages: list[ChatMessage],
        *,
        tools: list[dict[str, Any]] | None = None,
        tool_choice: str | dict[str, Any] | None = "auto",
        temperature: float = 0.8,
        structured_response: bool = False,
        runtime_context: str = "",
        trace_metadata: PromptTraceMetadata | None = None,
        cancel_checker: CancelChecker | None = None,
        **chat_params: Any,
    ) -> ChatCompletionTurn:
        """调用 OpenAI 原生 tools/tool_calls 协议并返回 assistant 消息。"""
        self._ensure_chat_config("缺少 API Key。请在设置中完成 API 配置。")
        check_cancelled(cancel_checker)

        if tools:
            chat_params["tools"] = tools
            chat_params["tool_choice"] = tool_choice
        if structured_response and "response_format" not in chat_params:
            chat_params["response_format"] = STRUCTURED_JSON_RESPONSE_FORMAT
        runtime_context_role = self._runtime_context_role
        request_messages = _messages_with_runtime_context(
            messages,
            runtime_context,
            runtime_context_role,
            runtime_items=prompt_metadata_with_context(trace_metadata),
        )
        payload, prompt_provenance, runtime_context_placement = _prepare_chat_completion_payload(
            model=self.settings.model,
            system_prompt=system_prompt,
            messages=request_messages,
            temperature=temperature,
            chat_params=chat_params,
            trace_metadata=trace_metadata,
        )
        log_event(
            "API",
            "准备发送原生工具聊天补全请求",
            {
                "model": self.settings.model,
                "temperature": temperature,
                "message_count": len(payload["messages"]),
                "tool_count": len(tools or []),
                "has_image": messages_contain_image(payload["messages"]),
                "chat_params": _filter_supported_chat_params(chat_params),
            },
        )
        try:
            data = self._call_model_with_trace(
                payload,
                cancel_checker=cancel_checker,
                prompt_provenance=prompt_provenance,
                trace_metadata=trace_metadata,
            )
        except ApiRequestError as exc:
            if (
                _has_system_runtime_facts(payload, prompt_provenance)
                and _is_runtime_context_role_unsupported_error(exc)
            ):
                self._runtime_context_role = "user"
                self._trace_local.pending_fallback = "runtime_context_role"
                runtime_context_role = "user"
                payload, prompt_provenance, runtime_context_placement = _prepare_chat_completion_payload(
                    model=self.settings.model,
                    system_prompt=system_prompt,
                    messages=_messages_with_runtime_context(
                        messages,
                        runtime_context,
                        "user",
                        runtime_items=prompt_metadata_with_context(trace_metadata),
                    ),
                    temperature=temperature,
                    chat_params=chat_params,
                    trace_metadata=trace_metadata,
                )
                log_event(
                    "API",
                    "端点不支持非首位 system 上下文，已回退为 user 上下文",
                    diagnostic_attributes(
                        exc,
                        reason_code="SYSTEM_CONTEXT_ROLE_UNSUPPORTED",
                        stage="compatibility_fallback",
                    ),
                )
                data = self._call_model_with_trace(
                    payload,
                    cancel_checker=cancel_checker,
                    prompt_provenance=prompt_provenance,
                    trace_metadata=trace_metadata,
                )
            else:
                raise
        check_cancelled(cancel_checker)

        try:
            raw_message = data["choices"][0]["message"]
        except (KeyError, IndexError, TypeError) as exc:
            raise ApiRequestError(f"API 返回格式无法解析：{json.dumps(data, ensure_ascii=False)}") from exc
        if not isinstance(raw_message, dict):
            raise ApiRequestError(f"API 返回 message 格式无法解析：{json.dumps(data, ensure_ascii=False)}")

        content = raw_message.get("content")
        usage = _summarize_token_usage(data.get("usage"))
        trace_call = self.last_trace_call
        if self._agent_trace_recorder is not None:
            raw_tool_calls = raw_message.get("tool_calls")
            self._agent_trace_recorder.record_model_reply(
                trace_call,
                raw_message=raw_message,
                usage=usage,
                parsed_tool_calls=(raw_tool_calls if isinstance(raw_tool_calls, list) else ()),
            )
        tool_calls = _parse_native_tool_calls(raw_message.get("tool_calls"))
        pseudo_tool_calls = False
        if not tool_calls:
            tool_calls = _parse_pseudo_tool_calls_from_content(content)
            pseudo_tool_calls = bool(tool_calls)
        normalized_message = _normalize_assistant_message(raw_message, content, tool_calls)
        parse_status = (
            "empty"
            if not str(content or "").strip()
            else "valid"
            if _is_valid_json(str(content))
            else "invalid_json"
            if str(content).lstrip().startswith(("{", "[", "```json"))
            else "text"
        )
        log_event(
            "API",
            "原生工具模型返回",
            {
                **_model_call_log_attributes(trace_call),
                "content": str(content or "").strip(),
                "reply_chars": len(str(content or "").strip()),
                "tool_call_count": len(tool_calls),
                "parse_status": parse_status,
                "tool_calls": [
                    {"id": call.id, "name": call.name, "arguments": call.arguments}
                    for call in tool_calls
                ],
                "usage": usage,
                **usage,
            },
            event="api.response.received",
            verbosity=1,
        )
        return ChatCompletionTurn(
            content=str(content or "").strip(),
            tool_calls=tool_calls,
            message=normalized_message,
            runtime_context_role=runtime_context_role,
            runtime_context_placement=runtime_context_placement,
            usage=usage,
            raw_content=str(content or ""),
            parse_status=parse_status,
            trace_call=trace_call,
        )

    def _call_model_with_trace(
        self, payload, *, cancel_checker=None, prompt_provenance=(), trace_metadata=None,
    ):
        # Protocol compatibility belongs to the selected model provider. Assistant
        # records one semantic call, including provider-reported attempts.
        self._trace_local.request_diagnostic = {}
        pending = getattr(self._trace_local, "pending_fallback", None)
        self._trace_local.pending_fallback = None
        if pending:
            self._trace_local.request_diagnostic["compatibilityFallback"] = pending
        check_cancelled(cancel_checker)
        trace_call = None
        if self._agent_trace_recorder is not None:
            trace_call = self._agent_trace_recorder.start_model_call(
                model=self.settings.model, payload=payload,
                prompt_provenance=prompt_provenance, metadata=trace_metadata,
            )
        self._trace_local.last_call = trace_call
        attributes = _model_call_log_attributes(trace_call, metadata=trace_metadata, model=self.settings.model)
        estimate = _safe_prompt_runtime_summary(payload, prompt_provenance)
        log_event("Context", "模型上下文已构建", {**attributes, **estimate}, event="context.prompt.prepared", verbosity=1)
        started = time.perf_counter()
        log_event("API", "发送模型请求", attributes, event="api.request.started", verbosity=1)
        try:
            response = self._post_chat_completions(payload, cancel_checker=cancel_checker)
        except BaseException as error:
            self._trace_local.request_diagnostic.update(_request_failure(error))
            if _is_runtime_context_role_unsupported_error(error):
                self._trace_local.request_diagnostic.update(faultDomain="compatibility", reasonCode="MODEL_PARAMETER_UNSUPPORTED", stage="response")
            _submit_model_call_metric(trace_call, request=self._trace_local.request_diagnostic,
                settings=self.settings, estimate=estimate, usage=None,
                latency_ms=int((time.perf_counter() - started) * 1000),
                outcome="cancelled" if isinstance(error, OperationCancelled) else "failed",
                error_code="REQUEST_CANCELLED" if isinstance(error, OperationCancelled) else "MODEL_REQUEST_FAILED")
            if trace_call is not None and trace_call.auto_operation:
                self._agent_trace_recorder.finish_operation(trace_call.operation_id, status="failed")
            raise
        _submit_model_call_metric(trace_call, request=self._trace_local.request_diagnostic,
            settings=self.settings, estimate=estimate, usage=_summarize_token_usage(response.get("usage")),
            latency_ms=int((time.perf_counter() - started) * 1000), outcome="success", error_code=None)
        return response

    def _ensure_chat_config(self, _message):
        if not self.settings.model:
            raise ApiConfigError("请先选择模型。")

    def _post_chat_completions(
        self,
        payload: dict[str, Any],
        *,
        cancel_checker: CancelChecker | None = None,
    ) -> dict[str, Any]:
        """调用 OpenAI 兼容的 chat/completions 接口并返回 JSON 数据。"""
        check_cancelled(cancel_checker)
        model_name = payload.get("model")
        self._emit_llm_event("llm.request.started", {"model": model_name})
        try:
            data = self._send_request(payload, cancel_checker=cancel_checker)
            check_cancelled(cancel_checker)
            if (
                not isinstance(data, dict)
                or not isinstance(data.get("choices"), list)
                or not data["choices"]
                or not isinstance(data["choices"][0], dict)
                or not isinstance(data["choices"][0].get("message"), dict)
            ):
                self._trace_local.request_diagnostic.update(
                    faultDomain="protocol",
                    reasonCode="MODEL_RESPONSE_INVALID",
                    stage="decode",
                )
                raise ApiRequestError("API 返回的消息结构无效。")
        except Exception as exc:  # noqa: BLE001 — 仅用于派发失败事件，随后原样抛出
            self._emit_llm_event(
                "llm.request.failed",
                {
                    "model": model_name,
                    **diagnostic_attributes(
                        exc,
                        reason_code="LLM_REQUEST_FAILED",
                        stage="llm_request",
                    ),
                },
            )
            raise

        self._emit_llm_event("llm.request.finished", {"model": model_name})
        return data

    def _send_request(self, payload, *, cancel_checker=None):
        request = _model_request(payload)
        try:
            response = self._model_client.complete(request, cancel_checker=cancel_checker)
        except ModelError as error:
            raise ApiRequestError(str(error)) from error
        diagnostic = dict(response.get("diagnostics") or {})
        fallbacks = diagnostic.pop("compatibilityFallbacks", [])
        if fallbacks:
            diagnostic["compatibilityFallback"] = fallbacks[-1]
        self._trace_local.request_diagnostic.update(diagnostic)
        message = response["message"]
        internal_message = {"role": "assistant", "content": message.get("content"), "providerData": message.get("providerData", {})}
        if message.get("toolCalls"):
            internal_message["tool_calls"] = [{**call.get("providerData", {}), "id": call["id"], "type": "function",
                "function": {"name": call["name"], "arguments": call["arguments"]}} for call in message["toolCalls"]]
        return {"choices": [{"message": internal_message, "finish_reason": response.get("finishReason")}],
                "usage": response.get("usage", {})}


def _model_request(payload):
    messages = []
    for value in payload["messages"]:
        message = {"role": value["role"], "content": value.get("content")}
        if value.get("providerData"):
            message["providerData"] = value["providerData"]
        if isinstance(message["content"], list):
            message["content"] = [({"type": "image", "dataUrl": part["image_url"]["url"],
                                    "detail": part["image_url"].get("detail", "auto")}
                                   if part.get("type") == "image_url" else dict(part)) for part in message["content"]]
        if value.get("tool_calls"):
            message["toolCalls"] = [{"id": call["id"], "name": call["function"]["name"],
                                      "arguments": call["function"]["arguments"],
                                      "providerData": {key: item for key, item in call.items() if key not in {"id", "type", "function"}}} for call in value["tool_calls"]]
        if value.get("tool_call_id"):
            message["toolCallId"] = value["tool_call_id"]
        messages.append(message)
    return {"messages": messages,
            "parameters": {key: value for key, value in payload.items()
                           if key not in {"messages", "model", "tools", "tool_choice", "response_format", "stream"}},
            "tools": [item["function"] for item in payload.get("tools") or []],
            "responseFormat": payload.get("response_format"), "toolChoice": payload.get("tool_choice"),
            "stream": bool(payload.get("stream", False))}


def _build_segmented_reply_instruction(
    reply_tones: list[str] | None,
    reply_visual: Mapping[str, Any] | None = None,
) -> str:
    return build_segmented_reply_instruction(reply_tones, reply_visual)


def _build_chat_completion_payload(
    *,
    model: str,
    system_prompt: str,
    messages: list[ChatMessage],
    temperature: float,
    chat_params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """构建 OpenAI 兼容请求体，并丢弃已知非标准参数。"""
    payload, _provenance, _placement = _prepare_chat_completion_payload(
        model=model,
        system_prompt=system_prompt,
        messages=messages,
        temperature=temperature,
        chat_params=chat_params,
    )
    return payload


def _prepare_chat_completion_payload(
    *,
    model: str,
    system_prompt: str,
    messages: list[ChatMessage],
    temperature: float,
    chat_params: dict[str, Any] | None = None,
    trace_metadata: PromptTraceMetadata | None = None,
) -> tuple[dict[str, Any], list[MessageProvenance | None], str]:
    """Build the exact Provider payload and its parallel, Python-only provenance."""
    _ = trace_metadata
    # 当 messages 中包含 role:tool 且尾部为 system 运行时上下文时，
    # 将尾部 system 消息合并到主 system prompt，避免干扰代理的
    # tool_call_id -> functionResponse.name 翻译。
    _system_prompt = system_prompt
    _messages = list(messages)
    appended_runtime_items: tuple[dict[str, Any], ...] = ()
    runtime_context_placement = "none"
    tail_provenance = message_provenance(_messages[-1]) if _messages else None
    if _has_tool_messages(_messages) and _messages and _messages[-1].get("role") == "system":
        tail_content = _messages[-1].get("content", "")
        if isinstance(tail_content, str) and tail_content.strip():
            _system_prompt = f"{_system_prompt.strip()}\n\n{tail_content.strip()}"
        if tail_provenance is not None and tail_provenance.kind == "runtime_context":
            appended_runtime_items = tail_provenance.runtime_items
            runtime_context_placement = "merged_system"
        _messages = _messages[:-1]
    elif tail_provenance is not None and tail_provenance.kind == "runtime_context":
        runtime_context_placement = (
            "tail_user" if _messages[-1].get("role") == "user" else "tail_system"
        )
    clean_messages = [strip_message_provenance(message) for message in _messages]
    provenance: list[MessageProvenance | None] = [
        MessageProvenance("system_prompt", runtime_items=appended_runtime_items)
    ]
    provenance.extend(message_provenance(message) for message in _messages)
    payload = {
        "model": model,
        "messages": [
            {
                "role": "system",
                "content": _system_prompt.strip(),
            },
            *clean_messages,
        ],
    }
    payload["temperature"] = temperature
    payload.update(_filter_supported_chat_params(chat_params or {}))
    _ensure_json_keyword_for_json_object_response(payload)
    return payload, provenance, runtime_context_placement


def _messages_with_runtime_context(
    messages: list[ChatMessage],
    runtime_context: str,
    role: str,
    *,
    runtime_items: tuple[dict[str, Any], ...] = (),
) -> list[ChatMessage]:
    request_messages = [*messages]
    if role == "user":
        for index, message in enumerate(request_messages):
            provenance = message_provenance(message)
            if (
                message.get("role") == "system"
                and provenance is not None and provenance.kind == "recent_proactive"
            ):
                # Keep the existing untrusted-facts wrapper and provenance;
                # these past utterances are not a new user request.
                request_messages[index] = {**message, "role": "user"}
    if not runtime_context.strip():
        return request_messages
    content = runtime_context.strip()
    if role == "user":
        content = (
            "[Sakura runtime context supplied by the host; each section declares its purpose]\n"
            + content
        )
    return [
        *request_messages,
        traced_message(
            {"role": role, "content": content},
            "runtime_context",
            runtime_items=runtime_items,
        ),
    ]


def _has_system_runtime_facts(
    payload: Mapping[str, Any], provenance: Sequence[MessageProvenance | None],
) -> bool:
    return any(
        message.get("role") == "system"
        and source is not None and source.kind in {"runtime_context", "recent_proactive"}
        for message, source in zip(payload["messages"][1:], provenance[1:])
    )


def _is_runtime_context_role_unsupported_error(exc: ApiRequestError) -> bool:
    text = str(exc).lower()
    if re.search(r"\bsystem messages? must be at the beginning(?:[.!\"']|$)", text):
        return True
    role_markers = ("system", "role", "messages")
    rejection_markers = (
        "unsupported", "not support", "invalid", "must be first",
        "only one", "not allowed", "unexpected", "order",
    )
    return any(marker in text for marker in role_markers) and any(
        marker in text for marker in rejection_markers
    )


def _filter_supported_chat_params(params: dict[str, Any]) -> dict[str, Any]:
    """过滤兼容端点常见不支持的内部参数，避免请求在网关层失败。"""
    filtered: dict[str, Any] = {}
    for key, value in params.items():
        if key not in SUPPORTED_CHAT_COMPLETION_PARAMS or value is None:
            continue
        if key == "max_tokens" and params.get("max_completion_tokens") is not None:
            continue
        filtered[key] = value
    return filtered


def _summarize_token_usage(usage: Any) -> dict[str, Any]:
    if not isinstance(usage, dict):
        return {}
    summary: dict[str, Any] = {}
    for key in (
        "prompt_tokens",
        "completion_tokens",
        "total_tokens",
        "input_tokens",
        "output_tokens",
    ):
        value = usage.get(key)
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
            summary[key] = value
    prompt_details = usage.get("prompt_tokens_details")
    completion_details = usage.get("completion_tokens_details")
    if isinstance(prompt_details, dict):
        cached = prompt_details.get("cached_tokens")
        if isinstance(cached, int) and not isinstance(cached, bool) and cached >= 0:
            summary["cached_input_tokens"] = cached
    if isinstance(completion_details, dict):
        reasoning = completion_details.get("reasoning_tokens")
        if isinstance(reasoning, int) and not isinstance(reasoning, bool) and reasoning >= 0:
            summary["reasoning_tokens"] = reasoning
    return summary


def _request_failure(error: BaseException) -> dict[str, Any]:
    if isinstance(error, OperationCancelled):
        return {"faultDomain": "cancelled", "reasonCode": "REQUEST_CANCELLED", "stage": "request"}
    cause, seen = error, set()
    while cause is not None and id(cause) not in seen:
        seen.add(id(cause))
        if isinstance(cause, ModelError):
            return {**cause.diagnostics, "reasonCode": cause.code}
        cause = cause.__cause__
    return {"faultDomain": "unknown", "reasonCode": "MODEL_REQUEST_FAILED", "stage": "unknown"}


def _submit_model_call_metric(
    call: TraceCall | None,
    *,
    settings: DialogueSettings,
    estimate: Mapping[str, Any],
    usage: Mapping[str, Any] | None,
    latency_ms: int,
    outcome: str,
    error_code: str | None,
    request: Mapping[str, Any] | None = None,
) -> None:
    if call is None:
        return
    try:
        candidate = _model_call_metric_candidate(
            call,
            settings=settings,
            estimate=estimate,
            usage=usage,
            latency_ms=latency_ms,
            outcome=outcome,
            error_code=error_code,
            request=request,
        )
        submit_telemetry_model_call(candidate)
    except Exception:  # noqa: BLE001 - telemetry must never affect the model call
        return


def _model_call_metric_candidate(
    call: TraceCall,
    *,
    settings: DialogueSettings,
    estimate: Mapping[str, Any],
    usage: Mapping[str, Any] | None,
    latency_ms: int,
    outcome: str,
    error_code: str | None,
    request: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    usage_keys = (
        "prompt_tokens", "completion_tokens", "total_tokens", "input_tokens",
        "output_tokens", "cached_input_tokens", "reasoning_tokens",
    )
    usage_value = None if not usage else {
        _camel_case_metric(key): (
            value if isinstance((value := usage.get(key)), int)
            and not isinstance(value, bool) and value >= 0 else None
        )
        for key in usage_keys
    }
    estimate_value = {
        "requestTokens": _nonnegative_metric(estimate.get("request_estimated_tokens")),
        "historyTokens": _nonnegative_metric(estimate.get("history_estimated_tokens")),
        "memoryTokens": _nonnegative_metric(estimate.get("memory_estimated_tokens")),
        "dynamicContextTokens": _nonnegative_metric(estimate.get("dynamic_context_estimated_tokens")),
        "toolSchemaTokens": _nonnegative_metric(estimate.get("tool_schema_estimated_tokens")),
        "historyMessages": _nonnegative_metric(estimate.get("history_messages")),
        "memories": _nonnegative_metric(estimate.get("memories")),
        "toolCount": _nonnegative_metric(estimate.get("tool_count")),
    }
    source = str(settings.context_window_source or "fallback")
    source = "configured" if source == "user" else source
    if source not in {"provider", "configured", "fallback"}:
        source = "unknown"
    return {
        "schema": 2,
        "request": dict(request or {}),
        "operationId": call.operation_id or None,
        "modelCall": call.model_call,
        "purpose": call.purpose,
        "modelFamily": _model_family(call.model),
        "outcome": outcome,
        "errorCode": error_code,
        "latencyMs": max(0, int(latency_ms)),
        "contextWindowTokens": max(0, int(settings.context_window_tokens)),
        "contextWindowSource": source,
        "usage": usage_value,
        "estimate": estimate_value,
    }


def _camel_case_metric(value: str) -> str:
    head, *tail = value.split("_")
    return head + "".join(part.title() for part in tail)


def _nonnegative_metric(value: object) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0


def _model_family(model: object) -> str:
    value = str(model or "").strip().lower()
    if value.startswith(("gpt-", "chatgpt-", "o1", "o3", "o4")):
        return "openai"
    if value.startswith("claude"):
        return "anthropic"
    if value.startswith("gemini"):
        return "gemini"
    if value.startswith("deepseek"):
        return "deepseek"
    return "custom" if value else "unknown"


def _model_call_log_attributes(
    call: TraceCall | None,
    *,
    metadata: PromptTraceMetadata | None = None,
    model: str = "",
) -> dict[str, Any]:
    attributes: dict[str, Any] = {}
    if call is not None:
        attributes.update(
            {
                "trace_id": str(call.trace),
                "model_call": call.model_call,
                "purpose": call.purpose,
                "model": call.model,
            }
        )
    else:
        purpose = metadata.purpose if metadata is not None else ""
        if purpose:
            attributes["purpose"] = purpose
        if model:
            attributes["model"] = model
    return attributes


def _safe_prompt_runtime_summary(
    payload: dict[str, Any],
    prompt_provenance: Sequence[MessageProvenance | None],
) -> dict[str, int]:
    try:
        return summarize_prompt_payload(payload, prompt_provenance)
    except Exception:  # noqa: BLE001 - runtime logging must never affect a model call
        return {}


def _is_valid_json(value: str) -> bool:
    try:
        json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return False
    return True


def _chat_reply_trace_mapping(reply: ChatReply) -> dict[str, Any]:
    return {
        "segments": [
            {
                "ja": segment.text,
                "zh": segment.translation,
                "tone": segment.tone,
                "portrait": segment.portrait,
                **({"control": segment.control} if segment.control is not None else {}),
            }
            for segment in reply.segments
        ]
    }


def _ensure_json_keyword_for_json_object_response(payload: dict[str, Any]) -> None:
    """json_object 模式下，部分兼容网关要求请求消息显式包含英文 json。"""
    response_format = payload.get("response_format")
    if not isinstance(response_format, dict) or response_format.get("type") != "json_object":
        return
    messages = payload.get("messages")
    if not isinstance(messages, list) or _messages_contain_json_keyword(messages):
        return
    system_message = messages[0] if messages else None
    if not isinstance(system_message, dict) or system_message.get("role") != "system":
        return
    content = system_message.get("content")
    if isinstance(content, str):
        system_message["content"] = f"{content}\n\n请只输出 JSON（json）对象。"


def _messages_contain_json_keyword(messages: list[Any]) -> bool:
    for message in messages:
        if not isinstance(message, dict):
            continue
        if _value_contains_json_keyword(message.get("content")):
            return True
    return False


def _value_contains_json_keyword(value: Any) -> bool:
    if isinstance(value, str):
        return "json" in value.lower()
    if isinstance(value, list):
        return any(_value_contains_json_keyword(item) for item in value)
    if isinstance(value, dict):
        return any(_value_contains_json_keyword(item) for item in value.values())
    return False


def _parse_native_tool_calls(raw_tool_calls: Any) -> list[NativeToolCall]:
    if not isinstance(raw_tool_calls, list):
        return []
    parsed: list[NativeToolCall] = []
    for index, raw_call in enumerate(raw_tool_calls):
        if not isinstance(raw_call, dict):
            continue
        function = raw_call.get("function")
        if not isinstance(function, dict):
            continue
        name = function.get("name")
        if not isinstance(name, str) or not name.strip():
            continue
        raw_arguments = function.get("arguments")
        arguments_error = ""
        if not isinstance(raw_arguments, str):
            arguments_json = json.dumps(raw_arguments, ensure_ascii=False)
            arguments = {}
            arguments_error = "工具参数必须是 JSON object 字符串。"
        else:
            arguments_json = raw_arguments
            try:
                decoded_arguments = json.loads(arguments_json or "{}")
            except json.JSONDecodeError as exc:
                arguments = {}
                arguments_error = f"工具参数不是有效 JSON：{exc.msg}。"
            else:
                if isinstance(decoded_arguments, dict):
                    arguments = decoded_arguments
                else:
                    arguments = {}
                    arguments_error = "工具参数必须解码为 JSON object。"
        call_id = raw_call.get("id")
        if not isinstance(call_id, str) or not call_id.strip():
            call_id = f"tool_call_{index}"
        parsed.append(
            NativeToolCall(
                id=call_id.strip(),
                name=name.strip(),
                arguments=arguments,
                arguments_json=arguments_json,
                arguments_error=arguments_error,
            )
        )
    return parsed


def _parse_pseudo_tool_calls_from_content(content: Any) -> list[NativeToolCall]:
    """Parse OpenAI-compatible providers that emit tool calls as JSON text.

    Some providers combine poorly with response_format=json_object and return
    {"tool_call": "name", "parameters": {...}} in message.content instead of
    native message.tool_calls. Keep this conservative: only accept top-level
    JSON objects/lists that clearly describe tool calls.
    """

    if not isinstance(content, str) or not content.strip():
        return []
    try:
        raw = json.loads(content)
    except json.JSONDecodeError:
        return []

    items: list[Any]
    if isinstance(raw, dict) and isinstance(raw.get("tool_calls"), list):
        items = raw["tool_calls"]
    elif isinstance(raw, dict) and isinstance(raw.get("tool_call"), dict):
        items = [raw["tool_call"]]
    elif isinstance(raw, dict) and (
        "tool_call" in raw or "tool" in raw or "name" in raw or "tool_name" in raw
    ):
        items = [raw]
    elif isinstance(raw, list):
        items = raw
    else:
        return []

    parsed: list[NativeToolCall] = []
    for index, item in enumerate(items):
        call = _parse_pseudo_tool_call(item, index)
        if call is not None:
            parsed.append(call)
    return parsed


def _parse_pseudo_tool_call(item: Any, index: int) -> NativeToolCall | None:
    if not isinstance(item, dict):
        return None
    name = item.get("tool_call") or item.get("tool") or item.get("name") or item.get("tool_name")
    if not isinstance(name, str) or not name.strip():
        return None
    arguments = (
        item.get("arguments")
        if "arguments" in item
        else item.get("parameters", item.get("args", {}))
    )
    if isinstance(arguments, str):
        try:
            decoded = json.loads(arguments or "{}")
        except json.JSONDecodeError as exc:
            return NativeToolCall(
                id=str(item.get("id") or f"pseudo_tool_call_{index}"),
                name=name.strip(),
                arguments={},
                arguments_json=arguments,
                arguments_error=f"工具参数不是有效 JSON：{exc.msg}。",
            )
        arguments = decoded
    arguments_error = ""
    if not isinstance(arguments, dict):
        arguments_error = "工具参数必须是 JSON object。"
        arguments = {}
    arguments_json = json.dumps(arguments, ensure_ascii=False)
    call_id = item.get("id")
    if not isinstance(call_id, str) or not call_id.strip():
        call_id = f"pseudo_tool_call_{index}"
    return NativeToolCall(
        id=call_id.strip(),
        name=name.strip(),
        arguments=dict(arguments),
        arguments_json=arguments_json,
        arguments_error=arguments_error,
    )


def _normalize_assistant_message(
    raw_message: dict[str, Any],
    content: Any,
    tool_calls: list[NativeToolCall],
) -> dict[str, Any]:
    message: dict[str, Any] = {
        "role": "assistant",
        "content": content if isinstance(content, str) else "",
    }
    if raw_message.get("providerData"):
        message["providerData"] = raw_message["providerData"]
    if tool_calls:
        raw_tool_calls = raw_message.get("tool_calls")
        normalized_calls: list[dict[str, Any]] = []
        for index, call in enumerate(tool_calls):
            raw_call = (
                raw_tool_calls[index]
                if isinstance(raw_tool_calls, list)
                and index < len(raw_tool_calls)
                and isinstance(raw_tool_calls[index], dict)
                else {}
            )
            normalized_call = dict(raw_call)
            normalized_call["id"] = call.id
            normalized_call["type"] = "function"
            raw_function = raw_call.get("function")
            normalized_function = dict(raw_function) if isinstance(raw_function, dict) else {}
            normalized_function["name"] = call.name
            normalized_function["arguments"] = call.arguments_json
            normalized_call["function"] = normalized_function
            normalized_calls.append(normalized_call)
        message["tool_calls"] = normalized_calls
    return message


def messages_contain_image(messages: list[ChatMessage]) -> bool:
    """检查消息中是否包含 OpenAI 兼容 image_url 内容块。"""
    for message in messages:
        content = message.get("content")
        if not isinstance(content, list):
            continue
        for part in content:
            if isinstance(part, dict) and part.get("type") == "image_url":
                return True
    return False


def is_vision_unsupported_error(error: BaseException | str) -> bool:
    """识别常见的非视觉模型或兼容接口图片输入错误。"""
    text = str(error).lower()
    markers = (
        "image_url",
        "image input",
        "image inputs",
        "vision",
        "multimodal",
        "modalities",
        "unsupported content",
        "content type",
        "does not support image",
        "only text",
    )
    return any(marker in text for marker in markers)


def _has_tool_messages(messages: list[ChatMessage]) -> bool:
    return any(msg.get("role") == "tool" for msg in messages)
