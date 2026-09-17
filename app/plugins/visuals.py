"""Static visual capability declarations. Discovery never imports plugin code."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, Mapping, Sequence


VISUAL_CONTRACT_VERSION = 1
RESOURCE_TYPE_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,159}@[1-9][0-9]{0,5}$")
_SERVICE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,199}$")


def relative_resource_path(value: object, *, allow_root: bool = False) -> str:
    """Accept portable, package-relative paths on every host platform."""
    if not isinstance(value, str) or not value:
        raise ValueError("VISUAL_PATH_INVALID")
    if allow_root and value == ".":
        return value
    if (
        "\\" in value
        or PurePosixPath(value).is_absolute()
        or PureWindowsPath(value).drive
        or any(part in {"", ".", ".."} for part in value.split("/"))
        or re.search(r'[<>:"|?*\x00-\x1f]', value)
        or any(part.endswith((" ", ".")) for part in value.split("/"))
    ):
        raise ValueError("VISUAL_PATH_INVALID")
    return value


def resolve_resource_path(root: Path, relative: str) -> Path:
    """Check actual containment as well as lexical paths, including junctions."""
    relative_resource_path(relative, allow_root=True)
    try:
        resolved_root = root.resolve(strict=True)
        resolved = (resolved_root / relative).resolve(strict=True)
        resolved.relative_to(resolved_root)
    except (OSError, ValueError, RuntimeError) as error:
        raise ValueError("VISUAL_RESOURCE_INVALID") from error
    return resolved


@dataclass(frozen=True)
class VisualCapability:
    resource_type: str
    service: str
    contract: int
    renderer: str
    editor: str | None = None

    def to_mapping(self) -> dict[str, Any]:
        result = {
            "type": self.resource_type,
            "service": self.service,
            "contract": self.contract,
            "renderer": self.renderer,
        }
        if self.editor is not None:
            result["editor"] = self.editor
        return result


def _visual_module(value: object, plugin_root: Path | None) -> str:
    try:
        path = relative_resource_path(value)
        if Path(path).suffix not in {".js", ".mjs"}:
            raise ValueError("VISUAL_MODULE_INVALID")
        if plugin_root is not None and not resolve_resource_path(plugin_root, path).is_file():
            raise ValueError("VISUAL_MODULE_INVALID")
        return path
    except ValueError as error:
        raise ValueError("VISUAL_MODULE_INVALID") from error


def visual_capabilities_from_manifest(
    value: object,
    provides: Sequence[str],
    *,
    plugin_root: Path | None = None,
    issues: list[dict[str, str]] | None = None,
) -> tuple[VisualCapability, ...]:
    """Collect usable capabilities; callers may retain optional failures locally.

    Startup payloads use strict parsing. Installation discovery supplies an issue
    list so one broken renderer or editor does not discard unrelated services.
    """
    def unavailable(error: ValueError, resource_type: str = "", part: str = "manifest") -> None:
        if issues is None:
            raise error
        issues.append({"kind": "visual", "type": resource_type, "part": part, "reasonCode": str(error)})

    if not isinstance(value, list) or len(value) > 32:
        unavailable(ValueError("VISUAL_MANIFEST_INVALID"))
        return ()
    capabilities = []
    seen: set[str] = set()
    for raw in value:
        resource_type = raw.get("type") if isinstance(raw, Mapping) else None
        if not isinstance(resource_type, str) or not RESOURCE_TYPE_PATTERN.fullmatch(resource_type):
            unavailable(ValueError("VISUAL_MANIFEST_INVALID"))
            continue
        service = raw.get("service")
        contract = raw.get("contract")
        if (
            resource_type in seen
            or not isinstance(service, str)
            or not _SERVICE.fullmatch(service)
            or service not in provides
            or type(contract) is not int
            or not 1 <= contract <= 65535
        ):
            unavailable(ValueError("VISUAL_MANIFEST_INVALID"), resource_type)
            continue
        try:
            renderer = _visual_module(raw.get("renderer"), plugin_root)
        except ValueError as error:
            unavailable(error, resource_type, "renderer")
            continue
        editor = None
        if "editor" in raw:
            try:
                editor = _visual_module(raw["editor"], plugin_root)
            except ValueError as error:
                unavailable(error, resource_type, "editor")
        seen.add(resource_type)
        capabilities.append(VisualCapability(resource_type, service, contract, renderer, editor))
    return tuple(capabilities)
