from __future__ import annotations

import ctypes
import errno
import os
import sys
import uuid
from pathlib import Path

import pytest

from app.core import instance


@pytest.fixture(autouse=True)
def _private_instance_lock(
    request: pytest.FixtureRequest,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    if os.name == "nt":
        if request.node.name != "test_shared_mutex_uses_the_frozen_windows_object_name":
            monkeypatch.setattr(
                instance,
                "SHARED_MUTEX_NAME",
                f"Local\\SakuraDesktop.Pytest.{uuid.uuid4().hex}",
            )
        return
    if sys.platform == "darwin":
        monkeypatch.setenv("TMPDIR", str(tmp_path))
    else:
        monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))


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


@pytest.mark.skipif(os.name != "nt", reason="Win32 kernel object contract")
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


@pytest.mark.skipif(os.name == "nt", reason="POSIX path contract")
def test_posix_lock_path_uses_frozen_platform_roots() -> None:
    assert instance.resolve_posix_lock_path(
        {"XDG_RUNTIME_DIR": "/run/user/1000", "HOME": "/home/user"},
        "linux",
    ) == Path("/run/user/1000/sakura/sakura.desktop.shared-user-data.v1.lock")
    assert instance.resolve_posix_lock_path(
        {"XDG_STATE_HOME": "/home/user/.state", "HOME": "/home/user"},
        "linux",
    ) == Path("/home/user/.state/sakura/sakura.desktop.shared-user-data.v1.lock")
    assert instance.resolve_posix_lock_path(
        {"HOME": "/home/user"},
        "linux",
    ) == Path("/home/user/.local/state/sakura/sakura.desktop.shared-user-data.v1.lock")
    assert instance.resolve_posix_lock_path(
        {"TMPDIR": "/private/tmp/user", "HOME": "/Users/user"},
        "darwin",
    ) == Path("/private/tmp/user/sakura/sakura.desktop.shared-user-data.v1.lock")
    assert instance.resolve_posix_lock_path(
        {"HOME": "/Users/user"},
        "darwin",
    ) == Path("/Users/user/Library/Caches/sakura/sakura.desktop.shared-user-data.v1.lock")
    with pytest.raises(OSError, match="absolute path"):
        instance.resolve_posix_lock_path(
            {"XDG_RUNTIME_DIR": "relative", "HOME": "/home/user"},
            "linux",
        )
    with pytest.raises(OSError, match="absolute path"):
        instance.resolve_posix_lock_path(
            {"XDG_RUNTIME_DIR": "   ", "HOME": "/home/user"},
            "linux",
        )


@pytest.mark.skipif(os.name == "nt", reason="POSIX advisory lock contract")
def test_posix_existing_unlocked_file_is_not_a_conflict() -> None:
    path = instance.resolve_posix_lock_path()
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.touch(mode=0o600, exist_ok=True)

    guard = instance.SingleInstanceGuard()
    assert guard.acquire() is instance.InstanceAcquireStatus.ACQUIRED
    guard.release()


@pytest.mark.skipif(os.name == "nt", reason="POSIX permission failure contract")
def test_posix_open_permission_failure_is_fatal(monkeypatch: pytest.MonkeyPatch) -> None:
    guard = instance.SingleInstanceGuard()

    def deny_open(*_args: object, **_kwargs: object) -> int:
        raise OSError(errno.EACCES, "denied")

    monkeypatch.setattr(instance.os, "open", deny_open)

    assert guard.acquire() is instance.InstanceAcquireStatus.FATAL
    assert guard.last_error == errno.EACCES
