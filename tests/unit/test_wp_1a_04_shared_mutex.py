from __future__ import annotations

import ctypes
from pathlib import Path

from app.core import instance


def test_shared_mutex_uses_the_frozen_windows_object_name(tmp_path: Path) -> None:
    assert instance.SHARED_MUTEX_NAME == "Local\\SakuraDesktop.SharedUserData.v1"
    guard = instance.SingleInstanceGuard(tmp_path)
    assert not (tmp_path / "data").exists()
    guard.release()


def test_shared_mutex_reports_conflict_and_can_be_reacquired_after_release(
    tmp_path: Path,
) -> None:
    first = instance.SingleInstanceGuard(tmp_path)
    second = instance.SingleInstanceGuard(tmp_path)

    assert first.acquire() is instance.InstanceAcquireStatus.ACQUIRED
    assert second.acquire() is instance.InstanceAcquireStatus.ALREADY_RUNNING

    first.release()
    assert second.acquire() is instance.InstanceAcquireStatus.ACQUIRED
    second.release()


def test_shared_mutex_distinguishes_win32_api_failure_from_conflict(
    tmp_path: Path,
) -> None:
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateEventW.argtypes = [
        ctypes.c_void_p,
        ctypes.c_bool,
        ctypes.c_bool,
        ctypes.c_wchar_p,
    ]
    kernel32.CreateEventW.restype = ctypes.c_void_p
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    kernel32.CloseHandle.restype = ctypes.c_bool

    event = kernel32.CreateEventW(None, True, False, instance.SHARED_MUTEX_NAME)
    assert event
    try:
        guard = instance.SingleInstanceGuard(tmp_path)
        assert guard.acquire() is instance.InstanceAcquireStatus.FATAL
        assert guard.last_error != 0
        guard.release()
    finally:
        assert kernel32.CloseHandle(event)
