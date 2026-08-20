from __future__ import annotations

import json
import shutil
import threading
import time
from pathlib import Path

import psutil
import pytest

from app.core_host import plugin_character
from app.core_host.plugin_worker_runtime import PluginWorkerRuntime, WorkerRuntimeError
from app.plugins.kernel import CallbackRegistry, EffectScope, PluginKernelError


FIXTURE_ROOT = Path(__file__).parents[1] / "fixtures" / "runtime_v2" / "plugin_kernel_v3"

_DESCENDANT_PROCESS_CODE = """
import os
import subprocess
import sys
import time
from pathlib import Path

marker = Path(sys.argv[1])
grandchild = subprocess.Popen(
    [sys.executable, "-c", "import time; time.sleep(30)"],
    stdin=subprocess.DEVNULL,
    stdout=subprocess.DEVNULL,
    stderr=subprocess.DEVNULL,
)
with marker.open("a", encoding="ascii") as handle:
    handle.write(f"{os.getpid()},{grandchild.pid}\\n")
    handle.flush()
time.sleep(30)
"""


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


def _process_tree_setup_source(marker_name: str) -> str:
    return f"""
import subprocess
import sys
from pathlib import Path

DESCENDANT_PROCESS_CODE = {_DESCENDANT_PROCESS_CODE!r}

def start_process_tree():
    marker = Path(__file__).parents[2] / {marker_name!r}
    return subprocess.Popen(
        [sys.executable, "-c", DESCENDANT_PROCESS_CODE, str(marker)],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
"""


def _wait_for_process_tree(marker: Path, *, lines: int = 1) -> list[tuple[int, int]]:
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if marker.exists():
            values = [
                tuple(int(value) for value in line.split(",", 1))
                for line in marker.read_text(encoding="ascii").splitlines()
                if line.strip()
            ]
            if len(values) >= lines:
                return values
        time.sleep(0.02)
    raise AssertionError("plugin descendant process tree did not start")


def _process_is_alive(pid: int) -> bool:
    try:
        return psutil.Process(pid).status() != psutil.STATUS_ZOMBIE
    except psutil.NoSuchProcess:
        return False


def _wait_for_worker_recovery(worker: object, failed_token: str) -> None:
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        host_services = getattr(worker, "_host_services", None)
        if (
            getattr(worker, "_token", failed_token) != failed_token
            and getattr(worker, "state", "") == "ready"
            and int(getattr(host_services, "settings_count", 0)) > 0
        ):
            return
        time.sleep(0.02)
    raise AssertionError("plugin worker did not recover its callback registrations")


