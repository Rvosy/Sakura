from __future__ import annotations

import io
import json
import os
import shutil
import time
from pathlib import Path

import pytest

from app.llm.prompts.types import ContextRequest
from app.storage.runtime_roots import RuntimeRoots


FIXTURE_ROOT = Path(__file__).parents[1] / "fixtures" / "runtime_v2" / "wp_4_04"


class _Runtime:
    def __init__(self) -> None:
        self.context_providers = []

    def set_context_providers(self, values) -> None:
        self.context_providers = list(values)


def _assistant_root(tmp_path: Path) -> Path:
    root = tmp_path / "assistant"
    (root / "plugins").mkdir(parents=True)
    (root / "plugins" / "__init__.py").write_text("", encoding="utf-8")
    shutil.copytree(FIXTURE_ROOT / "plugins", root / "plugins" / "builtin")
    (root / "plugins" / "builtin" / "__init__.py").write_text("", encoding="utf-8")
    return root


def _wait_until(predicate, timeout: float = 8.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.02)
    raise AssertionError("condition did not become true before the deadline")


def _runtime_records(stream: io.BytesIO) -> list[dict[str, object]]:
    from app.core_host.runtime_logging import CORE_BRIDGE_PREFIX

    records = []
    for line in stream.getvalue().splitlines():
        assert line.startswith(CORE_BRIDGE_PREFIX)
        records.append(json.loads(line.removeprefix(CORE_BRIDGE_PREFIX)))
    return records


def test_plugin_settings_preview_uses_v3_runtime_diagnostics() -> None:
    from app.core_host.plugin_settings import _preview_plugin
    from app.plugins.inventory import InstalledPluginRecord

    unsupported = _preview_plugin(
        InstalledPluginRecord(
            "pi_0123456789abcdef01234567", "bundled", "legacy", "legacy",
            "Legacy", "", "", "1.0.0", 2, "plugin:Legacy", False, False,
            ("com.example.legacy",), ("sakura.host.settings",),
            "API_VERSION_UNSUPPORTED", False, False,
        )
    )
    assert unsupported["enabled"] is False
    assert unsupported["supported"] is False
    assert unsupported["state"] == "failed"
    assert unsupported["reasonCode"] == "API_VERSION_UNSUPPORTED"
    assert unsupported["installId"] == "pi_0123456789abcdef01234567"
    assert unsupported["provides"] == ["com.example.legacy"]
    assert unsupported["requires"] == ["sakura.host.settings"]
    assert unsupported["missingServices"] == []

    required = _preview_plugin(
        InstalledPluginRecord(
            "pi_111111111111111111111111", "bundled", "required", "required",
            "Required", "", "", "1.0.0", 3, "plugin:Required", True, True,
            (), (), "READY", True, True,
        )
    )
    assert required["enabled"] is True
    assert required["state"] == "failed"
    assert required["reasonCode"] == "PLUGIN_APPLICATION_NOT_READY"

    invalid_user_required = _preview_plugin(
        InstalledPluginRecord(
            "pi_222222222222222222222222", "user", "broken", None,
            "Invalid plugin", "", "", "0.0.0", None, "", False, False,
            (), (), "PLUGIN_MANIFEST_INVALID", False, False,
        )
    )
    assert invalid_user_required["enabled"] is False
    assert invalid_user_required["pluginId"] is None
    assert invalid_user_required["canUninstall"] is True
    assert invalid_user_required["state"] == "failed"
    assert invalid_user_required["reasonCode"] == "PLUGIN_MANIFEST_INVALID"


