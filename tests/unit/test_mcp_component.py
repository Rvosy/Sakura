from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess
import sys
import time
import threading

import pytest
import psutil

from app.agent.tools import ToolRegistry
from app.core_host.plugin_application import PluginApplicationHost
from app.plugins.runtime_v4 import PluginRuntimeError
from app.storage.runtime_roots import RuntimeRoots


REPO = Path(__file__).parents[2]
DEPENDENCIES = REPO / "plugins/dependencies/sakura.mcp"


def until(check):
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        value = check()
        if value:
            return value
        threading.Event().wait(0.01)
    raise AssertionError("condition not reached")


def test_sdk_transports_and_operations(tmp_path):
    result = subprocess.run([sys.executable, "-I", "-S", str(REPO / "tests/fixtures/mcp_component/exercise.py"),
                             str(REPO), str(tmp_path)], capture_output=True, text=True, timeout=90)
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("service_access", ["get", "bind"])
def test_component_in_real_plugin_host(tmp_path, service_access):
    dist = tmp_path / "distribution"
    shutil.copytree(REPO / "plugins/builtin/sakura_mcp", dist / "plugins/builtin/sakura_mcp")
    shutil.copytree(DEPENDENCIES, dist / "plugins/dependencies/sakura.mcp")
    consumer = dist / "plugins/builtin/test_consumer"
    consumer.mkdir()
    (consumer / "plugin.yaml").write_text("api: 4\nid: test.consumer\nname: Consumer\nversion: 1.0.0\nentry: plugin:Consumer\nenabled: true\nprovides: [test.consumer]\nrequires: [sakura.mcp]\n", encoding="utf-8")
    (consumer / "plugin.py").write_text('''
class Consumer:
    def setup(self, context):
        self.service = context.get("sakura.mcp")
        context.provide("test.consumer", self, exports=["call"])
    def call(self, method, args):
        return getattr(self.service, method)(*args)
'''.replace('context.get("sakura.mcp")', f'context.{service_access}("sakura.mcp")'), encoding="utf-8")
    user = tmp_path / "user"
    user.mkdir()
    registry = ToolRegistry()
    application = PluginApplicationHost(RuntimeRoots(dist, user), "mcp-test", registry)
    try:
        application.start()
        snapshot = application.settings_snapshot()
        mcp = next(item for item in snapshot["plugins"] if item["pluginId"] == "sakura.mcp")
        assert mcp["state"] == "active", json.dumps(snapshot, ensure_ascii=False)
        assert registry.all() == []
        assert mcp["sections"] == []
        assert not (user / "config/mcp.yaml").exists()
        config = {"command": sys.executable, "args": ["-I", "-S", str(REPO / "tests/fixtures/mcp_component/server.py"), str(DEPENDENCIES)]}
        call = lambda method, *args: application.application.call_service("test.consumer", "call", method, list(args))
        with pytest.raises(PluginRuntimeError, match="MCP_CREDENTIAL_KEY_INVALID"):
            call("registerServer", config, "../foreign")
        handle = call("registerServer", config, "fixture")["handle"]
        with pytest.raises(PluginRuntimeError, match="MCP_CREDENTIAL_IN_USE"):
            call("registerServer", config, "fixture")
        until(lambda: call("status")[0]["state"] == "ready")
        op = call("begin", handle, "tools/call", {"name": "pid"})["operationId"]
        result = until(lambda: (value if (value := call("inspect", op))["state"] != "running" else None))
        server_pid = int(result["result"]["content"][0]["text"])
        assert psutil.pid_exists(server_pid)
        consumer_record = next(item for item in snapshot["plugins"] if item["pluginId"] == "test.consumer")
        application.set_enabled(consumer_record["installId"], False)
        until(lambda: not psutil.pid_exists(server_pid))
        application.set_enabled(consumer_record["installId"], True)
        assert call("status") == []
        with pytest.raises(PluginRuntimeError, match="MCP_HANDLE_NOT_FOUND"):
            call("begin", handle, "tools/list")
        # A crashed consumer cannot run cleanup: the generic scope event must reap its server.
        handle = call("registerServer", config, "fixture")["handle"]
        until(lambda: call("status")[0]["state"] == "ready")
        op = call("begin", handle, "tools/call", {"name": "pid"})["operationId"]
        result = until(lambda: (value if (value := call("inspect", op))["state"] != "running" else None))
        crashed_server_pid = int(result["result"]["content"][0]["text"])
        manager = application.application._manager
        component_pid = manager._records["sakura.mcp"].pid
        manager._records["test.consumer"].process._process.kill()
        until(lambda: not psutil.pid_exists(crashed_server_pid))
        assert manager._records["sakura.mcp"].pid == component_pid
        assert registry.all() == []
    finally:
        application.close()
    assert registry.all() == []
