"""Provider-owned HTTP protocol and narrow, explicit parameter compatibility."""
from __future__ import annotations

import asyncio
import json
import ssl
from copy import deepcopy
from urllib.parse import urlparse, urlunparse

import httpx
from openai import APIConnectionError, APIStatusError, APITimeoutError, AsyncOpenAI, Omit
from openai.resources.chat import AsyncChat  # Resolve lazy imports before serving jobs.

from sakura_cancellation import check_cancelled
from sakura_http import proxy_for_url
from sakura_model import ModelError
from sakura_provider_errors import public_provider_http_message, sanitize_provider_diagnostic


def normalize_base_url(value):
    parsed = urlparse(value.strip().rstrip("/"))
    if parsed.netloc.lower() == "generativelanguage.googleapis.com":
        parts = [part for part in parsed.path.split("/") if part]
        if parts in ([], ["v1"], ["v1beta"], ["v1", "openai"], ["v1beta", "openai"]):
            parsed = parsed._replace(path="/v1beta/openai")
    return urlunparse(parsed).rstrip("/")


def _unsupported(error, parameter):
    if not isinstance(error, APIStatusError) or error.status_code not in {400, 422}:
        return False
    text = error.response.text.lower()
    if parameter not in text and not (parameter == "response_format" and "json_object" in text):
        return False
    if any(marker in text for marker in ("between", "range", "minimum", "maximum", "less than", "greater than", "<=", ">=")):
        return False
    return any(marker in text for marker in ("unsupported", "not support", "does not support", "only support", "only the default", "default value", "not allowed", "cannot be", "not configurable"))


def _message(value):
    if not isinstance(value, dict) or value.get("role") not in {"system", "developer", "user", "assistant", "tool"}:
        raise ModelError("MODEL_REQUEST_INVALID", "模型消息格式无效。")
    message = {**deepcopy(value.get("providerData") or {}), "role": value["role"], "content": value.get("content")}
    if isinstance(message["content"], list):
        message["content"] = [({"type": "image_url", "image_url": {"url": part["dataUrl"], "detail": part.get("detail", "auto")}}
                               if part.get("type") == "image" else deepcopy(part)) for part in message["content"]]
    if value.get("toolCalls"):
        message["tool_calls"] = [{**deepcopy(call.get("providerData") or {}), "id": call["id"], "type": "function", "function": {"name": call["name"], "arguments": call["arguments"]}} for call in value["toolCalls"]]
    if value.get("toolCallId"):
        message["tool_call_id"] = value["toolCallId"]
    if value.get("name"):
        message["name"] = value["name"]
    return message


def _invalid_response(message, field):
    return ModelError("MODEL_RESPONSE_INVALID", message,
                      diagnostics={"faultDomain": "protocol", "stage": "decode", "validation_field": field})


def _response_value(value, expected, path):
    if not isinstance(value, expected):
        raise _invalid_response(f"模型响应的 {path} 应为 {expected.__name__}，实际为 {type(value).__name__}。", path)
    return value


def _response_field(value, name, expected, path):
    if name not in value:
        raise _invalid_response(f"模型响应缺少 {path} 字段。", path)
    return _response_value(value[name], expected, path)


def _response(data):
    _response_value(data, dict, "$")
    choices = _response_field(data, "choices", list, "choices")
    if not choices:
        raise _invalid_response("模型响应的 choices 是空数组。", "choices")
    choice = _response_value(choices[0], dict, "choices[0]")
    message = _response_field(choice, "message", dict, "choices[0].message")
    calls = []
    raw_calls = message.get("tool_calls")
    if raw_calls is not None:
        _response_value(raw_calls, list, "choices[0].message.tool_calls")
        for index, item in enumerate(raw_calls):
            path = f"choices[0].message.tool_calls[{index}]"
            _response_value(item, dict, path)
            function = _response_field(item, "function", dict, path + ".function")
            calls.append({"id": _response_field(item, "id", str, path + ".id"),
                          "name": _response_field(function, "name", str, path + ".function.name"),
                          "arguments": _response_field(function, "arguments", str, path + ".function.arguments"),
                          "providerData": {key: value for key, value in item.items() if key not in {"id", "type", "function"}}})
    return {"message": {"role": "assistant", "content": message.get("content"), "toolCalls": calls,
                        "providerData": {key: value for key, value in message.items() if key not in {"role", "content", "tool_calls"}}},
            "usage": data.get("usage") or {}, "finishReason": choice.get("finish_reason")}


def _model_ids(data):
    _response_value(data, dict, "$")
    entries = _response_field(data, "data", list, "data")
    return sorted({item["id"].strip() for item in entries
                   if isinstance(item, dict) and isinstance(item.get("id"), str) and item["id"].strip()}, key=str.casefold)


