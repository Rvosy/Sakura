from __future__ import annotations

import os
import plistlib
import shlex
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.storage.paths import StoragePaths


LAUNCH_AT_LOGIN_LABEL = "com.rvosy.sakura.launch-at-login"
WINDOWS_RUN_VALUE_NAME = "Sakura Desktop Pet"
LINUX_AUTOSTART_FILENAME = "sakura-desktop-pet.desktop"


class LaunchAtLoginError(RuntimeError):
    """Raised when the platform integration cannot be updated."""


@dataclass(frozen=True)
class LaunchAtLoginTarget:
    platform: str
    command: tuple[str, ...]
    supported: bool
    reason: str = ""


def current_platform_key(platform: str | None = None) -> str:
    value = platform or sys.platform
    if value == "darwin":
        return "macos"
    if value.startswith("win"):
        return "windows"
    if value.startswith("linux"):
        return "linux"
    return "unsupported"


def is_launch_at_login_supported(platform: str | None = None) -> bool:
    return current_platform_key(platform) in {"macos", "windows", "linux"}


def launch_at_login_platform_text(platform: str | None = None) -> str:
    platform_key = current_platform_key(platform)
    return {
        "macos": "macOS",
        "windows": "Windows",
        "linux": "Linux",
    }.get(platform_key, "当前系统")


def resolve_launch_at_login_target(
    base_dir: Path,
    *,
    platform: str | None = None,
) -> LaunchAtLoginTarget:
    base_dir = base_dir.resolve()
    platform_key = current_platform_key(platform)
    if platform_key == "unsupported":
        return LaunchAtLoginTarget(
            platform=platform_key,
            command=(),
            supported=False,
            reason=f"Unsupported platform: {platform or sys.platform}",
        )
    return LaunchAtLoginTarget(
        platform=platform_key,
        command=tuple(_launch_command_for_platform(base_dir, platform_key)),
        supported=True,
    )


def set_launch_at_login_enabled(
    base_dir: Path,
    enabled: bool,
    *,
    platform: str | None = None,
    home_dir: Path | None = None,
    windows_registry: Any | None = None,
) -> None:
    target = resolve_launch_at_login_target(base_dir, platform=platform)
    if not target.supported:
        raise LaunchAtLoginError(target.reason or "Launch at login is not supported.")
    if target.platform == "macos":
        _set_macos_launch_agent_enabled(
            base_dir,
            target.command,
            enabled,
            home_dir=home_dir,
        )
        return
    if target.platform == "windows":
        _set_windows_run_enabled(
            target.command,
            enabled,
            registry_module=windows_registry,
        )
        return
    if target.platform == "linux":
        _set_linux_autostart_enabled(target.command, enabled, home_dir=home_dir)
        return
    raise LaunchAtLoginError(f"Unsupported platform: {target.platform}")


def ensure_launch_at_login_state(
    base_dir: Path,
    enabled: bool,
    *,
    platform: str | None = None,
) -> None:
    if enabled:
        set_launch_at_login_enabled(base_dir, True, platform=platform)


def _launch_command_for_platform(base_dir: Path, platform_key: str) -> list[str]:
    if getattr(sys, "frozen", False):
        return [sys.executable]
    if platform_key == "macos":
        start_script = base_dir / "scripts" / "start.sh"
        if start_script.exists():
            return ["/bin/bash", str(start_script)]
    if platform_key == "linux":
        start_script = base_dir / "scripts" / "start.sh"
        if start_script.exists():
            return ["/bin/bash", str(start_script)]
    if platform_key == "windows":
        start_script = base_dir / "start.bat"
        if start_script.exists():
            return ["cmd.exe", "/c", str(start_script)]
        python_exe = _windows_python_executable(base_dir)
        if python_exe is not None:
            return [str(python_exe), str(base_dir / "main.py")]
    return [sys.executable, str(base_dir / "main.py")]


