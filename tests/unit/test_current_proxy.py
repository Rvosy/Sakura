from __future__ import annotations

import asyncio
import socket
import threading
import urllib.request
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from app.plugin_sdk import sakura_http


@contextmanager
def endpoint(label, on_request=None):
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            requests.append((self.path, self.headers.get("Host")))
            location = on_request(self) if on_request else None
            body = label.encode()
            self.send_response(302 if location else 200)
            if location:
                self.send_header("Location", location)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_CONNECT(self):
            requests.append((self.path, self.headers.get("Host")))
            self.send_error(502)

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", requests
    finally:
        server.shutdown()
        server.server_close()
        thread.join(2)


@pytest.fixture
def proxy_state(monkeypatch):
    state = {}
    monkeypatch.setattr(urllib.request, "getproxies", lambda: dict(state))
    monkeypatch.setattr(urllib.request, "proxy_bypass", lambda _host: False)
    original = socket.getaddrinfo

    def resolve(host, *args, **kwargs):
        return original("127.0.0.1" if host in {"proxy-test.invalid", b"proxy-test.invalid"} else host, *args, **kwargs)

    monkeypatch.setattr(socket, "getaddrinfo", resolve)
    return state


def test_reused_request_switches_direct_proxy_a_proxy_b_direct(proxy_state):
    with endpoint("direct") as (direct, _), endpoint("a") as (a, _), endpoint("b") as (b, _):
        url = direct.replace("127.0.0.1", "proxy-test.invalid") + "/download"
        request = urllib.request.Request(url)
        for proxy, expected in [(None, b"direct"), (a, b"a"), (b, b"b"), (None, b"direct")]:
            proxy_state.clear()
            if proxy:
                proxy_state["http"] = proxy
            with sakura_http.urlopen_direct_for_loopback(request, timeout=2) as response:
                assert response.read() == expected
            assert request.host == urllib.request.Request(url).host
            assert not request.has_header("Proxy-Authorization")


def test_loopback_stays_direct_when_proxy_is_enabled(proxy_state):
    with endpoint("local") as (local, _), endpoint("proxy") as (proxy, requests):
        proxy_state["http"] = proxy
        with sakura_http.urlopen_direct_for_loopback(local, timeout=2) as response:
            assert response.read() == b"local"
        assert requests == []


def test_existing_download_keeps_its_response_after_proxy_change(proxy_state):
    with endpoint("a") as (a, _), endpoint("b") as (b, _):
        proxy_state["http"] = a
        with sakura_http.urlopen_direct_for_loopback("http://proxy-test.invalid/file", timeout=2) as first:
            proxy_state["http"] = b
            with sakura_http.urlopen_direct_for_loopback("http://proxy-test.invalid/file", timeout=2) as second:
                assert second.read() == b"b"
            assert first.read() == b"a"


def test_redirect_refreshes_proxy_and_drops_previous_proxy_credentials(proxy_state):
    authorization = []
    with endpoint("direct") as (direct, direct_requests):
        target = direct.replace("127.0.0.1", "proxy-test.invalid")

        def redirect_to_direct(request):
            authorization.append(request.headers.get("Proxy-Authorization"))
            proxy_state.clear()
            return target + "/done"

        with endpoint("b", redirect_to_direct) as (b, requests_b):
            def redirect_to_b(request):
                authorization.append(request.headers.get("Proxy-Authorization"))
                proxy_state["http"] = b
                return target + "/next"

            with endpoint("a", redirect_to_b) as (a, requests_a):
                proxy_state["http"] = a.replace("http://", "http://user:password@")
                with sakura_http.urlopen_direct_for_loopback(target + "/start", timeout=2) as response:
                    assert response.read() == b"direct"
                assert len(requests_a) == len(requests_b) == len(direct_requests) == 1
                assert authorization == ["Basic dXNlcjpwYXNzd29yZA==", None]


def test_dependency_download_job_uses_current_proxy(proxy_state, tmp_path):
    from app.plugins.dependencies import PluginDependencyRoots

    roots = PluginDependencyRoots(tmp_path)
    for proxy in ["http://127.0.0.1:1234", "http://127.0.0.1:5678"]:
        proxy_state["https"] = proxy
        environment = roots._uv_environment()
        assert environment["HTTPS_PROXY"] == environment["https_proxy"] == proxy


def test_mcp_client_refreshes_proxy_per_request_with_open_stream(proxy_state):
    from app.core.httpx_client import create_mcp_http_client

    async def run(direct, a, b):
        url = direct.replace("127.0.0.1", "proxy-test.invalid")
        async with create_mcp_http_client(timeout=2) as client:
            proxy_state["http"] = a
            async with client.stream("GET", url) as first:
                proxy_state["http"] = b
                assert (await client.get(url)).text == "b"
                proxy_state.clear()
                assert (await client.get(url)).text == "direct"
                assert await first.aread() == b"a"

    with endpoint("direct") as (direct, _), endpoint("a") as (a, _), endpoint("b") as (b, _):
        asyncio.run(run(direct, a, b))


def test_search_proxy_pins_public_ip_and_preserves_host(proxy_state, monkeypatch):
    from plugins.builtin.sakura_web import web

    monkeypatch.setattr(web, "_resolve_public_addresses", lambda *_: ["93.184.216.34"])
    with endpoint("a") as (a, requests_a), endpoint("b") as (b, requests_b):
        for proxy, expected in [(a, b"a"), (b, b"b")]:
            proxy_state["http"] = proxy
            assert web._request_public_url_once("http://public.example/path", 20)[3] == expected
        assert requests_a == requests_b == [("http://93.184.216.34/path", "public.example")]
        proxy_state["https"] = b
        with pytest.raises(RuntimeError):
            web._request_public_url_once("https://public.example/path", 20)
        assert requests_b[-1][0] == "93.184.216.34:443"


def test_search_rejects_private_destination_even_with_proxy(proxy_state):
    from plugins.builtin.sakura_web import web

    with endpoint("proxy") as (proxy, requests):
        proxy_state["http"] = proxy
        with pytest.raises(ValueError):
            web._request_public_url_once("http://127.0.0.1/private", 20)
        assert requests == []
