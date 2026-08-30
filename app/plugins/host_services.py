"""Stable Plugin API v4 Host Service keys shared by Core adapters."""

from __future__ import annotations

HOST_CONTEXT_SERVICE = "sakura.host.context"
HOST_ARTIFACTS_SERVICE = "sakura.host.artifacts"
HOST_CHARACTER_SERVICE = "sakura.host.character"
HOST_MODEL_SLOTS_SERVICE = "sakura.host.model_slots"
HOST_MOBILE_SERVICE = "sakura.host.mobile"
HOST_SETTINGS_SERVICE = "sakura.host.settings"
HOST_SETTINGS_COLLECTION_V0_SERVICE = "sakura.host.settings.collection-v0"
HOST_SETTINGS_SURFACE_V0_SERVICE = "sakura.host.settings.surface-v0"
HOST_STORAGE_SERVICE = "sakura.host.storage"
HOST_TOOLS_SERVICE = "sakura.host.tools"
HOST_COMPOSER_TOOLS_V0_SERVICE = "sakura.host.ui.composer-tools-v0"
HOST_TIMELINE_SERVICE = "sakura.host.timeline"

__all__ = [
    "HOST_ARTIFACTS_SERVICE",
    "HOST_CHARACTER_SERVICE",
    "HOST_COMPOSER_TOOLS_V0_SERVICE",
    "HOST_CONTEXT_SERVICE",
    "HOST_MOBILE_SERVICE",
    "HOST_MODEL_SLOTS_SERVICE",
    "HOST_SETTINGS_COLLECTION_V0_SERVICE",
    "HOST_SETTINGS_SERVICE",
    "HOST_SETTINGS_SURFACE_V0_SERVICE",
    "HOST_STORAGE_SERVICE",
    "HOST_TIMELINE_SERVICE",
    "HOST_TOOLS_SERVICE",
]
