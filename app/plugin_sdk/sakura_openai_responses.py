"""OpenAI Responses 消息转换和流式响应，供模型服务插件共用。"""
from __future__ import annotations

from copy import deepcopy
import json
import time

from openai import APIConnectionError, APIError, APIStatusError, APITimeoutError, AsyncOpenAI

from sakura_model import ModelError
from sakura_provider_errors import public_provider_http_message, sanitize_provider_diagnostic


def _content(content, *, assistant=False):
    if content is None or content == "":
        return []
    if isinstance(content, str):
        content = [{"type": "text", "text": content}]
    result = []
    for part in content:
        if part["type"] in {"text", "input_text", "output_text"}:
            result.append({"type": "output_text", "text": part["text"], "annotations": []} if assistant
                          else {"type": "input_text", "text": part["text"]})
        elif part["type"] == "image":
            result.append({"type": "input_image", "image_url": part["dataUrl"], "detail": part.get("detail", "auto")})
        elif part["type"] == "image_url":
            image = part["image_url"]
            result.append({"type": "input_image", "image_url": image["url"], "detail": image.get("detail", "auto")})
        else:
            result.append(deepcopy(part))
    return result


def build_request(model, request):
    inputs, instructions = [], []
    for index, message in enumerate(request["messages"]):
        role = message["role"]
        if role in {"system", "developer"} and not inputs:
            parts = _content(message.get("content"))
            if all(part["type"] == "input_text" for part in parts):
                instructions.extend(part["text"] for part in parts)
                continue
        if role == "tool":
            content = message.get("content")
            inputs.append({"type": "function_call_output", "call_id": message["toolCallId"],
                           "output": content if isinstance(content, str) else _content(content)})
            continue
        metadata = message.get("providerData") or {}
        if role == "assistant" and metadata.get("responsesOutput"):
            # 保留推理签名、消息阶段及工具调用的原始关联。
            inputs.extend(deepcopy(metadata["responsesOutput"]))
            continue
        content = _content(message.get("content"), assistant=role == "assistant")
        if content:
            item = {"type": "message", "role": "developer" if role == "system" else role, "content": content}
            if role == "assistant":
                item.update(id=f"msg_sakura_{index}", status="completed")
            inputs.append(item)
        for call in message.get("toolCalls") or []:
            item = {"type": "function_call", "call_id": call["id"], "name": call["name"], "arguments": call["arguments"]}
            inputs.append(item)
    payload = {"model": model, "instructions": "\n\n".join(instructions), "input": inputs,
               "store": False, "include": ["reasoning.encrypted_content"]}
    parameters = request.get("parameters") or {}
    for name in ("temperature", "top_p", "reasoning"):
        if parameters.get(name) is not None:
            payload[name] = deepcopy(parameters[name])
    limit = parameters.get("max_completion_tokens", parameters.get("max_tokens"))
    if limit is not None:
        payload["max_output_tokens"] = limit
    if request.get("tools"):
        payload["tools"] = [{"type": "function", **deepcopy(tool), "strict": False} for tool in request["tools"]]
    if request.get("toolChoice") is not None:
        choice = deepcopy(request["toolChoice"])
        if isinstance(choice, dict) and choice.get("type") == "function":
            choice = {"type": "function", "name": choice["function"]["name"]}
        payload["tool_choice"] = choice
    if request.get("responseFormat"):
        format = deepcopy(request["responseFormat"])
        if format.get("type") == "json_schema":
            format = {"type": "json_schema", **format["json_schema"]}
        payload["text"] = {"format": format}
    return payload


