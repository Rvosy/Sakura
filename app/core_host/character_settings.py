"""Qt-free Runtime v2 character installation and selection boundary."""

from __future__ import annotations

import hmac
import threading
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from app.config.character_archive import CharacterArchiveError, import_character_archive
from app.config.character_loader import CharacterConfigError, CharacterRegistry
from app.config.settings_service import AppSettingsService
from app.core_host.protocol import response


CHARACTER_SETTINGS_REQUEST_NAMES = frozenset(
    {
        "characters.settings.get",
        "characters.settings.import",
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
        *,
        runtime_apply: Callable[[], None] | None = None,
    ) -> None:
        self._generation_id = generation_id
        self._generation_credential = generation_credential
        self._user_root = Path(user_root)
        self._settings = AppSettingsService(self._user_root)
        self._runtime_apply = runtime_apply
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
                raise CharacterSettingsError("INVALID_REQUEST", "角色设置请求格式无效。")
            if name == "characters.settings.get":
                if payload:
                    raise CharacterSettingsError("INVALID_REQUEST", "角色设置读取请求必须为空。")
                result = self.snapshot()
            elif name == "characters.settings.import":
                if set(payload) != {"path"}:
                    raise CharacterSettingsError("INVALID_REQUEST", "角色导入请求格式无效。")
                result = self.import_archive(payload["path"])
            elif name == "characters.settings.select":
                if set(payload) != {"characterId"}:
                    raise CharacterSettingsError("INVALID_REQUEST", "角色选择请求格式无效。")
                result = self.select(payload["characterId"])
            else:
                raise CharacterSettingsError("UNKNOWN_COMMAND", "不支持的角色设置命令。")
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
            try:
                imported = import_character_archive(archive, self._user_root)
                if current is None:
                    self._settings.save_current_character_id(
                        CharacterRegistry(self._user_root), imported.character_id
                    )
            except (OSError, CharacterArchiveError, CharacterConfigError, ValueError) as error:
                raise CharacterSettingsError(
                    "CHARACTER_IMPORT_FAILED",
                    "角色包导入失败，现有角色保持不变。",
                ) from error
            self._revision += 1
            self._apply_runtime()
            return self.snapshot()

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
            self._apply_runtime()
            return self.snapshot()

    def _apply_runtime(self) -> None:
        if self._runtime_apply is not None:
            self._runtime_apply()


__all__ = [
    "CHARACTER_SETTINGS_REQUEST_NAMES",
    "CharacterSettingsBoundary",
    "CharacterSettingsError",
]
