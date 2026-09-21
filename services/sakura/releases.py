#!/usr/bin/env python3
"""Validate and publish the bounded Sakura Service release documents.

The CLI builds CI draft input; only the private console calls publish().
Package bytes and updater signatures remain owned by the existing release build.
"""

from __future__ import annotations

import argparse
from datetime import datetime
import json
import os
from pathlib import Path
import re
import sys
import tempfile


SERVICE_ENDPOINT = "https://api.sakura.cialloo.cn/service/v1/latest.json"
LEGACY_SERVICE_ENDPOINT = "https://sakura.cialloo.cn/service/v1/latest.json"
GITHUB_ENDPOINT = "https://github.com/Rvosy/Sakura/releases/latest/download/latest.json"
MAX_PAYLOAD_BYTES = 128 * 1024
_VERSION = re.compile(r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)", re.ASCII)


def version_key(value: object) -> tuple[int, int, int]:
    if not isinstance(value, str) or len(value) > 64 or not _VERSION.fullmatch(value):
        raise ValueError("SERVICE_VERSION_INVALID")
    return tuple(int(part) for part in value.split("."))


def _fields(value: object, expected: set[str]) -> dict:
    if not isinstance(value, dict) or set(value) != expected:
        raise ValueError("SERVICE_FIELDS_INVALID")
    return value


def _date(value: object) -> None:
    if not isinstance(value, str) or len(value) > 64:
        raise ValueError("SERVICE_DATE_INVALID")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError("SERVICE_DATE_INVALID") from error
    if "T" not in value or parsed.tzinfo is None:
        raise ValueError("SERVICE_DATE_INVALID")


def validate_release(value: object) -> dict:
    release = _fields(value, {
        "schema", "latest", "minimumSupported", "releaseUrl", "publishedAt",
        "urgent", "downloads", "updaterManifestUrl",
    })
    if type(release["schema"]) is not int or release["schema"] != 1:
        raise ValueError("SERVICE_SCHEMA_INVALID")
    current = version_key(release["latest"])
    minimum = release["minimumSupported"]
    if minimum is not None and version_key(minimum) > current:
        raise ValueError("SERVICE_MINIMUM_VERSION_INVALID")
    if type(release["urgent"]) is not bool:
        raise ValueError("SERVICE_URGENT_INVALID")
    _date(release["publishedAt"])
    version = release["latest"]
    if release["releaseUrl"] != f"https://github.com/Rvosy/Sakura/releases/tag/v{version}":
        raise ValueError("SERVICE_RELEASE_URL_INVALID")
    downloads = _fields(release["downloads"], {
        "windowsX64Setup", "windowsX64Portable", "macosArm64Dmg",
    })
    for key, suffix in {
        "windowsX64Setup": "windows-x64-setup.exe",
        "windowsX64Portable": "windows-x64-portable.zip",
        "macosArm64Dmg": "macos-arm64.dmg",
    }.items():
        if downloads[key] != asset_url(version, suffix):
            raise ValueError("SERVICE_ASSET_URL_INVALID")
    if release["updaterManifestUrl"] not in (SERVICE_ENDPOINT, LEGACY_SERVICE_ENDPOINT, GITHUB_ENDPOINT):
        raise ValueError("SERVICE_UPDATER_URL_INVALID")
    return release


def asset_url(version: str, suffix: str) -> str:
    return f"https://github.com/Rvosy/Sakura/releases/download/v{version}/Sakura-{version}-{suffix}"


