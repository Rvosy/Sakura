"""Sakura 生产桌面入口：启动唯一的 Tauri 主程序。"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent
TAURI_BINARY_ENV = "SAKURA_DESKTOP_BIN"


def resolve_tauri_executable(base_dir: Path) -> Path | None:
    """解析已安装或本地构建的 Tauri 主程序，不自动回退到 Qt。"""

    configured = os.environ.get(TAURI_BINARY_ENV, "").strip()
    if configured:
        path = Path(configured).expanduser().resolve()
        if not path.is_file():
            raise FileNotFoundError(f"SAKURA_DESKTOP_BIN 指向的文件不存在：{path}")
        return path

    binary_name = "sakura-desktop.exe" if sys.platform == "win32" else "sakura-desktop"
    candidates = (
        Path(base_dir) / binary_name,
        Path(base_dir) / "desktop" / "src-tauri" / "target" / "release" / binary_name,
        Path(base_dir) / "desktop" / "src-tauri" / "target" / "debug" / binary_name,
    )
    return next((path.resolve() for path in candidates if path.is_file()), None)


def main() -> int:
    try:
        executable = resolve_tauri_executable(BASE_DIR)
    except FileNotFoundError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    if executable is None:
        print(
            "未找到 Sakura Tauri 主程序。请先运行：\n"
            "cargo build --manifest-path desktop/src-tauri/Cargo.toml",
            file=sys.stderr,
        )
        return 1

    environment = os.environ.copy()
    environment["SAKURA_BASE_DIR"] = str(BASE_DIR)
    environment["SAKURA_PYTHON_EXE"] = sys.executable
    environment.setdefault("PYTHONIOENCODING", "utf-8")
    result = subprocess.run(
        [str(executable)],
        cwd=BASE_DIR,
        env=environment,
        check=False,
    )
    return int(result.returncode)


if __name__ == "__main__":
    raise SystemExit(main())
