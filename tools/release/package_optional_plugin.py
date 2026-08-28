#!/usr/bin/env python3
"""Build one installable optional-plugin ZIP without adding it to the app staging."""

from __future__ import annotations

import argparse
import stat
import zipfile
from pathlib import Path


IGNORED_NAMES = {"__pycache__", ".DS_Store", ".pytest_cache", ".mypy_cache", ".ruff_cache"}


def build(source: Path, output: Path) -> None:
    source = source.resolve(strict=True)
    if not source.is_dir() or not (source / "plugin.yaml").is_file():
        raise ValueError("OPTIONAL_PLUGIN_SOURCE_INVALID")
    manifest = (source / "plugin.yaml").read_text(encoding="utf-8")
    if "api: 4" not in manifest.splitlines():
        raise ValueError("OPTIONAL_PLUGIN_API_INVALID")
    if output.exists():
        raise ValueError("OPTIONAL_PLUGIN_OUTPUT_EXISTS")
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "x", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(source.rglob("*")):
            relative = path.relative_to(source)
            if any(part in IGNORED_NAMES for part in relative.parts):
                continue
            if path.is_symlink():
                raise ValueError("OPTIONAL_PLUGIN_SYMLINK_FORBIDDEN")
            if path.is_dir():
                continue
            if not path.is_file():
                raise ValueError("OPTIONAL_PLUGIN_FILE_TYPE_INVALID")
            info = zipfile.ZipInfo(
                (Path(source.name) / relative).as_posix(),
                date_time=(2026, 1, 1, 0, 0, 0),
            )
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = (stat.S_IFREG | 0o644) << 16
            archive.writestr(info, path.read_bytes())


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    build(args.source, args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
