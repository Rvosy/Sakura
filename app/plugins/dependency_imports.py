"""插件私有依赖的导入路径与 Windows DLL 搜索目录。"""

import os
from pathlib import Path


def dependency_import_paths(dependency_root: Path, *, windows: bool) -> list[Path]:
    """显式加入 pywin32 的目录，不执行依赖包中的 .pth 文件。"""
    paths = [dependency_root]
    if windows:
        paths.extend(
            path for path in (
                dependency_root / "win32",
                dependency_root / "win32/lib",
                dependency_root / "pythonwin",
            ) if path.is_dir()
        )
    return paths


def dependency_dll_handles(dependency_root: Path) -> list:
    """调用方保留句柄，使 DLL 搜索目录在使用依赖期间有效。"""
    directory = dependency_root / "pywin32_system32"
    if os.name == "nt" and directory.is_dir():
        return [os.add_dll_directory(str(directory))]
    return []
