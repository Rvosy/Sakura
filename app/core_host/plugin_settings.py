"""Qt-free Runtime v2 plugin settings boundary for one Core generation."""

from __future__ import annotations

import hmac
import json
import threading
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from app.core_host.protocol import response
from app.plugins.inventory import PluginInventory
from app.plugins.installer import LocalPluginInstaller, PluginInstallError
from app.plugins.models import PLUGIN_API_V3_VERSION
from app.storage.paths import StoragePaths


PLUGIN_SETTINGS_REQUEST_NAMES = frozenset(
    {
        "plugins.settings.get",
        "plugins.settings.save",
        "plugins.enabled.set",
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
        "active",
        "failed",
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
        application_provider: Callable[[], object | None] | None = None,
        session_provider: Callable[[], object | None] | None = None,
    ) -> None:
        self._generation_id = generation_id
        self._generation_credential = generation_credential
        self._app_root = Path(app_root)
        self._config_path = StoragePaths(app_root).plugins_config()
        self._application_provider = application_provider
        self._session_provider = session_provider or (lambda: None)
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
                if set(payload) != {"pluginId", "sectionId", "values"}:
                    raise PluginSettingsError("INVALID_REQUEST", "插件设置保存请求格式无效。")
                result = self.save(payload)
            elif name == "plugins.enabled.set":
                if set(payload) != {"revision", "installId", "enabled"}:
                    raise PluginSettingsError("INVALID_REQUEST", "插件启停请求格式无效。")
                result = self.set_enabled(
                    payload["revision"],
                    payload["installId"],
                    payload["enabled"],
                )
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
                if set(payload) != {"revision", "installId"}:
                    raise PluginSettingsError("INVALID_REQUEST", "插件卸载请求格式无效。")
                result = self.uninstall(payload["revision"], payload["installId"])
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
        inventory = PluginInventory(self._app_root).scan()
        if worker is None:
            plugins = [_preview_plugin(record) for record in inventory.records[:64]]
            state = "starting"
            reason = "PLUGIN_APPLICATION_NOT_READY"
        else:
            try:
                state = getattr(worker, "state", "degraded")
                state = state if state in _PLUGIN_STATES else "degraded"
                reason = getattr(worker, "reason_code", "STATUS_INVALID")
                if state == "starting":
                    public = getattr(worker, "public_snapshot")()
                    plugins = [
                        item
                        for item in public.get("plugins", [])[:64]
                        if isinstance(item, Mapping)
                    ]
                    if not plugins:
                        plugins = [_preview_plugin(record) for record in inventory.records[:64]]
                    else:
                        plugins = _project_plugins(plugins, inventory)
                else:
                    raw = getattr(worker, "settings_snapshot")()
                    plugins = raw.get("plugins", []) if isinstance(raw, Mapping) else []
                    plugins = _project_plugins(
                        [item for item in plugins[:64] if isinstance(item, Mapping)],
                        inventory,
                    )
            except Exception:
                public = getattr(worker, "public_snapshot")()
                plugins = _project_plugins(
                    [
                        item
                        for item in public.get("plugins", [])[:64]
                        if isinstance(item, Mapping)
                    ],
                    inventory,
                )
                state = "degraded"
                reason = "PLUGIN_SETTINGS_UNAVAILABLE"
        return {
            "schemaVersion": 2,
            "revision": self._revision(),
            "state": state,
            "reasonCode": _reason_code(reason, "STATUS_INVALID"),
            "plugins": plugins,
        }

    def save(self, payload: Mapping[str, Any]) -> dict[str, object]:
        plugin_id = _identifier(payload.get("pluginId"))
        section_id = _identifier(payload.get("sectionId"))
        values = dict(_object(payload.get("values")))
        if len(json.dumps(values, ensure_ascii=False).encode("utf-8")) > 64 * 1024:
            raise PluginSettingsError("INVALID_REQUEST", "插件设置内容过大。")
        with self._save_lock:
            worker = self._worker()
            if worker is None:
                raise PluginSettingsError("PLUGIN_SETTINGS_NOT_READY", "插件设置仍在初始化。", retryable=True)
            try:
                saved = getattr(worker, "settings_save")(plugin_id, section_id, values)
            except Exception as error:
                code = str(getattr(error, "code", "SETTINGS_SAVE_FAILED"))
                raise PluginSettingsError(code, "插件详细设置保存失败。") from error
        if _application_state(saved) != "applied":
            raise PluginSettingsError(
                "CONFIG_APPLY_FAILED",
                "插件设置已保存，但应用失败。",
            )
        return {
            "saved": True,
            "pluginId": plugin_id,
            "sectionId": section_id,
            "changePlan": "applied",
            "applicationState": "applied",
            "applicationReasonCode": "READY",
        }

    def set_enabled(
        self,
        raw_revision: object,
        raw_install_id: object,
        raw_enabled: object,
    ) -> dict[str, object]:
        revision = _revision_value(raw_revision)
        install_id = _install_identifier(raw_install_id)
        if not isinstance(raw_enabled, bool):
            raise PluginSettingsError("INVALID_REQUEST", "插件启停值无效。")
        with self._save_lock:
            if revision != self._revision():
                raise PluginSettingsError("CONFIG_REVISION_CONFLICT", "插件列表已变化。", retryable=True)
            application = self._worker()
            if application is None:
                raise PluginSettingsError("PLUGIN_SETTINGS_NOT_READY", "插件设置仍在初始化。", retryable=True)
            try:
                result = getattr(application, "set_enabled")(install_id, raw_enabled)
            except Exception as error:
                code = str(getattr(error, "code", "PLUGIN_LIFECYCLE_FAILED"))
                raise PluginSettingsError(code, "插件启停未能应用。") from error
        return dict(result)

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
        result.update(
            managementAction="installed",
            installId=installed.install_id,
            pluginId=installed.plugin_id,
        )
        return result

    def uninstall(self, raw_revision: object, raw_install_id: object) -> dict[str, object]:
        revision = _revision_value(raw_revision)
        install_id = _install_identifier(raw_install_id)
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
                pending = installer.begin_uninstall(install_id)
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
        result.update(
            managementAction="uninstalled",
            installId=install_id,
            pluginId=pending.plugin_id,
        )
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
        if self._application_provider is not None:
            return self._application_provider()
        session = self._session_provider()
        return getattr(session, "plugin_worker", None) if session is not None else None

    def _revision(self) -> str:
        return PluginInventory(self._app_root).scan().revision

def _preview_plugin(spec: Any) -> dict[str, object]:
    supported = bool(spec.supported)
    source = spec.source
    required = bool(spec.required)
    enabled = bool(spec.desired_enabled)
    return {
        "installId": spec.install_id,
        "pluginId": spec.plugin_id,
        "name": spec.name[:120],
        "version": spec.version[:64],
        "author": spec.author[:120],
        "description": spec.description[:500],
        "enabled": enabled,
        "required": required,
        "source": source,
        "canUninstall": source == "user",
        "supported": supported,
        "provides": list(spec.provides),
        "requires": list(spec.requires),
        "missingServices": [],
        "state": (
            "failed" if supported and enabled else "disabled" if supported else "failed"
        ),
        "reasonCode": "PLUGIN_APPLICATION_NOT_READY" if supported and enabled else spec.reason_code,
        "sections": [],
    }


def _project_plugins(
    values: list[Mapping[str, Any]],
    inventory: Any,
) -> list[dict[str, object]]:
    if values and all(isinstance(item.get("installId"), str) for item in values):
        return [_project_plugin(item) for item in values]
    by_plugin_id = {
        item.get("pluginId"): item
        for item in values
        if isinstance(item.get("pluginId"), str)
    }
    return [
        _project_plugin(by_plugin_id.get(record.plugin_id, {}), record=record)
        for record in inventory.records[:64]
    ]


def _project_plugin(
    raw: Mapping[str, Any],
    *,
    record: Any | None = None,
) -> dict[str, object]:
    if record is not None:
        preview = _preview_plugin(record)
        preview.update(raw)
        raw = preview
    source = raw.get("source") if raw.get("source") in {"bundled", "user"} else "bundled"
    plugin_id = raw.get("pluginId")
    if plugin_id is not None:
        plugin_id = _identifier(plugin_id)
    return {
        "installId": _install_identifier(raw.get("installId")),
        "pluginId": plugin_id,
        "name": _text(raw.get("name"), 120, "Plugin"),
        "version": _text(raw.get("version"), 64, "0.0.0"),
        "author": _text(raw.get("author"), 120, ""),
        "description": _text(raw.get("description"), 500, ""),
        "enabled": bool(raw.get("enabled")),
        "required": bool(raw.get("required")) and source != "user",
        "source": source,
        "canUninstall": source == "user",
        "supported": bool(raw.get("supported")),
        "provides": _identifier_list(raw.get("provides")),
        "requires": _identifier_list(raw.get("requires")),
        "missingServices": _identifier_list(raw.get("missingServices")),
        "state": raw.get("state") if raw.get("state") in {"disabled", "active", "failed"} else "failed",
        "reasonCode": _reason_code(raw.get("reasonCode"), "STATUS_INVALID"),
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


def _install_identifier(value: object) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 27
        or not value.startswith("pi_")
        or any(character not in "0123456789abcdef" for character in value[3:])
    ):
        raise PluginSettingsError("INVALID_REQUEST", "插件安装标识无效。")
    return value


def _identifier_list(value: object) -> list[str]:
    if not isinstance(value, list) or len(value) > 64:
        return []
    result: list[str] = []
    for item in value:
        if (
            not isinstance(item, str)
            or not item
            or len(item) > 200
            or any(
                not (character.isascii() and (character.isalnum() or character in "_.-"))
                for character in item
            )
        ):
            return []
        result.append(item)
    return result


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


__all__ = ["PLUGIN_SETTINGS_REQUEST_NAMES", "PluginSettingsBoundary", "PluginSettingsError"]
