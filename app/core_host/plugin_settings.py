"""Qt-free Runtime v2 plugin settings boundary for one Core generation."""

from __future__ import annotations

import hmac
import json
import threading
from time import perf_counter
import yaml
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from app.core_host.protocol import response
from app.core.diagnostics import exception_diagnostics
from app.core.runtime_log import log_event
from app.plugins import update_transaction
from app.plugins.inventory import INSTALL_ID_PATTERN, PluginDesiredStateStore, PluginInventory
from app.plugins.installer import LocalPluginInstaller, PluginInstallError
from app.storage.paths import StoragePaths, sanitize_directory_component
from app.storage.runtime_roots import RuntimeRoots, coerce_runtime_roots


PLUGIN_SETTINGS_REQUEST_NAMES = frozenset(
    {
        "plugins.settings.get",
        "plugins.frontend.get",
        "plugins.settings.save",
        "plugins.enabled.set",
        "plugins.settings.action",
        "plugins.install",
        "plugins.marketplace.context",
        "plugins.marketplace.install",
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
    def __init__(self, code: str, message: str, *, retryable: bool = False, recovery_error: BaseException | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable
        self.recovery_error = recovery_error

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
        roots: RuntimeRoots | Path,
        *,
        application_provider: Callable[[], object | None] | None = None,
        initialization_failed: Callable[[], bool] | None = None,
    ) -> None:
        self._generation_id = generation_id
        self._generation_credential = generation_credential
        self._roots = coerce_runtime_roots(roots)
        self._user_root = self._roots.user_root
        self._config_path = StoragePaths(self._user_root).plugins_config()
        self._application_provider = application_provider
        self._initialization_failed = initialization_failed or (lambda: False)
        self._save_lock = threading.Lock()
        self._install_locks: dict[str, threading.Lock] = {}

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
            if name == "plugins.frontend.get":
                if set(payload) != {"serviceKey", "moduleName"}:
                    raise PluginSettingsError("INVALID_REQUEST", "插件界面模块请求格式无效。")
                result = self.frontend_module(payload["serviceKey"], payload["moduleName"])
            elif name == "plugins.settings.get":
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
            elif name == "plugins.marketplace.context":
                application = self._application()
                if payload or application is None:
                    raise PluginSettingsError("PLUGIN_SETTINGS_NOT_READY", "插件服务尚未就绪。")
                result = application.marketplace_context()
            elif name == "plugins.marketplace.install":
                if set(payload) != {"revision", "sourcePath", "pluginId", "version"}:
                    raise PluginSettingsError("INVALID_REQUEST", "市场安装请求格式无效。")
                result = self.marketplace_install(payload)
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
        diagnostics = {}
        application = self._application()
        inventory = self._refresh_inventory(application)
        if application is None:
            plugins = [_project_plugin({}, record=record) for record in inventory.records]
            state = "starting"
            reason = "PLUGIN_APPLICATION_NOT_READY"
            if self._initialization_failed():
                state = "failed"
                reason = "PLUGIN_APPLICATION_FAILED"
                for plugin in plugins:
                    if plugin["state"] == "starting":
                        plugin.update(state="failed", reasonCode=reason)
        else:
            try:
                state = getattr(application, "state", "degraded")
                state = state if state in _PLUGIN_STATES else "degraded"
                reason = getattr(application, "reason_code", "STATUS_INVALID")
                raw = getattr(application, "settings_snapshot")()
                if isinstance(raw, Mapping):
                    state = raw.get("state", state)
                    state = state if state in _PLUGIN_STATES else "degraded"
                    reason = raw.get("reasonCode", reason)
                plugins = raw.get("plugins", []) if isinstance(raw, Mapping) else []
                plugins = _project_plugins(
                    [item for item in plugins if isinstance(item, Mapping)],
                    inventory,
                )
            except Exception as error:
                diagnostics = exception_diagnostics(error, reason_code="PLUGIN_SETTINGS_UNAVAILABLE", stage="settings.snapshot")
                log_event("Plugin", "插件设置读取失败", diagnostics, event="plugin.settings.failed", severity="debug")
                public = getattr(application, "public_snapshot")()
                plugins = _project_plugins(
                    [
                        item
                        for item in public.get("plugins", [])
                        if isinstance(item, Mapping)
                    ],
                    inventory,
                )
                state = "degraded"
                reason = "PLUGIN_SETTINGS_UNAVAILABLE"
        return {
            "schemaVersion": 1,
            "revision": inventory.revision,
            "state": state,
            "reasonCode": _reason_code(reason, "STATUS_INVALID"),
            **({"diagnostics": diagnostics} if diagnostics else {}),
            "plugins": plugins,
        }

    def save(self, payload: Mapping[str, Any]) -> dict[str, object]:
        plugin_id = _identifier(payload.get("pluginId"))
        section_id = _identifier(payload.get("sectionId"))
        values = dict(_object(payload.get("values")))
        with self._save_lock:
            application = self._application()
            if application is None:
                raise PluginSettingsError("PLUGIN_SETTINGS_NOT_READY", "插件设置仍在初始化。", retryable=True)
            try:
                saved = getattr(application, "settings_save")(plugin_id, section_id, values)
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
            application = self._application()
            if application is None:
                raise PluginSettingsError("PLUGIN_SETTINGS_NOT_READY", "插件设置仍在初始化。", retryable=True)
            try:
                result = getattr(application, "set_enabled")(install_id, raw_enabled)
            except Exception as error:
                code = str(getattr(error, "code", "PLUGIN_LIFECYCLE_FAILED"))
                raise PluginSettingsError(code, "插件启停未能应用。") from error
            result = dict(result)
            result["plugins"] = _project_plugins(
                [item for item in result.get("plugins", []) if isinstance(item, Mapping)],
                self._refresh_inventory(application),
            )
            changed = next((item for item in result["plugins"] if item["installId"] == install_id), None)
            if (result.get("applicationState") == "error" and changed is not None
                    and changed["reasonCode"] == "MODEL_API_UPDATE_REQUIRED"):
                result["applicationReasonCode"] = changed["reasonCode"]
        return result

    def action(self, payload: Mapping[str, Any]) -> dict[str, object]:
        application = self._application()
        if application is None:
            raise PluginSettingsError("PLUGIN_SETTINGS_NOT_READY", "插件设置仍在初始化。", retryable=True)
        plugin_id = _identifier(payload.get("pluginId"))
        section_id = _identifier(payload.get("sectionId"))
        action_id = _identifier(payload.get("actionId"))
        values = dict(_object(payload.get("values")))
        try:
            result = getattr(application, "settings_action")(plugin_id, section_id, action_id, values)
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
        *, expected: tuple[str, str] | None = None,
        initial_enabled: bool = False,
    ) -> dict[str, object]:
        _revision_value(raw_revision)
        if raw_source_kind not in {"zip", "folder"}:
            raise PluginSettingsError("INVALID_REQUEST", "插件安装来源无效。")
        if (
            not isinstance(raw_source_path, str)
            or not raw_source_path
            or len(raw_source_path) > 4096
            or not Path(raw_source_path).is_absolute()
        ):
            raise PluginSettingsError("INVALID_REQUEST", "插件安装路径无效。")
        installer = LocalPluginInstaller(self._roots)
        try:
            with installer.prepare_package(Path(raw_source_path), raw_source_kind, expected=expected) as spec:
                with self._save_lock:
                    install_lock = self._install_locks.setdefault(spec.plugin_id, threading.Lock())
                with install_lock:
                    with self._save_lock:
                        application = self._application()
                        if application is None:
                            raise PluginSettingsError("PLUGIN_SETTINGS_NOT_READY", "插件设置仍在初始化。", retryable=True)
                        before = self._installed_record(application, spec.plugin_id)
                        if before is not None:
                            application.check_plugin_update_idle()
                    log_event("Plugin", "正在准备插件安装", {"pluginId": spec.plugin_id, "version": spec.version}, event="plugin.install.preparing")
                    with installer.prepare_installation(spec) as prepared:
                        with self._save_lock:
                            record = self._installed_record(application, spec.plugin_id)
                            if before is not None and (record is None or record.install_id != before.install_id):
                                raise PluginSettingsError("PLUGIN_NOT_FOUND", "原插件已卸载，未应用本次更新。")
                            application.recover_plugin_update(spec.plugin_id, replace_invalid=True)
                            record = self._installed_record(application, spec.plugin_id)
                            if record is not None:
                                current = next(p for p in self.snapshot()["plugins"] if p["installId"] == record.install_id)
                                current["enabled"] = PluginDesiredStateStore(self._user_root).read().get(spec.plugin_id, current["enabled"])
                                return self._update_plugin(application, installer, spec, prepared, record, current)
                            enabled = PluginDesiredStateStore(self._user_root).read().get(spec.plugin_id, initial_enabled)
                            return self._install_plugin(application, installer, spec, prepared, enabled)
        except PluginSettingsError:
            raise
        except Exception as error:
            code = getattr(error, "code", "PLUGIN_INSTALL_FAILED")
            message = "当前互动尚未结束，请结束后重试更新。" if code == "RUNTIME_UPDATE_BUSY" else "插件安装失败。"
            raise PluginSettingsError(code, message, retryable=code == "RUNTIME_UPDATE_BUSY") from error

    def _installed_record(self, application, plugin_id):
        inventory = self._refresh_inventory(application)
        matching = [p for p in inventory.records if p.plugin_id == plugin_id]
        if len(matching) > 1 or any(p.reason_code == "PLUGIN_ID_CONFLICT" for p in matching):
            raise PluginSettingsError("PLUGIN_ID_CONFLICT", "请先卸载重复安装的插件副本。")
        record = next(iter(matching), None)
        if record is None:
            record = next((p for p in inventory.records if p.source == "user" and p.plugin_id is None
                           and p.directory_name == sanitize_directory_component(plugin_id)), None)
        if record is not None and record.source != "user":
            raise PluginSettingsError("BUNDLED_PLUGIN_LOCKED", "内置插件随应用更新。")
        return record

    def _install_plugin(self, application, installer, spec, prepared, initial_enabled):
        installed = installer.install_prepared(spec, prepared, initial_enabled=initial_enabled)
        try:
            getattr(application, "install_plugin")(installed.install_id)
        except Exception as apply_error:
            rollback_error: BaseException | None = None
            try:
                application.uninstall_plugin(installed.plugin_id)
                installer.remove_installed_code(installed)
            except Exception as error:
                rollback_error = error
            application.refresh_inventory()
            code = (
                getattr(rollback_error, "code", "PLUGIN_INSTALL_ROLLBACK_FAILED")
                if rollback_error is not None
                else str(getattr(apply_error, "code", "PLUGIN_INSTALL_APPLY_FAILED"))
            )
            raise PluginSettingsError(
                code,
                "插件安装未能应用到当前运行时。",
                recovery_error=rollback_error,
            ) from apply_error
        result = self.snapshot()
        result.update(managementAction="installed", installId=installed.install_id, pluginId=installed.plugin_id)
        self._add_startup_warning(result, {installed.plugin_id})
        return result

    @staticmethod
    def _add_startup_warning(result, plugin_ids):
        failed = [p for p in result["plugins"] if p["pluginId"] in plugin_ids and p["enabled"] and p["state"] != "active"]
        if failed:
            details = [f'{p["pluginId"]}: {p.get("diagnostics", {}).get("diagnostic") or p["reasonCode"]}' for p in failed]
            if result.get("warning"):
                details.append(result["warning"]["detail"])
            result["warning"] = {"message": "插件已安装，部分插件尚未启动，请查看插件状态。", "detail": "\n".join(details)}

    def frontend_module(self, service_key: object, module_name: object) -> dict[str, object]:
        services = _identifier_list([service_key])
        if not services:
            raise PluginSettingsError("INVALID_REQUEST", "插件服务标识无效。")
        service = services[0]
        name = _identifier(module_name)
        application = self._application()
        providers = [record for record in self._refresh_inventory(application).records
                     if service in record.provides and record.runtime_eligible]
        if application is not None:
            try:
                owner = application.service_identity(service)["providerId"]
            except Exception as error:
                if getattr(error, "code", None) != "SERVICE_MISSING":
                    raise
            else:
                providers = [record for record in providers if record.plugin_id == owner]
        if len(providers) != 1:
            raise PluginSettingsError("PLUGIN_FRONTEND_UNAVAILABLE", "插件界面模块不可用。")
        record = providers[0]
        parent = (self._roots.distribution_root / "plugins/builtin" if record.source == "bundled"
                  else StoragePaths(self._user_root).user_plugins_dir)
        root = (parent / record.directory_name).resolve()
        manifest = yaml.safe_load((root / "plugin.yaml").read_text(encoding="utf-8"))
        modules = manifest.get("frontendModules")
        relative = modules.get(name) if isinstance(modules, Mapping) else None
        if not isinstance(relative, str) or not relative or Path(relative).is_absolute():
            raise PluginSettingsError("PLUGIN_FRONTEND_INVALID", "插件界面模块声明无效。")
        module = (root / relative).resolve()
        if not module.is_relative_to(root) or module.suffix not in {".js", ".mjs"}:
            raise PluginSettingsError("PLUGIN_FRONTEND_INVALID", "插件界面模块路径超出插件目录。")
        return {"pluginId": record.plugin_id, "source": module.read_text(encoding="utf-8")}

    def marketplace_install(self, payload: Mapping[str, Any]) -> dict[str, object]:
        plugin_id = _identifier(payload["pluginId"])
        version = payload["version"]
        if not isinstance(version, str) or not version:
            raise PluginSettingsError("INVALID_REQUEST", "市场安装请求格式无效。")
        return self.install(payload["revision"], "zip", payload["sourcePath"], expected=(plugin_id, version))

    def _update_plugin(self, application, installer, spec, transaction, record, existing):
        plugin_id, version = spec.plugin_id, spec.version
        warning = None
        started = perf_counter()
        paths = StoragePaths(self._user_root)
        code = paths.user_plugins_dir / record.directory_name
        dependencies = paths.plugin_dependency_root_for(plugin_id)
        try:
            if installer._unsafe_node(code):
                raise PluginInstallError("PLUGIN_INSTALL_SYMLINK_FORBIDDEN")
            with application.plugin_update(plugin_id) as dependents:
                desired = PluginDesiredStateStore(self._user_root)
                if plugin_id not in desired.read():
                    desired.set(plugin_id, existing["enabled"])
                log_event("Plugin", "正在切换插件版本", {"pluginId": plugin_id, "version": version}, event="plugin.update.switching")
                try:
                    application.uninstall_plugin(plugin_id)
                    state = update_transaction.begin(transaction, code, dependencies, plugin_id)
                    application.install_plugin(existing["installId"])
                    warning = update_transaction.commit(transaction, code, dependencies, state)
                except Exception as error:
                    recovery_error = None
                    try:
                        application.uninstall_plugin(plugin_id)
                        if (transaction / "transaction.json").exists():
                            update_transaction.rollback(transaction, code, dependencies)
                        application.refresh_inventory()
                        if existing["supported"]:
                            application.install_plugin(existing["installId"])
                    except Exception as recovery:
                        recovery_error = recovery
                        if (transaction / "transaction.json").exists():
                            application.record_update_recovery_failure(plugin_id, record.directory_name, recovery)
                    error_code = "PLUGIN_UPDATE_ROLLBACK_FAILED" if recovery_error else getattr(error, "code", "PLUGIN_UPDATE_FAILED")
                    message = "插件更新失败，原安装状态恢复失败。" if recovery_error else "插件更新失败，已恢复原安装状态。"
                    raise PluginSettingsError(error_code, message, recovery_error=recovery_error) from error
        except PluginSettingsError:
            raise
        except Exception as error:
            code = getattr(error, "code", "PLUGIN_UPDATE_FAILED")
            message = "当前互动尚未结束，请结束后重试更新。" if code == "RUNTIME_UPDATE_BUSY" else "插件更新失败。"
            raise PluginSettingsError(code, message, retryable=code == "RUNTIME_UPDATE_BUSY") from error
        result = self.snapshot()
        result.update(managementAction="updated", installId=existing["installId"], pluginId=plugin_id,
                      previousVersion=existing["version"], version=version)
        log_event("Plugin", "插件更新完成", {"pluginId": plugin_id, "version": version, "elapsedMs": round((perf_counter() - started) * 1000)}, event="plugin.update.completed")
        if warning:
            result["warning"] = {"message": "插件已更新，旧文件将在下次启动时清理。", "detail": warning}
        self._add_startup_warning(result, {plugin_id, *dependents})
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
            application = self._application()
            if application is None:
                raise PluginSettingsError(
                    "PLUGIN_SETTINGS_NOT_READY",
                    "插件设置仍在初始化。",
                    retryable=True,
                )
            installer = LocalPluginInstaller(self._roots)
            try:
                record = self._refresh_inventory(application).record(install_id)
                if record is not None and record.plugin_id is not None:
                    application.recover_plugin_update(record.plugin_id)
                pending = installer.begin_uninstall(install_id)
            except Exception as error:
                raise PluginSettingsError(getattr(error, "code", "PLUGIN_UNINSTALL_FAILED"), "本地插件卸载失败。") from error
            try:
                if pending.plugin_id is not None:
                    getattr(application, "uninstall_plugin")(pending.plugin_id)
            except Exception as apply_error:
                rollback_error: PluginInstallError | None = None
                try:
                    installer.rollback_uninstall(pending)
                except PluginInstallError as error:
                    rollback_error = error
                application.refresh_inventory()
                code = (
                    rollback_error.code
                    if rollback_error is not None
                    else str(getattr(apply_error, "code", "PLUGIN_UNINSTALL_APPLY_FAILED"))
                )
                raise PluginSettingsError(
                    code,
                    "插件卸载未能应用到当前运行时。",
                    recovery_error=rollback_error,
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
        application = self._application()
        if application is None:
            raise PluginSettingsError("PLUGIN_SETTINGS_NOT_READY", "插件设置仍在初始化。", retryable=True)
        plugin_id = _identifier(payload.get("pluginId"))
        section_id = _identifier(payload.get("sectionId"))
        collection_id = _identifier(payload.get("collectionId"))
        arguments = {
            key: value
            for key, value in payload.items()
            if key not in {"pluginId", "sectionId", "collectionId"}
        }
        try:
            result = getattr(application, "settings_collection")(
                operation,
                plugin_id,
                section_id,
                collection_id,
                arguments,
            )
        except Exception as error:
            code = str(getattr(error, "code", "SETTINGS_COLLECTION_FAILED"))
            raise PluginSettingsError(code, "插件 Collection 操作失败。") from error
        if not isinstance(result, Mapping):
            raise PluginSettingsError(
                "SETTINGS_COLLECTION_RESULT_INVALID",
                "插件 Collection 响应无效。",
            )
        return dict(result)

    def _application(self) -> object | None:
        if self._application_provider is not None:
            return self._application_provider()
        return None

    def _revision(self) -> str:
        return self._refresh_inventory(self._application()).revision

    def _refresh_inventory(self, application):
        return (
            application.refresh_inventory()
            if application is not None
            else PluginInventory(self._roots).scan()
        )

def _preview_plugin(spec: Any) -> dict[str, object]:
    supported = bool(spec.supported)
    source = spec.source
    required = bool(spec.required)
    enabled = bool(spec.desired_enabled)
    return {
        "installId": spec.install_id,
        "directoryName": spec.directory_name,
        "pluginId": spec.plugin_id,
        "name": spec.name[:120],
        "version": spec.version[:64],
        "author": spec.author[:120],
        "description": spec.description[:500],
        "presentation": {"kind": spec.presentation_kind, "category": spec.presentation_category, "icon": spec.presentation_icon},
        "enabled": enabled,
        "required": required,
        "source": source,
        "canUninstall": source == "user",
        "supported": supported,
        "provides": list(spec.provides),
        "requires": list(spec.requires),
        "missingServices": [],
        "state": (
            "starting" if supported and enabled else "disabled" if supported else "failed"
        ),
        "reasonCode": "PLUGIN_APPLICATION_NOT_READY" if supported and enabled else spec.reason_code,
        **({"diagnostics": dict(spec.diagnostics)} if getattr(spec, "diagnostics", None) else {}),
        "sections": [],
    }


def _project_plugins(
    values: list[Mapping[str, Any]],
    inventory: Any,
) -> list[dict[str, object]]:
    if values and all(isinstance(item.get("installId"), str) for item in values):
        return [_project_plugin(item, record=inventory.record(item["installId"])) for item in values]
    by_plugin_id = {
        item.get("pluginId"): item
        for item in values
        if isinstance(item.get("pluginId"), str)
    }
    return [
        _project_plugin(by_plugin_id.get(record.plugin_id, {}), record=record)
        for record in inventory.records
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
    model_update_required = raw.get("reasonCode") != "PLUGIN_ID_CONFLICT" and (
        "sakura.host.model_slots" in _identifier_list(raw.get("requires"))
        or raw.get("reasonCode") == "MODEL_API_UPDATE_REQUIRED"
    )
    return {
        "installId": _install_identifier(raw.get("installId")),
        "directoryName": raw.get("directoryName", ""),
        "pluginId": plugin_id,
        "name": _text(raw.get("name"), 120, "Plugin"),
        "version": _text(raw.get("version"), 64, "0.0.0"),
        "author": _text(raw.get("author"), 120, ""),
        "description": _text(raw.get("description"), 500, ""),
        "presentation": raw.get("presentation", {"kind": "extension", "category": "other", "icon": ""}),
        "enabled": bool(raw.get("enabled")),
        "required": bool(raw.get("required")) and source != "user",
        "source": source,
        "canUninstall": source == "user",
        "supported": bool(raw.get("supported")) and not model_update_required,
        "provides": _identifier_list(raw.get("provides")),
        "requires": _identifier_list(raw.get("requires")),
        "missingServices": _identifier_list(raw.get("missingServices")),
        "state": (
            ("failed" if raw.get("enabled") else "disabled") if model_update_required
            else "starting" if raw.get("enabled") and raw.get("reasonCode") in {"NOT_STARTED", "PLUGIN_STARTING"}
            else raw.get("state") if raw.get("state") in {"disabled", "starting", "active", "failed"}
            else "failed"
        ),
        "reasonCode": "MODEL_API_UPDATE_REQUIRED" if model_update_required else _reason_code(raw.get("reasonCode"), "STATUS_INVALID"),
        **({"diagnostics": dict(raw["diagnostics"])} if isinstance(raw.get("diagnostics"), Mapping) else {}),
        "pages": raw.get("pages", []),
        "sections": raw.get("sections", []) if isinstance(raw.get("sections"), list) else [],
    }


def _object(value: object) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise PluginSettingsError("INVALID_REQUEST", "插件设置必须是 object。")
    return value


def _boolean_mapping(value: object) -> dict[str, bool]:
    raw = _object(value)
    result: dict[str, bool] = {}
    for key, item in raw.items():
        if not isinstance(key, str) or not isinstance(item, bool):
            raise PluginSettingsError("INVALID_REQUEST", "插件启停字段无效。")
        result[_identifier(key)] = item
    return result


def _settings_mapping(value: object) -> dict[str, dict[str, dict[str, Any]]]:
    raw = _object(value)
    result: dict[str, dict[str, dict[str, Any]]] = {}
    for plugin_id, sections in raw.items():
        section_map = _object(sections)
        result[_identifier(plugin_id)] = {
            _identifier(section_id): dict(_object(values))
            for section_id, values in section_map.items()
        }
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
        or not INSTALL_ID_PATTERN.fullmatch(value)
    ):
        raise PluginSettingsError("INVALID_REQUEST", "插件安装标识无效。")
    return value


def _identifier_list(value: object) -> list[str]:
    if not isinstance(value, list):
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
