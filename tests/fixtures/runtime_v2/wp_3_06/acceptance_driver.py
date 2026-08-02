from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import ctypes
from ctypes import wintypes
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import yaml


REPO_ROOT = Path(__file__).resolve().parents[4]
SOURCE_DATASET = REPO_ROOT / "tests/fixtures/runtime_v2/wp_0_02/dataset"
PYTHON = REPO_ROOT / "runtime/python.exe"
TAURI = REPO_ROOT / "desktop/src-tauri/target/debug/sakura-runtime-v2-shell.exe"
DIRECTORY_ENV = "SAKURA_WP_3_06_ACCEPTANCE_DIRECTORY"
MODE_ENV = "SAKURA_WP_3_06_ACCEPTANCE_MODE"
ALLOWED_CHANGES = {
    "data/chat_history/fixture.jsonl",
    "data/runtime_v2/config/ui.json",
}


class _JobObjectBasicLimitInformation(ctypes.Structure):
    _fields_ = [
        ("per_process_user_time_limit", ctypes.c_int64),
        ("per_job_user_time_limit", ctypes.c_int64),
        ("limit_flags", wintypes.DWORD),
        ("minimum_working_set_size", ctypes.c_size_t),
        ("maximum_working_set_size", ctypes.c_size_t),
        ("active_process_limit", wintypes.DWORD),
        ("affinity", ctypes.c_size_t),
        ("priority_class", wintypes.DWORD),
        ("scheduling_class", wintypes.DWORD),
    ]


class _IoCounters(ctypes.Structure):
    _fields_ = [
        ("read_operation_count", ctypes.c_uint64),
        ("write_operation_count", ctypes.c_uint64),
        ("other_operation_count", ctypes.c_uint64),
        ("read_transfer_count", ctypes.c_uint64),
        ("write_transfer_count", ctypes.c_uint64),
        ("other_transfer_count", ctypes.c_uint64),
    ]


class _JobObjectExtendedLimitInformation(ctypes.Structure):
    _fields_ = [
        ("basic_limit_information", _JobObjectBasicLimitInformation),
        ("io_info", _IoCounters),
        ("process_memory_limit", ctypes.c_size_t),
        ("job_memory_limit", ctypes.c_size_t),
        ("peak_process_memory_used", ctypes.c_size_t),
        ("peak_job_memory_used", ctypes.c_size_t),
    ]


class WindowsProcessJob:
    """Own acceptance children so a killed driver cannot orphan Core/Tauri."""

    _extended_limit_information = 9
    _kill_on_job_close = 0x00002000

    def __init__(self) -> None:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        kernel32.CreateJobObjectW.restype = wintypes.HANDLE
        kernel32.SetInformationJobObject.argtypes = [
            wintypes.HANDLE,
            ctypes.c_int,
            ctypes.c_void_p,
            wintypes.DWORD,
        ]
        kernel32.SetInformationJobObject.restype = wintypes.BOOL
        kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL
        handle = kernel32.CreateJobObjectW(None, None)
        if not handle:
            raise ctypes.WinError(ctypes.get_last_error())
        information = _JobObjectExtendedLimitInformation()
        information.basic_limit_information.limit_flags = self._kill_on_job_close
        if not kernel32.SetInformationJobObject(
            handle,
            self._extended_limit_information,
            ctypes.byref(information),
            ctypes.sizeof(information),
        ):
            error = ctypes.get_last_error()
            kernel32.CloseHandle(handle)
            raise ctypes.WinError(error)
        self._kernel32 = kernel32
        self._handle = handle

    def assign(self, process: subprocess.Popen[str]) -> None:
        if not self._kernel32.AssignProcessToJobObject(self._handle, process._handle):
            raise ctypes.WinError(ctypes.get_last_error())

    def close(self) -> None:
        if self._handle:
            self._kernel32.CloseHandle(self._handle)
            self._handle = None


class ProviderHandler(BaseHTTPRequestHandler):
    requests: list[dict[str, object]] = []

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length", "0"))
        type(self).requests.append(json.loads(self.rfile.read(length)))
        content = json.dumps(
            {
                "segments": [
                    {
                        "ja": "[WP-3-06-TAURI-REPLY]",
                        "zh": "[WP-3-06-TAURI-REPLY-ZH]",
                        "tone": "neutral",
                        "portrait": "neutral",
                    }
                ]
            },
            ensure_ascii=False,
        )
        response = json.dumps(
            {"choices": [{"message": {"role": "assistant", "content": content}}]},
            ensure_ascii=False,
        ).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(response)))
        self.end_headers()
        self.wfile.write(response)

    def log_message(self, _format: str, *_args: object) -> None:
        return None


