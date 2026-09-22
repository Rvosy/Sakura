"""退役内置插件的离线迁移；兼容材料随目标发行版本保留。"""
from __future__ import annotations

import json
import os
import shutil
import tempfile
import time
import uuid
from collections.abc import Callable
from pathlib import Path

import yaml

from app.core.runtime_log import diagnostic_attributes, log_event
from app.storage.atomic import atomic_write_text
from app.storage.paths import StoragePaths
from app.storage.runtime_roots import RuntimeRoots

SOURCES = json.loads(Path(__file__).with_name("migration_sources.json").read_text(encoding="utf-8"))
MIGRATIONS = {key: value["directory"] for key, value in SOURCES.items()}
STATE_NAME = "plugin-migrations.json"
REVALIDATED_KEY = "__builtin_extraction_v1_revalidated"
PAYLOAD_PATH = Path("migration_payload/builtin-extraction-v1")


def _event(name: str, message: str, details: dict, plugin_id: str | None = None) -> None:
    log_event("Plugin", message, dict(details), event=f"plugin.migration.{name}",
              severity="info", plugin_id=plugin_id)


def _candidate_rejected(error: Exception, plugin_id: str, details: dict) -> None:
    _event("candidate_rejected", "插件迁移候选不可用", {
        **details,
        **diagnostic_attributes(error, reason_code=getattr(error, "code", "PLUGIN_MIGRATION_SOURCE_INVALID"),
                                stage=details["stage"]),
    }, plugin_id)


def _record(roots: RuntimeRoots, root: Path, plugin_id: str):
    from app.plugins.inventory import PluginInventory

    record = PluginInventory(roots)._record("user", root, {})
    if (record.plugin_id != plugin_id or not record.runtime_eligible
            or "sakura.host.model_slots" in record.requires):
        raise ValueError("PLUGIN_MIGRATION_SOURCE_INVALID")
    if record.capability_issues:
        raise ValueError(f"PLUGIN_MIGRATION_SOURCE_INVALID: {record.capability_issues}")
    return record


def _user_plugin(roots: RuntimeRoots, plugin_id: str):
    from app.plugins.inventory import PluginInventory

    return next((r for r in PluginInventory(roots).scan().records
                 if r.source == "user" and r.plugin_id == plugin_id), None)


def _dependency_root(dependencies, code: Path, root: Path) -> Path | None:
    from app.plugins.dependencies import PluginDependencyError

    verified = dependencies.verified_path(code, root)
    if verified is None:
        return None
    if any(path.name not in {".sakura-dependencies.json", "__pycache__"} for path in verified.iterdir()):
        return verified
    declaration = dependencies.declaration(code)
    # A leftover marker alone is not an installed environment. Empty requirements
    # are valid, however, and need no packages at all.
    requires_packages = declaration.kind not in {"requirements.txt", "requirements.lock"} or any(
        line.strip() and not line.lstrip().startswith("#")
        for line in declaration.path.read_text(encoding="utf-8").splitlines()
    )
    if requires_packages:
        raise PluginDependencyError("PLUGIN_DEPENDENCIES_MISSING")
    return verified


def _outside_recovery_versions(root: Path, plugin_id: str) -> bool:
    # Inventory substitutes an invalid record when the entry is missing. Use
    # the manifest to limit recovery to known historical/payload versions.
    try:
        raw = yaml.safe_load((root / "plugin.yaml").read_text(encoding="utf-8"))
    except (OSError, UnicodeError, yaml.YAMLError):
        return False
    source = SOURCES[plugin_id]
    return (isinstance(raw, dict) and raw.get("id") == plugin_id
            and isinstance(raw.get("version"), str) and bool(raw["version"])
            and raw["version"] not in (source["version"], *source.get("legacyVersions", [])))


