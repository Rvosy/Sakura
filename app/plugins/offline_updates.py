"""用当前发行包覆盖已安装的官方插件，保留用户配置与启停状态。"""
from __future__ import annotations

import json
import os
import re
import shutil
import uuid
from pathlib import Path

import yaml

from app.core.runtime_log import diagnostic_attributes, log_event
from app.plugins.bundled_migrations import PAYLOAD_PATH, SOURCES
from app.storage.atomic import atomic_write_text
from app.storage.paths import StoragePaths
from app.storage.runtime_roots import RuntimeRoots


def _clear_transaction(transaction: Path) -> None:
    # Keep the journal until all backup/staging contents are gone. A crash in
    # cleanup must leave enough state to resume it on the next launch.
    journal = transaction / "transaction.json"
    for path in transaction.iterdir():
        if path == journal:
            continue
        if path.is_dir() and not path.is_symlink():
            shutil.rmtree(path)
        else:
            path.unlink()
    journal.unlink()
    transaction.rmdir()


def _recover(transaction: Path, code: Path, dependencies: Path, state: dict) -> None:
    """Roll back interrupted publication before inventory or plugin imports."""
    if not state["committed"]:
        for name, target in (("code", code), ("dependencies", dependencies)):
            saved = transaction / ("old-" + name)
            if saved.exists():
                if target.exists():
                    shutil.rmtree(target)
                os.replace(saved, target)
            elif not state["existed"][name] and target.exists():
                shutil.rmtree(target)
        # Rollback is also a completed publication. A leftover cleanup journal
        # must never roll back a later successful update on the next startup.
        state = {**state, "committed": True}
        atomic_write_text(transaction / "transaction.json", json.dumps(state))
    try:
        _clear_transaction(transaction)
    except OSError as error:
        log_event("Plugin", "插件更新备份清理失败", diagnostic_attributes(
            error, reason_code="PLUGIN_UPDATE_CLEANUP_FAILED", stage="offline_update_cleanup"),
            event="plugin.update.cleanup_failed", severity="warning")


def recover_installed_plugins(roots: RuntimeRoots) -> dict[str, dict]:
    """Restore pending updates independently of the current distribution payload."""
    from app.plugins.inventory import PLUGIN_ID_PATTERN

    paths = StoragePaths(roots.user_root)
    failures = {}
    for journal in sorted(paths.user_plugins_dir.glob(".update-*/transaction.json")):
        transaction = journal.parent
        plugin_id = re.sub(r"--[0-9a-f]{32}$", "", transaction.name.removeprefix(".update-"))
        try:
            state = json.loads(journal.read_text(encoding="utf-8"))
            directory = state["directory"]
            if (not PLUGIN_ID_PATTERN.fullmatch(plugin_id) or state["pluginId"] != plugin_id
                    or transaction.is_symlink() or journal.is_symlink()
                    or not isinstance(directory, str) or not directory or directory in {".", ".."}
                    or "/" in directory or "\\" in directory or ":" in directory
                    or directory.startswith(".update-")
                    or not isinstance(state["committed"], bool)
                    or set(state["existed"]) != {"code", "dependencies"}
                    or any(not isinstance(value, bool) for value in state["existed"].values())):
                raise ValueError("PLUGIN_UPDATE_STATE_INVALID")
            code = paths.user_plugins_dir / directory
            dependencies = paths.plugin_dependency_root_for(plugin_id)
            if code.is_symlink() or dependencies.is_symlink():
                raise ValueError("PLUGIN_UPDATE_STATE_INVALID")
            _recover(transaction, code, dependencies, state)
        except Exception as error:
            log_event("Plugin", "插件离线更新恢复失败", diagnostic_attributes(
                error, reason_code="PLUGIN_UPDATE_RECOVERY_FAILED", stage="offline_update_recovery"),
                event="plugin.update.failed", severity="error")
            failures[plugin_id] = {
                "reasonCode": "PLUGIN_UPDATE_RECOVERY_FAILED",
                "diagnostics": diagnostic_attributes(error, reason_code="PLUGIN_UPDATE_RECOVERY_FAILED",
                                                       stage="offline_update_recovery"),
            }
    return failures


def _publish(transaction: Path, code: Path, dependencies: Path, plugin_id: str) -> None:
    state = {"pluginId": plugin_id, "directory": code.name, "committed": False,
             "existed": {"code": code.exists(), "dependencies": dependencies.exists()}}
    journal = transaction / "transaction.json"
    atomic_write_text(journal, json.dumps(state))
    try:
        for name, target in (("dependencies", dependencies), ("code", code)):
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists():
                os.replace(target, transaction / ("old-" + name))
            prepared = transaction / ("new-" + name)
            if prepared.exists():
                os.replace(prepared, target)
        state["committed"] = True
        atomic_write_text(journal, json.dumps(state))
    except Exception:
        try:
            _recover(transaction, code, dependencies, {**state, "committed": False})
        except Exception as error:
            error.plugin_update_recovery_failed = True
            raise
        raise
    _recover(transaction, code, dependencies, state)


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
            records = [record for record in installed_records
                       if record.plugin_id == plugin_id and record.source == "user"]
            if not records:
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
            _publish(transaction, target, dependency_target, plugin_id)
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
