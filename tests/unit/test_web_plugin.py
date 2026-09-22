from __future__ import annotations

import json
from importlib.metadata import distribution
import shutil
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from packaging.requirements import Requirement

from app.plugin_sdk.sakura_tools import Tool, ToolRegistry
from app.core_host.plugin_application import PluginApplicationHost
from app.plugins.inventory import PluginDesiredStateStore
from app.storage.runtime_roots import RuntimeRoots
from plugins.builtin.sakura_web import web

SOURCE = Path(__file__).parents[2] / "plugins/builtin/sakura_web"
NAMES = {"web__web_search", "web__fetch_url"}


def roots(tmp_path: Path, *, fixture_network: bool = True) -> RuntimeRoots:
    distribution = tmp_path / "distribution"
    plugin = distribution / "plugins/builtin/sakura_web"
    shutil.copytree(SOURCE, plugin)
    # Most cases replace network I/O. Supply the distribution marker without
    # installing packages; the proxy regression below supplies the real client.
    dependency = distribution / "plugins/dependencies/sakura.web"
    dependency.mkdir(parents=True)
    (dependency / ".sakura-dependencies.json").write_text(json.dumps({
        "schemaVersion": 1, "kind": "requirements.txt",
        "python": f"{sys.version_info.major}.{sys.version_info.minor}",
    }), encoding="utf-8")
    user = tmp_path / "user"
    user.mkdir()
    if fixture_network:
        with (plugin / "web.py").open("a", encoding="utf-8") as stream:
            stream.write(NETWORK_FIXTURE)
    return RuntimeRoots(distribution, user)


NETWORK_FIXTURE = r'''

def _resolve_public_addresses(host, port):
    if host == "private.example":
        raise ValueError("不允许读取私有网络地址。")
    return ["93.184.215.14"]

def _request_public_url_once(url, max_bytes):
    from urllib.parse import parse_qs, urlparse
    parsed = urlparse(url)
    if parsed.path == "/slow":
        from pathlib import Path
        import time
        Path("entered").write_text("ready")
        time.sleep(60)
    if parsed.path == "/timeout":
        raise TimeoutError("fixture")
    if parsed.path == "/redirect":
        return 302, "Found", {"Location": "http://127.0.0.1/secret"}, b""
    if parsed.path == "/forbidden":
        return 403, "Forbidden", {}, b""
    headers = {"Content-Type": "text/html; charset=utf-8"}
    if parsed.path == "/search":
        query = parse_qs(parsed.query)["q"][0]
        if query == "captcha":
            body = "<html>verify yourself</html>"
        elif query == "empty":
            body = '<li class="b_no">没有结果</li>'
        else:
            body = '<li class="b_algo"><a href="https://bad.example/">附加链接</a><h2><a href="https://example.com/news">中文搜索结果</a></h2><p>结果摘要</p></li>'
    elif parsed.path == "/large":
        body = '<html><script>' + 'x' * 300000 + '</script><p>正文</p></html>'
    else:
        body = '<title>网页标题</title><p>' + '正文内容' * 1000 + '</p><a href="/next">下一页</a>'
    return 200, "OK", headers, body.encode("utf-8")[:max_bytes]
'''


