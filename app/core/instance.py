"""Windows named mutex shared by the legacy Qt and Tauri desktop roots."""

from __future__ import annotations

import ctypes
from enum import Enum
from pathlib import Path


SHARED_MUTEX_NAME = r"Local\SakuraDesktop.SharedUserData.v1"

_ERROR_ALREADY_EXISTS = 183


class InstanceAcquireStatus(str, Enum):
    ACQUIRED = "acquired"
    ALREADY_RUNNING = "already_running"
    FATAL = "fatal"


class SingleInstanceGuard:
    """Own the shared Win32 mutex for the complete desktop lifetime.

    ``base_dir`` is retained for source compatibility with the legacy entry, but
    mutex construction and acquisition intentionally perform no filesystem I/O.
    """

    def __init__(self, base_dir: Path | None = None) -> None:
        del base_dir
        self._kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self._kernel32.CreateMutexW.argtypes = [
            ctypes.c_void_p,
            ctypes.c_bool,
            ctypes.c_wchar_p,
        ]
        self._kernel32.CreateMutexW.restype = ctypes.c_void_p
        self._kernel32.ReleaseMutex.argtypes = [ctypes.c_void_p]
        self._kernel32.ReleaseMutex.restype = ctypes.c_bool
        self._kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
        self._kernel32.CloseHandle.restype = ctypes.c_bool
        self._handle: int | None = None
        self.last_error = 0

    def acquire(self) -> InstanceAcquireStatus:
        if self._handle is not None:
            return InstanceAcquireStatus.ACQUIRED

        ctypes.set_last_error(0)
        handle = self._kernel32.CreateMutexW(None, True, SHARED_MUTEX_NAME)
        error = ctypes.get_last_error()
        if not handle:
            self.last_error = int(error)
            return InstanceAcquireStatus.FATAL
        if error == _ERROR_ALREADY_EXISTS:
            self._kernel32.CloseHandle(handle)
            self.last_error = int(error)
            return InstanceAcquireStatus.ALREADY_RUNNING

        self._handle = int(handle)
        self.last_error = 0
        return InstanceAcquireStatus.ACQUIRED

    def release(self) -> None:
        handle = self._handle
        if handle is None:
            return
        self._handle = None
        self._kernel32.ReleaseMutex(handle)
        self._kernel32.CloseHandle(handle)

    def __del__(self) -> None:
        try:
            self.release()
        except Exception:  # noqa: BLE001
            pass
