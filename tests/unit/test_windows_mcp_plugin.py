from __future__ import annotations

import json
import io
import os
from pathlib import Path
import shutil
import sys
import threading
import time

import psutil
import pytest

from app.plugin_sdk.sakura_tools import ToolRegistry
from app.core_host.plugin_application import PluginApplicationHost
from app.plugins.sakura_plugin_sdk import PluginContext
from app.storage.runtime_roots import RuntimeRoots
from app.storage.paths import StoragePaths
from app.core_host.runtime_logging import install_runtime_logging


ROOT = Path(__file__).parents[2]
CORE_DEPS = ROOT / "plugins/dependencies/sakura.mcp"


def test_tools_registered_after_setup_commit_are_active_and_revoked(tmp_path):
    registered = []
    handles = []
    def remote(service, method, args):
        if method == "register":
            registered.append(args[0]["name"])
            return {"registrationId": args[0]["name"]}
        registered.remove(args[0])
        return {"removed": True}
    def request(method, args):
        if method == "callback.register":
            handle = f"callback_{len(handles)}"
            handles.append(handle)
            return {"handle": handle}
        return {}
    context = PluginContext("fixture", tmp_path, tmp_path, remote, request)
    context.commit()
    context.get("sakura.host.tools").register({"name": "late_tool"}, lambda args: args)
    assert registered == ["late_tool"]
    context.close()
    assert registered == []


def until(check, timeout=30):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = check()
        if value:
            return value
        threading.Event().wait(0.02)
    raise AssertionError("condition not reached")


def roots(tmp_path, dependencies):
    dist, user = tmp_path / "distribution", tmp_path / "user"
    user.mkdir()
    shutil.copytree(ROOT / "plugins/builtin/sakura_mcp", dist / "plugins/builtin/sakura_mcp")
    shutil.copytree(ROOT / "plugins/optional/windows_mcp", user / "plugins/user/sakura.windows-mcp")
    for name, source in (("sakura.mcp", CORE_DEPS), ("sakura.windows-mcp", dependencies)):
        target = (dist / "plugins/dependencies" / name if name == "sakura.mcp"
                  else StoragePaths(user).plugin_dependency_root_for(name))
        shutil.copytree(source, target, ignore=shutil.ignore_patterns("__pycache__"))
        (target / ".sakura-dependencies.json").write_text(json.dumps({
            "schemaVersion": 1, "kind": "requirements.txt", "python": f"{sys.version_info.major}.{sys.version_info.minor}",
        }), encoding="utf-8")
    return RuntimeRoots(dist, user)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows-only consumer plugin")
def test_windows_plugin_dynamic_tools_and_cleanup(tmp_path):
    runtime = roots(tmp_path, CORE_DEPS)
    placeholder = StoragePaths(runtime.user_root).plugin_dependency_root_for("sakura.windows-mcp") / "windows_mcp"
    placeholder.mkdir()
    (placeholder / "__init__.py").write_text("", encoding="utf-8")
    server = runtime.user_root / "plugins/user/sakura.windows-mcp/server.py"
    server.write_text(FIXTURE, encoding="utf-8")
    registry = ToolRegistry()
    host = PluginApplicationHost(runtime, "windows-mcp-fixture", registry)
    stream = io.BytesIO()
    bridge = install_runtime_logging(stream)
    try:
        host.start()
        def status():
            return host.call_service("sakura.windows-mcp", "status")
        state = until(lambda: (value if (value := status())["state"] != "connecting" else None))
        assert state["state"] == "ready", state
        assert {tool.name for tool in registry.all()} == {"windows_mcp_result", "windows_mcp_snapshot", "windows_mcp_large", "windows_mcp_fail"}
        result = registry.execute("windows_mcp_snapshot", {})
        assert result.success, result
        server_pid = int(result.content["content"][0]["text"])
        assert psutil.pid_exists(server_pid)
        registry.execute("windows_mcp_fail", {})
        until(lambda: "MCP 工具返回失败" in stream.getvalue().decode("utf-8"))
        large = registry.execute("windows_mcp_large", {})
        assert large.success
        operation = large.content["operationId"]
        chunks, offset = [], 0
        while True:
            piece = registry.execute("windows_mcp_result", {"operationId": operation, "action": "read", "offset": offset})
            assert piece.success
            chunks.append(piece.content["text"])
            offset = piece.content["nextOffset"]
            if piece.content["done"]:
                break
        assert json.loads("".join(chunks))["content"][0]["text"] == "大结果" * 20000
        registry.execute("windows_mcp_result", {"operationId": operation, "action": "release"})
        record = next(row for row in host.settings_snapshot()["plugins"] if row["pluginId"] == "sakura.windows-mcp")
        assert record["sections"] == []
        host.set_enabled(record["installId"], False)
        until(lambda: not psutil.pid_exists(server_pid))
        assert registry.all() == []
        until(lambda: "Windows 操作工具已就绪" in stream.getvalue().decode("utf-8"))
        logs = stream.getvalue().decode("utf-8")
        assert "MCP 服务已连接" in logs
        assert "fixture-stderr" in logs
        assert "sakura.windows-mcp" in logs and "sakura.mcp" in logs
        assert "fixture-private-token" not in logs
    finally:
        host.close()
        bridge.close()


