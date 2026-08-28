from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import sys
import threading
import time
import zipfile
from pathlib import Path

import pytest

from app.agent.tools import ToolRegistry
from app.core_host.plugin_application import PluginApplicationHost
from app.core_host.plugin_settings import PluginSettingsBoundary
from app.plugins.installer import LocalPluginInstaller, PluginInstallError
from app.plugins.inventory import PluginDesiredStateStore, PluginInventory
from app.plugins.runtime_v4 import PluginRuntimeError, PluginRuntimeManager
from app.plugins.sakura_plugin_sdk import PluginApiError, RpcPeer
from app.storage.paths import StoragePaths
from app.storage.runtime_roots import RuntimeRoots


def _roots(tmp_path: Path) -> RuntimeRoots:
    distribution = tmp_path / "distribution"
    user = tmp_path / "user"
    (distribution / "plugins" / "builtin").mkdir(parents=True)
    user.mkdir()
    return RuntimeRoots(distribution, user)


def _wheel(parent: Path, version: str) -> Path:
    normalized = version.replace("-", "_")
    wheel = parent / f"conflict_dep-{normalized}-py3-none-any.whl"
    dist_info = f"conflict_dep-{version}.dist-info"
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr(
            "conflictdep/__init__.py",
            f"__version__ = {version!r}\n",
        )
        archive.writestr(
            f"{dist_info}/METADATA",
            f"Metadata-Version: 2.1\nName: conflict-dep\nVersion: {version}\n",
        )
        archive.writestr(
            f"{dist_info}/WHEEL",
            "Wheel-Version: 1.0\nGenerator: sakura-test\nRoot-Is-Purelib: true\nTag: py3-none-any\n",
        )
        archive.writestr(f"{dist_info}/RECORD", "")
    return wheel


def _plugin_source(
    parent: Path,
    plugin_id: str,
    service_key: str,
    *,
    wheel: Path | None = None,
    requires: tuple[str, ...] = (),
    body: str | None = None,
) -> Path:
    root = parent / plugin_id
    root.mkdir(parents=True)
    manifest_requires = ", ".join(requires)
    (root / "plugin.yaml").write_text(
        (
            "api: 4\n"
            f"id: {plugin_id}\n"
            f"name: {plugin_id}\n"
            "version: 1.0.0\n"
            "entry: plugin:Plugin\n"
            f"provides: [{service_key}]\n"
            f"requires: [{manifest_requires}]\n"
        ),
        encoding="utf-8",
    )
    if wheel is not None:
        (root / "requirements.txt").write_text(str(wheel.resolve()) + "\n", encoding="utf-8")
    (root / "plugin.py").write_text(
        body
        or f"""
import os
import time
import conflictdep

try:
    import app
except ModuleNotFoundError:
    CORE_VISIBLE = False
else:
    CORE_VISIBLE = True

try:
    import plugin_runner_v4
except ModuleNotFoundError:
    RUNNER_VISIBLE = False
else:
    RUNNER_VISIBLE = True

try:
    import sakura_plugin_sdk
except ModuleNotFoundError:
    TRANSPORT_VISIBLE = False
else:
    TRANSPORT_VISIBLE = True

import __main__
import sakura_plugin_api

class Service:
    def __init__(self):
        self.calls = 0

    def info(self):
        return {{
            "version": conflictdep.__version__,
            "pid": os.getpid(),
            "coreVisible": CORE_VISIBLE,
            "runnerVisible": RUNNER_VISIBLE,
            "transportVisible": TRANSPORT_VISIBLE,
            "publicTransportVisible": hasattr(sakura_plugin_api, "RpcPeer"),
            "mainRuntimeVisible": any(
                hasattr(__main__, name)
                for name in ("PluginRunner", "RpcPeer", "PluginContext")
            ),
        }}

    def fail(self):
        raise RuntimeError("fixture failure")

    def slow(self):
        self.calls += 1
        time.sleep(0.2)
        return "late"

    def count(self):
        return self.calls

    def crash(self):
        os._exit(9)

class Plugin:
    def setup(self, context):
        context.provide({service_key!r}, Service(), exports=("info", "fail", "slow", "count", "crash"))
""".strip(),
        encoding="utf-8",
    )
    return root


def _simple_service_body(service_key: str, value: str) -> str:
    return f"""
class Service:
    def ping(self):
        return {value!r}

class Plugin:
    def setup(self, context):
        context.provide({service_key!r}, Service(), exports=("ping",))
""".strip()


