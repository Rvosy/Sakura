from __future__ import annotations

import http.client
import json
import ssl
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable
from urllib.parse import urlparse, urlunparse

from app.llm.chat_reply import ChatReply, parse_chat_reply, sanitize_reply_tones
from app.core.cancellation import CancelChecker, cancellable_sleep, check_cancelled
from app.core.debug_log import debug_log, summarize_messages
from app.llm.prompt_templates import build_segmented_reply_instruction


MAX_API_RETRY_ATTEMPTS = 3
API_RETRY_DELAY_SECONDS = 0.8
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


class ApiConfigError(RuntimeError):
    """API 配置缺失或格式错误。"""


class ApiRequestError(RuntimeError):
    """API 请求失败。"""


@dataclass(frozen=True)
class ApiSettings:
    base_url: str
    api_key: str
    model: str
    timeout_seconds: int = 60
    # 角色对话生成参数；None 表示沿用内置默认/不发送该参数，保持历史行为。
    temperature: float | None = None  # None → 角色对话用内置默认 0.8
    top_p: float | None = None  # None → 不发送 top_p
    max_tokens: int | None = None  # None → 不发送 max_tokens（不截断输出）


@dataclass(frozen=True)
class NativeToolCall:
    """OpenAI 原生 tool_call，保留 id 以便后续 tool role 回填。"""

    id: str
    name: str
    arguments: dict[str, Any]
    arguments_json: str = "{}"


@dataclass(frozen=True)
class ChatCompletionTurn:
    """一次 Chat Completions 返回的 assistant 消息。"""

    content: str
    tool_calls: list[NativeToolCall]
    message: dict[str, Any]
    runtime_context_role: str = "system"


