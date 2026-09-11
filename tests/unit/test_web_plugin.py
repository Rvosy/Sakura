from __future__ import annotations

import json
import shutil
import threading
import time
from pathlib import Path

import pytest
import yaml

from app.agent.tools import Tool, ToolRegistry
from app.config.web_plugin_migration import migrate_web_configuration
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


def test_real_plugin_calls_and_disable(tmp_path: Path) -> None:
    runtime_roots = roots(tmp_path)
    migrate_web_configuration(runtime_roots.user_root)
    registry = ToolRegistry()
    application = PluginApplicationHost(runtime_roots, "web-fixture", registry)
    try:
        application.start()
        assert {tool.name for tool in registry.all()} == NAMES
        assert all(tool.source == "plugin" for tool in registry.all())
        record = application.settings_snapshot()["plugins"][0]
        assert record["state"] == "active"
        assert not record.get("sections")
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


def old_web(**changes):
    return {"transport": "stdio", "command": "{python}", "args": ["{core_root}/app/agent/mcp/web_search_server.py"], "name_prefix": "web__", "risk": "low", **changes}


def write_mcp(root: Path, value: dict) -> Path:
    path = root / "config/mcp.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(value, allow_unicode=True), encoding="utf-8")
    return path


@pytest.mark.parametrize("config,enabled", [
    (None, True),
    ({"enabled": False, "servers": {}}, False),
    ({"enabled": True, "servers": {"web": old_web()}}, True),
    ({"enabled": False, "servers": {"web": old_web()}}, False),
    ({"enabled": True, "servers": {"web": old_web(enabled=False)}}, False),
    ({"enabled": True, "servers": {"web": {"transport": "stdio", "command": "custom"}}}, True),
    ({"enabled": False, "servers": {"custom": {"transport": "stdio", "command": "custom"}}}, False),
])
def test_migration_initial_state(tmp_path: Path, config, enabled: bool) -> None:
    if config is not None:
        write_mcp(tmp_path, config)
    migrate_web_configuration(tmp_path)
    assert PluginDesiredStateStore(tmp_path).read()["sakura.web"] is enabled
    before = {path: path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}
    migrate_web_configuration(tmp_path)
    assert {path: path.read_bytes() for path in before} == before
    if config and "web" in config["servers"] and config["servers"]["web"].get("command") == "custom":
        assert yaml.safe_load((tmp_path / "config/mcp.yaml").read_text()) == config


def test_migration_preserves_tool_limits_and_explicit_switch(tmp_path: Path) -> None:
    runtime_roots = roots(tmp_path)
    root = runtime_roots.user_root
    external = {"transport": "sse", "url": "https://example.com/mcp", "headers": {"Authorization": "fixture-secret"}}
    write_mcp(root, {"enabled": True, "default_call_timeout": 25, "servers": {"web": old_web(exclude_tools=["fetch_*"], tool_policies={"web_search": {"risk": "high"}}), "external": external}})
    PluginDesiredStateStore(root).set("sakura.web", False)
    migrate_web_configuration(root)
    assert PluginDesiredStateStore(root).read()["sakura.web"] is False
    mcp = yaml.safe_load((root / "config/mcp.yaml").read_text())
    assert mcp["servers"]["external"] == external
    assert mcp["servers"]["web"]["enabled"] is False
    PluginDesiredStateStore(root).set("sakura.web", True)
    registry = ToolRegistry()
    app = PluginApplicationHost(runtime_roots, "web-limits", registry)
    try:
        app.start()
        assert [tool.name for tool in registry.all()] == ["web__web_search"]
        assert registry.get("web__web_search").risk == "high"
    finally:
        app.close()


@pytest.mark.parametrize("custom", [None, {"env": {"CUSTOM": "value"}}, {"args": ["{core_root}/app/agent/mcp/web_search_server.py", "--custom"]}])
def test_unsupported_migration_preserves_original(tmp_path: Path, custom) -> None:
    path = write_mcp(tmp_path, {"servers": {"web": old_web(**(custom or {}))}})
    if custom is None:
        path.write_text("servers: [broken", encoding="utf-8")
    before = path.read_bytes()
    migrate_web_configuration(tmp_path)
    assert path.read_bytes() == before
    assert PluginDesiredStateStore(tmp_path).read()["sakura.web"] is False
    assert json.loads((tmp_path / "data/plugins/sakura.web/config.json").read_text())["migration_error"]


def test_interrupted_handoff_never_enables_both(tmp_path: Path, monkeypatch) -> None:
    from app.config import web_plugin_migration as migration
    path = write_mcp(tmp_path, {"servers": {"web": old_web()}})
    original = path.read_bytes()
    write = migration.atomic_write_text
    def fail_mcp(target, *args, **kwargs):
        if target == path:
            raise OSError("fixture write failure")
        return write(target, *args, **kwargs)
    monkeypatch.setattr(migration, "atomic_write_text", fail_mcp)
    with pytest.raises(OSError):
        migrate_web_configuration(tmp_path)
    assert path.read_bytes() == original
    assert json.loads((tmp_path / "data/plugins/sakura.web/config.json").read_text())["migration_error"] == "WEB_MIGRATION_INCOMPLETE"
    monkeypatch.setattr(migration, "atomic_write_text", write)
    migrate_web_configuration(tmp_path)
    assert yaml.safe_load(path.read_text())["servers"]["web"]["enabled"] is False
    assert "migration_error" not in json.loads((tmp_path / "data/plugins/sakura.web/config.json").read_text())


