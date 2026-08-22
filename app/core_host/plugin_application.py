"""Generation-scoped owner for the Plugin API v3 application runtime."""

from __future__ import annotations

import secrets
import threading
from pathlib import Path
from typing import Any, Mapping

from app.plugins.inventory import (
    InstalledPluginRecord,
    PluginDesiredStateStore,
    PluginInventory,
    PluginInventorySnapshot,
)

from .plugin_worker import PluginWorkerClient, PluginWorkerError


class PluginApplicationHost:
    """Own the Worker independently from any Assistant Session."""

    def __init__(
        self,
        app_root: Path,
        generation_id: str,
        tool_registry: object,
        *,
        call_timeout: float | None = None,
    ) -> None:
        self._app_root = Path(app_root).resolve()
        self._generation_id = generation_id
        self._tool_registry = tool_registry
        self._desired = PluginDesiredStateStore(self._app_root)
        self._inventory = PluginInventory(self._app_root, self._desired)
        self._worker = (
            PluginWorkerClient(self._app_root, generation_id)
            if call_timeout is None
            else PluginWorkerClient(
                self._app_root,
                generation_id,
                call_timeout=call_timeout,
            )
        )
        self._worker.configure_host_services(tool_registry, None)
        self._lock = threading.RLock()
        self._session: object | None = None
        self._session_id = ""
        self._closed = False

    @property
    def worker(self) -> PluginWorkerClient:
        return self._worker

    def start(self) -> None:
        with self._lock:
            if self._closed:
                raise PluginWorkerError("GENERATION_INVALIDATED", "插件 generation 已失效。")
        self._worker.start()

    def bind_session(self, session: object) -> None:
        runtime = getattr(session, "runtime", None)
        character = getattr(session, "character", None)
        character_id = getattr(character, "id", None)
        if runtime is None or not isinstance(character_id, str) or not character_id:
            raise PluginWorkerError("PLUGIN_SESSION_INVALID", "插件 Session 无效。")
        with self._lock:
            if self._closed:
                raise PluginWorkerError("GENERATION_INVALIDATED", "插件 generation 已失效。")
            previous = self._session
            if previous is session:
                return
            self._session = session
            self._session_id = f"session_{secrets.token_hex(12)}"
            session_id = self._session_id
        if previous is not None:
            self._worker.unbind_session()
        self._worker.bind_session(
            session_id,
            character_id,
            self._tool_registry,
            runtime,
        )

    def unbind_session(self) -> None:
        with self._lock:
            if self._session is None:
                return
            self._session = None
            self._session_id = ""
        self._worker.unbind_session()

    def inventory(self) -> PluginInventorySnapshot:
        return self._inventory.scan()

    def public_snapshot(self) -> dict[str, Any]:
        return self._merge_inventory(self._worker.public_snapshot(), decorate=False)

    def settings_snapshot(self) -> dict[str, Any]:
        return self._merge_inventory(self._worker.settings_snapshot(), decorate=True)

    def set_enabled(self, install_id: str, enabled: bool) -> dict[str, Any]:
        inventory = self.inventory()
        record = inventory.record(install_id)
        if record is None:
            raise PluginWorkerError("PLUGIN_NOT_FOUND", "插件不存在。")
        if record.required and not enabled:
            raise PluginWorkerError("REQUIRED_PLUGIN_LOCKED", "必需插件不能禁用。")
        if record.plugin_id is None:
            if enabled:
                raise PluginWorkerError("PLUGIN_MANIFEST_INVALID", "损坏插件不能启用。")
            return self._management_result(
                self.settings_snapshot(),
                record,
                "applied",
                "READY",
            )
        self._desired.set(record.plugin_id, enabled)
        if not record.runtime_eligible:
            return self._management_result(
                self.settings_snapshot(),
                record,
                "degraded" if enabled else "applied",
                "DESIRED_SAVED_RUNTIME_DEGRADED" if enabled else "READY",
            )
        try:
            runtime = self._worker.set_plugin_enabled(record.plugin_id, enabled)
            recovered = self._worker.last_lifecycle_recovered
        except PluginWorkerError:
            snapshot = self.settings_snapshot()
            return self._management_result(
                snapshot,
                record,
                "degraded",
                "DESIRED_SAVED_RUNTIME_DEGRADED",
            )
        target = next(
            (
                item
                for item in runtime.get("plugins", [])
                if isinstance(item, Mapping) and item.get("pluginId") == record.plugin_id
            ),
            None,
        )
        converged = isinstance(target, Mapping) and target.get("enabled") is enabled
        state = "recovered" if recovered else "applied"
        reason = "DESIRED_SAVED_RUNTIME_RECOVERED" if recovered else "READY"
        if not converged:
            state = "degraded"
            reason = "DESIRED_SAVED_RUNTIME_DEGRADED"
        return self._management_result(
            self.settings_snapshot(),
            record,
            state,
            reason,
        )

    @property
    def last_lifecycle_recovered(self) -> bool:
        return self._worker.last_lifecycle_recovered

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
        self.unbind_session()
        self._worker.close()

    def __getattr__(self, name: str) -> Any:
        # Domain boundaries consume exported plugin Services through this
        # application owner; they never acquire Worker ownership.
        return getattr(self._worker, name)

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
            "schemaVersion": 2 if decorate else 1,
            "revision": inventory.revision,
            "state": runtime_snapshot.get("state", "ready"),
            "reasonCode": runtime_snapshot.get("reasonCode", "READY"),
            "plugins": plugins,
        }

    @staticmethod
    def _public_record(
        record: InstalledPluginRecord,
        runtime: Mapping[str, Any] | None,
    ) -> dict[str, Any]:
        runnable = runtime is not None
        state = runtime.get("state", "failed") if runnable else "failed"
        reason = runtime.get("reasonCode", record.reason_code) if runnable else record.reason_code
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
            "state": state,
            "reasonCode": reason,
            "provides": list(runtime.get("provides", record.provides)) if runnable else list(record.provides),
            "requires": list(runtime.get("requires", record.requires)) if runnable else list(record.requires),
            "optional": list(runtime.get("optional", record.optional)) if runnable else list(record.optional),
            "missingServices": list(runtime.get("missingServices", [])) if runnable else [],
            "conflicts": list(runtime.get("conflicts", [])) if runnable else [],
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