def response_result(response):
    if response.get("error") or response.get("status") in {"failed", "cancelled"}:
        body = {"error": response.get("error") or {"status": response["status"]}}
        message = public_provider_http_message(RuntimeError("API HTTP 200: " + json.dumps(body, ensure_ascii=False)), 200)
        raise ModelError("MODEL_REQUEST_FAILED", message)
    content, calls, refusals = [], [], []
    for item in response["output"]:
        if item["type"] == "message":
            for part in item.get("content", []):
                if part["type"] == "output_text":
                    content.append(part["text"])
                elif part["type"] == "refusal":
                    refusals.append(part["refusal"])
        elif item["type"] == "function_call":
            calls.append({"id": item["call_id"], "name": item["name"], "arguments": item["arguments"]})
    raw_usage = response.get("usage") or {}
    usage = {target: deepcopy(raw_usage[source]) for source, target in (
        ("input_tokens", "prompt_tokens"), ("output_tokens", "completion_tokens"), ("total_tokens", "total_tokens"),
        ("input_tokens_details", "prompt_tokens_details"), ("output_tokens_details", "completion_tokens_details"),
    ) if source in raw_usage}
    reason = (response.get("incomplete_details") or {}).get("reason")
    finish = "length" if reason == "max_output_tokens" else "content_filter" if reason == "content_filter" else "tool_calls" if calls else "stop"
    return {"message": {"role": "assistant", "content": "".join(content), "toolCalls": calls,
                        "providerData": {"responsesOutput": deepcopy(response["output"]), **({"refusal": "\n".join(refusals)} if refusals else {})}},
        "usage": usage, "finishReason": finish}


async def complete(http, settings, payload, *, headers=None, progress=lambda _event: None):
    """HTTP 与 SSE 解码交给 OpenAI SDK；供应商只提供连接和请求差异。"""
    key = settings["api_key"]
    try:
        async with AsyncOpenAI(api_key=key, base_url=settings["base_url"], organization="", project="",
                               max_retries=0, timeout=settings["timeout_seconds"], http_client=http) as sdk:
            stream = await sdk.responses.create(**payload, stream=True, extra_headers=headers)
            terminal = None
            pending = ""
            last_emit = time.monotonic()
            async with stream:
                async for event in stream:
                    if event.type == "response.output_text.delta":
                        pending += event.delta
                        while len(pending) >= 2048:
                            progress({"type": "text_delta", "text": pending[:2048]})
                            pending = pending[2048:]
                        if pending and time.monotonic() - last_emit >= .05:
                            progress({"type": "text_delta", "text": pending})
                            pending = ""
                            last_emit = time.monotonic()
                    elif event.type in {"response.completed", "response.incomplete", "response.failed", "response.done"}:
                        terminal = event.response if isinstance(event.response, dict) else event.response.model_dump(exclude_none=True)
                        break
                    elif event.type == "error":
                        raise ModelError("MODEL_REQUEST_FAILED", event.message)
            if terminal is None:
                raise ModelError("MODEL_RESPONSE_INCOMPLETE", "模型连接已结束，但没有收到完成事件。")
            result = response_result(terminal)
            if pending:
                progress({"type": "text_delta", "text": pending})
            return result
    except APIStatusError as error:
        status = error.status_code
        message = public_provider_http_message(RuntimeError(f"API HTTP {status}: {error.response.text}"), status, secrets=(key,))
        code = "MODEL_AUTHENTICATION_FAILED" if status in {401, 403} else "MODEL_RATE_LIMITED" if status == 429 else "MODEL_HTTP_FAILED"
        failure = ModelError(code, message, diagnostics={"httpStatus": status, "stage": "request"})
        failure.diagnostic_secrets = (key,)
        raise failure from error
    except (APIConnectionError, APITimeoutError, APIError) as error:
        code = "MODEL_REQUEST_TIMEOUT" if isinstance(error, APITimeoutError) else "MODEL_CONNECTION_FAILED" if isinstance(error, APIConnectionError) else "MODEL_REQUEST_FAILED"
        failure = ModelError(code, sanitize_provider_diagnostic(str(error.__cause__ or error), secrets=(key,)))
        failure.diagnostic_secrets = (key,)
        raise failure from error
