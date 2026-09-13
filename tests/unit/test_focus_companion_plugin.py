from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest

from app.agent.tools import ToolRegistry
from app.core_host.plugin_application import PluginApplicationHost
from app.plugins.installer import LocalPluginInstaller
from app.plugins.inventory import PluginDesiredStateStore
from app.plugins.sakura_plugin_sdk import PluginContext
from app.storage.runtime_roots import RuntimeRoots
from plugins.optional.focus_companion.plugin import (
    FocusCompanionExecutor,
    MAX_RECENT_OPERATIONS,
    SERVICE_KEY,
)
from tools.release.package_optional_plugin import build


SOURCE_PLUGIN_ROOT = Path(__file__).resolve().parents[2] / "plugins/optional/focus_companion"


def _request(operation_id: str = "operation-1", text: str = "专注 60 分钟 阅读") -> dict:
    return {
        "schemaVersion": 1,
        "operationId": operation_id,
        "generationId": "generation-1",
        "turnId": "turn-1",
        "characterId": "character-1",
        "input": {"text": text},
    }


@pytest.fixture
def executor(tmp_path: Path):
    context = PluginContext(
        "focus_companion", SOURCE_PLUGIN_ROOT, tmp_path,
        lambda *_args: None, lambda *_args: None,
    )
    service = FocusCompanionExecutor(context)
    context.provide(SERVICE_KEY, service, exports=("describe", "begin", "read", "cancel"))
    context.effect(service.close)
    try:
        yield context, service
    finally:
        context.close()


def _call(context, method, request):
    return context.call_local(SERVICE_KEY, method, [request], caller_id="sakura.core")


def test_focus_timer_waits_for_real_duration_before_returning_stable_reply(executor) -> None:
    context, service = executor
    started = time.monotonic()
    accepted = _call(context, "begin", _request(text="专注 1 秒 整理桌面"))
    assert accepted["state"] == "running"
    assert "整理桌面" in accepted["progress"]
    operation = service._operations["operation-1"]
    operation.thread.join(timeout=3)
    assert not operation.thread.is_alive()
    assert time.monotonic() - started >= 1
    final = _call(context, "read", {"operationId": "operation-1", "afterSequence": -1})
    assert final["state"] == "completed"
    assert "整理桌面" in final["reply"]["segments"][0]["text"]
    assert final["sequence"] > accepted["sequence"]
    assert _call(context, "read", {"operationId": "operation-1"}) == final
    assert _call(context, "cancel", {"operationId": "operation-1"}) == final


def test_cancel_reaches_worker_and_remains_cancelling_until_thread_exits(executor, monkeypatch) -> None:
    context, service = executor
    entered = threading.Event()
    received_stop = threading.Event()
    release = threading.Event()
    original = service._run_timer
    observed_callers = []

    def gated_worker(operation):
        observed_callers.append(context.caller_id)
        entered.set()
        if not operation.stop.wait(3):
            raise AssertionError("worker did not receive cancellation")
        received_stop.set()
        if not release.wait(3):
            raise AssertionError("worker was not released")
        original(operation)

    monkeypatch.setattr(service, "_run_timer", gated_worker)
    try:
        _call(context, "begin", _request())
        assert entered.wait(3)
        cancelling = _call(context, "cancel", {"operationId": "operation-1"})
        assert received_stop.wait(3)
        assert cancelling["state"] == "cancelling"
        operation = service._operations["operation-1"]
        assert operation.thread.is_alive()
        assert _call(context, "read", {"operationId": "operation-1"})["state"] == "cancelling"
        assert _call(context, "cancel", {"operationId": "operation-1"}) == cancelling
        assert observed_callers == [None]
    finally:
        release.set()
    operation.thread.join(timeout=3)
    assert not operation.thread.is_alive()
    cancelled = _call(context, "read", {"operationId": "operation-1"})
    assert cancelled["state"] == "cancelled"
    assert "reply" not in cancelled
    assert _call(context, "read", {"operationId": "operation-1"}) == cancelled


