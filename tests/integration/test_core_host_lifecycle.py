from __future__ import annotations

import queue
import struct
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import BinaryIO

import pytest

from app.core_host.protocol import decode_frame, encode_frame, read_frame


REPO_ROOT = Path(__file__).resolve().parents[2]
GENERATION_ID = "00000000-0000-4000-8000-000000001c01"


def request(request_id: str, name: str, payload: dict[str, object] | None = None) -> dict[str, object]:
    return {
        "protocolMajor": 2,
        "protocolMinor": 0,
        "kind": "request",
        "generationId": GENERATION_ID,
        "id": request_id,
        "name": name,
        "payload": payload or {},
        "deadlineMs": 3000,
        "priority": "control",
    }


def start_host(generation_id: str = GENERATION_ID) -> subprocess.Popen[bytes]:
    flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    return subprocess.Popen(
        [
            sys.executable,
            "-m",
            "app.core_host",
            "--generation-id",
            generation_id,
        ],
        cwd=REPO_ROOT,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        creationflags=flags,
    )


def read_with_deadline(
    process: subprocess.Popen[bytes], stream: BinaryIO, timeout: float = 3.0
) -> dict[str, object]:
    result: queue.Queue[object] = queue.Queue(maxsize=1)

    def reader() -> None:
        try:
            result.put(read_frame(stream))
        except BaseException as error:  # noqa: BLE001 - forwarded to the test thread
            result.put(error)

    thread = threading.Thread(target=reader, name="core-host-test-reader")
    thread.start()
    thread.join(timeout)
    if thread.is_alive():
        process.kill()
        process.wait(timeout=5)
        thread.join(5)
        if thread.is_alive():
            raise AssertionError("Core Host test reader survived process cleanup")
        raise TimeoutError("Core Host response exceeded its deadline")
    value = result.get_nowait()
    if isinstance(value, BaseException):
        raise value
    assert isinstance(value, dict)
    return value


def exchange(process: subprocess.Popen[bytes], message: dict[str, object]) -> dict[str, object]:
    assert process.stdin is not None
    assert process.stdout is not None
    process.stdin.write(encode_frame(message))
    process.stdin.flush()
    return read_with_deadline(process, process.stdout)


def stop_host(process: subprocess.Popen[bytes]) -> None:
    if process.stdin is not None and not process.stdin.closed:
        process.stdin.close()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)
        raise AssertionError("Core Host required forced cleanup")
    finally:
        for stream in (process.stdout, process.stderr):
            if stream is not None:
                stream.close()


def test_real_host_answers_hello_repeated_health_unknown_and_shutdown() -> None:
    process = start_host()
    try:
        hello = exchange(process, request("hello", "system.hello"))
        assert hello["ok"] is True
        assert hello["payload"] == {
            "capabilities": [
                "system.hello",
                "system.health",
                "system.shutdown",
                "core.initialize",
                "core.snapshot",
            ],
            "coreVersion": "0.1.0",
            "hostState": "transport_ready",
        }
        for index in range(2):
            health = exchange(process, request(f"health-{index}", "system.health"))
            assert health["ok"] is True
            assert health["payload"] == {
                "hostState": "transport_ready",
                "status": "healthy",
            }
        unknown = exchange(process, request("unknown", "system.unknown"))
        assert unknown["ok"] is False
        assert unknown["error"] == {
            "code": "UNKNOWN_CONTROL",
            "details": {},
            "message": "unsupported control request",
            "retryable": False,
        }
        shutdown = exchange(process, request("shutdown", "system.shutdown"))
        assert shutdown["ok"] is True
        assert shutdown["payload"] == {"accepted": True}
        assert process.wait(timeout=5) == 0
        assert process.stdout is not None
        assert process.stdout.read() == b""
    finally:
        stop_host(process)


def test_generation_mismatch_is_rejected_but_shutdown_remains_available() -> None:
    process = start_host()
    try:
        mismatch = request("wrong-generation", "system.health")
        mismatch["generationId"] = "00000000-0000-4000-8000-00000000ffff"
        response = exchange(process, mismatch)
        assert response["ok"] is False
        assert response["error"]["code"] == "GENERATION_MISMATCH"
        assert exchange(process, request("shutdown", "system.shutdown"))["ok"] is True
        assert process.wait(timeout=5) == 0
    finally:
        stop_host(process)


@pytest.mark.parametrize(
    "bad_frame",
    [
        struct.pack(">I", 1) + b"{",
        struct.pack(">I", 8 * 1024 * 1024 + 1),
        b"stdout pollution",
    ],
)
def test_real_host_fails_closed_without_writing_unframed_stdout(bad_frame: bytes) -> None:
    process = start_host()
    try:
        assert process.stdin is not None
        process.stdin.write(bad_frame)
        process.stdin.close()
        assert process.wait(timeout=5) != 0
        assert process.stdout is not None
        stdout = process.stdout.read()
        if stdout:
            decode_frame(stdout)
        assert stdout == b""
        assert process.stderr is not None
        stderr = process.stderr.read().decode("utf-8", errors="replace")
        assert "CORE_HOST_PROTOCOL_ERROR" in stderr
    finally:
        stop_host(process)


def test_clean_stdin_eof_exits_without_output_or_residual_thread() -> None:
    process = start_host()
    assert process.stdin is not None
    process.stdin.close()
    try:
        assert process.wait(timeout=5) == 0
        assert process.stdout is not None
        assert process.stdout.read() == b""
    finally:
        stop_host(process)


def test_real_host_initializes_in_background_and_returns_python_snapshot() -> None:
    process = start_host()
    try:
        assert exchange(process, request("hello", "system.hello"))["ok"] is True
        started = time.monotonic()
        initialize = exchange(
            process,
            request("initialize", "core.initialize", {"mode": "ready", "delayMs": 50}),
        )
        assert time.monotonic() - started < 0.5
        assert initialize["payload"]["readiness"] == "initializing"

        deadline = time.monotonic() + 2
        while True:
            snapshot = exchange(process, request("snapshot", "core.snapshot"))["payload"]
            if snapshot["readiness"] == "ready":
                break
            assert time.monotonic() < deadline
        assert snapshot["generationId"] == GENERATION_ID
        assert snapshot["revision"] == 2
        assert snapshot["components"] == {"fixture": {"state": "ready"}}
        assert exchange(process, request("shutdown", "system.shutdown"))["ok"] is True
        assert process.wait(timeout=5) == 0
    finally:
        stop_host(process)


def test_real_host_health_and_shutdown_remain_responsive_when_initialize_hangs() -> None:
    process = start_host()
    try:
        initialize = exchange(
            process,
            request("initialize", "core.initialize", {"mode": "hang"}),
        )
        assert initialize["payload"]["readiness"] == "initializing"
        for index in range(3):
            health = exchange(process, request(f"health-hang-{index}", "system.health"))
            assert health["payload"] == {
                "hostState": "initializing",
                "status": "healthy",
            }
        assert exchange(process, request("shutdown", "system.shutdown"))["ok"] is True
        assert process.wait(timeout=5) == 0
    finally:
        stop_host(process)
