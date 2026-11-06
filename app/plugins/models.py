"""Runtime v2 plugin manifest data models."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from app.plugins.visuals import VisualCapability


PLUGIN_API_V4_VERSION = 4


@dataclass(frozen=True)
class PluginSpec:
    """One validated plugin package discovered from a manifest."""

    entry: str
    enabled: bool = True
    priority: int = 100
    plugin_id: str = ""
    name: str = ""
    author: str = ""
    description: str = ""
    version: str = "0.0.0"
    api_version: int = PLUGIN_API_V4_VERSION
    required: bool = False
    permissions: tuple[str, ...] = field(default_factory=tuple)
    provides: tuple[str, ...] = field(default_factory=tuple)
    requires: tuple[str, ...] = field(default_factory=tuple)
    plugin_root: Path | None = None
    source: str = "manifest"
    visuals: tuple[VisualCapability, ...] = ()
