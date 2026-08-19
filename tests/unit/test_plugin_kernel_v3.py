from __future__ import annotations

import json
import shutil
import time
from pathlib import Path

import pytest

from app.core_host.plugin_worker_runtime import PluginWorkerRuntime, WorkerRuntimeError
from app.plugins.kernel import CallbackRegistry, EffectScope, PluginKernelError


FIXTURE_ROOT = Path(__file__).parents[1] / "fixtures" / "runtime_v2" / "plugin_kernel_v3"


def _fixture_root(tmp_path: Path) -> Path:
    root = tmp_path / "assistant"
    shutil.copytree(FIXTURE_ROOT / "plugins", root / "plugins")
    return root


def _empty_root(tmp_path: Path) -> Path:
    root = tmp_path / "assistant"
    (root / "plugins").mkdir(parents=True)
    (root / "plugins" / "__init__.py").write_text("", encoding="utf-8")
    return root


def _write_plugin(
    root: Path,
    directory: str,
    manifest: str,
    source: str,
) -> Path:
    plugin_root = root / "plugins" / directory
    plugin_root.mkdir(parents=True)
    (plugin_root / "__init__.py").write_text("", encoding="utf-8")
    (plugin_root / "plugin.yaml").write_text(manifest.strip(), encoding="utf-8")
    (plugin_root / "plugin.py").write_text(source.strip(), encoding="utf-8")
    return plugin_root


def _plugins(snapshot: dict[str, object]) -> dict[str, dict[str, object]]:
    return {
        item["pluginId"]: item
        for item in snapshot["plugins"]
        if isinstance(item, dict)
    }


def _service_call(
    runtime: PluginWorkerRuntime,
    service_key: str,
    method: str,
    *args: object,
) -> object:
    return runtime.handle(
        "service.call",
        {"serviceKey": service_key, "method": method, "args": list(args)},
    )


def test_callback_registered_after_activation_inherits_plugin_lifecycle() -> None:
    callbacks = CallbackRegistry()
    scope = EffectScope("com.example.dynamic-callback")
    callbacks.activate_plugin("com.example.dynamic-callback")

    handle, _dispose = callbacks.register(
        "com.example.dynamic-callback",
        "tools.handler",
        lambda arguments: {"echo": arguments["value"]},
        scope,
    )

    assert callbacks.invoke(handle, "tools.handler", [{"value": "hello"}]) == {
        "echo": "hello"
    }
    callbacks.deactivate_plugin("com.example.dynamic-callback")
    with pytest.raises(PluginKernelError, match="CALLBACK_INACTIVE"):
        callbacks.invoke(handle, "tools.handler", [{"value": "stale"}])
    scope.dispose()
    with pytest.raises(PluginKernelError, match="CALLBACK_INVALID"):
        callbacks.invoke(handle, "tools.handler", [{"value": "removed"}])


def test_weather_umbrella_activate_wait_restore_and_use_new_service_instance(
    tmp_path: Path,
) -> None:
    root = _fixture_root(tmp_path)
    runtime = PluginWorkerRuntime(root, "generation-v3")
    kernel = None
    try:
        snapshot = runtime.initialize()
        kernel = runtime._kernel
        by_id = _plugins(snapshot)
        assert by_id["com.example.weather-plugin"]["state"] == "active"
        assert by_id["com.example.umbrella-plugin"]["state"] == "active"

        first_weather = _service_call(runtime, "com.example.weather", "current")
        first_umbrella = _service_call(runtime, "com.example.umbrella", "status")
        assert first_umbrella["weatherInstanceId"] == first_weather["instanceId"]

        _service_call(runtime, "com.example.weather", "set_raining", True)
        raining = _service_call(runtime, "com.example.umbrella", "status")
        assert raining == {
            "weatherInstanceId": first_weather["instanceId"],
            "raining": True,
            "eventCount": 1,
        }

        disabled = runtime.handle(
            "lifecycle.set_enabled",
            {"pluginId": "com.example.weather-plugin", "enabled": False},
        )
        by_id = _plugins(disabled)
        assert by_id["com.example.weather-plugin"]["state"] == "disabled"
        assert by_id["com.example.umbrella-plugin"]["state"] == "waiting"
        assert by_id["com.example.weather-plugin"]["effectCount"] == 0
        assert by_id["com.example.umbrella-plugin"]["effectCount"] == 0
        with pytest.raises(WorkerRuntimeError, match="SERVICE_MISSING"):
            _service_call(runtime, "com.example.umbrella", "status")

        restored = runtime.handle(
            "lifecycle.set_enabled",
            {"pluginId": "com.example.weather-plugin", "enabled": True},
        )
        by_id = _plugins(restored)
        assert by_id["com.example.weather-plugin"]["state"] == "active"
        assert by_id["com.example.umbrella-plugin"]["state"] == "active"
        second_weather = _service_call(runtime, "com.example.weather", "current")
        second_umbrella = _service_call(runtime, "com.example.umbrella", "status")
        assert second_weather["instanceId"] != first_weather["instanceId"]
        assert second_umbrella["weatherInstanceId"] == second_weather["instanceId"]
        assert second_umbrella["eventCount"] == 0
    finally:
        runtime.close()
    assert kernel is not None
    assert kernel.snapshot()["services"] == []
    assert kernel.snapshot()["eventHandlerCount"] == 0
    assert kernel.snapshot()["transformHandlerCount"] == 0