def test_production_v4_hot_install_enable_and_uninstall_leave_unrelated_pid_stable(
    tmp_path: Path,
) -> None:
    roots = _roots(tmp_path)
    _plugin_source(
        roots.distribution_root / "plugins/builtin",
        "com.example.stable",
        "com.example.stable",
        body=_simple_service_body("com.example.stable", "stable"),
    )
    source = _plugin_source(
        tmp_path / "source",
        "com.example.installed",
        "com.example.installed",
        body=_simple_service_body("com.example.installed", "installed"),
    )
    application = PluginApplicationHost(
        roots,
        "generation-hot-install",
        ToolRegistry(),
    )
    boundary = PluginSettingsBoundary(
        "generation-hot-install",
        "credential",
        roots,
        application_provider=lambda: application,
    )
    application.start()
    try:
        before = {
            item["pluginId"]: item
            for item in application.public_snapshot()["plugins"]
        }
        assert before["com.example.stable"]["state"] == "active"
        stable_pid = next(
            item["pid"]
            for item in application.application.public_snapshot()["plugins"]
            if item["pluginId"] == "com.example.stable"
        )

        installed = boundary.install(
            boundary.snapshot()["revision"],
            "folder",
            str(source.resolve()),
        )
        after_install = {
            item["pluginId"]: item for item in installed["plugins"]
        }
        assert after_install["com.example.installed"]["state"] == "disabled"
        assert next(
            item["pid"]
            for item in application.application.public_snapshot()["plugins"]
            if item["pluginId"] == "com.example.stable"
        ) == stable_pid

        enabled = boundary.set_enabled(
            installed["revision"],
            installed["installId"],
            True,
        )
        enabled_records = {
            item["pluginId"]: item for item in enabled["plugins"]
        }
        assert enabled_records["com.example.installed"]["state"] == "active"
        assert next(
            item["pid"]
            for item in application.application.public_snapshot()["plugins"]
            if item["pluginId"] == "com.example.stable"
        ) == stable_pid
        assert application.call_service("com.example.installed", "ping") == "installed"

        uninstalled = boundary.uninstall(
            enabled["revision"],
            installed["installId"],
        )
        remaining = {
            item["pluginId"]: item for item in uninstalled["plugins"]
        }
        assert set(remaining) == {"com.example.stable"}
        assert next(
            item["pid"]
            for item in application.application.public_snapshot()["plugins"]
            if item["pluginId"] == "com.example.stable"
        ) == stable_pid
        assert not (
            roots.user_root / "plugins/user/com.example.installed"
        ).exists()
    finally:
        application.close()


def _install_enabled(roots: RuntimeRoots, source: Path) -> None:
    installed = LocalPluginInstaller(roots).install(source.resolve(), "folder")
    PluginDesiredStateStore(roots.user_root).set(installed.plugin_id, True)


def _wait_reason(manager: PluginRuntimeManager, plugin_id: str, reason: str) -> None:
    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline:
        record = next(
            item for item in manager.snapshot()["plugins"] if item["pluginId"] == plugin_id
        )
        if record["reasonCode"] == reason:
            return
        time.sleep(0.01)
    raise AssertionError(manager.snapshot())


def test_v4_plugins_use_distinct_processes_and_conflicting_dependency_roots(
    tmp_path: Path,
) -> None:
    roots = _roots(tmp_path)
    sources = tmp_path / "sources"
    sources.mkdir()
    wheel_v1 = _wheel(sources, "1.0.0")
    wheel_v2 = _wheel(sources, "2.0.0")
    _install_enabled(
        roots,
        _plugin_source(sources, "fixture.one", "fixture.service.one", wheel=wheel_v1),
    )
    _install_enabled(
        roots,
        _plugin_source(sources, "fixture.two", "fixture.service.two", wheel=wheel_v2),
    )

    manager = PluginRuntimeManager(
        roots,
        "generation-v4-isolation",
        PluginInventory(roots).scan().runtime_specs,
    )
    try:
        snapshot = manager.start()
        assert {item["state"] for item in snapshot["plugins"]} == {"active"}
        one = manager.call_service("fixture.service.one", "info")
        two = manager.call_service("fixture.service.two", "info")
        assert one["version"] == "1.0.0"
        assert two["version"] == "2.0.0"
        assert one["pid"] != two["pid"]
        assert one["coreVisible"] is False
        assert two["coreVisible"] is False
        assert one["runnerVisible"] is False
        assert one["transportVisible"] is False
        assert one["publicTransportVisible"] is False
        assert one["mainRuntimeVisible"] is False
        assert "conflictdep" not in sys.modules
        assert importlib.util.find_spec("conflictdep") is None
        paths = StoragePaths(roots.user_root)
        assert paths.plugin_dependency_root_for("fixture.one").is_dir()
        assert paths.plugin_dependency_root_for("fixture.two").is_dir()
    finally:
        manager.close()


def test_bundled_v4_plugin_uses_distribution_dependency_root_offline(
    tmp_path: Path,
) -> None:
    roots = _roots(tmp_path)
    wheel = _wheel(tmp_path, "4.0.0")
    plugin_root = _plugin_source(
        roots.distribution_root / "plugins/builtin",
        "fixture.bundled-dependencies",
        "fixture.bundled-dependencies.service",
        wheel=wheel,
    )
    requirements = plugin_root / "requirements.txt"
    dependency_root = (
        roots.distribution_root
        / "plugins/dependencies/fixture.bundled-dependencies"
    )
    dependency_root.mkdir(parents=True)
    with zipfile.ZipFile(wheel) as archive:
        archive.extractall(dependency_root)
    (dependency_root / ".sakura-dependencies.json").write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "kind": "requirements.txt",
                "fingerprint": hashlib.sha256(requirements.read_bytes()).hexdigest(),
                "python": f"{sys.version_info.major}.{sys.version_info.minor}",
            }
        ),
        encoding="utf-8",
    )

    manager = PluginRuntimeManager(
        roots,
        "generation-bundled-dependencies",
        PluginInventory(roots).scan().runtime_specs,
    )
    try:
        snapshot = manager.start()
        record = snapshot["plugins"][0]
        assert record["state"] == "active"
        assert manager.call_service(
            "fixture.bundled-dependencies.service",
            "info",
        )["version"] == "4.0.0"
        assert not StoragePaths(roots.user_root).plugin_dependency_root_for(
            "fixture.bundled-dependencies"
        ).exists()
    finally:
        manager.close()


