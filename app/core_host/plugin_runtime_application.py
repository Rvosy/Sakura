"""Application-facing owner for one Plugin Runtime v4 generation."""

from __future__ import annotations

import threading
from dataclasses import asdict
from typing import Any, Mapping, Sequence

from app.core_host.plugin_artifacts import PluginArtifactStore
from app.core_host.plugin_character import PluginCharacterStore
from app.core_host.plugin_host_services import PluginHostServices
from app.core_host.mobile_host import MobileHostService
from app.llm.prompts.types import ContextRequest
from app.plugins.host_services import (
    HOST_ARTIFACTS_SERVICE,
    HOST_CHARACTER_SERVICE,
    HOST_COMPOSER_TOOLS_V0_SERVICE,
    HOST_CONTEXT_SERVICE,
    HOST_MODEL_SLOTS_SERVICE,
    HOST_MOBILE_SERVICE,
    HOST_SETTINGS_COLLECTION_V0_SERVICE,
    HOST_SETTINGS_SERVICE,
    HOST_SETTINGS_SURFACE_V0_SERVICE,
    HOST_STORAGE_SERVICE,
    HOST_TIMELINE_SERVICE,
    HOST_TOOLS_SERVICE,
)
from app.config.settings_service import AppSettingsService
from app.plugins.inventory import RuntimePluginSpec
from app.plugins.runtime_v4 import PluginRuntimeError, PluginRuntimeManager
from app.storage.paths import StoragePaths
from app.storage.runtime_roots import RuntimeRoots
from app.storage.timeline import TimelineStore


_HOST_EXPORTS = {
    HOST_ARTIFACTS_SERVICE: ("allocate", "commit", "release"),
    HOST_CHARACTER_SERVICE: ("current", "get", "update", "resolve_resource"),
    HOST_TOOLS_SERVICE: ("register", "unregister"),
    HOST_CONTEXT_SERVICE: ("register", "unregister"),
    HOST_MODEL_SLOTS_SERVICE: ("register", "unregister", "catalog", "resolve"),
    HOST_STORAGE_SERVICE: ("resolve",),
    HOST_SETTINGS_SERVICE: ("register", "unregister"),
    HOST_SETTINGS_SURFACE_V0_SERVICE: ("register", "unregister"),
    HOST_SETTINGS_COLLECTION_V0_SERVICE: ("register", "unregister"),
    HOST_COMPOSER_TOOLS_V0_SERVICE: ("register", "unregister"),
    HOST_TIMELINE_SERVICE: ("latest_cursor", "read_recent", "read_since"),
}

_HOST_EVENT_NAMES = {
    "app.start": "sakura.host.app.started",
    "message.user": "sakura.host.message.received",
    "message.ai": "sakura.host.message.sent",
    "tool.started": "sakura.host.tool.started",
    "tool.finished": "sakura.host.tool.finished",
    "tool.failed": "sakura.host.tool.failed",
}


class _HostServiceAdapter:
    def __init__(self, services: PluginHostServices, service_key: str) -> None:
        self._services = services
        self._service_key = service_key

    def __getattr__(self, method: str) -> object:
        if method.startswith("_"):
            raise AttributeError(method)

        def call(*args: object) -> object:
            return self._services.call(self._service_key, method, args)

        return call

    def revoke_scope(self, plugin_id: str) -> None:
        self._services.revoke_scope(self._service_key, plugin_id)


