from __future__ import annotations

import shutil
from pathlib import Path

from app.llm.prompts.types import ContextRequest


FIXTURE_ROOT = Path(__file__).parents[1] / "fixtures" / "runtime_v2" / "wp_4_04"


def _assistant_root(tmp_path: Path) -> Path:
    root = tmp_path / "assistant"
    shutil.copytree(FIXTURE_ROOT / "plugins", root / "plugins")
    return root


def test_plugin_settings_boundary_remains_qt_free() -> None:
    """Core settings discovery must not pull the legacy Qt resource adapter."""

    source = """
import importlib.abc
import sys

class RejectPySide(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == 'PySide6' or fullname.startswith('PySide6.'):
            raise AssertionError(f'forbidden Qt import: {fullname}')
        return None

sys.meta_path.insert(0, RejectPySide())
import app.core_host.plugin_settings
import app.core_host.plugin_worker_runtime
"""
    import subprocess
    import sys

    subprocess.run([sys.executable, "-c", source], check=True)


def test_generation_private_worker_loads_healthy_plugin_and_isolates_bad_plugin(tmp_path: Path) -> None:
    from app.core_host.plugin_worker import PluginWorkerClient

    worker = PluginWorkerClient(_assistant_root(tmp_path), "generation-a")
    try:
        worker.start()
        snapshot = worker.wait_until_loaded(timeout=5)
        by_id = {item["pluginId"]: item for item in snapshot["plugins"]}

        assert by_id["fixture_plugin"]["state"] == "ready"
        assert by_id["broken_plugin"]["state"] == "degraded"
        assert by_id["broken_plugin"]["reasonCode"] in {
            "API_VERSION_UNSUPPORTED",
            "PERMISSION_UNKNOWN",
        }
        assert "entry" not in repr(snapshot)
        assert str(tmp_path) not in repr(snapshot)
        assert worker.call_tool("fixture_plugin:tool:fixture_echo", {"value": "hello"}) == {
            "echo": "hello"
        }
    finally:
        worker.close()
    assert worker.state == "stopped"


def test_worker_projects_prompt_context_event_and_declarative_settings(tmp_path: Path) -> None:
    from app.core_host.plugin_worker import PluginWorkerClient

    root = _assistant_root(tmp_path)
    worker = PluginWorkerClient(root, "generation-a")
    try:
        worker.start()
        worker.wait_until_loaded(timeout=5)
        patches = worker.prompt_patches()
        assert [(item.patch_id, item.system_prompt_append) for item in patches] == [
            ("fixture_prompt", "fixture prompt fact")
        ]
        providers = worker.context_providers()
        fragments = providers[0].build_context(ContextRequest(current_input="hello"))
        assert fragments[0].content == "input=hello"
        assert fragments[0].source == "plugin:fixture_plugin"
        assert fragments[0].trust == "untrusted"

        settings = worker.settings_snapshot()
        plugin = next(item for item in settings["plugins"] if item["pluginId"] == "fixture_plugin")
        assert plugin["sections"][0]["values"] == {"label": "fixture"}
        action = worker.settings_action("fixture_plugin", "general", "reset", {"label": "changed"})
        assert action == {"values": {"label": "fixture"}, "message": "reset"}
        worker.emit_event("message.user", {"text": "bounded"})
        assert (root / "data" / "plugins" / "fixture_plugin" / "config.json").is_file()
    finally:
        worker.close()


def test_worker_rejects_stale_contribution_identity(tmp_path: Path) -> None:
    from app.core_host.plugin_worker import PluginWorkerClient, PluginWorkerError

    worker = PluginWorkerClient(_assistant_root(tmp_path), "generation-a")
    try:
        worker.start()
        worker.wait_until_loaded(timeout=5)
        try:
            worker.call_tool("generation-old:fixture_plugin:tool:fixture_echo", {"value": "no"})
        except PluginWorkerError as error:
            assert error.code == "CONTRIBUTION_INVALID"
        else:
            raise AssertionError("stale contribution identity was accepted")
    finally:
        worker.close()


def test_worker_timeout_terminates_generation_and_invalidates_contributions(tmp_path: Path) -> None:
    from app.agent.tools import ToolRegistry
    from app.core_host.plugin_worker import PluginWorkerClient, PluginWorkerError

    class Runtime:
        prompt_patches = []
        context_providers = []

        def set_prompt_patches(self, values):
            self.prompt_patches = list(values)

        def set_context_providers(self, values):
            self.context_providers = list(values)

    registry = ToolRegistry()
    runtime = Runtime()
    worker = PluginWorkerClient(_assistant_root(tmp_path), "generation-a", call_timeout=1.0)
    try:
        worker.start()
        worker.wait_until_loaded(timeout=5)
        worker.bind_runtime(registry, runtime)
        assert worker.wait_until_bound(timeout=5)
        assert registry.get("fixture_echo") is not None
        try:
            worker.call_tool("fixture_plugin:tool:fixture_echo", {"value": "__hang__"})
        except PluginWorkerError as error:
            assert error.code == "PLUGIN_CALL_TIMEOUT"
        else:
            raise AssertionError("hung plugin call did not time out")
        assert worker.state == "degraded"
        assert registry.get("fixture_echo") is None
        assert runtime.prompt_patches == []
        assert runtime.context_providers == []
    finally:
        worker.close()