def test_v4_application_host_projects_contributions_config_and_explicit_lifecycle(
    tmp_path: Path,
) -> None:
    from app.agent.tools import ToolRegistry
    from app.core_host.plugin_application import PluginApplicationHost
    from app.llm.prompts.types import ContextRequest

    roots = _roots(tmp_path)
    bundled = roots.distribution_root / "plugins" / "builtin"
    _plugin_source(
        bundled,
        "fixture.application",
        "fixture.application.service",
        requires=("sakura.host.tools", "sakura.host.context"),
        body="""
import os

class Service:
    def __init__(self, label): self.label = label
    def info(self): return {"pid": os.getpid(), "label": self.label}
    def apply(self, values):
        self.label = values.get("label", self.label)
        return "restart_required" if values.get("restart") else "applied"

class Plugin:
    def setup(self, context):
        service = Service(context.config.get().get("label", "initial"))
        context.provide("fixture.application.service", service, exports=("info",))
        context.config.on_change(service.apply)
        context.get("sakura.host.tools").register(
            {
                "name": "fixture_v4_echo",
                "description": "Echo one fixture value.",
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
                "providerId": "fixture.v4.context",
                "description": "Fixture v4 context",
                "order": 100,
                "enabled": True,
            },
            lambda request: [{
                "content": "input=" + request["current_input"],
                "priority": 50,
                "budgetHint": 128,
                "label": "Fixture v4",
            }],
        )
""".strip(),
    )
    _plugin_source(
        bundled,
        "fixture.unrelated",
        "fixture.unrelated.service",
        body="""
import os
class Service:
    def info(self): return {"pid": os.getpid()}
class Plugin:
    def setup(self, context):
        context.provide("fixture.unrelated.service", Service(), exports=("info",))
""".strip(),
    )
    _plugin_source(
        bundled,
        "fixture.application-consumer",
        "fixture.application-consumer.service",
        requires=("fixture.application.service",),
        body="""
import os
class Service:
    def info(self): return {"pid": os.getpid()}
class Plugin:
    def setup(self, context):
        context.get("fixture.application.service").info()
        context.provide(
            "fixture.application-consumer.service",
            Service(),
            exports=("info",),
        )
""".strip(),
    )

    class Runtime:
        def __init__(self) -> None:
            self.context_providers = []

        def set_context_providers(self, providers) -> None:
            self.context_providers = list(providers)

    registry = ToolRegistry()
    runtime = Runtime()
    application = PluginApplicationHost(
        roots,
        "generation-v4-application",
        registry,
    )
    session = type(
        "Session",
        (),
        {
            "runtime": runtime,
            "character": type("Character", (), {"id": "fixture"})(),
        },
    )()
    try:
        application.start()
        application.bind_session(session)
        assert application.application.wait_until_loaded(timeout=2.0)
        assert application.application.wait_until_bound(timeout=2.0)
        snapshot = application.settings_snapshot()
        records = {item["pluginId"]: item for item in snapshot["plugins"]}
        assert records["fixture.application"]["state"] == "active"
        assert records["fixture.application"]["supported"] is True

        tool_result = registry.execute("fixture_v4_echo", {"value": "hello"})
        assert tool_result.success is True
        assert tool_result.content == {"echo": "hello"}
        assert len(runtime.context_providers) == 1
        fragments = runtime.context_providers[0].build_context(
            ContextRequest(current_input="hello")
        )
        assert [fragment.content for fragment in fragments] == ["input=hello"]

        first = application.call_service("fixture.application.service", "info")
        unrelated = application.call_service("fixture.unrelated.service", "info")
        consumer = application.call_service("fixture.application-consumer.service", "info")
        applied = application.application.apply_config(
            "fixture.application",
            {"label": "updated"},
        )
        assert applied == {"applicationState": "applied"}
        assert application.call_service("fixture.application.service", "info") == {
            "pid": first["pid"],
            "label": "updated",
        }

        restarted = application.application.apply_config(
            "fixture.application",
            {"label": "restarted", "restart": True},
        )
        assert restarted == {"applicationState": "applied", "reasonCode": "READY"}
        after_config_restart = application.call_service(
            "fixture.application.service",
            "info",
        )
        after_config_consumer = application.call_service(
            "fixture.application-consumer.service",
            "info",
        )
        assert after_config_restart["label"] == "restarted"
        assert after_config_restart["pid"] != first["pid"]
        assert after_config_consumer["pid"] != consumer["pid"]
        assert application.call_service("fixture.unrelated.service", "info") == unrelated

        application.application.reload_plugin("fixture.application")
        reloaded = application.call_service("fixture.application.service", "info")
        reloaded_consumer = application.call_service(
            "fixture.application-consumer.service",
            "info",
        )
        assert reloaded["pid"] != after_config_restart["pid"]
        assert reloaded_consumer["pid"] != after_config_consumer["pid"]
        assert application.call_service("fixture.unrelated.service", "info") == unrelated
        assert registry.execute("fixture_v4_echo", {"value": "again"}).content == {
            "echo": "again"
        }

        install_id = records["fixture.application"]["installId"]
        disabled = application.set_enabled(install_id, False)
        disabled_record = next(
            item for item in disabled["plugins"] if item["pluginId"] == "fixture.application"
        )
        assert disabled_record["state"] == "disabled"
        assert registry.get("fixture_v4_echo") is None
        assert runtime.context_providers == []
        assert application.call_service("fixture.unrelated.service", "info") == unrelated
        consumer_record = next(
            item
            for item in disabled["plugins"]
            if item["pluginId"] == "fixture.application-consumer"
        )
        assert consumer_record["state"] == "failed"
        assert consumer_record["reasonCode"] == "DEPENDENCY_FAILED"
    finally:
        application.close()
    assert registry.get("fixture_v4_echo") is None
    assert runtime.context_providers == []