def test_generation_private_bridge_calls_unknown_service_without_domain_routing(
    tmp_path: Path,
) -> None:
    from app.core_host.plugin_worker import PluginWorkerClient

    worker = PluginWorkerClient(_fixture_root(tmp_path), "generation-v3")
    try:
        worker.start()
        snapshot = worker.wait_until_loaded(timeout=5)
        assert _plugins(snapshot)["com.example.umbrella-plugin"]["state"] == "active"
        weather = worker.call_service("com.example.weather", "current")
        umbrella = worker.call_service("com.example.umbrella", "status")
        assert umbrella["weatherInstanceId"] == weather["instanceId"]

        disabled = worker.set_plugin_enabled("com.example.weather-plugin", False)
        assert _plugins(disabled)["com.example.umbrella-plugin"]["state"] == "waiting"
        restored = worker.set_plugin_enabled("com.example.weather-plugin", True)
        assert _plugins(restored)["com.example.umbrella-plugin"]["state"] == "active"
        assert (
            worker.call_service("com.example.weather", "current")["instanceId"]
            != weather["instanceId"]
        )
    finally:
        worker.close()
    assert worker.state == "stopped"


def test_hung_v3_shutdown_is_terminated_with_its_generation_worker(tmp_path: Path) -> None:
    from app.core_host.plugin_worker import PluginWorkerClient

    root = _empty_root(tmp_path)
    _write_plugin(
        root,
        "hung_shutdown",
        """
api: 3
id: com.example.hung-shutdown
name: Hung Shutdown
version: 0.1.0
entry: plugin:HungShutdownPlugin
provides: []
requires: []
optional: []
""",
        """
import time

class HungShutdownPlugin:
    def setup(self, _context):
        return None

    def shutdown(self):
        time.sleep(30)
""",
    )
    worker = PluginWorkerClient(root, "generation-v3")
    worker.start()
    snapshot = worker.wait_until_loaded(timeout=5)
    assert _plugins(snapshot)["com.example.hung-shutdown"]["state"] == "active"

    started = time.monotonic()
    worker.close()
    elapsed = time.monotonic() - started

    assert elapsed < 2.0
    assert worker.state == "stopped"


def test_hung_disable_rebuilds_worker_and_restores_other_desired_plugins(
    tmp_path: Path,
) -> None:
    from app.core_host.plugin_worker import PluginWorkerClient

    root = _fixture_root(tmp_path)
    _write_plugin(
        root,
        "hung_shutdown",
        """
api: 3
id: com.example.hung-shutdown
name: Hung Shutdown
version: 0.1.0
entry: plugin:HungShutdownPlugin
provides: []
requires: []
optional: []
""",
        """
import time

class HungShutdownPlugin:
    def setup(self, _context):
        return None

    def shutdown(self):
        time.sleep(30)
""",
    )
    worker = PluginWorkerClient(root, "generation-v3", call_timeout=0.2)
    try:
        worker.start()
        initial = worker.wait_until_loaded(timeout=5)
        assert _plugins(initial)["com.example.hung-shutdown"]["state"] == "active"
        first_token = worker._token
        first_weather = worker.call_service("com.example.weather", "current")

        started = time.monotonic()
        recovered = worker.set_plugin_enabled("com.example.hung-shutdown", False)
        elapsed = time.monotonic() - started

        by_id = _plugins(recovered)
        assert elapsed < 3.0
        assert worker._token != first_token
        assert worker.state == "ready"
        assert by_id["com.example.hung-shutdown"]["state"] == "disabled"
        assert by_id["com.example.weather-plugin"]["state"] == "active"
        assert by_id["com.example.umbrella-plugin"]["state"] == "active"
        second_weather = worker.call_service("com.example.weather", "current")
        assert second_weather["instanceId"] != first_weather["instanceId"]
        umbrella = worker.call_service("com.example.umbrella", "status")
        assert umbrella["weatherInstanceId"] == second_weather["instanceId"]
    finally:
        worker.close()


