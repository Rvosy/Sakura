"""Sakura plugin compatibility exports, loaded lazily by symbol."""

from __future__ import annotations

from importlib import import_module
from typing import Any


_EXPORT_GROUPS = {
    "app.plugins.base": ("PluginBase", "PluginContext"),
    "app.plugins.capabilities": ("PluginCapabilities", "PluginCapabilityRegistry"),
    "app.plugins.discovery": ("PluginDiscovery",),
    "app.plugins.events": ("PluginEventBus", "ScopedEventBus"),
    "app.plugins.manager": ("PluginLoadResult", "PluginManager"),
    "app.llm.prompts.types": ("ContextFragment", "ContextRequest"),
    "app.plugins.models": (
        "KNOWN_PLUGIN_PERMISSIONS",
        "PERMISSION_CHAT_UI",
        "PERMISSION_CONTEXT_PROVIDER",
        "PERMISSION_EVENT_APP",
        "PERMISSION_EVENT_CHARACTER",
        "PERMISSION_EVENT_MESSAGE",
        "PERMISSION_EVENT_TTS",
        "PERMISSION_PLUGIN_SETTINGS",
        "PERMISSION_PROMPT_PATCH",
        "PERMISSION_RENDERER",
        "PERMISSION_MOBILE_CHAT",
        "PERMISSION_TOOL",
        "PERMISSION_TOOLS_TAB",
        "PLUGIN_API_VERSION",
        "PLUGIN_API_V3_VERSION",
        "SUPPORTED_API_VERSIONS",
        "ChatUIWidgetContribution",
        "ContextProviderContribution",
        "PluginEvent",
        "PluginManifest",
        "PluginManifestView",
        "PluginSettingsAction",
        "PluginSettingsContribution",
        "PluginSettingsField",
        "PluginSpec",
        "PromptPatchContribution",
        "RendererContribution",
        "RendererCreateContext",
        "ToolContribution",
        "ToolsTabContribution",
    ),
    "app.plugins.services": (
        "PluginAgentService",
        "PluginInputService",
        "PluginMobileService",
        "PluginServices",
        "PluginTTSService",
        "PluginUIService",
    ),
}
_EXPORT_MODULES = {
    name: module_name
    for module_name, names in _EXPORT_GROUPS.items()
    for name in names
}
__all__ = list(_EXPORT_MODULES)


def __getattr__(name: str) -> Any:
    module_name = _EXPORT_MODULES.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(import_module(module_name), name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted((*globals(), *__all__))
