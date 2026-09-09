from __future__ import annotations

import shutil
import time
from pathlib import Path

import pytest

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


def test_plugin_settings_preview_uses_v4_runtime_diagnostics() -> None:
    from app.core_host.plugin_settings import _preview_plugin
    from app.plugins.inventory import InstalledPluginRecord

    unsupported = _preview_plugin(
        InstalledPluginRecord(
            "pi_bundled_666978747572655f706c7567696e", "bundled", "legacy", "legacy",
            "Legacy", "", "", "1.0.0", 2, "plugin:Legacy", False, False,
            ("com.example.legacy",), ("sakura.host.settings",),
            "API_VERSION_UNSUPPORTED", False, False,
        )
    )
    assert unsupported["enabled"] is False
    assert unsupported["supported"] is False
    assert unsupported["state"] == "failed"
    assert unsupported["reasonCode"] == "API_VERSION_UNSUPPORTED"
    assert unsupported["installId"] == "pi_bundled_666978747572655f706c7567696e"
    assert unsupported["provides"] == ["com.example.legacy"]
    assert unsupported["requires"] == ["sakura.host.settings"]
    assert unsupported["missingServices"] == []

    required = _preview_plugin(
        InstalledPluginRecord(
            "pi_user_6c6f63616c", "bundled", "required", "required",
            "Required", "", "", "1.0.0", 4, "plugin:Required", True, True,
            (), (), "READY", True, True,
        )
    )
    assert required["enabled"] is True
    assert required["state"] == "failed"
    assert required["reasonCode"] == "PLUGIN_APPLICATION_NOT_READY"

    invalid_user_required = _preview_plugin(
        InstalledPluginRecord(
            "pi_user_62726f6b656e", "user", "broken", None,
            "Invalid plugin", "", "", "0.0.0", None, "", False, False,
            (), (), "PLUGIN_MANIFEST_INVALID", False, False,
        )
    )
    assert invalid_user_required["enabled"] is False
    assert invalid_user_required["pluginId"] is None
    assert invalid_user_required["canUninstall"] is True
    assert invalid_user_required["state"] == "failed"
    assert invalid_user_required["reasonCode"] == "PLUGIN_MANIFEST_INVALID"


def test_production_application_uses_v4_host_contributions(tmp_path: Path) -> None:
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
        application.application.wait_until_loaded(timeout=5)
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
        assert application.application.wait_until_bound(timeout=5)

        result = registry.execute("fixture_echo", {"value": "hello"})
        assert result.success is True
        assert result.content == {"echo": "hello"}
    finally:
        application.close()
    assert application.application.state == "stopped"
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
        initializer_factory=lambda _root, _tools, _mcp: FailingInitializer(),
    )
    controller.enable_plugins()
    try:
        controller.begin({})
        _wait_until(lambda: controller.readiness() == "failed")
        application = controller.published_plugin_application()
        assert application is not None
        application.application.wait_until_loaded(timeout=5)

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


@pytest.mark.parametrize("cleanup_fails", [False, True])
def test_plugin_start_failure_closes_unpublished_application_resources(
    tmp_path: Path, monkeypatch, cleanup_fails: bool,
) -> None:
    from app.agent.mcp import provider
    from app.core_host import plugin_application
    from app.core_host.server import HostConfig, ReadinessController

    closed: list[str] = []

    class MCP:
        def close(self) -> None:
            closed.append("mcp")

    class PluginApplication:
        def __init__(self, *_args) -> None:
            pass

        def start(self) -> None:
            raise RuntimeError("plugin start failed")

        def close(self) -> None:
            closed.append("plugins")
            if cleanup_fails:
                raise RuntimeError("plugin cleanup failed")

    monkeypatch.setattr(provider, "start_mcp_tools_from_config", lambda *_args, **_kwargs: MCP())
    monkeypatch.setattr(plugin_application, "PluginApplicationHost", PluginApplication)
    controller = ReadinessController(
        HostConfig(RuntimeRoots(tmp_path, tmp_path), "generation-failed-start", "a" * 32),
    )
    controller.enable_mcp()
    controller.enable_plugins()
    controller.begin({})
    controller._worker.join(2)
    assert not controller._worker.is_alive()
    assert controller.readiness() == "failed"
    assert controller.published_plugin_application() is None
    assert closed == ["plugins", "mcp"]
    if cleanup_fails:
        with pytest.raises(RuntimeError, match="plugin cleanup failed"):
            controller.close()
    else:
        controller.close()
    controller.close()
    assert closed == ["plugins", "mcp"]
