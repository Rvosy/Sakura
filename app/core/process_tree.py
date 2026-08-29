"""Small cross-platform helpers for terminating an owned subprocess tree."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from collections.abc import Sequence
from typing import Protocol


class ProcessHandle(Protocol):
    pid: int

    def kill(self) -> None: ...

    def poll(self) -> int | None: ...

    def wait(self, timeout: float | None = None) -> int: ...


def terminate_process_tree(process: ProcessHandle, *, timeout: float) -> None:
    """Best-effort terminate ``process`` and the descendants it owns.

    The descendant snapshot is taken before the root is terminated. This is
    important on POSIX, where children are re-parented as soon as their parent
    exits and can no longer be discovered through that root PID.
    """

    budget = max(0.0, float(timeout))
    if process.poll() is not None:
        return
    deadline = time.monotonic() + budget
    if sys.platform == "win32":
        _terminate_windows_tree(process.pid, timeout=budget)
        _wait_or_kill_root(process, deadline)
        return

    descendants = _posix_descendant_pids(process.pid)
    targets = [*reversed(descendants), process.pid]
    _signal_existing(targets, signal.SIGTERM)
    _wait_until_gone(targets, deadline)
    remaining = [pid for pid in targets if _process_exists(pid)]
    if remaining:
        _signal_existing(remaining, signal.SIGKILL)
    _wait_or_kill_root(process, deadline)


def _wait_or_kill_root(process: ProcessHandle, deadline: float) -> None:
    if process.poll() is not None:
        return
    try:
        process.wait(timeout=max(0.0, deadline - time.monotonic()))
        return
    except (OSError, subprocess.TimeoutExpired):
        pass
    try:
        process.kill()
    except OSError:
        return
    try:
        process.wait(timeout=max(0.0, deadline - time.monotonic()))
    except (OSError, subprocess.TimeoutExpired):
        pass


def _terminate_windows_tree(root_pid: int, *, timeout: float) -> None:
    descendants = _windows_descendant_pids(root_pid)
    for pid in reversed(descendants):
        _terminate_windows_pid(pid)
    _terminate_windows_pid(root_pid)
    deadline = time.monotonic() + max(0.0, timeout)
    # The root is owned by ``subprocess.Popen``.  Its Windows process object
    # remains observable while Popen still holds the process handle, even
    # after TerminateProcess has completed.  Waiting for that PID to disappear
    # here consumes the whole timeout before ``_wait_or_kill_root`` can reap
    # it.  Descendants have no Popen owner, so only wait for those here and let
    # the caller reap the root through the actual process handle.
    _wait_until_gone(descendants, deadline)


def _windows_descendant_pids(root_pid: int) -> list[int]:
    """Snapshot descendants without depending on taskkill permissions."""

    import ctypes
    from ctypes import wintypes

    class ProcessEntry32(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD),
            ("cntUsage", wintypes.DWORD),
            ("th32ProcessID", wintypes.DWORD),
            ("th32DefaultHeapID", ctypes.c_size_t),
            ("th32ModuleID", wintypes.DWORD),
            ("cntThreads", wintypes.DWORD),
            ("th32ParentProcessID", wintypes.DWORD),
            ("pcPriClassBase", wintypes.LONG),
            ("dwFlags", wintypes.DWORD),
            ("szExeFile", wintypes.WCHAR * 260),
        ]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    create_snapshot = kernel32.CreateToolhelp32Snapshot
    create_snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    create_snapshot.restype = wintypes.HANDLE
    process_first = kernel32.Process32FirstW
    process_first.argtypes = [wintypes.HANDLE, ctypes.POINTER(ProcessEntry32)]
    process_first.restype = wintypes.BOOL
    process_next = kernel32.Process32NextW
    process_next.argtypes = [wintypes.HANDLE, ctypes.POINTER(ProcessEntry32)]
    process_next.restype = wintypes.BOOL
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = [wintypes.HANDLE]
    close_handle.restype = wintypes.BOOL

    snapshot = create_snapshot(0x00000002, 0)  # TH32CS_SNAPPROCESS
    invalid = ctypes.c_void_p(-1).value
    if snapshot in (None, invalid):
        return []
    children: dict[int, list[int]] = {}
    try:
        entry = ProcessEntry32()
        entry.dwSize = ctypes.sizeof(entry)
        if not process_first(snapshot, ctypes.byref(entry)):
            return []
        while True:
            children.setdefault(
                int(entry.th32ParentProcessID),
                [],
            ).append(int(entry.th32ProcessID))
            if not process_next(snapshot, ctypes.byref(entry)):
                break
    finally:
        close_handle(snapshot)

    descendants: list[int] = []
    pending = list(children.get(int(root_pid), ()))
    while pending:
        pid = pending.pop()
        descendants.append(pid)
        pending.extend(children.get(pid, ()))
    return descendants


def _terminate_windows_pid(pid: int) -> None:
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    open_process = kernel32.OpenProcess
    open_process.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    open_process.restype = wintypes.HANDLE
    terminate_process = kernel32.TerminateProcess
    terminate_process.argtypes = [wintypes.HANDLE, wintypes.UINT]
    terminate_process.restype = wintypes.BOOL
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = [wintypes.HANDLE]
    close_handle.restype = wintypes.BOOL
    handle = open_process(0x0001, False, int(pid))  # PROCESS_TERMINATE
    if not handle:
        return
    try:
        terminate_process(handle, 1)
    finally:
        close_handle(handle)


def _posix_descendant_pids(root_pid: int) -> list[int]:
    try:
        result = subprocess.run(
            ["ps", "-eo", "pid=,ppid="],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            check=False,
            timeout=2,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    children: dict[int, list[int]] = {}
    for line in result.stdout.splitlines():
        try:
            pid_text, parent_text = line.split(None, 1)
            child_pid, parent_pid = int(pid_text), int(parent_text)
        except (TypeError, ValueError):
            continue
        children.setdefault(parent_pid, []).append(child_pid)
    descendants: list[int] = []
    pending = list(children.get(root_pid, ()))
    while pending:
        child_pid = pending.pop()
        descendants.append(child_pid)
        pending.extend(children.get(child_pid, ()))
    return descendants


def _signal_existing(pids: Sequence[int], sent_signal: signal.Signals) -> None:
    for pid in pids:
        try:
            os.kill(pid, sent_signal)
        except (OSError, ProcessLookupError):
            pass


def _wait_until_gone(pids: Sequence[int], deadline: float) -> None:
    while time.monotonic() < deadline:
        if not any(_process_exists(pid) for pid in pids):
            return
        time.sleep(min(0.02, max(0.0, deadline - time.monotonic())))


def _process_exists(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True