def test_character_store_caches_only_manifest_path_for_large_resource_sets(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _empty_root(tmp_path)
    package = root / "characters" / "demo"
    assets = package / "assets"
    assets.mkdir(parents=True)
    (package / "card.md").write_text("demo", encoding="utf-8")
    (package / "portrait.png").write_bytes(b"portrait")
    manifest_path = package / "character.json"
    manifest_path.write_text(
        json.dumps(
            {
                "id": "demo",
                "display_name": "Demo",
                "card": "card.md",
                "portrait": {"default": "portrait.png"},
                "extensions": {"com.example.voice": {"revision": 1}},
            }
        ),
        encoding="utf-8",
    )
    for index in range(32):
        (assets / f"reference-{index}.wav").write_bytes(b"wav")

    real_registry = plugin_character.CharacterRegistry
    registry_loads = 0

    def counting_registry(app_root: Path):
        nonlocal registry_loads
        registry_loads += 1
        return real_registry(app_root)

    monkeypatch.setattr(plugin_character, "CharacterRegistry", counting_registry)
    store = plugin_character.PluginCharacterStore(root)

    for index in range(32):
        assert store.resolve_resource("demo", f"assets/reference-{index}.wav") == str(
            (assets / f"reference-{index}.wav").resolve()
        )
    assert registry_loads == 1

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["extensions"]["com.example.voice"]["revision"] = 2
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    assert store.get("com.example.voice", "demo") == {"revision": 2}
    assert registry_loads == 1


def _assert_processes_exit(pids: tuple[int, ...]) -> None:
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline and any(_process_is_alive(pid) for pid in pids):
        time.sleep(0.02)
    assert not [pid for pid in pids if _process_is_alive(pid)]


def _kill_fixture_processes(marker: Path) -> None:
    if not marker.exists():
        return
    pids = {
        int(value)
        for line in marker.read_text(encoding="ascii").splitlines()
        for value in line.split(",")
        if value.strip().isdigit()
    }
    for pid in pids:
        try:
            psutil.Process(pid).kill()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass


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
    marker = root / "hung-close-tree.txt"
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
        _process_tree_setup_source(marker.name)
        + """
import time

class HungShutdownPlugin:
    def setup(self, _context):
        self.process = start_process_tree()

    def shutdown(self):
        time.sleep(30)
""",
    )
    worker = PluginWorkerClient(root, "generation-v3")
    worker.start()
    snapshot = worker.wait_until_loaded(timeout=5)
    assert _plugins(snapshot)["com.example.hung-shutdown"]["state"] == "active"
    child_pid, grandchild_pid = _wait_for_process_tree(marker)[0]

    try:
        started = time.monotonic()
        worker.close()
        elapsed = time.monotonic() - started

        assert elapsed < 2.0
        assert worker.state == "stopped"
        _assert_processes_exit((child_pid, grandchild_pid))
    finally:
        worker.close()
        _kill_fixture_processes(marker)


def test_hung_disable_rebuilds_worker_and_restores_other_desired_plugins(
    tmp_path: Path,
) -> None:
    from app.core_host.plugin_worker import PluginWorkerClient

    root = _fixture_root(tmp_path)
    marker = root / "hung-disable-tree.txt"
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
        _process_tree_setup_source(marker.name)
        + """
import time

class HungShutdownPlugin:
    def setup(self, _context):
        self.process = start_process_tree()

    def shutdown(self):
        time.sleep(30)
""",
    )
    worker = PluginWorkerClient(root, "generation-v3", call_timeout=0.2)
    try:
        worker.start()
        initial = worker.wait_until_loaded(timeout=5)
        assert _plugins(initial)["com.example.hung-shutdown"]["state"] == "active"
        child_pid, grandchild_pid = _wait_for_process_tree(marker)[0]
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
        _assert_processes_exit((child_pid, grandchild_pid))
        second_weather = worker.call_service("com.example.weather", "current")
        assert second_weather["instanceId"] != first_weather["instanceId"]
        umbrella = worker.call_service("com.example.umbrella", "status")
        assert umbrella["weatherInstanceId"] == second_weather["instanceId"]
    finally:
        worker.close()
        _kill_fixture_processes(marker)


