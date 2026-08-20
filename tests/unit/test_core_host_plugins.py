from __future__ import annotations

import json
import shutil
import time
from pathlib import Path

import pytest

from app.llm.prompts.types import ContextRequest


FIXTURE_ROOT = Path(__file__).parents[1] / "fixtures" / "runtime_v2" / "wp_4_04"


class _Runtime:
    def __init__(self) -> None:
        self.context_providers = []

    def set_context_providers(self, values) -> None:
        self.context_providers = list(values)


def _assistant_root(tmp_path: Path) -> Path:
    root = tmp_path / "assistant"
    shutil.copytree(FIXTURE_ROOT / "plugins", root / "plugins")
    return root


def _wait_until(predicate, timeout: float = 8.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.02)
    raise AssertionError("condition did not become true before the deadline")


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


def test_plugin_settings_preview_uses_v3_runtime_diagnostics() -> None:
    from app.core_host.plugin_settings import _preview_plugin
    from app.plugins.models import PluginSpec

    unsupported = _preview_plugin(
        PluginSpec(
            entry="plugin:Legacy",
            plugin_id="legacy",
            api_version=2,
            enabled=False,
            permissions=("tool",),
        )
    )
    assert unsupported["enabled"] is False
    assert unsupported["supported"] is False
    assert unsupported["state"] == "failed"
    assert unsupported["reasonCode"] == "API_VERSION_UNSUPPORTED"
    assert unsupported["permissions"] == []

    required = _preview_plugin(
        PluginSpec(
            entry="plugin:Required",
            plugin_id="required",
            api_version=3,
            enabled=False,
            required=True,
        )
    )
    assert required["enabled"] is True
    assert required["state"] == "starting"
    assert required["reasonCode"] == "SESSION_NOT_READY"


def test_generation_private_worker_uses_only_v3_host_contributions(tmp_path: Path) -> None:
    from app.agent.tools import ToolRegistry
    from app.core_host.plugin_worker import PluginWorkerClient

    registry = ToolRegistry()
    runtime = _Runtime()
    worker = PluginWorkerClient(_assistant_root(tmp_path), "generation-a")
    worker.configure_host_services(registry, runtime)
    try:
        worker.start()
        worker.bind_runtime(registry, runtime)
        snapshot = worker.wait_until_loaded(timeout=5)
        by_id = {item["pluginId"]: item for item in snapshot["plugins"]}

        assert by_id["fixture_plugin"]["state"] == "active"
        assert by_id["fixture_plugin"]["apiVersion"] == 3
        assert by_id["fixture_plugin"]["supported"] is True
        assert by_id["broken_plugin"]["state"] == "failed"
        assert by_id["broken_plugin"]["apiVersion"] == 99
        assert by_id["broken_plugin"]["supported"] is False
        assert by_id["broken_plugin"]["reasonCode"] == "API_VERSION_UNSUPPORTED"
        assert set(snapshot) == {"schemaVersion", "state", "reasonCode", "plugins"}
        assert "entry" not in repr(snapshot)
        assert str(tmp_path) not in repr(snapshot)
        assert worker.wait_until_bound(timeout=5)

        result = registry.prepare_or_execute("fixture_echo", {"value": "hello"})
        assert result.success is True
        assert result.content == {"echo": "hello"}
    finally:
        worker.close()
    assert worker.state == "stopped"
    assert registry.get("fixture_echo") is None
    assert runtime.context_providers == []


