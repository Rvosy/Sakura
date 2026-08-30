#!/usr/bin/env python3
"""Assemble and validate the only distribution staging consumed by Tauri."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import tempfile
from pathlib import Path


TARGETS = {"windows-x64", "macos-arm64", "linux-x64"}
BUILTIN_PLUGINS = {
    "sakura_mem0",
    "sakura_mobile",
    "sakura_tts_hub",
    "sakura_genie",
    "sakura_gpt_sovits",
}
BUNDLED_DEPENDENCY_DIRECTORIES = {
    "sakura_mem0",
    "sakura_genie",
    "sakura_gpt_sovits",
}
CORE_IMPORTS = (
    "yaml",
    "py7zz",
    "mcp",
)
PLUGIN_ONLY_IMPORTS = (
    "playwright",
    "openai",
    "qdrant_client",
    "sqlalchemy",
    "posthog",
    "pytz",
    "google",
    "fastembed",
    "onnxruntime",
    "py7zr",
    "socksio",
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
DEPENDENCY_TEST_DIRECTORIES = {"test", "tests"}


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
            *sorted(FORBIDDEN_PARTS),
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


def _python_version(executable: Path) -> str:
    result = subprocess.run(
        [
            str(executable),
            "-I",
            "-S",
            "-c",
            "import sys;print(f'{sys.version_info.major}.{sys.version_info.minor}')",
        ],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=15,
    )
    return result.stdout.strip()


def stage_bundled_dependencies(stage: Path, target: str) -> None:
    executable = python_executable(stage / "python", target)
    suffix = ".exe" if target == "windows-x64" else ""
    uv = stage / "python/tools" / f"uv{suffix}"
    python_version = _python_version(executable)
    dependency_parent = stage / "plugins/dependencies"
    dependency_parent.mkdir(parents=True, exist_ok=True)
    environment = os.environ.copy()
    environment.update(
        {
            "PYTHONNOUSERSITE": "1",
            "UV_PYTHON_DOWNLOADS": "never",
        }
    )
    for directory_name in sorted(BUNDLED_DEPENDENCY_DIRECTORIES):
        plugin_root = stage / "plugins/builtin" / directory_name
        plugin_id = _manifest_plugin_id(plugin_root / "plugin.yaml")
        requirements = plugin_root / "requirements.txt"
        content = requirements.read_bytes()
        dependency_root = dependency_parent / plugin_id
        dependency_root.mkdir()
        subprocess.run(
            [
                str(uv),
                "pip",
                "install",
                "--target",
                str(dependency_root),
                "--python",
                str(executable),
                "--no-python-downloads",
                "--link-mode",
                "clone" if target == "macos-arm64" else "hardlink",
                "--no-progress",
                "--requirements",
                str(requirements),
            ],
            check=True,
            cwd=plugin_root,
            env=environment,
            timeout=600,
        )
        marker = {
            "schemaVersion": 1,
            "kind": "requirements.txt",
            "fingerprint": hashlib.sha256(content).hexdigest(),
            "python": python_version,
        }
        (dependency_root / ".sakura-dependencies.json").write_text(
            json.dumps(marker, ensure_ascii=False, sort_keys=True),
            encoding="utf-8",
        )


def _manifest_value(path: Path, key: str) -> str:
    pattern = re.compile(rf"^{re.escape(key)}:\s*(.+?)\s*$")
    for line in path.read_text(encoding="utf-8").splitlines():
        match = pattern.fullmatch(line)
        if match is not None:
            return match.group(1).strip('"\'')
    raise ValueError(f"STAGING_PLUGIN_MANIFEST_INVALID: {path.parent.name}:{key}")


def _manifest_plugin_id(path: Path) -> str:
    plugin_id = _manifest_value(path, "id")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}", plugin_id):
        raise ValueError(f"STAGING_PLUGIN_MANIFEST_INVALID: {path.parent.name}:id")
    return plugin_id


def smoke_bundled_entries(stage: Path, target: str) -> None:
    executable = python_executable(stage / "python", target)
    runner = stage / "core/app/plugins/plugin_runner_v4.py"
    environment = os.environ.copy()
    environment.pop("PYTHONPATH", None)
    environment.pop("PYTHONHOME", None)
    environment["PYTHONNOUSERSITE"] = "1"
    with tempfile.TemporaryDirectory(prefix="sakura-release-plugin-smoke-") as data_root:
        for directory_name in sorted(BUILTIN_PLUGINS):
            plugin_root = stage / "plugins/builtin" / directory_name
            plugin_id = _manifest_plugin_id(plugin_root / "plugin.yaml")
            command = [
                str(executable),
                "-I",
                "-B",
                "-S",
                str(runner),
                "--plugin-id",
                plugin_id,
                "--generation-id",
                "release-smoke",
                "--plugin-root",
                str(plugin_root),
                "--data-dir",
                str(Path(data_root) / plugin_id),
                "--entry",
                _manifest_value(plugin_root / "plugin.yaml", "entry"),
                "--validate-entry",
            ]
            dependency_root = stage / "plugins/dependencies" / plugin_id
            if dependency_root.is_dir():
                command.extend(["--dependency-root", str(dependency_root)])
            subprocess.run(
                command,
                check=True,
                cwd=plugin_root,
                env=environment,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=30,
            )


def forbidden_paths(stage: Path) -> list[str]:
    failures: list[str] = []
    for path in stage.rglob("*"):
        relative = path.relative_to(stage)
        lowered = {part.lower() for part in relative.parts}
        suffix = path.suffix.lower()
        python_path_file = path.is_file() and suffix == ".pth" and (
            path.parent.name.lower() == "site-packages"
            or (
                len(relative.parts) == 4
                and tuple(part.lower() for part in relative.parts[:2])
                == ("plugins", "dependencies")
            )
        )
        if lowered & FORBIDDEN_PARTS or (
            path.is_file()
            and suffix in FORBIDDEN_SUFFIXES
            and not python_path_file
        ):
            failures.append(relative.as_posix())
    return failures


def prune_non_runtime_files(stage: Path, target: str) -> None:
    """Remove installer artifacts that imports and plugin execution never consume."""
    roots = [site_packages(stage / "python", target)]
    dependency_parent = stage / "plugins/dependencies"
    if dependency_parent.is_dir():
        roots.extend(path for path in dependency_parent.iterdir() if path.is_dir())
    for root in roots:
        if not root.is_dir():
            continue
        test_directories = sorted(
            (
                path
                for path in root.rglob("*")
                if path.is_dir() and path.name.lower() in DEPENDENCY_TEST_DIRECTORIES
            ),
            key=lambda path: len(path.parts),
            reverse=True,
        )
        for directory in test_directories:
            shutil.rmtree(directory)
        for cache in sorted(root.rglob("__pycache__"), key=lambda path: len(path.parts), reverse=True):
            if cache.is_dir():
                shutil.rmtree(cache)
        for compiled in (*root.rglob("*.pyc"), *root.rglob("*.pyo")):
            compiled.unlink()
    if target == "windows-x64":
        # Console entry points created by pip are build-machine launchers. Core
        # calls Python modules directly and keeps the three supported tools in
        # python/tools instead.
        shutil.rmtree(stage / "python/Scripts", ignore_errors=True)
    if dependency_parent.is_dir():
        for dependency_root in dependency_parent.iterdir():
            if dependency_root.is_dir():
                # Plugin runners import dependency modules; none executes pip's
                # generated console entry points. Nested package binaries such
                # as py7zz/bin/7zz remain untouched.
                shutil.rmtree(dependency_root / "bin", ignore_errors=True)


def validate_layout(stage: Path, target: str, *, portable: bool) -> None:
    required = [
        stage / "VERSION",
        stage / "runtime-manifest.json",
        python_executable(stage / "python", target),
        site_packages(stage / "python", target),
        stage / "core/app/core_host/__main__.py",
        stage / "core/app/legacy_import/__main__.py",
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
    for plugin_id in sorted(BUILTIN_PLUGINS):
        manifest = stage / "plugins/builtin" / plugin_id / "plugin.yaml"
        if _manifest_value(manifest, "api") != "4":
            raise ValueError(f"STAGING_PLUGIN_API_INVALID: {plugin_id}")
    if (stage / "plugins/optional").exists():
        raise ValueError("STAGING_CONTAINS_OPTIONAL_PLUGINS")
    dependency_roots = stage / "plugins/dependencies"
    actual_dependency_roots = (
        {path.name for path in dependency_roots.iterdir() if path.is_dir()}
        if dependency_roots.is_dir()
        else set()
    )
    expected_dependency_roots = {
        _manifest_plugin_id(stage / "plugins/builtin" / directory_name / "plugin.yaml")
        for directory_name in BUNDLED_DEPENDENCY_DIRECTORIES
    }
    if actual_dependency_roots != expected_dependency_roots:
        raise ValueError(
            "STAGING_PLUGIN_DEPENDENCIES_INVALID: "
            f"expected={sorted(expected_dependency_roots)!r}, "
            f"actual={sorted(actual_dependency_roots)!r}"
        )
    for directory_name in sorted(BUNDLED_DEPENDENCY_DIRECTORIES):
        plugin_root = stage / "plugins/builtin" / directory_name
        plugin_id = _manifest_plugin_id(plugin_root / "plugin.yaml")
        requirements = plugin_root / "requirements.txt"
        marker_path = dependency_roots / plugin_id / ".sakura-dependencies.json"
        try:
            marker = json.loads(marker_path.read_text(encoding="utf-8"))
            fingerprint = hashlib.sha256(requirements.read_bytes()).hexdigest()
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            raise ValueError(f"STAGING_PLUGIN_DEPENDENCIES_INVALID: {plugin_id}")
        if marker != {
            "schemaVersion": 1,
            "kind": "requirements.txt",
            "fingerprint": fingerprint,
            "python": "3.12",
        }:
            raise ValueError(f"STAGING_PLUGIN_DEPENDENCIES_INVALID: {plugin_id}")
    for user_owned in ("config", "data", "characters", "tts"):
        if (stage / user_owned).exists():
            raise ValueError(f"STAGING_CONTAINS_USER_DATA: {user_owned}")
    if (stage / "plugins/user").exists():
        raise ValueError("STAGING_CONTAINS_USER_DATA: plugins/user")
    if (stage / "core/third_party").exists():
        raise ValueError("STAGING_CONTAINS_LEGACY_THIRD_PARTY")
    if (stage / "portable.flag").exists() != portable:
        raise ValueError("STAGING_PORTABLE_FLAG_INVALID")
    if not any(site_packages(stage / "python", target).glob("*.dist-info")):
        raise ValueError("STAGING_DIST_INFO_MISSING")
    forbidden = forbidden_paths(stage)
    if forbidden:
        raise ValueError(f"STAGING_FORBIDDEN_CONTENT: {', '.join(forbidden[:20])}")


def smoke(stage: Path, target: str) -> None:
    executable = python_executable(stage / "python", target)
    modules = ",".join(repr(name) for name in CORE_IMPORTS)
    plugin_only = ",".join(repr(name) for name in PLUGIN_ONLY_IMPORTS)
    script = (
        "import importlib,importlib.util,sys;"
        f"sys.path[:0]=[{str(stage / 'core')!r},{str(stage)!r}];"
        f"[importlib.import_module(name) for name in [{modules}]];"
        f"assert all(importlib.util.find_spec(name) is None for name in [{plugin_only}]);"
        "import app.core_host,app.legacy_import,plugins.builtin"
    )
    subprocess.run([str(executable), "-I", "-B", "-c", script], check=True, timeout=90)
    smoke_bundled_entries(stage, target)
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
    (output / "plugins").mkdir(exist_ok=True)
    copy_tree(repo / "plugins/builtin", output / "plugins/builtin")
    move_tools(output / "python", target)
    stage_bundled_dependencies(output, target)
    prune_non_runtime_files(output, target)
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