def test_plugin_settings_boundary_applies_v3_enablement_without_core_restart(
    tmp_path: Path,
) -> None:
    from app.agent.tools import ToolRegistry
    from app.core_host.plugin_settings import PluginSettingsBoundary
    from app.core_host.plugin_worker import PluginWorkerClient

    class Runtime:
        def set_prompt_patches(self, _values) -> None:
            pass

        def set_context_providers(self, _values) -> None:
            pass

    class Session:
        def __init__(self, plugin_worker: PluginWorkerClient) -> None:
            self.plugin_worker = plugin_worker

    root = _fixture_root(tmp_path)
    worker = PluginWorkerClient(root, "generation-v3-settings-boundary")
    worker.configure_host_services(ToolRegistry(), Runtime())
    boundary = PluginSettingsBoundary(
        "generation-v3-settings-boundary",
        "credential",
        root,
        session_provider=lambda: Session(worker),
    )
    try:
        worker.start()
        worker.wait_until_loaded(timeout=5)
        initial = boundary.snapshot()
        disabled = boundary.save(
            initial["revision"],
            {
                "enabledById": {"com.example.weather-plugin": False},
                "settingsById": {},
            },
        )
        assert disabled["changePlan"] == "applied"
        assert disabled["applicationState"] == "applied"
        by_id = _plugins(disabled)
        assert by_id["com.example.weather-plugin"]["state"] == "disabled"
        assert by_id["com.example.umbrella-plugin"]["state"] == "waiting"
        assert worker.state == "ready"

        restored = boundary.save(
            disabled["revision"],
            {
                "enabledById": {"com.example.weather-plugin": True},
                "settingsById": {},
            },
        )
        assert restored["changePlan"] == "applied"
        by_id = _plugins(restored)
        assert by_id["com.example.weather-plugin"]["state"] == "active"
        assert by_id["com.example.umbrella-plugin"]["state"] == "active"
    finally:
        worker.close()


def test_host_tools_and_context_use_generic_calls_and_effect_bound_callbacks(
    tmp_path: Path,
) -> None:
    from app.agent.tools import ToolRegistry
    from app.core_host.plugin_worker import PluginWorkerClient, PluginWorkerError
    from app.llm.prompts.types import ContextRequest

    class Runtime:
        def __init__(self) -> None:
            self.prompt_patches = []
            self.context_providers = []

        def set_prompt_patches(self, values) -> None:
            self.prompt_patches = list(values)

        def set_context_providers(self, values) -> None:
            self.context_providers = list(values)

    root = _empty_root(tmp_path)
    _write_plugin(
        root,
        "host_probe",
        """
api: 3
id: com.example.host-probe
name: Host Probe
version: 0.1.0
entry: plugin:HostProbePlugin
provides: []
requires:
  - sakura.host.tools
  - sakura.host.context
optional: []
""",
        """
class HostProbePlugin:
    def setup(self, context):
        context.get("sakura.host.tools").register(
            {
                "name": "v3_fixture_echo",
                "description": "Echo through an opaque v3 callback handle.",
                "parameters": {
                    "type": "object",
                    "properties": {"value": {"type": "string"}},
                    "required": ["value"],
                    "additionalProperties": False,
                },
                "group": "fixture",
                "risk": "low",
            },
            lambda arguments: {"echo": arguments["value"]},
        )
        context.get("sakura.host.context").register(
            {
                "providerId": "v3_fixture_context",
                "description": "Context through an opaque v3 callback handle.",
                "order": 80,
                "enabled": True,
            },
            lambda request: [{
                "content": "v3-context=" + request["current_input"],
                "priority": 70,
                "budgetHint": 256,
                "label": "Fixture",
            }],
        )
""",
    )
    _write_plugin(
        root,
        "failed_host_registration",
        """
api: 3
id: z.example.failed-host-registration
name: Failed Host Registration
version: 0.1.0
entry: plugin:FailedHostRegistration
provides: []
requires: [sakura.host.tools]
optional: []
""",
        """
class FailedHostRegistration:
    def setup(self, context):
        context.get("sakura.host.tools").register(
            {
                "name": "v3_must_not_survive",
                "description": "This registration must roll back.",
                "parameters": {"type": "object", "properties": {}},
            },
            lambda _arguments: {"invalid": True},
        )
        raise RuntimeError("fail after host registration")
""",
    )

    registry = ToolRegistry()
    agent_runtime = Runtime()
    worker = PluginWorkerClient(root, "generation-v3")
    worker.configure_host_services(registry, agent_runtime)
    try:
        worker.start()
        worker.bind_runtime(registry, agent_runtime)
        snapshot = worker.wait_until_loaded(timeout=5)
        by_id = _plugins(snapshot)
        assert by_id["com.example.host-probe"]["state"] == "active"
        assert by_id["z.example.failed-host-registration"]["state"] == "failed"
        assert "cb_" not in repr(snapshot)
        assert worker.wait_until_bound(timeout=5)
        assert registry.get("v3_must_not_survive") is None
        with pytest.raises(PluginWorkerError) as host_service_export:
            worker.call_service("sakura.host.tools", "register", {})
        assert host_service_export.value.code == "SERVICE_METHOD_NOT_EXPORTED"
        with pytest.raises(PluginWorkerError) as fabricated_callback:
            worker.invoke_callback("cb_00000000000000000000000000000000", "tools.handler", {})
        assert fabricated_callback.value.code == "CALLBACK_INVALID"

        tool = registry.get("v3_fixture_echo")
        assert tool is not None and tool.handler is not None
        result = registry.prepare_or_execute("v3_fixture_echo", {"value": "hello"})
        assert result.success is True
        assert result.content == {"echo": "hello"}

        assert len(agent_runtime.context_providers) == 1
        provider = agent_runtime.context_providers[0]
        fragments = provider.build_context(ContextRequest(current_input="hello"))
        assert len(fragments) == 1
        assert fragments[0].content == "v3-context=hello"
        assert fragments[0].priority == 70
        assert fragments[0].token_budget == 256

        disabled = worker.set_plugin_enabled("com.example.host-probe", False)
        assert _plugins(disabled)["com.example.host-probe"]["state"] == "disabled"
        assert _plugins(disabled)["com.example.host-probe"]["effectCount"] == 0
        assert registry.get("v3_fixture_echo") is None
        assert agent_runtime.context_providers == []
        with pytest.raises(PluginWorkerError, match="插件调用失败") as stale_tool:
            tool.handler({"value": "stale"})
        assert stale_tool.value.code == "CALLBACK_INVALID"
        with pytest.raises(PluginWorkerError) as stale_context:
            provider.build_context(ContextRequest(current_input="stale"))
        assert stale_context.value.code == "CALLBACK_INVALID"

        restored = worker.set_plugin_enabled("com.example.host-probe", True)
        assert _plugins(restored)["com.example.host-probe"]["state"] == "active"
        assert registry.get("v3_fixture_echo") is not None
        assert len(agent_runtime.context_providers) == 1
    finally:
        worker.close()
    assert registry.get("v3_fixture_echo") is None
    assert agent_runtime.context_providers == []


