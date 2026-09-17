from __future__ import annotations

import importlib.util
import io
import json
import os
import sys
import threading
import time
import zipfile
from pathlib import Path

import psutil
import pytest

from app.agent.tools import ToolRegistry
from app.config.character_loader import CharacterProfile
from app.core_host.plugin_application import PluginApplicationHost
from app.core_host import plugin_host_services
from app.core_host.plugin_settings import PluginSettingsBoundary
from app.plugins.installer import LocalPluginInstaller, PluginInstallError
from app.plugins.inventory import PluginDesiredStateStore, PluginInventory
from app.plugins.runtime_v4 import PluginRuntimeError, PluginRuntimeManager
from app.plugins.sakura_plugin_sdk import PluginApiError, RpcPeer
from app.storage.paths import StoragePaths
from app.storage.runtime_roots import RuntimeRoots


@pytest.mark.parametrize("retry_port", [8000, 8001])
def test_unchanged_config_does_not_reload_and_failed_apply_remains_retryable(tmp_path, retry_port):
    from app.plugins.sakura_plugin_sdk import PluginConfig
    root, data = tmp_path / "plugin", tmp_path / "data"
    root.mkdir()
    (root / "config.json").write_text('{"port": 8000}', encoding="utf-8")
    config = PluginConfig("fixture", root, data, lambda cleanup: cleanup)
    # Explicitly pinning a default persists the override without restarting.
    assert config.update({"port": 8000}) == "applied"
    assert json.loads((data / "config.json").read_text(encoding="utf-8")) == {"port": 8000}
    calls = []
    def apply(values):
        calls.append(values)
        return "error" if len(calls) == 1 else "applied"
    config.on_change(apply)
    assert config.update({"port": 8000}) == "applied"
    assert not calls
    assert config.update({"port": 8001}) == "error"
    assert config.update({"port": retry_port}) == "applied"
    assert len(calls) == 2
    assert config.replace({"port": retry_port}) == "applied"
    assert len(calls) == 2
    fresh = PluginConfig("fixture", root, data, lambda cleanup: cleanup)
    assert fresh.update({"port": retry_port}) == "applied"
    assert fresh.update({"port": 8002}) == "restart_required"
    assert fresh.update({"port": 8002}) == "restart_required"


def test_unified_logging_two_real_plugins_keep_identity_and_flush_cleanup(tmp_path: Path) -> None:
    from app.core_host.runtime_logging import install_runtime_logging, CORE_BRIDGE_PREFIX

    roots = _roots(tmp_path)
    services = {"one": "fixture.one.service", "two": "sakura.tts.provider.fixture"}
    for name in ("one", "two"):
        service_key = services[name]
        plugin_root = _plugin_source(roots.distribution_root / "plugins" / "builtin", f"fixture.{name}", service_key,
            requires=("sakura.host.logging", "sakura.host.settings", "sakura.host.model_slots"), body=f'''
class Plugin:
    def setup(self, context):
        context.get("sakura.host.settings").register(
            {{"sectionId": "fixture_{name}", "title": "示例设置", "fields": []}},
            load=lambda: {{}},
        )
        context.get("sakura.host.model_slots").register(
            {{"slotId": "summary", "label": "摘要模型", "description": "", "modelKind": "chat_completion", "required": False, "order": 50}},
            load=lambda: {{"profileId": "", "model": ""}},
            save=lambda values: None,
        )
        original_id = context.plugin_id
        context.plugin_id = "fixture.spoofed"
        logger = context.get("sakura.host.logging")
        logger.info("插件已启动", fields={{"stage": "setup", "elapsed_ms": 12}})
        context.plugin_id = original_id
        context.effect(lambda: logger.info("插件已清理", fields={{"stage": "cleanup"}}))
        class Service:
            def emit(self):
                return logger.error("业务调用失败", fields={{"stage": "run", "nested": {{"api_key": "private-credential", "count": 2}}}})
        context.provide("{service_key}", Service(), exports=("emit",))
''')
        manifest = plugin_root / 'plugin.yaml'
        manifest.write_text(manifest.read_text(encoding='utf-8').replace(f'name: fixture.{name}', f'name: 示例插件 {name}'), encoding='utf-8')
    stream = io.BytesIO()
    bridge = install_runtime_logging(stream)
    host = PluginApplicationHost(roots, "generation-logging", ToolRegistry())
    try:
        host.start()
        assert host.application.wait_until_loaded(timeout=3)
        current = host.application.public_snapshot()
        assert all(item["state"] == "active" for item in current["plugins"]), current
        for name in ("one", "two"):
            assert host.application.call_service(services[name], "emit") is True
    finally:
        host.close()
        bridge.close()
    records = [json.loads(line.removeprefix(CORE_BRIDGE_PREFIX)) for line in stream.getvalue().splitlines()
        if line.startswith(CORE_BRIDGE_PREFIX)]
    lifecycle = [r for r in records if r.get("event") == "plugin.loaded" or r.get("attributes", {}).get("event") == "plugin.stopped"]
    assert len(lifecycle) == 4
    assert {r["plugin_id"] for r in lifecycle} == {"fixture.one", "fixture.two"}
    assert all(r.get("plugin_name", "").startswith("示例插件") for r in lifecycle)
    custom = [r for r in records if r.get("custom") and r.get("attributes", {}).get("stage") in {"setup", "run", "cleanup"}]
    assert len(custom) == 6, custom
    for name in ("one", "two"):
        rows = [r for r in custom if r["plugin_id"] == f"fixture.{name}"]
        assert all(r["plugin_name"] == f"示例插件 {name}" for r in rows)
        assert all(r["channel"] == ("tts" if name == "two" else "plugin") for r in rows)
        assert {r["attributes"]["stage"] for r in rows} == {"setup", "run", "cleanup"}
    assert b"fixture.spoofed" not in stream.getvalue()
    assert b"private-credential" not in stream.getvalue()
    assert all(len(line) + 1 <= 4096 for line in stream.getvalue().splitlines())


