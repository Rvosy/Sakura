"""In-process provider transport for Assistant policy tests using mocked HTTP."""
import httpx

from plugins.builtin.sakura_model_openai_compatible.transport import execute


class LocalModelClient:
    def complete(self, request, *, cancel_checker=None, usage_category=None):
        return execute({"base_url": "https://api.example.com/v1", "api_key": "key", "model": "model", "timeout_seconds": 5},
                       request, cancel_checker=cancel_checker, progress=lambda _event: None)

    def close(self):
        pass


def mock_http(monkeypatch, handler):
    """仅替换 HTTP 传输，保留 SDK 和 Sakura 的请求、响应处理。"""
    class Client(httpx.AsyncClient):
        def __init__(self, **kwargs):
            kwargs.pop("proxy", None)
            super().__init__(**kwargs, transport=httpx.MockTransport(handler))
    monkeypatch.setattr(httpx, "AsyncClient", Client)
