"""Bounded native Shell + bundled Python Core lifecycle acceptance for Phase 1P."""

from __future__ import annotations

import argparse
import ctypes
import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time


DEADLINE_SECONDS = 60
ACCEPTANCE_PREFIX = "sakura-runtime-v2-wp-1c-02-"


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


def protected_manifest(repo: Path) -> str:
    digest = hashlib.sha256()
    for root_name in ("data", "runtime"):
        root = repo / root_name
        if not root.exists():
            digest.update(f"{root_name}:missing\n".encode())
            continue
        for path in sorted(root.rglob("*"), key=lambda item: item.as_posix()):
            relative = path.relative_to(repo).as_posix()
            if path.is_symlink():
                digest.update(f"L {relative} {os.readlink(path)}\n".encode())
            elif path.is_dir():
                digest.update(f"D {relative}\n".encode())
            elif path.is_file():
                digest.update(f"F {relative} {path.stat().st_size}\n".encode())
                with path.open("rb") as stream:
                    for block in iter(lambda: stream.read(1024 * 1024), b""):
                        digest.update(block)
    return digest.hexdigest()


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


def controlled_round(binary: Path, repo: Path, label: str, check_conflict: bool) -> None:
    directory = Path(tempfile.mkdtemp(prefix=f"{ACCEPTANCE_PREFIX}{label}-"))
    process: subprocess.Popen[bytes] | None = None
    try:
        deadline = time.monotonic() + DEADLINE_SECONDS
        process = start_shell(binary, repo, directory)
        wait_for(directory / "acceptance.ready", process, deadline)
        core_pid = int((directory / "core.pid").read_text(encoding="ascii"))

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

    before = protected_manifest(repo)
    controlled_round(binary, repo, "normal", check_conflict=True)
    crash_round(binary, repo)
    controlled_round(binary, repo, "reacquire", check_conflict=False)
    after = protected_manifest(repo)
    if before != after:
        raise RuntimeError("tracked user data/runtime scope changed during lifecycle acceptance")
    print(f"shell-core-lifecycle=passed protected-manifest={before}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
