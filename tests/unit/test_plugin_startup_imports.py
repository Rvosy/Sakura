from pathlib import Path
import json
import shutil
import threading
import subprocess
import sys

import pytest


@pytest.mark.parametrize("plugin", ["sakura_model_openai_compatible", "sakura_mcp"])
def test_plugin_registers_services_without_importing_request_sdks(plugin):
    """A cold or blocked request SDK must not consume runtime.initialize's budget."""
    script = r'''
import importlib.abc
import importlib
from pathlib import Path
import sys
from types import SimpleNamespace

repo, name = Path(sys.argv[1]), sys.argv[2]
sys.path[:0] = [str(repo), str(repo / "app/plugin_sdk")]
class ColdSDK(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split(".")[0] in {"openai", "httpx", "httpx2", "mcp", "pydantic"}:
            raise AssertionError("request SDK imported during plugin initialization: " + fullname)
sys.meta_path.insert(0, ColdSDK())
module = importlib.import_module("plugins.builtin." + name + ".plugin")
class Context:
    def __init__(self):
        self.config = SimpleNamespace(get=lambda: {})
        self.services, self.cleanups = {}, []
    def get(self, key):
        return SimpleNamespace(register=lambda *a, **kw: None, place=lambda *a, **kw: None,
                               register_provider=lambda *a, **kw: None)
    def effect(self, callback): self.cleanups.append(callback)
    def on(self, *args): pass
    def data_path(self, path): return Path(path)
    def provide(self, key, service, **kwargs): self.services[key] = service
context = Context()
instance = module.ModelPlugin() if name == "sakura_model_openai_compatible" else module.MCPPlugin()
try:
    instance.setup(context)
    assert len(context.services) == 1
    service = next(iter(context.services.values()))
    if name == "sakura_mcp": assert "stdio" in service.capabilities()["transports"]
    else: assert service.catalog() == []
finally:
    for cleanup in reversed(context.cleanups): cleanup()
'''
    result = subprocess.run(
        [sys.executable, "-I", "-S", "-c", script, str(Path(__file__).parents[2]), plugin],
        capture_output=True, text=True, timeout=15,
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("initially_enabled", [False, True])
def test_model_settings_enable_and_reload_do_not_wait_for_cold_sdk(tmp_path, initially_enabled):
    """Exercise production settings and worker RPC with an SDK import that never completes."""
    from app.plugin_sdk.sakura_tools import ToolRegistry
    from app.core_host.plugin_application import PluginApplicationHost
    from app.core_host.plugin_settings import PluginSettingsBoundary
    from app.plugins.inventory import PluginDesiredStateStore
    from app.storage.runtime_roots import DistributionPaths, RuntimeRoots

    repo = Path(__file__).parents[2]
    distribution, user = tmp_path / "distribution", tmp_path / "user"
    plugin_id = "sakura.model.openai_compatible"
    shutil.copytree(repo / "plugins/builtin/sakura_model_openai_compatible",
                    distribution / "plugins/builtin/sakura_model_openai_compatible",
                    ignore=shutil.ignore_patterns("__pycache__"))
    dependencies = DistributionPaths(distribution).plugin_dependency_root_for(plugin_id)
    dependencies.mkdir(parents=True)
    (dependencies / ".sakura-dependencies.json").write_text(json.dumps({
        "schemaVersion": 1, "kind": "requirements.txt",
        "python": f"{sys.version_info.major}.{sys.version_info.minor}",
    }), encoding="utf-8")
    # A blocked SDK models cold disk/antivirus/import work without a timing race or network.
    (dependencies / "httpx.py").write_text(
        'from pathlib import Path\nimport threading\n'
        'Path(__file__).with_name("sdk-import-attempted").touch()\n'
        'threading.Event().wait()\n', encoding="utf-8")
    roots = RuntimeRoots(distribution, user)
    PluginDesiredStateStore(user).set(plugin_id, initially_enabled)
    application = PluginApplicationHost(roots, "cold-sdk", ToolRegistry())
    boundary = PluginSettingsBoundary("cold-sdk", "fixture", roots,
                                      application_provider=lambda: application)
    try:
        application.start()
        assert application.wait_until_loaded(timeout=10)
        current = boundary.snapshot()
        record = current["plugins"][0]
        assert record["state"] == ("active" if initially_enabled else "disabled"), record
        if not initially_enabled:
            for enabled in (True, False, True):
                current = boundary.set_enabled(current["revision"], record["installId"], enabled)
                assert current["applicationState"] == "applied", current
                assert current["applicationReasonCode"] == "READY"
                assert current["plugins"][0]["state"] == ("active" if enabled else "disabled")
        before_pid = application._manager.snapshot()["plugins"][0]["pid"]
        saved = boundary.save({"pluginId": plugin_id, "sectionId": "request",
                               "values": {"timeout_seconds": 61}})
        assert saved["applicationState"] == "applied"
        assert saved["applicationReasonCode"] == "READY"
        after = application._manager.snapshot()["plugins"][0]
        assert after["state"] == "active", after
        assert after["pid"] != before_pid  # Configuration really restarted the worker.
        assert not (dependencies / "sdk-import-attempted").exists()
    finally:
        application.close()


def test_plugin_initialization_waits_for_its_owned_work_instead_of_eight_seconds(tmp_path):
    from app.plugins.inventory import PluginInventory
    from app.plugins.runtime_v4 import PluginRuntimeManager
    from app.storage.runtime_roots import RuntimeRoots

    distribution, user = tmp_path / "distribution", tmp_path / "user"
    source = distribution / "plugins/builtin/slow-start"
    source.mkdir(parents=True)
    (source / "plugin.yaml").write_text(
        'api: 4\nid: fixture.slow-start\nname: Fixture\nversion: 1.0.0\n'
        'entry: plugin:Plugin\nprovides: [fixture.ready]\nrequires: [fixture.gate]\n',
        encoding="utf-8")
    (source / "plugin.py").write_text(
        'class Plugin:\n    def setup(self, context):\n'
        '        context.get("fixture.gate").invoke("prepare", [], timeout_seconds=60)\n'
        '        context.provide("fixture.ready", object(), exports=())\n', encoding="utf-8")
    entered, release = threading.Event(), threading.Event()
    class Gate:
        def prepare(self, _argument):
            entered.set()
            release.wait()
    roots = RuntimeRoots(distribution, user)
    manager = PluginRuntimeManager(roots, "slow-start", PluginInventory(roots).scan().runtime_specs)
    manager.install_host_service("fixture.gate", Gate(), exports=("prepare",))
    completed, errors = [], []
    def start():
        try:
            completed.append(manager.start())
        except BaseException as error:
            errors.append(error)
    worker = threading.Thread(target=start, daemon=True)
    worker.start()
    try:
        assert entered.wait(5), completed or errors
        # Hold genuine setup work beyond the former eight-second startup limit.
        worker.join(timeout=8.1)
        assert worker.is_alive(), completed or errors
        assert manager.snapshot()["plugins"][0]["state"] == "starting"
        release.set()
        worker.join(timeout=3)
        assert not worker.is_alive()
        assert errors == []
        assert completed[0]["plugins"][0]["state"] == "active"
    finally:
        release.set()
        manager.close()
        worker.join(timeout=3)
