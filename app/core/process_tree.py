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
    kwargs: dict[str, object] = {
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "check": False,
        "timeout": max(0.05, timeout),
    }
    if hasattr(subprocess, "CREATE_NO_WINDOW"):
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW")
    try:
        subprocess.run(
            ["taskkill", "/PID", str(int(root_pid)), "/T", "/F"],
            **kwargs,
        )
    except (OSError, subprocess.TimeoutExpired):
        pass


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
