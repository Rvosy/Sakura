"""Bounded native Shell + bundled Python Core lifecycle acceptance for Phase 1P."""

from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time


DEADLINE_SECONDS = 60
ACCEPTANCE_PREFIX = "sakura-runtime-v2-wp-1c-02-"
LIFECYCLE_GOLDEN = (
    Path(__file__).resolve().parents[2]
    / "tests/fixtures/runtime_v2/wp_1c_04/lifecycle-golden.json"
)


def wait_for(path: Path, process: subprocess.Popen[bytes], deadline: float) -> None:
    while time.monotonic() < deadline:
        if path.is_file():
            return
        error_path = path.parent / "acceptance.error"
        if error_path.is_file():
            raise RuntimeError(error_path.read_text(encoding="utf-8", errors="replace"))
        return_code = process.poll()
        if return_code is not None:
            raise RuntimeError(f"Shell exited with {return_code} before {path.name}")
        time.sleep(0.02)
    raise TimeoutError(f"Shell did not create {path.name} before deadline")


def wait_process(process: subprocess.Popen[bytes], deadline: float) -> int:
    remaining = max(0.1, deadline - time.monotonic())
    try:
        return process.wait(timeout=remaining)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=10)
        raise TimeoutError("Shell did not exit before deadline")


def pid_is_alive(pid: int) -> bool:
    if sys.platform == "win32":
        process_query_limited_information = 0x1000
        still_active = 259
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.argtypes = [ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong]
        kernel32.OpenProcess.restype = ctypes.c_void_p
        kernel32.GetExitCodeProcess.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong)]
        kernel32.GetExitCodeProcess.restype = ctypes.c_int
        kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
        kernel32.CloseHandle.restype = ctypes.c_int
        handle = kernel32.OpenProcess(process_query_limited_information, False, pid)
        if not handle:
            return False
        exit_code = ctypes.c_ulong()
        queried = kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code))
        kernel32.CloseHandle(handle)
        return bool(queried) and exit_code.value == still_active
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def wait_pid_exited(pid: int, deadline: float) -> None:
    while time.monotonic() < deadline:
        if not pid_is_alive(pid):
            return
        time.sleep(0.02)
    raise RuntimeError(f"managed Core PID {pid} survived Shell termination")


def protected_summaries(repo: Path) -> dict[str, dict[str, object]]:
    summaries: dict[str, dict[str, object]] = {}
    for root_name in ("characters", "data", "runtime"):
        digest = hashlib.sha256()
        file_count = 0
        byte_count = 0
        root = repo / root_name
        if not root.exists():
            digest.update(f"{root_name}:missing\n".encode())
        else:
            for path in sorted(root.rglob("*"), key=lambda item: item.as_posix()):
                relative = path.relative_to(repo).as_posix()
                if path.is_symlink():
                    digest.update(f"L {relative} {os.readlink(path)}\n".encode())
                elif path.is_dir():
                    digest.update(f"D {relative}\n".encode())
                elif path.is_file():
                    size = path.stat().st_size
                    file_count += 1
                    byte_count += size
                    digest.update(f"F {relative} {size}\n".encode())
                    with path.open("rb") as stream:
                        for block in iter(lambda: stream.read(1024 * 1024), b""):
                            digest.update(block)
        summaries[root_name] = {
            "files": file_count,
            "bytes": byte_count,
            "sha256": digest.hexdigest(),
        }
    return summaries


def environment(repo: Path, directory: Path) -> dict[str, str]:
    result = os.environ.copy()
    result.update(
        {
            "SAKURA_PHASE_1C_ACCEPTANCE_DIRECTORY": str(directory),
            "SAKURA_PHASE_1C_REPO_ROOT": str(repo),
            "SAKURA_PHASE_1C_INITIALIZE_MODE": "ready",
            "SAKURA_PHASE_1P_CONTROLLED_EXIT": "1",
        }
    )
    return result