def test_host_registration_is_not_visible_until_plugin_setup_commits(
    tmp_path: Path,
) -> None:
    from app.agent.tools import ToolRegistry
    from app.core_host.plugin_worker import PluginWorkerClient

    class Runtime:
        def set_prompt_patches(self, _values) -> None:
            pass

        def set_context_providers(self, _values) -> None:
            pass

    root = _empty_root(tmp_path)
    marker = tmp_path / "registration-staged"
    release = tmp_path / "release-setup"
    _write_plugin(
        root,
        "staged_host_registration",
        """
api: 3
id: com.example.staged-host-registration
name: Staged Host Registration
version: 0.1.0
entry: plugin:StagedHostRegistration
provides: []
requires: [sakura.host.tools]
optional: []
""",
        f"""
import time
from pathlib import Path

MARKER = Path({str(marker)!r})
RELEASE = Path({str(release)!r})

class StagedHostRegistration:
    def setup(self, context):
        context.get("sakura.host.tools").register(
            {{
                "name": "v3_staged_tool",
                "description": "Must remain private until setup commits.",
                "parameters": {{"type": "object", "properties": {{}}}},
            }},
            lambda _arguments: {{"ready": True}},
        )
        MARKER.write_text("registered", encoding="utf-8")
        while not RELEASE.exists():
            time.sleep(0.01)
""",
    )
    registry = ToolRegistry()
    runtime = Runtime()
    worker = PluginWorkerClient(root, "generation-v3-staging")
    worker.configure_host_services(registry, runtime)
    try:
        worker.start()
        deadline = time.monotonic() + 3.0
        while not marker.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert marker.exists()
        assert registry.get("v3_staged_tool") is None

        release.write_text("continue", encoding="utf-8")
        snapshot = worker.wait_until_loaded(timeout=5)
        assert _plugins(snapshot)["com.example.staged-host-registration"]["state"] == "active"
        assert registry.get("v3_staged_tool") is not None
    finally:
        release.touch(exist_ok=True)
        worker.close()