def test_hung_service_call_rebuilds_worker_without_retrying_the_call(
    tmp_path: Path,
) -> None:
    from app.core_host.plugin_worker import PluginWorkerClient, PluginWorkerError

    root = _fixture_root(tmp_path)
    marker = root / "hung-service-call.txt"
    tree_marker = root / "hung-service-tree.txt"
    _write_plugin(
        root,
        "hung_service",
        """
api: 3
id: com.example.hung-service
name: Hung Service
version: 0.1.0
entry: plugin:HungServicePlugin
provides: [com.example.hung-service]
requires: []
optional: []
""",
        _process_tree_setup_source(tree_marker.name)
        + """
import time
from pathlib import Path

class HungService:
    def __init__(self, marker):
        self.marker = marker

    def block(self):
        with self.marker.open("a", encoding="utf-8") as handle:
            handle.write("called\\n")
        time.sleep(30)

class HungServicePlugin:
    def setup(self, context):
        self.process = start_process_tree()
        context.provide(
            "com.example.hung-service",
            HungService(Path(__file__).parents[2] / "hung-service-call.txt"),
            exports=("block",),
        )
""",
    )
    worker = PluginWorkerClient(root, "generation-v3-service-timeout", call_timeout=0.2)
    try:
        worker.start()
        worker.wait_until_loaded(timeout=5)
        child_pid, grandchild_pid = _wait_for_process_tree(tree_marker)[0]
        first_token = worker._token
        first_weather = worker.call_service("com.example.weather", "current")

        with pytest.raises(PluginWorkerError) as timed_out:
            worker.call_service("com.example.hung-service", "block")

        assert timed_out.value.code == "PLUGIN_CALL_TIMEOUT"
        assert worker._token != first_token
        assert worker.state == "ready"
        assert marker.read_text(encoding="utf-8").splitlines() == ["called"]
        _assert_processes_exit((child_pid, grandchild_pid))
        _wait_for_process_tree(tree_marker, lines=2)
        recovered = worker.call_service("com.example.weather", "current")
        assert recovered["instanceId"] != first_weather["instanceId"]
        assert _plugins(worker.public_snapshot())["com.example.hung-service"]["state"] == "active"
    finally:
        worker.close()
        _kill_fixture_processes(tree_marker)


def test_hung_callback_and_event_rebuild_worker_without_replaying_handlers(
    tmp_path: Path,
) -> None:
    from app.agent.tools import ToolRegistry
    from app.core_host.plugin_worker import PluginWorkerClient, PluginWorkerError

    class Runtime:
        def set_prompt_patches(self, _values) -> None:
            pass

        def set_context_providers(self, _values) -> None:
            pass

    root = _fixture_root(tmp_path)
    callback_marker = root / "hung-callback.txt"
    event_marker = root / "hung-event.txt"
    tree_marker = root / "hung-callback-tree.txt"
    _write_plugin(
        root,
        "hung_callback",
        """
api: 3
id: com.example.hung-callback
name: Hung Callback
version: 0.1.0
entry: plugin:HungCallbackPlugin
provides: []
requires: [sakura.host.settings]
optional: []
""",
        _process_tree_setup_source(tree_marker.name)
        + """
import time
from pathlib import Path

class HungCallbackPlugin:
    def setup(self, context):
        self.process = start_process_tree()
        context.on("sakura.host.hung", self.hung_event)
        context.get("sakura.host.settings").register(
            {
                "sectionId": "data",
                "title": "Data",
                "collections": [{
                    "collectionId": "rows",
                    "title": "Rows",
                    "columns": [{"key": "value", "label": "Value", "type": "string"}],
                }],
            },
            collections={
                "rows": {
                    "query": self.hung_query,
                    "create": None,
                    "update": None,
                    "delete": None,
                },
            },
        )

    def hung_query(self, _request):
        marker = Path(__file__).parents[2] / "hung-callback.txt"
        with marker.open("a", encoding="utf-8") as handle:
            handle.write("called\\n")
        time.sleep(30)

    def hung_event(self, _payload):
        marker = Path(__file__).parents[2] / "hung-event.txt"
        with marker.open("a", encoding="utf-8") as handle:
            handle.write("called\\n")
        time.sleep(30)
""",
    )
    worker = PluginWorkerClient(root, "generation-v3-callback-timeout", call_timeout=0.2)
    worker.configure_host_services(ToolRegistry(), Runtime())
    try:
        worker.start()
        worker.wait_until_loaded(timeout=5)
        first_tree = _wait_for_process_tree(tree_marker)[0]
        first_token = worker._token

        with pytest.raises(PluginWorkerError) as callback_timeout:
            worker.settings_collection(
                "query",
                "com.example.hung-callback",
                "data",
                "rows",
                {"cursor": None, "limit": 1, "search": "", "filters": {}},
            )
        assert callback_timeout.value.code == "PLUGIN_CALL_TIMEOUT"
        _wait_for_worker_recovery(worker, first_token)
        assert callback_marker.read_text(encoding="utf-8").splitlines() == ["called"]
        _assert_processes_exit(first_tree)

        second_tree = _wait_for_process_tree(tree_marker, lines=2)[1]
        second_token = worker._token
        with pytest.raises(PluginWorkerError) as event_timeout:
            worker.emit_event("sakura.host.hung", {})
        assert event_timeout.value.code == "PLUGIN_CALL_TIMEOUT"
        _wait_for_worker_recovery(worker, second_token)
        assert event_marker.read_text(encoding="utf-8").splitlines() == ["called"]
        _assert_processes_exit(second_tree)
        assert "instanceId" in worker.call_service("com.example.weather", "current")
    finally:
        worker.close()
        _kill_fixture_processes(tree_marker)


