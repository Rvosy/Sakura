"""Generation-scoped owner for the production Plugin Runtime v4 application."""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any, Mapping

from app.plugins.inventory import (
    InstalledPluginRecord,
    PluginDesiredStateStore,
    PluginInventory,
    PluginInventorySnapshot,
)
from app.plugins.models import PLUGIN_API_V4_VERSION
from app.plugins.runtime_v4 import PluginRuntimeError
from app.storage.runtime_roots import RuntimeRoots, coerce_runtime_roots

from .plugin_runtime_application import PluginRuntimeApplication


class PluginApplicationHost:
    """Own the plugin application independently from any Assistant Session."""

    def __init__(
        self,
        roots: RuntimeRoots | Path,
        generation_id: str,
        tool_registry: object,
        *,
        call_timeout: float | None = None,
    ) -> None:
        self._roots = coerce_runtime_roots(roots)
        self._user_root = self._roots.user_root
        self._generation_id = generation_id
        self._tool_registry = tool_registry
        self._desired = PluginDesiredStateStore(self._user_root)
        self._inventory = PluginInventory(self._roots, self._desired)
        inventory = self._inventory.scan()
        self._application: Any = PluginRuntimeApplication(
            self._roots,
            generation_id,
            tool_registry,
            inventory.runtime_specs,
            call_timeout=call_timeout,
        )
        self._lock = threading.RLock()
        self._session: object | None = None
        self._closed = False

    @property
    def application(self) -> Any:
        return self._application

    def start(self) -> None:
        with self._lock:
            if self._closed:
                raise PluginRuntimeError("GENERATION_INVALIDATED", "插件 generation 已失效。")
        self._application.start()

    def bind_session(self, session: object) -> None:
        runtime = getattr(session, "runtime", None)
        character = getattr(session, "character", None)
        character_id = getattr(character, "id", None)
        if runtime is None or not isinstance(character_id, str) or not character_id:
            raise PluginRuntimeError("PLUGIN_SESSION_INVALID", "插件 Session 无效。")
        with self._lock:
            if self._closed:
                raise PluginRuntimeError("GENERATION_INVALIDATED", "插件 generation 已失效。")
            previous = self._session
            if previous is session:
                return
            self._session = session
        if previous is not None:
            self._application.unbind_session()
        self._application.bind_runtime(
            self._tool_registry,
            runtime,
            session=session,
        )

    def unbind_session(self) -> None:
        with self._lock:
            if self._session is None:
                return
            self._session = None
        self._application.unbind_session()

    def bind_chat_boundary(self, boundary: object) -> None:
        callback = getattr(self._application, "bind_chat_boundary", None)
        if callable(callback):
            callback(boundary)

    def inventory(self) -> PluginInventorySnapshot:
        return self._inventory.scan()

    def public_snapshot(self) -> dict[str, Any]:
        return self._merge_inventory(self._application.public_snapshot(), decorate=False)

    def settings_snapshot(self) -> dict[str, Any]:
        return self._merge_inventory(self._application.settings_snapshot(), decorate=True)

    def set_enabled(self, install_id: str, enabled: bool) -> dict[str, Any]:
        inventory = self.inventory()
        record = inventory.record(install_id)
        if record is None:
            raise PluginRuntimeError("PLUGIN_NOT_FOUND", "插件不存在。")
        if record.required and not enabled:
            raise PluginRuntimeError("REQUIRED_PLUGIN_LOCKED", "必需插件不能禁用。")
        if record.plugin_id is None:
            if enabled:
                raise PluginRuntimeError("PLUGIN_MANIFEST_INVALID", "损坏插件不能启用。")
            return self._management_result(
                self.settings_snapshot(),
                record,
                "applied",
                "READY",
            )
        self._desired.set(record.plugin_id, enabled)
        application_state = "applied"
        application_reason = "READY"
        runtime_snapshot = self._application.set_plugin_enabled(record.plugin_id, enabled)
        runtime_record = next(
            (
                item
                for item in runtime_snapshot.get("plugins", [])
                if isinstance(item, Mapping) and item.get("pluginId") == record.plugin_id
            ),
            None,
        )
        expected_state = "active" if enabled else "disabled"
        if runtime_record is None or runtime_record.get("state") != expected_state:
            application_state = "error"
            application_reason = (
                str(runtime_record.get("reasonCode", "PLUGIN_LIFECYCLE_FAILED"))
                if runtime_record is not None
                else "PLUGIN_LIFECYCLE_FAILED"
            )
        return self._management_result(
            self.settings_snapshot(),
            record,
            application_state,
            application_reason,
        )

    def install_plugin(self, install_id: str) -> dict[str, Any]:
        record = self.inventory().record(install_id)
        if record is None:
            raise PluginRuntimeError("PLUGIN_NOT_FOUND", "插件不存在。")
        spec = record.runtime_spec()
        if spec is None or spec.api_version != PLUGIN_API_V4_VERSION:
            raise PluginRuntimeError("API_VERSION_UNSUPPORTED", "插件 API 版本不受支持。")
        return self._application.install_plugin(spec)

    def uninstall_plugin(self, plugin_id: str) -> dict[str, Any]:
        return self._application.uninstall_plugin(plugin_id)

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
        self.unbind_session()
        self._application.close()

    def __getattr__(self, name: str) -> Any:
        # Domain boundaries consume exported plugin Services through this
        # application owner; they never acquire Worker ownership.
        return getattr(self._application, name)

    def _merge_inventory(
        self,
        runtime_snapshot: Mapping[str, Any],
        *,
        decorate: bool,
    ) -> dict[str, Any]:
        inventory = self.inventory()
        runtime_records = {
            item.get("pluginId"): item
            for item in runtime_snapshot.get("plugins", [])
            if isinstance(item, Mapping) and isinstance(item.get("pluginId"), str)
        }
        plugins = [
            self._public_record(record, runtime_records.get(record.plugin_id))
            for record in inventory.records
        ]
        return {
            "schemaVersion": 1,
            "revision": inventory.revision,
            "state": runtime_snapshot.get("state", "ready"),
            "reasonCode": runtime_snapshot.get("reasonCode", "READY"),
            "plugins": plugins,
        }

    def _public_record(
        self,
        record: InstalledPluginRecord,
        runtime: Mapping[str, Any] | None,
    ) -> dict[str, Any]:
        runnable = runtime is not None and record.supported
        state = runtime.get("state", "failed") if runnable else "failed"
        reason = (
            runtime.get("reasonCode", record.reason_code)
            if runnable
            else record.reason_code
        )
        return {
            "installId": record.install_id,
            "pluginId": record.plugin_id,
            "name": record.name,
            "version": record.version,
            "author": record.author,
            "description": record.description,
            "enabled": bool(runtime.get("enabled")) if runnable else record.desired_enabled,
            "required": record.required,
            "source": record.source,
            "canUninstall": record.can_uninstall,
            "supported": record.supported,
            "provides": list(record.provides),
            "requires": list(record.requires),
            "missingServices": list(runtime.get("missingServices", []))[:64] if runnable else [],
            "state": state,
            "reasonCode": reason,
            "sections": list(runtime.get("sections", []))[:16] if runnable else [],
        }

    @staticmethod
    def _management_result(
        snapshot: Mapping[str, Any],
        record: InstalledPluginRecord,
        application_state: str,
        reason_code: str,
    ) -> dict[str, Any]:
        result = dict(snapshot)
        result.update(
            managementAction="enabled_changed",
            installId=record.install_id,
            pluginId=record.plugin_id,
            desiredSaved=True,
            applicationState=application_state,
            applicationReasonCode=reason_code,
        )
        return result


__all__ = ["PluginApplicationHost"]