def test_setup_failure_and_runtime_conflict_dispose_the_entire_root_scope(
    tmp_path: Path,
) -> None:
    root = _empty_root(tmp_path)
    shutil.copytree(
        FIXTURE_ROOT / "plugins" / "weather",
        root / "plugins" / "weather",
    )
    failed_root = _write_plugin(
        root,
        "failed_setup",
        """
api: 3
id: y.example.failed-setup
name: Failed Setup
version: 0.1.0
entry: plugin:FailedSetupPlugin
provides: [y.example.temporary]
requires: []
optional: []
""",
        """
from pathlib import Path

class Temporary:
    def ping(self):
        return "temporary"

class FailedSetupPlugin:
    def setup(self, context):
        context.on("y.example.failed.event", lambda _payload: None)
        context.effect(lambda: Path(__file__).with_name("disposed.marker").write_text("yes", encoding="utf-8"))
        context.provide("y.example.temporary", Temporary(), exports=("ping",))
        raise RuntimeError("setup failed after partial registration")
""",
    )
    conflict_root = _write_plugin(
        root,
        "runtime_conflict",
        """
api: 3
id: z.example.runtime-conflict
name: Runtime Conflict
version: 0.1.0
entry: plugin:RuntimeConflictPlugin
provides: [z.example.temporary]
requires: []
optional: []
""",
        """
from pathlib import Path

class Temporary:
    def ping(self):
        return "temporary"

class RuntimeConflictPlugin:
    def setup(self, context):
        context.on("z.example.conflict.event", lambda _payload: None)
        context.effect(lambda: Path(__file__).with_name("disposed.marker").write_text("yes", encoding="utf-8"))
        context.provide("z.example.temporary", Temporary(), exports=("ping",))
        context.provide("com.example.weather", object())
""",
    )

    runtime = PluginWorkerRuntime(root, "generation-v3")
    try:
        by_id = _plugins(runtime.initialize())
        assert by_id["com.example.weather-plugin"]["state"] == "active"
        assert by_id["y.example.failed-setup"]["state"] == "failed"
        assert by_id["z.example.runtime-conflict"]["state"] == "conflict"
        assert by_id["y.example.failed-setup"]["effectCount"] == 0
        assert by_id["z.example.runtime-conflict"]["effectCount"] == 0
        assert (failed_root / "disposed.marker").read_text(encoding="utf-8") == "yes"
        assert (conflict_root / "disposed.marker").read_text(encoding="utf-8") == "yes"
        assert runtime._kernel is not None
        diagnostics = runtime._kernel.snapshot()
        assert diagnostics["eventHandlerCount"] == 0
        assert diagnostics["services"] == ["com.example.weather"]
        for service_key in ("y.example.temporary", "z.example.temporary"):
            with pytest.raises(WorkerRuntimeError, match="SERVICE_MISSING"):
                _service_call(runtime, service_key, "ping")
    finally:
        runtime.close()


def test_compatibility_shutdown_runs_before_effects_and_cannot_skip_cleanup(
    tmp_path: Path,
) -> None:
    root = _empty_root(tmp_path)
    active_root = _write_plugin(
        root,
        "shutdown_order",
        """
api: 3
id: com.example.shutdown-order
name: Shutdown Order
version: 0.1.0
entry: plugin:ShutdownOrderPlugin
provides: []
requires: []
optional: []
""",
        """
from pathlib import Path

LOG = Path(__file__).with_name("lifecycle.log")

def append(value):
    with LOG.open("a", encoding="utf-8") as stream:
        stream.write(value + "\\n")

class ShutdownOrderPlugin:
    def setup(self, context):
        context.effect(lambda: append("effect"))

    def shutdown(self):
        append("shutdown")
        raise RuntimeError("compatibility hook failure")
""",
    )
    failed_root = _write_plugin(
        root,
        "failed_shutdown_order",
        """
api: 3
id: com.example.failed-shutdown-order
name: Failed Shutdown Order
version: 0.1.0
entry: plugin:FailedShutdownOrderPlugin
provides: []
requires: []
optional: []
""",
        """
from pathlib import Path

LOG = Path(__file__).with_name("lifecycle.log")

def append(value):
    with LOG.open("a", encoding="utf-8") as stream:
        stream.write(value + "\\n")

class FailedShutdownOrderPlugin:
    def setup(self, context):
        context.effect(lambda: append("effect"))
        raise RuntimeError("setup failed")

    def shutdown(self):
        append("shutdown")
""",
    )

    runtime = PluginWorkerRuntime(root, "generation-v3")
    try:
        snapshot = runtime.initialize()
        by_id = _plugins(snapshot)
        assert by_id["com.example.shutdown-order"]["state"] == "active"
        assert by_id["com.example.failed-shutdown-order"]["state"] == "failed"
        assert (failed_root / "lifecycle.log").read_text(encoding="utf-8").splitlines() == [
            "shutdown",
            "effect",
        ]

        disabled = runtime.handle(
            "lifecycle.set_enabled",
            {"pluginId": "com.example.shutdown-order", "enabled": False},
        )
        assert _plugins(disabled)["com.example.shutdown-order"]["effectCount"] == 0
        assert (active_root / "lifecycle.log").read_text(encoding="utf-8").splitlines() == [
            "shutdown",
            "effect",
        ]
    finally:
        runtime.close()