def test_quiescing_worker_timeout_never_spawns_shutdown_replacement(tmp_path: Path) -> None:
    from app.core_host.plugin_worker import PluginWorkerClient, PluginWorkerError

    root = _fixture_root(tmp_path)
    marker = root / "quiescing-service-call.txt"
    _write_plugin(
        root,
        "quiescing_hung_service",
        """
api: 3
id: com.example.quiescing-hung-service
name: Quiescing Hung Service
version: 0.1.0
entry: plugin:HungServicePlugin
provides: [com.example.quiescing-hung-service]
requires: []
optional: []
""",
        """
import time
from pathlib import Path

class HungService:
    def block(self):
        marker = Path(__file__).parents[2] / "quiescing-service-call.txt"
        marker.write_text("called", encoding="utf-8")
        time.sleep(30)

class HungServicePlugin:
    def setup(self, context):
        context.provide(
            "com.example.quiescing-hung-service",
            HungService(),
            exports=("block",),
        )
""",
    )
    worker = PluginWorkerClient(root, "generation-v3-quiescing", call_timeout=0.2)
    try:
        worker.start()
        worker.wait_until_loaded(timeout=5)
        first_token = worker._token
        first_process = worker._process
        worker.quiesce()

        with pytest.raises(PluginWorkerError) as failed:
            worker.call_service("com.example.quiescing-hung-service", "block")

        assert failed.value.code == "GENERATION_INVALIDATED"
        assert marker.read_text(encoding="utf-8") == "called"
        assert worker._token == first_token
        assert worker._process is first_process
        assert first_process is not None and first_process.poll() is not None
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


