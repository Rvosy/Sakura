"""Package-index defaults shared by explicit dependency installers."""
from __future__ import annotations

import os
import sys
import tomllib
from pathlib import Path
from typing import Mapping


DEFAULT_PYPI_INDEX = "https://mirrors.aliyun.com/pypi/simple"
_INDEX_KEYS = {"index", "default-index", "index-url", "extra-index-url", "no-index", "find-links", "sources"}
_UV_SOURCE_ENV = {
    "UV_DEFAULT_INDEX", "UV_INDEX_URL", "UV_INDEX", "UV_EXTRA_INDEX_URL",
    "UV_NO_INDEX", "UV_FIND_LINKS", "UV_CONFIG_FILE",
}


def uv_download_environment(
    directory: Path, environment: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Use the domestic default only when the caller has not chosen sources.

    Do not rewrite requirements, direct URLs, lockfiles, or uv's index strategy.
    """
    result = dict(os.environ if environment is None else environment)
    if any(result.get(key) for key in _UV_SOURCE_ENV):
        return result
    if _has_uv_sources(directory, result):
        return result
    # uv does not read pip's environment settings itself.
    if result.get("PIP_NO_INDEX") or result.get("PIP_FIND_LINKS"):
        for source, target in [("PIP_NO_INDEX", "UV_NO_INDEX"), ("PIP_FIND_LINKS", "UV_FIND_LINKS")]:
            if result.get(source):
                result[target] = result[source]
        return result
    result["UV_DEFAULT_INDEX"] = result.get("PIP_INDEX_URL") or DEFAULT_PYPI_INDEX
    if result.get("PIP_EXTRA_INDEX_URL"):
        result["UV_EXTRA_INDEX_URL"] = result["PIP_EXTRA_INDEX_URL"]
    return result


def _has_uv_sources(directory: Path, environment: Mapping[str, str]) -> bool:
    for name in ("requirements.lock", "requirements.txt"):
        try:
            lines = (directory / name).read_text(encoding="utf-8").splitlines()
        except FileNotFoundError:
            continue
        except (OSError, ValueError):
            return True
        if any(line.lstrip().startswith(("--index-url", "--extra-index-url", "--no-index", "--find-links", "-i ", "-f ")) for line in lines):
            return True
    paths = []
    for parent in (directory.resolve(), *directory.resolve().parents):
        paths.extend([parent / "uv.toml", parent / "pyproject.toml"])
    if sys.platform == "win32":
        if environment.get("APPDATA"):
            paths.append(Path(environment["APPDATA"]) / "uv" / "uv.toml")
        if environment.get("PROGRAMDATA"):
            paths.append(Path(environment["PROGRAMDATA"]) / "uv" / "uv.toml")
    else:
        paths.extend([
            Path(environment.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "uv" / "uv.toml",
            Path("/etc/uv/uv.toml"),
        ])
    for path in paths:
        try:
            data = tomllib.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            continue
        except (OSError, ValueError):
            # Let uv report its own configuration error without overriding it.
            return True
        if path.name == "pyproject.toml":
            tool = data.get("tool", {})
            if not isinstance(tool, dict):
                return True
            data = tool.get("uv", {})
        if isinstance(data, dict) and (
            _INDEX_KEYS.intersection(data)
            or isinstance(data.get("pip"), dict) and _INDEX_KEYS.intersection(data["pip"])
        ):
            return True
    return False