def test_v3_settings_save_reports_apply_state_and_explicit_reload_rebuilds_plugin(
    tmp_path: Path,
) -> None:
    from app.agent.tools import ToolRegistry
    from app.core_host.plugin_worker import PluginWorkerClient

    class Runtime:
        def set_prompt_patches(self, _values) -> None:
            pass

        def set_context_providers(self, _values) -> None:
            pass

    root = _empty_root(tmp_path)
    plugin_root = _write_plugin(
        root,
        "settings_probe",
        """
api: 3
id: com.example.settings-probe
name: Settings Probe
version: 0.1.0
entry: plugin:SettingsProbePlugin
provides: [com.example.settings-probe]
requires: [sakura.host.settings]
optional: []
""",
        """
class ProbeService:
    def __init__(self, label):
        self.label = label

    def read(self):
        return {"label": self.label}

class SettingsProbePlugin:
    def setup(self, context):
        current = context.config.get()
        context.config.on_change(lambda _values: "restart_required")
        context.get("sakura.host.settings").register(
            {
                "sectionId": "general",
                "title": "General",
                "order": 10,
                "fields": [{
                    "key": "label",
                    "label": "Label",
                    "type": "text",
                    "default": "initial",
                }],
                "actions": [{
                    "actionId": "probe",
                    "label": "Probe",
                    "description": "Return current draft.",
                }],
            },
            load=context.config.get,
            save=context.config.save,
            actions={
                "probe": lambda values: {
                    "values": {"label": values.get("label", "")},
                    "message": "probe-ok",
                },
            },
        )
        context.get("sakura.host.settings").register(
            {
                "sectionId": "advanced",
                "title": "Advanced",
                "order": 20,
                "fields": [{
                    "key": "debug",
                    "label": "Debug",
                    "type": "toggle",
                    "default": False,
                }],
            },
            load=context.config.get,
            save=context.config.save,
        )
        context.provide(
            "com.example.settings-probe",
            ProbeService(current.get("label", "initial")),
            exports=("read",),
        )
""",
    )
    (plugin_root / "config.json").write_text(
        '{"label": "initial", "debug": false}',
        encoding="utf-8",
    )
    user_config = root / "data" / "plugins" / "com.example.settings-probe" / "config.json"
    user_config.parent.mkdir(parents=True)
    user_config.write_text('{"debug": true}', encoding="utf-8")
    registry = ToolRegistry()
    worker = PluginWorkerClient(root, "generation-v3-settings")
    worker.configure_host_services(registry, Runtime())
    try:
        worker.start()
        worker.wait_until_loaded(timeout=5)
        snapshot = worker.settings_snapshot()
        plugin = _plugins(snapshot)["com.example.settings-probe"]
        assert plugin["state"] == "active"
        section = plugin["sections"][0]
        assert section["values"] == {"label": "initial"}
        assert section["reasonCode"] == "READY"
        assert plugin["sections"][1]["values"] == {"debug": True}

        action = worker.settings_action(
            "com.example.settings-probe",
            "general",
            "probe",
            {"label": "draft"},
        )
        assert action == {"values": {"label": "draft"}, "message": "probe-ok"}

        saved = worker.settings_save(
            "com.example.settings-probe",
            "general",
            {"label": "changed"},
        )
        assert saved == {
            "saved": True,
            "applicationState": "restart_required",
            "reasonCode": "CONFIG_RELOAD_REQUIRED",
        }
        assert json.loads(user_config.read_text(encoding="utf-8")) == {
            "debug": True,
            "label": "changed",
        }
        advanced = _plugins(worker.settings_snapshot())["com.example.settings-probe"][
            "sections"
        ][1]
        assert advanced["values"] == {"debug": True}
        assert worker.call_service("com.example.settings-probe", "read") == {
            "label": "initial"
        }
        section = _plugins(worker.settings_snapshot())["com.example.settings-probe"][
            "sections"
        ][0]
        assert section["reasonCode"] == "CONFIG_RELOAD_REQUIRED"
        assert any(
            item["actionId"] == "sakura.reload" for item in section["actions"]
        )

        reloaded = worker.settings_action(
            "com.example.settings-probe",
            "general",
            "sakura.reload",
            {},
        )
        assert reloaded == {"message": "插件已重新加载。"}
        assert worker.call_service("com.example.settings-probe", "read") == {
            "label": "changed"
        }
        section = _plugins(worker.settings_snapshot())["com.example.settings-probe"][
            "sections"
        ][0]
        assert section["reasonCode"] == "READY"
        assert all(item["actionId"] != "sakura.reload" for item in section["actions"])

        worker.set_plugin_enabled("com.example.settings-probe", False)
        assert getattr(worker._host_services, "settings_count") == 0
        assert _plugins(worker.settings_snapshot())["com.example.settings-probe"][
            "sections"
        ] == []
    finally:
        worker.close()


