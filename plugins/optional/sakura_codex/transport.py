"""Codex 订阅的地址、认证头和参数差异。"""
from __future__ import annotations

import asyncio
import time

from sakura_cancellation import await_cancellable
from sakura_model import ModelError
from sakura_openai_responses import build_request, complete

if __package__:
    from .oauth import checked_json, http_client
else:
    from oauth import checked_json, http_client


BASE_URL = "https://chatgpt.com/backend-api/codex"
USAGE_URL = "https://chatgpt.com/backend-api/wham/usage"
CLIENT_VERSION = "0.160.1"


def catalog_models(data):
    models = []
    for item in data["models"]:
        if item.get("visibility") == "hide":
            continue
        model_id = item["slug"]
        model = {"modelId": model_id, "label": item.get("display_name") or model_id}
        if item.get("context_window"):
            model["contextWindowTokens"] = item["context_window"]
        if item.get("input_modalities") is not None:
            model["inputModalities"] = item["input_modalities"]
        models.append(model)
    return models


def account_usage(data):
    windows = []
    limits = [("", data.get("rate_limit"))]
    limits.extend((item.get("limit_name") or item.get("metered_feature") or "其他额度", item.get("rate_limit"))
                  for item in data.get("additional_rate_limits") or [])
    for label, limit in limits:
        for key in ("primary_window", "secondary_window"):
            window = (limit or {}).get(key)
            if window is not None:
                windows.append({"label": label, "usedPercent": window.get("used_percent"),
                                "windowSeconds": window.get("limit_window_seconds"), "resetsAt": window.get("reset_at")})
    return {"windows": windows, "updatedAt": time.time()}


def execute(account, settings, request, *, cancel_checker, progress, operation="generate"):
    snapshot = settings["account"]
    tokens = account.authorize(snapshot, cancel_checker)
    if operation == "test_connection":
        request = {"messages": [{"role": "user", "content": "Reply with only OK."}]}
    payload = None if operation in {"list_models", "usage"} else build_request(settings["model"], request)
    ignored = []
    if payload:
        for name in ("temperature", "top_p", "max_output_tokens", "text"):
            if name in payload:
                payload.pop(name)
                ignored.append(name)

    async def send():
        headers = {"Authorization": "Bearer " + tokens["access_token"], "chatgpt-account-id": tokens["account_id"],
                   "originator": "sakura", "version": CLIENT_VERSION, "OpenAI-Beta": "responses=experimental",
                   "Accept": "text/event-stream", "User-Agent": "Sakura/codex-subscription"}
        async with http_client(BASE_URL, settings["timeout_seconds"]) as http:
            if operation == "usage":
                response = await http.get(USAGE_URL, headers={**headers, "Accept": "application/json"})
                return {"usage": account_usage(checked_json(response, secrets=(tokens["access_token"],)))}
            if operation == "list_models":
                response = await http.get(BASE_URL + "/models", params={"client_version": CLIENT_VERSION}, headers=headers)
                return {"models": catalog_models(checked_json(response, secrets=(tokens["access_token"],)))}
            result = await complete(http, {**settings, "base_url": BASE_URL, "api_key": tokens["access_token"]},
                                    payload, headers=headers, progress=progress if request.get("stream") else lambda _event: None)
            result["diagnostics"] = {"ignoredParameters": ignored}
            return result

    for attempt in range(2):
        # 将本次实际使用的令牌留在任务快照中，错误清洗也使用同一份凭据。
        snapshot["tokens"] = tokens
        try:
            return asyncio.run(await_cancellable(send(), cancel_checker))
        except ModelError as error:
            if attempt or error.diagnostics.get("httpStatus") != 401:
                raise
            tokens = account.authorize(snapshot, cancel_checker, force=True)
