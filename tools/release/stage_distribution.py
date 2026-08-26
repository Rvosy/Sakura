#!/usr/bin/env python3
"""Assemble and validate the only distribution staging consumed by Tauri."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import stat
import subprocess
from pathlib import Path


TARGETS = {"windows-x64", "macos-arm64", "linux-x64"}
BUILTIN_PLUGINS = {
    "playwright_browser",
    "sakura_mem0",
    "sakura_mobile",
    "sakura_tts_hub",
    "sakura_genie",
    "sakura_gpt_sovits",
}
IMPORTS = (
    "yaml",
    "playwright",
    "openai",
    "pydantic",
    "qdrant_client",
    "sqlalchemy",
    "posthog",
    "pytz",
    "google.protobuf",
    "fastembed",
    "onnxruntime",
    "py7zz",
    "py7zr",
    "mcp",
)
FORBIDDEN_PARTS = {
    ".cache",
    ".local-browsers",
    "fastembed-cache",
    "hf-cache",
    "ms-playwright",
    "gpt-sovits",
    "gpt_sovits-v2",
    "all-minilm-l6-v2-onnx",
    "genie-runtime",
}
FORBIDDEN_SUFFIXES = {".ckpt", ".pth", ".safetensors"}
IGNORED_NAMES = {"__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", ".DS_Store"}


def copy_tree(source: Path, target: Path) -> None:
    shutil.copytree(
        source,
        target,
        ignore=shutil.ignore_patterns(
            "__pycache__",
            "*.pyc",
            "*.pyo",
            ".DS_Store",
            ".pytest_cache",
            ".mypy_cache",
            ".ruff_cache",
        ),
    )


def python_executable(python_root: Path, target: str) -> Path:
    return python_root / ("python.exe" if target == "windows-x64" else "bin/python3")


def site_packages(python_root: Path, target: str) -> Path:
    if target == "windows-x64":
        return python_root / "Lib/site-packages"
    return python_root / "lib/python3.12/site-packages"


def move_tools(python_root: Path, target: str) -> None:
    tools = python_root / "tools"
    tools.mkdir(parents=True, exist_ok=True)
    executable_suffix = ".exe" if target == "windows-x64" else ""
    search_roots = [python_root / "Scripts", python_root / "bin", python_root]
    names = [
        (f"uv{executable_suffix}", [root / f"uv{executable_suffix}" for root in search_roots]),
        (f"uvx{executable_suffix}", [root / f"uvx{executable_suffix}" for root in search_roots]),
        (
            f"7zz{executable_suffix}",
            [site_packages(python_root, target) / "py7zz" / "bin" / f"7zz{executable_suffix}"],
        ),
    ]
    for name, candidates in names:
        source = next((candidate for candidate in candidates if candidate.is_file()), None)
        if source is None:
            raise ValueError(f"STAGING_TOOL_MISSING: {name}")
        target_path = tools / name
        shutil.copy2(source, target_path)
        target_path.chmod(target_path.stat().st_mode | stat.S_IXUSR)
        if source != target_path:
            source.unlink()


def write_windows_pth(python_root: Path) -> None:
    pth = python_root / "python312._pth"
    pth.write_text("python312.zip\n.\nLib/site-packages\nimport site\n", encoding="utf-8", newline="\n")


def forbidden_paths(stage: Path) -> list[str]:
    failures: list[str] = []
    for path in stage.rglob("*"):
        relative = path.relative_to(stage)
        lowered = {part.lower() for part in relative.parts}
        suffix = path.suffix.lower()
        site_packages_pth = (
            path.is_file()
            and suffix == ".pth"
            and path.parent.name.lower() == "site-packages"
        )
        if lowered & FORBIDDEN_PARTS or (
            path.is_file()
            and suffix in FORBIDDEN_SUFFIXES
            and not site_packages_pth
        ):
            failures.append(relative.as_posix())
    return failures


def validate_layout(stage: Path, target: str, *, portable: bool) -> None:
    required = [
        stage / "VERSION",
        stage / "runtime-manifest.json",
        python_executable(stage / "python", target),
        site_packages(stage / "python", target),
        stage / "core/app/core_host/__main__.py",
        stage / "plugins/builtin/__init__.py",
        stage / "python/tools" / ("uv.exe" if target == "windows-x64" else "uv"),
        stage / "python/tools" / ("uvx.exe" if target == "windows-x64" else "uvx"),
        stage / "python/tools" / ("7zz.exe" if target == "windows-x64" else "7zz"),
    ]
    missing = [path.relative_to(stage).as_posix() for path in required if not path.exists()]
    if missing:
        raise ValueError(f"STAGING_LAYOUT_INCOMPLETE: {', '.join(missing)}")
    actual_plugins = {
        path.parent.name for path in (stage / "plugins/builtin").glob("*/plugin.yaml")
    }
    if actual_plugins != BUILTIN_PLUGINS:
        raise ValueError(
            f"STAGING_PLUGIN_SET_INVALID: expected={sorted(BUILTIN_PLUGINS)!r}, actual={sorted(actual_plugins)!r}"
        )
    for user_owned in ("config", "data", "characters", "tts"):
        if (stage / user_owned).exists():
            raise ValueError(f"STAGING_CONTAINS_USER_DATA: {user_owned}")
    if (stage / "plugins/user").exists():
        raise ValueError("STAGING_CONTAINS_USER_DATA: plugins/user")
    if (stage / "portable.flag").exists() != portable:
        raise ValueError("STAGING_PORTABLE_FLAG_INVALID")
    if not any(site_packages(stage / "python", target).glob("*.dist-info")):
        raise ValueError("STAGING_DIST_INFO_MISSING")
    forbidden = forbidden_paths(stage)
    if forbidden:
        raise ValueError(f"STAGING_FORBIDDEN_CONTENT: {', '.join(forbidden[:20])}")


def smoke(stage: Path, target: str) -> None:
    executable = python_executable(stage / "python", target)
    modules = ",".join(repr(name) for name in IMPORTS)
    script = (
        "import importlib,sys;"
        f"sys.path[:0]=[{str(stage / 'core')!r},{str(stage)!r}];"
        f"[importlib.import_module(name) for name in [{modules}]];"
        "import app.core_host,plugins.builtin"
    )
    subprocess.run([str(executable), "-I", "-B", "-c", script], check=True, timeout=90)
    suffix = ".exe" if target == "windows-x64" else ""
    for name, argument in (("uv", "--version"), ("uvx", "--version"), ("7zz", "-h")):
        subprocess.run(
            [str(stage / "python/tools" / f"{name}{suffix}"), argument],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=15,
        )


def inventory(stage: Path, target: str) -> dict[str, object]:
    files = []
    directory_sizes: dict[str, int] = {}
    total = 0
    for path in sorted(item for item in stage.rglob("*") if item.is_file()):
        relative = path.relative_to(stage).as_posix()
        if relative == "release-inventory.json":
            continue
        content = path.read_bytes()
        size = len(content)
        total += size
        top = relative.partition("/")[0]
        directory_sizes[top] = directory_sizes.get(top, 0) + size
        files.append({"path": relative, "size": size, "sha256": hashlib.sha256(content).hexdigest()})
    return {
        "schemaVersion": 1,
        "target": target,
        "version": (stage / "VERSION").read_text(encoding="utf-8").strip(),
        "fileCount": len(files),
        "uncompressedBytes": total,
        "topLevelBytes": dict(sorted(directory_sizes.items())),
        "files": files,
    }


def assemble(repo: Path, python_source: Path, output: Path, target: str, *, portable: bool) -> None:
    if output.exists() and any(output.iterdir()):
        raise ValueError("STAGING_OUTPUT_NOT_EMPTY")
    output.mkdir(parents=True, exist_ok=True)
    shutil.copy2(repo / "VERSION", output / "VERSION")
    shutil.copy2(
        repo / f"desktop/src-tauri/runtime-layouts/{target}/runtime-manifest.json",
        output / "runtime-manifest.json",
    )
    copy_tree(python_source, output / "python")
    copy_tree(repo / "app", output / "core/app")
    copy_tree(repo / "third_party", output / "core/third_party")
    (output / "plugins").mkdir(exist_ok=True)
    copy_tree(repo / "plugins/builtin", output / "plugins/builtin")
    move_tools(output / "python", target)
    if target == "windows-x64":
        write_windows_pth(output / "python")
    if portable:
        (output / "portable.flag").write_bytes(b"")
    validate_layout(output, target, portable=portable)
    (output / "release-inventory.json").write_text(
        json.dumps(inventory(output, target), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--target", required=True, choices=sorted(TARGETS))
    parser.add_argument("--python-root", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--portable", action="store_true")
    parser.add_argument("--smoke", action="store_true")
    args = parser.parse_args()
    repo = Path(__file__).resolve().parents[2]
    python_root = args.python_root.resolve()
    output = args.output.resolve()
    if output == repo or repo in output.parents and output.name in {"data", "characters", "plugins"}:
        raise SystemExit("STAGING_OUTPUT_UNSAFE")
    assemble(repo, python_root, output, args.target, portable=args.portable)
    if args.smoke:
        smoke(output, args.target)
    report = json.loads((output / "release-inventory.json").read_text(encoding="utf-8"))
    print(json.dumps({key: report[key] for key in ("target", "version", "fileCount", "uncompressedBytes", "topLevelBytes")}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