def test_event_transform_registries_are_isolated_and_transform_failure_keeps_value(
    tmp_path: Path,
) -> None:
    root = _empty_root(tmp_path)
    _write_plugin(
        root,
        "hooks",
        """
api: 3
id: com.example.hooks
name: Hook Probe
version: 0.1.0
entry: plugin:HookPlugin
provides: [com.example.hook-probe]
requires: []
optional: []
""",
        """
from types import MappingProxyType

class Probe:
    def __init__(self, context):
        self.context = context
        self.events = 0
        self.after_failure = 0

    def on_event(self, _payload):
        self.events += 1

    def fail_event(self, _payload):
        raise RuntimeError("isolated event failure")

    def continue_event(self, _payload):
        self.after_failure += 1

    @staticmethod
    def mutate_then_fail(value):
        value["value"] = 99
        raise RuntimeError("unreachable")

    @staticmethod
    def next_value(value):
        return {"value": value["value"] + 1}

    def run(self):
        self.context.emit("com.example.shared", {"kind": "event"})
        self.context.emit("com.example.failure", {})
        transformed = self.context.transform("com.example.shared", "start")
        recovered = self.context.transform(
            "com.example.mutable",
            MappingProxyType({"value": 1}),
        )
        return {
            "events": self.events,
            "afterFailure": self.after_failure,
            "transformed": transformed,
            "recovered": dict(recovered),
        }

class HookPlugin:
    def setup(self, context):
        probe = Probe(context)
        context.on("com.example.shared", probe.on_event)
        context.on_transform("com.example.shared", lambda value: value + ":transformed")
        context.on("com.example.failure", probe.fail_event)
        context.on("com.example.failure", probe.continue_event)
        context.on_transform("com.example.mutable", probe.mutate_then_fail)
        context.on_transform("com.example.mutable", probe.next_value)
        context.provide("com.example.hook-probe", probe, exports=("run",))
""",
    )

    runtime = PluginWorkerRuntime(root, "generation-v3")
    try:
        assert _plugins(runtime.initialize())["com.example.hooks"]["state"] == "active"
        assert _service_call(runtime, "com.example.hook-probe", "run") == {
            "events": 1,
            "afterFailure": 1,
            "transformed": "start:transformed",
            "recovered": {"value": 2},
        }
    finally:
        runtime.close()


def test_active_runtime_conflict_disposes_offender_and_exports_are_explicit(
    tmp_path: Path,
) -> None:
    root = _empty_root(tmp_path)
    shutil.copytree(
        FIXTURE_ROOT / "plugins" / "weather",
        root / "plugins" / "weather",
    )
    _write_plugin(
        root,
        "late_conflict",
        """
api: 3
id: com.example.late-conflict
name: Late Conflict
version: 0.1.0
entry: plugin:LateConflictPlugin
provides: [com.example.late-conflict]
requires: []
optional: []
""",
        """
class LateConflictService:
    def __init__(self, context):
        self.context = context

    def trigger(self):
        self.context.provide("com.example.weather", object())

    def hidden(self):
        return "must not cross the Bridge"

    def spoof_host_event(self):
        self.context.emit("sakura.host.message.received", {})

class LateConflictPlugin:
    def setup(self, context):
        context.provide(
            "com.example.late-conflict",
            LateConflictService(context),
            exports=("trigger", "spoof_host_event"),
        )
""",
    )

    runtime = PluginWorkerRuntime(root, "generation-v3")
    try:
        by_id = _plugins(runtime.initialize())
        assert by_id["com.example.late-conflict"]["state"] == "active"
        with pytest.raises(WorkerRuntimeError, match="SERVICE_METHOD_NOT_EXPORTED"):
            _service_call(runtime, "com.example.late-conflict", "hidden")
        with pytest.raises(WorkerRuntimeError, match="HOST_EVENT_RESERVED"):
            _service_call(runtime, "com.example.late-conflict", "spoof_host_event")
        assert _plugins(runtime.handle("status.get", {}))["com.example.late-conflict"]["state"] == "active"

        with pytest.raises(WorkerRuntimeError, match="SERVICE_CONFLICT"):
            _service_call(runtime, "com.example.late-conflict", "trigger")
        by_id = _plugins(runtime.handle("status.get", {}))
        assert by_id["com.example.late-conflict"]["state"] == "conflict"
        assert by_id["com.example.late-conflict"]["effectCount"] == 0
        assert by_id["com.example.weather-plugin"]["state"] == "active"
        with pytest.raises(WorkerRuntimeError, match="SERVICE_MISSING"):
            _service_call(runtime, "com.example.late-conflict", "trigger")
    finally:
        runtime.close()


