from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from app.plugins.kernel import PluginKernelManager
from app.plugins.models import PLUGIN_API_V3_VERSION, PluginSpec


def _write_plugin(
    app_root: Path,
    directory: str,
    plugin_id: str,
    source: str,
    *,
    provides: tuple[str, ...] = (),
    requires: tuple[str, ...] = (),
    enabled: bool = True,
) -> PluginSpec:
    plugin_root = app_root / "plugins" / directory
    plugin_root.mkdir(parents=True)
    (plugin_root / "plugin.py").write_text(source.strip(), encoding="utf-8")
    manifest = [
        "api: 3",
        f"id: {plugin_id}",
        f"name: {plugin_id}",
        "version: 1.0.0",
        "entry: plugin:Plugin",
        f"enabled: {'true' if enabled else 'false'}",
        f"provides: [{', '.join(provides)}]",
        f"requires: [{', '.join(requires)}]",
    ]
    (plugin_root / "plugin.yaml").write_text(
        "\n".join(manifest) + "\n",
        encoding="utf-8",
    )
    return PluginSpec(
        entry="plugin:Plugin",
        enabled=enabled,
        plugin_id=plugin_id,
        name=plugin_id,
        version="1.0.0",
        api_version=PLUGIN_API_V3_VERSION,
        provides=provides,
        requires=requires,
        plugin_root=plugin_root,
        source="bundled",
    )


def _by_id(snapshot: dict[str, object]) -> dict[str, dict[str, object]]:
    return {
        str(item["pluginId"]): item
        for item in snapshot["plugins"]  # type: ignore[index]
    }


def _wait_until(predicate, timeout: float = 8.0) -> None:  # type: ignore[no-untyped-def]
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.02)
    raise AssertionError("condition did not become true before the deadline")


def test_public_context_is_only_the_small_v3_surface(tmp_path: Path) -> None:
    marker = tmp_path / "surface.json"
    spec = _write_plugin(
        tmp_path,
        "surface",
        "com.example.surface",
        f"""
import json
from pathlib import Path

class Plugin:
    def setup(self, context):
        public = sorted(name for name in dir(context) if not name.startswith("_"))
        Path({str(marker)!r}).write_text(json.dumps(public), encoding="utf-8")
""",
    )

    manager = PluginKernelManager(tmp_path, [spec])
    try:
        assert json.loads(marker.read_text(encoding="utf-8")) == [
            "config",
            "data_path",
            "effect",
            "get",
            "on",
            "provide",
        ]
    finally:
        manager.close()


def test_load_order_is_deterministic_and_topological(tmp_path: Path) -> None:
    marker = tmp_path / "order.txt"
    provider = _write_plugin(
        tmp_path,
        "provider",
        "com.example.provider",
        f"""
from pathlib import Path

MARKER = Path({str(marker)!r})

class Service:
    value = "provider"

class Plugin:
    def setup(self, context):
        with MARKER.open("a", encoding="utf-8") as stream:
            stream.write("provider\\n")
        context.provide("com.example.service", Service())
""",
        provides=("com.example.service",),
    )
    consumer = _write_plugin(
        tmp_path,
        "consumer",
        "com.example.consumer",
        f"""
from pathlib import Path

MARKER = Path({str(marker)!r})

class Plugin:
    def setup(self, context):
        service = context.get("com.example.service")
        with MARKER.open("a", encoding="utf-8") as stream:
            stream.write(service.value + "\\n")
""",
        requires=("com.example.service",),
    )

    manager = PluginKernelManager(tmp_path, [consumer, provider])
    try:
        assert marker.read_text(encoding="utf-8").splitlines() == [
            "provider",
            "provider",
        ]
        assert {item["state"] for item in manager.snapshot()["plugins"]} == {"active"}
    finally:
        manager.close()


