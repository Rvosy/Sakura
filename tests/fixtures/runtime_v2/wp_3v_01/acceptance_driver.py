from __future__ import annotations

import ctypes
import hashlib
import json
import os
import re
import shutil
import signal
import subprocess
import tempfile
import threading
import time
from ctypes import wintypes
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import yaml


REPO_ROOT = Path(__file__).resolve().parents[4]
SOURCE_DATASET = REPO_ROOT / "tests/fixtures/runtime_v2/wp_0_02/dataset"
PYTHON = (
    REPO_ROOT / "runtime/python.exe"
    if os.name == "nt"
    else REPO_ROOT / "runtime/bin/python3"
)
TAURI = REPO_ROOT / "desktop/src-tauri/target/debug/sakura-runtime-v2-shell"
if os.name == "nt":
    TAURI = TAURI.with_suffix(".exe")

WP3V_DIRECTORY_ENV = "SAKURA_WP_3V_01_ACCEPTANCE_DIRECTORY"
WP3V_MODE_ENV = "SAKURA_WP_3V_01_ACCEPTANCE_MODE"
LEGACY_DIRECTORY_ENV = "SAKURA_WP_3_06_ACCEPTANCE_DIRECTORY"
LEGACY_MODE_ENV = "SAKURA_WP_3_06_ACCEPTANCE_MODE"
ALLOWED_CHANGES = {"data/chat_history/fixture.jsonl"}
PRIVATE_PROVIDER_KEY = "LOCAL_WP_3V_01_KEY"
_SENSITIVE_PATTERNS = (
    re.compile(r"authorization\s*[:=]", re.IGNORECASE),
    re.compile(r"bearer\s+[A-Za-z0-9._-]+", re.IGNORECASE),
    re.compile(r"(?:api[_ -]?key|token|secret|password|credential)\s*[:=]", re.IGNORECASE),
    re.compile(r"PRIVATE_[A-Z0-9_]+"),
    re.compile(re.escape(PRIVATE_PROVIDER_KEY)),
)


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


class _JobObjectBasicAccountingInformation(ctypes.Structure):
    _fields_ = [
        ("total_user_time", ctypes.c_int64),
        ("total_kernel_time", ctypes.c_int64),
        ("this_period_total_user_time", ctypes.c_int64),
        ("this_period_total_kernel_time", ctypes.c_int64),
        ("total_page_fault_count", wintypes.DWORD),
        ("total_processes", wintypes.DWORD),
        ("active_processes", wintypes.DWORD),
        ("total_terminated_processes", wintypes.DWORD),
    ]


class ProcessOwner:
    """Own only acceptance children and expose a pre-cleanup residue count."""

    def __init__(self) -> None:
        self._groups: set[int] = set()
        self._kernel32 = None
        self._handle = None
        if os.name != "nt":
            return
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
        kernel32.QueryInformationJobObject.argtypes = [
            wintypes.HANDLE,
            ctypes.c_int,
            ctypes.c_void_p,
            wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD),
        ]
        kernel32.QueryInformationJobObject.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL
        handle = kernel32.CreateJobObjectW(None, None)
        if not handle:
            raise ctypes.WinError(ctypes.get_last_error())
        information = _JobObjectExtendedLimitInformation()
        information.basic_limit_information.limit_flags = 0x00002000
        if not kernel32.SetInformationJobObject(
            handle, 9, ctypes.byref(information), ctypes.sizeof(information)
        ):
            error = ctypes.get_last_error()
            kernel32.CloseHandle(handle)
            raise ctypes.WinError(error)
        self._kernel32 = kernel32
        self._handle = handle

    def assign(self, process: subprocess.Popen[str]) -> None:
        if os.name == "nt":
            assert self._kernel32 is not None and self._handle is not None
            if not self._kernel32.AssignProcessToJobObject(self._handle, process._handle):
                raise ctypes.WinError(ctypes.get_last_error())
        else:
            self._groups.add(process.pid)

    def active_count(self) -> int:
        if os.name == "nt":
            assert self._kernel32 is not None and self._handle is not None
            information = _JobObjectBasicAccountingInformation()
            returned = wintypes.DWORD()
            if not self._kernel32.QueryInformationJobObject(
                self._handle,
                1,
                ctypes.byref(information),
                ctypes.sizeof(information),
                ctypes.byref(returned),
            ):
                raise ctypes.WinError(ctypes.get_last_error())
            return int(information.active_processes)
        active = 0
        for pid, _parent, _name, group in process_table():
            if group in self._groups and pid != os.getpid():
                active += 1
        return active

    def close(self) -> None:
        if os.name == "nt":
            if self._handle:
                assert self._kernel32 is not None
                self._kernel32.CloseHandle(self._handle)
                self._handle = None
            return
        for group in self._groups:
            try:
                os.killpg(group, signal.SIGKILL)
            except ProcessLookupError:
                pass