class PluginRuntimeApplication:
    """Thin Core adapter around the generic v4 manager and existing Host Services."""

    def __init__(
        self,
        roots: RuntimeRoots,
        generation_id: str,
        tool_registry: object,
        specs: Sequence[RuntimePluginSpec],
        *,
        call_timeout: float | None = None,
    ) -> None:
        self._roots = roots
        self._generation_id = generation_id
        self._tool_registry = tool_registry
        self._runtime: object | None = None
        self._session: object | None = None
        self._chat_boundary: object | None = None
        self._closed = False
        self._loaded = threading.Event()
        self._bound = threading.Event()
        manager_options = {} if call_timeout is None else {"call_timeout": call_timeout}
        self._manager = PluginRuntimeManager(
            roots,
            generation_id,
            specs,
            **manager_options,
        )
        self._host_services = PluginHostServices(
            tool_registry,
            artifact_store=PluginArtifactStore(roots.user_root, generation_id),
            character_store=PluginCharacterStore(roots.user_root),
            timeline_store=TimelineStore(StoragePaths(roots.user_root).timeline_database()),
            current_character_id=self._current_character_id,
            invoke_callback=self._manager.invoke_callback,
            encode_context_request=_context_request_mapping,
            on_context_change=self._host_context_changed,
            storage_root=roots.user_root,
            model_catalog=self._model_catalog,
            model_resolver=self._resolve_model,
        )
        for service_key in self._host_services.available_keys:
            self._manager.install_host_service(
                service_key,
                _HostServiceAdapter(self._host_services, service_key),
                exports=_HOST_EXPORTS[service_key],
            )
        self._manager.install_host_service(
            HOST_MOBILE_SERVICE,
            MobileHostService(
                roots.user_root,
                session_provider=lambda: self._session,
                chat_boundary_provider=lambda: self._chat_boundary,
                artifact_resolver=self._host_services.resolve_committed_artifact,
                artifact_releaser=self._host_services.release_committed_artifact,
            ),
            exports=("characters", "history", "begin", "poll", "cancel", "theme"),
        )

    @property
    def state(self) -> str:
        if self._closed:
            return "stopped"
        snapshot = self._manager.snapshot()
        return str(snapshot.get("state", "ready"))

    @property
    def reason_code(self) -> str:
        if self._closed:
            return "PLUGIN_RUNTIME_STOPPED"
        snapshot = self._manager.snapshot()
        return str(snapshot.get("reasonCode", "READY"))

    def start(self) -> None:
        if self._closed:
            raise PluginRuntimeError("GENERATION_INVALIDATED")
        try:
            self._manager.start()
            self._manager.emit_host_event(
                "sakura.host.app.started",
                {"generationId": self._generation_id},
            )
        finally:
            self._loaded.set()

    def wait_until_loaded(self, *, timeout: float = 8.0) -> bool:
        return self._loaded.wait(max(0.0, timeout)) and not self._closed

    def public_snapshot(self) -> dict[str, Any]:
        snapshot = self._manager.snapshot()
        if self._closed:
            snapshot["state"] = "stopped"
            snapshot["reasonCode"] = "PLUGIN_RUNTIME_STOPPED"
        return snapshot

    def settings_snapshot(self) -> dict[str, Any]:
        return self._host_services.decorate_settings_snapshot(self._manager.snapshot())

    def call_service(self, service_key: str, method: str, *args: object) -> object:
        return self._manager.call_service(service_key, method, *args)

    def invoke_callback(self, handle: str, shape: str, *args: object) -> object:
        return self._manager.invoke_callback(handle, shape, *args)

    def emit_event(self, event_type: str, payload: Mapping[str, Any]) -> None:
        event_name = (
            event_type
            if event_type.startswith("sakura.host.")
            else _HOST_EVENT_NAMES.get(event_type)
        )
        if event_name is None:
            raise PluginRuntimeError("EVENT_INVALID")
        self._manager.emit_host_event(event_name, dict(payload))

    def bind_runtime(
        self,
        tool_registry: object,
        runtime: object,
        *,
        session: object | None = None,
    ) -> None:
        if self._closed:
            return
        self._tool_registry = tool_registry
        self._runtime = runtime
        self._session = session
        getattr(tool_registry, "set_event_emitter")(
            lambda event_name, payload: self.emit_event(event_name, payload or {})
        )
        getattr(runtime, "set_context_providers")(self._host_services.context_providers())
        self._bound.set()

    def unbind_session(self) -> None:
        registry = self._tool_registry
        runtime = self._runtime
        self._runtime = None
        self._session = None
        self._bound.clear()
        try:
            getattr(registry, "set_event_emitter")(None)
        except (AttributeError, TypeError):
            pass
        if runtime is not None:
            try:
                getattr(runtime, "set_context_providers")([])
            except (AttributeError, TypeError):
                pass

    def wait_until_bound(self, *, timeout: float = 8.0) -> bool:
        return self._bound.wait(max(0.0, timeout)) and not self._closed

    def bind_chat_boundary(self, boundary: object) -> None:
        self._chat_boundary = boundary

    def set_plugin_enabled(self, plugin_id: str, enabled: bool) -> dict[str, Any]:
        return self._manager.set_enabled(plugin_id, enabled)

    def install_plugin(self, spec: RuntimePluginSpec) -> dict[str, Any]:
        return self._manager.install_plugin(spec)

    def uninstall_plugin(self, plugin_id: str) -> dict[str, Any]:
        return self._manager.uninstall_plugin(plugin_id)

    def reload_plugin(self, plugin_id: str) -> dict[str, Any]:
        return self._manager.reload_plugin(plugin_id)

    def apply_config(self, plugin_id: str, values: Mapping[str, Any]) -> dict[str, Any]:
        return self._manager.apply_config(plugin_id, values)

    def settings_save(
        self,
        plugin_id: str,
        section_id: str,
        values: Mapping[str, Any],
    ) -> object:
        handled, result = self._host_services.settings_save(plugin_id, section_id, values)
        if not handled:
            raise PluginRuntimeError("SETTINGS_ID_INVALID", plugin_id=plugin_id)
        if isinstance(result, Mapping) and result.get("applicationState") == "restart_required":
            self._manager.reload_plugin(plugin_id)
            applied = dict(result)
            applied["applicationState"] = "applied"
            applied["reasonCode"] = "READY"
            return applied
        return result

    def settings_action(
        self,
        plugin_id: str,
        section_id: str,
        action_id: str,
        values: Mapping[str, Any],
    ) -> object:
        handled, result = self._host_services.settings_action(
            plugin_id,
            section_id,
            action_id,
            values,
        )
        if not handled:
            raise PluginRuntimeError("SETTINGS_ACTION_INVALID", plugin_id=plugin_id)
        return result

    def settings_collection(
        self,
        operation: str,
        plugin_id: str,
        section_id: str,
        collection_id: str,
        payload: Mapping[str, Any],
    ) -> object:
        return self._host_services.settings_collection(
            operation,
            plugin_id,
            section_id,
            collection_id,
            payload,
        )

    def settings_sections(self, surface: str) -> list[dict[str, Any]]:
        return self._host_services.settings_sections(surface)

    def model_slots(self) -> list[dict[str, Any]]:
        return self._host_services.model_slots()

    def model_slot_save(self, identity: str, selection: Mapping[str, Any]) -> object:
        return self._host_services.model_slot_save(identity, selection)

    def composer_tools(self) -> list[dict[str, object]]:
        return self._host_services.composer_tools()

    def invoke_composer_tool(self, public_id: str) -> dict[str, str]:
        return self._host_services.invoke_composer_tool(public_id)

    def resolve_committed_artifact(self, artifact_id: str) -> object:
        return self._host_services.resolve_committed_artifact(artifact_id)

    def release_committed_artifact(self, artifact_id: str) -> bool:
        return self._host_services.release_committed_artifact(artifact_id)

    def quiesce(self) -> None:
        return None

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self.unbind_session()
        self._manager.close()
        self._host_services.clear()
        self._loaded.set()

    def _current_character_id(self) -> str | None:
        runtime_character = getattr(self._runtime, "character_id", None)
        return runtime_character if isinstance(runtime_character, str) and runtime_character else None

    def _host_context_changed(self, providers: list[object]) -> None:
        runtime = self._runtime
        if runtime is None:
            return
        try:
            getattr(runtime, "set_context_providers")(providers)
        except (AttributeError, TypeError):
            pass

    def _model_catalog(self) -> list[dict[str, object]]:
        profiles = AppSettingsService(self._roots.user_root).load_api_profiles()
        return [
            {
                "id": profile.id,
                "alias": profile.alias,
                "models": list(profile.models),
            }
            for profile in profiles
        ]

    def _resolve_model(self, selection: Mapping[str, Any]) -> dict[str, object]:
        settings = AppSettingsService(self._roots.user_root)
        profile_id = selection.get("profileId")
        model = selection.get("model")
        if not isinstance(profile_id, str) or not isinstance(model, str):
            raise ValueError("MODEL_SLOT_SELECTION_INVALID")
        if bool(profile_id) != bool(model):
            raise ValueError("MODEL_SLOT_SELECTION_INVALID")
        if not profile_id:
            inherited = settings.load_model_selection().chat
            profile_id = inherited.profile_id.strip()
            model = inherited.model.strip()
        if not profile_id:
            return {
                "profileId": "",
                "model": "",
                "baseUrl": "",
                "apiKey": "",
                "timeoutSeconds": settings.load_api_settings().timeout_seconds,
            }
        profile = next(
            (item for item in settings.load_api_profiles() if item.id == profile_id),
            None,
        )
        if profile is None or model not in profile.models:
            raise ValueError("MODEL_REFERENCE_INVALID")
        return {
            "profileId": profile.id,
            "model": model,
            "baseUrl": profile.base_url,
            "apiKey": profile.api_key,
            "timeoutSeconds": settings.load_api_settings().timeout_seconds,
        }


def _context_request_mapping(request: ContextRequest) -> dict[str, Any]:
    value = asdict(request)
    value["recent_messages"] = [dict(item) for item in value.get("recent_messages", [])]
    return value


__all__ = ["PluginRuntimeApplication"]
