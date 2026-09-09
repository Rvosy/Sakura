"""Download and verify one pinned Runtime v2 Python source archive.

This is CI/build tooling. The Sakura application never imports it and the
RuntimeLocator never downloads or repairs a Runtime at application startup.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import sys
import tarfile
import time
import urllib.request
import zipfile


CHUNK_SIZE = 1024 * 1024
MAX_DOWNLOAD_ATTEMPTS = 6
DOWNLOAD_RETRY_DELAY_SECONDS = 5


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
    required = {"fileName", "url", "size"}
    missing_fields = required.difference(archive)
    if missing_fields:
        missing = ", ".join(sorted(missing_fields))
        raise ArchiveVerificationError(f"runtime manifest archive is missing: {missing}")
    url = archive["url"]
    size = archive["size"]
    if (
        not isinstance(url, str)
        or not url.startswith("https://")
        or not isinstance(archive["fileName"], str)
        or not archive["fileName"]
        or not isinstance(size, int)
        or isinstance(size, bool)
        or size <= 0
    ):
        raise ArchiveVerificationError("runtime archive identity is invalid")
    return archive


def verify_archive(archive: dict[str, object], path: Path) -> int:
    try:
        size = path.stat().st_size
    except OSError as exc:
        raise ArchiveVerificationError(f"cannot read runtime archive: {exc}") from exc
    if size != archive["size"]:
        raise ArchiveVerificationError(
            f"runtime archive size mismatch: expected {archive['size']}, got {size}"
        )
    try:
        if str(archive["fileName"]).endswith((".zip", ".whl")):
            with zipfile.ZipFile(path) as bundle:
                if not bundle.infolist():
                    raise ArchiveVerificationError("runtime archive is empty")
        else:
            with tarfile.open(path, "r:*") as bundle:
                if not bundle.getmembers():
                    raise ArchiveVerificationError("runtime archive is empty")
    except (OSError, EOFError, zipfile.BadZipFile, tarfile.TarError) as exc:
        raise ArchiveVerificationError(f"cannot parse runtime archive: {exc}") from exc
    return size


def download_and_verify(
    manifest_path: Path,
    output_path: Path,
    selector: str = "archive",
) -> int:
    archive = load_archive_manifest(manifest_path, selector)
    if output_path.exists():
        return verify_archive(archive, output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = output_path.with_name(f".{output_path.name}.partial-{os.getpid()}")
    if temporary.exists():
        raise ArchiveVerificationError("archive temporary output already exists")
    for attempt in range(1, MAX_DOWNLOAD_ATTEMPTS + 1):
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
            if attempt >= MAX_DOWNLOAD_ATTEMPTS:
                raise ArchiveVerificationError(
                    f"runtime archive download failed after {attempt} attempts: {exc}"
                ) from exc
            print(
                f"runtime archive download attempt {attempt} failed; retrying in "
                f"{DOWNLOAD_RETRY_DELAY_SECONDS}s: {exc}",
                file=sys.stderr,
            )
            time.sleep(DOWNLOAD_RETRY_DELAY_SECONDS)

    raise AssertionError("runtime archive download loop exited unexpectedly")


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
        size = download_and_verify(args.manifest, args.output, args.selector)
    except ArchiveVerificationError as exc:
        print(f"runtime archive verification failed: {exc}", file=sys.stderr)
        return 2
    print(json.dumps({"size": size}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
