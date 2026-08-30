from __future__ import annotations

import json
import os
import shutil
import time
from dataclasses import dataclass
from pathlib import Path

from .errors import LegacyImportError


_ATOMIC_TREE_NAMES = ("characters", "tts")


@dataclass(frozen=True)
class PendingCommit:
    import_id: str
    target: Path

    @property
    def journal_path(self) -> Path:
        return self.target / f".legacy-import-journal-{self.import_id}.json"

    @property
    def staging_path(self) -> Path:
        return self.target / f".legacy-import-staging-{self.import_id}"

    @property
    def backup_path(self) -> Path:
        return self.target / f".legacy-import-backup-{self.import_id}"


def commit_payload(target: Path, import_id: str, payload: Path) -> PendingCommit:
    pending = PendingCommit(import_id, target)
    journal: dict[str, object] = {
        "schemaVersion": 1,
        "importId": import_id,
        "state": "committing",
        "installed": [],
        "backups": [],
        "installedTrees": [],
        "backupTrees": [],
    }
    _write_journal(pending.journal_path, journal)
    try:
        pending.backup_path.mkdir(parents=True, exist_ok=False)
        _commit_atomic_trees(pending, payload, journal)
        for staged_file in sorted(path for path in payload.rglob("*") if path.is_file()):
            relative = staged_file.relative_to(payload)
            destination = target / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            if destination.exists():
                if not destination.is_file():
                    raise LegacyImportError("LEGACY_COMMIT_TARGET_CONFLICT", "committing", relative.as_posix())
                backup = pending.backup_path / relative
                backup.parent.mkdir(parents=True, exist_ok=True)
                journal["backups"].append(relative.as_posix())  # type: ignore[union-attr]
                _write_journal(pending.journal_path, journal)
                os.replace(destination, backup)
            journal["installed"].append(relative.as_posix())  # type: ignore[union-attr]
            _write_journal(pending.journal_path, journal)
            os.replace(staged_file, destination)
        journal["state"] = "pending_core_validation"
        _write_journal(pending.journal_path, journal)
        return pending
    except Exception as exc:
        try:
            rollback_commit(pending)
        except Exception as rollback_error:
            raise LegacyImportError("LEGACY_ROLLBACK_FAILED", "committing") from rollback_error
        if isinstance(exc, LegacyImportError):
            raise
        raise LegacyImportError("LEGACY_COMMIT_FAILED", "committing") from exc


