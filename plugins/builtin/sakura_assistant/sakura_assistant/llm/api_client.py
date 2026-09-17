from __future__ import annotations

from collections.abc import Mapping
from contextlib import contextmanager
from functools import wraps

import asyncio
import json
import re
import ssl
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Sequence
from urllib.parse import urlparse, urlunparse

import httpx
from openai import APIConnectionError, APIStatusError, APITimeoutError, AsyncOpenAI
# Resolve the SDK's lazy resource imports before readiness; importing them
# inside the first request would block the event loop and its cancellation.
from openai.resources.chat import AsyncChat

from sakura_cancellation import CancelChecker, OperationCancelled, check_cancelled
from sakura_assistant.diagnostics import submit_telemetry_model_call
from sakura_http import is_loopback_url, proxy_for_url
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


MAX_COMPATIBILITY_ATTEMPTS = 3
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



from sakura_model import ApiSettings, ApiConfigError, ApiRequestError, normalize_openai_base_url as _normalize_openai_base_url


@dataclass(frozen=True)
class NativeToolCall:
    """OpenAI 原生 tool_call，保留 id 以便后续 tool role 回填。"""

    id: str
    name: str
    arguments: dict[str, Any]
    arguments_json: str = "{}"
    arguments_error: str = ""


@dataclass(frozen=True)
class ChatCompletionTurn:
    """一次 Chat Completions 返回的 assistant 消息。"""

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
    settings: ApiSettings
    unsupported_chat_params: set[str] = field(default_factory=set)
    runtime_context_role: str = "system"


def _scoped_request(method):
    @wraps(method)
    def invoke(self, *args, **kwargs):
        with self.request_scope():
            return method(self, *args, **kwargs)
    return invoke