class OpenAICompatibleClient:
    def __init__(self, settings: ApiSettings) -> None:
        self.settings = settings
        self._unsupported_chat_params: set[str] = set()
        self._runtime_context_role = "system"
        # 可选事件发射器（由宿主注入），用于派发 llm.request.* 插件事件。
        self._event_emit: Callable[[str, dict[str, Any] | None], None] | None = None

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
        self.settings = settings
        self._unsupported_chat_params.clear()
        self._runtime_context_role = "system"
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

    def test_connection(self) -> str:
        """发送一次最小聊天请求，验证 Base URL、API Key 和模型是否可用。"""
        self._ensure_chat_config("缺少 API_KEY。请在设置中填写 API Key。")

        # 连通性检测只需验证 Base URL / API Key / 模型可用，不发送 temperature：
        # 部分模型（如 o1/o3/gpt-5 等推理模型）只接受默认温度，显式传值会直接报错。
        payload = {
            "model": self.settings.model,
            "messages": [
                {
                    "role": "user",
                    "content": "Reply with only OK.",
                },
            ],
            "max_tokens": 8,
        }
        data = self._post_chat_completions_with_compatibility_fallbacks(payload)

        try:
            content = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise ApiRequestError(f"API 返回格式无法解析：{json.dumps(data, ensure_ascii=False)}") from exc

        return str(content).strip() or "OK"

    def list_models(self) -> list[str]:
        """读取 OpenAI 兼容 /models 接口，返回可选择的模型 id 列表。"""
        self._ensure_model_list_config()
        base_url = _normalize_openai_base_url(self.settings.base_url)
        url = f"{base_url}/models"
        request = urllib.request.Request(
            url=url,
            method="GET",
            headers={
                "Authorization": f"Bearer {self.settings.api_key}",
            },
        )
        debug_log(
            "API",
            "准备检测模型列表",
            {
                "url": url,
                "configured_base_url": self.settings.base_url,
                "timeout_seconds": self.settings.timeout_seconds,
            },
        )
        response_body = self._send_with_retries(request)

        try:
            data: dict[str, Any] = json.loads(response_body)
        except json.JSONDecodeError as exc:
            raise ApiRequestError(f"API 返回格式无法解析：{response_body}") from exc

        return _parse_model_ids(data)

    def chat(
        self,
        system_prompt: str,
        messages: list[ChatMessage],
        reply_tones: list[str] | None = None,
        reply_portraits: list[str] | None = None,
        *,
        cancel_checker: CancelChecker | None = None,
        runtime_context: str = "",
    ) -> ChatReply:
        segmented_reply_instruction = _build_segmented_reply_instruction(reply_tones, reply_portraits)
        temperature, extra_params = self.resolve_dialogue_params()
        content = self.complete_raw(
            f"{system_prompt.strip()}\n\n{segmented_reply_instruction}",
            messages,
            temperature=temperature,
            response_format=STRUCTURED_JSON_RESPONSE_FORMAT,
            cancel_checker=cancel_checker,
            runtime_context=runtime_context,
            **extra_params,
        )
        check_cancelled(cancel_checker)

        reply = sanitize_reply_tones(parse_chat_reply(content), reply_tones)
        debug_log(
            "API",
            "聊天回复解析完成",
            {
                "segments": len(reply.segments),
                "tone": reply.tone,
                "portraits": [segment.portrait for segment in reply.segments],
                "reply": reply.text,
            },
        )
        return reply

    def complete_raw(
        self,
        system_prompt: str,
        messages: list[ChatMessage],
        temperature: float = 0.8,
        *,
        cancel_checker: CancelChecker | None = None,
        runtime_context: str = "",
        **chat_params: Any,
    ) -> str:
        """返回模型原始文本，供 Agent Runtime 解析工具调用 JSON。"""
        self._ensure_chat_config("缺少 API Key。请在 data/config/api.yaml 中配置 llm.api_key。")
        check_cancelled(cancel_checker)
        runtime_context_role = self._runtime_context_role
        payload = _build_chat_completion_payload(
            model=self.settings.model,
            system_prompt=system_prompt,
            messages=_messages_with_runtime_context(
                messages, runtime_context, runtime_context_role
            ),
            temperature=temperature,
            chat_params=chat_params,
        )
        debug_log(
            "API",
            "准备发送聊天补全请求",
            {
                "base_url": _normalize_openai_base_url(self.settings.base_url),
                "configured_base_url": self.settings.base_url,
                "model": self.settings.model,
                "timeout_seconds": self.settings.timeout_seconds,
                "temperature": temperature,
                "message_count": len(payload["messages"]),
                "has_image": messages_contain_image(payload["messages"]),
                "messages": summarize_messages(payload["messages"]),
                "chat_params": _filter_supported_chat_params(chat_params),
            },
        )
        try:
            data = self._post_chat_completions_with_compatibility_fallbacks(
                payload,
                cancel_checker=cancel_checker,
            )
        except ApiRequestError as exc:
            if (
                runtime_context.strip()
                and runtime_context_role == "system"
                and _is_runtime_context_role_unsupported_error(exc)
            ):
                self._runtime_context_role = "user"
                payload = _build_chat_completion_payload(
                    model=self.settings.model,
                    system_prompt=system_prompt,
                    messages=_messages_with_runtime_context(messages, runtime_context, "user"),
                    temperature=temperature,
                    chat_params=chat_params,
                )
                debug_log(
                    "API",
                    "端点不支持尾部 system 上下文，已回退为 user 上下文",
                    {"error": str(exc)},
                )
                data = self._post_chat_completions_with_compatibility_fallbacks(
                    payload, cancel_checker=cancel_checker
                )
            else:
                raise
        check_cancelled(cancel_checker)

        try:
            content = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise ApiRequestError(f"API 返回格式无法解析：{json.dumps(data, ensure_ascii=False)}") from exc

        reasoning = data["choices"][0]["message"].get("reasoning_content", "")
        content = (str(reasoning) + "\n" + str(content)).strip()
        result = str(content).strip()
        debug_log("API", "模型原始文本返回", {"content": result})
        return result

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
        cancel_checker: CancelChecker | None = None,
        **chat_params: Any,
    ) -> ChatCompletionTurn:
        """调用 OpenAI 原生 tools/tool_calls 协议并返回 assistant 消息。"""
        self._ensure_chat_config("缺少 API Key。请在 data/config/api.yaml 中配置 llm.api_key。")
        check_cancelled(cancel_checker)

        if tools:
            chat_params["tools"] = tools
            chat_params["tool_choice"] = tool_choice
        if structured_response and "response_format" not in chat_params:
            chat_params["response_format"] = STRUCTURED_JSON_RESPONSE_FORMAT
        runtime_context_role = self._runtime_context_role
        request_messages = _messages_with_runtime_context(
            messages, runtime_context, runtime_context_role
        )
        payload = _build_chat_completion_payload(
            model=self.settings.model,
            system_prompt=system_prompt,
            messages=request_messages,
            temperature=temperature,
            chat_params=chat_params,
        )
        debug_log(
            "API",
            "准备发送原生工具聊天补全请求",
            {
                "base_url": _normalize_openai_base_url(self.settings.base_url),
                "configured_base_url": self.settings.base_url,
                "model": self.settings.model,
                "timeout_seconds": self.settings.timeout_seconds,
                "temperature": temperature,
                "message_count": len(payload["messages"]),
                "tool_count": len(tools or []),
                "has_image": messages_contain_image(payload["messages"]),
                "messages": summarize_messages(payload["messages"]),
                "chat_params": _filter_supported_chat_params(chat_params),
            },
        )
        try:
            data = self._post_chat_completions_with_compatibility_fallbacks(
                payload,
                cancel_checker=cancel_checker,
            )
        except ApiRequestError as exc:
            if (
                runtime_context.strip()
                and runtime_context_role == "system"
                and _is_runtime_context_role_unsupported_error(exc)
            ):
                self._runtime_context_role = "user"
                runtime_context_role = "user"
                payload = _build_chat_completion_payload(
                    model=self.settings.model,
                    system_prompt=system_prompt,
                    messages=_messages_with_runtime_context(messages, runtime_context, "user"),
                    temperature=temperature,
                    chat_params=chat_params,
                )
                debug_log(
                    "API",
                    "端点不支持尾部 system 上下文，已回退为 user 上下文",
                    {"error": str(exc)},
                )
                data = self._post_chat_completions_with_compatibility_fallbacks(
                    payload, cancel_checker=cancel_checker
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
        tool_calls = _parse_native_tool_calls(raw_message.get("tool_calls"))
        if not tool_calls:
            tool_calls = _parse_pseudo_tool_calls_from_content(content)
        normalized_message = _normalize_assistant_message(raw_message, content, tool_calls)
        debug_log(
            "API",
            "原生工具模型返回",
            {
                "content": str(content or "").strip(),
                "tool_calls": [
                    {"id": call.id, "name": call.name, "arguments": call.arguments}
                    for call in tool_calls
                ],
            },
        )
        return ChatCompletionTurn(
            content=str(content or "").strip(),
            tool_calls=tool_calls,
            message=normalized_message,
            runtime_context_role=runtime_context_role,
        )

    def _post_chat_completions_with_compatibility_fallbacks(
        self,
        payload: dict[str, Any],
        *,
        cancel_checker: CancelChecker | None = None,
    ) -> dict[str, Any]:
        fallback_payload = dict(payload)
        for param in self._unsupported_chat_params:
            fallback_payload.pop(param, None)
        while True:
            check_cancelled(cancel_checker)
            try:
                return self._post_chat_completions(
                    fallback_payload,
                    cancel_checker=cancel_checker,
                )
            except ApiRequestError as exc:
                if "response_format" in fallback_payload and _is_response_format_unsupported_error(exc):
                    self._unsupported_chat_params.add("response_format")
                    fallback_payload.pop("response_format", None)
                    debug_log(
                        "API",
                        "结构化 response_format 不受支持，已回退普通请求",
                        {"error": str(exc)},
                    )
                    continue
                if "temperature" in fallback_payload and _is_temperature_unsupported_error(exc):
                    self._unsupported_chat_params.add("temperature")
                    fallback_payload.pop("temperature", None)
                    debug_log(
                        "API",
                        "模型不支持自定义 temperature，已回退默认温度",
                        {"error": str(exc)},
                    )
                    continue
                raise

    def _ensure_chat_config(self, api_key_message: str) -> None:
        if not self.settings.api_key:
            raise ApiConfigError(api_key_message)
        if not self.settings.base_url:
            raise ApiConfigError("缺少 BASE_URL。")
        if not self.settings.model:
            raise ApiConfigError("缺少 MODEL。")

    def _ensure_model_list_config(self) -> None:
        if not self.settings.api_key:
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
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        base_url = _normalize_openai_base_url(self.settings.base_url)
        url = f"{base_url}/chat/completions"
        request = urllib.request.Request(
            url=url,
            data=body,
            method="POST",
            headers={
                "Authorization": f"Bearer {self.settings.api_key}",
                "Content-Type": "application/json",
            },
        )

        debug_log(
            "API",
            "HTTP 请求体已构建",
            {
                "url": url,
                "configured_base_url": self.settings.base_url,
                "bytes": len(body),
                "payload": payload,
            },
        )
        model_name = payload.get("model")
        self._emit_llm_event("llm.request.started", {"model": model_name})
        try:
            response_body = self._send_with_retries(request, cancel_checker=cancel_checker)
            check_cancelled(cancel_checker)
            try:
                data: dict[str, Any] = json.loads(response_body)
            except json.JSONDecodeError as exc:
                raise ApiRequestError(f"API 返回格式无法解析：{response_body}") from exc
        except Exception as exc:  # noqa: BLE001 — 仅用于派发失败事件，随后原样抛出
            self._emit_llm_event(
                "llm.request.failed",
                {"model": model_name, "error": str(exc)},
            )
            raise

        self._emit_llm_event("llm.request.finished", {"model": model_name})
        return data

    def _send_with_retries(
        self,
        request: urllib.request.Request,
        *,
        cancel_checker: CancelChecker | None = None,
    ) -> str:
        last_error: BaseException | None = None
        for attempt in range(1, MAX_API_RETRY_ATTEMPTS + 1):
            check_cancelled(cancel_checker)
            started_at = time.perf_counter()
            try:
                with urllib.request.urlopen(
                    request,
                    timeout=self.settings.timeout_seconds,
                ) as response:
                    response_body = response.read().decode("utf-8")
                    debug_log(
                        "API",
                        "HTTP 请求成功",
                        {
                            "attempt": attempt,
                            "status": getattr(response, "status", None),
                            "elapsed_ms": int((time.perf_counter() - started_at) * 1000),
                            "response_body": response_body,
                        },
                    )
                    return response_body
            except urllib.error.HTTPError as exc:
                error_body = exc.read().decode("utf-8", errors="replace")
                debug_log(
                    "API",
                    "HTTP 请求失败",
                    {
                        "attempt": attempt,
                        "status": exc.code,
                        "elapsed_ms": int((time.perf_counter() - started_at) * 1000),
                        "error_body": error_body,
                    },
                )
                if exc.code not in {429, 500, 502, 503, 504} or attempt == MAX_API_RETRY_ATTEMPTS:
                    raise ApiRequestError(_format_api_http_error(exc.code, error_body, request.full_url)) from exc
                last_error = exc
            except urllib.error.URLError as exc:
                debug_log(
                    "API",
                    "URL 请求失败",
                    {
                        "attempt": attempt,
                        "elapsed_ms": int((time.perf_counter() - started_at) * 1000),
                        "reason": str(exc.reason),
                    },
                )
                if attempt == MAX_API_RETRY_ATTEMPTS:
                    raise ApiRequestError(f"API 请求失败：{exc.reason}") from exc
                last_error = exc
            except TimeoutError as exc:
                debug_log(
                    "API",
                    "请求超时",
                    {
                        "attempt": attempt,
                        "elapsed_ms": int((time.perf_counter() - started_at) * 1000),
                    },
                )
                if attempt == MAX_API_RETRY_ATTEMPTS:
                    raise ApiRequestError("API 请求超时。") from exc
                last_error = exc
            except (ssl.SSLError, ConnectionError, http.client.RemoteDisconnected) as exc:
                debug_log(
                    "API",
                    "连接中断",
                    {
                        "attempt": attempt,
                        "elapsed_ms": int((time.perf_counter() - started_at) * 1000),
                        "error": str(exc),
                    },
                )
                if attempt == MAX_API_RETRY_ATTEMPTS:
                    raise ApiRequestError(f"API 连接中断：{exc}") from exc
                last_error = exc

            debug_log(
                "API",
                "准备重试请求",
                {
                    "attempt": attempt,
                    "max_attempts": MAX_API_RETRY_ATTEMPTS,
                    "delay_seconds": API_RETRY_DELAY_SECONDS * attempt,
                    "last_error": str(last_error),
                },
            )
            cancellable_sleep(API_RETRY_DELAY_SECONDS * attempt, cancel_checker)

        raise ApiRequestError("API 请求失败。")


def _build_segmented_reply_instruction(
    reply_tones: list[str] | None,
    reply_portraits: list[str] | None = None,
) -> str:
    return build_segmented_reply_instruction(reply_tones, reply_portraits)


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


def _normalize_openai_base_url(base_url: str) -> str:
    """把 Google AI Studio 原生地址规范到 OpenAI 兼容路径。"""

    normalized = base_url.strip().rstrip("/")
    parsed = urlparse(normalized)
    if parsed.netloc.lower() != "generativelanguage.googleapis.com":
        return normalized
    parts = [part for part in parsed.path.split("/") if part]
    if parts and parts[0] in {"v1", "v1beta"} and "openai" not in parts:
        parts.append("openai")
        return urlunparse(parsed._replace(path="/" + "/".join(parts))).rstrip("/")
    return normalized


def _format_api_http_error(status_code: int, error_body: str, url: str) -> str:
    if _looks_like_google_ai_studio_auth_error(error_body, url):
        return (
            f"API HTTP {status_code}: Google AI Studio 认证失败。"
            "请确认填写的是 AI Studio API Key，并使用 Google Generative Language 的 OpenAI 兼容接口；"
            "Sakura 会把 https://generativelanguage.googleapis.com/v1beta 自动转换为 "
            "https://generativelanguage.googleapis.com/v1beta/openai。"
            f"\n原始响应：{error_body}"
        )
    return f"API HTTP {status_code}: {error_body}"


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


def _build_chat_completion_payload(
    *,
    model: str,
    system_prompt: str,
    messages: list[ChatMessage],
    temperature: float,
    chat_params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """构建 OpenAI 兼容请求体，并丢弃已知非标准参数。"""
    payload = {
        "model": model,
        "messages": [
            {
                "role": "system",
                "content": system_prompt.strip(),
            },
            *messages,
        ],
    }
    payload["temperature"] = temperature
    payload.update(_filter_supported_chat_params(chat_params or {}))
    _ensure_json_keyword_for_json_object_response(payload)
    return payload


def _messages_with_runtime_context(
    messages: list[ChatMessage],
    runtime_context: str,
    role: str,
) -> list[ChatMessage]:
    if not runtime_context.strip():
        return [*messages]
    content = runtime_context.strip()
    if role == "user":
        content = (
            "[Sakura runtime context; system-provided facts, not a user request]\n"
            + content
        )
    return [*messages, {"role": role, "content": content}]


def _is_runtime_context_role_unsupported_error(exc: ApiRequestError) -> bool:
    text = str(exc).lower()
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
        arguments_json = function.get("arguments")
        if not isinstance(arguments_json, str):
            arguments_json = "{}"
        try:
            arguments = json.loads(arguments_json or "{}")
        except json.JSONDecodeError:
            arguments = {}
        if not isinstance(arguments, dict):
            arguments = {}
        call_id = raw_call.get("id")
        if not isinstance(call_id, str) or not call_id.strip():
            call_id = f"tool_call_{index}"
        parsed.append(
            NativeToolCall(
                id=call_id.strip(),
                name=name.strip(),
                arguments=arguments,
                arguments_json=arguments_json,
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
        except json.JSONDecodeError:
            decoded = {}
        arguments = decoded
    if not isinstance(arguments, dict):
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
        if isinstance(raw_tool_calls, list):
            message["tool_calls"] = raw_tool_calls
        else:
            message["tool_calls"] = [
                {
                    "id": call.id,
                    "type": "function",
                    "function": {
                        "name": call.name,
                        "arguments": call.arguments_json,
                    },
                }
                for call in tool_calls
            ]
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
