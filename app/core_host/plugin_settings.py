"""Qt-free Runtime v2 plugin settings boundary for one Core generation."""

from __future__ import annotations

import hashlib
import hmac
import json
import threading
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from app.core_host.protocol import response
from app.plugins.discovery import PluginDiscovery
from app.plugins.installer import LocalPluginInstaller, PluginInstallError
from app.plugins.models import PLUGIN_API_V3_VERSION
from app.storage.paths import StoragePaths


PLUGIN_SETTINGS_REQUEST_NAMES = frozenset(
    {
        "plugins.settings.get",
        "plugins.settings.save",
        "plugins.settings.action",
        "plugins.install",
        "plugins.uninstall",
        "plugins.collection.query",
        "plugins.collection.create",
        "plugins.collection.update",
        "plugins.collection.delete",
    }
)
_PLUGIN_STATES = frozenset(
    {
        "disabled",
        "starting",
        "ready",
        "degraded",
        "stopping",
        "stopped",
        "waiting",
        "active",
        "failed",
        "conflict",
    }
)


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
            elif name == "plugins.install":
                if set(payload) != {"revision", "sourceKind", "sourcePath"}:
                    raise PluginSettingsError("INVALID_REQUEST", "插件安装请求格式无效。")
                result = self.install(
                    payload["revision"],
                    payload["sourceKind"],
                    payload["sourcePath"],
                )
            elif name == "plugins.uninstall":
                if set(payload) != {"revision", "pluginId"}:
                    raise PluginSettingsError("INVALID_REQUEST", "插件卸载请求格式无效。")
                result = self.uninstall(payload["revision"], payload["pluginId"])
            elif isinstance(name, str) and name.startswith("plugins.collection."):
                result = self.collection(name.rsplit(".", 1)[-1], payload)
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
            if (section_values or enabled) and worker is None:
                raise PluginSettingsError("PLUGIN_SETTINGS_NOT_READY", "插件设置仍在初始化。", retryable=True)
            application_states: list[str] = []
            for plugin_id, sections in section_values.items():
                for section_id, values in sections.items():
                    try:
                        saved = getattr(worker, "settings_save")(plugin_id, section_id, values)
                    except Exception as error:
                        code = str(getattr(error, "code", "SETTINGS_SAVE_FAILED"))
                        raise PluginSettingsError(code, "插件详细设置保存失败。") from error
                    application_states.append(_application_state(saved))
            for plugin_id, value in enabled.items():
                try:
                    getattr(worker, "set_plugin_enabled")(plugin_id, value)
                except Exception as error:
                    code = str(getattr(error, "code", "PLUGIN_LIFECYCLE_FAILED"))
                    raise PluginSettingsError(code, "插件启停未能在当前运行时应用。") from error
                application_states.append("applied")
        application_state = _aggregate_application_state(application_states)
        change_plan = (
            "plugin_reload_required"
            if application_state in {"restart_required", "error"}
            else "applied"
        )
        application_reason = {
            "applied": "READY",
            "restart_required": "CONFIG_RELOAD_REQUIRED",
            "error": "CONFIG_APPLY_FAILED",
        }[application_state]
        result = self.snapshot()
        result.update(
            saved=True,
            changePlan=change_plan,
            applicationState=application_state,
            applicationReasonCode=application_reason,
        )
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

    def install(
        self,
        raw_revision: object,
        raw_source_kind: object,
        raw_source_path: object,
    ) -> dict[str, object]:
        revision = _revision_value(raw_revision)
        if raw_source_kind not in {"zip", "folder"}:
            raise PluginSettingsError("INVALID_REQUEST", "插件安装来源无效。")
        if (
            not isinstance(raw_source_path, str)
            or not raw_source_path
            or len(raw_source_path) > 4096
            or not Path(raw_source_path).is_absolute()
        ):
            raise PluginSettingsError("INVALID_REQUEST", "插件安装路径无效。")
        with self._save_lock:
            if revision != self._revision():
                raise PluginSettingsError(
                    "CONFIG_REVISION_CONFLICT",
                    "插件设置已被其他窗口修改。",
                    retryable=True,
                )
            worker = self._worker()
            if worker is None:
                raise PluginSettingsError(
                    "PLUGIN_SETTINGS_NOT_READY",
                    "插件设置仍在初始化。",
                    retryable=True,
                )
            installer = LocalPluginInstaller(self._app_root)
            try:
                installed = installer.install(Path(raw_source_path), str(raw_source_kind))
            except PluginInstallError as error:
                raise PluginSettingsError(error.code, "本地插件安装失败。") from error
            try:
                getattr(worker, "rebuild")()
            except Exception as apply_error:
                rollback_error: PluginInstallError | None = None
                recovery_error: Exception | None = None
                try:
                    installer.remove_installed_code(installed)
                except PluginInstallError as error:
                    rollback_error = error
                try:
                    getattr(worker, "rebuild")()
                except Exception as error:
                    recovery_error = error
                code = (
                    rollback_error.code
                    if rollback_error is not None
                    else "PLUGIN_INSTALL_RECOVERY_FAILED"
                    if recovery_error is not None
                    else str(getattr(apply_error, "code", "PLUGIN_INSTALL_APPLY_FAILED"))
                )
                raise PluginSettingsError(
                    code,
                    "插件安装未能应用到当前运行时。",
                ) from apply_error
        result = self.snapshot()
        result.update(managementAction="installed", pluginId=installed.plugin_id)
        return result

    def uninstall(self, raw_revision: object, raw_plugin_id: object) -> dict[str, object]:
        revision = _revision_value(raw_revision)
        plugin_id = _identifier(raw_plugin_id)
        with self._save_lock:
            if revision != self._revision():
                raise PluginSettingsError(
                    "CONFIG_REVISION_CONFLICT",
                    "插件设置已被其他窗口修改。",
                    retryable=True,
                )
            worker = self._worker()
            if worker is None:
                raise PluginSettingsError(
                    "PLUGIN_SETTINGS_NOT_READY",
                    "插件设置仍在初始化。",
                    retryable=True,
                )
            installer = LocalPluginInstaller(self._app_root)
            try:
                pending = installer.begin_uninstall(plugin_id)
            except PluginInstallError as error:
                raise PluginSettingsError(error.code, "本地插件卸载失败。") from error
            try:
                getattr(worker, "rebuild")()
            except Exception as apply_error:
                rollback_error: PluginInstallError | None = None
                recovery_error: Exception | None = None
                try:
                    installer.rollback_uninstall(pending)
                except PluginInstallError as error:
                    rollback_error = error
                try:
                    getattr(worker, "rebuild")()
                except Exception as error:
                    recovery_error = error
                code = (
                    rollback_error.code
                    if rollback_error is not None
                    else "PLUGIN_UNINSTALL_RECOVERY_FAILED"
                    if recovery_error is not None
                    else str(getattr(apply_error, "code", "PLUGIN_UNINSTALL_APPLY_FAILED"))
                )
                raise PluginSettingsError(
                    code,
                    "插件卸载未能应用到当前运行时。",
                ) from apply_error
            try:
                installer.commit_uninstall(pending)
            except PluginInstallError as error:
                raise PluginSettingsError(
                    error.code,
                    "插件已停止，但残留代码清理失败。",
                ) from error
        result = self.snapshot()
        result.update(managementAction="uninstalled", pluginId=plugin_id)
        return result

    def collection(
        self,
        operation: str,
        payload: Mapping[str, Any],
    ) -> dict[str, object]:
        expected = {
            "query": {"pluginId", "sectionId", "collectionId", "cursor", "limit", "search", "filters"},
            "create": {"pluginId", "sectionId", "collectionId", "values"},
            "update": {"pluginId", "sectionId", "collectionId", "itemId", "values"},
            "delete": {"pluginId", "sectionId", "collectionId", "itemId"},
        }.get(operation)
        if expected is None or set(payload) != expected:
            raise PluginSettingsError("INVALID_REQUEST", "插件 Collection 请求格式无效。")
        worker = self._worker()
        if worker is None:
            raise PluginSettingsError("PLUGIN_SETTINGS_NOT_READY", "插件设置仍在初始化。", retryable=True)
        plugin_id = _identifier(payload.get("pluginId"))
        section_id = _identifier(payload.get("sectionId"))
        collection_id = _identifier(payload.get("collectionId"))
        arguments = {
            key: value
            for key, value in payload.items()
            if key not in {"pluginId", "sectionId", "collectionId"}
        }
        if len(json.dumps(arguments, ensure_ascii=False).encode("utf-8")) > 64 * 1024:
            raise PluginSettingsError("INVALID_REQUEST", "插件 Collection 请求内容过大。")
        try:
            result = getattr(worker, "settings_collection")(
                operation,
                plugin_id,
                section_id,
                collection_id,
                arguments,
            )
        except Exception as error:
            code = str(getattr(error, "code", "SETTINGS_COLLECTION_FAILED"))
            raise PluginSettingsError(code, "插件 Collection 操作失败。") from error
        if (
            not isinstance(result, Mapping)
            or len(json.dumps(result, ensure_ascii=False).encode("utf-8")) > 256 * 1024
        ):
            raise PluginSettingsError(
                "SETTINGS_COLLECTION_RESULT_INVALID",
                "插件 Collection 响应无效。",
            )
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

