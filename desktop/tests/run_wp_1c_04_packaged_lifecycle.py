"""Run the packaged Core lifecycle while proving its resource tree is read-only."""

from __future__ import annotations

import argparse
import hashlib
import os
from pathlib import Path
import subprocess


def tree_manifest(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*"), key=lambda item: item.as_posix()):
        relative = path.relative_to(root).as_posix()
        if path.is_symlink():
            digest.update(f"L {relative} {os.readlink(path)}\n".encode())
        elif path.is_dir():
            digest.update(f"D {relative}\n".encode())
        elif path.is_file():
            digest.update(f"F {relative} {path.stat().st_size}\n".encode())
            with path.open("rb") as stream:
                for block in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(block)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--resources", type=Path, required=True)
    args = parser.parse_args()
    resources = args.resources.resolve(strict=True)
    if not resources.name.startswith("sakura-wp-1c-04-"):
        raise ValueError("packaged lifecycle resources have an unexpected identity")

    before = tree_manifest(resources)
    command = [
        "cargo",
        "test",
        "--manifest-path",
        "src-tauri/Cargo.toml",
        "--locked",
        "core_host_runtime::tests::staged_packaged_runtime_runs_lifecycle_faults_and_clean_generations",
        "--",
        "--ignored",
        "--exact",
        "--nocapture",
        "--test-threads=1",
    ]
    completed = subprocess.run(command, check=False, timeout=220)
    if completed.returncode != 0:
        return completed.returncode
    after = tree_manifest(resources)
    if before != after:
        raise RuntimeError("packaged Runtime/Core resources changed during lifecycle acceptance")
    print(f"packaged-core-lifecycle=passed resource-manifest={before}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
