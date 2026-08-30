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
    state = journal.get("state")
    if state == "finalizing":
        raise LegacyImportError("LEGACY_ROLLBACK_FORBIDDEN", "core_validating")
    if state not in {"committing", "pending_core_validation"}:
        raise LegacyImportError("LEGACY_JOURNAL_INVALID", "committing")
    installed = journal.get("installed", [])
    backups = journal.get("backups", [])
    installed_trees = journal.get("installedTrees", [])
    backup_trees = journal.get("backupTrees", [])
    if not all(
        isinstance(value, list)
        for value in (installed, backups, installed_trees, backup_trees)
    ):
        raise LegacyImportError("LEGACY_JOURNAL_INVALID", "committing")
    for relative_text in reversed(installed):
        relative = _validated_relative(relative_text)
        path = pending.target / relative
        if path.is_file():
            path.unlink()
    for relative_text in reversed(backups):
        relative = _validated_relative(relative_text)
        backup = pending.backup_path / relative
        destination = pending.target / relative
        if backup.is_file():
            destination.parent.mkdir(parents=True, exist_ok=True)
            os.replace(backup, destination)
    for relative_text in reversed(installed_trees):
        relative = _validated_atomic_tree(relative_text)
        path = pending.target / relative
        if path.is_dir():
            shutil.rmtree(path)
        elif path.exists():
            raise LegacyImportError("LEGACY_ROLLBACK_FAILED", "committing")
    for relative_text in reversed(backup_trees):
        relative = _validated_atomic_tree(relative_text)
        backup = pending.backup_path / relative
        destination = pending.target / relative
        if backup.is_dir():
            destination.parent.mkdir(parents=True, exist_ok=True)
            os.replace(backup, destination)
    shutil.rmtree(pending.backup_path, ignore_errors=True)
    shutil.rmtree(pending.staging_path, ignore_errors=True)
    pending.journal_path.unlink(missing_ok=True)
    _remove_empty_dirs(
        pending.target,
        preserve={pending.target / _validated_atomic_tree(value) for value in backup_trees},
    )


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