def test_service_proxy_routes_success_error_timeout_and_never_replays(
    tmp_path: Path,
) -> None:
    roots = _roots(tmp_path)
    bundled = roots.distribution_root / "plugins" / "builtin"
    provider = _plugin_source(
        bundled,
        "fixture.provider",
        "fixture.echo",
        body="""
import time

class Echo:
    def __init__(self): self.calls = 0
    def echo(self, value): return {"echo": value}
    def fail(self): raise RuntimeError("boom")
    def slow(self):
        self.calls += 1
        time.sleep(0.2)
        return "late"
    def count(self): return self.calls

class Plugin:
    def setup(self, context):
        context.provide("fixture.echo", Echo(), exports=("echo", "fail", "slow", "count"))
""".strip(),
    )
    _plugin_source(
        bundled,
        "fixture.consumer",
        "fixture.facade",
        requires=("fixture.echo",),
        body="""
class Facade:
    def __init__(self, echo, initial):
        self.echo = echo
        self.initial = initial
    def call(self, value): return {"initial": self.initial, "result": self.echo.echo(value)}
    def fail(self): return self.echo.fail()

class Plugin:
    def setup(self, context):
        echo = context.get("fixture.echo")
        context.provide(
            "fixture.facade",
            Facade(echo, echo.echo("setup")),
            exports=("call", "fail"),
        )
""".strip(),
    )
    assert provider.is_dir()
    manager = PluginRuntimeManager(
        roots,
        "generation-v4-proxy",
        PluginInventory(roots).scan().runtime_specs,
        call_timeout=0.05,
    )
    try:
        manager.start()
        assert manager.call_service("fixture.facade", "call", {"value": 7}) == {
            "initial": {"echo": "setup"},
            "result": {"echo": {"value": 7}},
        }
        with pytest.raises(PluginRuntimeError) as failed:
            manager.call_service("fixture.facade", "fail")
        assert failed.value.code == "PLUGIN_CALL_FAILED"
        with pytest.raises(PluginRuntimeError) as hidden:
            manager.call_service("fixture.echo", "missing")
        assert hidden.value.code == "SERVICE_METHOD_NOT_EXPORTED"
        with pytest.raises(PluginRuntimeError) as timed_out:
            manager.call_service("fixture.echo", "slow")
        assert timed_out.value.code == "PLUGIN_CALL_TIMEOUT"
        time.sleep(0.25)
        assert manager.call_service("fixture.echo", "count") == 1
    finally:
        manager.close()


def test_v4_host_contributions_are_revoked_when_plugin_crashes(tmp_path: Path) -> None:
    from app.agent.tools import ToolRegistry
    from app.core_host.plugin_application import PluginApplicationHost

    roots = _roots(tmp_path)
    _plugin_source(
        roots.distribution_root / "plugins" / "builtin",
        "fixture.contribution-crash",
        "fixture.contribution-crash.service",
        requires=("sakura.host.tools",),
        body="""
import os
class Service:
    def crash(self): os._exit(9)
class Plugin:
    def setup(self, context):
        context.provide(
            "fixture.contribution-crash.service",
            Service(),
            exports=("crash",),
        )
        context.get("sakura.host.tools").register(
            {
                "name": "fixture_v4_crash_tool",
                "description": "A crash cleanup fixture.",
                "parameters": {"type": "object", "properties": {}},
                "group": "fixture",
                "risk": "low",
            },
            lambda _arguments: {"ok": True},
        )
""".strip(),
    )
    registry = ToolRegistry()
    application = PluginApplicationHost(
        roots,
        "generation-v4-contribution-crash",
        registry,
    )
    try:
        application.start()
        assert registry.get("fixture_v4_crash_tool") is not None
        with pytest.raises(PluginRuntimeError):
            application.call_service("fixture.contribution-crash.service", "crash")
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            record = application.public_snapshot()["plugins"][0]
            if record["reasonCode"] == "PLUGIN_PROCESS_EXITED":
                break
            time.sleep(0.01)
        else:
            raise AssertionError(application.public_snapshot())
        assert registry.get("fixture_v4_crash_tool") is None
    finally:
        application.close()


def test_provider_crash_invalidates_only_its_service_and_has_no_recovery(
    tmp_path: Path,
) -> None:
    roots = _roots(tmp_path)
    bundled = roots.distribution_root / "plugins" / "builtin"
    _plugin_source(
        bundled,
        "fixture.crash",
        "fixture.crash.service",
        body="""
import os
class Service:
    def crash(self): os._exit(9)
class Plugin:
    def setup(self, context):
        context.provide("fixture.crash.service", Service(), exports=("crash",))
""".strip(),
    )
    _plugin_source(
        bundled,
        "fixture.unrelated",
        "fixture.unrelated.service",
        body="""
import os
class Service:
    def pid(self): return os.getpid()
class Plugin:
    def setup(self, context):
        context.provide("fixture.unrelated.service", Service(), exports=("pid",))
""".strip(),
    )
    _plugin_source(
        bundled,
        "fixture.dependent",
        "fixture.dependent.service",
        requires=("fixture.crash.service",),
        body="""
import os
class Service:
    def pid(self): return os.getpid()
class Plugin:
    def setup(self, context):
        context.get("fixture.crash.service")
        context.provide("fixture.dependent.service", Service(), exports=("pid",))
""".strip(),
    )
    manager = PluginRuntimeManager(
        roots,
        "generation-v4-crash",
        PluginInventory(roots).scan().runtime_specs,
        call_timeout=0.2,
    )
    try:
        manager.start()
        unrelated_pid = manager.call_service("fixture.unrelated.service", "pid")
        dependent_pid = manager.call_service("fixture.dependent.service", "pid")
        with pytest.raises(PluginRuntimeError):
            manager.call_service("fixture.crash.service", "crash")
        _wait_reason(manager, "fixture.crash", "PLUGIN_PROCESS_EXITED")
        _wait_reason(manager, "fixture.dependent", "DEPENDENCY_FAILED")
        assert manager.call_service("fixture.unrelated.service", "pid") == unrelated_pid
        assert dependent_pid != unrelated_pid
        with pytest.raises(PluginRuntimeError) as dependent_missing:
            manager.call_service("fixture.dependent.service", "pid")
        assert dependent_missing.value.code == "SERVICE_MISSING"
        with pytest.raises(PluginRuntimeError) as missing:
            manager.call_service("fixture.crash.service", "crash")
        assert missing.value.code == "SERVICE_MISSING"
        time.sleep(0.1)
        crashed = next(
            item
            for item in manager.snapshot()["plugins"]
            if item["pluginId"] == "fixture.crash"
        )
        assert crashed["pid"] is None
        assert crashed["reasonCode"] == "PLUGIN_PROCESS_EXITED"
    finally:
        manager.close()


