"""Qt-free Runtime v2 plugin settings boundary for one Core generation."""

from __future__ import annotations

import hashlib
import hmac
import json
import threading
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

import yaml

from app.core_host.protocol import response
from app.plugins.discovery import PluginDiscovery
from app.storage.atomic import atomic_write_text
from app.storage.paths import StoragePaths


PLUGIN_SETTINGS_REQUEST_NAMES = frozenset(
    {"plugins.settings.get", "plugins.settings.save", "plugins.settings.action"}
)
_PLUGIN_STATES = frozenset({"disabled", "starting", "ready", "degraded", "stopping", "stopped"})


class PluginSettingsError(ValueError):
    def __init__(self, code: str, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable

    def public_error(self) -> dict[str, object]:
        return {
            "code": self.code,
            "message": self.message,
            "retryable": self.retryable,
            "details": {"feature": "plugins.manage", "field": ""},
        }


class PluginSettingsBoundary:
    def __init__(
        self,
        generation_id: str,
        generation_credential: str,
        app_root: Path,
        *,
        session_provider: Callable[[], object | None],
    ) -> None:
        self._generation_id = generation_id
        self._generation_credential = generation_credential
        self._app_root = Path(app_root)
        self._config_path = StoragePaths(app_root).plugins_config()
        self._session_provider = session_provider
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
                raise PluginSettingsError("INVALID_REQUEST", "插件设置请求格式无效。")
            name = request.get("name")
            if name == "plugins.settings.get":
                if payload:
                    raise PluginSettingsError("INVALID_REQUEST", "插件设置读取请求必须为空。")
                result = self.snapshot()
            elif name == "plugins.settings.save":
                if set(payload) != {"revision", "settings"}:
                    raise PluginSettingsError("INVALID_REQUEST", "插件设置保存请求格式无效。")
                result = self.save(payload["revision"], payload["settings"])
            elif name == "plugins.settings.action":
                if set(payload) != {"pluginId", "sectionId", "actionId", "values"}:
                    raise PluginSettingsError("INVALID_REQUEST", "插件设置动作格式无效。")
                result = self.action(payload)
            else:
                raise PluginSettingsError("UNKNOWN_COMMAND", "不支持的插件设置命令。")
            return response(
                request,
                generation_id=self._generation_id,
                generation_credential=self._generation_credential,
                protocol_minor=2,
                payload=result,
            )
        except PluginSettingsError as error:
            return response(
                request,
                generation_id=self._generation_id,
                generation_credential=self._generation_credential,
                protocol_minor=2,
                error=error.public_error(),
            )

    def snapshot(self) -> dict[str, object]:
        worker = self._worker()
        if worker is None:
            plugins = [_preview_plugin(spec) for spec in PluginDiscovery(self._app_root).discover()[:64]]
            state = "starting"
            reason = "SESSION_NOT_READY"
        else:
            try:
                state = getattr(worker, "state", "degraded")
                state = state if state in _PLUGIN_STATES else "degraded"
                reason = getattr(worker, "reason_code", "STATUS_INVALID")
                if state == "starting":
                    public = getattr(worker, "public_snapshot")()
                    plugins = [
                        _project_plugin(item)
                        for item in public.get("plugins", [])[:64]
                        if isinstance(item, Mapping)
                    ]
                    if not plugins:
                        plugins = [
                            _preview_plugin(spec)
                            for spec in PluginDiscovery(self._app_root).discover()[:64]
                        ]
                else:
                    raw = getattr(worker, "settings_snapshot")()
                    plugins = raw.get("plugins", []) if isinstance(raw, Mapping) else []
                    plugins = [_project_plugin(item) for item in plugins[:64] if isinstance(item, Mapping)]
            except Exception:
                public = getattr(worker, "public_snapshot")()
                plugins = [
                    _project_plugin(item)
                    for item in public.get("plugins", [])[:64]
                    if isinstance(item, Mapping)
                ]
                state = "degraded"
                reason = "PLUGIN_SETTINGS_UNAVAILABLE"
        return {
            "schemaVersion": 1,
            "revision": self._revision(),
            "state": state,
            "reasonCode": _reason_code(reason, "STATUS_INVALID"),
            "plugins": plugins,
        }

    def save(self, raw_revision: object, raw_settings: object) -> dict[str, object]:
        revision = _revision_value(raw_revision)
        settings = _object(raw_settings)
        if set(settings) != {"enabledById", "settingsById"}:
            raise PluginSettingsError("INVALID_REQUEST", "插件设置字段无效。")
        enabled = _boolean_mapping(settings["enabledById"])
        section_values = _settings_mapping(settings["settingsById"])
        with self._save_lock:
            if revision != self._revision():
                raise PluginSettingsError("CONFIG_REVISION_CONFLICT", "插件设置已被其他窗口修改。", retryable=True)
            specs = {spec.plugin_id: spec for spec in PluginDiscovery(self._app_root).discover()}
            if any(plugin_id not in specs for plugin_id in enabled | section_values):
                raise PluginSettingsError("PLUGIN_ID_INVALID", "插件标识无效。")
            if any(specs[plugin_id].required and not value for plugin_id, value in enabled.items()):
                raise PluginSettingsError("REQUIRED_PLUGIN_LOCKED", "必需插件不能禁用。")
            worker = self._worker()
            if section_values and worker is None:
                raise PluginSettingsError("PLUGIN_SETTINGS_NOT_READY", "插件设置仍在初始化。", retryable=True)
            for plugin_id, sections in section_values.items():
                for section_id, values in sections.items():
                    try:
                        getattr(worker, "settings_save")(plugin_id, section_id, values)
                    except Exception as error:
                        code = str(getattr(error, "code", "SETTINGS_SAVE_FAILED"))
                        raise PluginSettingsError(code, "插件详细设置保存失败。") from error
            if enabled:
                self._save_enabled(specs, enabled)
        result = self.snapshot()
        result.update(saved=True, changePlan="core_restart_required")
        return result

    def action(self, payload: Mapping[str, Any]) -> dict[str, object]:
        worker = self._worker()
        if worker is None:
            raise PluginSettingsError("PLUGIN_SETTINGS_NOT_READY", "插件设置仍在初始化。", retryable=True)
        plugin_id = _identifier(payload.get("pluginId"))
        section_id = _identifier(payload.get("sectionId"))
        action_id = _identifier(payload.get("actionId"))
        values = dict(_object(payload.get("values")))
        try:
            result = getattr(worker, "settings_action")(plugin_id, section_id, action_id, values)
        except Exception as error:
            code = str(getattr(error, "code", "SETTINGS_ACTION_FAILED"))
            raise PluginSettingsError(code, "插件设置动作失败。") from error
        if not isinstance(result, Mapping):
            raise PluginSettingsError("SETTINGS_ACTION_RESULT_INVALID", "插件设置动作响应无效。")
        return dict(result)

    def _worker(self) -> object | None:
        session = self._session_provider()
        return getattr(session, "plugin_worker", None) if session is not None else None

    def _revision(self) -> str:
        try:
            data = self._config_path.read_bytes() if self._config_path.is_file() else b""
        except OSError as error:
            raise PluginSettingsError("CONFIG_READ_FAILED", "插件配置不可读取。") from error
        return hashlib.sha256(data).hexdigest()[:16]

    def _save_enabled(self, specs: Mapping[str, Any], enabled: Mapping[str, bool]) -> None:
        try:
            raw = yaml.safe_load(self._config_path.read_text(encoding="utf-8")) if self._config_path.is_file() else []
        except (OSError, UnicodeError, yaml.YAMLError) as error:
            raise PluginSettingsError("CONFIG_READ_FAILED", "插件配置不可读取。") from error
        entries = list(raw) if isinstance(raw, list) else []
        by_id = {
            str(item.get("id")): dict(item)
            for item in entries
            if isinstance(item, Mapping) and isinstance(item.get("id"), str)
        }
        output = []
        for plugin_id, spec in specs.items():
            item = by_id.pop(plugin_id, {})
            item["id"] = plugin_id
            item["enabled"] = True if spec.required else enabled.get(plugin_id, spec.enabled)
            item.setdefault("priority", spec.priority)
            output.append(item)
        output.extend(by_id.values())
        try:
            atomic_write_text(
                self._config_path,
                yaml.safe_dump(output, allow_unicode=True, sort_keys=False),
            )
        except OSError as error:
            raise PluginSettingsError("CONFIG_SAVE_FAILED", "插件启停保存失败，原文件保持不变。", retryable=True) from error


def _preview_plugin(spec: Any) -> dict[str, object]:
    return {
        "pluginId": spec.plugin_id[:64],
        "name": (spec.name or spec.plugin_id)[:120],
        "version": spec.version[:64],
        "author": spec.author[:120],
        "description": spec.description[:500],
        "enabled": bool(spec.enabled or spec.required),
        "required": bool(spec.required),
        "supported": spec.api_version == 2,
        "state": "starting" if spec.enabled else "disabled",
        "reasonCode": "SESSION_NOT_READY" if spec.enabled else "PLUGIN_DISABLED",
        "permissions": [str(item)[:64] for item in spec.permissions[:32]],
        "unavailable": [],
        "sections": [],
    }


def _project_plugin(raw: Mapping[str, Any]) -> dict[str, object]:
    return {
        "pluginId": _safe_identifier(raw.get("pluginId"), "plugin"),
        "name": _text(raw.get("name"), 120, "Plugin"),
        "version": _text(raw.get("version"), 64, "0.0.0"),
        "author": _text(raw.get("author"), 120, ""),
        "description": _text(raw.get("description"), 500, ""),
        "enabled": bool(raw.get("enabled")),
        "required": bool(raw.get("required")),
        "supported": bool(raw.get("supported")),
        "state": raw.get("state") if raw.get("state") in _PLUGIN_STATES else "degraded",
        "reasonCode": _reason_code(raw.get("reasonCode"), "STATUS_INVALID"),
        "permissions": [_safe_identifier(item, "permission") for item in raw.get("permissions", [])[:32]] if isinstance(raw.get("permissions"), list) else [],
        "unavailable": [_safe_identifier(item, "unavailable") for item in raw.get("unavailable", [])[:16]] if isinstance(raw.get("unavailable"), list) else [],
        "sections": raw.get("sections", [])[:16] if isinstance(raw.get("sections"), list) else [],
    }


def _object(value: object) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise PluginSettingsError("INVALID_REQUEST", "插件设置必须是 object。")
    return value


def _boolean_mapping(value: object) -> dict[str, bool]:
    raw = _object(value)
    if len(raw) > 64:
        raise PluginSettingsError("INVALID_REQUEST", "插件启停项过多。")
    result: dict[str, bool] = {}
    for key, item in raw.items():
        if not isinstance(key, str) or not isinstance(item, bool):
            raise PluginSettingsError("INVALID_REQUEST", "插件启停字段无效。")
        result[_identifier(key)] = item
    return result


def _settings_mapping(value: object) -> dict[str, dict[str, dict[str, Any]]]:
    raw = _object(value)
    if len(raw) > 64:
        raise PluginSettingsError("INVALID_REQUEST", "插件设置项过多。")
    result: dict[str, dict[str, dict[str, Any]]] = {}
    for plugin_id, sections in raw.items():
        section_map = _object(sections)
        if len(section_map) > 16:
            raise PluginSettingsError("INVALID_REQUEST", "插件设置区块过多。")
        result[_identifier(plugin_id)] = {
            _identifier(section_id): dict(_object(values))
            for section_id, values in section_map.items()
        }
    if len(json.dumps(result, ensure_ascii=False).encode("utf-8")) > 64 * 1024:
        raise PluginSettingsError("INVALID_REQUEST", "插件设置内容过大。")
    return result


def _identifier(value: object) -> str:
    if not isinstance(value, str) or not value or len(value) > 64 or any(
        not (char.isascii() and (char.isalnum() or char in "_.-")) for char in value
    ):
        raise PluginSettingsError("INVALID_REQUEST", "插件标识无效。")
    return value


def _safe_identifier(value: object, default: str) -> str:
    try:
        return _identifier(value)
    except PluginSettingsError:
        return default


def _revision_value(value: object) -> str:
    if not isinstance(value, str) or len(value) != 16 or any(char not in "0123456789abcdef" for char in value):
        raise PluginSettingsError("INVALID_REQUEST", "插件设置 revision 无效。")
    return value


def _reason_code(value: object, default: str) -> str:
    if isinstance(value, str) and 1 <= len(value) <= 64 and all(
        char.isascii() and (char.isupper() or char.isdigit() or char == "_") for char in value
    ):
        return value
    return default


def _text(value: object, limit: int, default: str) -> str:
    return value[:limit] if isinstance(value, str) else default


__all__ = ["PLUGIN_SETTINGS_REQUEST_NAMES", "PluginSettingsBoundary", "PluginSettingsError"]
