#!/usr/bin/env python3
"""Install a hash-locked platform dependency set into a bundled Python."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

from tools.release.stage_distribution import python_executable, site_packages, write_windows_pth


def prepare(python_root: Path, target: str, lock: Path) -> None:
    python_root = python_root.resolve()
    lock = lock.resolve(strict=True)
    executable = python_executable(python_root, target)
    if not executable.is_file():
        raise ValueError(f"BUNDLED_PYTHON_MISSING: {executable}")
    if target == "windows-x64":
        write_windows_pth(python_root)
    site_packages(python_root, target).mkdir(parents=True, exist_ok=True)
    environment = os.environ.copy()
    environment.update(
        {
            "PIP_DISABLE_PIP_VERSION_CHECK": "1",
            "PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD": "1",
            "PYTHONNOUSERSITE": "1",
        }
    )
    subprocess.run(
        [
            sys.executable,
            "-m",
            "pip",
            "--python",
            str(executable),
            "install",
            "--require-hashes",
            "--only-binary=:all:",
            "--requirement",
            str(lock),
        ],
        check=True,
        env=environment,
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--target", required=True, choices=("windows-x64", "macos-arm64"))
    parser.add_argument("--python-root", required=True, type=Path)
    parser.add_argument("--lock", required=True, type=Path)
    args = parser.parse_args()
    prepare(args.python_root, args.target, args.lock)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
