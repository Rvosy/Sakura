"""Shared model configuration and errors, independent from the default Assistant."""
from __future__ import annotations

from dataclasses import dataclass, field
import asyncio
import json
import ssl
from urllib.parse import urlparse, urlunparse

if __package__:
    from .sakura_cancellation import CancelChecker, check_cancelled
    from .sakura_http import is_loopback_url, proxy_for_url
else:
    from sakura_cancellation import CancelChecker, check_cancelled
    from sakura_http import is_loopback_url, proxy_for_url

class ApiConfigError(RuntimeError):
    """API 配置缺失或格式错误。"""


class ApiRequestError(RuntimeError):
    """API 请求失败。"""


@dataclass(frozen=True)
class ApiSettings:
    base_url: str
    api_key: str = field(repr=False)
    model: str
    timeout_seconds: int = 60
    # 角色对话生成参数；None 表示沿用内置默认/不发送该参数，保持历史行为。
    temperature: float | None = None  # None → 角色对话用内置默认 0.8
    top_p: float | None = None  # None → 不发送 top_p
    max_tokens: int | None = None  # None → 不发送 max_tokens（不截断输出）
    context_window_tokens: int = 32_768
    context_window_source: str = "fallback"


def normalize_openai_base_url(base_url: str) -> str:
    normalized = base_url.strip().rstrip("/")
    parsed = urlparse(normalized)
    if parsed.netloc.lower() == "generativelanguage.googleapis.com":
        parts = [part for part in parsed.path.split("/") if part]
        if parts in ([], ["v1"], ["v1beta"], ["v1", "openai"], ["v1beta", "openai"]):
            return urlunparse(parsed._replace(path="/v1beta/openai")).rstrip("/")
    return normalized


class ModelProbe:
    """One explicit settings probe; owns and closes its request and connection."""

    def __init__(self, settings: ApiSettings, *, app_version: str | None = None) -> None:
        self.settings = settings
        self.app_version = (app_version or "dev").strip().removeprefix("v")

    def list_models(self, *, cancel_checker: CancelChecker | None = None) -> list[str]:
        data = self._request(chat=False, cancel_checker=cancel_checker)
        models = data.get("data")
        if not isinstance(models, list):
            raise ApiRequestError("API 模型列表格式无法解析。")
        return sorted({item["id"].strip() for item in models
                       if isinstance(item, dict) and isinstance(item.get("id"), str)
                       and item["id"].strip()}, key=str.casefold)

    def test_connection(self, *, cancel_checker: CancelChecker | None = None) -> str:
        data = self._request(chat=True, cancel_checker=cancel_checker)
        try:
            content = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as error:
            raise ApiRequestError("API 返回格式无法解析。") from error
        return str(content).strip() or "OK"

    def _request(self, *, chat: bool, cancel_checker: CancelChecker | None) -> dict:
        check_cancelled(cancel_checker)
        if not self.settings.base_url:
            raise ApiConfigError("缺少 BASE_URL。")
        if not self.settings.api_key and not is_loopback_url(self.settings.base_url):
            raise ApiConfigError("缺少 API_KEY。请在设置中填写 API Key。")
        if chat and not self.settings.model:
            raise ApiConfigError("缺少 MODEL。")
        import httpx
        from openai import APIConnectionError, APIStatusError, APITimeoutError, AsyncOpenAI, Omit

        async def run():
            base_url = normalize_openai_base_url(self.settings.base_url)
            async with httpx.AsyncClient(
                proxy=proxy_for_url(base_url), verify=ssl.create_default_context(),
                trust_env=False, timeout=self.settings.timeout_seconds, follow_redirects=False,
            ) as http_client:
                async with AsyncOpenAI(
                    api_key=self.settings.api_key or "local-endpoint", base_url=base_url,
                    organization="", project="", timeout=self.settings.timeout_seconds,
                    max_retries=0, default_headers={"User-Agent": f"Sakura/{self.app_version}"},
                    http_client=http_client,
                ) as sdk:
                    headers = {} if self.settings.api_key else {"Authorization": Omit()}

                    async def send():
                        check_cancelled(cancel_checker)
                        if chat:
                            return await sdk.chat.completions.with_raw_response.create(
                                model=self.settings.model,
                                messages=[{"role": "user", "content": "Reply with only OK."}],
                                extra_headers=headers,
                            )
                        return await sdk.models.with_raw_response.list(extra_headers=headers)

                    task = asyncio.create_task(send(), name="sakura-model-probe")
                    try:
                        while not task.done():
                            await asyncio.wait({task}, timeout=0.05)
                            check_cancelled(cancel_checker)
                        response = (await task).http_response
                        check_cancelled(cancel_checker)
                        try:
                            data = response.json()
                        except (json.JSONDecodeError, UnicodeDecodeError) as error:
                            raise ApiRequestError("API 返回格式无法解析。") from error
                        if not isinstance(data, dict):
                            raise ApiRequestError("API 返回的消息结构无效。")
                        return data
                    finally:
                        if not task.done():
                            task.cancel()
                        await asyncio.gather(task, return_exceptions=True)

        try:
            with asyncio.Runner() as runner:
                return runner.run(run())
        except APIStatusError as error:
            body = error.response.text
            if self.settings.api_key:
                body = body.replace(self.settings.api_key, "[REDACTED]")
            raise ApiRequestError(f"API HTTP {error.status_code}: {body}") from error
        except APITimeoutError as error:
            raise ApiRequestError("API 请求超时。") from error
        except APIConnectionError as error:
            raise ApiRequestError("模型服务连接失败。") from error
