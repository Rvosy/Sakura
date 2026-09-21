"""Application-facing owner for one Plugin Runtime v4 generation."""

from __future__ import annotations

import threading
from contextlib import contextmanager, nullcontext
from dataclasses import asdict, dataclass
from typing import Any, Mapping, Sequence

from app.core_host.plugin_artifacts import PluginArtifactStore
from app.core_host.audio_input import AudioInputResources, HOST_AUDIO_INPUT_SERVICE
from app.core_host.plugin_character import PluginCharacterStore
from app.core_host.plugin_host_services import PluginHostServices
from app.core_host.visual_host import VisualHost, VISUAL_INACTIVE_REASONS
from app.core.runtime_log import log_event
from app.core.diagnostics import exception_diagnostics
from app.core_host.mobile_host import MobileHostService
from app.core_host.chat_host import ChatHost, HOST_CHAT_SERVICE
from app.core_host.visual_control_host import HostVisualService, HOST_VISUAL_SERVICE
from app.core_host.screen_host import ScreenHost, HOST_SCREEN_SERVICE, migrate_legacy_screen_settings
from app.plugin_sdk.sakura_context import ContextRequest
from app.plugins.host_services import (
    HOST_ARTIFACTS_SERVICE,
    HOST_CHARACTER_SERVICE,
    HOST_COMPOSER_TOOLS_V0_SERVICE,
    HOST_CONTEXT_SERVICE,
    HOST_DIAGNOSTICS_SERVICE,
    HOST_LOGGING_SERVICE,
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
from app.config.model_references import (
    EMPTY_REFERENCE, ModelReferenceRepository, migrate_legacy_model_configuration, model_reference,
)
from app.plugins.inventory import PluginInventory, PluginInventorySnapshot, RuntimePluginSpec
from app.plugins.runtime_v4 import PluginRuntimeError, PluginRuntimeManager
from app.storage.paths import StoragePaths
from app.storage.runtime_roots import RuntimeRoots
from app.storage.timeline import TimelineStore


_HOST_EXPORTS = {
    HOST_LOGGING_SERVICE: ("emit",),
    HOST_ARTIFACTS_SERVICE: ("allocate", "commit", "release", "resolve", "release_received", "deliver", "release_delivered"),
    HOST_DIAGNOSTICS_SERVICE: ("emit",),
    HOST_CHARACTER_SERVICE: ("current", "get", "update", "resolve_resource"),
    HOST_TOOLS_SERVICE: ("register", "unregister", "catalog", "execute"),
    HOST_CONTEXT_SERVICE: ("register", "unregister", "describe", "catalog", "collect"),
    HOST_MODEL_SLOTS_SERVICE: ("register", "unregister", "catalog", "resolve", "active", "register_provider", "unregister_provider"),
    HOST_STORAGE_SERVICE: ("resolve",),
    HOST_SETTINGS_SERVICE: ("register", "unregister"),
    HOST_SETTINGS_SURFACE_V0_SERVICE: ("register", "unregister"),
    HOST_SETTINGS_COLLECTION_V0_SERVICE: ("register", "unregister"),
    HOST_COMPOSER_TOOLS_V0_SERVICE: ("register", "unregister"),
    HOST_TIMELINE_SERVICE: ("latest_cursor", "read_recent", "read_since", "read_turn_page"),
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
        self.allow_during_shutdown = service_key in {HOST_LOGGING_SERVICE, HOST_DIAGNOSTICS_SERVICE}

    def __getattr__(self, method: str) -> object:
        if method.startswith("_"):
            raise AttributeError(method)

        def call(*args: object) -> object:
            return self._services.call(self._service_key, method, args)

        return call

    def revoke_scope(self, plugin_id: str) -> None:
        self._services.revoke_scope(self._service_key, plugin_id)


@dataclass
class PreparedCharacter:
    character: object
    revision: int
    binding: object | None
    reason: str
    presentation: dict

    def close(self):
        if self.binding is not None:
            self.binding.close()


class PluginRuntimeApplication:
    """Thin Core adapter around the generic v4 manager and existing Host Services."""

    def __init__(
        self,
        roots: RuntimeRoots,
        generation_id: str,
        tool_registry: object,
        specs: Sequence[RuntimePluginSpec] | None = None,
        *,
        call_timeout: float | None = None,
    ) -> None:
        self._roots = roots
        self._generation_id = generation_id
        self._tool_registry = tool_registry
        self._assistant_inputs = {}
        self._assistant_input_lock = threading.Lock()
        self._session: object | None = None
        self._chat_boundary: object | None = None
        self._closed = False
        self._loaded = threading.Event()
        self._bound = threading.Event()
        self._model_configuration_issue = None
        try:
            migrate_legacy_model_configuration(roots.user_root)
        except (OSError, ValueError):
            self._model_configuration_issue = "CONFIG_DATA_INVALID"
        from app.plugins.bundled_migrations import migrate_bundled_plugins

        if specs is None:
            migrate_bundled_plugins(roots)
        self._inventory = PluginInventory(roots)
        self._inventory_snapshot = self._inventory.scan()
        manager_options = {} if call_timeout is None else {"call_timeout": call_timeout}
        self._manager = PluginRuntimeManager(
            roots,
            generation_id,
            self._inventory_snapshot.runtime_specs if specs is None else specs,
            before_start=self._prepare_plugin,
            **manager_options,
        )
        self.visuals = VisualHost(self._manager, inventory=self.inventory)
        self._visual_character = None
        self._visual_binding = None
        self._visual_reason = "VISUAL_NOT_BOUND"
        self._visual_state_lock = threading.RLock()
        self._visual_selector = None
        self.audio_input = AudioInputResources(roots.user_root, generation_id, self._manager.service_identity)
        self._manager.install_host_service(
            HOST_AUDIO_INPUT_SERVICE, self.audio_input,
            exports=("verifyProvider", "authorize", "acquire", "release", "revoke"),
        )
        self._character_store = PluginCharacterStore(roots.user_root)
        self._host_services = PluginHostServices(
            tool_registry,
            artifact_store=PluginArtifactStore(roots.user_root, generation_id),
            character_store=self._character_store,
            timeline_store=TimelineStore(StoragePaths(roots.user_root).timeline_database()),
            current_character_id=self._current_character_id,
            invoke_callback=self._manager.invoke_callback,
            encode_context_request=_context_request_mapping,
            on_context_change=self._host_context_changed,
            storage_root=roots.user_root,
            model_resolver=self._resolve_model,
            active_model_resolver=self.active_models,
            commit_plugin_scope=self._manager.commit_plugin_scope,
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
        commit_scope = lambda owner, commit: self._manager.commit_plugin_scope(*owner, commit)
        self.screen = ScreenHost(generation_id, commit_scope=commit_scope,
            session_provider=lambda: self._chat_boundary.current_host_state()["sessionId"] if self._chat_boundary else None,
            emit_callback=self._emit_desktop_event)
        self.chat = ChatHost(boundary_provider=lambda: self._chat_boundary,
            screen_host=self.screen, emit_callback=self._emit_desktop_event, commit_scope=commit_scope)
        self._manager.install_host_service(HOST_SCREEN_SERVICE, self.screen, exports=("capture", "release", "release_capture"))
        self._manager.install_host_service(HOST_CHAT_SERVICE, self.chat, exports=("current", "submit", "cancel"))
        self.visual_controls = HostVisualService(binding_provider=self._current_visual_binding,
            emit_callback=self._emit_desktop_event, is_idle=lambda: self.chat.current()["idle"],
            select_callback=self._select_visual_resource, commit_scope=commit_scope)
        self._manager.install_host_service(HOST_VISUAL_SERVICE, self.visual_controls,
            exports=("current", "apply", "status", "release", "select"))

    def _current_visual_binding(self):
        with self._visual_state_lock:
            return (self._visual_character.id, self._visual_binding) if self._visual_character else None

    def bind_visual_selector(self, callback):
        self._visual_selector = callback

    def _select_visual_resource(self, target, resource_id):
        if self._visual_selector is None:
            raise PluginRuntimeError("VISUAL_SELECTION_UNAVAILABLE")
        from app.core_host.real_chat import RealChatRejection
        state = self.chat.current()
        if not state["idle"] or self._chat_boundary is None:
            return {"accepted": False, "reasonCode": "VISUAL_BUSY"}
        try:
            with self._chat_boundary.idle_runtime_update():
                return self._visual_selector(target, resource_id, expected_visual_activity=state)
        except RealChatRejection as error:
            return {"accepted": False, "reasonCode": error.code}

    def commit_visual_selection(self, target, commit, *, expected_activity=None):
        """Commit a local preference while the caller and display target are current."""
        from app.core_host.screen_host import caller_identity
        owner = caller_identity()
        value = self._current_visual_binding()
        if value is None or value[1] is None or {
            "characterId": value[0], "bindingId": value[1].id,
            "resourceId": value[1].resource_id,
        } != target:
            raise PluginRuntimeError("VISUAL_BINDING_EXPIRED")
        def publish():
            value[1]._check_active()
            return commit()
        def commit_scope():
            return self._manager.commit_plugin_scope(*owner, publish)
        return self.visuals.commit_current(value[1],
            lambda: self.chat.commit_idle(expected_activity, commit_scope) if expected_activity is not None else commit_scope())

    def _emit_desktop_event(self, name, payload):
        if self._chat_boundary is None:
            raise PluginRuntimeError("DESKTOP_UNAVAILABLE")
        self._chat_boundary.publish_host_event(name, payload)

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

    def _prepare_plugin(self, spec) -> None:
        if spec.plugin_id == "sakura.screen_awareness":
            try:
                migrate_legacy_screen_settings(self._roots.user_root)
            except Exception as error:
                failure = PluginRuntimeError("SCREEN_SETTINGS_MIGRATION_FAILED", plugin_id=spec.plugin_id)
                failure.recovery_error = getattr(error, "recovery_error", None)
                raise failure from error

    def start(self) -> None:
        if self._closed:
            raise PluginRuntimeError("GENERATION_INVALIDATED")
        try:
            self._manager.start()
            self._manager.notify_host_event(
                "sakura.host.app.started",
                {"generationId": self._generation_id},
            )
        except BaseException as error:
            if not self._closed:
                log_event("Plugin", "插件启动未完成", exception_diagnostics(
                    error, reason_code="PLUGIN_START_FAILED", stage="plugin.start",
                ), event="plugin.start.failed", severity="error")
            raise
        finally:
            self._loaded.set()

    def start_character_presentation(self) -> dict[str, object] | None:
        """Start only the selected visual provider and its hard dependencies."""
        from app.config.character_loader import CharacterRegistry
        from app.config.core_config_reader import CoreConfigReader

        config = CoreConfigReader().read(self._roots.user_root)
        if config.current_character_id is None:
            return None
        registry = CharacterRegistry(self._roots.user_root)
        character = registry.profiles.get(config.current_character_id)
        if character is None:
            return None
        settings = AppSettingsService(self._roots.user_root)
        resource = settings.selected_visual_resource(character)
        service = self.visuals.startup_service(resource.type, character.visual_providers.get(resource.id)) if resource is not None else None
        self._manager.start(services=(service,) if service is not None else ())
        self.bind_visual_character(character)
        return self.visual_presentation()

    def start_assistant(self) -> None:
        services = {ref["serviceKey"] for ref in self.active_models().values() if ref}
        self._manager.start(services=(*sorted(services), "sakura.assistant"))

    def wait_until_loaded(self, *, timeout: float = 8.0) -> bool:
        return self._loaded.wait(max(0.0, timeout)) and not self._closed

    def public_snapshot(self) -> dict[str, Any]:
        snapshot = self._manager.snapshot()
        if self._closed:
            snapshot["state"] = "stopped"
            snapshot["reasonCode"] = "PLUGIN_RUNTIME_STOPPED"
        return snapshot

    def inventory(self) -> PluginInventorySnapshot:
        return self._inventory_snapshot

    def refresh_inventory(self) -> PluginInventorySnapshot:
        self._inventory_snapshot = self._inventory.scan()
        return self._inventory_snapshot

    def marketplace_context(self) -> dict[str, Any]:
        return {"api": 4, "services": self._manager.available_service_keys()}

    def settings_snapshot(self) -> dict[str, Any]:
        return self._host_services.decorate_settings_snapshot(self._manager.snapshot())

    def call_service(self, service_key: str, method: str, *args: object) -> object:
        return self._manager.call_service(service_key, method, *args)

    def call_bound_service(self, service_key, identity, method, *args, timeout=None):
        return self._manager.call_bound_service(service_key, identity, method, *args, timeout=timeout)

    def commit_bound_service(self, service_key, identity, commit):
        return self._manager.commit_bound_service(service_key, identity, commit)

    def abort_bound_service(self, service_key, identity, *, reason):
        stopped = self._manager.abort_bound_service(service_key, identity, reason=reason)
        with self._assistant_input_lock:
            artifact_ids = [artifact_id for artifact_id, (owner, _) in self._assistant_inputs.items()
                            if owner == identity]
        for artifact_id in artifact_ids:
            self.release_assistant_input(artifact_id)
        return stopped

    def service_identity(self, service_key: str) -> dict[str, str]:
        return self._manager.service_identity(service_key)

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
        self._manager.notify_host_event(event_name, dict(payload))

    def bind_session(self, session, *, prepared_character=None):
        character_id = getattr(getattr(session, "character", None), "id", None)
        if not character_id or getattr(session, "assistant", None) is None:
            raise PluginRuntimeError("PLUGIN_SESSION_INVALID")
        if self._closed:
            raise PluginRuntimeError("GENERATION_INVALIDATED")
        if self._session is session:
            return
        candidate = prepared_character or self.prepare_character(session.character)
        try:
            identity = getattr(session.assistant, "identity", None)
            if identity is not None and self.service_identity("sakura.assistant") != identity:
                raise PluginRuntimeError("SERVICE_BINDING_EXPIRED")
            for slot, model_identity in getattr(session, "model_bindings", {}).items():
                if self.service_identity(session.model_slots[slot]["serviceKey"]) != model_identity:
                    raise PluginRuntimeError("SERVICE_BINDING_EXPIRED")
            self.publish_character(candidate)
        except BaseException:
            candidate.close()
            raise
        self._session = session
        self.chat.invalidate_session()
        self.screen.invalidate_session()
        self._tool_registry.set_event_emitter(lambda name, payload: self.emit_event(name, payload or {}))
        session.visual_binding = candidate.binding
        self._bound.set()

    def create_assistant_input(self, identity, payload):
        import json
        if self.service_identity("sakura.assistant") != identity:
            raise PluginRuntimeError("SERVICE_BINDING_EXPIRED")
        request = dict(payload)
        grant = self._host_services.grant_history(identity["providerId"], request["session"]["character"]["id"], request.get("historyCursor"))
        request["historyToken"] = grant["historyToken"]
        request["historyCursor"] = grant["snapshotCursor"]
        try:
            descriptor = self._host_services.create_json_artifact(identity["providerId"], request)
        except BaseException:
            self._host_services.revoke_history(grant["historyToken"])
            raise
        with self._assistant_input_lock:
            self._assistant_inputs[descriptor["artifactId"]] = (dict(identity), grant["historyToken"])
        return descriptor

    def read_assistant_result(self, descriptor):
        import json
        artifact = self._host_services.resolve_committed_artifact(descriptor["artifactId"])
        payload = artifact.path.read_bytes()
        if len(payload) != descriptor["byteLength"]:
            raise PluginRuntimeError("ASSISTANT_RESULT_INVALID")
        return json.loads(payload)

    def release_assistant_input(self, artifact_id):
        with self._assistant_input_lock:
            identity = self._assistant_inputs.pop(artifact_id, None)
        if identity is not None:
            self._host_services.revoke_history(identity[1])
            self._host_services.release_owned_artifact(identity[0]["providerId"], artifact_id)

    def bind_visual_character(self, character) -> None:
        candidate = self.prepare_character(character)
        try:
            self.publish_character(candidate)
        except BaseException:
            candidate.close()
            raise

    def prepare_character(self, character, *, strict=False):
        from app.core_host.character_presentation import project_character_presentation
        from app.core_host.visual_host import VisualHostError
        binding = None
        reason = "VISUAL_RESOURCE_MISSING"
        resource = AppSettingsService(self._roots.user_root).selected_visual_resource(character)
        try:
            revision, binding = self.visuals.prepare(character.id, character.package_dir, resource,
                provider_id=character.visual_providers.get(resource.id) if resource is not None else None)
            if binding is not None:
                reason = "READY"
        except VisualHostError as error:
            reason = error.code
            if reason not in VISUAL_INACTIVE_REASONS:
                log_event("Visual", "角色表现加载失败", exception_diagnostics(
                    error, reason_code=reason, stage="visual.bind",
                ), event="visual.binding.failed", severity="warning")
            if strict and (reason not in VISUAL_INACTIVE_REASONS or reason == "VISUAL_BINDING_EXPIRED"):
                raise
            revision, binding = self.visuals.prepare(character.id, character.package_dir, None)
        try:
            presentation = project_character_presentation(character,
                binding.presentation() if binding is not None else None, reason_code=reason)
            return PreparedCharacter(character, revision, binding, reason, presentation)
        except BaseException:
            if binding is not None:
                binding.close()
            raise

    def publish_character(self, candidate):
        if self._closed:
            raise PluginRuntimeError("GENERATION_INVALIDATED")
        self.visuals.publish(candidate.revision, candidate.binding)
        with self._visual_state_lock:
            self._visual_character = candidate.character
            self._visual_binding = candidate.binding
            self._visual_reason = candidate.reason
        self.visual_controls.invalidate_target()
        self._character_store.set_current(candidate.character.id)
        if self._session is not None:
            self._session.visual_binding = self._visual_binding

    def visual_presentation(self):
        from app.core_host.character_presentation import project_character_presentation
        from app.core_host.visual_host import VisualHostError
        if self._visual_character is None:
            return None
        visual = None
        reason = self._visual_reason
        try:
            visual = self.visuals.presentation()
        except VisualHostError as error:
            reason = error.code
        return project_character_presentation(self._visual_character, visual, reason_code=reason)

    def preview_character_presentation(self, character):
        from app.core_host.character_presentation import project_character_presentation
        from app.core_host.visual_host import VisualHost, VisualHostError
        previous = getattr(self, "_preview_visuals", None)
        if previous is not None:
            previous.close()
        host = VisualHost(self._manager, inventory=self.inventory)
        self._preview_visuals = host
        visual, reason = None, "VISUAL_RESOURCE_MISSING"
        resource = AppSettingsService(self._roots.user_root).selected_visual_resource(character)
        if resource is not None:
            try:
                visual = host.bind(character.id, character.package_dir, resource, provider_id=character.visual_providers.get(resource.id)).presentation()
                reason = "READY"
            except VisualHostError as error:
                reason = error.code
                if reason not in VISUAL_INACTIVE_REASONS:
                    log_event("Visual", "角色预览加载失败", exception_diagnostics(
                        error, reason_code=reason, stage="visual.preview",
                    ), event="visual.preview.failed", severity="warning")
        return project_character_presentation(character, visual, reason_code=reason)

    def validate_visual_choice(self, character, resource):
        host = VisualHost(self._manager, inventory=self.inventory)
        try:
            host.bind(character.id, character.package_dir, resource, provider_id=character.visual_providers.get(resource.id))
        finally:
            host.close()

    def validate_visual_draft(self, character):
        from app.core_host.visual_host import VisualHostError
        for resource in character.visual_resources:
            try:
                record, capability = self.visuals._select(resource.type, character.visual_providers.get(resource.id))
            except VisualHostError as error:
                if error.code in VISUAL_INACTIVE_REASONS:
                    continue
                raise
            token = self._host_services.grant_visual_workspace(record.plugin_id, character.package_dir)
            try:
                binding = self.visuals._describe(record, capability, {"characterId": token, "resource": resource.to_mapping()}, character.package_dir)
                binding.close()
            finally:
                self._host_services.revoke_visual_workspace(token)

    def export_visual_resource(self, character, resource):
        import json
        from app.plugins.visuals import resolve_resource_path
        record, capability = self.visuals._select(resource.type, character.visual_providers.get(resource.id))
        token = self._host_services.grant_visual_workspace(record.plugin_id, character.package_dir)
        try:
            request = {"characterId": token, "resource": resource.to_mapping()}
            binding = self.visuals._describe(record, capability, request, character.package_dir)
            raw = json.loads((resolve_resource_path(character.package_dir, resource.root) / resource.entry).read_text(encoding="utf-8"))
            result = self._manager.call_service(capability.service, "exportResource", resource.to_mapping(), raw)
            result["assets"] = binding.description.get("assets", {})
            result["pluginRequirements"] = list(resource.plugin_requirements) or [{"kind": "visual", "type": resource.type, "plugins": [{"id": record.plugin_id, "name": record.name}]}]
            binding.close()
            return result
        finally:
            self._host_services.revoke_visual_workspace(token)

    def unbind_session(self) -> None:
        character = self._visual_character
        self.visuals.clear()
        with self._visual_state_lock:
            self._visual_binding = None
            self._visual_character = None
        self.visual_controls.invalidate_target()
        self.retire_session()

        # A missing model configuration removes chat, not the character window.
        # Revoke in-flight controls while issuing an independent display binding.
        if character is not None and not self._closed:
            self.bind_visual_character(character)

    def retire_session(self) -> None:
        """Remove chat after an independently prepared display was published."""
        registry = self._tool_registry
        if self._session is not None:
            self._session.visual_binding = None
        self._session = None
        self.chat.invalidate_session()
        self.screen.invalidate_session()
        self._bound.clear()
        if hasattr(registry, "set_event_emitter"):
            registry.set_event_emitter(None)

    def wait_until_bound(self, *, timeout: float = 8.0) -> bool:
        return self._bound.wait(max(0.0, timeout)) and not self._closed

    def bind_chat_boundary(self, boundary: object) -> None:
        self._chat_boundary = boundary

    def pause_service_providers(self, prefix: str):
        return self._manager.pause_service_providers(prefix)

    @contextmanager
    def prepare_voice_resources(self):
        hot, fallback = [], []
        for item in self._manager.snapshot()["plugins"]:
            services = [key for key in item["provides"] if key.startswith("sakura.tts.provider.")]
            if item["state"] != "active" or not services:
                continue
            if all({"prepareResourceUpdate", "finishResourceUpdate"} <= self._manager.service_exports(key) for key in services):
                hot.extend(services)
            else:
                fallback.append(item["pluginId"])
        with self._manager.pause_plugins(fallback) as errors:
            prepared = []
            try:
                for key in hot:
                    prepared.append(key)
                    if self._manager.call_service(key, "prepareResourceUpdate") is not True:
                        raise PluginRuntimeError("TTS_RESOURCE_UPDATE_BUSY")
                yield errors
            finally:
                for key in prepared:
                    try:
                        if self._manager.call_service(key, "finishResourceUpdate") is not True:
                            raise PluginRuntimeError("TTS_RESOURCE_UPDATE_FAILED")
                    except Exception as error:
                        errors.append(error)

    def prepare_character_switch(self):
        records = self._manager.snapshot()["plugins"]
        ids = [item["pluginId"] for item in records if item["state"] == "active"
               and ({"sakura.host.character", "sakura.host.timeline"} & set(item["requires"]))
               and not any(key == "sakura.tts" or key == "sakura.assistant" or key.startswith(("sakura.tts.provider.", "sakura.visual.")) for key in item["provides"])]
        return self._manager.pause_plugins(ids)

    @contextmanager
    def plugin_update(self, plugin_id: str):
        guard = self._chat_boundary.idle_runtime_update() if self._chat_boundary is not None else nullcontext()
        with guard, self._manager.plugin_update(plugin_id) as dependents:
            yield dependents

    def restore_update_dependents(self, plugin_ids: list[str]) -> None:
        self._manager.restore_update_dependents(plugin_ids)
        for plugin_id in plugin_ids:
            self._refresh_visual_provider(plugin_id)

    def set_plugin_enabled(self, plugin_id: str, enabled: bool) -> dict[str, Any]:
        result = self._manager.set_enabled(plugin_id, enabled)
        self._refresh_visual_provider(plugin_id)
        return result

    def install_plugin(self, spec: RuntimePluginSpec) -> dict[str, Any]:
        self.refresh_inventory()
        result = self._manager.install_plugin(spec)
        self._refresh_visual_provider(spec.plugin_id)
        return result

    def uninstall_plugin(self, plugin_id: str) -> dict[str, Any]:
        self.refresh_inventory()
        result = self._manager.uninstall_plugin(plugin_id)
        self._refresh_visual_provider(plugin_id)
        return result

    def reload_plugin(self, plugin_id: str) -> dict[str, Any]:
        self.refresh_inventory()
        result = self._manager.reload_plugin(plugin_id)
        self._refresh_visual_provider(plugin_id)
        return result

    def _refresh_visual_provider(self, plugin_id: str) -> None:
        character = self._visual_character
        resource = AppSettingsService(self._roots.user_root).selected_visual_resource(character) if character is not None else None
        if resource is None:
            return
        binding = self._visual_binding
        if (binding is not None and binding.provider_id == plugin_id
            or any(item["pluginId"] == plugin_id for item in self.visuals.candidates(resource.type))):
            self.bind_visual_character(character)

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
        return self._apply_settings_result(plugin_id, result)

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
        return self._apply_settings_result(plugin_id, result)

    def _apply_settings_result(self, plugin_id, result):
        if not isinstance(result, Mapping) or result.get("applicationState") != "restart_required":
            return result
        from .real_chat import RealChatRejection

        guard = self._chat_boundary.idle_runtime_update() if self._chat_boundary is not None else nullcontext()
        try:
            with guard:
                snapshot = self.reload_plugin(plugin_id)
        except RealChatRejection as error:
            if error.code != "RUNTIME_UPDATE_BUSY":
                raise
            raise PluginRuntimeError("PLUGIN_RELOAD_BUSY", "请结束当前对话后应用设置。", plugin_id=plugin_id) from error
        record = next((item for item in snapshot["plugins"] if item["pluginId"] == plugin_id), None)
        active = record is not None and record["state"] == "active"
        return {**result, "applicationState": "applied" if active else "error",
                "reasonCode": "READY" if active else (record["reasonCode"] if record else "PLUGIN_NOT_FOUND")}

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
        self.chat.close()
        self.screen.close()
        self.visual_controls.close()
        self.visuals.close()
        preview = getattr(self, "_preview_visuals", None)
        if preview is not None:
            preview.close()
        self.unbind_session()
        self._manager.close()
        with self._assistant_input_lock:
            artifact_ids = list(self._assistant_inputs)
        for artifact_id in artifact_ids:
            self.release_assistant_input(artifact_id)
        self.audio_input.close()
        self._host_services.clear()
        self._loaded.set()

    def _current_character_id(self) -> str | None:
        character_id = getattr(getattr(self._session, "character", None), "id", None)
        return character_id if isinstance(character_id, str) and character_id else None

    def _host_context_changed(self, providers):
        # Each Assistant freezes the public catalog when accepting a turn.
        return None

    def _model_catalog(self) -> list[dict[str, object]]:
        return self._host_services.model_catalog()

    def model_catalog(self):
        return self._model_catalog()

    def active_models(self):
        try:
            return ModelReferenceRepository(self._roots.user_root).active()
        except (OSError, ValueError):
            return {"chat": None, "vision_chat": None}

    def model_configuration_issue(self):
        repository = ModelReferenceRepository(self._roots.user_root)
        try:
            repository.load()
        except (OSError, ValueError):
            return "CONFIG_DATA_INVALID"
        return self._model_configuration_issue if not repository.path.exists() else None

    def _resolve_model(self, selection: Mapping[str, Any]) -> dict[str, object]:
        reference = model_reference(selection)
        return reference if reference["serviceKey"] else dict(self.active_models()["chat"] or EMPTY_REFERENCE)


def _context_request_mapping(request: ContextRequest) -> dict[str, Any]:
    value = asdict(request)
    value["recent_messages"] = [dict(item) for item in value.get("recent_messages", [])]
    return value


__all__ = ["PluginRuntimeApplication"]