def test_duplicate_begin_does_not_start_another_worker_or_accept_mutated_input(executor) -> None:
    context, service = executor
    request = _request()
    _call(context, "begin", request)
    operation = service._operations["operation-1"]
    worker = operation.thread
    repeated = _call(context, "begin", request)
    assert repeated["state"] == "running"
    assert repeated["operationId"] == request["operationId"]
    assert operation.thread is worker
    request["input"]["text"] = "专注 1 秒 另一件事"
    assert operation.request.text == "专注 60 分钟 阅读"
    with pytest.raises(ValueError, match="FOCUS_OPERATION_CONFLICT"):
        _call(context, "begin", request)
    with pytest.raises(RuntimeError, match="FOCUS_COMPANION_BUSY"):
        _call(context, "begin", _request("other-operation"))
    assert list(service._operations) == ["operation-1"]


@pytest.mark.parametrize("method", ["begin", "read", "cancel"])
@pytest.mark.parametrize("caller", [None, "another.plugin"])
def test_execution_methods_require_actual_host_identity(executor, method, caller) -> None:
    context, service = executor
    request = _request(text="你好")
    request["callerId"] = "sakura.core"
    with pytest.raises(PermissionError, match="FOCUS_EXECUTOR_CALLER_FORBIDDEN"):
        context.call_local(SERVICE_KEY, method, [request], caller_id=caller)
    assert service._operations == {}


def test_cleanup_stops_and_joins_active_timer(executor) -> None:
    context, service = executor
    _call(context, "begin", _request())
    operation = service._operations["operation-1"]
    assert operation.thread.is_alive()
    context.close()
    assert operation.stop.is_set()
    assert not operation.thread.is_alive()
    assert operation.state == "cancelled"
    assert service.describe()["ready"] is False
    service.close()


def test_help_and_recent_results_do_not_grow_without_bound(executor) -> None:
    context, service = executor
    for index in range(MAX_RECENT_OPERATIONS + 3):
        result = _call(context, "begin", _request(f"operation-{index}", "你好"))
        assert result["state"] == "completed"
        assert "专注 10 秒" in result["reply"]["segments"][0]["text"]
    assert len(service._operations) == MAX_RECENT_OPERATIONS
    assert all(operation.thread is None for operation in service._operations.values())
    with pytest.raises(ValueError, match="FOCUS_OPERATION_NOT_FOUND"):
        _call(context, "read", {"operationId": "operation-0"})
    assert _call(context, "begin", _request(f"operation-{MAX_RECENT_OPERATIONS + 2}", "你好")) == result


def test_packaged_plugin_registers_executes_and_reloads_in_real_process(tmp_path: Path) -> None:
    package = tmp_path / "focus_companion.sakplugin.zip"
    build(SOURCE_PLUGIN_ROOT, package)
    distribution = tmp_path / "distribution"
    user = tmp_path / "user"
    (distribution / "plugins/builtin").mkdir(parents=True)
    user.mkdir()
    roots = RuntimeRoots(distribution, user)
    installed = LocalPluginInstaller(roots).install(package.resolve(), "zip")
    PluginDesiredStateStore(user).set(installed.plugin_id, True)
    application = PluginApplicationHost(roots, "focus-companion-test", ToolRegistry())
    try:
        application.start()
        record = next(item for item in application.public_snapshot()["plugins"] if item["pluginId"] == "focus_companion")
        assert record["state"] == "active", record
        assert application.application.execution_candidates() == [
            {"serviceKey": SERVICE_KEY, "displayName": "专注陪伴", "pluginId": "focus_companion"}
        ]
        call = application.application.call_service
        assert call(SERVICE_KEY, "describe") == {"schemaVersion": 1, "ready": True, "inputs": ["text"]}
        accepted = call(SERVICE_KEY, "begin", _request(text="你好"))
        assert accepted["state"] == "completed"
        assert call(SERVICE_KEY, "read", {"operationId": "operation-1"}) == accepted
        application.set_enabled(installed.install_id, False)
        assert application.application.execution_candidates() == []
        application.set_enabled(installed.install_id, True)
        assert len(application.application.execution_candidates()) == 1
        assert call(SERVICE_KEY, "begin", _request(text="你好")) == accepted
    finally:
        application.close()
