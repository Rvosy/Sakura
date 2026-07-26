from __future__ import annotations

import json
import queue
import shutil
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
GENERATION_CREDENTIAL = "33" * 16
CAPABILITIES = [
    "system.hello",
    "system.health",
    "system.shutdown",
    "core.initialize",
    "core.snapshot",
]
LIFECYCLE_GOLDEN = json.loads(
    (REPO_ROOT / "tests/fixtures/runtime_v2/wp_1c_04/lifecycle-golden.json").read_text(
        encoding="utf-8"
    )
)
REQUEST_TIMEOUT = LIFECYCLE_GOLDEN["deadlinesMs"]["request"] / 1000
READY_FIXTURE_ROOT = REPO_ROOT / "tests/fixtures/runtime_v2/wp_3_01/ready"


def request(request_id: str, name: str, payload: dict[str, object] | None = None) -> dict[str, object]:
    return {
        "protocolMajor": 2,
        "protocolMinor": 1,
        "kind": "request",
        "generationId": GENERATION_ID,
        "generationCredential": GENERATION_CREDENTIAL,
        "id": request_id,
        "name": name,
        "payload": payload if payload is not None else (
            {
                "protocol": {"major": 2, "minMinor": 0, "maxMinor": 1},
                "requiredCapabilities": CAPABILITIES,
                "optionalCapabilities": [],
            }
            if name == "system.hello"
            else {}
        ),
        "deadlineMs": 3000,
        "priority": "control",
    }


def isolated_app_root(tmp_path: Path, *, ready: bool = False) -> Path:
    root = tmp_path / "app-root"
    if ready:
        shutil.copytree(READY_FIXTURE_ROOT, root)
    else:
        root.mkdir()
    return root


def start_host(app_root: Path, generation_id: str = GENERATION_ID) -> subprocess.Popen[bytes]:
    flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "app.core_host",
            "--app-root",
            str(app_root),
            "--generation-id",
            generation_id,
        ],
        cwd=REPO_ROOT,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        creationflags=flags,
    )
    assert process.stdin is not None
    process.stdin.write(bytes.fromhex(GENERATION_CREDENTIAL))
    process.stdin.flush()
    return process


def read_with_deadline(
    process: subprocess.Popen[bytes], stream: BinaryIO, timeout: float = REQUEST_TIMEOUT
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


def test_wp_1c_04_shared_lifecycle_golden_matches_python_contract() -> None:
    assert LIFECYCLE_GOLDEN["schemaVersion"] == 1
    assert LIFECYCLE_GOLDEN["protocol"] == {
        "major": 2,
        "minMinor": 0,
        "maxMinor": 1,
        "requiredCapabilities": CAPABILITIES,
    }
    assert LIFECYCLE_GOLDEN["deadlinesMs"] == {
        "hello": 3000,
        "initializeAcceptance": 5000,
        "readinessWatchdog": 30000,
        "request": 3000,
        "shutdown": 3000,
        "treeStop": 5000,
    }
    assert LIFECYCLE_GOLDEN["lifecycle"] == [
        "system.hello",
        "core.initialize",
        "core.readiness",
        "core.snapshot",
        "system.health",
        "system.shutdown",
    ]
    assert {layout["target"] for layout in LIFECYCLE_GOLDEN["layouts"]} == {
        "windows-x64",
        "macos-arm64",
        "linux-x64",
    }


def test_real_host_answers_hello_repeated_health_unknown_and_shutdown(tmp_path: Path) -> None:
    process = start_host(isolated_app_root(tmp_path))
    try:
        hello = exchange(process, request("hello", "system.hello"))
        assert hello["ok"] is True
        assert hello["payload"] == {
            "capabilities": CAPABILITIES,
            "coreVersion": "0.1.0",
            "hostState": "transport_ready",
            "protocol": {"major": 2, "minMinor": 0, "maxMinor": 2},
            "negotiated": {
                "major": 2,
                "minor": 1,
                "capabilities": CAPABILITIES,
            },
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


def test_generation_mismatch_is_rejected_but_shutdown_remains_available(tmp_path: Path) -> None:
    process = start_host(isolated_app_root(tmp_path))
    try:
        assert exchange(process, request("hello", "system.hello"))["ok"] is True
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
def test_real_host_fails_closed_without_writing_unframed_stdout(
    tmp_path: Path,
    bad_frame: bytes,
) -> None:
    process = start_host(isolated_app_root(tmp_path))
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


def test_clean_stdin_eof_exits_without_output_or_residual_thread(tmp_path: Path) -> None:
    process = start_host(isolated_app_root(tmp_path))
    assert process.stdin is not None
    process.stdin.close()
    try:
        assert process.wait(timeout=5) == 0
        assert process.stdout is not None
        assert process.stdout.read() == b""
    finally:
        stop_host(process)


@pytest.mark.parametrize("credential", [None, "77" * 16])
def test_real_host_rejects_missing_or_wrong_message_credential_without_echo(
    tmp_path: Path,
    credential: str | None,
) -> None:
    process = start_host(isolated_app_root(tmp_path))
    try:
        message = request("bad-credential", "system.hello")
        if credential is None:
            del message["generationCredential"]
        else:
            message["generationCredential"] = credential
        assert process.stdin is not None
        process.stdin.write(encode_frame(message))
        process.stdin.flush()
        assert process.wait(timeout=5) == 74
        assert process.stdout is not None
        assert process.stdout.read() == b""
        assert process.stderr is not None
        stderr = process.stderr.read().decode("utf-8", errors="replace")
        assert "CORE_HOST_TRANSPORT_ERROR TransportFailure" in stderr
        assert GENERATION_CREDENTIAL not in stderr
        if credential:
            assert credential not in stderr
    finally:
        stop_host(process)


def test_real_host_rejects_missing_bootstrap_credential_without_protocol_output(
    tmp_path: Path,
) -> None:
    flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "app.core_host",
            "--app-root",
            str(isolated_app_root(tmp_path)),
            "--generation-id",
            GENERATION_ID,
        ],
        cwd=REPO_ROOT,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        creationflags=flags,
    )
    assert process.stdin is not None
    process.stdin.close()
    try:
        assert process.wait(timeout=5) == 74
        assert process.stdout is not None
        assert process.stdout.read() == b""
        assert process.stderr is not None
        assert "TransportFailure" in process.stderr.read().decode(errors="replace")
    finally:
        stop_host(process)


