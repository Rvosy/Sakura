from __future__ import annotations

import threading
from pathlib import Path
from typing import Any
from unittest.mock import Mock

from plugins.builtin.sakura_mobile import plugin as mobile_plugin


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
SOURCE_PLUGIN_ROOT = REPOSITORY_ROOT / "plugins" / "builtin" / "sakura_mobile"
PLUGIN_ID = "sakura_mobile"


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
        self.artifacts = object()
        self.events: dict[str, Any] = {}
        self.cleanups: list[Any] = []

    def get(self, service_key: str) -> object:
        return {
            "sakura.host.mobile": self.mobile_service,
            "sakura.host.artifacts": self.artifacts,
            "sakura.host.logging": Mock(),
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


def test_bundled_plugin_manifests_are_all_v4_defaults() -> None:
    from app.plugins.discovery import PluginDiscovery

    specs = PluginDiscovery(REPOSITORY_ROOT).discover()
    bundled = [spec for spec in specs if spec.source == "bundled"]
    assert bundled
    assert {spec.plugin_id for spec in bundled} == {
        "sakura.memory.mem0",
        "sakura.tts",
        "sakura.tts.genie",
        "sakura.tts.gpt-sovits",
        "sakura_mobile",
    }
    assert all(spec.api_version == 4 for spec in bundled)
    assert all(spec.enabled and not spec.required for spec in bundled)


def test_mobile_status_is_quiet_and_bind_failure_is_logged(monkeypatch) -> None:
    context = _Context()
    plugin = mobile_plugin.SakuraMobilePlugin()
    plugin.setup(context)
    for _ in range(20):
        plugin.refresh_settings_status({})
    assert plugin._logger.mock_calls == []
    def fail(*args, **kwargs):
        raise OSError("private bind detail")
    monkeypatch.setattr(mobile_plugin, "run_mobile_server", fail)
    context.config.update({"enabled": True})
    assert plugin.status()["running"] is False
    plugin._logger.error.assert_called_once()
    assert plugin._logger.error.call_args.kwargs["fields"]["reason_code"] == "MOBILE_LISTEN_FAILED"
    assert "fixture-token" not in str(plugin._logger.mock_calls)
    assert "private bind detail" not in str(plugin._logger.mock_calls)


def test_mobile_http_activity_uses_host_logger_without_access_file(tmp_path) -> None:
    import urllib.request
    from plugins.builtin.sakura_mobile.server import run_mobile_server

    logger = Mock()
    server = run_mobile_server(tmp_path, object(), object(), host="127.0.0.1", port=0, token="private-token", logger=logger)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        for _ in range(3):
            with urllib.request.urlopen(f"http://127.0.0.1:{server.server_port}/api/status?token=private-token") as response:
                assert response.status == 200
        assert logger.info.call_count == logger.warning.call_count == logger.error.call_count == 0
        assert logger.debug.call_count > 0
        assert "private-token" not in str(logger.mock_calls)
        assert not (tmp_path / "mobile-server.log").exists()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(2)
