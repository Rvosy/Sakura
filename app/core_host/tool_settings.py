"""Qt-free Tools settings repository for one Runtime v2 Core generation."""

from __future__ import annotations

import hmac
import threading
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Callable

import yaml

from app.agent.runtime_limits import RuntimeLoopSettings, normalize_runtime_loop_settings
from app.core_host.protocol import error_payload, response
from app.storage.atomic import atomic_write_text
from app.storage.paths import StoragePaths


TOOL_SETTINGS_REQUEST_NAMES = frozenset({"tools.settings.get", "tools.settings.save"})
CURRENT_CONFIG_VERSION = 1


class ToolSettingsError(ValueError):
    def __init__(self, code: str, message: str, *, field: str = "") -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.field = field

    def public_error(self) -> dict[str, object]:
        return {
            "code": self.code,
            "message": self.message,
            "retryable": self.code == "CONFIG_SAVE_FAILED",
            "details": {"feature": "tools.runtime_limits", "field": self.field},
        }


class ToolSettingsBoundary:
    def __init__(
        self,
        generation_id: str,
        generation_credential: str,
        app_root: Path,
        *,
        runtime_apply: Callable[[RuntimeLoopSettings], None] | None = None,
    ) -> None:
        self._generation_id = generation_id
        self._generation_credential = generation_credential
        self._path = StoragePaths(app_root).system_config()
        self._save_lock = threading.Lock()
        self._runtime_apply = runtime_apply

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
                raise ToolSettingsError("INVALID_REQUEST", "Tools 设置请求格式无效。")
            if name == "tools.settings.get":
                if payload:
                    raise ToolSettingsError("INVALID_REQUEST", "Tools 设置读取请求必须为空。")
                result = self.snapshot()
            elif name == "tools.settings.save":
                if set(payload) != {"settings"}:
                    raise ToolSettingsError("INVALID_REQUEST", "Tools 设置保存请求格式无效。")
                result = self.save(payload["settings"])
            else:
                raise ToolSettingsError("UNKNOWN_COMMAND", "不支持的 Tools 设置命令。")
            return response(
                request,
                generation_id=self._generation_id,
                generation_credential=self._generation_credential,
                protocol_minor=2,
                payload=result,
            )
        except ToolSettingsError as error:
            return response(
                request,
                generation_id=self._generation_id,
                generation_credential=self._generation_credential,
                protocol_minor=2,
                error=error.public_error(),
            )

    def snapshot(self) -> dict[str, object]:
        document = self._read_document()
        return _snapshot(_limits_from_document(document))

    def save(self, raw: object) -> dict[str, object]:
        limits = _validate_settings(raw)
        with self._save_lock:
            document = self._read_document()
            updated = dict(document)
            tool_loop = dict(_mapping(updated.get("tool_loop")))
            tool_loop.update(
                {
                    "max_agent_steps_per_turn": limits.max_agent_steps_per_turn,
                    "max_tool_calls_per_step": limits.max_tool_calls_per_step,
                    "max_tool_calls_per_turn": limits.max_tool_calls_per_turn,
                }
            )
            updated["tool_loop"] = tool_loop
            try:
                serialized = yaml.safe_dump(
                    updated,
                    allow_unicode=True,
                    sort_keys=False,
                    default_flow_style=False,
                )
                atomic_write_text(self._path, serialized)
            except OSError as error:
                raise ToolSettingsError(
                    "CONFIG_SAVE_FAILED", "Tools 设置保存失败，原文件保持不变。"
                ) from error
            if self._runtime_apply is not None:
                self._runtime_apply(limits)
        return {**_snapshot(limits), "saved": True, "changePlan": "applied"}

    def _read_document(self) -> dict[str, Any]:
        return _read_document(self._path)


def load_tool_runtime_configuration(app_root: Path) -> RuntimeLoopSettings:
    """Load the generation startup values owned by the Tools settings boundary."""

    document = _read_document(StoragePaths(app_root).system_config())
    return _limits_from_document(document)


def _read_document(path: Path) -> dict[str, Any]:
    try:
        if not path.exists():
            return {"config_version": CURRENT_CONFIG_VERSION}
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, yaml.YAMLError) as error:
        raise ToolSettingsError("CONFIG_READ_ONLY", "Tools 配置损坏或不可读取。") from error
    if not isinstance(raw, Mapping):
        raise ToolSettingsError("CONFIG_READ_ONLY", "Tools 配置格式无效。")
    version = raw.get("config_version")
    if isinstance(version, bool) or not isinstance(version, int) or version != CURRENT_CONFIG_VERSION:
        raise ToolSettingsError("CONFIG_VERSION_UNSUPPORTED", "Tools 配置版本不受支持。")
    return dict(raw)


def _validate_settings(raw: object) -> RuntimeLoopSettings:
    if not isinstance(raw, Mapping) or set(raw) != {"runtimeLimits"}:
        raise ToolSettingsError("INVALID_REQUEST", "Tools 设置字段无效。")
    limits = raw.get("runtimeLimits")
    if not isinstance(limits, Mapping) or set(limits) != {
        "maxAgentStepsPerTurn",
        "maxToolCallsPerStep",
        "maxToolCallsPerTurn",
    }:
        raise ToolSettingsError("INVALID_REQUEST", "工具循环上限字段无效。")
    values: list[int] = []
    for field in ("maxAgentStepsPerTurn", "maxToolCallsPerStep", "maxToolCallsPerTurn"):
        value = limits.get(field)
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise ToolSettingsError("FIELD_INVALID", "工具循环上限必须是正整数。", field=field)
        values.append(value)
    normalized = normalize_runtime_loop_settings(RuntimeLoopSettings(*values))
    if tuple(values) != (
        normalized.max_agent_steps_per_turn,
        normalized.max_tool_calls_per_step,
        normalized.max_tool_calls_per_turn,
    ):
        raise ToolSettingsError("FIELD_INVALID", "工具循环上限超出允许范围。", field="runtimeLimits")
    return normalized


def _limits_from_document(document: Mapping[str, Any]) -> RuntimeLoopSettings:
    defaults = RuntimeLoopSettings()
    values = _mapping(document.get("tool_loop"))

    def integer(name: str, default: int) -> int:
        value = values.get(name, default)
        return value if isinstance(value, int) and not isinstance(value, bool) else default

    return normalize_runtime_loop_settings(
        RuntimeLoopSettings(
            integer("max_agent_steps_per_turn", defaults.max_agent_steps_per_turn),
            integer("max_tool_calls_per_step", defaults.max_tool_calls_per_step),
            integer("max_tool_calls_per_turn", defaults.max_tool_calls_per_turn),
        )
    )


def _mapping(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _snapshot(limits: RuntimeLoopSettings) -> dict[str, object]:
    return {
        "schemaVersion": 1,
        "runtimeLimits": {
            "maxAgentStepsPerTurn": limits.max_agent_steps_per_turn,
            "maxToolCallsPerStep": limits.max_tool_calls_per_step,
            "maxToolCallsPerTurn": limits.max_tool_calls_per_turn,
        },
    }


__all__ = [
    "TOOL_SETTINGS_REQUEST_NAMES",
    "ToolSettingsBoundary",
    "ToolSettingsError",
    "load_tool_runtime_configuration",
]
