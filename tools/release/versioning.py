#!/usr/bin/env python3
"""Keep every release version projection equal to the repository VERSION."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path


SEMVER = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$")


def repository_root() -> Path:
    return Path(__file__).resolve().parents[2]


def source_version(root: Path) -> str:
    version = (root / "VERSION").read_text(encoding="utf-8").strip().removeprefix("v")
    if not SEMVER.fullmatch(version):
        raise ValueError(f"VERSION is not valid SemVer: {version!r}")
    return version


def projected_versions(root: Path) -> dict[str, str]:
    tauri = json.loads((root / "desktop/src-tauri/tauri.conf.json").read_text(encoding="utf-8"))
    cargo_text = (root / "desktop/src-tauri/Cargo.toml").read_text(encoding="utf-8")
    cargo = re.search(r"(?ms)^\[package\].*?^version\s*=\s*\"([^\"]+)\"", cargo_text)
    if cargo is None:
        raise ValueError("Cargo package version is missing")
    lock_text = (root / "desktop/src-tauri/Cargo.lock").read_text(encoding="utf-8")
    lock = re.search(
        r'(?ms)^\[\[package\]\]\nname = "sakura-runtime-v2-shell"\nversion = "([^"]+)"',
        lock_text,
    )
    if lock is None:
        raise ValueError("Cargo lock package version is missing")
    projected = {"tauri": str(tauri.get("version", "")), "cargo": cargo.group(1), "cargo-lock": lock.group(1)}
    for target in ("windows-x64", "macos-arm64", "linux-x64"):
        manifest = json.loads(
            (root / f"desktop/src-tauri/runtime-layouts/{target}/runtime-manifest.json").read_text(encoding="utf-8")
        )
        projected[f"runtime-manifest:{target}"] = str(manifest.get("productVersion", ""))
    return projected


def sync_versions(root: Path, version: str) -> None:
    tauri_path = root / "desktop/src-tauri/tauri.conf.json"
    tauri = json.loads(tauri_path.read_text(encoding="utf-8"))
    tauri["version"] = version
    tauri_path.write_text(json.dumps(tauri, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    cargo_path = root / "desktop/src-tauri/Cargo.toml"
    cargo_text = cargo_path.read_text(encoding="utf-8")
    updated, count = re.subn(
        r"(?ms)(^\[package\].*?^version\s*=\s*)\"[^\"]+\"",
        rf'\g<1>"{version}"',
        cargo_text,
        count=1,
    )
    if count != 1:
        raise ValueError("Cargo package version is missing")
    cargo_path.write_text(updated, encoding="utf-8")

    lock_path = root / "desktop/src-tauri/Cargo.lock"
    lock_text = lock_path.read_text(encoding="utf-8")
    lock_text, count = re.subn(
        r'(?ms)(^\[\[package\]\]\nname = "sakura-runtime-v2-shell"\nversion = )"[^"]+"',
        rf'\g<1>"{version}"',
        lock_text,
        count=1,
    )
    if count != 1:
        raise ValueError("Cargo lock package version is missing")
    lock_path.write_text(lock_text, encoding="utf-8")

    for target in ("windows-x64", "macos-arm64", "linux-x64"):
        manifest_path = root / f"desktop/src-tauri/runtime-layouts/{target}/runtime-manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["productVersion"] = version
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--write", action="store_true", help="project VERSION into Tauri and Cargo")
    args = parser.parse_args()
    root = repository_root()
    version = source_version(root)
    if args.write:
        sync_versions(root, version)
    drift = {name: value for name, value in projected_versions(root).items() if value != version}
    if drift:
        details = ", ".join(f"{name}={value}" for name, value in drift.items())
        raise SystemExit(f"VERSION_DRIFT: VERSION={version}, {details}")
    print(version)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
