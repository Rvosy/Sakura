"""模型输出的语法修复与结束状态；业务结构由调用方的 Pydantic 模型负责。"""
from __future__ import annotations

import json

from json_repair import repair_json

from sakura_model import ModelError


def load_json_object(content: str) -> tuple[dict, bool]:
    try:
        value = json.loads(content)
        repaired = False
    except json.JSONDecodeError:
        value = repair_json(content, return_objects=True, skip_json_loads=True)
        repaired = True
    if not isinstance(value, dict):
        raise ValueError("模型输出必须是 JSON 对象。")
    return value, repaired


def response_format(name: str, schema: dict, *, strict: bool = True) -> dict:
    def closed(value):
        if isinstance(value, list):
            return [closed(item) for item in value]
        if not isinstance(value, dict):
            return value
        result = {key: closed(item) for key, item in value.items() if key != "default"}
        if "properties" in result:
            result.update(required=list(result["properties"]), additionalProperties=False)
        return result
    return {
        "type": "json_schema",
        "json_schema": {"name": name, "strict": strict, "schema": closed(schema) if strict else schema},
    }


def require_complete_response(response: dict) -> None:
    """截断和拒绝不能通过补齐 JSON 当成成功结果。"""
    finish = response.get("finishReason")
    usage = response.get("usage") or {}
    diagnostic = {
        "finish_reason": finish, "completion_tokens": usage.get("completion_tokens"), "stage": "response",
    }
    if finish == "length":
        raise ModelError("MODEL_OUTPUT_TRUNCATED", "模型输出达到长度上限，结果不完整。请增加最大输出或缩小本次输入。", diagnostics=diagnostic)
    metadata = response["message"].get("providerData") or {}
    refusal = response["message"].get("refusal") or metadata.get("refusal")
    if refusal or finish == "content_filter":
        raise ModelError("MODEL_OUTPUT_REFUSED", str(refusal or "模型服务拦截了本次输出。"), diagnostics=diagnostic)


def validation_message(error) -> str:
    return "; ".join(
        f"{'.'.join(str(part) for part in item['loc']) or '$'}: {item['msg']}"
        for item in error.errors(include_url=False, include_input=False)
    )
