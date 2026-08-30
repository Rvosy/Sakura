#!/usr/bin/env python3
"""Explicitly prepare API 4 dependency roots for the source-tree distribution."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path

import yaml


PLUGIN_DIRECTORIES = ("sakura_genie", "sakura_gpt_sovits", "sakura_mem0")
_PLUGIN_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")


def _uv_executable(python: Path) -> Path:
    suffix = ".exe" if os.name == "nt" else ""
    candidates = (
        python.with_name(f"uv{suffix}"),
        python.parent / "Scripts" / f"uv{suffix}",
        python.parent / "tools" / f"uv{suffix}",
        python.parent.parent / "tools" / f"uv{suffix}",
    )
    executable = next((path for path in candidates if path.is_file()), None)
    if executable is None:
        raise ValueError("DEVELOPMENT_UV_MISSING")
    return executable


def prepare(repo: Path, python: Path) -> None:
    repo = repo.resolve(strict=True)
    python = python.resolve(strict=True)
    plugins = repo / "plugins"
    final = plugins / "dependencies"
    staging = Path(tempfile.mkdtemp(prefix=".dependencies-build-", dir=plugins))
    backup = plugins / f".dependencies-backup-{uuid.uuid4().hex}"
    published = False
    previous_saved = False
    environment = os.environ.copy()
    environment.update({"PYTHONNOUSERSITE": "1", "UV_PYTHON_DOWNLOADS": "never"})
    try:
        uv = _uv_executable(python)
        for directory_name in PLUGIN_DIRECTORIES:
            plugin_root = plugins / "builtin" / directory_name
            requirements = plugin_root / "requirements.txt"
            manifest = yaml.safe_load((plugin_root / "plugin.yaml").read_text(encoding="utf-8"))
            plugin_id = manifest.get("id") if isinstance(manifest, dict) else None
            if (
                not isinstance(manifest, dict)
                or manifest.get("api") != 4
                or not isinstance(plugin_id, str)
                or not _PLUGIN_ID.fullmatch(plugin_id)
            ):
                raise ValueError(f"DEVELOPMENT_PLUGIN_INVALID: {directory_name}")
            entry = manifest.get("entry")
            if not isinstance(entry, str):
                raise ValueError(f"DEVELOPMENT_PLUGIN_INVALID: {directory_name}")
            dependency_root = staging / plugin_id
            dependency_root.mkdir()
            subprocess.run(
                [
                    str(uv),
                    "pip",
                    "install",
                    "--target",
                    str(dependency_root),
                    "--python",
                    str(python),
                    "--no-python-downloads",
                    "--link-mode",
                    "clone" if sys.platform == "darwin" else "hardlink",
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
                "fingerprint": hashlib.sha256(requirements.read_bytes()).hexdigest(),
                "python": f"{sys.version_info.major}.{sys.version_info.minor}",
            }
            (dependency_root / ".sakura-dependencies.json").write_text(
                json.dumps(marker, ensure_ascii=False, sort_keys=True),
                encoding="utf-8",
            )
            with tempfile.TemporaryDirectory(prefix="sakura-plugin-entry-") as data_dir:
                subprocess.run(
                    [
                        str(python),
                        "-I",
                        "-S",
                        str(repo / "app/plugins/plugin_runner_v4.py"),
                        "--plugin-id",
                        plugin_id,
                        "--generation-id",
                        "development-install",
                        "--plugin-root",
                        str(plugin_root),
                        "--dependency-root",
                        str(dependency_root),
                        "--data-dir",
                        data_dir,
                        "--entry",
                        entry,
                        "--validate-entry",
                    ],
                    check=True,
                    cwd=plugin_root,
                    env=environment,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    timeout=30,
                )
        if final.exists():
            os.replace(final, backup)
            previous_saved = True
        os.replace(staging, final)
        published = True
        if previous_saved:
            shutil.rmtree(backup, ignore_errors=True)
    except Exception:
        if previous_saved and not final.exists() and backup.exists():
            os.replace(backup, final)
        raise
    finally:
        if not published:
            shutil.rmtree(staging, ignore_errors=True)


def main() -> int:
    repo = Path(__file__).resolve().parents[1]
    prepare(repo, Path(sys.executable))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