def _needs_repair(roots: RuntimeRoots, plugin_id: str, details: dict | None = None) -> bool:
    from app.plugins.dependencies import PluginDependencyError, PluginDependencyRoots

    details = details if details is not None else {}
    details["stage"] = "inspect_existing"
    record = _user_plugin(roots, plugin_id)
    if record is None:
        # A completed migration followed by uninstall must remain uninstalled.
        details["outcome"] = "kept_uninstalled"
        return (StoragePaths(roots.user_root).user_plugins_dir / plugin_id).exists()
    details.update(code_source="user", version=record.version, outcome="revalidated")
    root = StoragePaths(roots.user_root).user_plugins_dir / record.directory_name
    if _outside_recovery_versions(root, plugin_id):
        details["outcome"] = "retained_external_version"
        return False
    try:
        details["stage"] = "validate_existing_source"
        _record(roots, root, plugin_id)
        dependencies = PluginDependencyRoots(roots.user_root)
        details["stage"] = "validate_existing_dependencies"
        dependency = _dependency_root(dependencies, root, StoragePaths(roots.user_root).plugin_dependency_root_for(plugin_id))
        details.update(stage="validate_existing_entry", dependency_source="user" if dependency is not None else "none")
        dependencies._validate_entry(
            plugin_id, root, dependency, record.entry,
            runtime_imports=SOURCES[plugin_id].get("runtimeImports", ()),
        )
    except (ValueError, PluginDependencyError) as error:
        _candidate_rejected(error, plugin_id, details)
        return True
    return False


def _prepare(roots: RuntimeRoots, plugin_id: str, staging: Path, original: Path | None, details: dict):
    """Select local code/dependencies, then prepare a complete replacement."""
    from app.plugins.dependencies import PluginDependencyError, PluginDependencyRoots

    paths = StoragePaths(roots.user_root)
    dependencies = PluginDependencyRoots(roots.user_root)
    directory = MIGRATIONS[plugin_id]
    payload = roots.distribution_root / PAYLOAD_PATH
    backups = roots.user_root / "plugins/migration-backups"
    sources = [("user", original)] if original is not None else []
    sources.append(("builtin", roots.distribution_root / "plugins/builtin" / directory))
    # 1.2.0 backups contain code under a random directory. They remain read-only.
    sources.extend(("backup", path.parent) for path in sorted(backups.glob("*/*/plugin.yaml"), reverse=True))
    sources.append(("payload", payload / "plugins" / directory))
    if (roots.distribution_root / "app/core_host").is_dir():
        sources.append(("development", roots.distribution_root / "plugins/optional" / directory))
    dependency_sources = [
        ("user", paths.plugin_dependency_root_for(plugin_id)),
        ("builtin", roots.distribution_root / "plugins/dependencies" / plugin_id),
        *(("backup", path) for path in sorted(backups.glob(f"*/dependencies/{plugin_id}"), reverse=True)),
        ("payload", payload / "dependencies" / plugin_id),
    ]
    last_error = ValueError("PLUGIN_MIGRATION_SOURCE_MISSING")
    last_details = {"stage": "select_source"}
    for code_source, source in sources:
        if not (source / "plugin.yaml").is_file():
            continue
        details.update(stage="validate_source", code_source=code_source)
        details.pop("version", None)
        details.pop("dependency_source", None)
        try:
            record = _record(roots, source, plugin_id)
            details["version"] = record.version
            declaration = dependencies.declaration(source)
        except (ValueError, PluginDependencyError) as error:
            last_error = error
            last_details = dict(details)
            _candidate_rejected(error, plugin_id, details)
            continue
        details["stage"] = "copy_code"
        code = staging / "code"
        if code.exists():
            shutil.rmtree(code)
        shutil.copytree(source, code, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        candidates = [("none", None)] if declaration is None else dependency_sources
        for dependency_source, candidate in candidates:
            details.update(stage="validate_dependencies", dependency_source=dependency_source)
            copied_dependencies = None
            try:
                dependency = None if candidate is None else _dependency_root(dependencies, code, candidate)
                if dependency is None:
                    details["dependency_source"] = "none"
                if dependency is not None and dependency != paths.plugin_dependency_root_for(plugin_id):
                    details["stage"] = "copy_dependencies"
                    copied_dependencies = staging / "dependencies"
                    if copied_dependencies.exists():
                        shutil.rmtree(copied_dependencies)
                    shutil.copytree(dependency, copied_dependencies)
                details["stage"] = "validate_entry"
                dependencies._validate_entry(
                    plugin_id, code, copied_dependencies or dependency, record.entry,
                    runtime_imports=SOURCES[plugin_id].get("runtimeImports", ()),
                )
            except PluginDependencyError as error:
                last_error = error
                last_details = dict(details)
                _candidate_rejected(error, plugin_id, details)
                continue
            _event("source_selected", "插件迁移来源已就绪", details, plugin_id)
            return code, copied_dependencies
    details.clear()
    details.update(last_details)
    raise last_error


def _publish(roots: RuntimeRoots, plugin_id: str, code: Path, dependencies: Path | None, target: Path, details: dict) -> None:
    """Keep originals until staging is ready; backups also survive process exit."""
    dependency_target = StoragePaths(roots.user_root).plugin_dependency_root_for(plugin_id)
    backup = roots.user_root / "plugins/migration-backups" / str(uuid.uuid4())
    moved: list[tuple[Path, Path]] = []
    published: list[Path] = []
    replacements = [(dependencies, dependency_target), (code, target)]
    try:
        for prepared, destination in replacements:
            if prepared is None:
                continue
            details["stage"] = "publish_dependencies" if destination == dependency_target else "publish_code"
            destination.parent.mkdir(parents=True, exist_ok=True)
            if destination.exists():
                saved = backup / (f"dependencies/{plugin_id}" if destination == dependency_target else target.name)
                saved.parent.mkdir(parents=True, exist_ok=True)
                os.replace(destination, saved)
                moved.append((saved, destination))
            os.replace(prepared, destination)
            published.append(destination)
    except Exception:
        rollback_failed = False
        for destination in reversed(published):
            try:
                shutil.rmtree(destination)
            except OSError as rollback_error:
                rollback_failed = True
                _failure(rollback_error, "PLUGIN_MIGRATION_ROLLBACK_FAILED", plugin_id,
                         {**details, "stage": "rollback_remove"})
        for saved, destination in reversed(moved):
            try:
                os.replace(saved, destination)
            except OSError as rollback_error:
                rollback_failed = True
                _failure(rollback_error, "PLUGIN_MIGRATION_ROLLBACK_FAILED", plugin_id,
                         {**details, "stage": "rollback_restore"})
        if not rollback_failed:
            _event("rollback_completed", "插件迁移已恢复原文件", {**details, "stage": "rollback", "outcome": "restored"}, plugin_id)
        raise


def ensure_external_plugin(roots: RuntimeRoots, plugin_id: str, *, enabled: bool, repair: bool = False,
                           diagnostics: dict | None = None) -> None:
    from app.plugins.inventory import PluginDesiredStateStore, PluginInventory

    details = diagnostics if diagnostics is not None else {}
    details["stage"] = "inspect_existing"
    paths = StoragePaths(roots.user_root)
    existing = _user_plugin(roots, plugin_id)
    if existing is not None and not repair:
        details.update(outcome="retained", code_source="user", version=existing.version)
        return
    original = paths.user_plugins_dir / (existing.directory_name if existing is not None else plugin_id)
    if existing is not None and _outside_recovery_versions(original, plugin_id):
        details.update(outcome="retained_external_version", code_source="user", version=existing.version)
        return
    if existing is None and original.exists():
        owner = PluginInventory(roots)._record("user", original, {}).plugin_id
        if owner is not None and owner != plugin_id:
            raise ValueError("PLUGIN_ID_CONFLICT")
    details.pop("outcome", None)
    details["stage"] = "prepare"
    paths.user_plugins_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".migration-", dir=paths.user_plugins_dir) as temporary:
        code, dependencies = _prepare(roots, plugin_id, Path(temporary), original if original.exists() else None, details)
        _publish(roots, plugin_id, code, dependencies, original, details)
    details["stage"] = "save_enabled_state"
    desired = PluginDesiredStateStore(roots.user_root)
    if plugin_id not in desired.read():
        desired.set(plugin_id, enabled)
    details["outcome"] = "repaired" if repair else "migrated"


