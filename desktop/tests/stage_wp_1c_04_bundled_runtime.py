"""Stage a verified native Runtime archive in the frozen packaged layout."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil


def _relative(path: Path) -> Path:
    if path.is_absolute() or not path.parts or ".." in path.parts:
        raise ValueError(f"unsafe manifest path: {path}")
    return path


def stage(repo: Path, target: str, development_runtime: Path, resources: Path) -> Path:
    repo = repo.resolve(strict=True)
    development_runtime = development_runtime.resolve(strict=True)
    resources = resources.resolve(strict=False)
    if (
        not resources.name.startswith("sakura-wp-1c-04-")
        or resources in {repo, development_runtime}
        or repo in resources.parents
        or resources in repo.parents
    ):
        raise ValueError("packaged resources must be an isolated directory outside the repository")

    manifest_path = (
        repo
        / "desktop"
        / "src-tauri"
        / "runtime-layouts"
        / target
        / "runtime-manifest.json"
    )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("schemaVersion") != 1 or manifest.get("target") != target:
        raise ValueError("Runtime manifest identity is invalid")

    development_python = _relative(Path(manifest["developmentPythonRelativePath"]))
    packaged_python = _relative(Path(manifest["packagedPythonRelativePath"]))
    if development_python.parts[0] != "runtime" or len(development_python.parts) < 2:
        raise ValueError("development Python layout is not rooted in the controlled Runtime")
    runtime_python_suffix = Path(*development_python.parts[1:])
    if not (development_runtime / runtime_python_suffix).is_file():
        raise ValueError("development Runtime does not contain the manifest Python executable")
    suffix_parts = runtime_python_suffix.parts
    if packaged_python.parts[-len(suffix_parts) :] != suffix_parts:
        raise ValueError("development and packaged Python layouts do not share an archive suffix")
    packaged_runtime_prefix = Path(*packaged_python.parts[: -len(suffix_parts)])
    if not packaged_runtime_prefix.parts:
        raise ValueError("packaged Python layout has no controlled Runtime directory")

    runtime_root = resources / "runtime-v2" / target
    if resources.exists():
        shutil.rmtree(resources)
    runtime_root.mkdir(parents=True)
    try:
        shutil.copytree(development_runtime, runtime_root / packaged_runtime_prefix)

        packaged_resource_root = runtime_root / _relative(
            Path(manifest["packagedApplicationRootRelativePath"])
        )
        core_source = repo / "app" / "core_host"
        shutil.copytree(
            core_source,
            packaged_resource_root / "app" / "core_host",
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo"),
        )
        shutil.copy2(manifest_path, runtime_root / "runtime-manifest.json")

        expected_python = runtime_root / packaged_python
        expected_entry = runtime_root / _relative(
            Path(manifest["packagedCoreEntryRelativePath"])
        )
        if not expected_python.is_file() or not expected_entry.is_file():
            raise RuntimeError("staged packaged Runtime is incomplete")
    except BaseException:
        shutil.rmtree(resources, ignore_errors=True)
        raise
    return runtime_root


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--target", required=True)
    parser.add_argument("--development-runtime", type=Path, required=True)
    parser.add_argument("--resources", type=Path, required=True)
    args = parser.parse_args()
    runtime_root = stage(args.repo, args.target, args.development_runtime, args.resources)
    print(f"packaged-runtime={runtime_root}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