def validate_updater(value: object, version: str) -> dict:
    updater = _fields(value, {"version", "notes", "pub_date", "platforms", "portable"})
    if updater["version"] != version:
        raise ValueError("SERVICE_UPDATER_VERSION_MISMATCH")
    if not isinstance(updater["notes"], str) or len(updater["notes"]) > 16000:
        raise ValueError("SERVICE_NOTES_INVALID")
    _date(updater["pub_date"])
    platforms = _fields(updater["platforms"], {"windows-x86_64", "darwin-aarch64"})
    for key, suffix in {
        "windows-x86_64": "windows-x64-setup.exe",
        "darwin-aarch64": "macos-arm64.app.tar.gz",
    }.items():
        artifact = _fields(platforms[key], {"url", "signature"})
        signature = artifact["signature"]
        if not isinstance(signature, str) or not signature.strip() or len(signature) > 8192:
            raise ValueError("SERVICE_SIGNATURE_MISSING")
        if artifact["url"] != asset_url(version, suffix):
            raise ValueError("SERVICE_ASSET_URL_INVALID")
    portable = _fields(updater["portable"], {"windows-x86_64"})
    entry = _fields(portable["windows-x86_64"], {"url"})
    if entry["url"] != asset_url(version, "windows-x64-portable.zip"):
        raise ValueError("SERVICE_ASSET_URL_INVALID")
    return updater


def _unique_object(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("SERVICE_DUPLICATE_FIELD")
        result[key] = value
    return result


def decode(raw: bytes) -> dict:
    if len(raw) > MAX_PAYLOAD_BYTES:
        raise ValueError("SERVICE_PAYLOAD_TOO_LARGE")
    value = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_object)
    if not isinstance(value, dict):
        raise ValueError("SERVICE_FIELDS_INVALID")
    return value


def build_payload(release: object, updater: object) -> dict:
    metadata = dict(validate_release(release))
    manifest = validate_updater(updater, metadata["latest"])
    metadata["updaterManifestUrl"] = SERVICE_ENDPOINT
    return {"schema": 1, "release": metadata, "updater": manifest}


def _atomic_write(path: Path, value: dict) -> None:
    raw = (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as output:
            output.write(raw)
            output.flush()
            os.fsync(output.fileno())
        os.chmod(temporary, 0o644)
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def publish(raw: bytes, root: Path) -> None:
    """Called under the deploy process lock; validate everything before any write."""
    payload = decode(raw)
    manifest = None
    if "release" in payload:
        _fields(payload, {"schema", "release", "updater"})
        if type(payload["schema"]) is not int or payload["schema"] != 1:
            raise ValueError("SERVICE_SCHEMA_INVALID")
        metadata = validate_release(payload["release"])
        manifest = validate_updater(payload["updater"], metadata["latest"])
        if metadata["updaterManifestUrl"] not in (SERVICE_ENDPOINT, LEGACY_SERVICE_ENDPOINT):
            raise ValueError("SERVICE_UPDATER_URL_INVALID")
        metadata = {**metadata, "updaterManifestUrl": SERVICE_ENDPOINT}
    else:
        # Existing CI sends only releases.json. Keep its restricted write contract.
        metadata = validate_release(payload)
        if metadata["updaterManifestUrl"] != GITHUB_ENDPOINT:
            raise ValueError("SERVICE_UPDATER_MANIFEST_REQUIRED")
    for name, field in (("releases.json", "latest"), ("latest.json", "version")):
        path = root / name
        if path.exists():
            existing = decode(path.read_bytes())
            if version_key(metadata["latest"]) < version_key(existing.get(field)):
                raise ValueError("SERVICE_VERSION_DOWNGRADE")
    if manifest is not None:
        # Readers use each document independently. Publish the updater first so
        # releases.json never introduces a missing updater endpoint. A failed
        # second write can be repaired by publishing the same version again.
        _atomic_write(root / "latest.json", manifest)
    _atomic_write(root / "releases.json", metadata)


def main() -> int:
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    build = commands.add_parser("build")
    build.add_argument("--release", type=Path, required=True)
    build.add_argument("--updater", type=Path, required=True)
    build.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "build":
        payload = build_payload(decode(args.release.read_bytes()), decode(args.updater.read_bytes()))
        encoded = (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
        decode(encoded)
        args.output.write_bytes(encoded)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, OSError) as error:
        print(f"Service publication failed: {error}", file=sys.stderr)
        raise SystemExit(1)
