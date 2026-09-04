"""Qt-free Runtime v2 character installation and selection boundary."""

from __future__ import annotations

import hmac
import threading
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from app.config.character_archive import (
    CharacterArchiveError,
    export_character_archive,
    export_character_voice_archive,
    import_character_archive,
    import_character_voice_archive,
)
from app.config.character_loader import (
    CharacterConfigError,
    CharacterProfile,
    CharacterRegistry,
)
from app.config.settings_service import AppSettingsService
from app.core_host.protocol import response

CHARACTER_SETTINGS_REQUEST_NAMES = frozenset(
    {
        "characters.settings.get",
        "characters.settings.import",
        "characters.settings.import_voice",
        "characters.settings.export",
        "characters.settings.select",
    }
)


class CharacterSettingsError(ValueError):
    def __init__(self, code: str, message: str, *, field: str = "") -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.field = field

    def public_error(self) -> dict[str, object]:
        return {
            "code": self.code,
            "message": self.message,
            "retryable": self.code == "CHARACTER_CONFIG_SAVE_FAILED",
            "details": {"feature": "character.manage", "field": self.field},
        }


class CharacterSettingsBoundary:
    def __init__(
        self,
        generation_id: str,
        generation_credential: str,
        user_root: Path,
    ) -> None:
        self._generation_id = generation_id
        self._generation_credential = generation_credential
        self._user_root = Path(user_root)
        self._settings = AppSettingsService(self._user_root)
        self._revision = 1
        self._lock = threading.Lock()

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
                raise CharacterSettingsError(
                    "INVALID_REQUEST", "角色设置请求格式无效。"
                )
            if name == "characters.settings.get":
                if payload:
                    raise CharacterSettingsError(
                        "INVALID_REQUEST", "角色设置读取请求必须为空。"
                    )
                result = self.snapshot()
            elif name == "characters.settings.import":
                if set(payload) != {"path"}:
                    raise CharacterSettingsError(
                        "INVALID_REQUEST", "角色导入请求格式无效。"
                    )
                result = self.import_archive(payload["path"])
            elif name == "characters.settings.import_voice":
                if set(payload) != {"path", "characterId"}:
                    raise CharacterSettingsError(
                        "INVALID_REQUEST", "语音包导入请求格式无效。"
                    )
                result = self.import_voice_archive(
                    payload["path"], payload["characterId"]
                )
            elif name == "characters.settings.export":
                if set(payload) != {"path", "characterId", "kind"}:
                    raise CharacterSettingsError(
                        "INVALID_REQUEST", "角色包导出请求格式无效。"
                    )
                result = self.export_archive(
                    payload["path"],
                    payload["characterId"],
                    payload["kind"],
                )
            elif name == "characters.settings.select":
                if set(payload) != {"characterId"}:
                    raise CharacterSettingsError(
                        "INVALID_REQUEST", "角色选择请求格式无效。"
                    )
                result = self.select(payload["characterId"])
            else:
                raise CharacterSettingsError(
                    "UNKNOWN_COMMAND", "不支持的角色设置命令。"
                )
            return response(
                request,
                generation_id=self._generation_id,
                generation_credential=self._generation_credential,
                protocol_minor=2,
                payload=result,
            )
        except CharacterSettingsError as error:
            return response(
                request,
                generation_id=self._generation_id,
                generation_credential=self._generation_credential,
                protocol_minor=2,
                error=error.public_error(),
            )

    def snapshot(self) -> dict[str, object]:
        registry = CharacterRegistry(self._user_root)
        current = self._settings.load_current_character_id(registry)
        return {
            "schemaVersion": 1,
            "revision": self._revision,
            "currentCharacterId": current,
            "characters": [
                {
                    "id": profile.id,
                    "displayName": profile.display_name,
                    "hasVoice": profile.voice is not None,
                    "hasExportableVoice": self._has_exportable_voice(profile),
                }
                for profile in registry.all()
            ],
        }

    def import_archive(self, raw_path: object) -> dict[str, object]:
        if not isinstance(raw_path, str) or not raw_path.strip():
            raise CharacterSettingsError(
                "CHARACTER_ARCHIVE_PATH_INVALID",
                "请选择有效的 Sakura .char 角色包。",
                field="path",
            )
        archive = Path(raw_path).expanduser()
        if not archive.is_absolute() or archive.suffix.lower() != ".char":
            raise CharacterSettingsError(
                "CHARACTER_ARCHIVE_PATH_INVALID",
                "请选择有效的 Sakura .char 角色包。",
                field="path",
            )
        with self._lock:
            before = CharacterRegistry(self._user_root)
            current = self._settings.load_current_character_id(before)
            change_plan = "unchanged"
            try:
                imported = import_character_archive(archive, self._user_root)
                if current is None:
                    self._settings.save_current_character_id(
                        CharacterRegistry(self._user_root), imported.character_id
                    )
                    change_plan = "core_restart_required"
            except (
                OSError,
                CharacterArchiveError,
                CharacterConfigError,
                ValueError,
            ) as error:
                raise CharacterSettingsError(
                    "CHARACTER_IMPORT_FAILED",
                    "角色包导入失败，现有角色保持不变。",
                ) from error
            self._revision += 1
            return self._change_result(change_plan)

    def import_voice_archive(
        self,
        raw_path: object,
        raw_character_id: object,
    ) -> dict[str, object]:
        archive = self._import_archive_path(raw_path, suffix=".voice")
        character_id = self._character_id(raw_character_id)
        with self._lock:
            registry = CharacterRegistry(self._user_root)
            try:
                registry.get(character_id)
            except CharacterConfigError as error:
                raise CharacterSettingsError(
                    "CHARACTER_NOT_FOUND",
                    "选择的角色不存在。",
                    field="characterId",
                ) from error
            current = self._settings.load_current_character_id(registry)
            try:
                import_character_voice_archive(archive, self._user_root, character_id)
            except (
                OSError,
                CharacterArchiveError,
                CharacterConfigError,
                ValueError,
            ) as error:
                raise CharacterSettingsError(
                    "CHARACTER_VOICE_IMPORT_FAILED",
                    "语音包导入失败。请检查语音包和当前角色的语音文件。",
                    field="path",
                ) from error
            self._revision += 1
            return self._change_result(
                "core_restart_required" if current == character_id else "unchanged"
            )

    def export_archive(
        self,
        raw_path: object,
        raw_character_id: object,
        raw_kind: object,
    ) -> dict[str, object]:
        if not isinstance(raw_kind, str) or raw_kind not in {"full", "card", "voice"}:
            raise CharacterSettingsError(
                "CHARACTER_ARCHIVE_KIND_INVALID",
                "请选择有效的角色包导出类型。",
                field="kind",
            )
        suffix = ".voice" if raw_kind == "voice" else ".char"
        output = self._export_archive_path(raw_path, suffix=suffix)
        character_id = self._character_id(raw_character_id)
        if not output.parent.is_dir():
            raise CharacterSettingsError(
                "CHARACTER_ARCHIVE_PATH_INVALID",
                "请选择有效的导出目录。",
                field="path",
            )
        with self._lock:
            try:
                profile = CharacterRegistry(self._user_root).get(character_id)
            except CharacterConfigError as error:
                raise CharacterSettingsError(
                    "CHARACTER_NOT_FOUND",
                    "选择的角色不存在。",
                    field="characterId",
                ) from error
            if raw_kind in {"full", "voice"} and not self._has_exportable_voice(
                profile
            ):
                message = (
                    "当前角色没有完整语音模型，请导出单角色包。"
                    if raw_kind == "full"
                    else "当前角色没有可导出的语音模型。"
                )
                raise CharacterSettingsError(
                    "CHARACTER_VOICE_NOT_EXPORTABLE",
                    message,
                    field="kind",
                )
            try:
                if raw_kind == "voice":
                    export_character_voice_archive(profile, output)
                else:
                    export_character_archive(
                        profile,
                        output,
                        include_voice=raw_kind == "full",
                    )
            except (
                OSError,
                CharacterArchiveError,
                CharacterConfigError,
                ValueError,
            ) as error:
                raise CharacterSettingsError(
                    "CHARACTER_EXPORT_FAILED",
                    "角色包导出失败，目标文件未被替换。",
                    field="path",
                ) from error
        return {
            "schemaVersion": 1,
            "outputPath": str(output),
            "message": f"角色包已导出到：{output}",
        }

    def select(self, raw_character_id: object) -> dict[str, object]:
        if not isinstance(raw_character_id, str) or not raw_character_id.strip():
            raise CharacterSettingsError(
                "CHARACTER_ID_INVALID",
                "角色标识无效。",
                field="characterId",
            )
        character_id = raw_character_id.strip()
        with self._lock:
            registry = CharacterRegistry(self._user_root)
            try:
                registry.get(character_id)
                current = self._settings.load_current_character_id(registry)
                if current == character_id:
                    return self._change_result("unchanged")
                self._settings.save_current_character_id(registry, character_id)
            except CharacterConfigError as error:
                raise CharacterSettingsError(
                    "CHARACTER_NOT_FOUND",
                    "选择的角色不存在。",
                    field="characterId",
                ) from error
            except (OSError, ValueError) as error:
                raise CharacterSettingsError(
                    "CHARACTER_CONFIG_SAVE_FAILED",
                    "角色选择保存失败，原配置保持不变。",
                    field="characterId",
                ) from error
            self._revision += 1
            return self._change_result("core_restart_required")

    def _change_result(self, change_plan: str) -> dict[str, object]:
        if change_plan not in {"unchanged", "core_restart_required"}:
            raise ValueError("invalid character change plan")
        return {
            "schemaVersion": 1,
            "snapshot": self.snapshot(),
            "changePlan": change_plan,
        }

    @staticmethod
    def _character_id(raw_character_id: object) -> str:
        if not isinstance(raw_character_id, str) or not raw_character_id.strip():
            raise CharacterSettingsError(
                "CHARACTER_ID_INVALID",
                "角色标识无效。",
                field="characterId",
            )
        return raw_character_id.strip()

    @staticmethod
    def _import_archive_path(raw_path: object, *, suffix: str) -> Path:
        if not isinstance(raw_path, str) or not raw_path.strip():
            raise CharacterSettingsError(
                "CHARACTER_ARCHIVE_PATH_INVALID",
                f"请选择有效的 Sakura {suffix} 文件用于导入。",
                field="path",
            )
        path = Path(raw_path).expanduser()
        if (
            not path.is_absolute()
            or path.suffix.lower() != suffix
            or not path.is_file()
        ):
            raise CharacterSettingsError(
                "CHARACTER_ARCHIVE_PATH_INVALID",
                f"请选择有效的 Sakura {suffix} 文件用于导入。",
                field="path",
            )
        return path

    @staticmethod
    def _export_archive_path(raw_path: object, *, suffix: str) -> Path:
        if not isinstance(raw_path, str) or not raw_path.strip():
            raise CharacterSettingsError(
                "CHARACTER_ARCHIVE_PATH_INVALID",
                "请选择有效的角色包导出路径。",
                field="path",
            )
        path = Path(raw_path).expanduser()
        if not path.is_absolute():
            raise CharacterSettingsError(
                "CHARACTER_ARCHIVE_PATH_INVALID",
                "请选择有效的角色包导出路径。",
                field="path",
            )
        return path if path.suffix.lower() == suffix else path.with_suffix(suffix)

    @staticmethod
    def _has_exportable_voice(profile: CharacterProfile) -> bool:
        voice = profile.voice
        return bool(
            voice is not None
            and voice.gpt_model_path is not None
            and voice.gpt_model_path.is_file()
            and voice.sovits_model_path is not None
            and voice.sovits_model_path.is_file()
        )


__all__ = [
    "CHARACTER_SETTINGS_REQUEST_NAMES",
    "CharacterSettingsBoundary",
    "CharacterSettingsError",
]
