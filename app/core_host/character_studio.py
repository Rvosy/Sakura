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


CHARACTER_STUDIO_REQUEST_NAMES = frozenset(
    {
        "studio.bootstrap",
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

    @property
    def _service(self) -> CharacterStudioService:
        service = self._service_instance
        if service is not None:
            return service
        with self._service_init_lock:
            service = self._service_instance
            if service is None:
                service = CharacterStudioService(self._user_root)
                self._service_instance = service
            return service

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
                        if result.get("saved_character_id") == current
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

    def _import_asset(self, payload: dict[str, Any], current: str) -> dict[str, Any]:
        self._keys(
            payload,
            required={"workspaceId", "kind", "path"},
            optional={"label", "refLang", "operationId"},
        )
        workspace_id = self._text(payload["workspaceId"])
        kind = self._text(payload["kind"])
        path = Path(self._text(payload["path"]))
        operation = self._begin_operation(payload)
        cancel_check = self._cancel_check(operation)
        try:
            with self._mutation_lock:
                commit_started = self._commit_started(operation)
                if kind == "portrait":
                    result = self._service.import_portrait(
                        workspace_id,
                        path,
                        label=self._text(payload.get("label"), required=False) or "portrait",
                        cancel_check=cancel_check,
                        commit_started=commit_started,
                    )
                elif kind == "portraitFolder":
                    result = self._service.import_portrait_folder(
                        workspace_id,
                        path,
                        cancel_check=cancel_check,
                        commit_started=commit_started,
                    )
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
        return {_snake_to_camel(str(key)): _keys_to_camel(item) for key, item in value.items()}
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
    return _mapping_to_internal(value)


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
        "doc": _keys_to_camel(value.get("doc") or {}),
        "characters": characters,
        "currentCharacterId": current or None,
    }


def _draft_to_public(value: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "schemaVersion": 1,
        "workspaceId": str(value.get("workspace_id") or ""),
        "doc": _keys_to_camel(value.get("doc") or {}),
        "isDirty": bool(value.get("is_dirty")),
        "savedAt": int(value.get("saved_at") or 0),
    }


__all__ = [
    "CHARACTER_STUDIO_REQUEST_NAMES",
    "CharacterStudioBoundary",
    "CharacterStudioError",
]
