from __future__ import annotations

from datetime import datetime
from threading import Event
from types import SimpleNamespace

import pytest

from app.core_host.assistant_adapter import ReadinessResult
from app.core_host.lifecycle_history import LifecycleHistory
from app.core_host.server import ControlDispatcher, HostConfig, ReadinessController
from app.storage.paths import StoragePaths
from app.storage.runtime_roots import RuntimeRoots
from app.storage.timeline import TimelineKind, TimelineStore


def rows(root, character_id="sakura"):
    return TimelineStore(StoragePaths(root).timeline_database()).read_all(character_id)


@pytest.mark.parametrize("shutdown_payload,expected", [
    ({"appExiting": True}, ["app.started", "app.closed"]),
    ({"appExiting": False}, ["app.started"]),
    ({}, ["app.started"]),
    ({"appExiting": "true"}, ["app.started"]),
    (None, ["app.started"]),  # EOF/crash cleanup is not a confirmed app exit.
])
def test_published_session_and_confirmed_app_exit_write_character_history(tmp_path, shutdown_payload, expected):
    session = SimpleNamespace(character=SimpleNamespace(id="sakura"))

    class Initializer:
        def initialize(self, _cancel):
            return ReadinessResult("ready", "READY", "ready", False, None, session=session)

        def close(self):
            pass

    config = HostConfig(RuntimeRoots(tmp_path, tmp_path), "test-generation", "a" * 32)
    dispatcher = ControlDispatcher(config, initializer_factory=lambda *_: Initializer())

    def request(name, payload):
        return {"protocolMajor": 2, "protocolMinor": 1, "kind": "request",
                "generationId": config.generation_id, "generationCredential": config.generation_credential,
                "id": name, "name": name, "payload": payload, "deadlineMs": 3000, "priority": "control"}

    try:
        hello, _ = dispatcher.dispatch(request("system.hello", {
            "protocol": {"major": 2, "minMinor": 0, "maxMinor": 1},
            "requiredCapabilities": ["system.hello", "system.shutdown", "core.initialize"],
            "optionalCapabilities": [],
        }))
        assert hello["ok"]
        assert dispatcher.dispatch(request("core.initialize", {}))[0]["ok"]
        dispatcher._readiness._worker.join(3)
        assert dispatcher.published_session() is session
        assert [row.payload["eventType"] for row in rows(tmp_path)] == ["app.started"]
        dispatcher.dispatch(request("core.initialize", {}))  # No duplicate opening.
        if shutdown_payload is not None:
            for _ in range(2):
                response, stop = dispatcher.dispatch(request("system.shutdown", shutdown_payload))
                assert response["ok"] and stop
    finally:
        dispatcher.close()

    entries = rows(tmp_path)
    assert [row.payload["eventType"] for row in entries] == expected
    assert all(row.kind is TimelineKind.SYSTEM and row.origin == "host" for row in entries)
    assert all(datetime.fromisoformat(row.created_at).tzinfo is not None for row in entries)
    assert len({row.turn_id for row in entries}) == len(entries)
    assert rows(tmp_path, "other-character") == []


def test_new_core_generation_records_reconnection_instead_of_a_new_app_opening(tmp_path):
    first = LifecycleHistory(tmp_path, 1)
    first.start("sakura")
    second = LifecycleHistory(tmp_path, 2)
    second.start("sakura")
    second.start("sakura")
    second.finish("sakura")
    assert [row.payload["eventType"] for row in rows(tmp_path)] == [
        "app.started", "app.reconnected", "app.closed",
    ]


def test_visual_session_is_recorded_even_when_assistant_has_not_started(tmp_path, monkeypatch):
    entered = Event()
    release = Event()
    presentation = dict(schemaVersion=2, generationId="generation", characterId="sakura",
                        displayName="Sakura", initialMessage="你好。", themeTokens={},
                        visual=None, visualReasonCode="VISUAL_RESOURCE_MISSING")

    class Application:
        def start_character_presentation(self):
            return presentation

        def start_assistant(self):
            entered.set()
            assert release.wait(3)

        def close(self):
            release.set()

    monkeypatch.setattr("app.core_host.plugin_application.PluginApplicationHost", lambda *_, **__: Application())
    controller = ReadinessController(HostConfig(RuntimeRoots(tmp_path, tmp_path), "generation", "a" * 32))
    controller.enable_plugins()
    try:
        controller.begin({})
        assert entered.wait(3)
        assert controller.published_session() is None
        assert [row.payload["eventType"] for row in rows(tmp_path)] == ["app.started"]
        controller.record_normal_shutdown()
    finally:
        controller.close()
    assert [row.payload["eventType"] for row in rows(tmp_path)] == ["app.started", "app.closed"]


def test_shutdown_before_initialization_prevents_a_late_opening(tmp_path):
    history = LifecycleHistory(tmp_path, 1)
    history.finish(None)
    history.start("sakura")
    assert not StoragePaths(tmp_path).timeline_database().exists()


def test_history_write_failure_is_reported_without_blocking_lifecycle(tmp_path, monkeypatch):
    history = LifecycleHistory(tmp_path, 1)
    reports = []

    def fail():
        raise OSError("test database unavailable")

    monkeypatch.setattr(history._store, "initialize", fail)
    monkeypatch.setattr("app.core_host.lifecycle_history.log_event", lambda *args, **kwargs: reports.append((args, kwargs)))
    history.start("sakura")
    history.finish("sakura")
    assert [args[2]["event_type"] for args, _ in reports] == ["app.started", "app.closed"]
    assert all(args[2]["reason_code"] == "LIFECYCLE_HISTORY_WRITE_FAILED" for args, _ in reports)
