from __future__ import annotations

import hashlib
import json
import os
import queue
import shutil
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import BinaryIO

import pytest

from app.core_host.protocol import encode_frame, read_frame


REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURE_ROOT = REPO_ROOT / "tests/fixtures/runtime_v2/wp_3_01/ready"
GENERATION_CREDENTIAL = "53" * 16
CAPABILITIES = [
    "system.hello",
    "system.health",
    "system.shutdown",
    "core.initialize",
    "core.snapshot",
]
FORBIDDEN_IMPORT_PREFIXES = (
    "PySide6",
    "app.application",
    "app.memory",
    "app.plugins",
    "app.voice",
)


def _manifest(root: Path) -> dict[str, tuple[int, int, str]]:
    return {
        path.relative_to(root).as_posix(): (
            path.stat().st_size,
            path.stat().st_mtime_ns,
            hashlib.sha256(path.read_bytes()).hexdigest(),
        )
        for path in root.rglob("*")
        if path.is_file()
    }


def _request(
    generation_id: str,
    request_id: str,
    name: str,
    payload: dict[str, object] | None = None,
) -> dict[str, object]:
    return {
        "protocolMajor": 2,
        "protocolMinor": 1,
        "kind": "request",
        "generationId": generation_id,
        "generationCredential": GENERATION_CREDENTIAL,
        "id": request_id,
        "name": name,
        "payload": payload
        if payload is not None
        else (
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


def _read_with_deadline(process: subprocess.Popen[bytes], stream: BinaryIO) -> dict[str, object]:
    result: queue.Queue[object] = queue.Queue(maxsize=1)

    def read() -> None:
        try:
            result.put(read_frame(stream))
        except BaseException as error:  # noqa: BLE001 - forwarded to test thread
            result.put(error)

    reader = threading.Thread(target=read, name="assistant-acceptance-reader")
    reader.start()
    reader.join(3)
    if reader.is_alive():
        process.kill()
        process.wait(timeout=5)
        reader.join(5)
        raise TimeoutError("Core Host response exceeded the outer deadline")
    value = result.get_nowait()
    if isinstance(value, BaseException):
        raise value
    assert isinstance(value, dict)
    return value


def _exchange(process: subprocess.Popen[bytes], message: dict[str, object]) -> dict[str, object]:
    assert process.stdin is not None
    assert process.stdout is not None
    process.stdin.write(encode_frame(message))
    process.stdin.flush()
    return _read_with_deadline(process, process.stdout)


def _start_host(app_root: Path, generation_id: str) -> subprocess.Popen[bytes]:
    flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    environment = os.environ.copy()
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
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
        env=environment,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        creationflags=flags,
    )
    assert process.stdin is not None
    process.stdin.write(bytes.fromhex(GENERATION_CREDENTIAL))
    process.stdin.flush()
    return process


def _stop_host(process: subprocess.Popen[bytes]) -> None:
    if process.stdin is not None and not process.stdin.closed:
        process.stdin.close()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)
        raise AssertionError("real Core Host required forced test cleanup")
    finally:
        for stream in (process.stdout, process.stderr):
            if stream is not None:
                stream.close()


def _ready(_root: Path) -> None:
    return None


def _setup_required(root: Path) -> None:
    (root / "data/config/system_config.yaml").unlink()


def _degraded_with_combined_character_faults(root: Path) -> None:
    (root / "data/config/characters.yaml").write_text(
        "current_character_id: missing\n", encoding="utf-8"
    )
    broken = root / "characters/broken"
    broken.mkdir()
    (broken / "character.json").write_text(
        '{"id":"broken","display_name":"PRIVATE_OPTIONAL_CHARACTER"}',
        encoding="utf-8",
    )


def _failed(root: Path) -> None:
    (root / "data/config/system_config.yaml").write_text("not: [valid", encoding="utf-8")