def _windows_python_executable(base_dir: Path) -> Path | None:
    for relative in (
        Path("runtime") / "pythonw.exe",
        Path("runtime") / "python.exe",
        Path(".venv") / "Scripts" / "pythonw.exe",
        Path(".venv") / "Scripts" / "python.exe",
    ):
        candidate = base_dir / relative
        if candidate.exists():
            return candidate
    return None


def _set_macos_launch_agent_enabled(
    base_dir: Path,
    command: tuple[str, ...],
    enabled: bool,
    *,
    home_dir: Path | None = None,
) -> None:
    plist_path = _macos_launch_agent_path(home_dir=home_dir)
    if enabled:
        logs_dir = StoragePaths(base_dir).logs_dir
        logs_dir.mkdir(parents=True, exist_ok=True)
        plist_path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "Label": LAUNCH_AT_LOGIN_LABEL,
            "ProgramArguments": list(command),
            "RunAtLoad": True,
            "LimitLoadToSessionType": "Aqua",
            "StandardOutPath": str(logs_dir / "launch-at-login.out.log"),
            "StandardErrorPath": str(logs_dir / "launch-at-login.err.log"),
        }
        plist_path.write_bytes(plistlib.dumps(data, sort_keys=False))
        return
    _unlink_if_exists(plist_path)


def _macos_launch_agent_path(*, home_dir: Path | None = None) -> Path:
    home = home_dir or Path.home()
    return home / "Library" / "LaunchAgents" / f"{LAUNCH_AT_LOGIN_LABEL}.plist"


def _set_windows_run_enabled(
    command: tuple[str, ...],
    enabled: bool,
    *,
    registry_module: Any | None = None,
) -> None:
    winreg = registry_module or _import_winreg()
    key_path = r"Software\Microsoft\Windows\CurrentVersion\Run"
    access = getattr(winreg, "KEY_SET_VALUE", 0)
    create_key = getattr(winreg, "CreateKey", None)
    key_context = (
        create_key(winreg.HKEY_CURRENT_USER, key_path)
        if callable(create_key)
        else winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, access)
    )
    with key_context as key:
        if enabled:
            winreg.SetValueEx(
                key,
                WINDOWS_RUN_VALUE_NAME,
                0,
                winreg.REG_SZ,
                subprocess.list2cmdline(list(command)),
            )
            return
        try:
            winreg.DeleteValue(key, WINDOWS_RUN_VALUE_NAME)
        except FileNotFoundError:
            return


def _import_winreg() -> Any:
    if current_platform_key() != "windows":
        raise LaunchAtLoginError("Windows registry is only available on Windows.")
    import winreg  # type: ignore[import-not-found]

    return winreg


def _set_linux_autostart_enabled(
    command: tuple[str, ...],
    enabled: bool,
    *,
    home_dir: Path | None = None,
) -> None:
    desktop_path = _linux_autostart_path(home_dir=home_dir)
    if enabled:
        desktop_path.parent.mkdir(parents=True, exist_ok=True)
        desktop_path.write_text(_linux_desktop_entry(command), encoding="utf-8")
        return
    _unlink_if_exists(desktop_path)


def _linux_autostart_path(*, home_dir: Path | None = None) -> Path:
    config_home = os.environ.get("XDG_CONFIG_HOME")
    if config_home and home_dir is None:
        root = Path(config_home)
    else:
        root = (home_dir or Path.home()) / ".config"
    return root / "autostart" / LINUX_AUTOSTART_FILENAME


def _linux_desktop_entry(command: tuple[str, ...]) -> str:
    exec_line = " ".join(shlex.quote(part) for part in command)
    return "\n".join(
        (
            "[Desktop Entry]",
            "Type=Application",
            "Name=Sakura Desktop Pet",
            f"Exec={exec_line}",
            "Terminal=false",
            "X-GNOME-Autostart-enabled=true",
            "",
        )
    )


def _unlink_if_exists(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        return