def test_plugin_start_failures_reach_log_bridge_with_identity(tmp_path: Path):
    from app.core_host.runtime_logging import install_runtime_logging, CORE_BRIDGE_PREFIX
    roots = _roots(tmp_path)
    parent = roots.distribution_root / "plugins/builtin"
    _plugin_source(parent, "fixture.missing", "fixture.missing.service", requires=("fixture.absent",), body="class Plugin: pass")
    _plugin_source(parent, "fixture.broken", "fixture.broken.service", body="class Plugin:\n    def setup(self, context): raise ValueError('fixture setup failed')")
    stream = io.BytesIO()
    bridge = install_runtime_logging(stream)
    host = PluginApplicationHost(roots, "failure-log-test", ToolRegistry())
    try:
        host.start()
        assert host.application.wait_until_loaded(timeout=3)
    finally:
        host.close()
        bridge.close()
    records = [json.loads(line.removeprefix(CORE_BRIDGE_PREFIX)) for line in stream.getvalue().splitlines() if line.startswith(CORE_BRIDGE_PREFIX)]
    failures = [row for row in records if row.get("attributes", {}).get("event") in {"plugin.start.blocked", "plugin.start.failed"}]
    assert {row["plugin_id"] for row in failures} == {"fixture.missing", "fixture.broken"}
    assert all(row["severity"] == "error" and row["plugin_name"] == row["plugin_id"] for row in failures)
    assert next(row for row in failures if row["plugin_id"] == "fixture.missing")["attributes"]["reason_code"] == "MISSING_SERVICE"
    broken = next(row for row in failures if row["plugin_id"] == "fixture.broken")["attributes"]
    assert "fixture setup failed" in broken["diagnostic"]
    assert "ValueError" in broken["exception_chain"]
    assert ":setup:" in broken["exception_stack"]