@pytest.mark.parametrize(
    ("mutate", "state", "code", "has_summary"),
    [
        pytest.param(_ready, "ready", "READY", True, id="ready"),
        pytest.param(
            _setup_required,
            "setup_required",
            "CORE_CONFIG_SETUP_REQUIRED",
            False,
            id="setup-required",
        ),
        pytest.param(
            _degraded_with_combined_character_faults,
            "degraded",
            "CHARACTER_FALLBACK_APPLIED",
            True,
            id="degraded-combined-character-faults",
        ),
        pytest.param(
            _failed,
            "failed",
            "CONFIG_DATA_INVALID",
            False,
            id="failed",
        ),
    ],
)
def test_real_host_assistant_readiness_matrix_is_read_only_and_bounded(
    tmp_path: Path,
    mutate: Callable[[Path], None],
    state: str,
    code: str,
    has_summary: bool,
) -> None:
    source_before = _manifest(FIXTURE_ROOT)
    app_root = tmp_path / f"fixture-{state}"
    shutil.copytree(FIXTURE_ROOT, app_root)
    mutate(app_root)
    copied_before = _manifest(app_root)
    generation_id = f"00000000-0000-4000-8000-{len(state):012x}"
    process = _start_host(app_root, generation_id)
    reader_threads_before = {thread.ident for thread in threading.enumerate()}
    try:
        assert _exchange(process, _request(generation_id, "hello", "system.hello"))["ok"] is True
        initialize = _exchange(
            process, _request(generation_id, "initialize", "core.initialize", {})
        )
        assert initialize["payload"]["readiness"] == "initializing"

        snapshots: list[dict[str, object]] = []
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            health = _exchange(
                process,
                _request(generation_id, f"health-{len(snapshots)}", "system.health"),
            )
            assert health["payload"]["status"] == "healthy"
            snapshot = _exchange(
                process,
                _request(generation_id, f"snapshot-{len(snapshots)}", "core.snapshot"),
            )["payload"]
            assert isinstance(snapshot, dict)
            snapshots.append(snapshot)
            if snapshot["readiness"] != "initializing":
                break
        else:
            pytest.fail("real Assistant readiness exceeded the outer deadline")

        final = snapshots[-1]
        assert final["readiness"] == state
        assert final["components"] == {
            "assistant": {"state": state, "code": code, "retryable": False}
        }
        assert (final.get("currentCharacterSummary") is not None) is has_summary
        assert (final.get("characterPresentation") is not None) is has_summary
        if has_summary:
            assert final["characterPresentation"]["generationId"] == generation_id
            assert final["characterPresentation"]["characterId"] == final[
                "currentCharacterSummary"
            ]["id"]
        assert all(
            later["revision"] >= earlier["revision"]
            for earlier, later in zip(snapshots, snapshots[1:])
        )
        repeated = _exchange(
            process, _request(generation_id, "snapshot-repeated", "core.snapshot")
        )["payload"]
        assert repeated == final

        shutdown = _exchange(
            process, _request(generation_id, "shutdown", "system.shutdown")
        )
        assert shutdown["payload"] == {"accepted": True}
        assert process.wait(timeout=5) == 0
        assert process.poll() == 0
        assert process.stdout is not None and process.stdout.read() == b""
        assert process.stderr is not None
        stderr = process.stderr.read().decode("utf-8", errors="replace")
        assert "PRIVATE_OPTIONAL_CHARACTER" not in stderr
        assert not any(prefix in stderr for prefix in FORBIDDEN_IMPORT_PREFIXES)
    finally:
        _stop_host(process)

    assert _manifest(FIXTURE_ROOT) == source_before
    assert _manifest(app_root) == copied_before
    assert not list(app_root.rglob("__pycache__"))
    assert {thread.ident for thread in threading.enumerate()} == reader_threads_before


def test_consecutive_generations_reject_stale_snapshot_and_credential(tmp_path: Path) -> None:
    roots = []
    snapshots = []
    for index in range(2):
        app_root = tmp_path / f"generation-{index}"
        shutil.copytree(FIXTURE_ROOT, app_root)
        roots.append(app_root)
        generation_id = f"00000000-0000-4000-8000-{index + 1:012x}"
        process = _start_host(app_root, generation_id)
        try:
            assert _exchange(
                process, _request(generation_id, f"hello-{index}", "system.hello")
            )["ok"] is True
            stale = _request(generation_id, f"stale-{index}", "core.snapshot")
            stale["generationId"] = "00000000-0000-4000-8000-000000000000"
            assert _exchange(process, stale)["error"]["code"] == "GENERATION_MISMATCH"

            bad_credential = _request(generation_id, f"credential-{index}", "system.health")
            bad_credential["generationCredential"] = "73" * 16
            assert process.stdin is not None
            process.stdin.write(encode_frame(bad_credential))
            process.stdin.flush()
            assert process.wait(timeout=5) == 74
            assert process.stdout is not None and process.stdout.read() == b""
            assert process.stderr is not None
            stderr = process.stderr.read().decode("utf-8", errors="replace")
            assert GENERATION_CREDENTIAL not in stderr
            assert "73" * 16 not in stderr
            snapshots.append(generation_id)
        finally:
            _stop_host(process)

    assert len(set(snapshots)) == 2
    assert roots[0] != roots[1]
