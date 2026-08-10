from __future__ import annotations

import json
import sys
import time
import uuid
from pathlib import Path

import psutil

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


FIXTURE_SERVER = (
    Path(__file__).resolve().parents[1]
    / "fixtures"
    / "runtime_v2"
    / "wp_4_03"
    / "stdio_server.py"
)


def _hello(request_id: str) -> dict[str, object]:
    return _request(
        request_id,
        "system.hello",
        {
            "protocol": {"major": 2, "minMinor": 2, "maxMinor": 2},
            "requiredCapabilities": CAPABILITIES,
            "optionalCapabilities": [
                "transport.concurrent-router",
                "assistant.mcp-v1",
            ],
        },
    )


def _wait_for(
    process,
    name: str,
    predicate,
    *,
    timeout: float = 10,
) -> dict[str, object]:
    deadline = time.monotonic() + timeout
    sequence = 0
    last: dict[str, object] | None = None
    while time.monotonic() < deadline:
        response = _exchange(
            process,
            _request(f"{name}-{sequence}", name, {}),
        )
        assert response["ok"] is True
        payload = response["payload"]
        assert isinstance(payload, dict)
        last = payload
        if predicate(payload):
            return payload
        sequence += 1
        time.sleep(0.02)
    raise TimeoutError(f"{name} did not reach the expected state: {last!r}")


def _start_initialized_host(app_root: Path):
    process = _start_host(app_root)
    hello = _exchange(process, _hello("mcp-hello"))
    assert "assistant.mcp-v1" in hello["payload"]["capabilities"]
    initialized = _exchange(
        process,
        _request("mcp-initialize", "core.initialize", {}),
    )
    assert initialized["ok"] is True
    return process


def _write_mcp_config(app_root: Path, document: object) -> None:
    path = app_root / "data" / "config" / "mcp.yaml"
    path.write_text(json.dumps(document), encoding="utf-8")


def _wait_process_gone(pid: int, timeout: float = 5) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not psutil.pid_exists(pid):
            return
        try:
            if psutil.Process(pid).status() == psutil.STATUS_ZOMBIE:
                return
        except psutil.NoSuchProcess:
            return
        time.sleep(0.05)
    raise AssertionError(f"MCP stdio process {pid} survived Core shutdown")


def test_real_core_mcp_slow_start_is_non_blocking_and_shutdown_has_no_residue(
    tmp_path: Path,
) -> None:
    provider, provider_thread = _start_provider("complete")
    app_root = _configure_app_root(tmp_path, provider.server_address[1])
    pid_file = tmp_path / "mcp.pid"
    release_file = tmp_path / "mcp.release"
    secret = f"WP403_PRIVATE_{uuid.uuid4().hex}"
    _write_mcp_config(
        app_root,
        {
            "enabled": True,
            "default_call_timeout": 20,
            "servers": {
                "fixture": {
                    "transport": "stdio",
                    "command": sys.executable,
                    "args": [
                        str(FIXTURE_SERVER),
                        "--pid-file",
                        str(pid_file),
                        "--release-file",
                        str(release_file),
                    ],
                    "env": {"WP403_PRIVATE": secret},
                    "name_prefix": "fixture__",
                    "risk": "high",
                }
            },
        },
    )
    process = _start_initialized_host(app_root)
    child_pid: int | None = None
    try:
        deadline = time.monotonic() + 5
        while not pid_file.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert pid_file.exists(), "the real stdio MCP fixture did not start"
        child_pid = int(pid_file.read_text(encoding="ascii"))
        assert psutil.pid_exists(child_pid)

        readiness = _wait_for(
            process,
            "core.snapshot",
            lambda payload: payload.get("readiness") in {"ready", "degraded"},
        )
        assert readiness["readiness"] in {"ready", "degraded"}
        starting = _exchange(
            process,
            _request("mcp-starting", "mcp.settings.get", {}),
        )["payload"]
        assert starting["reasonCode"] == "STARTING"
        assert starting["servers"][0]["state"] == "starting"
        serialized = json.dumps(starting, ensure_ascii=False)
        assert secret not in serialized
        assert str(app_root) not in serialized
        assert str(FIXTURE_SERVER) not in serialized

        release_file.touch()
        ready = _wait_for(
            process,
            "mcp.settings.get",
            lambda payload: payload.get("reasonCode") == "READY",
        )
        assert ready["servers"] == [
            {
                "serverId": "fixture",
                "transport": "stdio",
                "enabled": True,
                "state": "ready",
                "reasonCode": "READY",
                "toolCount": 1,
            }
        ]

        shutdown = _exchange(
            process,
            _request("mcp-shutdown", "system.shutdown", {}),
        )
        assert shutdown["ok"] is True
        process.wait(timeout=5)
        assert process.returncode == 0
        _wait_process_gone(child_pid)
    finally:
        if child_pid is not None and psutil.pid_exists(child_pid):
            psutil.Process(child_pid).kill()
            psutil.Process(child_pid).wait(timeout=5)
        _stop(process)
        _stop_provider(provider, provider_thread)


def test_damaged_config_and_missing_command_degrade_only_mcp(tmp_path: Path) -> None:
    provider, provider_thread = _start_provider("complete")
    try:
        invalid_root = _configure_app_root(tmp_path / "invalid", provider.server_address[1])
        (invalid_root / "data" / "config" / "mcp.yaml").write_text(
            "servers: [unterminated",
            encoding="utf-8",
        )
        invalid_process = _start_initialized_host(invalid_root)
        try:
            _wait_for(
                invalid_process,
                "core.snapshot",
                lambda payload: payload.get("readiness") in {"ready", "degraded"},
            )
            status = _wait_for(
                invalid_process,
                "mcp.settings.get",
                lambda payload: payload.get("reasonCode") == "CONFIG_INVALID",
            )
            assert status == {
                "schemaVersion": 1,
                "desktop": status["desktop"],
                "desktopEnabled": False,
                "configState": "invalid",
                "reasonCode": "CONFIG_INVALID",
                "servers": [],
            }
        finally:
            _stop(invalid_process)

        missing_root = _configure_app_root(tmp_path / "missing", provider.server_address[1])
        missing_command = f"wp403-missing-{uuid.uuid4().hex}"
        _write_mcp_config(
            missing_root,
            {
                "enabled": True,
                "servers": {
                    "missing": {
                        "transport": "stdio",
                        "command": missing_command,
                    }
                },
            },
        )
        missing_process = _start_initialized_host(missing_root)
        try:
            _wait_for(
                missing_process,
                "core.snapshot",
                lambda payload: payload.get("readiness") in {"ready", "degraded"},
            )
            status = _wait_for(
                missing_process,
                "mcp.settings.get",
                lambda payload: payload.get("reasonCode") == "NO_READY_SERVERS",
            )
            assert status["servers"] == [
                {
                    "serverId": "missing",
                    "transport": "stdio",
                    "enabled": True,
                    "state": "degraded",
                    "reasonCode": "COMMAND_NOT_FOUND",
                    "toolCount": 0,
                }
            ]
            assert missing_command not in json.dumps(status)
        finally:
            _stop(missing_process)
    finally:
        _stop_provider(provider, provider_thread)
