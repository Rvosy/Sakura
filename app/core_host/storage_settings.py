"""Runtime v2 user-root and optional TTS-root settings boundary."""

from __future__ import annotations

import hmac
import threading
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from app.core_host.protocol import response
from app.storage.tts_storage import TtsStorage, TtsStorageUnavailable


STORAGE_SETTINGS_REQUEST_NAMES = frozenset(
    {
        "storage.settings.get",
        "storage.settings.choose_tts_root",
        "storage.settings.reset_tts_root",
    }
)


class StorageSettingsError(ValueError):
    def __init__(self, code: str, message: str, *, field: str = "") -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.field = field

    def public_error(self) -> dict[str, object]:
        return {
            "code": self.code,
            "message": self.message,
            "retryable": False,
            "details": {"feature": "storage.tts_root", "field": self.field},
        }


class StorageSettingsBoundary:
    def __init__(self, generation_id: str, generation_credential: str, user_root: Path) -> None:
        self._generation_id = generation_id
        self._generation_credential = generation_credential
        self._storage = TtsStorage(user_root)
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
                raise StorageSettingsError("INVALID_REQUEST", "存储设置请求格式无效。")
            if name == "storage.settings.get":
                if payload:
                    raise StorageSettingsError("INVALID_REQUEST", "存储设置读取请求必须为空。")
                result = self.snapshot()
            elif name == "storage.settings.choose_tts_root":
                if set(payload) != {"path"}:
                    raise StorageSettingsError("INVALID_REQUEST", "TTS 目录设置请求格式无效。")
                result = self.choose(payload["path"])
            elif name == "storage.settings.reset_tts_root":
                if payload:
                    raise StorageSettingsError("INVALID_REQUEST", "TTS 目录重置请求必须为空。")
                result = self.reset()
            else:
                raise StorageSettingsError("UNKNOWN_COMMAND", "不支持的存储设置命令。")
            return response(
                request,
                generation_id=self._generation_id,
                generation_credential=self._generation_credential,
                protocol_minor=2,
                payload=result,
            )
        except StorageSettingsError as error:
            return response(
                request,
                generation_id=self._generation_id,
                generation_credential=self._generation_credential,
                protocol_minor=2,
                error=error.public_error(),
            )

    def snapshot(self) -> dict[str, object]:
        try:
            return self._storage.snapshot(create_default=True).to_payload()
        except TtsStorageUnavailable as error:
            raise StorageSettingsError(
                error.code,
                "TTS 存储配置不可用。",
                field="ttsRoot",
            ) from error

    def choose(self, raw_path: object) -> dict[str, object]:
        if not isinstance(raw_path, str) or not raw_path.strip():
            raise StorageSettingsError("INVALID_REQUEST", "TTS 目录无效。", field="ttsRoot")
        with self._lock:
            try:
                return self._storage.set_custom_root(Path(raw_path)).to_payload()
            except TtsStorageUnavailable as error:
                raise StorageSettingsError(
                    error.code,
                    "所选 TTS 目录不存在、不是目录或不可写。",
                    field="ttsRoot",
                ) from error

    def reset(self) -> dict[str, object]:
        with self._lock:
            try:
                return self._storage.reset().to_payload()
            except (OSError, TtsStorageUnavailable) as error:
                raise StorageSettingsError(
                    "TTS_STORAGE_CONFIG_SAVE_FAILED",
                    "TTS 存储设置重置失败，原配置保持不变。",
                    field="ttsRoot",
                ) from error


__all__ = [
    "STORAGE_SETTINGS_REQUEST_NAMES",
    "StorageSettingsBoundary",
    "StorageSettingsError",
]
