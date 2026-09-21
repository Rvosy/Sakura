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
from sakura_http import is_loopback_url, proxy_for_url
from sakura_model import ModelError
from sakura_provider_errors import public_provider_http_message


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


def _response(data):
    try:
        choice = data["choices"][0]
        message = choice["message"]
        if not isinstance(message, dict):
            raise TypeError()
        calls = [{"id": item["id"], "name": item["function"]["name"], "arguments": item["function"]["arguments"],
                  "providerData": {key: value for key, value in item.items() if key not in {"id", "type", "function"}}}
                 for item in message.get("tool_calls") or []]
    except (KeyError, IndexError, TypeError) as error:
        raise ModelError("MODEL_RESPONSE_INVALID", "模型返回的消息结构无效。") from error
    return {"message": {"role": "assistant", "content": message.get("content"), "toolCalls": calls,
                        "providerData": {key: value for key, value in message.items() if key not in {"role", "content", "tool_calls"}}},
            "usage": data.get("usage") or {}, "finishReason": choice.get("finish_reason")}


def _model_ids(data):
    if not isinstance(data, dict) or not isinstance(data.get("data"), list):
        raise ModelError("MODEL_RESPONSE_INVALID", "模型列表格式无法解析。")
    return sorted({item["id"].strip() for item in data["data"]
                   if isinstance(item, dict) and isinstance(item.get("id"), str) and item["id"].strip()}, key=str.casefold)


def execute(settings, request, *, cancel_checker, progress, operation="generate"):
    settings, request = deepcopy(settings), deepcopy(request)
    base_url = normalize_base_url(settings["base_url"])
    key = settings["api_key"]
    if not key and not is_loopback_url(base_url):
        raise ModelError("MODEL_CONFIGURATION_INVALID", "请先配置 API Key。")
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

    async def run():
        async with httpx.AsyncClient(proxy=proxy_for_url(base_url), verify=ssl.create_default_context(), trust_env=False,
                                     timeout=settings["timeout_seconds"], follow_redirects=False) as http:
            async with AsyncOpenAI(api_key=key or "local-endpoint", base_url=base_url, organization="", project="", max_retries=0,
                                   timeout=settings["timeout_seconds"], default_headers={"User-Agent": "Sakura/model-provider"}, http_client=http) as sdk:
                headers = {} if key else {"Authorization": Omit()}
                async def send():
                    if operation == "list_models":
                        response = await sdk.models.with_raw_response.list(extra_headers=headers)
                        return response.http_response.json()
                    # Streaming is optional. The complete reply remains authoritative;
                    # batched deltas never enter the Assistant's segmented reply policy.
                    if request.get("stream"):
                        stream = await sdk.chat.completions.create(**payload, stream=True, stream_options={"include_usage": True}, extra_headers=headers)
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
                    return response.http_response.json()

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
                        result["diagnostics"] = {"attemptCount": attempt, "compatibilityFallbacks": fallbacks, "httpStatus": 200}
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
        raise ModelError(code, message, diagnostics={**diagnostics, "httpStatus": status, "faultDomain": domain, "stage": "request"}) from None
    except APITimeoutError as error:
        stage = "read" if isinstance(error.__cause__, httpx.ReadTimeout) else "connect" if isinstance(error.__cause__, httpx.ConnectTimeout) else "request"
        code = {"read": "MODEL_READ_TIMEOUT", "connect": "MODEL_CONNECTION_TIMEOUT", "request": "MODEL_REQUEST_TIMEOUT"}[stage]
        raise ModelError(code, "模型请求超时。", diagnostics={**diagnostics, "faultDomain": "transport", "stage": stage}) from None
    except APIConnectionError:
        raise ModelError("MODEL_CONNECTION_FAILED", "模型服务连接失败。", diagnostics={**diagnostics, "faultDomain": "transport", "stage": "connect"}) from None
    except (json.JSONDecodeError, UnicodeDecodeError, KeyError, TypeError) as error:
        raise ModelError("MODEL_RESPONSE_INVALID", "模型返回格式无法解析。") from error
