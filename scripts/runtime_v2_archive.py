"""Download and verify one pinned Runtime v2 Python source archive.

This is CI/build tooling. The Sakura application never imports it and the
RuntimeLocator never downloads or repairs a Runtime at application startup.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import urllib.request


CHUNK_SIZE = 1024 * 1024


class ArchiveVerificationError(RuntimeError):
    pass


def load_archive_manifest(
    path: Path, selector: str = "archive"
) -> dict[str, object]:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
        archive = document[selector]
    except (OSError, json.JSONDecodeError, KeyError, TypeError) as exc:
        raise ArchiveVerificationError(f"invalid runtime manifest: {exc}") from exc
    if not isinstance(archive, dict):
        raise ArchiveVerificationError("runtime manifest archive must be an object")
    required = {"fileName", "url", "size", "sha256"}
    missing_fields = required.difference(archive)
    if missing_fields:
        missing = ", ".join(sorted(missing_fields))
        raise ArchiveVerificationError(f"runtime manifest archive is missing: {missing}")
    url = archive["url"]
    sha256 = archive["sha256"]
    size = archive["size"]
    if (
        not isinstance(url, str)
        or not url.startswith("https://")
        or not isinstance(sha256, str)
        or len(sha256) != 64
        or sha256 != sha256.lower()
        or any(char not in "0123456789abcdef" for char in sha256)
        or not isinstance(size, int)
        or isinstance(size, bool)
        or size <= 0
    ):
        raise ArchiveVerificationError("runtime archive identity is invalid")
    return archive


def verify_archive(archive: dict[str, object], path: Path) -> tuple[int, str]:
    digest = hashlib.sha256()
    size = 0
    try:
        with path.open("rb") as stream:
            while chunk := stream.read(CHUNK_SIZE):
                size += len(chunk)
                digest.update(chunk)
    except OSError as exc:
        raise ArchiveVerificationError(f"cannot read runtime archive: {exc}") from exc
    actual_hash = digest.hexdigest()
    if size != archive["size"]:
        raise ArchiveVerificationError(
            f"runtime archive size mismatch: expected {archive['size']}, got {size}"
        )
    if actual_hash != archive["sha256"]:
        raise ArchiveVerificationError(
            "runtime archive SHA-256 mismatch: "
            f"expected {archive['sha256']}, got {actual_hash}"
        )
    return size, actual_hash


def download_and_verify(
    manifest_path: Path,
    output_path: Path,
    selector: str = "archive",
) -> tuple[int, str]:
    archive = load_archive_manifest(manifest_path, selector)
    if output_path.exists():
        return verify_archive(archive, output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = output_path.with_name(f".{output_path.name}.partial-{os.getpid()}")
    if temporary.exists():
        raise ArchiveVerificationError("archive temporary output already exists")
    try:
        request = urllib.request.Request(
            str(archive["url"]), headers={"User-Agent": "Sakura-Runtime-v2-CI"}
        )
        with urllib.request.urlopen(request, timeout=60) as response, temporary.open(
            "xb"
        ) as destination:
            shutil.copyfileobj(response, destination, CHUNK_SIZE)
        result = verify_archive(archive, temporary)
        temporary.replace(output_path)
        return result
    except ArchiveVerificationError:
        temporary.unlink(missing_ok=True)
        raise
    except Exception as exc:
        temporary.unlink(missing_ok=True)
        raise ArchiveVerificationError(f"runtime archive download failed: {exc}") from exc


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--selector", choices=("archive", "assistantDependency"), default="archive"
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    try:
        size, sha256 = download_and_verify(args.manifest, args.output, args.selector)
    except ArchiveVerificationError as exc:
        print(f"runtime archive verification failed: {exc}", file=sys.stderr)
        return 2
    print(json.dumps({"size": size, "sha256": sha256}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