class ProviderHandler(BaseHTTPRequestHandler):
    requests: list[dict[str, object]] = []
    lock = threading.Lock()

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length", "0"))
        request = json.loads(self.rfile.read(length))
        with type(self).lock:
            type(self).requests.append(request)
            request_number = len(type(self).requests)
        message = _last_user_message(request)
        if "CANCEL" in message or "SHUTDOWN" in message:
            time.sleep(3)
        reply = {
            1: "[WP-3V-01-REPLY-1]",
            3: "[WP-3V-01-REPLY-3]",
        }.get(request_number, "[WP-3V-01-SLOW-REPLY]")
        content = json.dumps(
            {
                "segments": [
                    {
                        "ja": reply,
                        "zh": f"{reply}-ZH",
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
        try:
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(response)))
            self.end_headers()
            self.wfile.write(response)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def log_message(self, _format: str, *_args: object) -> None:
        return None


def _last_user_message(request: dict[str, object]) -> str:
    messages = request.get("messages")
    if not isinstance(messages, list):
        return ""
    for message in reversed(messages):
        if isinstance(message, dict) and message.get("role") == "user":
            content = message.get("content")
            return content if isinstance(content, str) else ""
    return ""


def manifest(root: Path) -> dict[str, str]:
    return {
        path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def changed_paths(before: dict[str, str], after: dict[str, str]) -> set[str]:
    return {name for name in set(before) | set(after) if before.get(name) != after.get(name)}


def find_sensitive_evidence(text: str) -> list[str]:
    return [pattern.pattern for pattern in _SENSITIVE_PATTERNS if pattern.search(text)]


def wait_for(path: Path, timeout: float = 45) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.is_file():
            return
        time.sleep(0.05)
    raise TimeoutError(f"acceptance marker timed out: {path.name}")


def wait_for_zero(owner: ProcessOwner, timeout: float = 12) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if owner.active_count() == 0:
            return
        time.sleep(0.1)
    raise RuntimeError(f"acceptance process residue remained: {owner.active_count()}")


def configure_provider(app_root: Path, port: int) -> None:
    path = app_root / "data/config/api.yaml"
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    profile = document["api_profiles"][0]
    profile["base_url"] = f"http://127.0.0.1:{port}/v1"
    profile["api_key"] = PRIVATE_PROVIDER_KEY
    document["llm"]["base_url"] = profile["base_url"]
    document["llm"]["api_key"] = profile["api_key"]
    path.write_text(
        yaml.safe_dump(document, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )


def seed_frozen_legacy_oracle_markers(app_root: Path) -> None:
    history = app_root / "data/chat_history/fixture.jsonl"
    entries = [
        {"role": "user", "content": "[WP-3-06-LEGACY-USER]"},
        {"role": "assistant", "content": "[WP-3-06-LEGACY-REPLY]"},
        {"role": "user", "content": "[WP-3-06-TAURI-USER]"},
        {"role": "assistant", "content": "[WP-3-06-TAURI-REPLY]"},
    ]
    with history.open("a", encoding="utf-8", newline="\n") as stream:
        for index, entry in enumerate(entries, start=2):
            stream.write(
                json.dumps(
                    {
                        "created_at": f"2000-01-01T00:00:{index:02d}+00:00",
                        **entry,
                    },
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
                + "\n"
            )


def environment(directory: Path, *, legacy: bool = False) -> dict[str, str]:
    env = dict(os.environ)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    if legacy:
        env[LEGACY_DIRECTORY_ENV] = str(directory)
        env[LEGACY_MODE_ENV] = "legacy-read"
    else:
        env[WP3V_DIRECTORY_ENV] = str(directory)
        env[WP3V_MODE_ENV] = "vertical"
    return env


def start_process(
    command: list[str], directory: Path, owner: ProcessOwner, *, legacy: bool = False
) -> subprocess.Popen[str]:
    kwargs: dict[str, object] = {}
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True
    process = subprocess.Popen(
        command,
        cwd=REPO_ROOT,
        env=environment(directory, legacy=legacy),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        **kwargs,
    )
    try:
        owner.assign(process)
    except Exception:
        process.kill()
        process.communicate(timeout=10)
        raise
    return process


def finish_process(
    process: subprocess.Popen[str], label: str, timeout: float = 75
) -> tuple[str, str]:
    try:
        stdout, stderr = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        process.kill()
        stdout, stderr = process.communicate(timeout=10)
        raise RuntimeError(f"{label} timed out\nstdout={stdout}\nstderr={stderr}")
    if process.returncode != 0:
        raise RuntimeError(
            f"{label} failed ({process.returncode})\nstdout={stdout}\nstderr={stderr}"
        )
    return stdout, stderr


def process_table() -> list[tuple[int, int, str, int]]:
    if os.name == "nt":
        return _windows_process_table()
    output = subprocess.run(
        ["ps", "-axo", "pid=,ppid=,comm=,pgid="],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    ).stdout
    rows = []
    for line in output.splitlines():
        parts = line.strip().split(None, 3)
        if len(parts) == 4:
            rows.append((int(parts[0]), int(parts[1]), parts[2], int(parts[3])))
    return rows


def _windows_process_table() -> list[tuple[int, int, str, int]]:
    class ProcessEntry32W(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD),
            ("cntUsage", wintypes.DWORD),
            ("th32ProcessID", wintypes.DWORD),
            ("th32DefaultHeapID", ctypes.c_size_t),
            ("th32ModuleID", wintypes.DWORD),
            ("cntThreads", wintypes.DWORD),
            ("th32ParentProcessID", wintypes.DWORD),
            ("pcPriClassBase", ctypes.c_long),
            ("dwFlags", wintypes.DWORD),
            ("szExeFile", wintypes.WCHAR * 260),
        ]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel32.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(ProcessEntry32W)]
    kernel32.Process32FirstW.restype = wintypes.BOOL
    kernel32.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(ProcessEntry32W)]
    kernel32.Process32NextW.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    snapshot = kernel32.CreateToolhelp32Snapshot(0x00000002, 0)
    if snapshot == wintypes.HANDLE(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    rows: list[tuple[int, int, str, int]] = []
    try:
        entry = ProcessEntry32W()
        entry.dwSize = ctypes.sizeof(entry)
        success = kernel32.Process32FirstW(snapshot, ctypes.byref(entry))
        while success:
            rows.append(
                (
                    int(entry.th32ProcessID),
                    int(entry.th32ParentProcessID),
                    str(entry.szExeFile),
                    0,
                )
            )
            success = kernel32.Process32NextW(snapshot, ctypes.byref(entry))
    finally:
        kernel32.CloseHandle(snapshot)
    return rows


def terminate_core_descendant(root_pid: int, timeout: float = 15) -> int:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        rows = process_table()
        descendants = {root_pid}
        changed = True
        while changed:
            changed = False
            for pid, parent, _name, _group in rows:
                if parent in descendants and pid not in descendants:
                    descendants.add(pid)
                    changed = True
        candidates = [
            pid
            for pid, _parent, name, _group in rows
            if pid in descendants and pid != root_pid and "python" in Path(name).name.lower()
        ]
        if candidates:
            pid = candidates[0]
            if os.name == "nt":
                _terminate_windows_process(pid)
            else:
                os.kill(pid, signal.SIGKILL)
            return pid
        time.sleep(0.05)
    raise RuntimeError("real bundled Python Core descendant was not found")


def _terminate_windows_process(pid: int) -> None:
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
    kernel32.TerminateProcess.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel32.OpenProcess(0x0001 | 0x00100000, False, pid)
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        if not kernel32.TerminateProcess(handle, 97):
            raise ctypes.WinError(ctypes.get_last_error())
    finally:
        kernel32.CloseHandle(handle)


def run() -> dict[str, object]:
    if not PYTHON.is_file() or not TAURI.is_file():
        raise RuntimeError("WP-3V-01 requires the staged Runtime Python and debug Tauri executable")

    ProviderHandler.requests = []
    directory = Path(tempfile.mkdtemp(prefix="sakura-wp-3-06-"))
    app_root = directory / "app-root"
    owner = ProcessOwner()
    server = ThreadingHTTPServer(("127.0.0.1", 0), ProviderHandler)
    server.daemon_threads = True
    provider_thread = threading.Thread(target=server.serve_forever, name="wp-3v-01-provider")
    provider_thread.start()
    evidence_text = ""
    try:
        shutil.copytree(SOURCE_DATASET, app_root)
        (directory / ".sakura-wp-3v-01-sanitized").write_text("sanitized", encoding="utf-8")
        (directory / ".sakura-wp-3-06-sanitized").write_text("sanitized", encoding="utf-8")
        configure_provider(app_root, int(server.server_address[1]))
        seed_frozen_legacy_oracle_markers(app_root)
        before = manifest(app_root)

        tauri = start_process([str(TAURI)], directory, owner)
        wait_for(directory / "core.kill_requested")
        core_pid = terminate_core_descendant(tauri.pid)
        (directory / "core.killed").write_text(str(core_pid), encoding="utf-8")
        wait_for(directory / "tauri.vertical_complete")
        wait_for(directory / "tauri.shutdown_during_chat")
        stdout, stderr = finish_process(tauri, "wp-3v-01-tauri")
        evidence_text += stdout + stderr

        legacy = start_process(
            [str(PYTHON), "legacy_qt_main.py"], directory, owner, legacy=True
        )
        legacy_stdout, legacy_stderr = finish_process(legacy, "wp-3v-01-legacy-oracle")
        evidence_text += legacy_stdout + legacy_stderr
        wait_for(directory / "legacy.read_complete")

        after = manifest(app_root)
        changed = changed_paths(before, after)
        if changed != ALLOWED_CHANGES:
            raise AssertionError(f"unexpected manifest changes: {sorted(changed)}")
        evidence_text += (directory / "tauri.evidence.json").read_text(encoding="utf-8")
        sensitive = find_sensitive_evidence(evidence_text)
        if sensitive:
            raise AssertionError(f"sensitive evidence markers found: {sensitive}")
        if len(ProviderHandler.requests) < 4:
            raise AssertionError("real Assistant path did not complete all provider requests")
        wait_for_zero(owner)
        return {
            "status": "passed",
            "changed_paths": sorted(changed),
            "fixture_files": len(after),
            "provider_requests": len(ProviderHandler.requests),
            "core_kills": 1,
            "cancel_terminals": 1,
            "generation_rehydrated": True,
            "legacy_oracle": "read-compatible",
            "process_residue": 0,
            "sensitive_evidence": 0,
            "acceptance_root_removed": True,
        }
    finally:
        owner.close()
        server.shutdown()
        server.server_close()
        provider_thread.join(5)
        shutil.rmtree(directory, ignore_errors=True)


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False, sort_keys=True))