class OpenAICompatibleClient:
    def __init__(
        self,
        settings: ApiSettings,
        *,
        agent_trace_recorder: AgentTraceRecorder | None = None,
        app_version: str | None = None,
    ) -> None:
        self._configuration = _ClientConfiguration(settings)
        resolved_version = app_version or "dev"
        self._app_version = resolved_version.strip().removeprefix("v") or "dev"
        # 可选事件发射器（由宿主注入），用于派发 llm.request.* 插件事件。
        self._event_emit: Callable[[str, dict[str, Any] | None], None] | None = None
        self._agent_trace_recorder = agent_trace_recorder
        self._trace_local = threading.local()
        if agent_trace_recorder is not None:
            agent_trace_recorder.add_secret(settings.api_key)

    @property
    def _active_configuration(self) -> _ClientConfiguration:
        return getattr(self._trace_local, "configuration", self._configuration)

    @property
    def settings(self) -> ApiSettings:
        return self._active_configuration.settings

    @property
    def _unsupported_chat_params(self) -> set[str]:
        return self._active_configuration.unsupported_chat_params

    @property
    def _runtime_context_role(self) -> str:
        return self._active_configuration.runtime_context_role

    @_runtime_context_role.setter
    def _runtime_context_role(self, value: str) -> None:
        self._active_configuration.runtime_context_role = value

    @contextmanager
    def request_scope(self):
        """固定本轮配置，在当前线程复用连接；退出时回收所有异步 I/O。"""
        if getattr(self._trace_local, "configuration", None) is not None:
            yield self
            return
        self._trace_local.configuration = self._configuration
        self._trace_local.sdk = None
        self._trace_local.attempt = 0
        try:
            with asyncio.Runner() as runner:
                self._trace_local.runner = runner
                try:
                    yield self
                finally:
                    if self._trace_local.sdk is not None:
                        runner.run(self._trace_local.sdk.close())
        finally:
            del self._trace_local.configuration
            del self._trace_local.runner
            del self._trace_local.sdk

    def set_agent_trace_recorder(self, recorder: AgentTraceRecorder | None) -> None:
        self._agent_trace_recorder = recorder
        if recorder is not None:
            recorder.add_secret(self.settings.api_key)

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

    def update_settings(self, settings: ApiSettings) -> None:
        """运行时更新 API 配置，供设置界面保存后立即生效。"""
        self._configuration = _ClientConfiguration(settings)
        if self._agent_trace_recorder is not None:
            self._agent_trace_recorder.add_secret(settings.api_key)
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
    def test_connection(self, *, cancel_checker: CancelChecker | None = None) -> str:
        """发送一次最小聊天请求，验证 Base URL、API Key 和模型是否可用。"""
        self._ensure_chat_config("缺少 API_KEY。请在设置中填写 API Key。")

        # 连通性检测只需验证 Base URL / API Key / 模型可用，不发送 temperature：
        # 部分推理模型只接受默认温度；也不限制输出 token，避免挤占思考预算。
        payload = {
            "model": self.settings.model,
            "messages": [
                {
                    "role": "user",
                    "content": "Reply with only OK.",
                },
            ],
        }
        data = self._post_chat_completions_with_compatibility_fallbacks(
            payload,
            cancel_checker=cancel_checker,
        )
        check_cancelled(cancel_checker)

        try:
            content = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise ApiRequestError(f"API 返回格式无法解析：{json.dumps(data, ensure_ascii=False)}") from exc

        return str(content).strip() or "OK"

    @_scoped_request
    def list_models(self, *, cancel_checker: CancelChecker | None = None) -> list[str]:
        """读取 OpenAI 兼容 /models 接口，返回可选择的模型 id 列表。"""
        self._ensure_model_list_config()
        base_url = _normalize_openai_base_url(self.settings.base_url)
        url = f"{base_url}/models"
        log_event(
            "API",
            "准备检测模型列表",
            {
                "url": url,
                "configured_base_url": self.settings.base_url,
                "timeout_seconds": self.settings.timeout_seconds,
            },
        )
        data = self._send_request(None, cancel_checker=cancel_checker)
        check_cancelled(cancel_checker)

        model_ids = _parse_model_ids(data)
        log_event(
            "API",
            "模型列表探测完成",
            {
                "total_count": len(model_ids),
                "models": model_ids,
            },
        )
        return model_ids

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
                "base_url": _normalize_openai_base_url(self.settings.base_url),
                "configured_base_url": self.settings.base_url,
                "endpoint_host": urlparse(_normalize_openai_base_url(self.settings.base_url)).netloc,
                "model": self.settings.model,
                "timeout_seconds": self.settings.timeout_seconds,
                "temperature": temperature,
                "message_count": len(payload["messages"]),
                "has_image": messages_contain_image(payload["messages"]),
                "chat_params": _filter_supported_chat_params(chat_params),
            },
        )
        try:
            data = self._post_chat_completions_with_compatibility_fallbacks(
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
                data = self._post_chat_completions_with_compatibility_fallbacks(
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
                "base_url": _normalize_openai_base_url(self.settings.base_url),
                "configured_base_url": self.settings.base_url,
                "endpoint_host": urlparse(_normalize_openai_base_url(self.settings.base_url)).netloc,
                "model": self.settings.model,
                "timeout_seconds": self.settings.timeout_seconds,
                "temperature": temperature,
                "message_count": len(payload["messages"]),
                "tool_count": len(tools or []),
                "has_image": messages_contain_image(payload["messages"]),
                "chat_params": _filter_supported_chat_params(chat_params),
            },
        )
        try:
            data = self._post_chat_completions_with_compatibility_fallbacks(
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
                data = self._post_chat_completions_with_compatibility_fallbacks(
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

    def _post_chat_completions_with_compatibility_fallbacks(
        self,
        payload: dict[str, Any],
        *,
        cancel_checker: CancelChecker | None = None,
        prompt_provenance: Sequence[MessageProvenance | None] = (),
        trace_metadata: PromptTraceMetadata | None = None,
    ) -> dict[str, Any]:
        fallback_payload = dict(payload)
        for param in self._unsupported_chat_params:
            fallback_payload.pop(param, None)
        fallback_kind = getattr(self._trace_local, "pending_fallback", None)
        self._trace_local.pending_fallback = None
        for attempt in range(1, MAX_COMPATIBILITY_ATTEMPTS + 1):
            self._trace_local.request_diagnostic = (
                {"compatibilityFallback": fallback_kind} if fallback_kind else {}
            )
            check_cancelled(cancel_checker)
            trace_call = None
            if self._agent_trace_recorder is not None:
                trace_call = self._agent_trace_recorder.start_model_call(
                    model=self.settings.model,
                    payload=fallback_payload,
                    prompt_provenance=prompt_provenance,
                    metadata=trace_metadata,
                )
            self._trace_local.last_call = trace_call
            call_attributes = _model_call_log_attributes(
                trace_call,
                metadata=trace_metadata,
                model=self.settings.model,
            )
            metric_estimate = _safe_prompt_runtime_summary(
                fallback_payload,
                prompt_provenance,
            )
            log_event(
                "Context",
                "模型上下文已构建",
                {
                    **call_attributes,
                    **metric_estimate,
                },
                event="context.prompt.prepared",
                verbosity=1,
            )
            metric_started_at = time.perf_counter()
            log_event(
                "API",
                "发送模型请求",
                {
                    **call_attributes,
                    "provider": "openai_compatible",
                },
                event="api.request.started",
                verbosity=1,
            )
            try:
                response = self._post_chat_completions(
                    fallback_payload,
                    cancel_checker=cancel_checker,
                )
            except ApiRequestError as exc:
                if (
                    _is_response_format_unsupported_error(exc)
                    or _is_temperature_unsupported_error(exc)
                    or _is_runtime_context_role_unsupported_error(exc)
                ):
                    self._trace_local.request_diagnostic.update(
                        faultDomain="compatibility",
                        reasonCode="MODEL_PARAMETER_UNSUPPORTED",
                        stage="response",
                    )
                elif self._trace_local.request_diagnostic.get("faultDomain") not in {
                    "context",
                    "protocol",
                }:
                    self._trace_local.request_diagnostic.update(_request_failure(exc))
                _submit_model_call_metric(
                    trace_call,
                    request=getattr(self._trace_local, "request_diagnostic", {}),
                    settings=self.settings,
                    estimate=metric_estimate,
                    usage=None,
                    latency_ms=int((time.perf_counter() - metric_started_at) * 1000),
                    outcome="failed",
                    error_code="MODEL_REQUEST_FAILED",
                )
                if trace_call is not None and trace_call.auto_operation:
                    self._agent_trace_recorder.finish_operation(
                        trace_call.operation_id, status="failed"
                    )
                if "response_format" in fallback_payload and _is_response_format_unsupported_error(exc):
                    fallback_kind = "response_format"
                    self._unsupported_chat_params.add("response_format")
                    fallback_payload.pop("response_format", None)
                    log_event(
                        "API",
                        "结构化 response_format 不受支持，已回退普通请求",
                        {
                            "attempt": attempt,
                            "max_attempts": MAX_COMPATIBILITY_ATTEMPTS,
                            **diagnostic_attributes(
                                exc,
                                reason_code="MODEL_REQUEST_RETRYABLE",
                                stage="request_retry",
                            ),
                        },
                    )
                    continue
                if "temperature" in fallback_payload and _is_temperature_unsupported_error(exc):
                    fallback_kind = "temperature"
                    self._unsupported_chat_params.add("temperature")
                    fallback_payload.pop("temperature", None)
                    log_event(
                        "API",
                        "模型不支持自定义 temperature，已回退默认温度",
                        {
                            "attempt": attempt,
                            "max_attempts": MAX_COMPATIBILITY_ATTEMPTS,
                            **diagnostic_attributes(
                                exc,
                                reason_code="MODEL_REQUEST_FAILED",
                                stage="request",
                            ),
                        },
                    )
                    continue
                raise
            except BaseException as error:
                self._trace_local.request_diagnostic.update(_request_failure(error))
                _submit_model_call_metric(
                    trace_call,
                    request=getattr(self._trace_local, "request_diagnostic", {}),
                    settings=self.settings,
                    estimate=metric_estimate,
                    usage=None,
                    latency_ms=int((time.perf_counter() - metric_started_at) * 1000),
                    outcome="cancelled"
                    if isinstance(error, OperationCancelled)
                    else "failed",
                    error_code="REQUEST_CANCELLED"
                    if isinstance(error, OperationCancelled)
                    else "MODEL_REQUEST_FAILED",
                )
                raise
            else:
                _submit_model_call_metric(
                    trace_call,
                    request=getattr(self._trace_local, "request_diagnostic", {}),
                    settings=self.settings,
                    estimate=metric_estimate,
                    usage=_summarize_token_usage(response.get("usage")),
                    latency_ms=int((time.perf_counter() - metric_started_at) * 1000),
                    outcome="success",
                    error_code=None,
                )
                return response
        raise ApiRequestError("API 兼容性自动回退已达到最大次数。")

    def _ensure_chat_config(self, api_key_message: str) -> None:
        if not self.settings.api_key and not is_loopback_url(self.settings.base_url):
            raise ApiConfigError(api_key_message)
        if not self.settings.base_url:
            raise ApiConfigError("缺少 BASE_URL。")
        if not self.settings.model:
            raise ApiConfigError("缺少 MODEL。")

    def _ensure_model_list_config(self) -> None:
        if not self.settings.api_key and not is_loopback_url(self.settings.base_url):
            raise ApiConfigError("缺少 API_KEY。请在设置中填写 API Key。")
        if not self.settings.base_url:
            raise ApiConfigError("缺少 BASE_URL。")

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

    def _send_request(
        self,
        payload: dict[str, Any] | None,
        *,
        cancel_checker: CancelChecker | None = None,
    ) -> dict[str, Any]:
        """发送一次请求；网络错误直接交给调用者，不隐式重放生成。"""
        check_cancelled(cancel_checker)
        if payload is not None and payload.get("stream"):
            raise ApiConfigError("当前模型接口仅支持完整回复。")
        check_cancelled(cancel_checker)
        settings = self.settings
        base_url = _normalize_openai_base_url(settings.base_url)
        endpoint = f"{base_url}/{'models' if payload is None else 'chat/completions'}"
        if self._trace_local.sdk is None:
            self._trace_local.sdk = AsyncOpenAI(
                api_key=settings.api_key or "local-endpoint",
                base_url=base_url,
                # 配置由 Sakura 管理，不从环境变量继承账号或项目。
                organization="",
                project="",
                timeout=settings.timeout_seconds,
                max_retries=0,
                default_headers={
                    "User-Agent": f"Sakura/{self._app_version}",
                },
                http_client=httpx.AsyncClient(
                    proxy=proxy_for_url(base_url),
                    verify=ssl.create_default_context(),
                    trust_env=False,
                    timeout=settings.timeout_seconds,
                    follow_redirects=False,
                ),
            )
        self._trace_local.attempt += 1
        attempt = self._trace_local.attempt
        self._trace_local.request_diagnostic = {
            **getattr(self._trace_local, "request_diagnostic", {}),
            "attemptCount": attempt,
        }
        started_at = time.perf_counter()
        attributes = {
            **_model_call_log_attributes(self.last_trace_call),
            "attempt": attempt,
            "endpoint_host": urlparse(endpoint).netloc,
            "model": settings.model,
        }
        try:
            response = self._trace_local.runner.run(
                self._request_async(self._trace_local.sdk, payload, cancel_checker)
            )
            self._trace_local.request_diagnostic["httpStatus"] = response.status_code
            log_event(
                "API", "HTTP 请求成功",
                {
                    **attributes,
                    "status": response.status_code,
                    "elapsed_ms": int((time.perf_counter() - started_at) * 1000),
                    "response_body": response.text,
                },
                event="api.request.finished", verbosity=1,
            )
            try:
                data = response.json()
            except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                raise ApiRequestError("API 返回格式无法解析。") from exc
            if not isinstance(data, dict):
                self._trace_local.request_diagnostic.update(
                    faultDomain="protocol", reasonCode="MODEL_RESPONSE_INVALID", stage="decode",
                )
                raise ApiRequestError("API 返回的消息结构无效。")
            return data
        except (APIStatusError, APIConnectionError) as exc:
            self._trace_local.request_diagnostic.update(_request_failure(exc))
            if isinstance(exc, APIStatusError):
                error_body = exc.response.text
                if any(marker in error_body.lower() for marker in (
                    "context_length_exceeded", "maximum context length", "context window",
                )):
                    self._trace_local.request_diagnostic.update(
                        faultDomain="context", reasonCode="MODEL_CONTEXT_REJECTED", stage="request",
                    )
                message = _format_api_http_error(
                    exc.status_code, error_body, endpoint, settings.api_key,
                )
            elif isinstance(exc, APITimeoutError):
                message = "API 请求超时。"
            else:
                message = "API 请求失败：" + _safe_diagnostic_text(
                    str(exc.__cause__ or exc), settings.api_key,
                )
            log_event(
                "API", "HTTP 请求失败",
                {
                    **attributes,
                    "status": getattr(exc, "status_code", None),
                    "elapsed_ms": int((time.perf_counter() - started_at) * 1000),
                    "retryable": False,
                    "error_type": type(exc).__name__,
                    "diagnostic": message,
                },
                event="api.request.failed", severity="warning", verbosity=0,
            )
            raise ApiRequestError(message) from exc

    async def _request_async(self, sdk, payload, cancel_checker):
        from openai import Omit

        headers = {"Authorization": Omit()} if not self.settings.api_key else {}
        async def send():
            check_cancelled(cancel_checker)
            if payload is None:
                response = await sdk.models.with_raw_response.list(extra_headers=headers)
            else:
                chat: AsyncChat = sdk.chat
                response = await chat.completions.with_raw_response.create(
                    model=payload["model"],
                    messages=payload["messages"],
                    extra_headers=headers,
                    extra_body={key: value for key, value in payload.items() if key not in {"model", "messages"}},
                )
            return response.http_response

        task = asyncio.create_task(send(), name="sakura-model-request")
        try:
            while not task.done():
                await asyncio.wait({task}, timeout=0.05)
                check_cancelled(cancel_checker)
            return await task
        finally:
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)



def _build_segmented_reply_instruction(
    reply_tones: list[str] | None,
    reply_visual: Mapping[str, Any] | None = None,
) -> str:
    return build_segmented_reply_instruction(reply_tones, reply_visual)


def _parse_model_ids(data: dict[str, Any]) -> list[str]:
    """解析 /models 响应中的模型 id，过滤坏数据并稳定排序。"""
    raw_models = data.get("data")
    if not isinstance(raw_models, list):
        raise ApiRequestError(f"API 模型列表格式无法解析：{json.dumps(data, ensure_ascii=False)}")

    model_ids: set[str] = set()
    for item in raw_models:
        if not isinstance(item, dict):
            continue
        model_id = item.get("id")
        if isinstance(model_id, str) and model_id.strip():
            model_ids.add(model_id.strip())
    return sorted(model_ids, key=str.casefold)



def _format_api_http_error(
    status_code: int,
    error_body: str,
    url: str,
    api_key: str = "",
) -> str:
    safe_error_body = error_body.replace(api_key, "[REDACTED]") if api_key else error_body
    if _looks_like_google_ai_studio_auth_error(safe_error_body, url):
        return (
            f"API HTTP {status_code}: Google AI Studio 认证失败。"
            "请确认填写的是 AI Studio API Key，并使用 Google Generative Language 的 OpenAI 兼容接口；"
            "Sakura 会把 https://generativelanguage.googleapis.com/v1beta 自动转换为 "
            "https://generativelanguage.googleapis.com/v1beta/openai。"
            f"\n原始响应：{safe_error_body}"
        )
    return f"API HTTP {status_code}: {safe_error_body}"


def _provider_error_diagnostic(error_body: str, api_key: str) -> dict[str, str]:
    """Extract a bounded, credential-free Provider error summary for support logs."""

    error_type = ""
    error_code = ""
    message = ""
    try:
        parsed = json.loads(error_body)
    except (json.JSONDecodeError, TypeError):
        parsed = None
    candidate = parsed.get("error") if isinstance(parsed, dict) else None
    if isinstance(candidate, dict):
        error_type = str(candidate.get("type") or "")
        error_code = str(candidate.get("code") or "")
        message = str(candidate.get("message") or "")
    elif isinstance(candidate, str):
        message = candidate
    if not message:
        message = str(error_body or "")
    output: dict[str, str] = {}
    for key, value in (("provider_error_type", error_type), ("provider_error_code", error_code)):
        safe = re.sub(r"[^A-Za-z0-9._:-]+", "_", value).strip("_")[:96]
        if safe:
            output[key] = safe
    diagnostic = _safe_diagnostic_text(message, api_key)
    if diagnostic:
        output["diagnostic"] = diagnostic
    return output


def _safe_diagnostic_text(value: str, api_key: str = "") -> str:
    from sakura_assistant.diagnostics import safe_diagnostic_text
    return safe_diagnostic_text(value, 4096, secrets=(api_key,))


def _looks_like_google_ai_studio_auth_error(error_body: str, url: str) -> bool:
    parsed = urlparse(url)
    if parsed.netloc.lower() != "generativelanguage.googleapis.com":
        return False
    text = error_body.lower()
    return (
        "api_key_service_blocked" in text
        or "unauthenticated" in text
        or "invalid authentication credentials" in text
        or "modelservice.listmodels" in text
    )



def _has_tool_messages(messages: list[ChatMessage]) -> bool:
    return any(msg.get("role") == "tool" for msg in messages)


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
    """Classify types/status only; never project provider text or URLs."""
    chain = error
    seen: set[int] = set()
    while chain.__cause__ is not None and id(chain) not in seen:
        if isinstance(chain, (APIStatusError, httpx.TimeoutException, httpx.RequestError)):
            break
        seen.add(id(chain))
        chain = chain.__cause__
    if isinstance(error, OperationCancelled):
        return {
            "faultDomain": "cancelled",
            "reasonCode": "REQUEST_CANCELLED",
            "stage": "request",
        }
    if isinstance(chain, APIStatusError):
        status = chain.status_code
        domain = (
            "authentication"
            if status in {401, 403}
            else "rate_limit"
            if status == 429
            else "provider"
        )
        code = (
            "MODEL_AUTHENTICATION_FAILED"
            if status in {401, 403}
            else "MODEL_RATE_LIMITED"
            if status == 429
            else "MODEL_HTTP_FAILED"
        )
        return {
            "faultDomain": domain,
            "reasonCode": code,
            "httpStatus": status,
            "stage": "request",
        }
    if isinstance(chain, (json.JSONDecodeError, UnicodeDecodeError)):
        return {
            "faultDomain": "protocol",
            "reasonCode": "MODEL_RESPONSE_INVALID",
            "stage": "decode",
        }
    if isinstance(chain, (TimeoutError, httpx.TimeoutException, APITimeoutError)):
        stage = "read" if isinstance(chain, httpx.ReadTimeout) else "connect" if isinstance(chain, httpx.ConnectTimeout) else "request"
        return {
            "faultDomain": "transport",
            "reasonCode": "MODEL_READ_TIMEOUT"
            if stage == "read"
            else "MODEL_CONNECTION_TIMEOUT"
            if stage == "connect"
            else "MODEL_REQUEST_TIMEOUT",
            "stage": stage,
        }
    if isinstance(
        chain, (APIConnectionError, httpx.RequestError, OSError)
    ):
        return {
            "faultDomain": "transport",
            "reasonCode": "MODEL_CONNECTION_FAILED",
            "stage": "connect",
        }
    return {
        "faultDomain": "unknown",
        "reasonCode": "MODEL_REQUEST_FAILED",
        "stage": "unknown",
    }


def _submit_model_call_metric(
    call: TraceCall | None,
    *,
    settings: ApiSettings,
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
    settings: ApiSettings,
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


def _is_response_format_unsupported_error(exc: ApiRequestError) -> bool:
    text = str(exc).lower()
    return "response_format" in text or "json_object" in text or "json schema" in text


def _is_temperature_unsupported_error(exc: ApiRequestError) -> bool:
    text = str(exc).lower()
    if "temperature" not in text:
        return False
    # 值域错误（如「temperature 必须在 0~2 之间」）属于用户填错配置，应原样抛出，
    # 不能误判成「模型不支持自定义温度」而静默剥参、悄悄忽略用户设置。
    range_markers = (
        "between",
        "range",
        "minimum",
        "maximum",
        "less than",
        "greater than",
        "<=",
        ">=",
    )
    if any(marker in text for marker in range_markers):
        return False
    # 不同供应商对「仅支持默认温度」的措辞各异，尽量覆盖以便自动回退。
    markers = (
        "unsupported",
        "not support",
        "does not support",
        "only support",
        "only the default",
        "default value",
        "only accept",
        "not allowed",
        "can only be",
        "must be",
        "cannot be changed",
        "cannot be modified",
        "cannot be set",
        "is fixed",
        "not configurable",
        "cannot be configured",
        "invalid",
    )
    return any(marker in text for marker in markers)


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
