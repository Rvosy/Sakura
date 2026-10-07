"""Upgrade existing, unmodified official plugins from distribution payloads."""
from __future__ import annotations

import json
import os
import shutil
import zipfile
from pathlib import Path

import yaml

from app.core.runtime_log import diagnostic_attributes, log_event
from app.plugins.app_compatibility import semver_precedence
from app.plugins.bundled_migrations import PAYLOAD_PATH, SOURCES
from app.storage.atomic import atomic_write_text
from app.storage.paths import StoragePaths
from app.storage.runtime_roots import RuntimeRoots

BASELINES = Path("migration_payload/plugin-updates")
IGNORED = {"__pycache__", ".DS_Store", ".pytest_cache", ".mypy_cache", ".ruff_cache"}


def _files(root: Path) -> dict[str, bytes]:
    result = {}
    for path in root.rglob("*"):
        relative = path.relative_to(root)
        if any(part in IGNORED for part in relative.parts) or path.suffix in {".pyc", ".pyo"}:
            continue
        if path.is_symlink():
            raise ValueError(f"PLUGIN_UPDATE_CUSTOM_SOURCE: {path}")
        if path.is_file():
            result[relative.as_posix()] = path.read_bytes()
    return result


def _matches_baseline(root: Path, baseline_root: Path, baselines: list[dict]) -> bool:
    if root.is_symlink() or (root / ".git").exists():
        return False
    files = _files(root)
    for baseline in baselines:
        with zipfile.ZipFile(baseline_root / baseline["file"]) as archive:
            if files == {name: archive.read(name) for name in archive.namelist()}:
                return True
    return False


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
    _clear_transaction(transaction)


def recover_installed_plugins(roots: RuntimeRoots) -> None:
    """Restore pending updates independently of the current distribution payload."""
    from app.plugins.inventory import PLUGIN_ID_PATTERN

    paths = StoragePaths(roots.user_root)
    for journal in sorted(paths.user_plugins_dir.glob(".update-*/transaction.json")):
        transaction = journal.parent
        try:
            state = json.loads(journal.read_text(encoding="utf-8"))
            plugin_id, directory = state["pluginId"], state["directory"]
            if (not isinstance(plugin_id, str) or not PLUGIN_ID_PATTERN.fullmatch(plugin_id)
                    or transaction.name != ".update-" + plugin_id
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
            # Discovering or migrating a half-published installation could replace
            # its recoverable original files. Leave them untouched for recovery.
            raise RuntimeError("PLUGIN_UPDATE_RECOVERY_FAILED") from error


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
        _recover(transaction, code, dependencies, {**state, "committed": False})
        raise
    _recover(transaction, code, dependencies, state)


def update_installed_plugins(roots: RuntimeRoots, *, progress=None) -> dict[str, dict]:
    from app.plugins.dependencies import PluginDependencyRoots
    from app.plugins.inventory import PluginInventory
    from app.plugins.installer import LocalPluginInstaller

    recover_installed_plugins(roots)
    baseline_root = roots.distribution_root / BASELINES
    try:
        sources = json.loads((baseline_root / "sources.json").read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as error:
        diagnostics = diagnostic_attributes(error, reason_code="PLUGIN_UPDATE_PAYLOAD_INVALID", stage="offline_update")
        log_event("Plugin", "无法读取插件离线更新清单", diagnostics,
                  event="plugin.update.failed", severity="error")
        return {}
    paths = StoragePaths(roots.user_root)
    payload = roots.distribution_root / PAYLOAD_PATH
    failures = {}
    installed_records = PluginInventory(roots).scan().records
    for plugin_id, source in sources.items():
        transaction = paths.user_plugins_dir / (".update-" + plugin_id)
        dependency_target = paths.plugin_dependency_root_for(plugin_id)
        details = {"stage": "offline_update", "pluginId": plugin_id}
        try:
            records = [record for record in installed_records
                       if record.plugin_id == plugin_id and record.source == "user"]
            if not records:
                continue
            if len(records) != 1 or records[0].reason_code == "PLUGIN_ID_CONFLICT":
                raise ValueError("PLUGIN_ID_CONFLICT")
            installed = records[0]
            target = paths.user_plugins_dir / installed.directory_name
            replacement = payload / "plugins" / source["directory"]
            raw = yaml.safe_load((replacement / "plugin.yaml").read_text(encoding="utf-8"))
            if semver_precedence(installed.version) >= semver_precedence(raw["version"]):
                continue
            details.update(fromVersion=installed.version, toVersion=raw["version"])
            if not _matches_baseline(target, baseline_root, source["baselines"]):
                raise ValueError("PLUGIN_UPDATE_CUSTOM_SOURCE: 插件与已发布源码不同，请手动更新。")
            if progress is not None:
                progress({"state": "running", "completed": 0, "total": 1, "pluginId": plugin_id})
            installer = LocalPluginInstaller(roots)
            spec = installer._validated_spec(replacement)
            if spec.plugin_id != plugin_id:
                raise ValueError("PLUGIN_PACKAGE_IDENTITY_MISMATCH")
            transaction.mkdir(parents=True, exist_ok=True)
            for name in ("new-code", "new-dependencies"):
                if (transaction / name).exists():
                    shutil.rmtree(transaction / name)
            installer._copy_folder(replacement, transaction / "new-code")
            dependencies = PluginDependencyRoots(roots.user_root)
            dependency_source = dependencies.verified_path(dependencies.declaration(replacement),
                payload / "dependencies" / plugin_id)
            if dependency_source is not None:
                shutil.copytree(dependency_source, transaction / "new-dependencies")
            dependencies._validate_entry(plugin_id, transaction / "new-code",
                transaction / "new-dependencies" if dependency_source is not None else None,
                spec.entry, runtime_imports=SOURCES.get(plugin_id, {}).get("runtimeImports", ()))
            if raw.get("enabled", True) != installed.desired_enabled:
                from app.plugins.inventory import PluginDesiredStateStore

                PluginDesiredStateStore(roots.user_root).set(plugin_id, installed.desired_enabled)
            _publish(transaction, target, dependency_target, plugin_id)
            log_event("Plugin", "已离线更新插件", details, event="plugin.update.completed", plugin_id=plugin_id)
            if progress is not None:
                progress({"state": "completed", "completed": 1, "total": 1, "pluginId": plugin_id})
        except Exception as error:
            diagnostics = diagnostic_attributes(error, reason_code="PLUGIN_UPDATE_REQUIRED", stage=details["stage"])
            log_event("Plugin", "插件离线更新未完成", {**details, **diagnostics},
                event="plugin.update.failed", severity="error", plugin_id=plugin_id)
            # Compatibility floors apply only when a changed contract requires
            # the new plugin. Other old providers may continue to run.
            failures[plugin_id] = {"reasonCode": "PLUGIN_UPDATE_REQUIRED", "diagnostics": diagnostics,
                                  "minimumVersion": source.get("minimumCompatibleVersion", "0.0.0")}
            if progress is not None:
                progress({"state": "failed", "completed": 0, "total": 1, "pluginId": plugin_id})
    return failures
