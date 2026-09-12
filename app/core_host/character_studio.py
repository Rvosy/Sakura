"""Runtime v2 character Studio boundary owned by one Core generation."""

from __future__ import annotations

import hmac
import re
import threading
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Callable

from app.config.character_loader import CharacterConfigError, CharacterRegistry
from app.config.character_studio import (
    CharacterStudioOperationCancelled,
    CharacterStudioService,
)
from app.config.models import DEFAULT_THEME_SETTINGS, THEME_COLOR_FIELDS, theme_to_mapping
from app.config.settings_service import AppSettingsService
from app.core_host.protocol import error_payload, response
from app.core.diagnostics import exception_diagnostics
from app.core.runtime_log import log_event
from app.core_host.visual_host import VisualHostError, VISUAL_INACTIVE_REASONS
from app.plugins.runtime_v4 import PluginRuntimeError


CHARACTER_STUDIO_REQUEST_NAMES = frozenset(
    {
        "studio.bootstrap",
        "studio.plugin.requirements",
        "studio.character.presentation",
        "studio.visual.catalog",
        "studio.visual.previews",
        "studio.visual.open",
        "studio.visual.create",
        "studio.visual.export",
        "studio.visual.import",
        "studio.character.open",
        "studio.character.create",
        "studio.character.publish",
        "studio.draft.save",
        "studio.draft.discard",
        "studio.workspace.release",
        "studio.asset.import",
        "studio.reference.preview",
        "studio.archive.export",
        "studio.operation.cancel",
    }
)
_DOC_FIELDS = frozenset(
    {
        "id",
        "displayName",
        "initialMessage",
        "cardText",
        "defaultPortrait",
        "expressions",
        "replyTones",
        "theme",
        "voice",
        "referenceAudios",
        "visuals",
        "visualData",
    }
)
_VOICE_FIELDS = frozenset(
    {"toneRefs", "gptModel", "sovitsModel", "refLang", "textLang"}
)
_REFERENCE_AUDIO_FIELDS = frozenset({"audioPath", "refLang", "refText", "tone"})
_THEME_FIELDS = frozenset(
    {
        field.split("_")[0]
        + "".join(part[:1].upper() + part[1:] for part in field.split("_")[1:])
        for field, _label, _default in THEME_COLOR_FIELDS
    }
    | {"aiEnabled", "visualEffectMode"}
)
_OPERATION_ID_RE = re.compile(r"^[A-Za-z0-9-]{8,64}$")


class _StudioOperation:
    def __init__(self, operation_id: str) -> None:
        self.id = operation_id
        self.cancel = threading.Event()
        self.phase = "copying"


class CharacterStudioError(ValueError):
    def __init__(self, code: str, message: str, *, field: str = "") -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.field = field

    def public_error(self) -> dict[str, object]:
        payload = error_payload(self.code, self.message)
        payload["retryable"] = self.code in {"STUDIO_IO_FAILED", "STUDIO_CORE_BUSY"}
        payload["details"] = {"feature": "character.studio", "field": self.field}
        return payload