def test_proxy_fetch_in_isolated_worker(tmp_path: Path, monkeypatch) -> None:
    runtime_roots = roots(tmp_path, fixture_network=False)
    dependency = runtime_roots.distribution_root / "plugins/dependencies/sakura.web"
    # Copy only the client's packages into a private distribution root. The
    # Worker must never gain access to the test process's site-packages.
    pending = [Requirement("httpx[socks]")]
    copied = set()
    while pending:
        requirement = pending.pop()
        key = (requirement.name, frozenset(requirement.extras))
        if key in copied:
            continue
        copied.add(key)
        package = distribution(requirement.name)
        for relative in package.files or ():
            if ".." in relative.parts or "__pycache__" in relative.parts:
                continue
            target = dependency / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(package.locate_file(relative), target)
        for declaration in package.requires or ():
            required = Requirement(declaration)
            if required.marker is None or any(
                required.marker.evaluate({"extra": extra})
                for extra in {"", *requirement.extras}
            ):
                pending.append(required)
    plugin = runtime_roots.distribution_root / "plugins/builtin/sakura_web"
    with (plugin / "web.py").open("a", encoding="utf-8") as stream:
        # Destination DNS must belong to the proxy, even in the isolated worker.
        # Request selection, proxy transport and tool RPC use production code.
        stream.write('\ndef _resolve_public_addresses(host, port):\n    raise AssertionError("Proxy destination must not use local DNS")\n')
    requests = []

    class Proxy(BaseHTTPRequestHandler):
        def do_GET(self):
            requests.append((self.path, self.headers.get("Host")))
            body = b"<title>Proxy fixture</title><p>isolated worker response</p>"
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Proxy)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    proxy = f"http://127.0.0.1:{server.server_port}"
    for key in ("HTTP_PROXY", "http_proxy", "HTTPS_PROXY", "https_proxy", "ALL_PROXY", "all_proxy"):
        monkeypatch.setenv(key, proxy)
    for key in ("NO_PROXY", "no_proxy"):
        monkeypatch.setenv(key, "")
    registry = ToolRegistry()
    application = PluginApplicationHost(runtime_roots, "web-proxy", registry)
    thread.start()
    try:
        application.start()
        fetched = registry.execute("web__fetch_url", {"url": "http://public.example/article"})
        assert fetched.success, fetched
        assert fetched.content["title"] == "Proxy fixture"
        assert "isolated worker response" in fetched.content["text"]
        assert requests == [("http://public.example/article", "public.example")]
        process = application._manager._records["sakura.web"].process._process
        assert process.args[1:3] == ["-I", "-S"]
        assert str(dependency) in process.args
    finally:
        application.close()
        server.shutdown()
        server.server_close()
        thread.join(5)


def test_real_plugin_calls_and_disable(tmp_path: Path) -> None:
    runtime_roots = roots(tmp_path)
    registry = ToolRegistry()
    application = PluginApplicationHost(runtime_roots, "web-fixture", registry)
    try:
        application.start()
        assert {tool.name for tool in registry.all()} == NAMES
        assert all(tool.source == "plugin" for tool in registry.all())
        record = application.settings_snapshot()["plugins"][0]
        assert record["state"] == "active"
        assert record["sections"][0]["sectionId"] == "search"
        application.settings_save("sakura.web", "search", {"provider": "bing", "tavily_api_key": "", "tavily_depth": "basic"})
        result = registry.execute("web__web_search", {"query": "中文新闻"})
        assert result.success, result
        assert result.content["results"] == [{"title": "中文搜索结果", "url": "https://example.com/news", "snippet": "结果摘要"}]
        fetched = registry.execute("web__fetch_url", {"url": "https://example.com/article", "max_chars": 500})
        assert fetched.success, fetched
        assert len(fetched.content["text"]) == 500
        assert fetched.content["truncated"] is True
        assert fetched.content["links"] == [{"text": "下一页", "url": "https://example.com/next"}]
        for url, code in (("https://example.com/timeout", "WEB_TIMEOUT"), ("https://example.com/forbidden", "WEB_HTTP_ERROR"), ("https://example.com/redirect", "WEB_INVALID_REQUEST"), ("http://127.0.0.1/", "WEB_INVALID_REQUEST")):
            failed = registry.execute("web__fetch_url", {"url": url})
            assert not failed.success
            assert failed.reason_code == code
        assert registry.execute("web__web_search", {"query": "captcha"}).reason_code == "WEB_SEARCH_RESPONSE_INVALID"
        assert registry.execute("web__web_search", {"query": "empty"}).content["results"] == []
        large = registry.execute("web__fetch_url", {"url": "https://example.com/large"})
        assert large.success and large.content["truncated"] is True
        assert application.public_snapshot()["plugins"][0]["state"] == "active"
        application.set_enabled(record["installId"], False)
        assert registry.all() == []
    finally:
        application.close()
    assert not (runtime_roots.user_root / "config/mcp.yaml").exists()


def test_disable_cancels_pending_worker_and_withdraws_tools(tmp_path: Path) -> None:
    runtime_roots = roots(tmp_path)
    registry = ToolRegistry()
    application = PluginApplicationHost(runtime_roots, "web-cancel", registry)
    result = []
    worker = threading.Thread(target=lambda: result.append(registry.execute("web__fetch_url", {"url": "https://example.com/slow"})))
    try:
        application.start()
        worker.start()
        marker = runtime_roots.user_root / "data/plugins/sakura.web/entered"
        deadline = time.monotonic() + 5
        while not marker.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert marker.exists()
        record = application.public_snapshot()["plugins"][0]
        application.set_enabled(record["installId"], False)
        worker.join(5)
        assert not worker.is_alive()
        assert result and not result[0].success
        assert registry.all() == []
    finally:
        application.close()
        if worker.ident is not None:
            worker.join(5)


