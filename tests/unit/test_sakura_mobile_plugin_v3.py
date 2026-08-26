from __future__ import annotations

import shutil
import threading
from pathlib import Path
from typing import Any

from plugins.builtin.sakura_mobile import plugin as mobile_plugin


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
SOURCE_PLUGIN_ROOT = REPOSITORY_ROOT / "plugins" / "builtin" / "sakura_mobile"
PLUGIN_ID = "sakura_mobile"


def _assistant_root(tmp_path: Path, *, with_provider: bool = False) -> Path:
    root = tmp_path / "assistant"
    builtin = root / "plugins" / "builtin"
    builtin.mkdir(parents=True)
    (root / "plugins" / "__init__.py").write_text("", encoding="utf-8")
    (builtin / "__init__.py").write_text("", encoding="utf-8")
    shutil.copytree(SOURCE_PLUGIN_ROOT, builtin / PLUGIN_ID)
    if with_provider:
        provider = builtin / "mobile_provider"
        provider.mkdir()
        (provider / "__init__.py").write_text("", encoding="utf-8")
        (provider / "plugin.yaml").write_text(
            """
api: 3
id: fixture.mobile-provider
name: Fixture Mobile Provider
author: Sakura Tests
description: Worker-local mobile Service fixture
version: 1.0.0
entry: plugin:MobileProviderPlugin
enabled: true
provides:
  - sakura.mobile
requires: []
""".strip(),
            encoding="utf-8",
        )
        (provider / "plugin.py").write_text(
            """
class MobileService:
    def characters(self):
        return []

    def history(self, _character_id, limit=50):
        return []

    def chat(self, _character_id, _text, _image_data_url=""):
        return {"segments": []}

    def theme(self):
        return {}


class MobileProviderPlugin:
    def setup(self, context):
        context.provide("sakura.mobile", MobileService())
""".strip(),
            encoding="utf-8",
        )
    return root


def _plugin(snapshot: dict[str, Any], plugin_id: str = PLUGIN_ID) -> dict[str, Any]:
    return next(item for item in snapshot["plugins"] if item["pluginId"] == plugin_id)


class _Runtime:
    def set_prompt_patches(self, _values: object) -> None:
        pass

    def set_context_providers(self, _values: object) -> None:
        pass


def test_bundled_mobile_fails_when_required_mobile_service_is_missing(tmp_path: Path) -> None:
    from app.agent.tools import ToolRegistry
    from app.core_host.plugin_worker import PluginWorkerClient

    root = _assistant_root(tmp_path)
    worker = PluginWorkerClient(root, "generation-mobile-waiting")
    worker.configure_host_services(ToolRegistry(), _Runtime())
    try:
        worker.start()
        snapshot = worker.wait_until_loaded(timeout=5)
        plugin = _plugin(snapshot)
        assert plugin["state"] == "failed"
        assert plugin["reasonCode"] == "MISSING_SERVICE"
        assert plugin["sections"] == []
        assert not (root / "data" / "plugins" / PLUGIN_ID).exists()
    finally:
        worker.close()


def test_mobile_activates_and_disposes_with_worker_local_provider(tmp_path: Path) -> None:
    from app.agent.tools import ToolRegistry
    from app.core_host.plugin_worker import PluginWorkerClient

    root = _assistant_root(tmp_path, with_provider=True)
    worker = PluginWorkerClient(root, "generation-mobile-provider")
    worker.configure_host_services(ToolRegistry(), _Runtime())
    try:
        worker.start()
        snapshot = worker.wait_until_loaded(timeout=5)
        assert _plugin(snapshot)["state"] == "active"
        settings = _plugin(worker.settings_snapshot())["sections"]
        assert settings[0]["sectionId"] == PLUGIN_ID

        disabled = worker.set_plugin_enabled("fixture.mobile-provider", False)
        assert _plugin(disabled)["state"] == "failed"
        assert _plugin(disabled)["reasonCode"] == "MISSING_SERVICE"

        enabled = worker.set_plugin_enabled("fixture.mobile-provider", True)
        assert _plugin(enabled)["state"] == "active"
    finally:
        worker.close()