def start_shell(binary: Path, repo: Path, directory: Path) -> subprocess.Popen[bytes]:
    return subprocess.Popen(
        [str(binary)],
        cwd=repo,
        env=environment(repo, directory),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def assert_process_success(process: subprocess.Popen[bytes], deadline: float) -> None:
    return_code = wait_process(process, deadline)
    stdout, stderr = process.communicate()
    if return_code != 0:
        raise RuntimeError(
            f"Shell returned {return_code}\nstdout:\n{stdout.decode(errors='replace')}"
            f"\nstderr:\n{stderr.decode(errors='replace')}"
        )


def assert_lifecycle_evidence(directory: Path, repo: Path) -> None:
    golden = json.loads(LIFECYCLE_GOLDEN.read_text(encoding="utf-8"))
    layout = json.loads((directory / "runtime-layout.json").read_text(encoding="utf-8"))
    targets = {item["target"]: item for item in golden["layouts"]}
    if layout.get("target") not in targets:
        raise RuntimeError("Shell RuntimeLocator returned an unknown target")
    if layout.get("mode") != "explicit_development":
        raise RuntimeError("Shell development acceptance did not use its explicit layout")
    if layout.get("coreModule") != "app.core_host" or not layout.get("sourceId"):
        raise RuntimeError("Shell RuntimeLocator evidence omitted Core identity")
    expected = targets[layout["target"]]
    expected_architecture = "arm64" if sys.platform == "darwin" else "x64"
    if expected.get("architecture") != expected_architecture:
        raise RuntimeError("Shell target architecture does not match the native runner")
    python = Path(layout["pythonExecutable"]).resolve(strict=True)
    runtime = (repo / "runtime").resolve(strict=True)
    python_text = os.path.normcase(os.path.normpath(str(python))).removeprefix("\\\\?\\")
    runtime_text = os.path.normcase(os.path.normpath(str(runtime))).removeprefix("\\\\?\\")
    if os.path.commonpath((python_text, runtime_text)) != runtime_text:
        raise RuntimeError("Shell development acceptance escaped its bundled Runtime")
    packaged_suffix = Path(expected["packagedPythonRelativePath"]).parts[1:]
    expected_parent = packaged_suffix[:-1]
    actual_parent = python.parent.parts[-len(expected_parent) :] if expected_parent else ()
    expected_name = packaged_suffix[-1]
    name_matches = (
        python.name.casefold() == expected_name.casefold()
        if sys.platform == "win32"
        else python.name.startswith(expected_name)
    )
    if tuple(part.casefold() for part in actual_parent) != tuple(
        part.casefold() for part in expected_parent
    ) or not name_matches:
        raise RuntimeError("Shell bundled Python layout differs from the frozen target golden")
    for marker in ("acceptance.hello", "acceptance.initialize", "snapshot.json"):
        if not (directory / marker).is_file():
            raise RuntimeError(f"Shell lifecycle evidence omitted {marker}")
    snapshot = json.loads((directory / "snapshot.json").read_text(encoding="utf-8"))
    if snapshot.get("readiness") != "ready":
        raise RuntimeError("Shell lifecycle Snapshot did not reach ready")
    matrix = json.loads((directory / "native-fault-matrix.json").read_text(encoding="utf-8"))
    rows = matrix.get("rows")
    if not isinstance(rows, list):
        raise RuntimeError("native real-host fault matrix omitted its rows")
    expected_labels = {
        "close-throw",
        "close-block",
        "crash-one-descendant",
        "forced-recovery-multi-descendant",
        "generation-1",
        "generation-2",
    }
    by_label = {row.get("label"): row for row in rows if isinstance(row, dict)}
    if set(by_label) != expected_labels:
        raise RuntimeError("native real-host fault matrix labels are incomplete")
    for label, row in by_label.items():
        if (
            row.get("treeEmpty") is not True
            or row.get("nativeIdentityPresent") is not False
            or row.get("pipesReleased") is not True
            or row.get("threadsReleased") is not True
            or row.get("handlesReleased") is not True
            or row.get("tempReleased") is not True
            or row.get("coreLockOwned") is not False
        ):
            raise RuntimeError(f"native fault row {label} did not release every resource")
        identities = [*row.get("descendantPids", [])]
        if isinstance(row.get("rootPid"), int):
            identities.append(row["rootPid"])
        for pid in identities:
            if not isinstance(pid, int) or pid_is_alive(pid):
                raise RuntimeError(f"native fault row {label} retained descendant identity {pid}")
    recovery = by_label["forced-recovery-multi-descendant"]
    if not isinstance(recovery.get("shutdownElapsedMs"), int) or not isinstance(
        recovery.get("recoveryElapsedMs"), int
    ):
        raise RuntimeError("forced recovery row omitted independent elapsed evidence")
    generations = [by_label["generation-1"], by_label["generation-2"]]
    if len({row.get("generationId") for row in generations}) != 2 or not all(
        row.get("staleSnapshotRejected") is True for row in generations
    ):
        raise RuntimeError("consecutive native generations accepted a stale Snapshot")
    if generations[1].get("staleCredentialRejected") is not True:
        raise RuntimeError("second native generation accepted a stale credential")


def controlled_round(binary: Path, repo: Path, label: str, check_conflict: bool) -> None:
    directory = Path(tempfile.mkdtemp(prefix=f"{ACCEPTANCE_PREFIX}{label}-"))
    process: subprocess.Popen[bytes] | None = None
    try:
        deadline = time.monotonic() + DEADLINE_SECONDS
        process = start_shell(binary, repo, directory)
        wait_for(directory / "acceptance.ready", process, deadline)
        core_pid = int((directory / "core.pid").read_text(encoding="ascii"))
        assert_lifecycle_evidence(directory, repo)

        if check_conflict:
            conflict = start_shell(binary, repo, directory)
            assert_process_success(conflict, deadline)
            if not (directory / "acceptance.lock_conflict").is_file():
                raise RuntimeError("second Shell did not record shared-lock conflict")

        (directory / "acceptance.exit_requested").write_bytes(b"exit")
        assert_process_success(process, deadline)
        wait_pid_exited(core_pid, deadline)
        if not (directory / "acceptance.cleaned").is_file():
            raise RuntimeError("Shell did not verify Core tree cleanup")
    finally:
        if process is not None and process.poll() is None:
            process.kill()
            process.wait(timeout=10)
        shutil.rmtree(directory, ignore_errors=False)
        if directory.exists():
            raise RuntimeError(f"acceptance directory survived cleanup: {directory}")


def crash_round(binary: Path, repo: Path) -> None:
    directory = Path(tempfile.mkdtemp(prefix=f"{ACCEPTANCE_PREFIX}crash-"))
    process: subprocess.Popen[bytes] | None = None
    try:
        deadline = time.monotonic() + DEADLINE_SECONDS
        process = start_shell(binary, repo, directory)
        wait_for(directory / "acceptance.ready", process, deadline)
        core_pid = int((directory / "core.pid").read_text(encoding="ascii"))
        process.kill()
        process.wait(timeout=10)
        wait_pid_exited(core_pid, deadline)
    finally:
        if process is not None and process.poll() is None:
            process.kill()
            process.wait(timeout=10)
        shutil.rmtree(directory, ignore_errors=False)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--repo", type=Path, required=True)
    args = parser.parse_args()
    binary = args.binary.resolve(strict=True)
    repo = args.repo.resolve(strict=True)

    before = protected_summaries(repo)
    controlled_round(binary, repo, "normal", check_conflict=True)
    crash_round(binary, repo)
    controlled_round(binary, repo, "reacquire", check_conflict=False)
    after = protected_summaries(repo)
    if before != after:
        raise RuntimeError("tracked user data/runtime scope changed during lifecycle acceptance")
    print(
        "shell-core-lifecycle=passed protected-summaries="
        + json.dumps(before, sort_keys=True)
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