def test_generation_private_worker_uses_only_v3_host_contributions(tmp_path: Path) -> None:
    from app.agent.tools import ToolRegistry
    from app.core_host.plugin_application import PluginApplicationHost

    registry = ToolRegistry()
    runtime = _Runtime()
    application = PluginApplicationHost(_assistant_root(tmp_path), "generation-a", registry)
    session = type("Session", (), {
        "runtime": runtime,
        "character": type("Character", (), {"id": "fixture"})(),
    })()
    try:
        application.start()
        application.bind_session(session)
        application.worker.wait_until_loaded(timeout=5)
        snapshot = application.public_snapshot()
        by_id = {item["pluginId"]: item for item in snapshot["plugins"]}

        assert by_id["fixture_plugin"]["state"] == "active"
        assert by_id["fixture_plugin"]["supported"] is True
        assert by_id["broken_plugin"]["state"] == "failed"
        assert by_id["broken_plugin"]["supported"] is False
        assert by_id["broken_plugin"]["reasonCode"] == "API_VERSION_UNSUPPORTED"
        assert set(snapshot) == {
            "schemaVersion", "revision", "state", "reasonCode", "plugins"
        }
        assert "entry" not in repr(snapshot)
        assert str(tmp_path) not in repr(snapshot)
        assert application.worker.wait_until_bound(timeout=5)

        result = registry.execute("fixture_echo", {"value": "hello"})
        assert result.success is True
        assert result.content == {"echo": "hello"}
    finally:
        application.close()
    assert application.worker.state == "stopped"
    assert registry.get("fixture_echo") is None
    assert runtime.context_providers == []


def test_assistant_failure_keeps_plugin_application_manageable(tmp_path: Path) -> None:
    from app.core_host.server import HostConfig, ReadinessController

    class FailingInitializer:
        def initialize(self, _cancel) -> object:
            raise RuntimeError("assistant fixture failure")

        def close(self) -> None:
            pass

    root = _assistant_root(tmp_path)
    controller = ReadinessController(
        HostConfig(RuntimeRoots(root, root), "generation-plugin-application", "a" * 32),
        initializer_factory=lambda _root: FailingInitializer(),
    )
    controller.enable_plugins()
    try:
        controller.begin({})
        _wait_until(lambda: controller.readiness() == "failed")
        application = controller.published_plugin_application()
        assert application is not None
        application.worker.wait_until_loaded(timeout=5)

        snapshot = application.settings_snapshot()
        fixture = next(
            item for item in snapshot["plugins"] if item["pluginId"] == "fixture_plugin"
        )
        assert fixture["state"] == "active"
        assert application.settings_save(
            "fixture_plugin",
            "general",
            {"label": "available-without-assistant"},
        ) == {
            "saved": True,
            "applicationState": "applied",
            "reasonCode": "READY",
        }

        disabled = application.set_enabled(fixture["installId"], False)
        disabled_fixture = next(
            item for item in disabled["plugins"] if item["pluginId"] == "fixture_plugin"
        )
        assert disabled_fixture["state"] == "disabled"
        assert disabled["applicationState"] == "applied"
        assert controller.published_session() is None
    finally:
        controller.close()


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
            "applicationState": "applied",
            "reasonCode": "READY",
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


def test_worker_forwards_main_and_background_logs_without_legacy_file(
    tmp_path: Path,
) -> None:
    from app.core.runtime_log import RUNTIME_LOG_PATH_KEY
    from app.core_host.plugin_worker import PluginWorkerClient
    from app.core_host.runtime_logging import install_runtime_logging

    root = tmp_path / "assistant"
    plugin_root = root / "plugins" / "user" / "log_fixture"
    plugin_root.mkdir(parents=True)
    (plugin_root / "plugin.yaml").write_text(
        """
id: log_fixture
name: Log Fixture
author: Sakura Tests
description: Worker log forwarding fixture
version: 1.0.0
api: 3
enabled: true
priority: 100
entry: plugin:LogFixture
provides: []
requires: []
""".strip(),
        encoding="utf-8",
    )
    (plugin_root / "plugin.py").write_text(
        r"""
import threading
from app.core.runtime_log import log_event


def _emit_background():
    log_event(
        "TTS",
        "发送 GPT-SoVITS 请求",
        {
            "provider": "gpt_sovits",
            "text_chars": 41,
            "attempt": 1,
            "api_url": "http://127.0.0.1:9880/tts",
            "weights_path": r"D:\private\voice.pth",
        },
    )


class LogFixture:
    def setup(self, _context):
        log_event(
            "TTS",
            "发送 GPT-SoVITS 请求",
            {"provider": "gpt_sovits", "text_chars": 31, "attempt": 1},
        )
        thread = threading.Thread(target=_emit_background)
        thread.start()
        thread.join()
""".strip(),
        encoding="utf-8",
    )

    stream = io.BytesIO()
    runtime_logging = install_runtime_logging(stream)
    worker = PluginWorkerClient(root, "generation-log-forwarding")
    try:
        worker.start()
        snapshot = worker.wait_until_loaded(timeout=5)
        assert snapshot["state"] == "ready"
    finally:
        worker.close()
        runtime_logging.close()

    tts_records = [
        record
        for record in _runtime_records(stream)
        if record.get("event") == "tts.request.started"
    ]
    assert [record["attributes"]["text_chars"] for record in tts_records] == [
        31,
        41,
    ]
    assert all(
        record["attributes"]["provider"] == "gpt_sovits"
        for record in tts_records
    )
    persisted = stream.getvalue().decode("utf-8")
    assert "127.0.0.1" not in persisted
    assert "voice.pth" not in persisted
    assert not Path(os.environ[RUNTIME_LOG_PATH_KEY]).exists()


