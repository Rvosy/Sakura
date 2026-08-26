#!/usr/bin/env python3
"""Write compressed and staged release size evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


def digest(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            hasher.update(block)
    return hasher.hexdigest()


def tree_size(path: Path) -> int:
    if path.is_file():
        return path.stat().st_size
    return sum(item.stat().st_size for item in path.rglob("*") if item.is_file())


def build_report(
    inventory_path: Path, artifacts: list[Path], installed_paths: list[Path] | None = None
) -> dict[str, object]:
    inventory = json.loads(inventory_path.read_text(encoding="utf-8"))
    top_level = inventory.get("topLevelBytes")
    if not isinstance(top_level, dict):
        raise ValueError("RELEASE_INVENTORY_INVALID")
    existing = [path.resolve(strict=True) for path in artifacts]
    installed = [path.resolve(strict=True) for path in (installed_paths or [])]
    largest = max(top_level.items(), key=lambda item: int(item[1]), default=("", 0))
    return {
        "schemaVersion": 1,
        "target": inventory.get("target"),
        "version": inventory.get("version"),
        "uncompressedBytes": inventory.get("uncompressedBytes"),
        "installedBytes": sum(tree_size(path) for path in installed)
        if installed
        else inventory.get("uncompressedBytes"),
        "largestTopLevelDirectory": {"name": largest[0], "bytes": largest[1]},
        "artifacts": [
            {"name": path.name, "bytes": path.stat().st_size, "sha256": digest(path)}
            for path in sorted(existing, key=lambda item: item.name)
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--inventory", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--artifact", action="append", default=[], type=Path)
    parser.add_argument("--installed-path", action="append", default=[], type=Path)
    args = parser.parse_args()
    report = build_report(args.inventory, args.artifact, args.installed_path)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
