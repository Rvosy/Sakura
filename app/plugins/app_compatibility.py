"""插件声明的最低 Sakura 版本检查。"""

import re
from pathlib import Path
from collections.abc import Mapping
from app.config.app_version import read_app_version

_SEMVER = re.compile(
    r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)"
    r"(?:-([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?"
    r"(?:\+([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?"
)


def semver_precedence(value: object) -> tuple:
    """按 SemVer 比较；构建元数据不影响顺序。"""
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


def app_version_reason(raw: Mapping, distribution_root: Path) -> str:
    if "min_app_version" not in raw:
        return "READY"
    try:
        minimum = semver_precedence(raw["min_app_version"])
    except ValueError:
        return "PLUGIN_MANIFEST_INVALID"
    try:
        current = semver_precedence(read_app_version(distribution_root))
    except ValueError:
        return "APP_VERSION_UNAVAILABLE"
    return "APP_VERSION_UNSUPPORTED" if current < minimum else "READY"


def compatibility_message(code: str) -> str:
    return {
        "APP_VERSION_UNSUPPORTED": "请升级 Sakura 主程序后重试。",
        "APP_VERSION_UNAVAILABLE": "无法读取 Sakura 主程序版本，请检查安装。",
        "PLUGIN_MANIFEST_INVALID": "插件清单格式无效。",
    }.get(code, "插件安装失败。")