def test_real_host_initializes_in_background_and_returns_python_snapshot(tmp_path: Path) -> None:
    process = start_host(isolated_app_root(tmp_path, ready=True))
    try:
        assert exchange(process, request("hello", "system.hello"))["ok"] is True
        started = time.monotonic()
        initialize = exchange(
            process,
            request("initialize", "core.initialize", {}),
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
        assert snapshot["components"] == {
            "assistant": {"state": "ready", "code": "READY", "retryable": False}
        }
        assert set(snapshot["currentCharacterSummary"]) == {
            "id",
            "displayName",
            "initialMessage",
            "replyTones",
            "portraitChoices",
        }
        assert exchange(process, request("shutdown", "system.shutdown"))["ok"] is True
        assert process.wait(timeout=5) == 0
    finally:
        stop_host(process)


def test_real_host_rejects_fixture_modes_and_remains_responsive(tmp_path: Path) -> None:
    process = start_host(isolated_app_root(tmp_path))
    try:
        assert exchange(process, request("hello", "system.hello"))["ok"] is True
        initialize = exchange(
            process,
            request("initialize", "core.initialize", {"mode": "hang"}),
        )
        assert initialize["ok"] is False
        assert initialize["error"]["code"] == "INVALID_INITIALIZE"
        for index in range(3):
            health = exchange(process, request(f"health-hang-{index}", "system.health"))
            assert health["payload"] == {
                "hostState": "transport_ready",
                "status": "healthy",
            }
        assert exchange(process, request("shutdown", "system.shutdown"))["ok"] is True
        assert process.wait(timeout=5) == 0
    finally:
        stop_host(process)


def test_real_host_failed_readiness_still_cleans_init_and_writer_threads(tmp_path: Path) -> None:
    app_root = isolated_app_root(tmp_path)
    config_dir = app_root / "data" / "config"
    config_dir.mkdir(parents=True)
    (config_dir / "system_config.yaml").write_text("not: [valid", encoding="utf-8")
    process = start_host(app_root)
    try:
        assert exchange(process, request("hello-failed", "system.hello"))["ok"] is True
        initialize = exchange(
            process,
            request("initialize-failed", "core.initialize", {}),
        )
        assert initialize["payload"]["readiness"] == "initializing"
        deadline = (
            time.monotonic()
            + LIFECYCLE_GOLDEN["deadlinesMs"]["readinessWatchdog"] / 1000
        )
        while True:
            snapshot = exchange(process, request("snapshot-failed", "core.snapshot"))["payload"]
            if snapshot["readiness"] == "failed":
                break
            assert time.monotonic() < deadline
        health = exchange(process, request("health-failed", "system.health"))
        assert health["payload"] == {"hostState": "failed", "status": "healthy"}
        assert exchange(process, request("shutdown-failed", "system.shutdown"))["ok"] is True
        assert (
            process.wait(timeout=LIFECYCLE_GOLDEN["deadlinesMs"]["treeStop"] / 1000)
            == 0
        )
        assert process.stdout is not None
        assert process.stdout.read() == b""
    finally:
        stop_host(process)