def test_plugin_stderr_is_forwarded_before_process_exit(tmp_path: Path, monkeypatch) -> None:
    from app.core import runtime_log
    roots = _roots(tmp_path)
    _plugin_source(roots.distribution_root / "plugins/builtin", "fixture.stderr", "fixture.stderr.service", body='''class Plugin:
    def setup(self, context):
        print("small stderr diagnostic", flush=True)
        context.provide("fixture.stderr.service", object(), exports=())
''')
    received = threading.Event()
    captured = []
    def capture(level, message, **kwargs):
        captured.append({**kwargs, "level": level})
        if kwargs.get("fields", {}).get("event") == "plugin.process.stderr":
            received.set()
    monkeypatch.setattr(runtime_log, "log_message", capture)
    host = PluginApplicationHost(roots, "stderr-test", ToolRegistry())
    try:
        host.start()
        assert host.application.wait_until_loaded(timeout=3)
        assert received.wait(3), "short stderr output must arrive while the plugin is alive"
        row = next(row for row in captured if row.get("fields", {}).get("event") == "plugin.process.stderr")
        assert row["plugin_id"] == "fixture.stderr"
        assert row["plugin_name"] == "fixture.stderr"
        assert row["level"] == "info"
        assert row["fields"]["diagnostic"] == "small stderr diagnostic"
    finally:
        host.close()


@pytest.mark.parametrize("output", [
    "Fetching 18 files: 100%| 18/18 [00:01<00:00, 13.43it/s]",
    "Warning: You are sending unauthenticated requests to the HF Hub.",
    "Failed to load spaCy lemma model: spaCy is not installed.",
])
def test_raw_plugin_diagnostics_do_not_imply_failure(output, monkeypatch) -> None:
    from types import SimpleNamespace
    from app.core import runtime_log
    from app.plugins.runtime_v4 import _PluginProcess

    captured = []
    monkeypatch.setattr(runtime_log, "log_message", lambda level, message, **kwargs:
                        captured.append((level, kwargs)))
    worker = _PluginProcess.__new__(_PluginProcess)
    worker._spec = SimpleNamespace(plugin_id="fixture.stderr", name="Fixture")
    worker._drain_stderr(SimpleNamespace(stderr=io.BytesIO((output + "\n").encode())))
    assert len(captured) == 1
    level, row = captured[0]
    assert level == "info"
    assert row["fields"]["diagnostic"] == output


def test_unified_logging_sdk_queue_is_bounded_and_does_not_block(tmp_path: Path) -> None:
    from app.plugins.sakura_plugin_sdk import PluginContext

    entered, release = threading.Event(), threading.Event()
    sent = []
    def remote(service, method, args):
        entered.set()
        release.wait(2)
        sent.append(args)
        return {"accepted": True}
    context = PluginContext("fixture.queue", tmp_path, tmp_path, remote, lambda *_: None)
    logger = context.get("sakura.host.logging")
    try:
        assert logger.info("first")
        assert entered.wait(1)
        started = time.monotonic()
        results = [logger.info("queued") for _ in range(160)]
        assert time.monotonic() - started < 0.5
        assert sum(results) == 128
        assert logger.error("priority") is True
        release.set()
    finally:
        release.set()
        context.close()
    assert sum(args[1] for args in sent) == 33
    assert any(item["message"] == "priority" for args in sent for item in args[0])
    assert logger.info("after close") is False


def test_plugin_diagnostics_uses_generic_logger_and_bound_identity(monkeypatch) -> None:
    from app.plugins.host_services import HOST_CALLER

    captured = []
    monkeypatch.setattr(plugin_host_services, "log_message", lambda *args, **kwargs: captured.append((args, kwargs)))
    service = plugin_host_services._DiagnosticsHostService()
    descriptor = {
        "event": "third_party.engine.failed",
        "severity": "warning",
        "attributes": {
            "source_file": "my_plugin/engine.py",
            "elapsed_ms": "12034.5",
            "engine_state": {"phase": "loading"},
        },
    }
    token = HOST_CALLER.set("third.party")
    try:
        assert service.call("emit", ["spoofed.plugin", descriptor]) == {"accepted": True}
    finally:
        HOST_CALLER.reset(token)
    assert captured == [(("warning", descriptor["event"]), {
        "fields": {"event": descriptor["event"], **descriptor["attributes"]},
        "component": "plugin", "plugin_id": "third.party", "plugin_name": None,
    })]
    with pytest.raises(plugin_host_services.HostServiceError, match="LOG_CALLER_REQUIRED"):
        service.call("emit", ["third.party", descriptor])


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


