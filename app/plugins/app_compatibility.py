"""Plugin minimum application version declarations and SemVer precedence."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Mapping

from app.config.app_version import read_app_version


_SEMVER = re.compile(
    r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)"
    r"(?:-([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?"
    r"(?:\+([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?"
)


def semver_precedence(value: object) -> tuple:
    """Parse strict SemVer; build metadata does not affect precedence."""
    if not isinstance(value, str) or (match := _SEMVER.fullmatch(value)) is None:
        raise ValueError("PLUGIN_MANIFEST_INVALID")
    core = tuple(int(match[index]) for index in (1, 2, 3))
    prerelease = match[4]
    if prerelease is None:
        return core, (1,)
    identifiers = []
    for identifier in prerelease.split("."):
        if identifier.isascii() and identifier.isdigit():
            if len(identifier) > 1 and identifier.startswith("0"):
                raise ValueError("PLUGIN_MANIFEST_INVALID")
            identifiers.append((0, int(identifier)))
        else:
            identifiers.append((1, identifier))
    return core, (0, tuple(identifiers))


def minimum_app_version(raw: Mapping[str, object]) -> str:
    """Absence preserves the compatibility behavior of older plugin packages."""
    if "min_app_version" not in raw:
        return ""
    value = raw["min_app_version"]
    semver_precedence(value)
    assert isinstance(value, str)
    return value


def app_version_reason(minimum: str, distribution_root: Path) -> str:
    if minimum == "":
        return "READY"
    try:
        minimum_precedence = semver_precedence(minimum)
    except ValueError:
        return "PLUGIN_MANIFEST_INVALID"
    try:
        current = read_app_version(distribution_root)
        current_precedence = semver_precedence(current)
    except (ValueError, UnicodeError):
        return "APP_VERSION_UNAVAILABLE"
    return "APP_VERSION_UNSUPPORTED" if current_precedence < minimum_precedence else "READY"


def compatibility_message(code: str, fallback: str) -> str:
    return {
        "APP_VERSION_UNSUPPORTED": "请升级 Sakura 主程序后重试。",
        "APP_VERSION_UNAVAILABLE": "无法读取 Sakura 主程序版本，请检查安装。",
    }.get(code, fallback)