def test_missing_dependency_fails_without_importing_plugin(tmp_path: Path) -> None:
    marker = tmp_path / "imported"
    spec = _write_plugin(
        tmp_path,
        "missing",
        "com.example.missing",
        f"""
from pathlib import Path
Path({str(marker)!r}).write_text("imported", encoding="utf-8")

class Plugin:
    def setup(self, context):
        pass
""",
        requires=("com.example.absent",),
    )

    manager = PluginKernelManager(tmp_path, [spec])
    try:
        plugin = _by_id(manager.snapshot())["com.example.missing"]
        assert plugin["state"] == "failed"
        assert plugin["reasonCode"] == "MISSING_SERVICE"
        assert not marker.exists()
    finally:
        manager.close()


def test_dependency_cycle_fails_all_members_without_import(tmp_path: Path) -> None:
    a = _write_plugin(
        tmp_path,
        "a",
        "com.example.a",
        "class Plugin:\n    def setup(self, context):\n        raise AssertionError('not imported')",
        provides=("com.example.a-service",),
        requires=("com.example.b-service",),
    )
    b = _write_plugin(
        tmp_path,
        "b",
        "com.example.b",
        "class Plugin:\n    def setup(self, context):\n        raise AssertionError('not imported')",
        provides=("com.example.b-service",),
        requires=("com.example.a-service",),
    )

    manager = PluginKernelManager(tmp_path, [b, a])
    try:
        plugins = _by_id(manager.snapshot())
        assert {
            plugins["com.example.a"]["reasonCode"],
            plugins["com.example.b"]["reasonCode"],
        } == {"DEPENDENCY_CYCLE"}
    finally:
        manager.close()


def test_declared_service_conflict_fails_providers_without_setup(tmp_path: Path) -> None:
    specs = [
        _write_plugin(
            tmp_path,
            name,
            f"com.example.{name}",
            "class Plugin:\n    def setup(self, context):\n        raise AssertionError('not imported')",
            provides=("com.example.shared",),
        )
        for name in ("alpha", "beta")
    ]

    manager = PluginKernelManager(tmp_path, list(reversed(specs)))
    try:
        plugins = _by_id(manager.snapshot())
        assert {plugin["state"] for plugin in plugins.values()} == {"failed"}
        assert {plugin["reasonCode"] for plugin in plugins.values()} == {
            "SERVICE_CONFLICT"
        }
    finally:
        manager.close()


def test_setup_failure_runs_cleanup_lifo(tmp_path: Path) -> None:
    marker = tmp_path / "cleanup.txt"
    spec = _write_plugin(
        tmp_path,
        "broken",
        "com.example.broken",
        f"""
from pathlib import Path
MARKER = Path({str(marker)!r})

def record(value):
    with MARKER.open("a", encoding="utf-8") as stream:
        stream.write(value + "\\n")

class Plugin:
    def setup(self, context):
        context.effect(lambda: record("first"))
        context.effect(lambda: record("second"))
        raise RuntimeError("setup failed")
""",
    )

    manager = PluginKernelManager(tmp_path, [spec])
    try:
        plugin = _by_id(manager.snapshot())["com.example.broken"]
        assert plugin["state"] == "failed"
        assert plugin["reasonCode"] == "PLUGIN_SETUP_FAILED"
        assert marker.read_text(encoding="utf-8").splitlines() == ["second", "first"]
    finally:
        manager.close()


def test_worker_close_cleans_consumers_then_providers_and_each_stack_lifo(
    tmp_path: Path,
) -> None:
    marker = tmp_path / "close.txt"
    provider = _write_plugin(
        tmp_path,
        "provider",
        "com.example.provider",
        f"""
from pathlib import Path
MARKER = Path({str(marker)!r})
def record(value):
    with MARKER.open("a", encoding="utf-8") as stream:
        stream.write(value + "\\n")
class Plugin:
    def setup(self, context):
        context.effect(lambda: record("provider-first"))
        context.effect(lambda: record("provider-second"))
        context.provide("com.example.service", object())
""",
        provides=("com.example.service",),
    )
    consumer = _write_plugin(
        tmp_path,
        "consumer",
        "com.example.consumer",
        f"""
from pathlib import Path
MARKER = Path({str(marker)!r})
def record(value):
    with MARKER.open("a", encoding="utf-8") as stream:
        stream.write(value + "\\n")
class Plugin:
    def setup(self, context):
        context.get("com.example.service")
        context.effect(lambda: record("consumer-first"))
        context.effect(lambda: record("consumer-second"))
""",
        requires=("com.example.service",),
    )

    manager = PluginKernelManager(tmp_path, [consumer, provider])
    manager.close()
    assert marker.read_text(encoding="utf-8").splitlines() == [
        "consumer-second",
        "consumer-first",
        "provider-second",
        "provider-first",
    ]