def test_generation_bound_artifact_is_committed_and_released_with_plugin_effect(
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
    _write_plugin(
        root,
        "artifact_probe",
        """
api: 3
id: com.example.artifact-probe
name: Artifact Probe
version: 0.1.0
entry: plugin:ArtifactProbePlugin
provides: [com.example.artifact-probe]
requires: [sakura.host.artifacts]
optional: []
""",
        """
import threading
from pathlib import Path

class ArtifactProbeService:
    def __init__(self, descriptor, artifacts):
        self.descriptor = descriptor
        self.artifacts = artifacts

    def read(self):
        return self.descriptor

    def background_allocate(self):
        observed = []

        def run():
            try:
                self.artifacts.allocate({"mediaType": "audio/wav", "suffix": ".wav"})
            except Exception as error:
                observed.append(getattr(error, "code", type(error).__name__))

        thread = threading.Thread(target=run)
        thread.start()
        thread.join()
        return observed[0] if observed else "UNEXPECTED_SUCCESS"

class ArtifactProbePlugin:
    def setup(self, context):
        artifacts = context.get("sakura.host.artifacts")
        allocated = artifacts.allocate({"mediaType": "audio/wav", "suffix": ".wav"})
        Path(allocated["path"]).write_bytes(b"RIFF-fixture")
        committed = artifacts.commit(allocated["artifactId"])
        context.provide(
            "com.example.artifact-probe",
            ArtifactProbeService(committed, artifacts),
            exports=("read", "background_allocate"),
        )
""",
    )
    worker = PluginWorkerClient(root, "generation-v3-artifact")
    worker.configure_host_services(ToolRegistry(), Runtime())
    try:
        worker.start()
        snapshot = worker.wait_until_loaded(timeout=5)
        assert _plugins(snapshot)["com.example.artifact-probe"]["state"] == "active"
        descriptor = worker.call_service("com.example.artifact-probe", "read")
        assert descriptor["mediaType"] == "audio/wav"
        assert descriptor["byteLength"] == len(b"RIFF-fixture")
        assert "path" not in descriptor
        assert getattr(worker._host_services, "artifact_count") == 1
        cache_root = root / "data" / "cache" / "plugin-artifacts"
        assert len(list(cache_root.rglob("payload.wav"))) == 1
        live = worker._request("status.get", {})
        assert _plugins(live)["com.example.artifact-probe"]["effectCount"] == 1

        assert (
            worker.call_service(
                "com.example.artifact-probe",
                "background_allocate",
            )
            == "HOST_CALL_THREAD_INVALID"
        )
        assert worker.call_service("com.example.artifact-probe", "read") == descriptor

        disabled = worker.set_plugin_enabled("com.example.artifact-probe", False)
        assert _plugins(disabled)["com.example.artifact-probe"]["effectCount"] == 0
        assert getattr(worker._host_services, "artifact_count") == 1
        assert worker.resolve_committed_artifact(descriptor["artifactId"]).byte_length == len(
            b"RIFF-fixture"
        )
        assert worker.release_committed_artifact(descriptor["artifactId"]) is True
        assert getattr(worker._host_services, "artifact_count") == 0
        assert list(cache_root.rglob("payload.wav")) == []
    finally:
        worker.close()


def test_character_host_service_scopes_extensions_and_resolves_package_resources(
    tmp_path: Path,
) -> None:
    from app.agent.tools import ToolRegistry
    from app.core_host.plugin_worker import PluginWorkerClient, PluginWorkerError

    class Runtime:
        def set_prompt_patches(self, _values) -> None:
            pass

        def set_context_providers(self, _values) -> None:
            pass

    root = _empty_root(tmp_path)
    character_root = root / "characters" / "demo"
    (character_root / "assets").mkdir(parents=True)
    (character_root / "card.md").write_text("demo", encoding="utf-8")
    (character_root / "portrait.png").write_bytes(b"portrait")
    resource = character_root / "assets" / "reference.txt"
    resource.write_text("reference", encoding="utf-8")
    manifest_path = character_root / "character.json"
    manifest_path.write_text(
        json.dumps(
            {
                "id": "demo",
                "display_name": "Demo",
                "card": "card.md",
                "portrait": {"default": "portrait.png"},
                "extensions": {
                    "com.example.character-probe": {"label": "before"},
                    "com.example.other": {"private": "preserved"},
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    _write_plugin(
        root,
        "character_probe",
        """
api: 3
id: com.example.character-probe
name: Character Probe
version: 0.1.0
entry: plugin:CharacterProbePlugin
provides: [com.example.character-probe]
requires: [sakura.host.character]
optional: []
""",
        """
class CharacterProbeService:
    def __init__(self, character, result):
        self.character = character
        self.result = result

    def read(self):
        return self.result

    def escape(self):
        return self.character.resolve_resource("demo", "../outside.txt")

class CharacterProbePlugin:
    def setup(self, context):
        character = context.get("sakura.host.character")
        before = character.get("demo")
        after = character.update("demo", {"enabled": True})
        resolved = character.resolve_resource("demo", "assets/reference.txt")
        context.provide(
            "com.example.character-probe",
            CharacterProbeService(character, {
                "before": before,
                "after": after,
                "resolved": resolved,
            }),
            exports=("read", "escape"),
        )
""",
    )
    worker = PluginWorkerClient(root, "generation-v3-character")
    worker.configure_host_services(ToolRegistry(), Runtime())
    try:
        worker.start()
        snapshot = worker.wait_until_loaded(timeout=5)
        assert _plugins(snapshot)["com.example.character-probe"]["state"] == "active"
        result = worker.call_service("com.example.character-probe", "read")
        assert result == {
            "before": {"label": "before"},
            "after": {"label": "before", "enabled": True},
            "resolved": str(resource.resolve()),
        }
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        assert manifest["extensions"] == {
            "com.example.character-probe": {"label": "before", "enabled": True},
            "com.example.other": {"private": "preserved"},
        }
        with pytest.raises(PluginWorkerError) as escaped:
            worker.call_service("com.example.character-probe", "escape")
        assert escaped.value.code == "CHARACTER_RESOURCE_INVALID"
    finally:
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
                "surface": "voice",
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
        voice_sections = worker.settings_sections("voice")
        assert len(voice_sections) == 1
        assert voice_sections[0]["pluginId"] == "com.example.settings-probe"
        assert voice_sections[0]["sectionId"] == "general"

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


def test_v3_settings_collection_is_bounded_generic_and_effect_scoped(
    tmp_path: Path,
) -> None:
    from app.agent.tools import ToolRegistry
    from app.core_host.plugin_settings import PluginSettingsBoundary
    from app.core_host.plugin_worker import PluginWorkerClient, PluginWorkerError

    class Runtime:
        def set_prompt_patches(self, _values) -> None:
            pass

        def set_context_providers(self, _values) -> None:
            pass

    root = _empty_root(tmp_path)
    _write_plugin(
        root,
        "collection_probe",
        """
api: 3
id: com.example.collection-probe
name: Collection Probe
version: 0.1.0
entry: plugin:CollectionProbePlugin
provides: []
requires: [sakura.host.settings]
optional: []
""",
        """
class CollectionProbePlugin:
    def setup(self, context):
        self.items = {
            "seed": {"content": "seed value", "layer": "core"},
        }
        self.sequence = 0
        context.get("sakura.host.settings").register(
            {
                "sectionId": "data",
                "title": "Data",
                "collections": [{
                    "collectionId": "entries",
                    "title": "Entries",
                    "description": "Generic collection fixture.",
                    "columns": [
                        {"key": "content", "label": "Content", "type": "string"},
                        {"key": "layer", "label": "Layer", "type": "string"},
                    ],
                    "fields": [
                        {"key": "content", "label": "Content", "type": "text", "required": True},
                        {"key": "layer", "label": "Layer", "type": "select", "required": True,
                         "options": [
                             {"label": "Core", "value": "core"},
                             {"label": "Daily", "value": "daily"},
                         ]},
                    ],
                    "filters": [{
                        "key": "layer",
                        "label": "Layer",
                        "options": [
                            {"label": "Core", "value": "core"},
                            {"label": "Daily", "value": "daily"},
                        ],
                    }],
                    "searchable": True,
                    "pageSize": 2,
                    "deleteConfirmation": "Delete this entry?",
                }],
            },
            collections={
                "entries": {
                    "query": self.query,
                    "create": self.create,
                    "update": self.update,
                    "delete": self.delete,
                },
            },
        )

    def item(self, item_id):
        return {"itemId": item_id, "values": dict(self.items[item_id])}

    def query(self, request):
        values = list(self.items.items())
        search = request["search"].lower()
        if search:
            values = [(key, value) for key, value in values if search in value["content"].lower()]
        layer = request["filters"].get("layer")
        if layer:
            values = [(key, value) for key, value in values if value["layer"] == layer]
        offset = int(request["cursor"] or "0")
        page = values[offset:offset + request["limit"]]
        next_offset = offset + len(page)
        return {
            "items": [{"itemId": key, "values": dict(value)} for key, value in page],
            "nextCursor": str(next_offset) if next_offset < len(values) else None,
            "total": len(values),
        }

    def create(self, values):
        self.sequence += 1
        item_id = f"created-{self.sequence}"
        self.items[item_id] = dict(values)
        return self.item(item_id)

    def update(self, item_id, values):
        self.items[item_id].update(values)
        return self.item(item_id)

    def delete(self, item_id):
        return {"deleted": self.items.pop(item_id, None) is not None}
""",
    )
    _write_plugin(
        root,
        "failed_collection_probe",
        """
api: 3
id: com.example.failed-collection
name: Failed Collection
version: 0.1.0
entry: plugin:FailedCollectionPlugin
provides: []
requires: [sakura.host.settings]
optional: []
""",
        """
class FailedCollectionPlugin:
    def setup(self, context):
        context.get("sakura.host.settings").register(
            {
                "sectionId": "failed",
                "title": "Failed",
                "collections": [{
                    "collectionId": "rows",
                    "title": "Rows",
                    "columns": [{"key": "value", "label": "Value", "type": "string"}],
                }],
            },
            collections={
                "rows": {
                    "query": lambda _request: {"items": [], "nextCursor": None, "total": 0},
                    "create": None,
                    "update": None,
                    "delete": None,
                },
            },
        )
        raise RuntimeError("fail after collection registration")
""",
    )

    def start_worker(generation: str) -> PluginWorkerClient:
        client = PluginWorkerClient(root, generation)
        client.configure_host_services(ToolRegistry(), Runtime())
        client.start()
        client.wait_until_loaded(timeout=5)
        return client

    worker = start_worker("generation-v3-collection-a")
    try:
        boundary = PluginSettingsBoundary(
            "generation-v3-collection-a",
            "credential",
            root,
            session_provider=lambda: type("Session", (), {"plugin_worker": worker})(),
        )
        plugin = _plugins(worker.settings_snapshot())["com.example.collection-probe"]
        collection = plugin["sections"][0]["collections"][0]
        assert collection["collectionId"] == "entries"
        assert collection["canCreate"] is True
        assert "cb_" not in repr(collection)
        assert getattr(worker._host_services, "settings_count") == 1

        query = worker.settings_collection(
            "query",
            "com.example.collection-probe",
            "data",
            "entries",
            {"cursor": None, "limit": 2, "search": "seed", "filters": {"layer": "core"}},
        )
        assert query == {
            "items": [{"itemId": "seed", "values": {"content": "seed value", "layer": "core"}}],
            "nextCursor": None,
            "total": 1,
        }
        bridged = boundary.handle({
            "id": "collection-query",
            "name": "plugins.collection.query",
            "generationId": "generation-v3-collection-a",
            "generationCredential": "credential",
            "payload": {
                "pluginId": "com.example.collection-probe",
                "sectionId": "data",
                "collectionId": "entries",
                "cursor": None,
                "limit": 2,
                "search": "seed",
                "filters": {},
            },
        })
        assert bridged["ok"] is True
        assert bridged["payload"]["total"] == 1
        created = worker.settings_collection(
            "create",
            "com.example.collection-probe",
            "data",
            "entries",
            {"values": {"content": "new", "layer": "daily"}},
        )
        assert created["itemId"] == "created-1"
        updated = worker.settings_collection(
            "update",
            "com.example.collection-probe",
            "data",
            "entries",
            {"itemId": "created-1", "values": {"content": "changed"}},
        )
        assert updated["values"] == {"content": "changed", "layer": "daily"}
        assert worker.settings_collection(
            "delete",
            "com.example.collection-probe",
            "data",
            "entries",
            {"itemId": "created-1"},
        ) == {"deleted": True}

        with pytest.raises(PluginWorkerError) as unbounded:
            worker.settings_collection(
                "query",
                "com.example.collection-probe",
                "data",
                "entries",
                {"cursor": None, "limit": 101, "search": "", "filters": {}},
            )
        assert unbounded.value.code == "SETTINGS_COLLECTION_QUERY_INVALID"

        worker.reload_plugin("com.example.collection-probe")
        assert worker.settings_collection(
            "query",
            "com.example.collection-probe",
            "data",
            "entries",
            {"cursor": None, "limit": 2, "search": "", "filters": {}},
        )["total"] == 1
        worker.set_plugin_enabled("com.example.collection-probe", False)
        with pytest.raises(PluginWorkerError) as stale:
            worker.settings_collection(
                "query",
                "com.example.collection-probe",
                "data",
                "entries",
                {"cursor": None, "limit": 2, "search": "", "filters": {}},
            )
        assert stale.value.code == "SETTINGS_COLLECTION_INVALID"
        worker.set_plugin_enabled("com.example.collection-probe", True)
    finally:
        worker.close()

    rebuilt = start_worker("generation-v3-collection-b")
    try:
        assert rebuilt.settings_collection(
            "query",
            "com.example.collection-probe",
            "data",
            "entries",
            {"cursor": None, "limit": 2, "search": "", "filters": {}},
        )["total"] == 1
    finally:
        rebuilt.close()


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


def test_worker_rejects_host_event_payload_over_utf8_json_limit(tmp_path: Path) -> None:
    runtime = PluginWorkerRuntime(_empty_root(tmp_path), "generation-v3-event-limit")
    try:
        runtime.initialize()
        with pytest.raises(WorkerRuntimeError) as oversized:
            runtime.handle(
                "event.emit",
                {
                    "eventType": "sakura.host.fixture",
                    "payload": {"content": "🌸" * 16_384},
                },
            )
        assert oversized.value.code == "EVENT_INVALID"
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


def test_plugin_data_path_is_private_persistent_and_rejects_escape(
    tmp_path: Path,
) -> None:
    root = _empty_root(tmp_path)
    _write_plugin(
        root,
        "data_path_probe",
        """
api: 3
id: com.example.data-path-probe
name: Data Path Probe
version: 0.1.0
entry: plugin:DataPathProbePlugin
provides: [com.example.data-path-probe]
requires: []
optional: []
""",
        """
class DataPathProbe:
    def __init__(self, context):
        self.context = context

    def write(self):
        target = self.context.data_path("cache/value.txt")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("persistent", encoding="utf-8")
        return target.read_text(encoding="utf-8")

    def escape(self):
        return str(self.context.data_path("../outside.txt"))


class DataPathProbePlugin:
    def setup(self, context):
        context.provide(
            "com.example.data-path-probe",
            DataPathProbe(context),
            exports=("write", "escape"),
        )
""",
    )
    runtime = PluginWorkerRuntime(root, "generation-v3")
    try:
        runtime.initialize()
        assert _service_call(runtime, "com.example.data-path-probe", "write") == "persistent"
        assert (
            root
            / "data"
            / "plugins"
            / "com.example.data-path-probe"
            / "cache"
            / "value.txt"
        ).read_text(encoding="utf-8") == "persistent"
        with pytest.raises(WorkerRuntimeError, match="PLUGIN_DATA_PATH_INVALID"):
            _service_call(runtime, "com.example.data-path-probe", "escape")
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


def test_generic_collection_bridge_does_not_name_a_memory_implementation() -> None:
    repository = Path(__file__).parents[2]
    implementation_files = (
        repository / "app" / "plugins" / "host_services.py",
        repository / "app" / "core_host" / "plugin_settings.py",
        repository / "app" / "core_host" / "plugin_worker.py",
        repository / "app" / "core_host" / "plugin_host_services.py",
        repository / "desktop" / "src-tauri" / "src" / "plugin_settings.rs",
        repository / "desktop" / "frontend" / "settings" / "plugin-runtime.js",
    )
    generic_source = "\n".join(
        path.read_text(encoding="utf-8").lower()
        for path in implementation_files
    )
    for forbidden in ("mem0", "memory.query", "memory.search", "memory.collection"):
        assert forbidden not in generic_source