FIXTURE = '''
import sys, os
print("fixture-stderr token=fixture-private-token", file=sys.stderr, flush=True)
from pathlib import Path
dependency = Path(sys.argv[1])
sys.path[:0] = [str(dependency), str(dependency / "win32"), str(dependency / "win32/lib")]
from mcp.server import MCPServer
server = MCPServer("Windows fixture")
@server.tool(name="Snapshot")
def snapshot() -> str:
    return str(os.getpid())
@server.tool(name="Large")
def large() -> str:
    return "大结果" * 20000
@server.tool(name="Fail")
def fail() -> str:
    raise ValueError("fixture tool failure")
server.run()
'''


@pytest.mark.skipif(sys.platform != "win32" or os.environ.get("SAKURA_TEST_WINDOWS_MCP_LIVE") != "1",
                    reason="Opt-in: requires an unlocked Windows desktop and the released server dependencies")
def test_released_windows_mcp_readonly_desktop(tmp_path):
    runtime = roots(tmp_path, Path(os.environ["SAKURA_WINDOWS_MCP_TEST_DEPS"]))
    registry = ToolRegistry()
    host = PluginApplicationHost(runtime, "windows-mcp-live", registry)
    try:
        host.start()
        def status():
            return host.call_service("sakura.windows-mcp", "status")
        state = until(lambda: (value if (value := status())["state"] != "connecting" else None), timeout=70)
        assert state["state"] == "ready", state
        catalog = host.call_service("sakura.windows-mcp", "catalog")
        names = [item["name"] for item in catalog]
        assert {"Snapshot", "Screenshot", "Click", "PowerShell"}.issubset(names)
        result = registry.execute("windows_mcp_snapshot", {"use_vision": False, "use_ui_tree": False, "use_dom": False})
        assert result.success, result.reason_code
        assert result.content.get("isError") is not True
        assert any(item.get("type") == "text" and item.get("text") for item in result.content["content"])
        # Report only protocol facts and sizes, never desktop text or screenshots.
        manager = host._manager
        snapshot = host.settings_snapshot()
        core = next(row for row in snapshot["plugins"] if row["pluginId"] == "sakura.mcp")
        assert core["sections"] == []
        record = next(row for row in snapshot["plugins"] if row["pluginId"] == "sakura.windows-mcp")
        children = psutil.Process(manager._records["sakura.mcp"].pid).children(recursive=True)
        assert children
        server_pids = [child.pid for child in children]
        host.set_enabled(record["installId"], False)
        until(lambda: all(not psutil.pid_exists(pid) for pid in server_pids))
        assert registry.all() == []
        print(json.dumps({"upstream": "windows-mcp 0.8.5", "toolCount": len(names), "tools": names,
                          "snapshotBytes": len(json.dumps(result.content).encode()), "serversReaped": len(server_pids)}))
    finally:
        host.close()