def test_service_conflict_fails_all_participants_without_starting_them(
    tmp_path: Path,
) -> None:
    roots = _roots(tmp_path)
    bundled = roots.distribution_root / "plugins" / "builtin"
    _plugin_source(bundled, "fixture.first", "fixture.shared")
    _plugin_source(bundled, "fixture.second", "fixture.shared")
    manager = PluginRuntimeManager(
        roots,
        "generation-v4-conflict",
        PluginInventory(roots).scan().runtime_specs,
    )
    try:
        snapshot = manager.start()
        assert {item["reasonCode"] for item in snapshot["plugins"]} == {"SERVICE_CONFLICT"}
        assert all(item["pid"] is None for item in snapshot["plugins"])
    finally:
        manager.close()


def test_incremental_enable_and_reload_never_choose_a_service_conflict_winner(
    tmp_path: Path,
) -> None:
    roots = _roots(tmp_path)
    bundled = roots.distribution_root / "plugins" / "builtin"
    provider_body = """
class Service:
    def info(self): return {"ready": True}
class Plugin:
    def setup(self, context):
        context.provide("fixture.incremental.shared", Service(), exports=("info",))
""".strip()
    _plugin_source(
        bundled,
        "fixture.incumbent",
        "fixture.incremental.shared",
        body=provider_body,
    )
    _plugin_source(
        bundled,
        "fixture.challenger",
        "fixture.incremental.shared",
        body=provider_body,
    )
    _plugin_source(
        bundled,
        "fixture.incremental-consumer",
        "fixture.incremental.consumer",
        requires=("fixture.incremental.shared",),
        body="""
class Service: pass
class Plugin:
    def setup(self, context):
        context.get("fixture.incremental.shared").info()
        context.provide("fixture.incremental.consumer", Service(), exports=())
""".strip(),
    )
    PluginDesiredStateStore(roots.user_root).set("fixture.challenger", False)
    manager = PluginRuntimeManager(
        roots,
        "generation-v4-incremental-conflict",
        PluginInventory(roots).scan().runtime_specs,
    )
    try:
        started = manager.start()
        started_by_id = {item["pluginId"]: item for item in started["plugins"]}
        assert started_by_id["fixture.incumbent"]["state"] == "active"
        assert started_by_id["fixture.challenger"]["state"] == "disabled"
        assert started_by_id["fixture.incremental-consumer"]["state"] == "active"

        conflicted = manager.set_enabled("fixture.challenger", True)
        conflicted_by_id = {item["pluginId"]: item for item in conflicted["plugins"]}
        assert {
            conflicted_by_id[plugin_id]["reasonCode"]
            for plugin_id in ("fixture.incumbent", "fixture.challenger")
        } == {"SERVICE_CONFLICT"}
        assert conflicted_by_id["fixture.incremental-consumer"]["reasonCode"] == (
            "DEPENDENCY_FAILED"
        )
        assert all(
            conflicted_by_id[plugin_id]["pid"] is None
            for plugin_id in conflicted_by_id
        )
        with pytest.raises(PluginRuntimeError) as missing:
            manager.call_service("fixture.incremental.shared", "info")
        assert missing.value.code == "SERVICE_MISSING"

        with pytest.raises(PluginRuntimeError) as reload_failed:
            manager.reload_plugin("fixture.incumbent")
        assert reload_failed.value.code == "SERVICE_CONFLICT"
        assert {
            item["reasonCode"]
            for item in manager.snapshot()["plugins"]
            if item["pluginId"] in {"fixture.incumbent", "fixture.challenger"}
        } == {"SERVICE_CONFLICT"}
    finally:
        manager.close()


def test_restart_required_reports_failure_and_leaves_consumers_stopped(
    tmp_path: Path,
) -> None:
    roots = _roots(tmp_path)
    bundled = roots.distribution_root / "plugins" / "builtin"
    _plugin_source(
        bundled,
        "fixture.restart-failure",
        "fixture.restart-failure.service",
        body="""
class Service: pass
class Plugin:
    def setup(self, context):
        if context.config.get().get("broken"):
            raise RuntimeError("configured failure")
        context.config.on_change(lambda _values: "restart_required")
        context.provide("fixture.restart-failure.service", Service(), exports=())
""".strip(),
    )
    _plugin_source(
        bundled,
        "fixture.restart-consumer",
        "fixture.restart-consumer.service",
        requires=("fixture.restart-failure.service",),
        body="""
class Service: pass
class Plugin:
    def setup(self, context):
        context.get("fixture.restart-failure.service")
        context.provide("fixture.restart-consumer.service", Service(), exports=())
""".strip(),
    )
    manager = PluginRuntimeManager(
        roots,
        "generation-v4-restart-failure",
        PluginInventory(roots).scan().runtime_specs,
    )
    try:
        manager.start()
        with pytest.raises(PluginRuntimeError) as failed:
            manager.apply_config("fixture.restart-failure", {"broken": True})
        assert failed.value.code == "PLUGIN_CALL_FAILED"
        records = {item["pluginId"]: item for item in manager.snapshot()["plugins"]}
        assert records["fixture.restart-failure"]["state"] == "failed"
        assert records["fixture.restart-failure"]["reasonCode"] == "PLUGIN_CALL_FAILED"
        assert records["fixture.restart-consumer"]["state"] == "failed"
        assert records["fixture.restart-consumer"]["reasonCode"] == "DEPENDENCY_FAILED"
        with pytest.raises(PluginRuntimeError) as missing:
            manager.call_service("fixture.restart-consumer.service", "missing")
        assert missing.value.code == "SERVICE_MISSING"
    finally:
        manager.close()


