from __future__ import annotations

import threading
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from app.config.core_config_reader import CoreConfigReader
from app.config.executor_settings import ExecutorSettingsRepository, read_executor_selection
from app.core_host.assistant_adapter import ReadinessResult
from app.core_host.executor_settings import ExecutorSettingsBoundary
from app.core_host.server import HostConfig, ReadinessController
from app.storage.runtime_roots import RuntimeRoots


CREDENTIAL = "0123456789abcdef0123456789abcdef"
SERVICE = "example.rules"


def _request(name: str, payload: dict[str, object]) -> dict[str, object]:
    return {
        "protocolMajor": 2, "protocolMinor": 2, "kind": "request",
        "generationId": "executor-test", "generationCredential": CREDENTIAL,
        "id": name, "name": name, "payload": payload,
        "deadlineMs": 3000, "priority": "interactive",
    }


def test_selection_preserves_unrelated_settings_and_ignores_unneeded_models(tmp_path: Path) -> None:
    config = tmp_path / "config"
    config.mkdir()
    (config / "system_config.yaml").write_text("config_version: 1\nother: {keep: true}\n", encoding="utf-8")
    api = config / "api.yaml"
    api.write_text("not: [valid yaml", encoding="utf-8")
    (config / "characters.yaml").write_text("current_character_id: test\n", encoding="utf-8")
    ExecutorSettingsRepository(tmp_path).save(SERVICE)
    assert read_executor_selection(tmp_path) == SERVICE
    assert yaml.safe_load((config / "system_config.yaml").read_text(encoding="utf-8"))["other"] == {"keep": True}
    assert api.read_text(encoding="utf-8") == "not: [valid yaml"
    result = CoreConfigReader().read(tmp_path)
    assert result.config_problem is None
    assert result.executor_key == SERVICE
    assert result.current_character_id == "test"
    assert result.provider_selection is None
    (config / "characters.yaml").write_text("current_character_id: []\n", encoding="utf-8")
    assert CoreConfigReader().read(tmp_path).config_problem.code == "CONFIG_DATA_INVALID"


def test_pending_selection_is_persisted_without_claiming_it_is_active(tmp_path: Path) -> None:
    current = {"serviceKey": "", "readiness": "ready", "code": "READY"}
    candidates = [{"serviceKey": SERVICE, "displayName": "规则回复", "pluginId": "rules"}]
    applied = []
    boundary = ExecutorSettingsBoundary(
        "executor-test", CREDENTIAL, tmp_path,
        application_provider=lambda: SimpleNamespace(execution_candidates=lambda: candidates),
        status_provider=lambda: dict(current), runtime_apply=lambda: applied.append(SERVICE),
    )
    result = boundary.handle(_request("settings.executor.save", {"serviceKey": SERVICE}))
    assert result["ok"] is True
    assert applied == [SERVICE]
    assert result["payload"]["state"] == "pending"
    assert result["payload"]["selected_service_key"] == SERVICE
    assert result["payload"]["applied_service_key"] == ""
    current.update(serviceKey=SERVICE)
    assert boundary.snapshot()["state"] == "ready"
    current.update(pending=True)
    assert boundary.snapshot()["state"] == "pending"
    current.update(pending=False)
    candidates.clear()
    current.update(serviceKey=None, readiness="setup_required", code="EXECUTOR_UNAVAILABLE")
    missing = boundary.snapshot()
    assert missing["state"] == "unavailable"
    assert missing["selected_service_key"] == SERVICE
    assert boundary.handle(_request("settings.executor.save", {"serviceKey": "missing"}))["error"]["code"] == "EXECUTOR_UNAVAILABLE"
    assert read_executor_selection(tmp_path) == SERVICE
    assert boundary.handle(_request("settings.executor.save", {"serviceKey": []}))["ok"] is False
    stale = _request("settings.executor.get", {})
    stale["generationId"] = "previous"
    with pytest.raises(RuntimeError, match="GENERATION_IDENTITY_MISMATCH"):
        boundary.handle(stale)


def test_model_updates_preserve_independent_session_and_switch_recreates_only_session(tmp_path: Path) -> None:
    repository = ExecutorSettingsRepository(tmp_path)
    repository.save(SERVICE)
    initialized = threading.Event()
    sessions = []

    class Initializer:
        retired = 0

        def initialize(self, _cancel):
            key = read_executor_selection(tmp_path)
            session = SimpleNamespace(executor_key=key) if key else None
            sessions.append(session)
            initialized.set()
            return ReadinessResult(
                state="ready" if key else "setup_required",
                code="READY" if key else "PROVIDER_SETUP_REQUIRED", message="", retryable=False,
                current_character_summary=None, current_character_presentation=None, session=session,
            )

        def retire_session(self):
            self.retired += 1

        def close(self):
            pass

    initializer = Initializer()
    controller = ReadinessController(
        HostConfig(RuntimeRoots(tmp_path, tmp_path), "executor-test", CREDENTIAL),
        initializer_factory=lambda *_: initializer,
    )
    try:
        controller.begin({})
        assert initialized.wait(2)
        controller._worker.join(2)
        original = controller.published_session()
        controller.apply_provider_configuration()
        assert controller.published_session() is original
        assert initializer.retired == 0
        repository.save("")
        controller.apply_executor_configuration()
        assert initializer.retired == 1
        assert controller.published_session() is None
        assert controller.readiness() == "setup_required"
        assert controller.executor_status()["code"] == "PROVIDER_SETUP_REQUIRED"
    finally:
        controller.close()


