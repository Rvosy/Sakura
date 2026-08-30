#!/usr/bin/env python3
"""Create the Tauri v2 static updater manifest from signed artifacts."""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote


PLATFORM_KEYS = {
    "windows-x64": "windows-x86_64",
    "macos-arm64": "darwin-aarch64",
}


def platform_entry(artifact: Path, signature: Path, base_url: str) -> dict[str, str]:
    if not base_url.startswith("https://"):
        raise ValueError("UPDATER_BASE_URL_INVALID")
    value = signature.read_text(encoding="utf-8").strip()
    if not value:
        raise ValueError(f"UPDATER_SIGNATURE_MISSING: {signature}")
    return {
        "signature": value,
        "url": f"{base_url.rstrip('/')}/{quote(artifact.name)}",
    }


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build_manifest(
    *,
    version: str,
    notes: str,
    base_url: str,
    releases: list[tuple[str, Path, Path]],
    portable: Path | None,
    pub_date: str,
    require_all_platforms: bool = True,
) -> dict[str, object]:
    platforms: dict[str, object] = {}
    for target, artifact, signature in releases:
        key = PLATFORM_KEYS[target]
        if key in platforms:
            raise ValueError(f"UPDATER_PLATFORM_DUPLICATE: {key}")
        platforms[key] = platform_entry(artifact.resolve(strict=True), signature.resolve(strict=True), base_url)
    if not platforms:
        raise ValueError("UPDATER_PLATFORM_SET_EMPTY")
    if require_all_platforms and set(platforms) != set(PLATFORM_KEYS.values()):
        raise ValueError("UPDATER_PLATFORM_SET_INCOMPLETE")
    manifest: dict[str, object] = {
        "version": version,
        "notes": notes,
        "pub_date": pub_date,
        "platforms": platforms,
    }
    if portable is not None:
        portable = portable.resolve(strict=True)
        manifest["portable"] = {
            "windows-x86_64": {
                "url": f"{base_url.rstrip('/')}/{quote(portable.name)}",
                "sha256": sha256(portable),
            }
        }
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--version", required=True)
    parser.add_argument("--notes", default="Sakura Runtime v2 update")
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--portable", type=Path)
    parser.add_argument(
        "--allow-platform-subset",
        action="store_true",
        help="allow a platform-only test manifest; formal releases must omit this flag",
    )
    parser.add_argument("--release", action="append", nargs=3, metavar=("TARGET", "ARTIFACT", "SIGNATURE"))
    args = parser.parse_args()
    releases = [(target, Path(artifact), Path(signature)) for target, artifact, signature in args.release or []]
    manifest = build_manifest(
        version=args.version,
        notes=args.notes,
        base_url=args.base_url,
        releases=releases,
        portable=args.portable,
        pub_date=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        require_all_platforms=not args.allow_platform_subset,
    )
    args.output.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