def finalize_commit(pending: PendingCommit) -> None:
    if not pending.journal_path.is_file():
        raise LegacyImportError("LEGACY_COMMIT_NOT_FOUND", "core_validating")
    try:
        journal = json.loads(pending.journal_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise LegacyImportError("LEGACY_JOURNAL_INVALID", "core_validating") from exc
    state = journal.get("state")
    if state not in {"pending_core_validation", "finalizing"}:
        raise LegacyImportError("LEGACY_JOURNAL_INVALID", "core_validating")
    if state == "pending_core_validation":
        journal["state"] = "finalizing"
        _write_journal(pending.journal_path, journal)
    # Once finalizing is durable, backup cleanup is irreversible and must never
    # fall back to rollback.  Best-effort leftovers keep the journal so startup
    # recovery can continue the same cleanup safely.
    shutil.rmtree(pending.backup_path, ignore_errors=True)
    shutil.rmtree(pending.staging_path, ignore_errors=True)
    if not pending.backup_path.exists() and not pending.staging_path.exists():
        try:
            pending.journal_path.unlink(missing_ok=True)
        except OSError:
            # The durable finalizing state makes a leftover journal harmless;
            # the next startup will retry cleanup instead of rolling back.
            pass


def rollback_commit(pending: PendingCommit) -> None:
    try:
        journal = json.loads(pending.journal_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise LegacyImportError("LEGACY_JOURNAL_INVALID", "committing") from exc
    if not isinstance(journal, dict):
        raise LegacyImportError("LEGACY_JOURNAL_INVALID", "committing")
    state = journal.get("state")
    if state == "finalizing":
        raise LegacyImportError("LEGACY_ROLLBACK_FORBIDDEN", "core_validating")
    if state not in {"committing", "pending_core_validation", "rolling_back"}:
        raise LegacyImportError("LEGACY_JOURNAL_INVALID", "committing")
    installed, backups, installed_trees, backup_trees = _validated_rollback_lists(journal)
    journal["installed"] = installed
    journal["backups"] = backups
    journal["installedTrees"] = installed_trees
    journal["backupTrees"] = backup_trees

    try:
        if state != "rolling_back":
            # Older journals did not checkpoint rollback progress.  A missing
            # backup with the expected target still present means either the
            # commit rename never ran or an earlier rollback already restored
            # it.  Remove both intents before any installed target is deleted.
            _reconcile_legacy_restores(
                pending,
                installed,
                backups,
                installed_trees,
                backup_trees,
            )
            journal["state"] = "rolling_back"
            _write_journal(pending.journal_path, journal)

        _remove_installed_files(pending, journal, installed)
        _restore_backup_files(pending, journal, backups)
        _remove_installed_trees(pending, journal, installed_trees)
        _restore_backup_trees(pending, journal, backup_trees)
        _cleanup_rollback(pending)
    except LegacyImportError:
        raise
    except OSError as exc:
        raise LegacyImportError("LEGACY_ROLLBACK_FAILED", "committing") from exc


def _validated_rollback_lists(
    journal: dict[str, object],
) -> tuple[list[str], list[str], list[str], list[str]]:
    values = (
        journal.get("installed", []),
        journal.get("backups", []),
        journal.get("installedTrees", []),
        journal.get("backupTrees", []),
    )
    if not all(isinstance(value, list) for value in values):
        raise LegacyImportError("LEGACY_JOURNAL_INVALID", "committing")
    installed = [_validated_relative(value).as_posix() for value in values[0]]
    backups = [_validated_relative(value).as_posix() for value in values[1]]
    installed_trees = [_validated_atomic_tree(value).as_posix() for value in values[2]]
    backup_trees = [_validated_atomic_tree(value).as_posix() for value in values[3]]
    return installed, backups, installed_trees, backup_trees


def _reconcile_legacy_restores(
    pending: PendingCommit,
    installed: list[str],
    backups: list[str],
    installed_trees: list[str],
    backup_trees: list[str],
) -> None:
    restored_files = _completed_legacy_restores(pending, backups, tree=False)
    restored_trees = _completed_legacy_restores(pending, backup_trees, tree=True)
    if restored_files:
        backups[:] = [value for value in backups if value not in restored_files]
        installed[:] = [value for value in installed if value not in restored_files]
    if restored_trees:
        backup_trees[:] = [value for value in backup_trees if value not in restored_trees]
        installed_trees[:] = [value for value in installed_trees if value not in restored_trees]


def _completed_legacy_restores(
    pending: PendingCommit,
    backups: list[str],
    *,
    tree: bool,
) -> set[str]:
    completed: set[str] = set()
    for relative_text in backups:
        relative = (
            _validated_atomic_tree(relative_text) if tree else _validated_relative(relative_text)
        )
        backup = pending.backup_path / relative
        destination = pending.target / relative
        backup_matches = backup.is_dir() if tree else backup.is_file()
        destination_matches = destination.is_dir() if tree else destination.is_file()
        if backup_matches:
            continue
        if backup.exists() or not destination_matches:
            raise LegacyImportError("LEGACY_ROLLBACK_FAILED", "committing")
        completed.add(relative_text)
    return completed


def _remove_installed_files(
    pending: PendingCommit,
    journal: dict[str, object],
    remaining: list[str],
) -> None:
    while remaining:
        relative = _validated_relative(remaining[-1])
        path = pending.target / relative
        if path.is_file():
            path.unlink()
        elif path.exists():
            raise LegacyImportError("LEGACY_ROLLBACK_FAILED", "committing")
        remaining.pop()
        _write_journal(pending.journal_path, journal)


def _restore_backup_files(
    pending: PendingCommit,
    journal: dict[str, object],
    remaining: list[str],
) -> None:
    while remaining:
        relative = _validated_relative(remaining[-1])
        backup = pending.backup_path / relative
        destination = pending.target / relative
        if backup.is_file():
            if destination.exists():
                raise LegacyImportError("LEGACY_ROLLBACK_FAILED", "committing")
            destination.parent.mkdir(parents=True, exist_ok=True)
            os.replace(backup, destination)
        elif backup.exists() or not destination.is_file():
            raise LegacyImportError("LEGACY_ROLLBACK_FAILED", "committing")
        remaining.pop()
        _write_journal(pending.journal_path, journal)


def _remove_installed_trees(
    pending: PendingCommit,
    journal: dict[str, object],
    remaining: list[str],
) -> None:
    while remaining:
        relative = _validated_atomic_tree(remaining[-1])
        path = pending.target / relative
        if path.is_dir():
            shutil.rmtree(path)
        elif path.exists():
            raise LegacyImportError("LEGACY_ROLLBACK_FAILED", "committing")
        remaining.pop()
        _write_journal(pending.journal_path, journal)


def _restore_backup_trees(
    pending: PendingCommit,
    journal: dict[str, object],
    remaining: list[str],
) -> None:
    while remaining:
        relative = _validated_atomic_tree(remaining[-1])
        backup = pending.backup_path / relative
        destination = pending.target / relative
        if backup.is_dir():
            if destination.exists():
                raise LegacyImportError("LEGACY_ROLLBACK_FAILED", "committing")
            destination.parent.mkdir(parents=True, exist_ok=True)
            os.replace(backup, destination)
        elif backup.exists() or not destination.is_dir():
            raise LegacyImportError("LEGACY_ROLLBACK_FAILED", "committing")
        remaining.pop()
        _write_journal(pending.journal_path, journal)


def _cleanup_rollback(pending: PendingCommit) -> None:
    for temporary in (pending.backup_path, pending.staging_path):
        if temporary.is_dir():
            shutil.rmtree(temporary)
        elif temporary.exists():
            raise LegacyImportError("LEGACY_ROLLBACK_FAILED", "committing")
        if temporary.exists():
            raise LegacyImportError("LEGACY_ROLLBACK_FAILED", "committing")
    _remove_empty_dirs(
        pending.target,
        preserve={pending.target / name for name in _ATOMIC_TREE_NAMES},
    )
    pending.journal_path.unlink(missing_ok=True)


def recover_pending_commits(target: Path) -> list[str]:
    recovered: list[str] = []
    for temporary in sorted(target.glob(".legacy-import-journal-*.json.tmp")):
        import_id = temporary.name.removeprefix(".legacy-import-journal-").removesuffix(
            ".json.tmp"
        )
        if temporary.is_file() and import_id.replace("-", "").isalnum():
            temporary.unlink()
    for journal in sorted(target.glob(".legacy-import-journal-*.json")):
        import_id = journal.name.removeprefix(".legacy-import-journal-").removesuffix(".json")
        if not import_id or not import_id.replace("-", "").isalnum():
            raise LegacyImportError("LEGACY_JOURNAL_INVALID", "inspect")
        pending = PendingCommit(import_id, target)
        try:
            state = json.loads(journal.read_text(encoding="utf-8")).get("state")
        except (OSError, json.JSONDecodeError, AttributeError) as exc:
            raise LegacyImportError("LEGACY_JOURNAL_INVALID", "inspect") from exc
        if state == "finalizing":
            finalize_commit(pending)
        else:
            rollback_commit(pending)
        recovered.append(import_id)
    for staging in sorted(target.glob(".legacy-import-staging-*")):
        import_id = staging.name.removeprefix(".legacy-import-staging-")
        if staging.is_dir() and import_id.replace("-", "").isalnum():
            shutil.rmtree(staging)
    for cancel in sorted(target.glob(".legacy-import-cancel-*")):
        import_id = cancel.name.removeprefix(".legacy-import-cancel-")
        if cancel.is_file() and import_id.replace("-", "").isalnum():
            cancel.unlink()
    if any(target.glob(".legacy-import-backup-*")):
        raise LegacyImportError("LEGACY_JOURNAL_INVALID", "inspect")
    return recovered


def _validated_relative(value: object) -> Path:
    if not isinstance(value, str) or not value:
        raise LegacyImportError("LEGACY_JOURNAL_INVALID", "committing")
    path = Path(value)
    if path.is_absolute() or path.drive or ".." in path.parts:
        raise LegacyImportError("LEGACY_JOURNAL_INVALID", "committing")
    return path


def _validated_atomic_tree(value: object) -> Path:
    path = _validated_relative(value)
    if len(path.parts) != 1 or path.as_posix() not in _ATOMIC_TREE_NAMES:
        raise LegacyImportError("LEGACY_JOURNAL_INVALID", "committing")
    return path


def _commit_atomic_trees(
    pending: PendingCommit,
    payload: Path,
    journal: dict[str, object],
) -> None:
    for name in _ATOMIC_TREE_NAMES:
        staged = payload / name
        if not staged.is_dir():
            continue
        relative = Path(name)
        destination = pending.target / relative
        if destination.exists():
            if not destination.is_dir():
                raise LegacyImportError(
                    "LEGACY_COMMIT_TARGET_CONFLICT", "committing", name
                )
            backup = pending.backup_path / relative
            journal["backupTrees"].append(name)  # type: ignore[union-attr]
            _write_journal(pending.journal_path, journal)
            os.replace(destination, backup)
        journal["installedTrees"].append(name)  # type: ignore[union-attr]
        _write_journal(pending.journal_path, journal)
        os.replace(staged, destination)


def _write_journal(path: Path, value: object) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        _replace_journal(temporary, path)
    except OSError as exc:
        temporary.unlink(missing_ok=True)
        raise LegacyImportError("LEGACY_JOURNAL_WRITE_FAILED", "committing") from exc


def _replace_journal(temporary: Path, path: Path) -> None:
    """Tolerate brief Windows scanner locks without retrying the import itself."""
    delays = (0.01, 0.02, 0.04, 0.08, 0.16)
    for delay in delays:
        try:
            os.replace(temporary, path)
            return
        except PermissionError:
            if os.name != "nt":
                raise
            time.sleep(delay)
    os.replace(temporary, path)


def _remove_empty_dirs(root: Path, *, preserve: set[Path] | None = None) -> None:
    preserved = preserve or set()
    for directory in sorted((path for path in root.rglob("*") if path.is_dir()), reverse=True):
        if directory in preserved:
            continue
        try:
            directory.rmdir()
        except OSError:
            pass