def test_host_event_handler_timeout_does_not_fail_application_or_other_plugins(
    tmp_path: Path,
) -> None:
    from app.agent.tools import ToolRegistry
    from app.core_host.plugin_application import PluginApplicationHost

    roots = _roots(tmp_path)
    bundled = roots.distribution_root / "plugins" / "builtin"
    _plugin_source(
        bundled,
        "fixture.event-a-slow",
        "fixture.event-a-slow.service",
        body="""
import time
class Service: pass
class Plugin:
    def setup(self, context):
        context.provide("fixture.event-a-slow.service", Service(), exports=())
        context.on("sakura.host.app.started", lambda _payload: time.sleep(0.2))
""".strip(),
    )
    _plugin_source(
        bundled,
        "fixture.event-b-ready",
        "fixture.event-b-ready.service",
        body="""
class Service:
    def __init__(self): self.started = False
    def state(self): return {"started": self.started}
def fail(_payload): raise RuntimeError("event fixture failure")
class Plugin:
    def setup(self, context):
        service = Service()
        context.provide("fixture.event-b-ready.service", service, exports=("state",))
        context.on("sakura.host.app.started", fail)
        context.on(
            "sakura.host.app.started",
            lambda _payload: setattr(service, "started", True),
        )
""".strip(),
    )
    application = PluginApplicationHost(
        roots,
        "generation-v4-event-isolation",
        ToolRegistry(),
        call_timeout=0.05,
    )
    try:
        application.start()
        assert application.call_service("fixture.event-b-ready.service", "state") == {
            "started": True
        }
        records = application.public_snapshot()["plugins"]
        assert {item["state"] for item in records} == {"active"}
    finally:
        application.close()


