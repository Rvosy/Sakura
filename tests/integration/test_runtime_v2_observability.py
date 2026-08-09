from __future__ import annotations

import io
import json
import subprocess
import sys
import textwrap
from pathlib import Path

from app.core_host.protocol import encode_frame, read_frame
from app.core_host.runtime_logging import CORE_BRIDGE_PREFIX


REPO_ROOT = Path(__file__).resolve().parents[2]
GENERATION_ID = "00000000-0000-4000-8000-000000004101"
GENERATION_CREDENTIAL = "41" * 16


def _request(request_id: str, name: str, payload: dict[str, object]) -> dict[str, object]:
    return {
        "protocolMajor": 2,
        "protocolMinor": 1,
        "kind": "request",
        "generationId": GENERATION_ID,
        "generationCredential": GENERATION_CREDENTIAL,
        "id": request_id,
        "name": name,
        "payload": payload,
        "deadlineMs": 3000,
        "priority": "control",
    }


def _run_core(app_root: Path, input_bytes: bytes) -> subprocess.CompletedProcess[bytes]:
    flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "app.core_host",
            "--app-root",
            str(app_root),
            "--generation-id",
            GENERATION_ID,
            "--generation-number",
            "7",
        ],
        cwd=REPO_ROOT,
        input=input_bytes,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=15,
        check=False,
        creationflags=flags,
    )


def _bridge_records(stderr: bytes) -> list[dict[str, object]]:
    records = []
    for line in stderr.splitlines():
        if line.startswith(CORE_BRIDGE_PREFIX):
            records.append(json.loads(line.removeprefix(CORE_BRIDGE_PREFIX)))
    return records


def test_real_core_lifecycle_keeps_stdout_framed_and_uses_only_stderr_bridge(
    tmp_path: Path,
) -> None:
    app_root = tmp_path / "WP4L01-private-absolute-path"
    app_root.mkdir()
    hello = _request(
        "hello",
        "system.hello",
        {
            "protocol": {"major": 2, "minMinor": 0, "maxMinor": 1},
            "requiredCapabilities": [
                "system.hello",
                "system.health",
                "system.shutdown",
                "core.initialize",
                "core.snapshot",
            ],
            "optionalCapabilities": ["transport.concurrent-router"],
        },
    )
    shutdown = _request("shutdown", "system.shutdown", {})
    result = _run_core(
        app_root,
        bytes.fromhex(GENERATION_CREDENTIAL) + encode_frame(hello) + encode_frame(shutdown),
    )

    assert result.returncode == 0, result.stderr.decode("utf-8", errors="replace")
    stdout = io.BytesIO(result.stdout)
    assert read_frame(stdout)["id"] == "hello"  # type: ignore[index]
    assert read_frame(stdout)["id"] == "shutdown"  # type: ignore[index]
    assert read_frame(stdout) is None
    records = _bridge_records(result.stderr)
    assert [record["event"] for record in records] == [
        "core.process.started",
        "core.process.stopping",
    ]
    serialized = result.stderr.decode("utf-8", errors="replace")
    assert GENERATION_CREDENTIAL not in serialized
    assert str(app_root) not in serialized
    assert not (app_root / "data/logs/sakura-runtime.log").exists()


def test_core_bootstrap_failure_is_structured_without_stdout_pollution(tmp_path: Path) -> None:
    app_root = tmp_path / "app-root"
    app_root.mkdir()
    result = _run_core(app_root, b"")

    assert result.returncode == 74
    assert result.stdout == b""
    records = _bridge_records(result.stderr)
    assert [record["event"] for record in records] == [
        "core.process.started",
        "core.error.unhandled",
        "core.process.stopping",
    ]
    assert records[1]["attributes"] == {
        "code": "CORE_HOST_TRANSPORT_ERROR",
        "category": "TransportFailure",
    }
    assert b"CORE_HOST_TRANSPORT_ERROR TransportFailure" in result.stderr


def test_saturated_unread_stderr_cannot_hold_core_bridge_process_open() -> None:
    script = textwrap.dedent(
        """
        from app.core_host.runtime_logging import install_runtime_logging

        bridge = install_runtime_logging()
        for index in range(2000):
            bridge.emit_fixed(
                severity="info",
                channel="core.test",
                event="core.test.saturated",
                attributes={"status": "x" * 128, "revision": index},
                operation_id="operation-" + ("x" * 100),
            )
        bridge.close()
        """
    )
    flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    process = subprocess.Popen(
        [sys.executable, "-c", script],
        cwd=REPO_ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        creationflags=flags,
    )
    try:
        process.wait(timeout=5)
        assert process.returncode == 0
        assert process.stdout is not None
        assert process.stderr is not None
        assert process.stdout.read() == b""
        stderr = process.stderr.read()
        assert stderr.startswith(CORE_BRIDGE_PREFIX)
        assert len(stderr) >= 3000
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)