@pytest.mark.parametrize("old_config,existing", [(None, None), (None, False), ({"servers": {"web": old_web(exclude_tools=["fetch_*"])}}, None)])
def test_legacy_import_initializes_web_before_losing_source_state(tmp_path: Path, old_config, existing) -> None:
    from app.legacy_import.configuration import migrate_configuration
    source = tmp_path / "source"
    (source / "data/config").mkdir(parents=True)
    if old_config is not None:
        write_mcp(source / "data", old_config)
    current = tmp_path / "current"
    if existing is not None:
        PluginDesiredStateStore(current).set("sakura.web", existing)
    staged = tmp_path / "staged"
    migrate_configuration(source, staged, new_tts_root=current / "tts", existing_user_root=current)
    assert PluginDesiredStateStore(staged).read()["sakura.web"] is (existing if existing is not None else True)
    if old_config is None:
        assert not (staged / "config/mcp.yaml").exists()
    else:
        assert yaml.safe_load((staged / "config/mcp.yaml").read_text())["servers"]["web"]["enabled"] is False
        assert json.loads((staged / "data/plugins/sakura.web/config.json").read_text())["allowed_tools"] == ["web_search"]


@pytest.mark.parametrize("external_first", [True, False])
def test_external_mcp_collision_never_overwrites_tool(tmp_path: Path, external_first: bool) -> None:
    from app.agent.mcp.config import MCPConfig, MCPServerConfig
    from app.agent.mcp.bridge import MCPToolSpec
    from app.agent.mcp.provider import MCPToolProvider
    class Bridge:
        def connect(self):
            pass
        def list_tools(self):
            return [MCPToolSpec("web_search", "external search", {"type": "object"})]
        def call_tool(self, name, arguments):
            return {"external": True}
        def close(self):
            pass
    registry = ToolRegistry()
    provider = MCPToolProvider(MCPConfig(enabled=True, servers=[MCPServerConfig(name="web", transport="stdio", command="custom")]), bridge_factory=lambda *_: Bridge())
    app = PluginApplicationHost(roots(tmp_path), "web-conflict", registry)
    try:
        if external_first:
            provider.register_tools(registry)
            original = registry.get("web__web_search")
            app.start()
            assert app.public_snapshot()["plugins"][0]["reasonCode"] == "TOOL_NAME_CONFLICT"
            assert registry.get("web__fetch_url") is None
        else:
            app.start()
            original = registry.get("web__web_search")
            assert provider.register_tools(registry) == 0
            assert provider.status_snapshot()["servers"][0]["reasonCode"] == "TOOL_NAME_CONFLICT"
        assert registry.get("web__web_search") is original
    finally:
        app.close()
        provider.close()


@pytest.mark.parametrize("mcp_failure", [False, True])
def test_production_initialization_offers_plugin_without_mcp(tmp_path: Path, mcp_failure: bool) -> None:
    from app.core_host.server import HostConfig, ReadinessController
    runtime_roots = roots(tmp_path)
    if mcp_failure:
        write_mcp(runtime_roots.user_root, {"servers": {"external": {"transport": "stdio", "command": "definitely-missing-web-fixture"}}})
    seen = []
    class Initializer:
        def initialize(self, cancel):
            raise RuntimeError("fixture: no chat model configured")
        def close(self):
            pass
    def factory(roots, tools, mcp):
        seen.extend(tools.all())
        return Initializer()
    controller = ReadinessController(HostConfig(runtime_roots, "web-production", "a" * 32), initializer_factory=factory)
    controller.enable_plugins()
    controller.enable_mcp()
    try:
        controller.begin({})
        deadline = time.monotonic() + 8
        while controller.readiness() != "failed" and time.monotonic() < deadline:
            time.sleep(0.01)
        assert {tool.name for tool in seen} == NAMES
        app = controller.published_plugin_application()
        assert app.public_snapshot()["plugins"][0]["state"] == "active"
        assert PluginDesiredStateStore(runtime_roots.user_root).read()["sakura.web"] is True
        assert (runtime_roots.user_root / "config/mcp.yaml").exists() is mcp_failure
    finally:
        controller.close()


def test_worker_exit_withdraws_tools_without_automatic_restart(tmp_path: Path) -> None:
    registry = ToolRegistry()
    app = PluginApplicationHost(roots(tmp_path), "web-crash", registry)
    try:
        app.start()
        process = app.application._manager._records["sakura.web"].process._process
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


def test_migrated_tool_deadline_reaches_worker_callback() -> None:
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
    migrate_web_configuration(runtime_roots.user_root)
    app = PluginApplicationHost(runtime_roots, "web-bad-config", ToolRegistry())
    try:
        app.start()
        assert app.public_snapshot()["plugins"][0]["state"] == "failed"
    finally:
        app.close()