def test_production_initialization_offers_plugin_without_legacy_setup(tmp_path: Path) -> None:
    from app.core_host.server import HostConfig, ReadinessController
    runtime_roots = roots(tmp_path)
    seen = []
    initializer_created = threading.Event()
    class Initializer:
        def initialize(self, cancel):
            raise RuntimeError("fixture: no chat model configured")
        def close(self):
            pass
    def factory(roots, tools):
        seen.append(tools)
        initializer_created.set()
        return Initializer()
    controller = ReadinessController(HostConfig(runtime_roots, "web-production", "a" * 32), initializer_factory=factory)
    controller.enable_plugins()
    try:
        deadline = time.monotonic() + 8
        controller.begin({})
        assert initializer_created.wait(max(0, deadline - time.monotonic()))
        app = controller.published_plugin_application()
        assert app is not None
        assert app.wait_until_loaded(timeout=max(0, deadline - time.monotonic()))
        assert controller.readiness() == "failed"
        assert {tool.name for tool in seen[0].all()} == NAMES
        assert app.public_snapshot()["plugins"][0]["state"] == "active"
    finally:
        controller.close()


def test_worker_exit_withdraws_tools_without_automatic_restart(tmp_path: Path) -> None:
    registry = ToolRegistry()
    app = PluginApplicationHost(roots(tmp_path), "web-crash", registry)
    try:
        app.start()
        process = app._manager._records["sakura.web"].process._process
        process.kill()
        process.wait(timeout=5)
        deadline = time.monotonic() + 5
        while registry.all() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert registry.all() == []
        assert app.public_snapshot()["plugins"][0]["state"] == "failed"
    finally:
        app.close()


def test_plugin_tool_declared_error_is_failed_and_reason_is_sanitized() -> None:
    registry = ToolRegistry([Tool("fixture", "fixture", {}, source="plugin", handler=lambda _: {"isError": True, "error": "provider failed", "reasonCode": "https://private.example/?token=secret"})])
    result = registry.execute("fixture", {})
    assert result.success is False
    assert result.reason_code == "PLUGIN_TOOL_EXECUTION_FAILED"


@pytest.mark.parametrize("timeout", [0, -1, 121, True, float("inf")])
def test_tool_deadline_rejects_invalid_limits(timeout) -> None:
    from app.core_host.plugin_host_services import _ToolsHostService, HostServiceError
    service = _ToolsHostService(ToolRegistry(), lambda *args, **kwargs: {})
    with pytest.raises(HostServiceError, match="TOOL_DESCRIPTOR_INVALID"):
        service.call("register", [{"name": "fixture", "description": "fixture", "timeoutSeconds": timeout}, "cb_" + "a" * 32])


def test_tool_deadline_reaches_worker_callback() -> None:
    from app.core_host.plugin_host_services import _ToolsHostService
    registry = ToolRegistry()
    calls = []
    def invoke(*args, **kwargs):
        calls.append(kwargs["timeout"])
        return {}
    service = _ToolsHostService(registry, invoke)
    service.call("register", [{"name": "fixture", "description": "fixture", "timeoutSeconds": 25}, "cb_" + "a" * 32])
    assert registry.execute("fixture", {}).success
    assert calls == [25]


def test_corrupt_plugin_config_does_not_fail_core_initialization(tmp_path: Path) -> None:
    runtime_roots = roots(tmp_path)
    config = runtime_roots.user_root / "data/plugins/sakura.web/config.json"
    config.parent.mkdir(parents=True)
    config.write_text("{broken", encoding="utf-8")
    app = PluginApplicationHost(runtime_roots, "web-bad-config", ToolRegistry())
    try:
        app.start()
        assert app.public_snapshot()["plugins"][0]["state"] == "failed"
    finally:
        app.close()


