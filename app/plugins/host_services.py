"""Stable Plugin API v4 Host Service keys shared by Core adapters."""

from __future__ import annotations

from contextvars import ContextVar

# Bound by the runtime for the duration of a host call, never supplied by payload.
HOST_CALLER: ContextVar[str | None] = ContextVar("sakura_host_caller", default=None)

HOST_CALLER_LOG_METADATA: ContextVar[tuple[str, tuple[str, ...]]] = ContextVar("sakura_host_log_metadata", default=("", ()))

HOST_CONTEXT_SERVICE = "sakura.host.context"
HOST_DIAGNOSTICS_SERVICE = "sakura.host.diagnostics"
HOST_LOGGING_SERVICE = "sakura.host.logging"
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
    "HOST_DIAGNOSTICS_SERVICE",
    "HOST_LOGGING_SERVICE",
    "HOST_MOBILE_SERVICE",
    "HOST_MODEL_SLOTS_SERVICE",
    "HOST_SETTINGS_COLLECTION_V0_SERVICE",
    "HOST_SETTINGS_SERVICE",
    "HOST_SETTINGS_SURFACE_V0_SERVICE",
    "HOST_STORAGE_SERVICE",
    "HOST_TIMELINE_SERVICE",
    "HOST_TOOLS_SERVICE",
]