def test_dependency_install_failure_keeps_code_environment_and_config_unpublished(
    tmp_path: Path,
) -> None:
    roots = _roots(tmp_path)
    source = _plugin_source(
        tmp_path,
        "fixture.broken",
        "fixture.broken.service",
    )
    (source / "requirements.txt").write_text(
        str((tmp_path / "missing.whl").resolve()) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(PluginInstallError) as failed:
        LocalPluginInstaller(roots).install(source.resolve(), "folder")

    assert failed.value.code == "PLUGIN_DEPENDENCY_INSTALL_FAILED"
    paths = StoragePaths(roots.user_root)
    assert not (paths.user_plugins_dir / "fixture.broken").exists()
    assert not paths.plugin_dependency_root_for("fixture.broken").exists()
    assert not paths.plugins_config().exists()


def test_entry_import_failure_rolls_back_successful_dependency_install(tmp_path: Path) -> None:
    roots = _roots(tmp_path)
    wheel = _wheel(tmp_path, "3.0.0")
    source = _plugin_source(
        tmp_path,
        "fixture.syntax",
        "fixture.syntax.service",
        wheel=wheel,
    )
    (source / "plugin.py").write_text("class Plugin(:\n", encoding="utf-8")

    with pytest.raises(PluginInstallError) as failed:
        LocalPluginInstaller(roots).install(source.resolve(), "folder")

    assert failed.value.code == "PLUGIN_ENTRY_IMPORT_FAILED"
    paths = StoragePaths(roots.user_root)
    assert not (paths.user_plugins_dir / "fixture.syntax").exists()
    assert not paths.plugin_dependency_root_for("fixture.syntax").exists()
    assert not paths.plugins_config().exists()


def test_production_application_host_runs_v4_and_reports_api3_as_unsupported(
    tmp_path: Path,
) -> None:
    from app.core_host.plugin_application import PluginApplicationHost

    roots = _roots(tmp_path)
    legacy = _plugin_source(
        roots.distribution_root / "plugins" / "builtin",
        "fixture.v3-active",
        "fixture.v3.service",
        body="""
class Service: pass
class Plugin:
    def setup(self, context):
        context.provide("fixture.v3.service", Service(), exports=())
""".strip(),
    )
    (legacy / "plugin.yaml").write_text(
        (legacy / "plugin.yaml").read_text(encoding="utf-8").replace("api: 4", "api: 3"),
        encoding="utf-8",
    )
    _plugin_source(
        roots.distribution_root / "plugins" / "builtin",
        "fixture.v4-only",
        "fixture.v4.service",
        body="""
class Service: pass
class Plugin:
    def setup(self, context):
        context.provide("fixture.v4.service", Service(), exports=())
""".strip(),
    )
    host = PluginApplicationHost(roots, "generation-legacy-projection", object())
    try:
        host.start()
        host.application.wait_until_loaded()
        records = {
            item["pluginId"]: item for item in host.settings_snapshot()["plugins"]
        }
        assert records["fixture.v4-only"]["supported"] is True
        assert records["fixture.v4-only"]["state"] == "active"
        assert records["fixture.v3-active"]["supported"] is False
        assert records["fixture.v3-active"]["state"] == "failed"
        assert records["fixture.v3-active"]["reasonCode"] == "API_VERSION_UNSUPPORTED"
    finally:
        host.close()


def test_dependency_quarantine_rollback_failure_preserves_only_code_copy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    roots = _roots(tmp_path)
    wheel = _wheel(tmp_path, "4.0.0")
    source = _plugin_source(
        tmp_path,
        "fixture.quarantine",
        "fixture.quarantine.service",
        wheel=wheel,
    )
    installer = LocalPluginInstaller(roots)
    installed = installer.install(source.resolve(), "folder")
    record = next(
        item
        for item in PluginInventory(roots).scan().records
        if item.plugin_id == installed.plugin_id
    )
    original = installer._replace_path
    calls = 0

    def fail_dependency_and_code_restore(source_path: Path, target_path: Path) -> None:
        nonlocal calls
        calls += 1
        if calls in {2, 3}:
            raise OSError("fixture move failure")
        original(source_path, target_path)

    monkeypatch.setattr(installer, "_replace_path", fail_dependency_and_code_restore)
    with pytest.raises(PluginInstallError) as failed:
        installer.begin_uninstall(record.install_id)

    assert failed.value.code == "PLUGIN_UNINSTALL_ROLLBACK_FAILED"
    quarantines = list(StoragePaths(roots.user_root).user_plugins_dir.glob(".uninstall-*"))
    assert len(quarantines) == 1
    assert (quarantines[0] / "code" / "plugin.yaml").is_file()
    assert not installed.code_dir.exists()
    assert StoragePaths(roots.user_root).plugin_dependency_root_for(installed.plugin_id).is_dir()


def test_transport_failure_terminates_live_runner_and_owned_child(tmp_path: Path) -> None:
    roots = _roots(tmp_path)
    bundled = roots.distribution_root / "plugins" / "builtin"
    _plugin_source(
        bundled,
        "fixture.protocol",
        "fixture.protocol.service",
        body="""
import os
import subprocess
import sys
import time

class Service:
    def __init__(self, child_pid_path): self.child_pid_path = child_pid_path
    def break_protocol(self):
        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
        self.child_pid_path.parent.mkdir(parents=True, exist_ok=True)
        self.child_pid_path.write_text(str(child.pid), encoding="utf-8")
        os.write(1, b"\\x00\\x00\\x00\\x02{}")
        time.sleep(30)

class Plugin:
    def setup(self, context):
        context.provide(
            "fixture.protocol.service",
            Service(context.data_path("child.pid")),
            exports=("break_protocol",),
        )
""".strip(),
    )
    manager = PluginRuntimeManager(
        roots,
        "generation-v4-protocol",
        PluginInventory(roots).scan().runtime_specs,
        call_timeout=0.3,
    )
    try:
        manager.start()
        with pytest.raises(PluginRuntimeError):
            manager.call_service("fixture.protocol.service", "break_protocol")
        _wait_reason(manager, "fixture.protocol", "PLUGIN_PROCESS_EXITED")
        child_pid = int(
            (StoragePaths(roots.user_root).plugin_data_for("fixture.protocol") / "child.pid")
            .read_text(encoding="utf-8")
        )
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline and _pid_exists(child_pid):
            time.sleep(0.02)
        assert not _pid_exists(child_pid)
    finally:
        manager.close()


def test_root_process_exit_is_detected_while_child_keeps_stdout_open(tmp_path: Path) -> None:
    roots = _roots(tmp_path)
    bundled = roots.distribution_root / "plugins" / "builtin"
    _plugin_source(
        bundled,
        "fixture.crashing-provider",
        "fixture.crashing-provider.service",
        body="""
import os
import subprocess
import sys

class Service:
    def __init__(self, child_pid_path): self.child_pid_path = child_pid_path
    def crash_with_child(self):
        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
        self.child_pid_path.parent.mkdir(parents=True, exist_ok=True)
        self.child_pid_path.write_text(str(child.pid), encoding="utf-8")
        os._exit(9)

class Plugin:
    def setup(self, context):
        context.provide(
            "fixture.crashing-provider.service",
            Service(context.data_path("child.pid")),
            exports=("crash_with_child",),
        )
""".strip(),
    )
    _plugin_source(
        bundled,
        "fixture.crash-consumer",
        "fixture.crash-consumer.service",
        requires=("fixture.crashing-provider.service",),
        body="""
class Service: pass
class Plugin:
    def setup(self, context):
        context.get("fixture.crashing-provider.service")
        context.provide("fixture.crash-consumer.service", Service(), exports=())
""".strip(),
    )
    manager = PluginRuntimeManager(
        roots,
        "generation-v4-root-exit",
        PluginInventory(roots).scan().runtime_specs,
        call_timeout=0.3,
    )
    try:
        manager.start()
        with pytest.raises(PluginRuntimeError):
            manager.call_service(
                "fixture.crashing-provider.service",
                "crash_with_child",
            )
        _wait_reason(manager, "fixture.crashing-provider", "PLUGIN_PROCESS_EXITED")
        _wait_reason(manager, "fixture.crash-consumer", "DEPENDENCY_FAILED")
        child_pid = int(
            (
                StoragePaths(roots.user_root).plugin_data_for("fixture.crashing-provider")
                / "child.pid"
            ).read_text(encoding="utf-8")
        )
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline and _pid_exists(child_pid):
            time.sleep(0.02)
        assert not _pid_exists(child_pid)
    finally:
        manager.close()


def _pid_exists(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def test_generation_close_stops_plugin_that_is_still_in_setup(tmp_path: Path) -> None:
    roots = _roots(tmp_path)
    bundled = roots.distribution_root / "plugins" / "builtin"
    _plugin_source(
        bundled,
        "fixture.starting",
        "fixture.starting.service",
        body="""
import time
class Service: pass
class Plugin:
    def setup(self, context):
        time.sleep(30)
        context.provide("fixture.starting.service", Service(), exports=())
""".strip(),
    )
    manager = PluginRuntimeManager(
        roots,
        "generation-v4-close-starting",
        PluginInventory(roots).scan().runtime_specs,
    )
    thread = threading.Thread(target=manager.start, daemon=True)
    thread.start()
    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline:
        record = manager.snapshot()["plugins"][0]
        if record["reasonCode"] == "PLUGIN_STARTING":
            break
        time.sleep(0.01)
    else:
        raise AssertionError(manager.snapshot())

    manager.close()
    thread.join(timeout=2.0)

    assert not thread.is_alive()
    record = manager.snapshot()["plugins"][0]
    assert record["state"] != "active"
    assert record["pid"] is None


def test_provider_crash_while_consumer_setup_never_publishes_consumer(
    tmp_path: Path,
) -> None:
    roots = _roots(tmp_path)
    bundled = roots.distribution_root / "plugins" / "builtin"
    _plugin_source(
        bundled,
        "fixture.setup-provider",
        "fixture.setup-provider.service",
        body="""
import time
class Service:
    def block(self): time.sleep(30)
class Plugin:
    def setup(self, context):
        context.provide("fixture.setup-provider.service", Service(), exports=("block",))
""".strip(),
    )
    _plugin_source(
        bundled,
        "fixture.setup-consumer",
        "fixture.setup-consumer.service",
        requires=("fixture.setup-provider.service",),
        body="""
class Service: pass
class Plugin:
    def setup(self, context):
        context.get("fixture.setup-provider.service").block()
        context.provide("fixture.setup-consumer.service", Service(), exports=())
""".strip(),
    )
    manager = PluginRuntimeManager(
        roots,
        "generation-v4-provider-dies-during-setup",
        PluginInventory(roots).scan().runtime_specs,
        call_timeout=5.0,
    )
    thread = threading.Thread(target=manager.start, daemon=True)
    thread.start()
    provider_pid: int | None = None
    deadline = time.monotonic() + 3.0
    while time.monotonic() < deadline:
        records = {item["pluginId"]: item for item in manager.snapshot()["plugins"]}
        provider_pid = records["fixture.setup-provider"]["pid"]
        if (
            provider_pid is not None
            and records["fixture.setup-consumer"]["reasonCode"] == "PLUGIN_STARTING"
        ):
            break
        time.sleep(0.01)
    else:
        manager.close()
        raise AssertionError(manager.snapshot())

    assert provider_pid is not None
    os.kill(provider_pid, 9)
    thread.join(timeout=3.0)
    assert not thread.is_alive()
    _wait_reason(manager, "fixture.setup-provider", "PLUGIN_PROCESS_EXITED")
    consumer = next(
        item
        for item in manager.snapshot()["plugins"]
        if item["pluginId"] == "fixture.setup-consumer"
    )
    assert consumer["state"] == "failed"
    assert consumer["reasonCode"] == "DEPENDENCY_FAILED"
    assert consumer["pid"] is None
    manager.close()


def test_rpc_deadline_includes_blocked_pipe_write() -> None:
    class BlockingInput:
        def __init__(self) -> None:
            self.release = threading.Event()

        def read(self, _size: int) -> bytes:
            self.release.wait()
            return b""

    class BlockingOutput:
        def __init__(self) -> None:
            self.release = threading.Event()

        def write(self, data: bytes) -> int:
            self.release.wait()
            return len(data)

        def flush(self) -> None:
            return None

    input_stream = BlockingInput()
    output_stream = BlockingOutput()
    peer = RpcPeer(
        input_stream,  # type: ignore[arg-type]
        output_stream,  # type: ignore[arg-type]
        generation_id="generation-blocked-write",
        plugin_id="fixture.blocked-write",
        request_handler=lambda _name, _payload: None,
    )
    peer.start(thread_name="fixture-blocked-write")
    started = time.monotonic()
    try:
        with pytest.raises(PluginApiError) as failed:
            peer.request("fixture.call", {}, timeout=0.05)
        assert failed.value.code == "PLUGIN_CALL_TIMEOUT"
        assert time.monotonic() - started < 0.2
    finally:
        peer.close()
        input_stream.release.set()
        output_stream.release.set()


def test_v3_install_is_rejected_without_resolving_dependency_declaration(tmp_path: Path) -> None:
    roots = _roots(tmp_path)
    source = _plugin_source(
        tmp_path,
        "fixture.v3-dependencies",
        "fixture.v3-dependencies.service",
    )
    manifest = (source / "plugin.yaml").read_text(encoding="utf-8")
    (source / "plugin.yaml").write_text(
        manifest.replace("api: 4", "api: 3"),
        encoding="utf-8",
    )
    (source / "requirements.txt").write_text(
        str((tmp_path / "missing-v3.whl").resolve()) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(PluginInstallError) as failed:
        LocalPluginInstaller(roots).install(source.resolve(), "folder")

    assert failed.value.code == "API_VERSION_UNSUPPORTED"
    paths = StoragePaths(roots.user_root)
    assert not (paths.user_plugins_dir / "fixture.v3-dependencies").exists()
    assert not paths.plugin_dependency_root_for("fixture.v3-dependencies").exists()