@pytest.mark.skipif(os.name != "nt", reason="pywin32 import layout is Windows-only")
def test_windows_v4_plugin_imports_pywintypes_from_private_win32_lib(
    tmp_path: Path,
) -> None:
    roots = _roots(tmp_path)
    plugin_root = _plugin_source(
        roots.distribution_root / "plugins/builtin",
        "fixture.windows-pywin32",
        "fixture.windows-pywin32.service",
        body="""
import pywintypes

class Service:
    def ping(self):
        return pywintypes.VALUE

class Plugin:
    def setup(self, context):
        context.provide(
            "fixture.windows-pywin32.service",
            Service(),
            exports=("ping",),
        )
""".strip(),
    )
    requirements = plugin_root / "requirements.txt"
    requirements.write_text("pywin32-fixture==1\n", encoding="utf-8")
    dependency_root = (
        roots.distribution_root / "plugins/dependencies/fixture.windows-pywin32"
    )
    (dependency_root / "win32/lib").mkdir(parents=True)
    (dependency_root / "win32/lib/pywintypes.py").write_text(
        "VALUE = 'private-pywin32'\n",
        encoding="utf-8",
    )
    (dependency_root / ".sakura-dependencies.json").write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "kind": "requirements.txt",
                "python": f"{sys.version_info.major}.{sys.version_info.minor}",
            }
        ),
        encoding="utf-8",
    )

    manager = PluginRuntimeManager(
        roots,
        "generation-windows-pywin32",
        PluginInventory(roots).scan().runtime_specs,
    )
    try:
        snapshot = manager.start()
        assert snapshot["plugins"][0]["state"] == "active"
        assert manager.call_service(
            "fixture.windows-pywin32.service",
            "ping",
        ) == "private-pywin32"
    finally:
        manager.close()


def test_failed_enable_returns_saved_revision_for_subsequent_toggles(tmp_path: Path) -> None:
    roots = _roots(tmp_path)
    plugin_id = "fixture.missing-service"
    _plugin_source(
        roots.distribution_root / "plugins/builtin",
        plugin_id,
        "fixture.consumer",
        requires=("fixture.absent",),
        body=_simple_service_body("fixture.consumer", "ready"),
    )
    PluginDesiredStateStore(roots.user_root).set(plugin_id, False)
    application = PluginApplicationHost(roots, "generation-failed-enable", ToolRegistry())
    boundary = PluginSettingsBoundary(
        "generation-failed-enable", "credential", roots,
        application_provider=lambda: application,
    )
    try:
        application.start()
        initial = boundary.snapshot()
        install_id = initial["plugins"][0]["installId"]
        failed = boundary.set_enabled(initial["revision"], install_id, True)
        assert failed["desiredSaved"] is True
        assert failed["applicationState"] == "error"
        assert failed["applicationReasonCode"] == "MISSING_SERVICE"
        assert failed["plugins"][0]["state"] == "failed"
        assert failed["revision"] != initial["revision"]
        assert failed["revision"] == boundary.snapshot()["revision"]
        assert PluginDesiredStateStore(roots.user_root).read()[plugin_id] is True

        disabled = boundary.set_enabled(failed["revision"], install_id, False)
        assert disabled["applicationState"] == "applied"
        assert disabled["plugins"][0]["state"] == "disabled"
        retried = boundary.set_enabled(disabled["revision"], install_id, True)
        assert retried["applicationReasonCode"] == "MISSING_SERVICE"
        assert retried["revision"] == boundary.snapshot()["revision"]
    finally:
        application.close()


