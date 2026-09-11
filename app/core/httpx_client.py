"""MCP HTTP transport with request-scoped proxy selection and stream ownership."""
from __future__ import annotations

import anyio
import httpx

from app.plugin_sdk.sakura_http import proxy_for_url


class _OwnedStream(httpx.AsyncByteStream):
    def __init__(self, stream: httpx.AsyncByteStream, transport: httpx.AsyncHTTPTransport):
        self._stream = stream
        self._transport = transport

    async def __aiter__(self):
        async for chunk in self._stream:
            yield chunk

    async def aclose(self):
        with anyio.CancelScope(shield=True):
            try:
                await self._stream.aclose()
            finally:
                await self._transport.aclose()


class CurrentProxyTransport(httpx.AsyncBaseTransport):
    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        transport = httpx.AsyncHTTPTransport(
            proxy=proxy_for_url(str(request.url)),
        )
        try:
            response = await transport.handle_async_request(request)
        except BaseException:
            with anyio.CancelScope(shield=True):
                await transport.aclose()
            raise
        response.stream = _OwnedStream(response.stream, transport)
        return response


def create_mcp_http_client(headers=None, timeout=None, auth=None):
    options = {"headers": headers, "auth": auth}
    if timeout is not None:
        options["timeout"] = timeout
    return httpx.AsyncClient(
        transport=CurrentProxyTransport(), trust_env=False, follow_redirects=True, **options,
    )