def manifest(root: Path) -> dict[str, str]:
    return {
        path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def wait_for(path: Path, timeout: float = 40) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.is_file():
            return
        time.sleep(0.05)
    raise TimeoutError(f"acceptance marker timed out: {path.name}")


def environment(directory: Path, mode: str) -> dict[str, str]:
    env = dict(os.environ)
    env[DIRECTORY_ENV] = str(directory)
    env[MODE_ENV] = mode
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    return env


def run_process(
    command: list[str],
    directory: Path,
    mode: str,
    job: WindowsProcessJob,
    timeout: float = 75,
) -> None:
    process = start_process(command, directory, mode, job)
    finish_process(process, mode, timeout)


def start_process(
    command: list[str], directory: Path, mode: str, job: WindowsProcessJob
) -> subprocess.Popen[str]:
    process = subprocess.Popen(
        command,
        cwd=REPO_ROOT,
        env=environment(directory, mode),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    try:
        job.assign(process)
    except Exception:
        process.kill()
        process.communicate(timeout=10)
        raise
    return process


def finish_process(process: subprocess.Popen[str], mode: str, timeout: float = 45) -> None:
    try:
        stdout, stderr = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        process.kill()
        stdout, stderr = process.communicate(timeout=10)
        raise RuntimeError(f"{mode} timed out\nstdout={stdout}\nstderr={stderr}")
    if process.returncode != 0:
        raise RuntimeError(
            f"{mode} failed ({process.returncode})\nstdout={stdout}\nstderr={stderr}"
        )


def configure_provider(app_root: Path, port: int) -> None:
    path = app_root / "data/config/api.yaml"
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    profile = document["api_profiles"][0]
    profile["base_url"] = f"http://127.0.0.1:{port}/v1"
    profile["api_key"] = "LOCAL_WP_3_06_KEY"
    document["llm"]["base_url"] = profile["base_url"]
    document["llm"]["api_key"] = profile["api_key"]
    path.write_text(
        yaml.safe_dump(document, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )


def changed_paths(before: dict[str, str], after: dict[str, str]) -> set[str]:
    return {name for name in set(before) | set(after) if before.get(name) != after.get(name)}


def run() -> dict[str, object]:
    if os.name != "nt":
        raise RuntimeError("WP-3-06 real-process acceptance requires Windows")
    if not PYTHON.is_file() or not TAURI.is_file():
        raise RuntimeError("WP-3-06 acceptance requires runtime Python and a debug Tauri executable")

    directory = Path(tempfile.mkdtemp(prefix="sakura-wp-3-06-"))
    app_root = directory / "app-root"
    job = WindowsProcessJob()
    server = ThreadingHTTPServer(("127.0.0.1", 0), ProviderHandler)
    server.daemon_threads = True
    provider_thread = threading.Thread(target=server.serve_forever, name="wp-3-06-provider")
    provider_thread.start()
    try:
        shutil.copytree(SOURCE_DATASET, app_root)
        (directory / ".sakura-wp-3-06-sanitized").write_text("sanitized", encoding="utf-8")
        configure_provider(app_root, int(server.server_address[1]))
        before = manifest(app_root)

        run_process([str(PYTHON), "legacy_qt_main.py"], directory, "legacy-write", job)
        wait_for(directory / "legacy.write_complete")
        run_process([str(TAURI)], directory, "tauri-chat", job)
        wait_for(directory / "tauri.chat_complete")
        run_process([str(PYTHON), "legacy_qt_main.py"], directory, "legacy-read", job)
        wait_for(directory / "legacy.read_complete")

        legacy_hold = start_process(
            [str(PYTHON), "legacy_qt_main.py"], directory, "legacy-hold", job
        )
        wait_for(directory / "legacy.holding")
        run_process([str(TAURI)], directory, "tauri-hold", job, timeout=20)
        wait_for(directory / "tauri.lock_conflict")
        (directory / "legacy.release").write_text("release", encoding="utf-8")
        finish_process(legacy_hold, "legacy-hold")
        wait_for(directory / "legacy.released")

        (directory / "legacy.lock_conflict").unlink(missing_ok=True)
        tauri_hold = start_process([str(TAURI)], directory, "tauri-hold", job)
        wait_for(directory / "tauri.holding")
        run_process(
            [str(PYTHON), "legacy_qt_main.py"],
            directory,
            "legacy-read",
            job,
            timeout=20,
        )
        wait_for(directory / "legacy.lock_conflict")
        (directory / "tauri.release").write_text("release", encoding="utf-8")
        finish_process(tauri_hold, "tauri-hold")
        wait_for(directory / "tauri.released")

        run_process([str(PYTHON), "legacy_qt_main.py"], directory, "legacy-read", job)
        after = manifest(app_root)
        changed = changed_paths(before, after)
        if changed != ALLOWED_CHANGES:
            raise AssertionError(f"unexpected compatibility manifest changes: {sorted(changed)}")
        if not ProviderHandler.requests:
            raise AssertionError("the real Tauri/Core path did not reach the local provider")
        return {
            "status": "passed",
            "changed_paths": sorted(changed),
            "fixture_files": len(after),
            "provider_requests": len(ProviderHandler.requests),
            "lock_conflicts": ["legacy-holds-tauri", "tauri-holds-legacy"],
            "acceptance_root_removed": True,
        }
    finally:
        job.close()
        server.shutdown()
        server.server_close()
        provider_thread.join(5)
        shutil.rmtree(directory, ignore_errors=True)


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False, sort_keys=True))