def test_web_failure_logs_code_without_request_content(monkeypatch):
    from plugins.builtin.sakura_web.plugin import _handler
    rows = []
    class Logger:
        def warning(self, message, *, fields):
            rows.append(fields)
    def fail(*args):
        raise TimeoutError("private request detail")
    monkeypatch.setattr(web, "search_web", fail)
    result = _handler("web_search", Logger())({"query": "private search"})
    assert result["reasonCode"] == "WEB_TIMEOUT"
    assert rows == [{"tool": "web_search", "reason_code": "WEB_TIMEOUT"}]


def test_fake_ip_search_preserves_proxy_hostname(monkeypatch):
    import httpx

    monkeypatch.setattr(web.socket, "getaddrinfo", lambda *a, **k: [
        (web.socket.AF_INET, web.socket.SOCK_STREAM, 6, "", ("198.18.0.4", 443))
    ])
    monkeypatch.setattr(web, "proxy_for_url", lambda url: "http://127.0.0.1:7890")
    requests = []
    original_client = httpx.Client

    def respond(request):
        requests.append(str(request.url))
        return httpx.Response(200, stream=httpx.ByteStream(b'<li class="b_algo"><h2><a href="https://example.com/">Result</a></h2></li>'))

    def client(**kwargs):
        assert kwargs["proxy"] == "http://127.0.0.1:7890"
        return original_client(transport=httpx.MockTransport(respond), trust_env=False)

    monkeypatch.setattr(httpx, "Client", client)
    assert web.search_web("Bing")["results"][0]["title"] == "Result"
    assert requests == ["https://www.bing.com/search?q=Bing"]


@pytest.mark.parametrize("address", ["198.18.0.4", "127.0.0.1", "192.168.1.1"])
def test_direct_fetch_rejects_non_public_dns(monkeypatch, address):
    monkeypatch.setattr(web, "proxy_for_url", lambda url: None)
    monkeypatch.setattr(web.socket, "getaddrinfo", lambda *a, **k: [
        (web.socket.AF_INET, web.socket.SOCK_STREAM, 6, "", (address, 80))
    ])
    with pytest.raises(ValueError, match="私有网络"):
        web.fetch_url("http://public.example/")


@pytest.mark.parametrize("target", ["http://127.0.0.1/", "http://192.168.1.1/", "http://198.18.0.4/", "http://localhost/", "http://[::1]/"])
def test_proxy_redirect_rejects_explicit_local_target(monkeypatch, target):
    monkeypatch.setattr(web, "proxy_for_url", lambda url: "http://127.0.0.1:7890")
    calls = []

    def proxy(url, *args):
        calls.append(url)
        return 302, "Found", {"Location": target}, b""

    monkeypatch.setattr(web, "_request_at_address", proxy)
    with pytest.raises(ValueError, match="私有网络"):
        web.fetch_url("https://public.example/")
    assert calls == ["https://public.example/"]


def test_search_action_long_result_through_real_settings_boundary(tmp_path):
    runtime_roots = roots(tmp_path)
    source = runtime_roots.distribution_root / "plugins/builtin/sakura_web/search.py"
    with source.open("a", encoding="utf-8") as stream:
        stream.write('\ndef search(query, max_results, values):\n    return {"results": [{"title": "Result", "url": "https://example.com/", "snippet": "中文摘要" * 3000}] * 5}\n')
    application = PluginApplicationHost(runtime_roots, "web-action", ToolRegistry())
    try:
        application.start()
        assert application.settings_action("sakura.web", "search", "test_search", {"provider": "baidu", "test_query": "fixture"}) == {}
        deadline = time.monotonic() + 3
        while True:
            section = application.settings_snapshot()["plugins"][0]["sections"][0]
            fields = {field["key"]: field["value"] for field in section["fields"]}
            if fields["test_status"]["state"] != "working":
                break
            assert time.monotonic() < deadline
        assert fields["test_status"]["state"] == "ready"
        assert fields["test_result"]
        result_field = next(field for field in section["fields"] if field["key"] == "test_result")
        assert len(json.dumps(result_field, ensure_ascii=False).encode("utf-8")) <= 16384
        assert application.settings_save("sakura.web", "search", {"provider": "bing", "tavily_api_key": "", "tavily_depth": "basic"})["saved"] is True
        saved_section = application.settings_snapshot()["plugins"][0]["sections"][0]
        assert next(field["value"] for field in saved_section["fields"] if field["key"] == "provider") == "bing"
        assert "已截断" in fields["test_result"]
    finally:
        application.close()
