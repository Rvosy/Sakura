from __future__ import annotations

import threading
from pathlib import Path

import psutil
import pytest

from app.plugins.inventory import PluginInventory
from app.plugins.runtime_v4 import PluginRuntimeError, PluginRuntimeManager
from app.storage.runtime_roots import RuntimeRoots


PLUGIN = "fixture.bound-lifecycle"
SERVICE = "fixture.bound-lifecycle.service"


def _manager(tmp_path: Path) -> PluginRuntimeManager:
    distribution = tmp_path / "distribution"
    user = tmp_path / "user"
    plugin = distribution / "plugins" / "builtin" / PLUGIN
    plugin.mkdir(parents=True)
    user.mkdir()
    (plugin / "plugin.yaml").write_text(
        f"api: 4\nid: {PLUGIN}\nname: Bound lifecycle\nversion: 1.0.0\n"
        f"entry: plugin:Plugin\nprovides: [{SERVICE}]\nrequires: []\n", encoding="utf-8",
    )
    (plugin / "plugin.py").write_text(
        '''
import os
import subprocess
import sys

class Service:
    def child(self):
        child = subprocess.Popen([sys.executable, "-c", "import threading; threading.Event().wait()"])
        return child.pid
    def crash(self):
        os._exit(9)
    def ping(self):
        return "ready"

class Plugin:
    def setup(self, context):
        context.provide("fixture.bound-lifecycle.service", Service(), exports=("child", "crash", "ping"))
''', encoding="utf-8",
    )
    roots = RuntimeRoots(distribution, user)
    return PluginRuntimeManager(roots, "bound-lifecycle-test", PluginInventory(roots).scan().runtime_specs, call_timeout=0.3)


@pytest.mark.parametrize("source", ["process_exit", "manager_close", "concurrent_stop"])
def test_bound_stop_waits_for_removed_process_cleanup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, source: str,
) -> None:
    manager = _manager(tmp_path)
    cleanup_entered = threading.Event()
    release_cleanup = threading.Event()
    wait_entered = threading.Event()
    stopped = threading.Event()
    observed = threading.Event()
    errors = []
    closing_thread = None
    stopping_thread = None
    child = None
    scopes_cleared = []
    try:
        manager.start()
        identity = manager.service_identity(SERVICE)
        process = manager._records[PLUGIN].process
        assert process is not None
        child = psutil.Process(manager.call_service(SERVICE, "child"))
        original_clear = manager._clear_plugin_scope

        def clear_scope(plugin_id):
            scopes_cleared.append(plugin_id)
            original_clear(plugin_id)

        monkeypatch.setattr(manager, "_clear_plugin_scope", clear_scope)
        method = "terminate_after_transport_failure" if source == "process_exit" else "close"
        original_cleanup = getattr(process, method)

        def gated_cleanup(*args, **kwargs):
            cleanup_entered.set()
            assert release_cleanup.wait(5)
            return original_cleanup(*args, **kwargs)

        original_wait = getattr(process, "wait_for_cleanup", lambda: None)

        def observed_wait():
            wait_entered.set()
            observed.set()
            original_wait()

        monkeypatch.setattr(process, method, gated_cleanup)
        monkeypatch.setattr(process, "wait_for_cleanup", observed_wait, raising=False)
        if source == "process_exit":
            with pytest.raises(PluginRuntimeError):
                manager.call_service(SERVICE, "crash")
        else:
            closing_thread = threading.Thread(target=manager.close)
            closing_thread.start()
        assert cleanup_entered.wait(3)
        with pytest.raises(PluginRuntimeError):
            manager.service_identity(SERVICE)

        def stop_bound():
            try:
                if source == "concurrent_stop":
                    manager._stop_process(PLUGIN, reason="EXECUTOR_PROTOCOL_FAILED", failed=True)
                else:
                    manager.stop_bound_service(SERVICE, identity, reason="EXECUTOR_PROTOCOL_FAILED")
            except BaseException as error:
                errors.append(error)
            finally:
                stopped.set()
                observed.set()

        stopping_thread = threading.Thread(target=stop_bound)
        stopping_thread.start()
        assert observed.wait(2)
        assert wait_entered.is_set(), "service removal must not be treated as process cleanup completion"
        assert not stopped.is_set()
        assert scopes_cleared == []
        assert child.is_running()
        release_cleanup.set()
        assert stopped.wait(3)
        assert errors == []
        assert process._process.poll() is not None
        child.wait(timeout=3)
    finally:
        release_cleanup.set()
        if stopping_thread is not None:
            stopping_thread.join(3)
        if closing_thread is not None:
            closing_thread.join(3)
        manager.close()
        if child is not None:
            try:
                child.kill()
                child.wait(timeout=3)
            except psutil.NoSuchProcess:
                pass