@pytest.mark.parametrize("fail_publish", [False, True])
def test_voice_resource_update_restores_dependents_and_preserves_unrelated_processes(tmp_path, fail_publish):
    roots = _roots(tmp_path)
    for name, service, requires in (
        ("voice", "sakura.tts.provider.fixture", ()),
        ("consumer", "fixture.consumer", ("sakura.tts.provider.fixture",)),
        ("unrelated", "fixture.unrelated", ()),
    ):
        _plugin_source(roots.distribution_root / "plugins/builtin", f"fixture.{name}", service,
            requires=requires, body=_simple_service_body(service, name))
    application = PluginApplicationHost(roots, "generation-voice-update", ToolRegistry())
    application.start()
    try:
        def records():
            return {item["pluginId"]: item for item in application.application.public_snapshot()["plugins"]}
        before = records()
        assert all(item["state"] == "active" for item in before.values())
        desired = PluginDesiredStateStore(roots.user_root).read()
        try:
            with application.application.prepare_voice_resources() as errors:
                paused = records()
                assert paused["fixture.voice"]["pid"] is None
                assert paused["fixture.consumer"]["pid"] is None
                assert paused["fixture.unrelated"]["pid"] == before["fixture.unrelated"]["pid"]
                assert all(item["enabled"] for item in paused.values())
                if fail_publish:
                    raise OSError("publication failed")
        except OSError:
            assert fail_publish
        assert not errors
        after = records()
        assert all(item["state"] == "active" for item in after.values())
        assert after["fixture.unrelated"]["pid"] == before["fixture.unrelated"]["pid"]
        for name in ("voice", "consumer"):
            assert after[f"fixture.{name}"]["pid"] != before[f"fixture.{name}"]["pid"]
        assert PluginDesiredStateStore(roots.user_root).read() == desired
    finally:
        application.close()


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
            "character": CharacterProfile("fixture", "Fixture", tmp_path, tmp_path / "card.md", ""),
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


def test_isolated_plugin_can_stop_owned_process_with_public_sdk(tmp_path: Path) -> None:
    roots = _roots(tmp_path)
    _plugin_source(
        roots.distribution_root / "plugins/builtin",
        "fixture.process-owner",
        "fixture.process-owner",
        body="""
import subprocess
import sys
from sakura_process import terminate_process_tree

class ProcessOwner:
    def stop_owned(self):
        process = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
        try:
            terminate_process_tree(process, timeout=0.2)
            return process.poll() is not None
        finally:
            if process.poll() is None:
                process.kill()
            process.wait(timeout=2)

class Plugin:
    def setup(self, context):
        context.provide("fixture.process-owner", ProcessOwner(), exports=("stop_owned",))
""".strip(),
    )
    manager = PluginRuntimeManager(
        roots, "generation-process-sdk", PluginInventory(roots).scan().runtime_specs
    )
    try:
        assert manager.start()["plugins"][0]["state"] == "active"
        assert manager.call_service("fixture.process-owner", "stop_owned") is True
    finally:
        manager.close()


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

class BoundaryError(RuntimeError):
    code = "MEMORY_ROUND_TRIP_MISMATCH"
    field = "category"
    details = {"content": "private memory fixture"}

class Echo:
    def __init__(self): self.calls = 0
    def echo(self, value): return {"echo": value}
    def fail(self): raise BoundaryError("boom")
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
        from app.core.diagnostics import exception_diagnostics
        fields = exception_diagnostics(failed.value, reason_code=failed.value.code, stage="plugin_call")
        assert "boom" in fields["diagnostic"]
        assert "fail:" in fields["exception_stack"]
        assert fields["cause_type"] == "BoundaryError"
        assert fields["cause_code"] == "MEMORY_ROUND_TRIP_MISMATCH"
        assert fields["validation_field"] == "category"
        assert "private memory fixture" not in repr(fields)
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
    return psutil.pid_exists(pid)