class _Config:
    def __init__(self) -> None:
        self.values: dict[str, Any] = {
            "enabled": False,
            "host": "127.0.0.1",
            "port": 8765,
            "token": "fixture-token",
        }
        self.handlers: list[Any] = []

    def get(self) -> dict[str, Any]:
        return dict(self.values)

    def update(self, values: dict[str, Any]) -> list[str]:
        self.values.update(values)
        return [handler(dict(self.values)) for handler in self.handlers]

    def on_change(self, handler: Any) -> None:
        self.handlers.append(handler)


class _Settings:
    def __init__(self) -> None:
        self.args: tuple[Any, ...] = ()
        self.kwargs: dict[str, Any] = {}

    def register(self, *args: Any, **kwargs: Any) -> None:
        self.args = args
        self.kwargs = kwargs


class _Context:
    def __init__(self) -> None:
        self.config = _Config()
        self.settings = _Settings()
        self.mobile_service = object()
        self.events: dict[str, Any] = {}
        self.cleanups: list[Any] = []

    def get(self, service_key: str) -> object:
        return {
            "sakura.mobile": self.mobile_service,
            "sakura.host.settings": self.settings,
        }[service_key]

    def effect(self, cleanup: Any) -> None:
        self.cleanups.append(cleanup)

    def on(self, event_name: str, callback: Any) -> None:
        self.events[event_name] = callback

    def data_path(self, relative: str) -> Path:
        return Path("/tmp/sakura-mobile-test/data/plugins/sakura_mobile") / relative


class _Server:
    def __init__(self) -> None:
        self.stopped = threading.Event()
        self.closed = False

    def serve_forever(self) -> None:
        self.stopped.wait()

    def shutdown(self) -> None:
        self.stopped.set()

    def server_close(self) -> None:
        self.closed = True


def test_mobile_config_restart_and_effect_join_server_thread(
    monkeypatch: Any,
) -> None:
    servers: list[_Server] = []

    def create_server(*_args: Any, **_kwargs: Any) -> _Server:
        server = _Server()
        servers.append(server)
        return server

    monkeypatch.setattr(mobile_plugin, "run_mobile_server", create_server)
    context = _Context()
    plugin = mobile_plugin.SakuraMobilePlugin()
    plugin.setup(context)

    context.events["sakura.host.app.started"]({})
    assert plugin.status()["running"] is False
    assert context.settings.kwargs["save"](
        {
            "enabled": True,
            "host": "127.0.0.1",
            "port": 8766,
            "token": "changed-token",
        }
    ) == ["applied"]
    assert plugin.status()["running"] is True
    thread = plugin._thread
    assert thread is not None and thread.is_alive()

    action = context.settings.kwargs["actions"]["refresh_status"]({})
    assert action["values"]["running"] == "运行中"
    assert "changed-token" in action["values"]["local_url"]

    for cleanup in reversed(context.cleanups):
        cleanup()
    assert servers[0].closed is True
    assert thread.is_alive() is False


def test_all_bundled_plugin_manifests_use_api_v3() -> None:
    from app.plugins.discovery import PluginDiscovery

    specs = PluginDiscovery(REPOSITORY_ROOT).discover()
    bundled = [spec for spec in specs if spec.source == "bundled"]
    assert bundled
    assert {spec.plugin_id for spec in bundled} >= {
        "playwright_browser",
        "sakura.memory.mem0",
        "sakura.tts",
        "sakura.tts.genie",
        "sakura.tts.gpt-sovits",
        "sakura_mobile",
    }
    assert {spec.api_version for spec in bundled} == {3}
    first_release = {
        "playwright_browser",
        "sakura.memory.mem0",
        "sakura.tts",
        "sakura.tts.genie",
        "sakura.tts.gpt-sovits",
        "sakura_mobile",
    }
    selected = [spec for spec in bundled if spec.plugin_id in first_release]
    assert len(selected) == 6
    assert all(spec.enabled and not spec.required for spec in selected)