def test_event_handler_failure_is_logged_once_and_plugin_stays_active(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spec = _write_plugin(
        tmp_path,
        "events",
        "com.example.events",
        """
class Plugin:
    def setup(self, context):
        def broken(_payload):
            raise RuntimeError("handler failed")
        context.on("sakura.host.message.received", broken)
""",
    )
    logged: list[str] = []
    monkeypatch.setattr(
        "app.plugins.kernel.log_event",
        lambda _source, message, _details: logged.append(message),
    )

    manager = PluginKernelManager(tmp_path, [spec])
    try:
        manager.emit_host_event("sakura.host.message.received", {"text": "one"})
        manager.emit_host_event("sakura.host.message.received", {"text": "two"})
        plugin = _by_id(manager.snapshot())["com.example.events"]
        assert plugin["state"] == "active"
        assert logged == ["插件事件 handler 失败"]
    finally:
        manager.close()


def test_service_exception_propagates_without_changing_plugin_state(
    tmp_path: Path,
) -> None:
    spec = _write_plugin(
        tmp_path,
        "service",
        "com.example.service-plugin",
        """
class Service:
    def fail(self):
        raise RuntimeError("service failed")

class Plugin:
    def setup(self, context):
        context.provide("com.example.service", Service(), exports=("fail",))
""",
        provides=("com.example.service",),
    )

    manager = PluginKernelManager(tmp_path, [spec])
    try:
        with pytest.raises(RuntimeError, match="service failed"):
            manager.call_service("com.example.service", "fail", [])
        assert _by_id(manager.snapshot())["com.example.service-plugin"]["state"] == "active"
    finally:
        manager.close()


def test_enable_disable_replaces_the_whole_worker(tmp_path: Path) -> None:
    from app.core_host.plugin_worker import PluginWorkerClient

    marker = tmp_path / "cleanup.txt"
    _write_plugin(
        tmp_path,
        "managed",
        "com.example.managed",
        f"""
from pathlib import Path
MARKER = Path({str(marker)!r})
def cleanup():
    with MARKER.open("a", encoding="utf-8") as stream:
        stream.write("cleanup\\n")
class Plugin:
    def setup(self, context):
        context.effect(cleanup)
""",
    )
    worker = PluginWorkerClient(tmp_path, "generation-management")
    try:
        worker.start()
        assert _by_id(worker.wait_until_loaded(timeout=5))["com.example.managed"]["state"] == "active"
        first_token = worker._token

        disabled = worker.set_plugin_enabled("com.example.managed", False)
        assert worker._token != first_token
        assert _by_id(disabled)["com.example.managed"]["state"] == "disabled"
        assert marker.read_text(encoding="utf-8").splitlines() == ["cleanup"]

        second_token = worker._token
        enabled = worker.set_plugin_enabled("com.example.managed", True)
        assert worker._token != second_token
        assert _by_id(enabled)["com.example.managed"]["state"] == "active"
    finally:
        worker.close()
    assert marker.read_text(encoding="utf-8").splitlines() == ["cleanup", "cleanup"]


def test_restart_required_config_replaces_worker_immediately(tmp_path: Path) -> None:
    from app.agent.tools import ToolRegistry
    from app.core_host.plugin_worker import PluginWorkerClient

    setups = tmp_path / "setups.txt"
    _write_plugin(
        tmp_path,
        "settings",
        "com.example.settings",
        f"""
from pathlib import Path
SETUPS = Path({str(setups)!r})

class Plugin:
    def setup(self, context):
        with SETUPS.open("a", encoding="utf-8") as stream:
            stream.write("setup\\n")
        context.get("sakura.host.settings").register(
            {{
                "sectionId": "general",
                "title": "General",
                "fields": [{{
                    "key": "label",
                    "label": "Label",
                    "type": "text",
                    "default": "fixture",
                }}],
                "actions": [],
            }},
            load=context.config.get,
            save=context.config.update,
        )
""",
        requires=("sakura.host.settings",),
    )

    class Runtime:
        def set_context_providers(self, _values):  # type: ignore[no-untyped-def]
            return None

    worker = PluginWorkerClient(tmp_path, "generation-config")
    registry = ToolRegistry()
    runtime = Runtime()
    worker.configure_host_services(registry, runtime)
    try:
        worker.start()
        worker.bind_runtime(registry, runtime)
        worker.wait_until_loaded(timeout=5)
        first_token = worker._token
        result = worker.settings_save(
            "com.example.settings",
            "general",
            {"label": "changed"},
        )
        assert result == {
            "saved": True,
            "applicationState": "applied",
            "reasonCode": "READY",
        }
        assert worker._token != first_token
        assert setups.read_text(encoding="utf-8").splitlines() == ["setup", "setup"]
    finally:
        worker.close()


def test_timed_out_call_is_not_replayed_and_rebuild_is_attempted_once(
    tmp_path: Path,
) -> None:
    from app.core_host.plugin_worker import PluginWorkerClient, PluginWorkerError

    calls = tmp_path / "calls.txt"
    setups = tmp_path / "setups.txt"
    _write_plugin(
        tmp_path,
        "timeout",
        "com.example.timeout",
        f"""
import time
from pathlib import Path
CALLS = Path({str(calls)!r})
SETUPS = Path({str(setups)!r})

class Service:
    def hang(self):
        with CALLS.open("a", encoding="utf-8") as stream:
            stream.write("call\\n")
        time.sleep(30)
    def ping(self):
        return "pong"

class Plugin:
    def setup(self, context):
        with SETUPS.open("a", encoding="utf-8") as stream:
            stream.write("setup\\n")
        context.provide(
            "com.example.timeout-service",
            Service(),
            exports=("hang", "ping"),
        )
""",
        provides=("com.example.timeout-service",),
    )
    worker = PluginWorkerClient(
        tmp_path,
        "generation-timeout",
        call_timeout=0.15,
    )
    try:
        worker.start()
        worker.wait_until_loaded(timeout=5)
        first_token = worker._token
        with pytest.raises(PluginWorkerError) as failed:
            worker.call_service("com.example.timeout-service", "hang")
        assert failed.value.code == "PLUGIN_CALL_TIMEOUT"

        _wait_until(lambda: worker._token != first_token)
        worker.wait_until_loaded(timeout=5)
        rebuilt_token = worker._token
        time.sleep(0.4)
        assert worker._token == rebuilt_token
        assert calls.read_text(encoding="utf-8").splitlines() == ["call"]
        assert setups.read_text(encoding="utf-8").splitlines() == ["setup", "setup"]
        assert worker.call_service("com.example.timeout-service", "ping") == "pong"
    finally:
        worker.close()


def test_removed_worker_commands_are_rejected(tmp_path: Path) -> None:
    from app.core_host.plugin_worker_runtime import PluginWorkerRuntime, WorkerRuntimeError

    runtime = PluginWorkerRuntime(tmp_path, "generation-commands")
    try:
        runtime.initialize()
        for command in (
            "hook.transform",
            "session.bind",
            "session.unbind",
            "lifecycle.set_enabled",
            "lifecycle.reload",
        ):
            with pytest.raises(WorkerRuntimeError) as failed:
                runtime.handle(command, {})
            assert failed.value.code == "PLUGIN_COMMAND_UNKNOWN"
    finally:
        runtime.close()