def test_v3_config_saves_only_plugin_data_and_reports_local_apply_state(
    tmp_path: Path,
) -> None:
    root = _empty_root(tmp_path)
    plugin_root = _write_plugin(
        root,
        "config_probe",
        """
api: 3
id: com.example.config-probe
name: Config Probe
version: 0.1.0
entry: plugin:ConfigProbePlugin
provides: [com.example.config-probe]
requires: []
optional: []
""",
        """
class ConfigProbeService:
    def __init__(self, config):
        self.config = config

    def read(self):
        return self.config.get()

    def save(self, values):
        return {"apply": self.config.save(values), "values": self.config.get()}

    def replace(self, values):
        return {"apply": self.config.replace(values), "values": self.config.get()}

class ConfigProbePlugin:
    def setup(self, context):
        context.config.on_change(lambda _values: "applied")
        context.provide(
            "com.example.config-probe",
            ConfigProbeService(context.config),
            exports=("read", "save", "replace"),
        )
""",
    )
    (plugin_root / "config.json").write_text(
        '{"defaultOnly": true, "label": "default"}',
        encoding="utf-8",
    )

    runtime = PluginWorkerRuntime(root, "generation-v3")
    try:
        runtime.initialize()
        assert _service_call(runtime, "com.example.config-probe", "read") == {
            "defaultOnly": True,
            "label": "default",
        }
        saved = _service_call(
            runtime,
            "com.example.config-probe",
            "save",
            {"label": "user"},
        )
        assert saved == {
            "apply": ["applied"],
            "values": {"defaultOnly": True, "label": "user"},
        }
        assert (root / "data" / "plugins" / "com.example.config-probe" / "config.json").is_file()
        assert (plugin_root / "config.json").read_text(encoding="utf-8") == (
            '{"defaultOnly": true, "label": "default"}'
        )
        replaced = _service_call(
            runtime,
            "com.example.config-probe",
            "replace",
            {"whole": "override"},
        )
        assert replaced == {
            "apply": ["applied"],
            "values": {
                "defaultOnly": True,
                "label": "default",
                "whole": "override",
            },
        }
        user_config = root / "data" / "plugins" / "com.example.config-probe" / "config.json"
        assert json.loads(user_config.read_text(encoding="utf-8")) == {
            "whole": "override"
        }
    finally:
        runtime.close()


def test_declared_conflict_and_dependency_cycle_have_deterministic_states(
    tmp_path: Path,
) -> None:
    root = _empty_root(tmp_path)
    provider_source = """
class Service:
    def ping(self):
        return "ok"

class Provider:
    def setup(self, context):
        context.provide("com.example.duplicate", Service(), exports=("ping",))
"""
    for suffix in ("a", "b"):
        _write_plugin(
            root,
            f"duplicate_{suffix}",
            f"""
api: 3
id: com.example.duplicate-{suffix}
name: Duplicate {suffix.upper()}
version: 0.1.0
entry: plugin:Provider
provides: [com.example.duplicate]
requires: []
optional: []
""",
            provider_source,
        )
    _write_plugin(
        root,
        "cycle_a",
        """
api: 3
id: com.example.cycle-a-plugin
name: Cycle A
version: 0.1.0
entry: plugin:CycleA
provides: [com.example.cycle-a]
requires: [com.example.cycle-b]
optional: []
""",
        """
class CycleA:
    def setup(self, context):
        context.provide("com.example.cycle-a", object())
""",
    )
    _write_plugin(
        root,
        "cycle_b",
        """
api: 3
id: com.example.cycle-b-plugin
name: Cycle B
version: 0.1.0
entry: plugin:CycleB
provides: [com.example.cycle-b]
requires: [com.example.cycle-a]
optional: []
""",
        """
class CycleB:
    def setup(self, context):
        context.provide("com.example.cycle-b", object())
""",
    )

    runtime = PluginWorkerRuntime(root, "generation-v3")
    try:
        by_id = _plugins(runtime.initialize())
        assert by_id["com.example.duplicate-a"]["state"] == "conflict"
        assert by_id["com.example.duplicate-b"]["state"] == "conflict"
        assert by_id["com.example.cycle-a-plugin"]["reasonCode"] == "DEPENDENCY_CYCLE"
        assert by_id["com.example.cycle-b-plugin"]["reasonCode"] == "DEPENDENCY_CYCLE"

        recovered = runtime.handle(
            "lifecycle.set_enabled",
            {"pluginId": "com.example.duplicate-a", "enabled": False},
        )
        by_id = _plugins(recovered)
        assert by_id["com.example.duplicate-a"]["state"] == "disabled"
        assert by_id["com.example.duplicate-b"]["state"] == "active"
        assert _service_call(runtime, "com.example.duplicate", "ping") == "ok"
    finally:
        runtime.close()


def test_core_and_generic_bridge_do_not_name_the_unknown_weather_capability() -> None:
    repository = Path(__file__).parents[2]
    implementation_files = (
        repository / "app" / "plugins" / "kernel.py",
        repository / "app" / "plugins" / "host_services.py",
        repository / "app" / "core_host" / "plugin_worker.py",
        repository / "app" / "core_host" / "plugin_worker_runtime.py",
        repository / "app" / "core_host" / "plugin_host_services.py",
    )
    for path in implementation_files:
        assert "com.example.weather" not in path.read_text(encoding="utf-8")
