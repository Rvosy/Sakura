"""Runtime v2 screen-awareness settings boundary."""

from __future__ import annotations

import hmac
import threading
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from app.agent.screen_awareness import (
    SCREEN_AWARENESS_MAX_CHECK_INTERVAL_MINUTES,
    SCREEN_AWARENESS_MAX_COOLDOWN_MINUTES,
    SCREEN_AWARENESS_MAX_SCREEN_CONTEXT_BATCH_LIMIT,
    SCREEN_AWARENESS_MIN_CHECK_INTERVAL_MINUTES,
    SCREEN_AWARENESS_MIN_COOLDOWN_MINUTES,
    SCREEN_AWARENESS_MIN_SCREEN_CONTEXT_BATCH_LIMIT,
    SCREEN_AWARENESS_SCREEN_CONTEXT_RESOLUTIONS,
    ScreenAwarenessSettings,
)
from app.config.settings_service import AppSettingsService
from app.core_host.protocol import response


SCREEN_AWARENESS_SETTINGS_REQUEST_NAMES = frozenset(
    {"screen_awareness.settings.get", "screen_awareness.settings.save"}
)


class ScreenAwarenessSettingsError(ValueError):
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
            "details": {"feature": "privacy.screen_awareness", "field": self.field},
        }


class ScreenAwarenessSettingsBoundary:
    def __init__(self, generation_id: str, generation_credential: str, app_root: Path) -> None:
        self._generation_id = generation_id
        self._generation_credential = generation_credential
        self._service = AppSettingsService(app_root)
        self._save_lock = threading.Lock()

    def handle(self, request: dict[str, Any]) -> dict[str, Any]:
        supplied = request.get("generationCredential")
        if (
            request.get("generationId") != self._generation_id
            or not isinstance(supplied, str)
            or not hmac.compare_digest(supplied, self._generation_credential)
        ):
            raise RuntimeError("GENERATION_IDENTITY_MISMATCH")
        try:
            payload = request.get("payload")
            if not isinstance(payload, Mapping):
                raise ScreenAwarenessSettingsError("INVALID_REQUEST", "主动屏幕感知设置请求格式无效。")
            name = request.get("name")
            if name == "screen_awareness.settings.get":
                if payload:
                    raise ScreenAwarenessSettingsError("INVALID_REQUEST", "设置读取请求必须为空。")
                result = self.snapshot()
            elif name == "screen_awareness.settings.save":
                if set(payload) != {"settings"}:
                    raise ScreenAwarenessSettingsError("INVALID_REQUEST", "设置保存请求格式无效。")
                result = self.save(payload["settings"])
            else:
                raise ScreenAwarenessSettingsError("UNKNOWN_COMMAND", "不支持的主动屏幕感知设置命令。")
            return response(
                request,
                generation_id=self._generation_id,
                generation_credential=self._generation_credential,
                protocol_minor=2,
                payload=result,
            )
        except ScreenAwarenessSettingsError as error:
            return response(
                request,
                generation_id=self._generation_id,
                generation_credential=self._generation_credential,
                protocol_minor=2,
                error=error.public_error(),
            )

    def snapshot(self) -> dict[str, object]:
        try:
            settings = self._service.load_screen_awareness_settings().normalized()
        except (OSError, UnicodeError, ValueError) as error:
            raise ScreenAwarenessSettingsError(
                "CONFIG_READ_ONLY", "主动屏幕感知配置损坏或不可读取。"
            ) from error
        return _snapshot(settings)

    def save(self, raw: object) -> dict[str, object]:
        settings = _validate_settings(raw)
        with self._save_lock:
            try:
                self._service.save_screen_awareness_settings(settings)
            except (OSError, UnicodeError, ValueError) as error:
                raise ScreenAwarenessSettingsError(
                    "CONFIG_SAVE_FAILED", "主动屏幕感知设置保存失败，原文件保持不变。"
                ) from error
        return _snapshot(settings)


def _validate_settings(raw: object) -> ScreenAwarenessSettings:
    fields = {
        "enabled",
        "checkIntervalMinutes",
        "cooldownMinutes",
        "batchLimit",
        "resolution",
    }
    if not isinstance(raw, Mapping) or set(raw) != fields:
        raise ScreenAwarenessSettingsError("INVALID_REQUEST", "主动屏幕感知设置字段无效。")
    enabled = raw.get("enabled")
    if not isinstance(enabled, bool):
        raise ScreenAwarenessSettingsError("FIELD_INVALID", "启用开关必须是布尔值。", field="enabled")

    def bounded_integer(name: str, minimum: int, maximum: int) -> int:
        value = raw.get(name)
        if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
            raise ScreenAwarenessSettingsError(
                "FIELD_INVALID", f"{name} 超出允许范围。", field=name
            )
        return value

    resolution = raw.get("resolution")
    if resolution not in SCREEN_AWARENESS_SCREEN_CONTEXT_RESOLUTIONS:
        raise ScreenAwarenessSettingsError(
            "FIELD_INVALID", "截图分辨率无效。", field="resolution"
        )
    return ScreenAwarenessSettings(
        enabled=enabled,
        screen_context_enabled=enabled,
        check_interval_minutes=bounded_integer(
            "checkIntervalMinutes",
            SCREEN_AWARENESS_MIN_CHECK_INTERVAL_MINUTES,
            SCREEN_AWARENESS_MAX_CHECK_INTERVAL_MINUTES,
        ),
        cooldown_minutes=bounded_integer(
            "cooldownMinutes",
            SCREEN_AWARENESS_MIN_COOLDOWN_MINUTES,
            SCREEN_AWARENESS_MAX_COOLDOWN_MINUTES,
        ),
        screen_context_batch_limit=bounded_integer(
            "batchLimit",
            SCREEN_AWARENESS_MIN_SCREEN_CONTEXT_BATCH_LIMIT,
            SCREEN_AWARENESS_MAX_SCREEN_CONTEXT_BATCH_LIMIT,
        ),
        screen_context_resolution=str(resolution),
    )


def _snapshot(settings: ScreenAwarenessSettings) -> dict[str, object]:
    normalized = settings.normalized()
    return {
        "schemaVersion": 1,
        "settings": {
            "enabled": normalized.allows_screen_context(),
            "checkIntervalMinutes": normalized.check_interval_minutes,
            "cooldownMinutes": normalized.cooldown_minutes,
            "batchLimit": normalized.screen_context_batch_limit,
            "resolution": normalized.screen_context_resolution,
        },
    }


__all__ = [
    "SCREEN_AWARENESS_SETTINGS_REQUEST_NAMES",
    "ScreenAwarenessSettingsBoundary",
    "ScreenAwarenessSettingsError",
]