def _wait_pids_gone(pids: list[int], *, timeout: float = 2.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if all(not _pid_exists(pid) for pid in pids):
            return
        time.sleep(0.02)
    assert all(not _pid_exists(pid) for pid in pids)


def test_generation_close_acknowledges_four_real_plugins_without_timeout_multiplication(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    roots = _roots(tmp_path)
    bundled = roots.distribution_root / "plugins" / "builtin"
    for index in range(4):
        plugin_id = f"fixture.close-{index}"
        _plugin_source(
            bundled,
            plugin_id,
            f"{plugin_id}.service",
            body=_simple_service_body(f"{plugin_id}.service", str(index)),
        )
    manager = PluginRuntimeManager(
        roots,
        "generation-v4-fast-close",
        PluginInventory(roots).scan().runtime_specs,
    )
    close_acknowledgements: list[object] = []
    original_request = RpcPeer.request

    def recording_request(self, name, payload, *, timeout=3.0):  # type: ignore[no-untyped-def]
        result = original_request(self, name, payload, timeout=timeout)
        if name == "runtime.close":
            close_acknowledgements.append(result)
        return result

    monkeypatch.setattr(RpcPeer, "request", recording_request)
    snapshot = manager.start()
    pids = [int(item["pid"]) for item in snapshot["plugins"]]

    started = time.monotonic()
    manager.close()
    elapsed = time.monotonic() - started

    assert elapsed < 1.5
    assert close_acknowledgements == [None] * 4
    _wait_pids_gone(pids)
    manager.close()


def test_generation_close_unregisters_provider_from_live_tts_hub(tmp_path: Path) -> None:
    from app.core_host.runtime_logging import CORE_BRIDGE_PREFIX, install_runtime_logging

    roots = _roots(tmp_path)
    bundled = roots.distribution_root / "plugins" / "builtin"
    hub_source = Path(__file__).resolve().parents[2] / "plugins/builtin/sakura_tts_hub/plugin.py"
    _plugin_source(
        bundled, "fixture.tts-hub", "sakura.tts", requires=("sakura.host.logging",),
        body=hub_source.read_text(encoding="utf-8") + "\nPlugin = SakuraTTSHubPlugin\n",
    )
    cleanup_result = tmp_path / "provider-cleanup.json"
    _plugin_source(
        bundled, "fixture.tts-provider", "fixture.tts-provider.service", requires=("sakura.tts",),
        body=f'''
import json
from pathlib import Path

class Plugin:
    def setup(self, context):
        hub = context.get("sakura.tts")
        hub.registerProvider({{
            "providerId": "fixture.tts-provider", "serviceKey": "fixture.tts-provider.service", "label": "Fixture"
        }})
        def cleanup():
            result = hub.unregisterProvider("fixture.tts-provider", "fixture.tts-provider.service")
            Path({str(cleanup_result)!r}).write_text(json.dumps(result), encoding="utf-8")
        context.effect(cleanup)
        context.provide("fixture.tts-provider.service", object(), exports=())
''',
    )
    stream = io.BytesIO()
    bridge = install_runtime_logging(stream)
    host = PluginApplicationHost(roots, "generation-hub-cleanup", ToolRegistry())
    try:
        host.start()
        assert host.application.wait_until_loaded(timeout=3)
        snapshot = host.application.public_snapshot()
        assert all(item["state"] == "active" for item in snapshot["plugins"]), snapshot
        pids = [item["pid"] for item in snapshot["plugins"]]
    finally:
        host.close()
        bridge.close()

    records = [json.loads(line.removeprefix(CORE_BRIDGE_PREFIX))
               for line in stream.getvalue().splitlines() if line.startswith(CORE_BRIDGE_PREFIX)]
    cleanup_failures = [record for record in records
                        if record.get("attributes", {}).get("event") == "plugin.cleanup.failed"]
    assert not cleanup_failures, cleanup_failures
    assert json.loads(cleanup_result.read_text(encoding="utf-8")) == {
        "removed": True, "providerId": "fixture.tts-provider", "serviceKey": "fixture.tts-provider.service",
    }
    _wait_pids_gone(pids)


@pytest.mark.parametrize(
    "condition", ["valid", "external", "undeclared", "inactive_target", "stale_target", "not_draining", "stale_caller", "expired"],
)
def test_draining_dependency_calls_keep_identity_scope_and_deadline(
    tmp_path: Path, condition: str,
) -> None:
    from types import SimpleNamespace
    from app.plugins.runtime_v4 import _DrainingProcess, _ServiceBinding

    roots = _roots(tmp_path)
    bundled = roots.distribution_root / "plugins/builtin"
    for name in ("caller", "dependency"):
        service_key = f"fixture.{name}.service"
        _plugin_source(
            bundled, f"fixture.{name}", service_key,
            requires=("fixture.dependency.service",) if name == "caller" and condition != "undeclared" else (),
            body=_simple_service_body(service_key, name),
        )
    manager = PluginRuntimeManager(roots, "draining-scope", PluginInventory(roots).scan().runtime_specs)
    calls = []

    class Dependency:
        def call_service(self, service_key, method, args, **kwargs):  # type: ignore[no-untyped-def]
            calls.append((service_key, method, args, kwargs))
            return {"removed": True}

    caller, dependency = SimpleNamespace(scope_id="caller-process-scope"), Dependency()
    target_record = manager._records["fixture.dependency"]
    target_record.process = object() if condition == "stale_target" else dependency
    target_record.state = "disabled" if condition == "inactive_target" else "active"
    manager._services["fixture.dependency.service"] = _ServiceBinding(
        "fixture.dependency", frozenset({"unregisterProvider"}), process=dependency,
    )
    manager._closed = True
    if condition != "not_draining":
        manager._draining_processes["fixture.caller"] = _DrainingProcess(
            caller, time.monotonic() + (-1 if condition == "expired" else 0.5),
        )

    def invoke():  # type: ignore[no-untyped-def]
        if condition == "external":
            return manager.call_service("fixture.dependency.service", "unregisterProvider", "fixture.caller")
        return manager._handle_plugin_request(
            "fixture.caller", "service.call",
            {"serviceKey": "fixture.dependency.service", "method": "unregisterProvider", "args": ["fixture.caller"]},
            calling_process=object() if condition == "stale_caller" else caller,
        )

    if condition == "valid":
        assert invoke() == {"removed": True}
        assert len(calls) == 1
        assert 0 < calls[0][3]["timeout"] <= 0.5
        assert calls[0][3]["caller_id"] == "fixture.caller"
        assert calls[0][3]["caller_scope"] == "caller-process-scope"
    else:
        with pytest.raises((PluginApiError, PluginRuntimeError)) as rejected:
            invoke()
        assert rejected.value.code == ("PLUGIN_CALL_TIMEOUT" if condition == "expired" else "GENERATION_INVALIDATED")
        assert calls == []


def test_generation_close_uses_one_deadline_when_plugin_cleanup_blocks(
    tmp_path: Path,
) -> None:
    roots = _roots(tmp_path)
    bundled = roots.distribution_root / "plugins" / "builtin"
    for index in range(3):
        plugin_id = f"fixture.close-normal-{index}"
        _plugin_source(
            bundled,
            plugin_id,
            f"{plugin_id}.service",
            body=_simple_service_body(f"{plugin_id}.service", str(index)),
        )
    _plugin_source(
        bundled,
        "fixture.close-z-blocking",
        "fixture.close-z-blocking.service",
        body="""
import time

class Service: pass

class Plugin:
    def setup(self, context):
        context.effect(lambda: time.sleep(30))
        context.provide("fixture.close-z-blocking.service", Service(), exports=())
""".strip(),
    )
    manager = PluginRuntimeManager(
        roots,
        "generation-v4-bounded-close",
        PluginInventory(roots).scan().runtime_specs,
    )
    snapshot = manager.start()
    pids = [int(item["pid"]) for item in snapshot["plugins"]]

    started = time.monotonic()
    manager.close()
    elapsed = time.monotonic() - started

    assert elapsed < 1.5
    _wait_pids_gone(pids)


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


def test_effect_disposer_and_context_shutdown_release_each_resource_once(tmp_path: Path) -> None:
    from app.plugins.sakura_plugin_sdk import PluginContext

    context = PluginContext("fixture.cleanup", tmp_path, tmp_path, lambda *_: None, lambda *_: None)
    calls = []
    first = context.effect(lambda: calls.append("first"))
    second = context.effect(lambda: calls.append("second"))
    first()
    context.close()
    first()
    second()
    assert calls == ["first", "second"]


def test_initialization_keeps_primary_and_cleanup_errors_when_log_bridge_is_closed(tmp_path, monkeypatch):
    from app.core import runtime_log

    roots = _roots(tmp_path)
    _plugin_source(roots.distribution_root / "plugins/builtin", "fixture.cleanup", "fixture.cleanup.service", body='''
def release():
    raise OSError("cleanup fixture failure")

class Plugin:
    def setup(self, context):
        context.get("sakura.host.logging").close()
        context.effect(release)
        raise ValueError("primary fixture failure")
''')
    captured = []
    monkeypatch.setattr(runtime_log, "log_message", lambda *args, **kwargs: captured.append(kwargs))
    manager = PluginRuntimeManager(roots, "generation-cleanup-evidence", PluginInventory(roots).scan().runtime_specs)
    try:
        snapshot = manager.start()
        assert snapshot["plugins"][0]["state"] == "failed"
        failure = next(row["fields"] for row in captured if row.get("fields", {}).get("event") == "plugin.start.failed")
        assert "ValueError: primary fixture failure" in failure["exception_stack"]
        assert "OSError: cleanup fixture failure" in failure["exception_stack"]
        assert "release" in failure["exception_stack"]
    finally:
        manager.close()


def test_optional_observer_does_not_block_service_and_queued_work_is_owned_by_process(tmp_path: Path):
    roots = _roots(tmp_path)
    _plugin_source(roots.distribution_root / "plugins/builtin", "fixture.observer", "fixture.observer.service", body='''
import threading

class Service:
    def __init__(self):
        self.entered = threading.Event()
        self.release = threading.Event()
        self.finished = threading.Event()
    def observe(self, payload):
        self.entered.set()
        self.release.wait()
        self.finished.set()
    def entered_observer(self): return self.entered.wait(2)
    def complete(self):
        self.release.set()
        return self.finished.wait(2)

class Plugin:
    def setup(self, context):
        service = Service()
        context.on("sakura.host.timeline.changed", service.observe)
        context.provide("fixture.observer.service", service, exports=("entered_observer", "complete"))
''')
    manager = PluginRuntimeManager(roots, "generation-observer", PluginInventory(roots).scan().runtime_specs)
    snapshot = manager.start()
    pid = snapshot["plugins"][0]["pid"]
    try:
        manager.notify_host_event("sakura.host.timeline.changed", {"cursor": "fixture"})
        assert manager.call_service("fixture.observer.service", "entered_observer") is True
        assert manager.call_service("fixture.observer.service", "complete") is True
        with pytest.raises(PluginRuntimeError, match="HOST_EVENT_NAME_INVALID"):
            manager.notify_host_event("sakura.host.scope.closed", {})
    finally:
        manager.close()
    _wait_pids_gone([pid])


def test_observer_queue_is_bounded_and_does_not_consume_request_capacity():
    from app.plugins.sakura_plugin_sdk import MAX_PENDING_REQUESTS

    entered, release, responded = threading.Event(), threading.Event(), threading.Event()
    dropped = []

    def observe(_name, _payload):
        entered.set()
        release.wait()

    peer = RpcPeer(io.BytesIO(), io.BytesIO(), generation_id="bounded", plugin_id="fixture",
        request_handler=lambda *_: responded.set(), notification_handler=observe,
        notification_error_handler=lambda name, error: dropped.append((name, type(error).__name__)))
    worker = threading.Thread(target=peer._notification_loop)
    worker.start()
    notification = {"type": "notification", "generationId": "bounded", "pluginId": "fixture",
                    "name": "event.emit", "payload": {"name": "sakura.host.timeline.changed"}}
    try:
        peer._accept(notification)
        assert entered.wait(1)
        for _ in range(MAX_PENDING_REQUESTS + 1):
            peer._accept(notification)
        assert dropped == [("event.emit", "Full")]
        peer._accept({**notification, "type": "request", "id": "still-callable", "name": "service.call"})
        assert responded.wait(1)
    finally:
        peer.close()
        release.set()
        worker.join(1)
    assert not worker.is_alive()


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


def test_transport_rejects_invalid_payload_without_losing_plugin(tmp_path: Path) -> None:
    roots = _roots(tmp_path)
    _plugin_source(roots.distribution_root / "plugins/builtin", "fixture.payload", "fixture.payload", body="""
class Service:
    def echo(self, value): return value
    def large(self): return "x" * (1024 * 1024)
    def nonfinite(self): return float("nan")
class Plugin:
    def setup(self, context):
        context.provide("fixture.payload", Service(), exports=("echo", "large", "nonfinite"))
""")
    manager = PluginRuntimeManager(roots, "payload-regression", PluginInventory(roots).scan().runtime_specs)
    try:
        manager.start()
        for method, args, code in (
            ("large", (), "PLUGIN_FRAME_TOO_LARGE"),
            ("nonfinite", (), "SERVICE_PAYLOAD_INVALID"),
            ("echo", ("x" * (1024 * 1024),), "PLUGIN_FRAME_TOO_LARGE"),
        ):
            with pytest.raises((PluginRuntimeError, PluginApiError)) as rejected:
                manager.call_service("fixture.payload", method, *args)
            assert rejected.value.code == code
            assert manager.call_service("fixture.payload", "echo", "alive") == "alive"
    finally:
        manager.close()