def test_old_identity_never_stops_a_same_id_replacement(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    try:
        manager.start()
        previous = manager.service_identity(SERVICE)
        manager.reload_plugin(PLUGIN)
        current = manager.service_identity(SERVICE)
        assert current != previous
        manager.stop_bound_service(SERVICE, previous, reason="EXECUTOR_PROTOCOL_FAILED")
        assert manager.service_identity(SERVICE) == current
        assert manager.call_service(SERVICE, "ping") == "ready"
    finally:
        manager.close()


def test_old_exit_tail_preserves_reloaded_scopes_and_consumers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    consumer_id = "fixture.bound-consumer"
    consumer_service = "fixture.bound-consumer.service"
    host_service = "sakura.host.fixture_registration"
    distribution = tmp_path / "distribution"
    user = tmp_path / "user"
    user.mkdir()
    for plugin_id, service_key, dependencies in (
        (PLUGIN, SERVICE, [host_service]),
        (consumer_id, consumer_service, [host_service, SERVICE]),
    ):
        plugin = distribution / "plugins" / "builtin" / plugin_id
        plugin.mkdir(parents=True)
        (plugin / "plugin.yaml").write_text(
            f"api: 4\nid: {plugin_id}\nname: Exit tail fixture\nversion: 1.0.0\n"
            f"entry: plugin:Plugin\nprovides: [{service_key}]\n"
            f"requires: [{', '.join(dependencies)}]\n", encoding="utf-8",
        )
        (plugin / "plugin.py").write_text(
            f'''
import os

class Service:
    def crash(self):
        os._exit(9)
    def ping(self):
        return "ready"

class Plugin:
    def setup(self, context):
        context.get("{host_service}").register(context.plugin_id)
        context.provide("{service_key}", Service(), exports=("crash", "ping"))
''', encoding="utf-8",
        )

    class Registrations:
        def __init__(self):
            self.active = {}
            self.sequence = 0

        def register(self, plugin_id):
            self.sequence += 1
            registration_id = f"registration-{self.sequence}"
            self.active[registration_id] = plugin_id
            return {"registrationId": registration_id}

        def unregister(self, registration_id):
            self.active.pop(registration_id, None)

    roots = RuntimeRoots(distribution, user)
    manager = PluginRuntimeManager(
        roots, "exit-tail-test", PluginInventory(roots).scan().runtime_specs,
        call_timeout=0.3,
    )
    registrations = Registrations()
    manager.install_host_service(host_service, registrations, exports=("register", "unregister"))
    cleanup_done = threading.Event()
    release_tail = threading.Event()
    tail_done = threading.Event()
    try:
        manager.start()
        old_provider = manager._records[PLUGIN].process
        old_consumer = manager._records[consumer_id].process
        assert old_provider is not None and old_consumer is not None
        original_cleanup = old_provider.terminate_after_transport_failure
        original_exit = old_provider._on_exit

        def gate_exit_tail():
            original_cleanup()
            cleanup_done.set()
            assert release_tail.wait(8)

        def observe_exit(*args):
            try:
                original_exit(*args)
            finally:
                tail_done.set()

        monkeypatch.setattr(old_provider, "terminate_after_transport_failure", gate_exit_tail)
        monkeypatch.setattr(old_provider, "_on_exit", observe_exit)
        with pytest.raises(PluginRuntimeError):
            manager.call_service(SERVICE, "crash")
        assert cleanup_done.wait(3)
        manager.reload_plugin(PLUGIN)
        replacement_provider = manager._records[PLUGIN].process
        replacement_consumer = manager._records[consumer_id].process
        assert replacement_provider is not None and replacement_provider is not old_provider
        assert replacement_consumer is not None and replacement_consumer is not old_consumer
        replacement_registrations = dict(registrations.active)
        assert sorted(replacement_registrations.values()) == sorted([PLUGIN, consumer_id])

        release_tail.set()
        assert tail_done.wait(3)
        assert registrations.active == replacement_registrations
        assert manager._records[PLUGIN].process is replacement_provider
        assert manager._records[consumer_id].process is replacement_consumer
        assert manager.call_service(SERVICE, "ping") == "ready"
        assert manager.call_service(consumer_service, "ping") == "ready"
    finally:
        release_tail.set()
        manager.close()


@pytest.mark.parametrize("source", ["close", "transport_failure", "process_exit"])
def test_cleanup_failure_wakes_waiters_and_blocks_same_id_restart(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, source: str,
) -> None:
    from app.core import runtime_log

    manager = _manager(tmp_path)
    process = None
    child = None
    original_close_job = None
    failure = OSError("fixture cleanup I/O failure")
    error_logged = threading.Event()
    original_log = runtime_log.log_event

    def log_cleanup(*args, **kwargs):
        original_log(*args, **kwargs)
        if kwargs.get("event") == "plugin.cleanup.failed":
            error_logged.set()

    try:
        manager.start()
        identity = manager.service_identity(SERVICE)
        process = manager._records[PLUGIN].process
        child = psutil.Process(manager.call_service(SERVICE, "child"))
        original_close_job = process._close_windows_job

        def failed_cleanup():
            raise failure

        monkeypatch.setattr(process, "_close_windows_job", failed_cleanup)
        monkeypatch.setattr(runtime_log, "log_event", log_cleanup)
        if source == "process_exit":
            with pytest.raises(PluginRuntimeError):
                manager.call_service(SERVICE, "crash")
            assert error_logged.wait(2)
        elif source == "transport_failure":
            with pytest.raises(PluginRuntimeError, match="PLUGIN_CLEANUP_FAILED") as raised:
                process.terminate_after_transport_failure()
            assert raised.value.__cause__ is failure

        with pytest.raises(PluginRuntimeError, match="PLUGIN_CLEANUP_FAILED") as raised:
            manager.stop_bound_service(SERVICE, identity, reason="EXECUTOR_PROTOCOL_FAILED")
        assert raised.value.__cause__ is failure
        assert process._cleanup_complete.is_set()
        with pytest.raises(PluginRuntimeError, match="PLUGIN_CLEANUP_FAILED") as waiter:
            process.wait_for_cleanup()
        assert waiter.value.__cause__ is failure
        with pytest.raises(PluginRuntimeError, match="PLUGIN_CLEANUP_FAILED"):
            process.close()
        assert manager._draining_processes[PLUGIN] is process
        state = manager.set_enabled(PLUGIN, True)["plugins"][0]
        assert state["state"] == "failed"
        assert state["reasonCode"] == "PLUGIN_CLEANUP_FAILED"
        assert manager._records[PLUGIN].process is None
        with pytest.raises(PluginRuntimeError, match="PLUGIN_CLEANUP_FAILED"):
            manager.reload_plugin(PLUGIN)
        assert manager._draining_processes[PLUGIN] is process
    finally:
        if original_close_job is not None:
            original_close_job()
        if child is not None:
            try:
                child.kill()
                child.wait(timeout=3)
            except psutil.NoSuchProcess:
                pass
        if process is not None and process._process is not None:
            if process._process.poll() is None:
                process._process.kill()
            process._process.wait(timeout=3)
            if process._peer is not None:
                process._peer.close()
            for stream in (process._process.stdin, process._process.stdout):
                if stream is not None:
                    stream.close()
        try:
            manager.close()
        except PluginRuntimeError as error:
            assert error.code == "PLUGIN_CLEANUP_FAILED"