class CharacterStudioBoundary:
    """Expose the existing draft service without leaking workspace paths to WebViews."""

    def __init__(
        self,
        generation_id: str,
        generation_credential: str,
        user_root: Path,
        *,
        quiesce_generation: Callable[[], None] | None = None,
        plugin_application_provider: Callable[[], object | None] = lambda: None,
    ) -> None:
        self._generation_id = generation_id
        self._generation_credential = generation_credential
        self._user_root = Path(user_root)
        self._settings = AppSettingsService(self._user_root)
        self._service_instance: CharacterStudioService | None = None
        self._service_init_lock = threading.Lock()
        self._mutation_lock = threading.Lock()
        self._operation_lock = threading.Lock()
        self._active_operation: _StudioOperation | None = None
        self._quiesce_generation = quiesce_generation
        self._generation_invalidated = False
        self._plugin_application_provider = plugin_application_provider

    @property
    def _service(self) -> CharacterStudioService:
        service = self._service_instance
        if service is not None:
            return service
        with self._service_init_lock:
            service = self._service_instance
            if service is None:
                service = CharacterStudioService(self._user_root, validate_visuals=self._validate_visuals)
                self._service_instance = service
            return service

    def _validate_visuals(self, character):
        application = self._plugin_application_provider()
        if application is not None:
            application.application.validate_visual_draft(character)

    def handle(self, request: dict[str, Any]) -> dict[str, Any]:
        supplied = request.get("generationCredential")
        if (
            request.get("generationId") != self._generation_id
            or not isinstance(supplied, str)
            or not hmac.compare_digest(supplied, self._generation_credential)
        ):
            raise RuntimeError("GENERATION_IDENTITY_MISMATCH")
        try:
            name = request.get("name")
            payload = request.get("payload")
            if not isinstance(payload, Mapping):
                raise CharacterStudioError("STUDIO_REQUEST_INVALID", "角色工坊请求格式无效。")
            result = self._dispatch(str(name or ""), dict(payload))
            return response(
                request,
                generation_id=self._generation_id,
                generation_credential=self._generation_credential,
                protocol_minor=2,
                payload=result,
            )
        except CharacterStudioError as error:
            public_error = error.public_error()
            if self._generation_invalidated:
                public_error["details"]["generationInvalidated"] = True
            return response(
                request,
                generation_id=self._generation_id,
                generation_credential=self._generation_credential,
                protocol_minor=2,
                error=public_error,
            )
        except CharacterStudioOperationCancelled:
            return response(
                request,
                generation_id=self._generation_id,
                generation_credential=self._generation_credential,
                protocol_minor=2,
                error=CharacterStudioError(
                    "STUDIO_OPERATION_CANCELLED",
                    "操作已取消，临时文件已清理。",
                ).public_error(),
            )
        except (VisualHostError, PluginRuntimeError) as error:
            public_error = CharacterStudioError(error.code, "表现资源操作失败，请查看运行日志。").public_error()
            if self._generation_invalidated:
                public_error["details"]["generationInvalidated"] = True
            return response(
                request, generation_id=self._generation_id,
                generation_credential=self._generation_credential, protocol_minor=2,
                error=public_error,
            )
        except (CharacterConfigError, OSError, ValueError) as error:
            public_error = CharacterStudioError(
                "STUDIO_OPERATION_FAILED",
                str(error) or "角色工坊操作失败。",
            ).public_error()
            if self._generation_invalidated:
                public_error["details"]["generationInvalidated"] = True
            return response(
                request,
                generation_id=self._generation_id,
                generation_credential=self._generation_credential,
                protocol_minor=2,
                error=public_error,
            )

    def _dispatch(self, name: str, payload: dict[str, Any]) -> dict[str, Any]:
        current = self._current_character_id()
        if name == "studio.plugin.requirements":
            from app.config.character_studio import CharacterStudioDoc, _merge_character_manifest
            from app.config.plugin_requirements import requirements_for_manifest, check_requirements
            self._keys(payload, required={"workspaceId"})
            workspace = self._text(payload["workspaceId"])
            with self._mutation_lock:
                state = self._service._require_state(workspace)
                doc = CharacterStudioDoc.from_payload(state["doc"])
                manifest = _merge_character_manifest(self._service._workspace_package(workspace), doc)
            application = self._plugin_application_provider()
            if application is None:
                raise CharacterStudioError("STUDIO_CORE_UNAVAILABLE", "插件状态暂不可用。")
            return {"schemaVersion": 1, "items": check_requirements(requirements_for_manifest(manifest), application.inventory().records)}
        if name.startswith("studio.visual."):
            return self._visual_request(name, payload)
        if name == "studio.character.presentation":
            self._keys(payload, required={"characterId"})
            application = self._plugin_application_provider()
            if application is None:
                raise CharacterStudioError("STUDIO_CORE_UNAVAILABLE", "角色表现暂不可用。")
            result = application.preview_character_presentation(self._text(payload["characterId"]))
            return {**result, "generationId": self._generation_id}
        if name == "studio.bootstrap":
            self._keys(payload, optional={"initialCharacterId"})
            characters = self._service.list_characters(current_character_id=current)
            requested = self._text(payload.get("initialCharacterId"), required=False)
            ids = {str(item.get("id") or "") for item in characters}
            selected = (
                requested
                if requested in ids
                else current
                if current in ids
                else str(characters[0].get("id") or "")
                if characters
                else ""
            )
            return {
                "schemaVersion": 1,
                "selectedCharacterId": selected or None,
                "currentCharacterId": current or None,
                "characters": [_summary_to_public(item) for item in characters],
                "themeDefaults": _keys_to_camel(theme_to_mapping(DEFAULT_THEME_SETTINGS)),
                "themeFields": [
                    {"id": _snake_to_camel(field), "label": label}
                    for field, label, _default in THEME_COLOR_FIELDS
                ],
            }
        if name == "studio.character.open":
            self._keys(payload, required={"characterId"})
            return _opened_to_public(
                self._service.open_character(self._text(payload["characterId"])), current
            )
        if name == "studio.character.create":
            self._keys(payload, required={"doc"})
            doc = _doc_to_internal(self._mapping(payload["doc"], "doc"))
            with self._mutation_lock:
                return _opened_to_public(self._service.create_character(doc), current)
        if name == "studio.draft.save":
            self._keys(payload, required={"workspaceId", "doc"})
            with self._mutation_lock:
                result = self._service.save_workspace_draft(
                    self._text(payload["workspaceId"]),
                    _doc_to_internal(self._mapping(payload["doc"], "doc")),
                )
            return _draft_to_public(result)
        if name == "studio.draft.discard":
            self._keys(payload, required={"workspaceId"})
            with self._mutation_lock:
                result = self._service.discard_draft(
                    self._text(payload["workspaceId"]), current_character_id=current
                )
            return _opened_to_public(result, current) if result.get("doc") else {
                "schemaVersion": 1,
                "discardedCharacterId": str(result.get("discarded_character_id") or ""),
                "wasInstalled": bool(result.get("was_installed")),
                "characters": [_summary_to_public(item) for item in result.get("characters", [])],
            }
        if name == "studio.workspace.release":
            self._keys(payload, required={"workspaceId"})
            with self._mutation_lock:
                result = self._service.release_workspace(self._text(payload["workspaceId"]))
            return {"schemaVersion": 1, "released": bool(result.get("released"))}
        if name == "studio.character.publish":
            self._keys(payload, required={"workspaceId", "doc"}, optional={"operationId"})
            workspace_id = self._text(payload["workspaceId"])
            operation = self._begin_operation(payload)
            try:
                with self._mutation_lock:
                    result = self._service.save_character(
                        _doc_to_internal(self._mapping(payload["doc"], "doc")),
                        workspace_id,
                        current_character_id=current,
                        cancel_check=self._cancel_check(operation),
                        commit_started=self._commit_started(operation),
                        quiesce_current=self._quiesce_current_generation,
                    )
            finally:
                self._finish_operation(operation)
            public = _opened_to_public(result, current)
            public.update(
                {
                    "savedCharacterId": str(result.get("saved_character_id") or ""),
                    "currentCharacterId": current or None,
                    "changePlan": (
                        "core_restart_required"
                        if result.get("changed") and result.get("saved_character_id") == current
                        else "unchanged"
                    ),
                    "message": str(result.get("message") or ""),
                }
            )
            return public
        if name == "studio.asset.import":
            return self._import_asset(payload, current)
        if name == "studio.reference.preview":
            self._keys(payload, required={"workspaceId", "relativePath"})
            descriptor = self._service.describe_reference_audio_preview(
                self._text(payload["workspaceId"]), self._text(payload["relativePath"])
            )
            return {
                "schemaVersion": 1,
                "sourcePath": descriptor["source_path"],
                "mediaType": descriptor["mime_type"],
                "byteLength": descriptor["byte_length"],
            }
        if name == "studio.archive.export":
            self._keys(
                payload,
                required={"workspaceId", "path", "includeVoice"},
                optional={"operationId"},
            )
            if not isinstance(payload["includeVoice"], bool):
                raise CharacterStudioError("STUDIO_REQUEST_INVALID", "includeVoice 必须是布尔值。")
            operation = self._begin_operation(payload)
            try:
                with self._mutation_lock:
                    result = self._service.export_archive(
                        self._text(payload["workspaceId"]),
                        Path(self._text(payload["path"])),
                        include_voice=payload["includeVoice"],
                        cancel_check=self._cancel_check(operation),
                        commit_started=self._commit_started(operation),
                    )
            finally:
                self._finish_operation(operation)
            return {
                "schemaVersion": 1,
                "outputPath": str(result.get("output_path") or ""),
                "message": str(result.get("message") or ""),
            }
        if name == "studio.operation.cancel":
            self._keys(payload, required={"operationId"})
            operation_id = self._operation_id(payload.get("operationId"), required=True)
            with self._operation_lock:
                operation = self._active_operation
                if operation is None or operation.id != operation_id:
                    return {"schemaVersion": 1, "cancelled": False, "state": "not_found"}
                if operation.phase == "committing":
                    return {"schemaVersion": 1, "cancelled": False, "state": "finalizing"}
                operation.cancel.set()
                return {"schemaVersion": 1, "cancelled": True, "state": "cancel_requested"}
        raise CharacterStudioError("STUDIO_COMMAND_UNKNOWN", "不支持的角色工坊命令。")

    def _visual_request(self, name, payload):
        operation = self._begin_operation(payload) if name in {"studio.visual.import", "studio.visual.export"} else None
        try:
            return self._visual_request_impl(name, payload, operation)
        finally:
            self._finish_operation(operation)

    def _visual_request_impl(self, name, payload, operation):
        import uuid
        from app.config.character_resources import CharacterVisualResource, character_visual_resources
        from app.config.character_studio import CharacterStudioDoc
        from app.core_host.character_presentation import project_character_presentation
        from app.plugins.visuals import resolve_resource_path
        application = self._plugin_application_provider()
        if application is None:
            raise CharacterStudioError("STUDIO_CORE_UNAVAILABLE", "表现插件暂不可用。")
        host = application.application.visuals
        if name == "studio.visual.catalog":
            self._keys(payload)
            return {"schemaVersion": 1, "items": host.catalog()}
        self._keys(payload, required={"workspaceId"}, optional={"resourceId", "type", "providerId", "path", "operationId", "relativePath", "name"})
        workspace = self._text(payload["workspaceId"])
        with self._mutation_lock:
            state = self._service._require_state(workspace)
            doc = CharacterStudioDoc.from_payload(state["doc"])
            package = self._service._workspace_package(workspace)
            resources, selected = character_visual_resources(doc.to_manifest(), package)
            if name == "studio.visual.previews":
                from app.core_host.visual_host import VisualHostError
                from app.plugins.runtime_v4 import PluginRuntimeError
                items = []
                for resource in resources:
                    item = {"resourceId": resource.id, "relativePath": None}
                    try:
                        editor_resource, raw = self._visual_editor_input(package, doc, resource)
                        relative = host.preview_image(editor_resource, raw, (doc.visuals or {}).get("providers", {}).get(resource.id))
                        if relative:
                            path = resolve_resource_path(resolve_resource_path(package, resource.root), relative)
                            media_type = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp", ".gif": "image/gif"}.get(path.suffix.lower())
                            size = path.stat().st_size
                            if path.is_file() and media_type and 0 < size <= 20 * 1024 * 1024:
                                item.update(relativePath=relative, sourcePath=str(path), mediaType=media_type, byteLength=size)
                    except (VisualHostError, PluginRuntimeError, ValueError, OSError) as error:
                        # Covers are optional; one unavailable provider or image
                        # must not hide the other cards or block their editors.
                        code = getattr(error, "code", "VISUAL_PREVIEW_FAILED")
                        if code not in VISUAL_INACTIVE_REASONS:
                            log_event("Visual", "形态封面加载失败", exception_diagnostics(
                                error, reason_code=code, stage="studio.visual.previews",
                            ), event="visual.preview.failed", severity="warning")
                    items.append(item)
                return {"schemaVersion": 1, "items": items}
            if name == "studio.visual.import":
                import shutil
                from app.config.visual_archive import import_visual_archive
                resource = import_visual_archive(Path(self._text(payload.get("path"))), package, cancel_check=self._cancel_check(operation), commit_started=self._commit_started(operation))
                try:
                    visual_config = dict(doc.visuals or {})
                    visual_config.update({"resources": [item.to_mapping() for item in (*resources, resource)], "default": resource.id})
                    doc.visuals = visual_config
                    return _draft_to_public(self._service.save_workspace_draft(workspace, doc.to_payload()))
                except BaseException as error:
                    try:
                        shutil.rmtree(package / resource.root)
                    except OSError as recovery:
                        error.recovery_error = recovery
                    raise
            if name == "studio.visual.create":
                resource_id = "visual-" + uuid.uuid4().hex[:12]
                resource = CharacterVisualResource.from_mapping({"id": resource_id, "type": self._text(payload.get("type")), "root": f"visuals/{resource_id}", "entry": "resource.json", "name": payload.get("name", "")})
                initial = host.editor(resource, {}, payload.get("providerId"))
                resources = (*resources, resource)
                from app.config.character_studio import _resolve_workspace_path
                target = _resolve_workspace_path(package, resource.root, "表现资源")
                target.mkdir(parents=True, exist_ok=False)
                try:
                    (target / resource.entry).write_text("{}", encoding="utf-8")
                    doc.visuals = {**(doc.visuals or {}), "resources": [item.to_mapping() for item in resources], "default": selected or resource_id}
                    doc.visuals.setdefault("providers", {})[resource_id] = initial["visual"]["providerId"]
                    doc.visual_data[resource_id] = initial["data"]
                    return _draft_to_public(self._service.save_workspace_draft(workspace, doc.to_payload()))
                except BaseException as error:
                    import shutil
                    try:
                        shutil.rmtree(target)
                    except OSError as recovery:
                        error.recovery_error = recovery
                    raise
            resource_id = self._text(payload.get("resourceId"))
            resource = next((item for item in resources if item.id == resource_id), None)
            if resource is None:
                raise CharacterStudioError("VISUAL_RESOURCE_MISSING", "没有找到所选表现资源。")
            if name == "studio.visual.export":
                from dataclasses import replace
                from app.config.character_loader import _load_profile
                from app.config.character_studio import _write_visual_draft
                from app.config.visual_archive import export_visual_archive
                if resource.type == "sakura.visual.portrait@1" and resource.root == "." and resource.entry == "character.json" and resource.id not in doc.visual_data:
                    import json
                    from app.storage.atomic import atomic_write_text
                    # Export describes files in the draft package; materialize its
                    # pending inline portrait without publishing the installed role.
                    _, raw = self._visual_editor_input(package, doc, resource)
                    atomic_write_text(package / "character.json", json.dumps(raw, ensure_ascii=False, indent=2))
                _write_visual_draft(package, doc)
                resources, selected = character_visual_resources(doc.to_manifest(), package)
                character = replace(_load_profile(package / "character.json"), visual_resources=resources, default_visual_id=selected, visual_providers=(doc.visuals or {}).get("providers", {}))
                resource = next(item for item in character.visual_resources if item.id == resource_id)
                projection = application.application.export_visual_resource(character, resource)
                path = export_visual_archive(package, resource, projection, Path(self._text(payload.get("path"))), cancel_check=self._cancel_check(operation), commit_started=self._commit_started(operation))
                return {"schemaVersion": 1, "outputPath": str(path)}
            editor_resource, raw = self._visual_editor_input(package, doc, resource)
            result = host.editor(editor_resource, raw, payload.get("providerId") or (doc.visuals or {}).get("providers", {}).get(resource_id))
            from types import SimpleNamespace
            character = SimpleNamespace(id=doc.id, display_name=doc.display_name, initial_message=doc.initial_message or "你好", theme_settings=doc.theme)
            presentation = project_character_presentation(character, result["visual"], reason_code="READY")
            return {"schemaVersion": 1, "presentation": {**presentation, "generationId": self._generation_id}, "providerScopeId": result["providerScopeId"], "data": result["data"], "resource": resource.to_mapping(),
                "assetRootPath": str(resolve_resource_path(package, resource.root))}

    @staticmethod
    def _visual_editor_input(package, doc, resource):
        import json
        from app.config.character_resources import CharacterVisualResource
        from app.config.character_studio import _resolve_workspace_path
        if resource.id in doc.visual_data:
            editor_resource = resource
            # The saved draft already contains the plugin's private projection.
            if resource.root == "." and resource.entry == "character.json":
                editor_resource = CharacterVisualResource(resource.id, resource.type, ".", f"visuals/{resource.id}.json")
            return editor_resource, doc.visual_data[resource.id]
        relative = resource.entry if resource.root == "." else f"{resource.root}/{resource.entry}"
        path = _resolve_workspace_path(package, relative, "表现入口")
        raw = None
        if path.is_file() and path.stat().st_size <= 256 * 1024:
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
            except (UnicodeError, ValueError):
                pass
        if resource.type == "sakura.visual.portrait@1" and resource.root == "." and resource.entry == "character.json":
            # v1.1.0 autosave kept edits in the document, not the copied manifest.
            # Preserve opaque fields while presenting those pending edits to the adapter.
            raw = dict(raw) if isinstance(raw, dict) else {}
            portrait = raw.get("portrait")
            raw["portrait"] = {
                **(portrait if isinstance(portrait, dict) else {}),
                "default": doc.default_portrait,
                "expressions": dict(doc.expressions),
            }
        return resource, raw

    def _import_asset(self, payload: dict[str, Any], current: str) -> dict[str, Any]:
        self._keys(
            payload,
            required={"workspaceId", "kind", "path"},
            optional={"label", "refLang", "operationId", "resourceId"},
        )
        workspace_id = self._text(payload["workspaceId"])
        kind = self._text(payload["kind"])
        path = Path(self._text(payload["path"]))
        operation = self._begin_operation(payload)
        cancel_check = self._cancel_check(operation)
        try:
            with self._mutation_lock:
                commit_started = self._commit_started(operation)
                if kind in {"visual", "visualFolder"}:
                    result = self._service.import_visual_asset(workspace_id, path, self._text(payload.get("resourceId")), cancel_check=cancel_check, commit_started=commit_started)
                elif kind in {"gptModel", "sovitsModel"}:
                    result = self._service.import_voice_model(
                        workspace_id,
                        path,
                        model_type="gpt" if kind == "gptModel" else "sovits",
                        cancel_check=cancel_check,
                        commit_started=commit_started,
                    )
                elif kind == "referenceAudio":
                    result = self._service.import_reference_audio(
                        workspace_id,
                        path,
                        cancel_check=cancel_check,
                        commit_started=commit_started,
                    )
                elif kind == "referenceAudioFolder":
                    result = self._service.import_reference_audio_folder(
                        workspace_id,
                        path,
                        ref_lang=self._text(payload.get("refLang"), required=False) or "ja",
                        cancel_check=cancel_check,
                        commit_started=commit_started,
                    )
                else:
                    raise CharacterStudioError(
                        "STUDIO_ASSET_KIND_INVALID",
                        "不支持的角色资源类型。",
                        field="kind",
                    )
        finally:
            self._finish_operation(operation)
        return {"schemaVersion": 1, **_keys_to_camel(result)}

    def _begin_operation(self, payload: Mapping[str, Any]) -> _StudioOperation | None:
        operation_id = self._operation_id(payload.get("operationId"), required=False)
        if not operation_id:
            return None
        operation = _StudioOperation(operation_id)
        with self._operation_lock:
            if self._active_operation is not None:
                raise CharacterStudioError("STUDIO_CORE_BUSY", "另一个角色工坊操作仍在进行。")
            self._active_operation = operation
        return operation

    def _finish_operation(self, operation: _StudioOperation | None) -> None:
        if operation is None:
            return
        with self._operation_lock:
            if self._active_operation is operation:
                self._active_operation = None

    @staticmethod
    def _cancel_check(operation: _StudioOperation | None):
        if operation is None:
            return None

        def check() -> None:
            if operation.cancel.is_set():
                raise CharacterStudioOperationCancelled()

        return check

    def _commit_started(self, operation: _StudioOperation | None):
        if operation is None:
            return None

        def mark() -> None:
            with self._operation_lock:
                if self._active_operation is operation:
                    if operation.cancel.is_set():
                        raise CharacterStudioOperationCancelled()
                    operation.phase = "committing"

        return mark

    def _quiesce_current_generation(self) -> None:
        if self._quiesce_generation is None:
            return
        self._generation_invalidated = True
        try:
            self._quiesce_generation()
        except Exception as exc:
            raise CharacterStudioError(
                "STUDIO_OPERATION_FAILED",
                "停止当前角色的运行任务失败。",
            ) from exc

    @staticmethod
    def _operation_id(value: object, *, required: bool) -> str:
        operation_id = value.strip() if isinstance(value, str) else ""
        if not operation_id:
            if required:
                raise CharacterStudioError("STUDIO_REQUEST_INVALID", "缺少 operationId。")
            return ""
        if not _OPERATION_ID_RE.fullmatch(operation_id):
            raise CharacterStudioError(
                "STUDIO_REQUEST_INVALID", "operationId 格式无效。", field="operationId"
            )
        return operation_id

    def _current_character_id(self) -> str:
        registry = CharacterRegistry(self._user_root)
        return self._settings.load_current_character_id(registry) or ""

    @staticmethod
    def _keys(
        payload: Mapping[str, Any], *, required: set[str] = set(), optional: set[str] = set()
    ) -> None:
        if set(payload) != required | (set(payload) & optional) or not required.issubset(payload):
            raise CharacterStudioError("STUDIO_REQUEST_INVALID", "角色工坊请求字段无效。")

    @staticmethod
    def _text(value: object, *, required: bool = True) -> str:
        text = value.strip() if isinstance(value, str) else ""
        if required and not text:
            raise CharacterStudioError("STUDIO_REQUEST_INVALID", "角色工坊请求缺少必填字段。")
        return text

    @staticmethod
    def _mapping(value: object, field: str) -> dict[str, Any]:
        if not isinstance(value, Mapping):
            raise CharacterStudioError("STUDIO_REQUEST_INVALID", f"{field} 必须是对象。", field=field)
        return dict(value)


