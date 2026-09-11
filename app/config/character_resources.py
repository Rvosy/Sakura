"""Resource references shared by character visual providers and their host."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from app.plugins.visuals import RESOURCE_TYPE_PATTERN, relative_resource_path, resolve_resource_path


@dataclass(frozen=True)
class CharacterVisualResource:
    id: str
    type: str
    root: str
    entry: str
    name: str = ""

    @classmethod
    def from_mapping(cls, value: object) -> "CharacterVisualResource":
        if not isinstance(value, Mapping) or not {"id", "type", "root", "entry"} <= set(value) or set(value) - {"id", "type", "root", "entry", "name"}:
            raise ValueError("VISUAL_RESOURCE_INVALID")
        resource_id, resource_type = value.get("id"), value.get("type")
        if (
            not isinstance(resource_id, str)
            or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", resource_id)
            or not isinstance(resource_type, str)
            or not RESOURCE_TYPE_PATTERN.fullmatch(resource_type)
            or not isinstance(value.get("name", ""), str)
            or len(value.get("name", "")) > 80
        ):
            raise ValueError("VISUAL_RESOURCE_INVALID")
        return cls(
            resource_id,
            resource_type,
            relative_resource_path(value.get("root"), allow_root=True),
            relative_resource_path(value.get("entry")),
            value.get("name", "").strip() or (
                "立绘1" if resource_id == "portrait-default" and resource_type == "sakura.visual.portrait@1" else ""
            ),
        )

    def to_mapping(self) -> dict[str, str]:
        return {"id": self.id, "type": self.type, "root": self.root, "entry": self.entry, **({"name": self.name} if self.name else {})}

    def validate_paths(self, package_dir: Path, *, require_exists: bool = True) -> None:
        # Revalidate direct Python construction as well as deserialized input.
        self.from_mapping(self.to_mapping())
        if not require_exists:
            try:
                package_root = package_dir.resolve(strict=True)
                resource_root = (package_root / self.root).resolve(strict=False)
                resource_root.relative_to(package_root)
                (resource_root / self.entry).resolve(strict=False).relative_to(resource_root)
            except (OSError, ValueError, RuntimeError) as error:
                raise ValueError("VISUAL_RESOURCE_INVALID") from error
            return
        resource_root = resolve_resource_path(package_dir, self.root)
        if not resource_root.is_dir() or not resolve_resource_path(resource_root, self.entry).is_file():
            raise ValueError("VISUAL_RESOURCE_INVALID")


def legacy_portrait_resource() -> CharacterVisualResource:
    return CharacterVisualResource("portrait-default", "sakura.visual.portrait@1", ".", "character.json", "立绘1")


def character_visual_resources(
    manifest: Mapping, package_dir: Path,
) -> tuple[tuple[CharacterVisualResource, ...], str | None]:
    """Normalize references without running or requiring any visual provider."""
    if "visuals" not in manifest:
        if "portrait" not in manifest:
            return (), None
        # A named old-format adapter; the portrait provider interprets the file.
        if not isinstance(manifest["portrait"], Mapping):
            raise ValueError("VISUAL_RESOURCE_INVALID")
        resource = legacy_portrait_resource()
        return (resource,), resource.id
    raw = manifest["visuals"]
    if not isinstance(raw, Mapping) or not isinstance(raw.get("resources"), list) or len(raw["resources"]) > 32:
        raise ValueError("VISUAL_RESOURCE_INVALID")
    resources = tuple(CharacterVisualResource.from_mapping(item) for item in raw["resources"])
    ids = {item.id for item in resources}
    providers = raw.get("providers", {})
    if not isinstance(providers, Mapping) or set(providers) - ids or any(not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", value) for value in providers.values()):
        raise ValueError("VISUAL_PROVIDER_SELECTION_INVALID")
    selected = raw.get("default")
    if len(ids) != len(resources) or (selected is not None and (not isinstance(selected, str) or selected not in ids)):
        raise ValueError("VISUAL_RESOURCE_INVALID")
    if resources and selected is None:
        selected = resources[0].id
    for resource in resources:
        resource.validate_paths(package_dir, require_exists=False)
    return resources, selected