def test_worker_runtime_log_frames_are_generation_scoped_and_best_effort(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.core_host import plugin_worker as worker_module

    captured: list[dict[str, object]] = []
    monkeypatch.setattr(
        worker_module,
        "forward_runtime_log_record",
        lambda payload: captured.append(dict(payload)) or True,
    )
    worker = worker_module.PluginWorkerClient(tmp_path, "generation-current")
    payload = {
        "severity": "info",
        "verbosity": "info",
        "channel": "tts",
        "event": "tts.request.started",
        "message": "fixed",
    }
    valid = {
        "kind": "runtime.log",
        "generationId": "generation-current",
        "token": "worker-token",
        "payload": payload,
    }

    worker._handle_runtime_log(valid, "worker-token")
    worker._handle_runtime_log({**valid, "generationId": "generation-old"}, "worker-token")
    worker._handle_runtime_log({**valid, "token": "wrong"}, "worker-token")
    worker._handle_runtime_log({**valid, "unexpected": True}, "worker-token")

    assert captured == [payload]


def test_runtime_v2_rejects_v2_manifest_without_importing_or_feature_rpc(
    tmp_path: Path,
) -> None:
    from app.core_host.plugin_worker_runtime import PluginWorkerRuntime, WorkerRuntimeError

    root = tmp_path / "assistant"
    plugin_root = root / "plugins" / "user" / "legacy_fixture"
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

        with pytest.raises(WorkerRuntimeError) as removed_lifecycle:
            runtime.handle(
                "lifecycle.set_enabled",
                {"pluginId": "legacy_fixture", "enabled": False},
            )
        assert removed_lifecycle.value.code == "PLUGIN_COMMAND_UNKNOWN"
    finally:
        runtime.close()


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

        failed = registry.execute("fixture_echo", {"value": "__hang__"})
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
        failed = registry.execute("fixture_echo", {"value": "__error__"})
        assert failed.success is False
        assert failed.reason_code == "PLUGIN_CALLBACK_IO_FAILED"
        assert "secret" not in repr(failed).lower()
        assert "browser.exe" not in repr(failed).lower()
    finally:
        worker.close()


def test_plugin_tool_executes_directly_in_assistant_mode(tmp_path: Path) -> None:
    from app.agent.tools import ToolRegistry
    from app.core_host.plugin_worker import PluginWorkerClient

    registry = ToolRegistry()
    runtime = _Runtime()
    worker = PluginWorkerClient(_assistant_root(tmp_path), "generation-a")
    worker.configure_host_services(registry, runtime)
    try:
        worker.start()
        worker.wait_until_loaded(timeout=5)
        assert registry.get("fixture_echo") is not None
        result = registry.execute("fixture_echo", {"value": "direct"})
        assert result.success and result.content == {"echo": "direct"}
    finally:
        worker.close()
