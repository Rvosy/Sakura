#!/usr/bin/env python3
"""Stage the pinned development Python Runtime from the compiled platform manifest."""

from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import stat
import sys
import tarfile
import zipfile
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.runtime_v2_archive import ArchiveVerificationError, download_and_verify


class BootstrapRuntimeError(RuntimeError):
    pass


def current_target() -> str:
    system = platform.system()
    machine = platform.machine().lower()
    if system == "Linux" and machine in {"x86_64", "amd64"}:
        return "linux-x64"
    if system == "Darwin" and machine in {"arm64", "aarch64"}:
        return "macos-arm64"
    if system == "Windows" and machine in {"amd64", "x86_64"}:
        return "windows-x64"
    raise BootstrapRuntimeError(f"BOOTSTRAP_RUNTIME_UNSUPPORTED: {system} {machine}")


def runtime_python(runtime_root: Path, target: str) -> Path:
    if target == "windows-x64":
        return runtime_root / "python.exe"
    return runtime_root / "bin" / "python3"


def load_manifest(path: Path, expected_target: str) -> dict[str, object]:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise BootstrapRuntimeError(f"invalid runtime manifest: {exc}") from exc
    if document.get("target") != expected_target:
        raise BootstrapRuntimeError(
            f"runtime manifest target {document.get('target')!r} does not match {expected_target}"
        )
    archive = document.get("archive")
    if not isinstance(archive, dict):
        raise BootstrapRuntimeError("runtime manifest archive must be an object")
    return document


def extract_archive(archive: Path, destination: Path, strip_components: int) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    if archive.name.endswith(".zip"):
        with zipfile.ZipFile(archive) as bundle:
            bundle.extractall(destination)
        return
    with tarfile.open(archive, "r:*") as bundle:
        members = []
        for member in bundle.getmembers():
            parts = Path(member.name).parts
            if strip_components:
                if len(parts) <= strip_components:
                    continue
                member.name = str(Path(*parts[strip_components:]))
            if member.name in {"", ".", ".."} or Path(member.name).is_absolute():
                raise BootstrapRuntimeError(f"unsafe archive member: {member.name}")
            if Path(member.name).parts and Path(member.name).parts[0] == "..":
                raise BootstrapRuntimeError(f"unsafe archive member: {member.name}")
            members.append(member)
        kwargs: dict[str, object] = {}
        if sys.version_info >= (3, 12):
            kwargs["filter"] = "data"
        bundle.extractall(destination, members=members, **kwargs)


def stage_runtime(
    *,
    repo_root: Path,
    target: str,
    archive_cache: Path | None = None,
) -> Path:
    runtime_root = repo_root / "runtime"
    python = runtime_python(runtime_root, target)
    if python.is_file():
        return python

    manifest_path = (
        repo_root / "desktop" / "src-tauri" / "runtime-layouts" / target / "runtime-manifest.json"
    )
    document = load_manifest(manifest_path, target)
    archive_meta = document["archive"]
    assert isinstance(archive_meta, dict)
    file_name = archive_meta["fileName"]
    strip_components = int(archive_meta.get("stripComponents") or 0)
    if not isinstance(file_name, str) or not file_name:
        raise BootstrapRuntimeError("runtime archive fileName is invalid")

    cache = archive_cache or (repo_root / "temp" / "runtime-archives")
    cache.mkdir(parents=True, exist_ok=True)
    archive_path = cache / file_name
    download_and_verify(manifest_path, archive_path)

    staging = repo_root / f".runtime-bootstrap-{os.getpid()}"
    if staging.exists():
        shutil.rmtree(staging)
    try:
        extract_archive(archive_path, staging, strip_components)
        staged_python = runtime_python(staging, target)
        if not staged_python.is_file():
            raise BootstrapRuntimeError(f"BOOTSTRAP_RUNTIME_MISSING_PYTHON: {staged_python}")
        mode = staged_python.stat().st_mode
        staged_python.chmod(mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        if runtime_root.exists():
            shutil.rmtree(runtime_root)
        staging.rename(runtime_root)
    finally:
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)
    return runtime_python(runtime_root, target)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, default=REPO_ROOT)
    parser.add_argument(
        "--target",
        choices=("windows-x64", "macos-arm64", "linux-x64"),
    )
    args = parser.parse_args()
    try:
        target = args.target or current_target()
        python = stage_runtime(repo_root=args.repo.resolve(), target=target)
    except (ArchiveVerificationError, BootstrapRuntimeError) as exc:
        print(f"[错误] 无法准备 Python Runtime：{exc}", file=sys.stderr)
        return 1
    print(f"[OK] staged {python}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