def test_selection_saved_during_initialization_is_applied_after_initial_result(tmp_path: Path) -> None:
    entered = threading.Event()
    release = threading.Event()
    retired = []
    published_keys = []
    repository = ExecutorSettingsRepository(tmp_path)
    repository.save("")

    class Initializer:
        def initialize(self, _cancel):
            key = read_executor_selection(tmp_path)
            if not entered.is_set():
                entered.set()
                assert release.wait(3)
            return ReadinessResult(
                state="ready", code="READY", message="", retryable=False,
                current_character_summary=None, current_character_presentation=None,
                session=SimpleNamespace(executor_key=key),
            )

        def retire_session(self):
            retired.append(True)

        def close(self):
            release.set()

    controller = ReadinessController(
        HostConfig(RuntimeRoots(tmp_path, tmp_path), "executor-test", CREDENTIAL),
        initializer_factory=lambda *_: Initializer(),
    )
    controller.set_session_published_callback(
        lambda: published_keys.append(controller.published_session().executor_key)
    )
    try:
        controller.begin({})
        assert entered.wait(2)
        repository.save(SERVICE)
        controller.apply_executor_configuration()
        release.set()
        controller._worker.join(2)
        assert controller.published_session().executor_key == SERVICE
        assert retired == [True]
        assert published_keys == [SERVICE]
    finally:
        release.set()
        controller.close()


@pytest.mark.parametrize("binding_fails", [False, True])
def test_session_is_published_only_after_application_binding(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, binding_fails: bool,
) -> None:
    from app.core_host import plugin_application
    from app.config import web_plugin_migration

    entered = threading.Event()
    release = threading.Event()
    retired = []

    class Initializer:
        def set_plugin_application(self, application):
            self.application = application

        def initialize(self, _cancel):
            return ReadinessResult(
                state="ready", code="READY", message="", retryable=False,
                current_character_summary=None, current_character_presentation=None,
                session=SimpleNamespace(executor_key=SERVICE),
            )

        def retire_session(self):
            retired.append(True)

        def close(self):
            release.set()

    class Application:
        unbound = 0

        def start(self):
            pass

        def bind_session(self, _session):
            entered.set()
            assert release.wait(3)
            if binding_fails:
                raise RuntimeError("binding failed")

        def unbind_session(self):
            self.unbound += 1

        def close(self):
            pass

    application = Application()
    initializer = Initializer()
    monkeypatch.setattr(plugin_application, "PluginApplicationHost", lambda *_: application)
    monkeypatch.setattr(web_plugin_migration, "prepare_bundled_web_plugin", lambda *_: None)
    controller = ReadinessController(
        HostConfig(RuntimeRoots(tmp_path, tmp_path), "executor-test", CREDENTIAL),
        initializer_factory=lambda *_: initializer,
    )
    controller.enable_plugins()
    try:
        controller.begin({})
        assert entered.wait(2)
        assert controller.published_session() is None
        assert controller.readiness() == "initializing"
        assert initializer.application is application
        release.set()
        controller._worker.join(2)
        if binding_fails:
            assert controller.published_session() is None
            assert controller.executor_status()["code"] == "EXECUTOR_BIND_FAILED"
            assert retired == [True]
            assert application.unbound == 1
        else:
            assert controller.published_session().executor_key == SERVICE
            assert retired == []
    finally:
        release.set()
        controller.close()


def test_settings_do_not_report_expired_executor_binding_as_ready(tmp_path: Path) -> None:
    class ExpiredExecutor:
        def validate_binding(self):
            raise RuntimeError("old instance")

    controller = ReadinessController(HostConfig(RuntimeRoots(tmp_path, tmp_path), "executor-test", CREDENTIAL))
    with controller._lock:
        controller._session = SimpleNamespace(executor_key=SERVICE, executor=ExpiredExecutor())
        controller._readiness = "ready"
    assert controller.executor_status() == {
        "serviceKey": SERVICE, "readiness": "setup_required", "code": "EXECUTOR_BINDING_EXPIRED",
    }
    controller.close()
