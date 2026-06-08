from __future__ import annotations

import plistlib
import shlex
from pathlib import Path

import pytest

from app.platforms.launch_at_login import (
    LAUNCH_AT_LOGIN_LABEL,
    LINUX_AUTOSTART_FILENAME,
    WINDOWS_RUN_VALUE_NAME,
    LaunchAtLoginError,
    resolve_launch_at_login_target,
    set_launch_at_login_enabled,
)


def test_macos_launch_agent_is_written_and_removed(tmp_path: Path) -> None:
    root = _runtime_root(tmp_path)
    home = tmp_path / "home"

    set_launch_at_login_enabled(root, True, platform="darwin", home_dir=home)

    plist_path = home / "Library" / "LaunchAgents" / f"{LAUNCH_AT_LOGIN_LABEL}.plist"
    data = plistlib.loads(plist_path.read_bytes())
    assert data["Label"] == LAUNCH_AT_LOGIN_LABEL
    assert data["ProgramArguments"] == ["/bin/bash", str(root / "scripts" / "start.sh")]
    assert data["RunAtLoad"] is True
    assert data["LimitLoadToSessionType"] == "Aqua"
    assert data["StandardOutPath"] == str(root / "data" / "logs" / "launch-at-login.out.log")

    set_launch_at_login_enabled(root, False, platform="darwin", home_dir=home)

    assert not plist_path.exists()


def test_linux_autostart_desktop_file_is_written_and_removed(tmp_path: Path) -> None:
    root = _runtime_root(tmp_path)
    home = tmp_path / "home"

    set_launch_at_login_enabled(root, True, platform="linux", home_dir=home)

    desktop_path = home / ".config" / "autostart" / LINUX_AUTOSTART_FILENAME
    content = desktop_path.read_text(encoding="utf-8")
    start_script = shlex.quote(str(root / "scripts" / "start.sh"))
    assert "Type=Application" in content
    assert "Name=Sakura Desktop Pet" in content
    assert f"Exec=/bin/bash {start_script}" in content
    assert "X-GNOME-Autostart-enabled=true" in content

    set_launch_at_login_enabled(root, False, platform="linux", home_dir=home)

    assert not desktop_path.exists()


def test_windows_run_key_uses_packaged_runtime_python(tmp_path: Path) -> None:
    root = _runtime_root(tmp_path)
    runtime = root / "runtime"
    runtime.mkdir()
    pythonw = runtime / "pythonw.exe"
    pythonw.write_text("fake", encoding="utf-8")
    registry = FakeWinreg()

    set_launch_at_login_enabled(
        root,
        True,
        platform="win32",
        windows_registry=registry,
    )

    value = registry.values[WINDOWS_RUN_VALUE_NAME]
    assert "pythonw.exe" in value
    assert "main.py" in value

    set_launch_at_login_enabled(
        root,
        False,
        platform="win32",
        windows_registry=registry,
    )

    assert WINDOWS_RUN_VALUE_NAME not in registry.values


def test_windows_run_key_prefers_start_bat_when_available(tmp_path: Path) -> None:
    root = _runtime_root(tmp_path / "release build")
    start_bat = root / "start.bat"
    start_bat.write_text("@echo off\r\n", encoding="utf-8")
    registry = FakeWinreg()

    set_launch_at_login_enabled(
        root,
        True,
        platform="win32",
        windows_registry=registry,
    )

    value = registry.values[WINDOWS_RUN_VALUE_NAME]
    assert value == f'cmd.exe /c "{start_bat}"'


def test_resolve_launch_target_reports_unsupported_platform(tmp_path: Path) -> None:
    target = resolve_launch_at_login_target(tmp_path, platform="plan9")

    assert not target.supported
    assert target.platform == "unsupported"
    with pytest.raises(LaunchAtLoginError):
        set_launch_at_login_enabled(tmp_path, True, platform="plan9")


class FakeRegistryKey:
    def __enter__(self) -> "FakeRegistryKey":
        return self

    def __exit__(self, *_args: object) -> None:
        return None


class FakeWinreg:
    HKEY_CURRENT_USER = object()
    KEY_SET_VALUE = 0x0002
    REG_SZ = 1

    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    def OpenKey(self, *_args: object) -> FakeRegistryKey:
        return FakeRegistryKey()

    def SetValueEx(
        self,
        _key: FakeRegistryKey,
        name: str,
        _reserved: int,
        _kind: int,
        value: str,
    ) -> None:
        self.values[name] = value

    def DeleteValue(self, _key: FakeRegistryKey, name: str) -> None:
        try:
            del self.values[name]
        except KeyError as exc:
            raise FileNotFoundError(name) from exc


def _runtime_root(tmp_path: Path) -> Path:
    root = tmp_path / "Sakura"
    scripts = root / "scripts"
    scripts.mkdir(parents=True)
    start_script = scripts / "start.sh"
    start_script.write_text("#!/bin/bash\n", encoding="utf-8")
    start_script.chmod(0o755)
    (root / "main.py").write_text("print('sakura')\n", encoding="utf-8")
    return root.resolve()
