"""用当前发行包覆盖已安装的官方插件，保留用户配置与启停状态。"""
from __future__ import annotations

import shutil
import uuid
from pathlib import Path

import yaml

from app.core.runtime_log import diagnostic_attributes, log_event
from app.plugins import update_transaction
from app.plugins.update_transaction import recover_installed_plugins
from app.plugins.bundled_migrations import PAYLOAD_PATH, SOURCES
from app.storage.atomic import atomic_write_text
from app.storage.paths import StoragePaths
from app.storage.runtime_roots import RuntimeRoots


def _publish(transaction: Path, code: Path, dependencies: Path, plugin_id: str, revision: str, receipt: Path) -> None:
    try:
        state = update_transaction.begin(transaction, code, dependencies, plugin_id, revision)
    except Exception as error:
        try:
            update_transaction.rollback(transaction, code, dependencies)
        except Exception:
            error.plugin_update_recovery_failed = True
        raise
    update_transaction.commit(transaction, code, dependencies, state, receipt)


def update_installed_plugins(roots: RuntimeRoots, *, progress=None, recovery_failures=None) -> dict[str, dict]:
    from app.plugins.dependencies import PluginDependencyRoots
    from app.plugins.inventory import PluginDesiredStateStore, PluginInventory

    failures = dict(recover_installed_plugins(roots) if recovery_failures is None else recovery_failures)
    paths = StoragePaths(roots.user_root)
    payload = roots.distribution_root / PAYLOAD_PATH
    if not payload.is_dir():
        return failures
    installed_records = PluginInventory(roots).scan().records
    for plugin_id, source in SOURCES.items():
        if plugin_id in failures:
            continue
        dependency_target = paths.plugin_dependency_root_for(plugin_id)
        details = {"stage": "offline_update", "pluginId": plugin_id}
        transaction = None
        try:
            revision = (payload / "revision.txt").read_text(encoding="utf-8")
            receipt = paths.config_dir / "plugin-updates" / plugin_id
            if receipt.is_file() and receipt.read_text(encoding="utf-8") == revision:
                continue
            records = [record for record in installed_records
                       if record.plugin_id == plugin_id and record.source == "user"]
            if not records:
                # Later marketplace installs belong to the user; this bundle
                # has already finished processing the absent plugin.
                atomic_write_text(receipt, revision)
                continue
            if len(records) != 1 or records[0].reason_code == "PLUGIN_ID_CONFLICT":
                raise ValueError("PLUGIN_ID_CONFLICT")
            installed = records[0]
            target = paths.user_plugins_dir / installed.directory_name
            if target.is_symlink() or dependency_target.is_symlink():
                raise ValueError("PLUGIN_UPDATE_UNSAFE_PATH")
            replacement = payload / "plugins" / source["directory"]
            raw = yaml.safe_load((replacement / "plugin.yaml").read_text(encoding="utf-8"))
            details.update(fromVersion=installed.version, toVersion=raw["version"])
            if progress is not None:
                progress({"state": "running", "completed": 0, "total": 1, "pluginId": plugin_id})
            # Each publication owns its backups; locked committed backups do not
            # prevent a later update from publishing a fresh pair of directories.
            transaction = paths.user_plugins_dir / (".update-" + plugin_id + "--" + uuid.uuid4().hex)
            transaction.mkdir(parents=True)
            shutil.copytree(replacement, transaction / "new-code",
                            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
            dependency_source = payload / "dependencies" / plugin_id
            if PluginDependencyRoots(roots.user_root).declaration(replacement) is not None:
                shutil.copytree(dependency_source, transaction / "new-dependencies")
            if raw.get("enabled", True) != installed.desired_enabled:
                PluginDesiredStateStore(roots.user_root).set(plugin_id, installed.desired_enabled)
            _publish(transaction, target, dependency_target, plugin_id, revision, receipt)
            log_event("Plugin", "已离线更新插件", details, event="plugin.update.completed", plugin_id=plugin_id)
            if progress is not None:
                progress({"state": "completed", "completed": 1, "total": 1, "pluginId": plugin_id})
        except Exception as error:
            diagnostics = diagnostic_attributes(error, reason_code="PLUGIN_UPDATE_FAILED", stage=details["stage"])
            log_event("Plugin", "插件离线更新未完成", {**details, **diagnostics},
                event="plugin.update.failed", severity="error", plugin_id=plugin_id)
            failures[plugin_id] = {"reasonCode": "PLUGIN_UPDATE_FAILED", "diagnostics": diagnostics}
            if getattr(error, "plugin_update_recovery_failed", False):
                failures[plugin_id]["reasonCode"] = "PLUGIN_UPDATE_RECOVERY_FAILED"
            elif transaction is not None and transaction.exists() and not (transaction / "transaction.json").exists():
                try:
                    shutil.rmtree(transaction)
                except OSError as cleanup_error:
                    log_event("Plugin", "插件更新暂存目录清理失败", diagnostic_attributes(
                        cleanup_error, reason_code="PLUGIN_UPDATE_CLEANUP_FAILED", stage="offline_update_cleanup"),
                        event="plugin.update.cleanup_failed", severity="warning")
            if progress is not None:
                progress({"state": "failed", "completed": 0, "total": 1, "pluginId": plugin_id})
    return failures
