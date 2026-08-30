"""Runtime v2 plugin manifest data models."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Sequence

from app.llm.prompts.types import ContextFragment, ContextRequest


PLUGIN_API_V4_VERSION = 4


@dataclass(frozen=True)
class PromptPatchContribution:
    """Internal Assistant prompt patch retained for the headless chat runtime."""

    patch_id: str
    system_prompt_append: str = ""
    reply_protocol_append: str = ""


@dataclass(frozen=True)
class ContextProviderContribution:
    """Dynamic context contribution consumed by the Runtime v2 Assistant."""

    provider_id: str
    description: str
    build_context: Callable[[ContextRequest], Sequence[ContextFragment]]
    order: float = 100.0
    enabled: bool = True


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
    priority_override: bool = False