def _snake_to_camel(key: str) -> str:
    first, *rest = key.split("_")
    return first + "".join(part[:1].upper() + part[1:] for part in rest)


def _camel_to_snake(key: str) -> str:
    result = []
    for character in key:
        if character.isupper():
            result.extend(("_", character.lower()))
        else:
            result.append(character)
    return "".join(result)


def _keys_to_camel(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {_snake_to_camel(str(key)): item if key in {"visual_data", "visuals", "data"} else _keys_to_camel(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_keys_to_camel(item) for item in value]
    return value


def _doc_to_internal(value: Mapping[str, Any]) -> dict[str, Any]:
    unknown = set(value) - _DOC_FIELDS
    if unknown:
        raise CharacterStudioError(
            "STUDIO_REQUEST_INVALID",
            "角色文档包含不支持的字段。",
            field=str(sorted(unknown)[0]),
        )
    expressions = value.get("expressions")
    if expressions is not None and not isinstance(expressions, Mapping):
        raise CharacterStudioError("STUDIO_REQUEST_INVALID", "expressions 必须是对象。", field="expressions")
    theme = value.get("theme")
    if theme is not None:
        if not isinstance(theme, Mapping) or set(theme) - _THEME_FIELDS:
            raise CharacterStudioError("STUDIO_REQUEST_INVALID", "theme 字段无效。", field="theme")
    voice = value.get("voice")
    if voice is not None:
        if not isinstance(voice, Mapping) or set(voice) - _VOICE_FIELDS:
            raise CharacterStudioError("STUDIO_REQUEST_INVALID", "voice 字段无效。", field="voice")
    references = value.get("referenceAudios")
    if references is not None:
        if not isinstance(references, list) or any(
            not isinstance(item, Mapping) or set(item) - _REFERENCE_AUDIO_FIELDS
            for item in references
        ):
            raise CharacterStudioError(
                "STUDIO_REQUEST_INVALID",
                "referenceAudios 字段无效。",
                field="referenceAudios",
            )
    result = _mapping_to_internal({key: item for key, item in value.items() if key not in {"visualData", "visuals"}})
    if "visualData" in value: result["visual_data"] = value["visualData"]
    if "visuals" in value: result["visuals"] = value["visuals"]
    return result


def _mapping_to_internal(value: Mapping[str, Any]) -> dict[str, Any]:
    return {
        _camel_to_snake(str(key)): (
            [_mapping_to_internal(item) if isinstance(item, Mapping) else item for item in item_value]
            if isinstance(item_value, list)
            else _mapping_to_internal(item_value)
            if isinstance(item_value, Mapping)
            else item_value
        )
        for key, item_value in value.items()
    }


def _summary_to_public(value: Mapping[str, Any]) -> dict[str, Any]:
    allowed = {
        "id", "display_name", "is_current", "has_voice", "source", "is_installed",
        "has_draft", "draft_kind", "is_dirty",
    }
    public = _keys_to_camel({key: value.get(key) for key in allowed if key in value})
    public.setdefault("isInstalled", bool(value.get("is_installed")))
    public.setdefault("hasDraft", bool(value.get("has_draft")))
    public.setdefault("isDirty", bool(value.get("is_dirty")))
    return public


def _opened_to_public(value: Mapping[str, Any], current: str) -> dict[str, Any]:
    characters = []
    for item in value.get("characters", []):
        if not isinstance(item, Mapping):
            continue
        public = _summary_to_public(item)
        public["isCurrent"] = str(item.get("id") or "") == current
        characters.append(public)
    return {
        "schemaVersion": 1,
        "workspaceId": str(value.get("workspace_id") or ""),
        "source": str(value.get("source") or "draft"),
        "resumed": bool(value.get("resumed")),
        "isDirty": bool(value.get("is_dirty")),
        "doc": _public_doc(value.get("doc") or {}),
        "modelFiles": _keys_to_camel(value.get("model_files") or []),
        "characters": characters,
        "currentCharacterId": current or None,
    }


def _draft_to_public(value: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "schemaVersion": 1,
        "workspaceId": str(value.get("workspace_id") or ""),
        "doc": _public_doc(value.get("doc") or {}),
        "isDirty": bool(value.get("is_dirty")),
        "savedAt": int(value.get("saved_at") or 0),
        "modelFiles": _keys_to_camel(value.get("model_files") or []),
    }


def _public_doc(value):
    from app.config.character_resources import CharacterVisualResource, legacy_portrait_resource
    doc = _keys_to_camel(value)
    # Named legacy projection at the old document boundary only.
    if doc.get("visuals") is None:
        resources = [legacy_portrait_resource().to_mapping()] if doc.get("defaultPortrait") else []
        doc["visuals"] = {"resources": resources, "default": resources[0]["id"] if resources else None}
    else:
        doc["visuals"]["resources"] = [CharacterVisualResource.from_mapping(item).to_mapping() for item in doc["visuals"]["resources"]]
    return doc


__all__ = [
    "CHARACTER_STUDIO_REQUEST_NAMES",
    "CharacterStudioBoundary",
    "CharacterStudioError",
]
