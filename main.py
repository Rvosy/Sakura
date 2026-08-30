"""Runtime v2 development launcher.

The launcher hands off to the built Tauri shell without keeping a resident
Python lifecycle root. ``start.bat`` launches that executable directly.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent
TAURI_BINARY_STEM = "sakura"


def tauri_binary_name(platform: str | None = None) -> str:
    if (platform or sys.platform) == "win32":
        return f"{TAURI_BINARY_STEM}.exe"
    return TAURI_BINARY_STEM


def resolve_tauri_binary(base_dir: Path = BASE_DIR) -> Path | None:
    binary_name = tauri_binary_name()
    candidate = base_dir / "desktop" / "src-tauri" / "target" / "debug" / binary_name
    return candidate if candidate.is_file() else None


def main() -> int:
    executable = resolve_tauri_binary()
    if executable is None:
        sys.stderr.write(
            "[Sakura Runtime v2] 未找到开发版 Tauri Shell。请先运行 "
            "cargo build --manifest-path desktop/src-tauri/Cargo.toml --locked。\n"
        )
        return 1
    if sys.platform == "darwin":
        start_script = BASE_DIR / "scripts" / "start.sh"
        os.execv(
            "/bin/bash",
            ["/bin/bash", str(start_script), *sys.argv[1:]],
        )
        return 1
    os.execv(str(executable), [str(executable), *sys.argv[1:]])
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