def _preview_plugin(spec: Any) -> dict[str, object]:
    supported = spec.api_version == PLUGIN_API_V3_VERSION
    source = spec.source if spec.source in {"bundled", "user"} else "bundled"
    invalid_user_required = source == "user" and bool(spec.required)
    required = bool(spec.required and source != "user")
    enabled = bool(spec.enabled or (supported and required))
    return {
        "pluginId": spec.plugin_id[:64],
        "name": (spec.name or spec.plugin_id)[:120],
        "version": spec.version[:64],
        "author": spec.author[:120],
        "description": spec.description[:500],
        "enabled": enabled,
        "required": required,
        "source": source,
        "canUninstall": source == "user",
        "supported": supported,
        "state": (
            "failed"
            if invalid_user_required
            else "starting"
            if supported and enabled
            else "disabled"
            if supported
            else "failed"
        ),
        "reasonCode": (
            "PLUGIN_MANIFEST_INVALID"
            if invalid_user_required
            else "SESSION_NOT_READY"
            if supported and enabled
            else "PLUGIN_DISABLED"
            if supported
            else "API_VERSION_UNSUPPORTED"
        ),
        "permissions": [],
        "unavailable": [],
        "sections": [],
    }


def _project_plugin(raw: Mapping[str, Any]) -> dict[str, object]:
    source = raw.get("source") if raw.get("source") in {"bundled", "user"} else "bundled"
    return {
        "pluginId": _safe_identifier(raw.get("pluginId"), "plugin"),
        "name": _text(raw.get("name"), 120, "Plugin"),
        "version": _text(raw.get("version"), 64, "0.0.0"),
        "author": _text(raw.get("author"), 120, ""),
        "description": _text(raw.get("description"), 500, ""),
        "enabled": bool(raw.get("enabled")),
        "required": bool(raw.get("required")) and source != "user",
        "source": source,
        "canUninstall": source == "user",
        "supported": bool(raw.get("supported")),
        "state": raw.get("state") if raw.get("state") in _PLUGIN_STATES else "degraded",
        "reasonCode": _reason_code(raw.get("reasonCode"), "STATUS_INVALID"),
        "permissions": [],
        "unavailable": [],
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


def _application_state(result: object) -> str:
    if not isinstance(result, Mapping):
        return "applied"
    state = result.get("applicationState", "applied")
    if state not in {"applied", "restart_required", "error"}:
        raise PluginSettingsError("SETTINGS_SAVE_RESULT_INVALID", "插件设置应用状态无效。")
    return str(state)


def _aggregate_application_state(states: list[str]) -> str:
    if "error" in states:
        return "error"
    if "restart_required" in states:
        return "restart_required"
    return "applied"


__all__ = ["PLUGIN_SETTINGS_REQUEST_NAMES", "PluginSettingsBoundary", "PluginSettingsError"]