def test_worker_projects_v3_context_event_and_declarative_settings(tmp_path: Path) -> None:
    from app.agent.tools import ToolRegistry
    from app.core_host.plugin_worker import PluginWorkerClient

    root = _assistant_root(tmp_path)
    registry = ToolRegistry()
    runtime = _Runtime()
    worker = PluginWorkerClient(root, "generation-a")
    worker.configure_host_services(registry, runtime)
    try:
        worker.start()
        worker.bind_runtime(registry, runtime)
        worker.wait_until_loaded(timeout=5)
        assert worker.wait_until_bound(timeout=5)

        assert len(runtime.context_providers) == 1
        fragments = runtime.context_providers[0].build_context(
            ContextRequest(current_input="hello")
        )
        assert fragments[0].content == "input=hello"
        assert fragments[0].source == "plugin"
        assert fragments[0].trust == "untrusted"

        settings = worker.settings_snapshot()
        plugin = next(item for item in settings["plugins"] if item["pluginId"] == "fixture_plugin")
        assert plugin["sections"][0]["values"] == {"label": "fixture"}
        action = worker.settings_action("fixture_plugin", "general", "reset", {"label": "changed"})
        assert action == {"values": {"label": "fixture"}, "message": "reset"}
        saved = worker.settings_save("fixture_plugin", "general", {"label": "changed"})
        assert saved == {
            "saved": True,
            "applicationState": "restart_required",
            "reasonCode": "CONFIG_RELOAD_REQUIRED",
        }
        worker.emit_event(
            "message.user",
            {"role": "user", "characters": 7, "text": "bounded"},
        )
        config = json.loads(
            (root / "data" / "plugins" / "fixture_plugin" / "config.json").read_text(
                encoding="utf-8"
            )
        )
        assert config == {
            "event_characters": 7,
            "event_role": "user",
            "label": "changed",
        }
    finally:
        worker.close()


def test_runtime_v2_rejects_v2_manifest_without_importing_or_feature_rpc(
    tmp_path: Path,
) -> None:
    from app.core_host.plugin_worker_runtime import PluginWorkerRuntime, WorkerRuntimeError

    root = tmp_path / "assistant"
    plugin_root = root / "plugins" / "legacy_fixture"
    plugin_root.mkdir(parents=True)
    (plugin_root / "plugin.yaml").write_text(
        """
api: 2
id: legacy_fixture
name: Legacy Fixture
version: 1.0.0
entry: plugin:LegacyFixture
enabled: true
""".strip(),
        encoding="utf-8",
    )
    (plugin_root / "plugin.py").write_text(
        'raise RuntimeError("v2 plugin must not be imported")',
        encoding="utf-8",
    )

    runtime = PluginWorkerRuntime(root, "generation-a")
    try:
        snapshot = runtime.initialize()
        plugin = snapshot["plugins"][0]
        assert plugin["apiVersion"] == 2
        assert plugin["supported"] is False
        assert plugin["state"] == "failed"
        assert plugin["reasonCode"] == "API_VERSION_UNSUPPORTED"

        for command in (
            "tool.call",
            "context.call",
            "settings.get",
            "settings.save",
            "settings.action",
        ):
            with pytest.raises(WorkerRuntimeError) as unsupported:
                runtime.handle(command, {})
            assert unsupported.value.code == "PLUGIN_COMMAND_UNKNOWN"

        disabled = runtime.handle(
            "lifecycle.set_enabled",
            {"pluginId": "legacy_fixture", "enabled": False},
        )
        assert disabled["plugins"][0]["enabled"] is False
        assert disabled["plugins"][0]["state"] == "failed"
        assert disabled["plugins"][0]["reasonCode"] == "API_VERSION_UNSUPPORTED"
        restored = runtime.handle(
            "lifecycle.set_enabled",
            {"pluginId": "legacy_fixture", "enabled": True},
        )
        assert restored["plugins"][0]["state"] == "failed"
        assert restored["plugins"][0]["reasonCode"] == "API_VERSION_UNSUPPORTED"
    finally:
        runtime.close()


def test_runtime_v2_v3_import_path_does_not_load_legacy_manager_or_capabilities(
    tmp_path: Path,
) -> None:
    root = tmp_path / "assistant"
    plugin_root = root / "plugins" / "v3_fixture"
    plugin_root.mkdir(parents=True)
    (plugin_root / "plugin.yaml").write_text(
        """
api: 3
id: v3_fixture
name: V3 Fixture
version: 1.0.0
entry: plugin:V3Fixture
provides: [com.example.v3-fixture]
requires: []
optional: []
""".strip(),
        encoding="utf-8",
    )
    (plugin_root / "plugin.py").write_text(
        """
class V3Fixture:
    def setup(self, context):
        context.provide("com.example.v3-fixture", object())
""".strip(),
        encoding="utf-8",
    )
    source = f"""
import sys
from pathlib import Path
from app.core_host.plugin_worker_runtime import PluginWorkerRuntime

runtime = PluginWorkerRuntime(Path({str(root)!r}), "generation-v3-only")
try:
    snapshot = runtime.initialize()
    assert snapshot["plugins"][0]["state"] == "active"
    assert "app.plugins.manager" not in sys.modules
    assert "app.plugins.capabilities" not in sys.modules
finally:
    runtime.close()
"""
    import subprocess
    import sys

    subprocess.run([sys.executable, "-c", source], check=True)


