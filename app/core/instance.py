"""Shared desktop instance lock used by the legacy Qt and Tauri roots."""

from __future__ import annotations

import ctypes
import errno
import os
import stat
import sys
from enum import Enum
from pathlib import Path
from typing import Mapping


SHARED_MUTEX_NAME = r"Local\SakuraDesktop.SharedUserData.v1"
SHARED_INSTANCE_ID = "sakura.desktop.shared-user-data.v1"
POSIX_LOCK_DIRECTORY = "sakura"
POSIX_LOCK_FILE_NAME = f"{SHARED_INSTANCE_ID}.lock"

_ERROR_ALREADY_EXISTS = 183


class InstanceAcquireStatus(str, Enum):
    ACQUIRED = "acquired"
    ALREADY_RUNNING = "already_running"
    FATAL = "fatal"


def _required_absolute_root(environment: Mapping[str, str], name: str) -> Path | None:
    raw = environment.get(name)
    if raw is None or raw == "":
        return None
    root = Path(raw)
    if not root.is_absolute():
        raise OSError(errno.EINVAL, f"{name} must be an absolute path", raw)
    return root


def resolve_posix_lock_path(
    environment: Mapping[str, str] | None = None,
    platform: str | None = None,
) -> Path:
    """Resolve the frozen per-user POSIX lock path without touching the filesystem."""

    env = os.environ if environment is None else environment
    target = sys.platform if platform is None else platform

    if target == "darwin":
        root = _required_absolute_root(env, "TMPDIR")
        if root is None:
            home = _required_absolute_root(env, "HOME")
            if home is None:
                raise OSError(errno.ENOENT, "TMPDIR and HOME are both unavailable")
            root = home / "Library" / "Caches"
    elif target.startswith("linux"):
        root = _required_absolute_root(env, "XDG_RUNTIME_DIR")
        if root is None:
            root = _required_absolute_root(env, "XDG_STATE_HOME")
        if root is None:
            home = _required_absolute_root(env, "HOME")
            if home is None:
                raise OSError(
                    errno.ENOENT,
                    "XDG_RUNTIME_DIR, XDG_STATE_HOME and HOME are unavailable",
                )
            root = home / ".local" / "state"
    else:
        raise OSError(errno.ENOTSUP, f"unsupported POSIX lock platform: {target}")

    return root / POSIX_LOCK_DIRECTORY / POSIX_LOCK_FILE_NAME


def _prepare_posix_lock_path(path: Path) -> Path:
    parent = path.parent
    parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    canonical_parent = parent.resolve(strict=True)
    parent_stat = canonical_parent.stat()
    if not stat.S_ISDIR(parent_stat.st_mode):
        raise OSError(errno.ENOTDIR, "shared lock parent is not a directory", str(parent))
    if hasattr(os, "geteuid") and parent_stat.st_uid != os.geteuid():
        raise OSError(errno.EPERM, "shared lock parent is not owned by the current user", str(parent))
    os.chmod(canonical_parent, 0o700)
    return canonical_parent / path.name


class SingleInstanceGuard:
    """Own the platform shared lock for the complete desktop lifetime."""

    def __init__(self, base_dir: Path | None = None) -> None:
        del base_dir
        self.last_error = 0
        self._handle: int | None = None
        self._fd: int | None = None
        self._lock_path: Path | None = None
        self._kernel32 = None

        if os.name == "nt":
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

    @property
    def lock_path(self) -> Path | None:
        return self._lock_path

    def acquire(self) -> InstanceAcquireStatus:
        if os.name == "nt":
            return self._acquire_windows()
        return self._acquire_posix()

    def _acquire_windows(self) -> InstanceAcquireStatus:
        if self._handle is not None:
            return InstanceAcquireStatus.ACQUIRED
        assert self._kernel32 is not None

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

    def _acquire_posix(self) -> InstanceAcquireStatus:
        if self._fd is not None:
            return InstanceAcquireStatus.ACQUIRED

        try:
            import fcntl

            if self._lock_path is None:
                self._lock_path = resolve_posix_lock_path()
            lock_path = _prepare_posix_lock_path(self._lock_path)
            flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_CLOEXEC", 0)
            flags |= getattr(os, "O_NOFOLLOW", 0)
            fd = os.open(lock_path, flags, 0o600)
            try:
                file_stat = os.fstat(fd)
                if not stat.S_ISREG(file_stat.st_mode) or file_stat.st_nlink != 1:
                    raise OSError(errno.EINVAL, "shared lock must be a single regular file")
                if hasattr(os, "geteuid") and file_stat.st_uid != os.geteuid():
                    raise OSError(errno.EPERM, "shared lock is not owned by the current user")
                os.fchmod(fd, 0o600)
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except OSError as error:
                    if error.errno in {errno.EACCES, errno.EAGAIN, errno.EWOULDBLOCK}:
                        os.close(fd)
                        self.last_error = int(error.errno or errno.EWOULDBLOCK)
                        return InstanceAcquireStatus.ALREADY_RUNNING
                    raise
            except BaseException:
                os.close(fd)
                raise
        except OSError as error:
            self.last_error = int(error.errno or errno.EIO)
            return InstanceAcquireStatus.FATAL

        self._lock_path = lock_path
        self._fd = fd
        self.last_error = 0
        return InstanceAcquireStatus.ACQUIRED

    def release(self) -> None:
        if os.name == "nt":
            handle = self._handle
            if handle is None:
                return
            self._handle = None
            assert self._kernel32 is not None
            self._kernel32.ReleaseMutex(handle)
            self._kernel32.CloseHandle(handle)
            return

        fd = self._fd
        if fd is None:
            return
        self._fd = None
        try:
            import fcntl

            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)

    def holder_description(self) -> str:
        return "另一个 Sakura 实例"

    def __del__(self) -> None:
        try:
            self.release()
        except Exception:  # noqa: BLE001
            pass
