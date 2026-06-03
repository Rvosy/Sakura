from __future__ import annotations

import os
import platform
import sys
from pathlib import Path


_MACHO_MAGICS = {
    b"\xfe\xed\xfa\xce",
    b"\xce\xfa\xed\xfe",
    b"\xfe\xed\xfa\xcf",
    b"\xcf\xfa\xed\xfe",
    b"\xca\xfe\xba\xbe",
    b"\xca\xfe\xba\xbf",
}


def current_system_name() -> str:
    if sys.platform == "win32":
        return "windows"
    if sys.platform == "darwin":
        return "macos"
    if sys.platform.startswith("linux"):
        return "linux"
    return sys.platform.lower()


def current_platform_label() -> str:
    system = {
        "windows": "Windows",
        "macos": "macOS",
        "linux": "Linux",
    }.get(current_system_name(), platform.system() or sys.platform)
    machine = platform.machine().strip()
    return f"{system} {machine}".strip()


def executable_system_name(path: Path) -> str | None:
    try:
        with path.open("rb") as file:
            header = file.read(4)
    except OSError:
        return None
    if header.startswith(b"MZ"):
        return "windows"
    if header == b"\x7fELF":
        return "linux"
    if header in _MACHO_MAGICS:
        return "macos"
    return None


def is_executable_for_current_system(path: Path) -> bool:
    executable_system = executable_system_name(path)
    if executable_system is None:
        return True
    return executable_system == current_system_name()


def has_runtime_execute_permission(path: Path) -> bool:
    if sys.platform == "win32":
        return True
    return os.access(path, os.X_OK)


def runtime_python_candidates(runtime_dir: Path) -> tuple[Path, ...]:
    if sys.platform == "win32":
        relative_names = ("python.exe", "python")
    else:
        relative_names = ("bin/python3", "bin/python", "python3", "python", "python.exe")
    candidates: list[Path] = []
    for relative_name in relative_names:
        candidate = runtime_dir / relative_name
        if candidate not in candidates:
            candidates.append(candidate)
    return tuple(candidates)


def find_usable_runtime_python(runtime_dir: Path) -> Path | None:
    for candidate in runtime_python_candidates(runtime_dir):
        if (
            candidate.is_file()
            and is_executable_for_current_system(candidate)
            and has_runtime_execute_permission(candidate)
        ):
            return candidate
    return None


def format_runtime_python_issue(runtime_dir: Path) -> str:
    for candidate in runtime_python_candidates(runtime_dir):
        if not candidate.is_file():
            continue
        executable_system = executable_system_name(candidate)
        if executable_system is not None and executable_system != current_system_name():
            expected = {
                "windows": "Windows",
                "macos": "macOS",
                "linux": "Linux",
            }.get(executable_system, executable_system)
            return (
                f"检测到 {expected} Python 运行时：{candidate}；"
                f"当前系统是 {current_platform_label()}。"
                "请安装适配当前系统的 TTS 运行环境，或改用已在运行的外部 TTS 服务。"
            )
        if not has_runtime_execute_permission(candidate):
            return f"Python 运行时没有执行权限：{candidate}"
    return f"未找到当前系统可执行的 Python 运行时（查找目录：{runtime_dir}）。"
