"""插件代码和依赖成对切换，启动前恢复未完成的更新。"""
from __future__ import annotations

import json
import os
import re
import shutil
from pathlib import Path

from app.core.runtime_log import diagnostic_attributes, log_event
from app.storage.atomic import atomic_write_text
from app.storage.paths import StoragePaths
from app.storage.runtime_roots import RuntimeRoots


def _clear_transaction(transaction: Path) -> None:
    # 清理完成前保留记录，下次启动可以继续收尾。
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


def recover(transaction: Path, code: Path, dependencies: Path, state: dict) -> str | None:
    """发现和加载插件前恢复未完成的切换。"""
    if not state["committed"]:
        for name, target in (("code", code), ("dependencies", dependencies)):
            saved = transaction / ("old-" + name)
            if saved.exists():
                if target.exists():
                    shutil.rmtree(target)
                os.replace(saved, target)
            elif not state["existed"][name] and target.exists():
                shutil.rmtree(target)
        # 恢复完成后只允许继续清理，避免下次启动撤销后来的更新。
        state = {**state, "committed": True}
        atomic_write_text(transaction / "transaction.json", json.dumps(state))
    try:
        _clear_transaction(transaction)
    except OSError as error:
        log_event("Plugin", "插件更新备份清理失败", diagnostic_attributes(
            error, reason_code="PLUGIN_UPDATE_CLEANUP_FAILED", stage="update_cleanup"),
            event="plugin.update.cleanup_failed", severity="warning")
        return str(error)
    return None


def update_journals(roots: RuntimeRoots, plugin_id: str | None = None) -> list[Path]:
    journals = sorted(StoragePaths(roots.user_root).user_plugins_dir.glob(".update-*/transaction.json"))
    return [journal for journal in journals if plugin_id is None or _plugin_id(journal) == plugin_id]


def _plugin_id(journal: Path) -> str:
    return re.sub(r"--[0-9a-f]{32}$", "", journal.parent.name.removeprefix(".update-"))


def recover_installed_plugins(
    roots: RuntimeRoots, plugin_id: str | None = None, *, replace_invalid: bool = False,
) -> dict[str, dict]:
    """恢复插件市场更新留下的事务，保留已提交的版本。"""
    from app.plugins.inventory import PLUGIN_ID_PATTERN

    paths = StoragePaths(roots.user_root)
    failures = {}
    for journal in update_journals(roots, plugin_id):
        transaction = journal.parent
        recovering_id = _plugin_id(journal)
        directory_name = recovering_id
        try:
            try:
                state = json.loads(journal.read_text(encoding="utf-8"))
                directory = state["directory"]
                if (not PLUGIN_ID_PATTERN.fullmatch(recovering_id) or state["pluginId"] != recovering_id
                        or transaction.is_symlink() or journal.is_symlink()
                        or not isinstance(directory, str) or not directory or directory in {".", ".."}
                        or "/" in directory or "\\" in directory or ":" in directory
                        or directory.startswith(".update-")
                        or not isinstance(state["committed"], bool)
                        or not isinstance(state["existed"], dict)
                        or any(not isinstance(state["existed"].get(name), bool) for name in ("code", "dependencies"))):
                    raise ValueError("PLUGIN_UPDATE_STATE_INVALID")
            except (ValueError, TypeError, KeyError) as error:
                if not replace_invalid:
                    raise
                # 新包已验证，保留坏记录和旧文件后重新安装，避免反复重放坏事务。
                saved = transaction.with_name(".failed-" + transaction.name.removeprefix("."))
                os.replace(transaction, saved)
                log_event("Plugin", "已保留损坏的更新记录，继续重新安装", {
                    "pluginId": recovering_id, "backup": str(saved),
                    **diagnostic_attributes(error, reason_code="PLUGIN_UPDATE_STATE_INVALID", stage="update_recovery"),
                }, event="plugin.update.recovery_skipped", severity="warning")
                continue
            directory_name = directory
            code = paths.user_plugins_dir / directory
            dependencies = paths.plugin_dependency_root_for(recovering_id)
            if code.is_symlink() or dependencies.is_symlink():
                raise ValueError("PLUGIN_UPDATE_STATE_INVALID")
            recover(transaction, code, dependencies, state)
        except Exception as error:
            log_event("Plugin", "插件更新恢复失败", diagnostic_attributes(
                error, reason_code="PLUGIN_UPDATE_RECOVERY_FAILED", stage="update_recovery"),
                event="plugin.update.failed", severity="error")
            if not PLUGIN_ID_PATTERN.fullmatch(recovering_id):
                continue
            failures[recovering_id] = {
                "directoryName": directory_name,
                "reasonCode": "PLUGIN_UPDATE_RECOVERY_FAILED",
                "diagnostics": diagnostic_attributes(error, reason_code="PLUGIN_UPDATE_RECOVERY_FAILED",
                                                       stage="update_recovery"),
            }
    return failures


def begin(transaction: Path, code: Path, dependencies: Path, plugin_id: str) -> dict:
    state = {"pluginId": plugin_id, "directory": code.name, "committed": False,
             "existed": {"code": code.exists(), "dependencies": dependencies.exists()}}
    atomic_write_text(transaction / "transaction.json", json.dumps(state))
    for name, target in (("dependencies", dependencies), ("code", code)):
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            os.replace(target, transaction / ("old-" + name))
        prepared = transaction / ("new-" + name)
        if prepared.exists():
            os.replace(prepared, target)
    return state


def commit(transaction: Path, code: Path, dependencies: Path, state: dict) -> str | None:
    state = {**state, "committed": True}
    atomic_write_text(transaction / "transaction.json", json.dumps(state))
    return recover(transaction, code, dependencies, state)


def rollback(transaction: Path, code: Path, dependencies: Path) -> None:
    state = json.loads((transaction / "transaction.json").read_text(encoding="utf-8"))
    recover(transaction, code, dependencies, state)