def test_worker_binding_does_not_require_a_plugin_tool(tmp_path: Path) -> None:
    from app.agent.tools import ToolRegistry
    from app.core_host.plugin_worker import PluginWorkerClient

    root = tmp_path / "assistant"
    root.mkdir()
    registry = ToolRegistry()
    runtime = _Runtime()
    worker = PluginWorkerClient(root, "generation-empty")
    worker.configure_host_services(registry, runtime)
    try:
        worker.start()
        worker.bind_runtime(registry, runtime)
        worker.wait_until_loaded(timeout=5)
        assert worker.wait_until_bound(timeout=5)
    finally:
        worker.close()


def test_worker_timeout_rebuilds_v3_callbacks_and_invalidates_old_tool(tmp_path: Path) -> None:
    from app.agent.tools import ToolRegistry
    from app.core_host.plugin_worker import PluginWorkerClient

    registry = ToolRegistry()
    runtime = _Runtime()
    worker = PluginWorkerClient(
        _assistant_root(tmp_path),
        "generation-a",
        call_timeout=0.2,
    )
    worker.configure_host_services(registry, runtime)
    try:
        worker.start()
        worker.bind_runtime(registry, runtime)
        worker.wait_until_loaded(timeout=5)
        assert worker.wait_until_bound(timeout=5)
        first_token = worker._token
        first_tool = registry.get("fixture_echo")
        assert first_tool is not None

        failed = registry.prepare_or_execute("fixture_echo", {"value": "__hang__"})
        assert failed.success is False
        assert failed.reason_code == "PLUGIN_CALL_TIMEOUT"
        _wait_until(
            lambda: worker._token != first_token
            and registry.get("fixture_echo") is not None
            and registry.get("fixture_echo") is not first_tool
            and bool(runtime.context_providers)
        )
        assert runtime.context_providers
    finally:
        worker.close()


def test_worker_callback_failure_exposes_only_sanitized_reason_code(tmp_path: Path) -> None:
    from app.agent.tools import ToolRegistry
    from app.core_host.plugin_worker import PluginWorkerClient

    registry = ToolRegistry()
    runtime = _Runtime()
    worker = PluginWorkerClient(_assistant_root(tmp_path), "generation-a")
    worker.configure_host_services(registry, runtime)
    try:
        worker.start()
        worker.wait_until_loaded(timeout=5)
        failed = registry.prepare_or_execute("fixture_echo", {"value": "__error__"})
        assert failed.success is False
        assert failed.reason_code == "PLUGIN_CALLBACK_IO_FAILED"
        assert "secret" not in repr(failed).lower()
        assert "browser.exe" not in repr(failed).lower()
    finally:
        worker.close()


def test_plugin_tool_descriptor_does_not_activate_confirmation_in_assistant_mode(
    tmp_path: Path,
) -> None:
    from app.agent.actions import PendingToolAction
    from app.agent.tools import ToolRegistry
    from app.core_host.plugin_worker import PluginWorkerClient

    registry = ToolRegistry()
    runtime = _Runtime()
    worker = PluginWorkerClient(_assistant_root(tmp_path), "generation-a")
    worker.configure_host_services(registry, runtime)
    try:
        worker.start()
        worker.wait_until_loaded(timeout=5)
        tool = registry.get("fixture_echo")
        assert tool is not None and tool.requires_confirmation is False
        result = registry.prepare_or_execute("fixture_echo", {"value": "direct"})
        assert not isinstance(result, PendingToolAction)
        assert result.success and result.content == {"echo": "direct"}
    finally:
        worker.close()
