from __future__ import annotations

import shutil
import time
from pathlib import Path

from tests.integration.test_core_host_real_chat_integration import (
    CAPABILITIES,
    _configure_app_root,
    _exchange,
    _request,
    _start_host,
    _start_provider,
    _stop,
    _stop_provider,
)


FIXTURE_ROOT = Path(__file__).resolve().parents[1] / "fixtures" / "runtime_v2" / "wp_4_04"


def _hello() -> dict[str, object]:
    return _request(
        "plugins-hello",
        "system.hello",
        {
            "protocol": {"major": 2, "minMinor": 2, "maxMinor": 2},
            "requiredCapabilities": CAPABILITIES,
            "optionalCapabilities": [
                "transport.concurrent-router",
                "assistant.tools-v1",
                "assistant.plugins-v1",
            ],
        },
    )


def _wait_plugins(process, timeout: float = 10) -> dict[str, object]:
    deadline = time.monotonic() + timeout
    last = None
    sequence = 0
    while time.monotonic() < deadline:
        response = _exchange(
            process,
            _request(f"plugins-get-{sequence}", "plugins.settings.get", {}),
        )
        assert response["ok"] is True
        last = response["payload"]
        if last["state"] in {"ready", "degraded"} and last["plugins"]:
            return last
        sequence += 1
        time.sleep(0.05)
    raise TimeoutError(f"plugin worker did not publish status: {last!r}")


def test_real_core_plugin_worker_settings_and_shutdown_are_generation_scoped(tmp_path: Path) -> None:
    provider, provider_thread = _start_provider("complete")
    app_root = _configure_app_root(tmp_path, provider.server_address[1])
    shutil.copytree(FIXTURE_ROOT / "plugins", app_root / "plugins")
    process = _start_host(app_root)
    try:
        hello = _exchange(process, _hello())
        assert "assistant.plugins-v1" in hello["payload"]["capabilities"]
        assert _exchange(process, _request("plugins-init", "core.initialize", {}))["ok"] is True
        snapshot = _wait_plugins(process)
        by_id = {item["pluginId"]: item for item in snapshot["plugins"]}
        assert by_id["fixture_plugin"]["state"] == "ready"
        assert by_id["broken_plugin"]["state"] == "degraded"
        assert "entry" not in repr(snapshot)
        assert str(app_root) not in repr(snapshot)

        action = _exchange(
            process,
            _request(
                "plugins-action",
                "plugins.settings.action",
                {
                    "pluginId": "fixture_plugin",
                    "sectionId": "general",
                    "actionId": "reset",
                    "values": {"label": "changed"},
                },
            ),
        )
        assert action["ok"] is True
        assert action["payload"] == {"values": {"label": "fixture"}, "message": "reset"}
    finally:
        _stop(process)
        _stop_provider(provider, provider_thread)