def _failure(error: Exception, code: str, plugin_id: str | None = None, details: dict | None = None) -> None:
    details = details or {}
    log_event("Plugin", "插件迁移未完成", {
        **details, "code": code, "outcome": "failed",
        **diagnostic_attributes(error, reason_code=code, stage=details.get("stage", "builtin_extraction")),
    },
        event="plugin.migration.failed", severity="error", plugin_id=plugin_id)


def migrate_bundled_plugins(roots: RuntimeRoots, *, progress: Callable[[dict], None] | None = None) -> dict[str, str]:
    from app.plugins.inventory import PluginDesiredStateStore

    config = roots.user_root / "config"
    state_path = config / STATE_NAME
    failures: dict[str, str] = {}
    started = time.monotonic()

    def summary(count, total, ignored=0):
        _event("completed", "插件迁移检查结束", {
            "stage": "builtin_extraction", "outcome": "failed" if failures else "success" if total else "skipped",
            "count": count, "items": total, "failed": sum(key in MIGRATIONS for key in failures), "ignored": ignored,
            "elapsed_ms": round((time.monotonic() - started) * 1000),
        })

    def report(state, count, total, plugin_id=None):
        if progress is not None:
            progress({"state": state, "completed": count, "total": total, "pluginId": plugin_id})

    try:
        try:
            completed = json.loads(state_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            completed = {} if config.exists() else {key: "not_applicable" for key in MIGRATIONS}
        if not isinstance(completed, dict):
            raise ValueError("PLUGIN_MIGRATION_STATE_INVALID")
    except (OSError, UnicodeError, ValueError) as error:
        _failure(error, "PLUGIN_MIGRATION_STATE_INVALID", details={"stage": "read_state", "elapsed_ms": round((time.monotonic() - started) * 1000)})
        report("failed", 0, len(MIGRATIONS))
        # Preserve damaged metadata and every user plugin; the Core can still run.
        failures = {key: "PLUGIN_MIGRATION_STATE_INVALID" for key in (*MIGRATIONS, "__migration__")}
        summary(0, len(MIGRATIONS))
        return failures

    def save():
        atomic_write_text(state_path, json.dumps(completed, ensure_ascii=False, indent=2) + "\n")

    revalidated = completed.get(REVALIDATED_KEY) == "completed"
    pending = [plugin_id for plugin_id in MIGRATIONS
               if completed.get(plugin_id) != "not_applicable"
               and not (revalidated and completed.get(plugin_id) == "completed")]
    first_record = not state_path.exists()
    if pending or first_record:
        _event("started", "开始检查插件迁移", {
            "stage": "builtin_extraction", "items": len(pending), "ignored": len(MIGRATIONS) - len(pending),
        })
    count = 0
    handled = False
    for plugin_id in pending:
        # Include existing-copy import checks in the migration phase, outside
        # the Core's ordinary initialization deadline.
        report("running", count, len(pending), plugin_id)
        state = completed.get(plugin_id)
        plugin_started = time.monotonic()
        details = {"stage": "read_state", "status": state or "pending"}
        _event("plugin_started", "开始迁移插件", details, plugin_id)
        if state not in (None, "completed", "repairing"):
            failures[plugin_id] = "PLUGIN_MIGRATION_STATE_INVALID"
            _failure(ValueError("PLUGIN_MIGRATION_STATE_INVALID"), failures[plugin_id], plugin_id, {
                **details, "elapsed_ms": round((time.monotonic() - plugin_started) * 1000),
            })
            continue
        handled = True
        try:
            repair = state == "repairing" or _needs_repair(roots, plugin_id, details)
            if state == "completed" and not repair:
                count += 1
                _event("plugin_completed", "插件迁移检查完成", {
                    **details, "elapsed_ms": round((time.monotonic() - plugin_started) * 1000),
                }, plugin_id)
                continue
            details["stage"] = "read_enabled_state"
            desired = PluginDesiredStateStore(roots.user_root).read()
            # A persisted repair flag makes interrupted publication resumable.
            completed[plugin_id] = "repairing"
            details["stage"] = "save_repair_state"
            save()
            ensure_external_plugin(roots, plugin_id, enabled=desired.get(plugin_id, True), repair=repair, diagnostics=details)
            completed[plugin_id] = "completed"
            details["stage"] = "save_completed_state"
            save()
            count += 1
            _event("plugin_completed", "插件迁移检查完成", {
                **details, "elapsed_ms": round((time.monotonic() - plugin_started) * 1000),
            }, plugin_id)
        except Exception as error:
            # Also retain failures raised while inspecting an old completed copy.
            # Successful items must not be rechecked when this item is retried.
            completed[plugin_id] = "repairing"
            code = "PLUGIN_MIGRATION_SOURCE_MISSING" if str(error) == "PLUGIN_MIGRATION_SOURCE_MISSING" else "PLUGIN_MIGRATION_FAILED"
            failures[plugin_id] = code
            _failure(error, code, plugin_id, {
                **details, "elapsed_ms": round((time.monotonic() - plugin_started) * 1000),
            })
    if handled and not revalidated:
        # A string status keeps this flat file readable by 1.2.0 after rollback.
        completed[REVALIDATED_KEY] = "completed"
    if (handled and not revalidated) or not state_path.exists():
        try:
            save()
        except OSError as error:
            failures["__migration__"] = "PLUGIN_MIGRATION_STATE_WRITE_FAILED"
            _failure(error, failures["__migration__"], details={"stage": "save_state", "elapsed_ms": round((time.monotonic() - started) * 1000)})
    if pending or failures:
        report("failed" if failures else "completed", count, len(pending), next(iter(failures), None))
    if pending or failures or first_record:
        summary(count, len(pending), len(MIGRATIONS) - len(pending))
    return failures
