"""Portable resource declarations supplied by character plugins."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
import re

from app.plugins.visuals import relative_resource_path, resolve_resource_path


def parse_extension_resources(value: object) -> dict[str, dict]:
    from app.config.plugin_requirements import parse_requirements

    if not isinstance(value, Mapping):
        raise ValueError("CHARACTER_RESOURCE_DECLARATION_INVALID")
    result = {}
    for owner, declaration in value.items():
        if (not isinstance(owner, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", owner)
                or not isinstance(declaration, Mapping)
                or set(declaration) - {"kind", "paths", "pluginRequirements"}
                or not isinstance(declaration.get("kind"), str)
                or not declaration["kind"]
                or not isinstance(declaration.get("paths"), list)):
            raise ValueError("CHARACTER_RESOURCE_DECLARATION_INVALID")
        result[owner] = {
            "kind": declaration["kind"],
            "paths": list(dict.fromkeys(relative_resource_path(path) for path in declaration["paths"])),
            "pluginRequirements": parse_requirements(declaration.get("pluginRequirements", [])),
        }
    return result


def extension_resource_files(package_dir: Path, declarations: Mapping[str, dict]) -> dict[str, set[Path]]:
    """Resolve declared files/directories without interpreting plugin configuration."""
    result = {}
    package_root = package_dir.resolve()

    def resolve(relative):
        relative_resource_path(relative)
        cursor = package_root
        for component in relative.split("/"):
            cursor = cursor / component
            if cursor.is_symlink():
                raise ValueError("CHARACTER_RESOURCE_SYMLINK_INVALID")
        return resolve_resource_path(package_root, relative)

    for owner, declaration in declarations.items():
        files = set()
        for relative in declaration["paths"]:
            resource = resolve(relative)
            if resource.is_dir():
                for child in resource.rglob("*"):
                    checked = resolve(child.relative_to(package_root).as_posix())
                    if checked.is_file():
                        files.add(checked)
            elif resource.is_file():
                files.add(resource)
            else:
                raise ValueError("CHARACTER_RESOURCE_INVALID")
        result[owner] = files
    return result