def execute(settings, request, *, cancel_checker, progress, operation="generate"):
    settings, request = deepcopy(settings), deepcopy(request)
    base_url = normalize_base_url(settings["base_url"])
    key = settings["api_key"]
    if operation == "generate":
        if not isinstance(request, dict) or not isinstance(request.get("messages"), list) or not request["messages"]:
            raise ModelError("MODEL_REQUEST_INVALID", "模型请求缺少消息。")
        parameters = request.get("parameters") or {}
        allowed = {"temperature", "top_p", "max_tokens", "max_completion_tokens", "presence_penalty", "frequency_penalty", "seed", "stop"}
        if not isinstance(parameters, dict) or set(parameters) - allowed:
            raise ModelError("MODEL_REQUEST_INVALID", "模型生成参数无效。")
        payload = {**parameters, "model": settings["model"], "messages": [_message(item) for item in request["messages"]]}
        if request.get("responseFormat") is not None:
            payload["response_format"] = request["responseFormat"]
        if request.get("tools"):
            payload["tools"] = [{"type": "function", "function": item} for item in request["tools"]]
        if request.get("toolChoice") is not None:
            payload["tool_choice"] = request["toolChoice"]
    else:
        payload = {"model": settings["model"], "messages": [{"role": "user", "content": "Reply with only OK."}]}
    diagnostics = {}

    def decode_response(response):
        diagnostics["httpStatus"] = response.status_code
        try:
            data = response.json()
        except (json.JSONDecodeError, UnicodeDecodeError) as error:
            content_type = sanitize_provider_diagnostic(response.headers.get("content-type", "未提供"), secrets=(key,))
            detail = sanitize_provider_diagnostic(str(error), secrets=(key,))
            raise ModelError("MODEL_RESPONSE_INVALID", f"模型响应无法解码为 JSON（Content-Type: {content_type}）：{detail}",
                             diagnostics={"faultDomain": "protocol", "stage": "decode"}) from error
        if isinstance(data, dict) and data.get("error") is not None:
            upstream = data["error"]
            # Only error fields may leave the Provider; never include echoed
            # requests, conversation content or arbitrary response metadata.
            if isinstance(upstream, dict):
                public = {field: upstream[field] for field in ("message", "code", "type", "status", "param", "request_id")
                          if isinstance(upstream.get(field), (str, int, float)) and not isinstance(upstream[field], bool)}
            elif isinstance(upstream, str):
                public = {"message": upstream}
            else:
                public = {}
            if not public:
                public = {"message": "模型服务返回 error 字段，但未提供错误说明。"}
            status = response.status_code
            message = public_provider_http_message(RuntimeError(f"API HTTP {status}: " + json.dumps({"error": public})), status, secrets=(key,))
            raise ModelError("MODEL_REQUEST_FAILED", message, diagnostics={"faultDomain": "provider", "stage": "response"})
        return data

    async def run():
        async with httpx.AsyncClient(proxy=proxy_for_url(base_url), verify=ssl.create_default_context(), trust_env=False,
                                     timeout=settings["timeout_seconds"], follow_redirects=False) as http:
            async with AsyncOpenAI(api_key=key or "local-endpoint", base_url=base_url, organization="", project="", max_retries=0,
                                   timeout=settings["timeout_seconds"], default_headers={"User-Agent": "Sakura/model-provider"}, http_client=http) as sdk:
                headers = {} if key else {"Authorization": Omit()}
                async def send():
                    if operation == "list_models":
                        response = await sdk.models.with_raw_response.list(extra_headers=headers)
                        return decode_response(response.http_response)
                    # Streaming is optional. The complete reply remains authoritative;
                    # batched deltas never enter the Assistant's segmented reply policy.
                    if request.get("stream"):
                        stream = await sdk.chat.completions.create(**payload, stream=True, stream_options={"include_usage": True}, extra_headers=headers)
                        diagnostics["httpStatus"] = stream.response.status_code
                        content, calls, usage, finish = [], {}, {}, None
                        metadata = {}
                        pending, last_emit = "", asyncio.get_running_loop().time()
                        async with stream:
                            async for chunk in stream:
                                check_cancelled(cancel_checker)
                                if chunk.usage:
                                    usage = chunk.usage.model_dump(exclude_none=True)
                                for choice in chunk.choices:
                                    if choice.index != 0:
                                        continue
                                    finish = choice.finish_reason or finish
                                    delta = choice.delta
                                    # Reasoning/refusal are text deltas; opaque signatures
                                    # and other metadata are snapshots, not token fragments.
                                    for name, value in delta.model_dump(exclude_none=True, exclude={"role", "content", "tool_calls"}).items():
                                        if name in {"reasoning_content", "reasoning", "refusal"} and isinstance(value, str):
                                            metadata[name] = metadata.get(name, "") + value
                                        else:
                                            metadata[name] = deepcopy(value)
                                    if delta.content:
                                        content.append(delta.content)
                                        pending += delta.content
                                    for item in delta.tool_calls or []:
                                        call = calls.setdefault(item.index, {"id": "", "type": "function", "function": {"name": "", "arguments": ""}})
                                        call.update(deepcopy(item.model_extra or {}))
                                        call["id"] += item.id or ""
                                        if item.function:
                                            call["function"]["name"] += item.function.name or ""
                                            call["function"]["arguments"] += item.function.arguments or ""
                                now = asyncio.get_running_loop().time()
                                while len(pending) >= 2048:
                                    progress({"type": "text_delta", "text": pending[:2048]})
                                    pending = pending[2048:]
                                    last_emit = now
                                if pending and now - last_emit >= .05:
                                    progress({"type": "text_delta", "text": pending})
                                    pending = ""
                                    last_emit = now
                        if pending:
                            progress({"type": "text_delta", "text": pending})
                        return {"choices": [{"message": {**metadata, "content": "".join(content), "tool_calls": [calls[index] for index in sorted(calls)]}, "finish_reason": finish}], "usage": usage}
                    response = await sdk.chat.completions.with_raw_response.create(**payload, extra_headers=headers)
                    return decode_response(response.http_response)

                async def guarded():
                    task = asyncio.create_task(send())
                    try:
                        while not task.done():
                            await asyncio.wait({task}, timeout=.05)
                            check_cancelled(cancel_checker)
                        return await task
                    finally:
                        if not task.done():
                            task.cancel()
                        await asyncio.gather(task, return_exceptions=True)
                fallbacks = []
                for attempt in range(1, 4):
                    check_cancelled(cancel_checker)
                    diagnostics["attemptCount"] = attempt
                    try:
                        data = await guarded()
                        if operation == "list_models":
                            return {"models": _model_ids(data)}
                        result = _response(data)
                        result["diagnostics"] = {**diagnostics, "compatibilityFallbacks": fallbacks}
                        return result
                    except APIStatusError as error:
                        parameter = next((name for name in ("response_format", "temperature") if name in payload and _unsupported(error, name)), None)
                        if parameter is None or request.get("stream") or attempt == 3:
                            raise
                        payload.pop(parameter)
                        fallbacks.append(parameter)
                raise AssertionError("compatibility attempts exhausted")
    try:
        with asyncio.Runner() as runner:
            return runner.run(run())
    except ModelError as error:
        error.diagnostics = {**diagnostics, **error.diagnostics}
        error.status_code = error.diagnostics.get("httpStatus")
        raise
    except APIStatusError as error:
        status = error.status_code
        body = error.response.text
        message = public_provider_http_message(
            RuntimeError(f"API HTTP {status}: {body}"), status, secrets=(key,),
        )
        domain = "authentication" if status in {401, 403} else "rate_limit" if status == 429 else "provider"
        code = "MODEL_AUTHENTICATION_FAILED" if status in {401, 403} else "MODEL_RATE_LIMITED" if status == 429 else "MODEL_HTTP_FAILED"
        if any(marker in body.lower() for marker in ("context_length_exceeded", "maximum context length", "context window")):
            domain, code = "context", "MODEL_CONTEXT_REJECTED"
        failure = ModelError(code, message, diagnostics={**diagnostics, "httpStatus": status, "faultDomain": domain, "stage": "request"})
        failure.diagnostic_secrets = (key,)
        raise failure from error
    except APITimeoutError as error:
        stage = "read" if isinstance(error.__cause__, httpx.ReadTimeout) else "connect" if isinstance(error.__cause__, httpx.ConnectTimeout) else "request"
        code = {"read": "MODEL_READ_TIMEOUT", "connect": "MODEL_CONNECTION_TIMEOUT", "request": "MODEL_REQUEST_TIMEOUT"}[stage]
        detail = sanitize_provider_diagnostic(str(error.__cause__ or error) or type(error.__cause__ or error).__name__, secrets=(key,))
        failure = ModelError(code, detail, diagnostics={**diagnostics, "faultDomain": "transport", "stage": stage})
        failure.diagnostic_secrets = (key,)
        raise failure from error
    except APIConnectionError as error:
        detail = sanitize_provider_diagnostic(str(error.__cause__ or error) or type(error.__cause__ or error).__name__, secrets=(key,))
        failure = ModelError("MODEL_CONNECTION_FAILED", detail, diagnostics={**diagnostics, "faultDomain": "transport", "stage": "connect"})
        failure.diagnostic_secrets = (key,)
        raise failure from error
